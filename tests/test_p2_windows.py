"""P2 lane B tests — Windows-semantics band (P2-L02-win-read,
P2-L09-native placeholder, P2-P09 Windows no-replace).

There is no native Windows host in this environment, so these tests
run a STRICT byte-lock emulator: a port that enforces the real
msvcrt.locking semantics — a second handle may not read a byte range
locked by another handle. Under that band the old PR3 pattern (locking
the owner file bytes and reading them through a second handle) fails,
while the candidate path (offset-0 lock on a separate 1-byte guard,
owner I/O on a different file, guard bytes never read) works.

These results are band observations only; native Windows support
remains withheld until P2-L09-native runs on real Windows+NTFS.
"""
import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, runtime, script_source

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")


class StrictWindowsPort:
    """Emulator enforcing real byte-range lock semantics on this host.
    A locked byte cannot be read through any other handle — the rule
    the old PR3 owner-read path violated."""

    name = "win32"

    def __init__(self):
        self._locks = {}  # (dev, ino) -> holding fd

    @staticmethod
    def _key(fd):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    def open_guard(self, path):
        return os.open(
            str(path), os.O_RDWR | getattr(os, "O_BINARY", 0))

    def lock(self, fd):
        key = self._key(fd)
        if key in self._locks:
            raise PermissionError(
                errno.EACCES, "byte range locked by another handle")
        self._locks[key] = fd

    def unlock(self, fd):
        self._locks.pop(self._key(fd), None)

    # --- emulator-only probes used by the negative control ---
    def read_byte(self, fd):
        key = self._key(fd)
        if key in self._locks and self._locks[key] != fd:
            raise PermissionError(
                errno.EACCES, "locked byte read via second handle")
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 1)

    def write_byte(self, fd, data):
        key = self._key(fd)
        if key in self._locks and self._locks[key] != fd:
            raise PermissionError(
                errno.EACCES, "locked byte write via second handle")
        os.lseek(fd, 0, os.SEEK_SET)
        return os.write(fd, data)


class WindowsBandCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-win-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.port = StrictWindowsPort()
        self._saved = gg_lock._PORT
        gg_lock._PORT = self.port
        self.addCleanup(setattr, gg_lock, "_PORT", self._saved)

    def test_candidate_lock_cycle_under_strict_bytes(self):
        """install -> acquire -> owner write -> release must work under
        strict byte-lock semantics because the guard and owner files
        are separate and guard bytes are never read."""
        proto = gg_lock.install_new(self.root)
        with gg_lock.Lock(self.root) as cap:
            self.assertEqual(cap.workspace_id, proto["workspace_id"])
            owner = json.loads(
                (self.root / ".gg-lock" / "owner.json").read_bytes())
            self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(gg_lock.probe_lock(self.root)["lock_state"],
                         "free")

    def test_candidate_never_reads_guard_bytes(self):
        src = script_source("gg_lock.py")
        self.assertNotIn("os.read", src)
        # read_bytes is applied to owner/protocol/claim/active/stage
        # files — never to the guard path.
        self.assertNotIn("read_bytes(\n", src)
        for needle in ("read_bytes(guard", "read_bytes(_guard_path",
                       "read_bytes(lockdir / GUARD_NAME",
                       "read_bytes(rdir / GUARD_NAME"):
            self.assertNotIn(needle, src)

    def test_old_pr3_owner_lock_read_fails_strict(self):
        """Negative control for the retired PR3 pattern: byte-lock a
        file, then read through a second handle. Under strict
        semantics this MUST fail — the old design's owner-read was
        the defect."""
        target = self.root / "owner.json"
        target.write_bytes(b"O")
        fd1 = self.port.open_guard(target)
        self.port.lock(fd1)
        fd2 = self.port.open_guard(target)
        with self.assertRaises(PermissionError):
            self.port.read_byte(fd2)
        with self.assertRaises(PermissionError):
            self.port.write_byte(fd2, b"X")
        self.port.unlock(fd1)
        os.close(fd1)
        os.close(fd2)

    def test_ambiguous_contention_is_busy_or_denied(self):
        """Windows cannot distinguish contention from permission
        denial: EACCES maps to unavailable/busy_or_denied, never
        guessed as stale or live-PID."""

        class DenyPort(StrictWindowsPort):
            def lock(self, fd):
                raise PermissionError(errno.EACCES, "denied")

        gg_lock._PORT = DenyPort()
        gg_lock.install_new(self.root)
        st = gg_lock.probe_lock(self.root)
        self.assertEqual(st["lock_state"], "unavailable")
        self.assertEqual(st["reason"], "busy_or_denied")
        with self.assertRaises(gg_lock.LockError) as cm:
            gg_lock.Lock(self.root).__enter__()
        self.assertEqual(cm.exception.status, "unavailable")
        self.assertEqual(cm.exception.reason, "busy_or_denied")

    def test_recovery_under_strict_bytes(self):
        (self.root / "project.json").write_text("{}")
        result = gg_lock.upgrade_offline(self.root, offline_confirmed=True)
        self.assertEqual(result["status"], "completed", result)
        with gg_lock.Lock(self.root):
            pass

    def test_identity_unavailable_fails_closed(self):
        """When a filesystem cannot yield a stable inode the identity
        primitive refuses instead of substituting path/size/mtime."""

        class FakeStat:
            st_mode = 0o100644
            st_dev = 7
            st_ino = 0

        with mock.patch.object(gg_fs.sys, "platform", "win32"):
            with self.assertRaises(gg_fs.FsError) as cm:
                gg_fs._identity_from_stat(FakeStat(), "file", "x")
            self.assertEqual(cm.exception.reason, "identity_unavailable")

    def test_no_replace_windows_path_is_rename_not_fallback(self):
        """The Windows no-replace contract is the documented
        fail-on-existing os.rename; exists()+rename, os.replace and
        shutil.move fallbacks must not exist."""
        src = script_source("gg_fs.py")
        self.assertIn('sys.platform == "win32"', src)
        self.assertIn("os.rename(str(src), str(dst))", src)
        self.assertNotIn("os.replace(", src)
        self.assertNotIn("shutil.move(", src)
        # Host check of the real no-replace primitive (macOS band here):
        root_id = gg_fs.identity(self.root, kind="directory")
        a = self.root / "a"
        a.write_bytes(b"a")
        b = self.root / "b"
        b.write_bytes(b"b")
        res = gg_fs.rename_noreplace(
            a, b, source_identity=gg_fs.identity(a, kind="file"),
            source_parent_identity=root_id,
            destination_parent_identity=root_id)
        self.assertEqual(res["state"], "not_installed")
        self.assertEqual(res["reason"], "destination_exists")
        self.assertEqual(b.read_bytes(), b"b")

    @unittest.skipUnless(sys.platform == "win32",
                         "native Windows+NTFS required — withheld")
    def test_native_windows_two_process_contention(self):
        """P2-L09-native: real Windows host only. Not run here; support
        is withheld until this executes on native Windows+NTFS."""
        self.fail("native Windows run not recorded")


if __name__ == "__main__":
    unittest.main()
