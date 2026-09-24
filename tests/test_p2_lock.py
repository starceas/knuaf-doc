"""P2 lane B unit/band tests — gg_fs primitives + gg_lock core guard.

Covers the B-owned acceptance items: P2-L01-held/capability,
P2-L02-repeat/owner-failure, P2-L04-structure/init-race, P2-L06
(candidate unlock behaviour), P2-L10-fork/exec, P2-L11-doctor, plus the
gg_fs side of P2-P01/P09/P15.

These are lane-local tests: they do not claim product PASS or native
Windows support.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._harness import ContractCase, runtime, script_source

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")

SCRIPTS = gg_fs.__file__ and str(Path(gg_lock.__file__).parent)


def _env():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = SCRIPTS
    return env


HOLDER = r"""
import sys
sys.path.insert(0, %r)
import gg_lock
root = sys.argv[1]
lock = gg_lock.Lock(root)
cap = lock.__enter__()
sys.stdout.write('READY\n')
sys.stdout.flush()
sys.stdin.readline()
lock.__exit__(None, None, None)
sys.stdout.write('RELEASED\n')
sys.stdout.flush()
""" % SCRIPTS


CONTENDER = r"""
import sys
sys.path.insert(0, %r)
import gg_lock
root = sys.argv[1]
try:
    with gg_lock.Lock(root):
        print('ACQUIRED')
except gg_lock.LockError as e:
    print('BUSY:' + e.reason)
""" % SCRIPTS


def _contender_result(root):
    out = subprocess.run(
        [sys.executable, "-B", "-c", CONTENDER, str(root)],
        capture_output=True, env=_env(), text=True, timeout=30)
    return out.stdout.strip()


EXEC_HOLDER = r"""
import sys, os
sys.path.insert(0, %r)
import gg_lock
lock = gg_lock.Lock(sys.argv[1])
cap = lock.__enter__()
sys.stdout.write('READY\n')
sys.stdout.flush()
os.execv(sys.executable, [sys.executable, '-B', '-c',
         'import time; time.sleep(3)'])
""" % SCRIPTS


def _spawn_holder(root):
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", HOLDER, str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=_env(), text=True,
    )
    assert proc.stdout.readline().strip() == "READY"
    return proc


def _release_holder(proc):
    proc.stdin.write("\n")
    proc.stdin.flush()
    proc.stdin.close()
    assert proc.stdout.readline().strip() == "RELEASED"
    proc.wait(timeout=15)
    assert proc.returncode == 0


class FsPrimitivesCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-fs-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_canonical_json_bytes_contract(self):
        data = gg_fs.canonical_json_bytes({"b": 1, "a": "한글", "c": [True]})
        self.assertEqual(data, b'{"a":"\xed\x95\x9c\xea\xb8\x80","b":1,"c":[true]}\n')
        self.assertTrue(data.endswith(b"}\n"))
        self.assertNotIn(b" ", data)
        with self.assertRaises(ValueError):
            gg_fs.canonical_json_bytes(float("nan"))
        with self.assertRaises(ValueError):
            gg_fs.canonical_json_bytes({1: "non-string key"})

    def test_validate_relative_path(self):
        self.assertEqual(gg_fs.validate_relative_path("a/b/c.txt"),
                         "a/b/c.txt")
        for bad in ("/abs", "//unc/x", "a//b", "./x", "a/../b", "a/./b",
                    "C:/x", "C:x", "", "a\x00b", "a/"):
            with self.assertRaises(ValueError, msg=bad):
                gg_fs.validate_relative_path(bad)

    def test_identity_kinds_and_symlink_rejection(self):
        f = self.root / "f.txt"
        f.write_text("x")
        ident = gg_fs.identity(f, kind="file")
        self.assertEqual(ident["kind"], "file")
        self.assertEqual(ident["platform"], sys.platform)
        with self.assertRaises(gg_fs.FsError):
            gg_fs.identity(f, kind="directory")
        link = self.root / "link"
        link.symlink_to(f)
        with self.assertRaises(gg_fs.FsError) as cm:
            gg_fs.identity(link, kind="file")
        self.assertEqual(cm.exception.reason, "symlink_reparse")
        fd = os.open(str(f), os.O_RDONLY)
        try:
            self.assertTrue(gg_fs.same_identity(
                gg_fs.fd_identity(fd, kind="file"), ident))
        finally:
            os.close(fd)

    def test_rename_noreplace_file_and_directory(self):
        src = self.root / "src.txt"
        src.write_bytes(b"payload")
        dst = self.root / "dst.txt"
        root_id = gg_fs.identity(self.root, kind="directory")
        res = gg_fs.rename_noreplace(
            src, dst, source_identity=gg_fs.identity(src, kind="file"),
            source_parent_identity=root_id,
            destination_parent_identity=root_id)
        self.assertEqual(res["state"], "installed")
        self.assertEqual(res["sha256"], gg_fs.sha256_bytes(b"payload"))
        # Existing destination refuses, everything preserved.
        other = self.root / "other.txt"
        other.write_bytes(b"other")
        res = gg_fs.rename_noreplace(
            other, dst, source_identity=gg_fs.identity(other, kind="file"),
            source_parent_identity=root_id,
            destination_parent_identity=root_id)
        self.assertEqual(res["state"], "not_installed")
        self.assertEqual(res["reason"], "destination_exists")
        self.assertEqual(dst.read_bytes(), b"payload")
        self.assertEqual(other.read_bytes(), b"other")
        # Directory rename works.
        dsrc = self.root / "d1"
        dsrc.mkdir()
        (dsrc / "inside.txt").write_text("i")
        ddst = self.root / "d2"
        res = gg_fs.rename_noreplace(
            dsrc, ddst,
            source_identity=gg_fs.identity(dsrc, kind="directory"),
            source_parent_identity=root_id,
            destination_parent_identity=root_id)
        self.assertEqual(res["state"], "installed")
        self.assertIsNone(res["sha256"])
        self.assertTrue((ddst / "inside.txt").exists())
        # Wrong source identity refused before any move.
        res = gg_fs.rename_noreplace(
            other, self.root / "d3",
            source_identity=gg_fs.identity(src.parent / "dst.txt",
                                           kind="file"),
            source_parent_identity=root_id,
            destination_parent_identity=root_id)
        self.assertEqual(res["state"], "not_installed")

    def test_publish_bytes_noreplace(self):
        target = self.root / "receipt.json"
        root_id = gg_fs.identity(self.root, kind="directory")
        res = gg_fs.publish_bytes_noreplace(
            target, b'{"a":1}\n', parent_identity=root_id)
        self.assertEqual(res["state"], "installed")
        self.assertEqual(target.read_bytes(), b'{"a":1}\n')
        # Declared identical existing file is a re-verify, not a rename.
        res = gg_fs.publish_bytes_noreplace(
            target, b'{"a":1}\n', parent_identity=root_id,
            existing_sha256=gg_fs.sha256_bytes(b'{"a":1}\n'))
        self.assertEqual(res["state"], "installed")
        self.assertEqual(res["reason"], "already_present")
        # Foreign or different bytes are refused and preserved.
        res = gg_fs.publish_bytes_noreplace(
            target, b'{"a":2}\n', parent_identity=root_id)
        self.assertEqual(res["state"], "not_installed")
        self.assertEqual(res["reason"], "destination_exists")
        self.assertEqual(target.read_bytes(), b'{"a":1}\n')
        res = gg_fs.publish_bytes_noreplace(
            target, b'{"a":1}\n', parent_identity=root_id,
            existing_sha256="0" * 64)
        self.assertEqual(res["state"], "not_installed")
        self.assertEqual(res["reason"], "destination_hash_mismatch")
        # Non-regular destination preserved.
        (self.root / "adir").mkdir()
        res = gg_fs.publish_bytes_noreplace(
            self.root / "adir", b"x", parent_identity=root_id)
        self.assertEqual(res["state"], "not_installed")
        self.assertEqual(res["reason"], "destination_not_regular")


class LockStructureCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-lock-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _make_ready(self):
        return gg_lock.install_new(self.root)

    def test_install_new_fields_and_exclusivity(self):
        proto = self._make_ready()
        self.assertEqual(proto["schema"], "gg-lock/2")
        self.assertEqual(proto["protocol"], 2)
        self.assertIsNone(proto["recovery_id"])
        for key in ("workspace_id", "root_identity",
                    "directory_identity", "guard_identity"):
            self.assertIn(key, proto)
        guard = self.root / ".gg-lock" / "guard"
        self.assertEqual(guard.stat().st_size, 1)
        # Nobody may continue or repeat an install.
        with self.assertRaises(gg_lock.LockError):
            gg_lock.install_new(self.root)

    def test_install_refusals(self):
        # Existing project -> legacy, no auto-install.
        (self.root / "project.json").write_text("{}")
        with self.assertRaises(gg_lock.LockError) as cm:
            gg_lock.install_new(self.root)
        self.assertEqual(cm.exception.status, "legacy")
        # Existing .gg-lock (even empty) is never continued.
        other = Path(self.tmp.name) / "b"
        other.mkdir()
        (other / ".gg-lock").mkdir()
        with self.assertRaises(gg_lock.LockError):
            gg_lock.install_new(other)
        # Active recovery file blocks.
        third = Path(self.tmp.name) / "c"
        third.mkdir()
        (third / ".gg-recovery-active.json").write_text("{}")
        with self.assertRaises(gg_lock.LockError):
            gg_lock.install_new(third)

    def test_structures(self):
        self.assertEqual(
            gg_lock.inspect_lock(self.root)["structure"], "absent")
        # initializing: empty orphan dir
        (self.root / ".gg-lock").mkdir()
        self.assertEqual(
            gg_lock.inspect_lock(self.root)["structure"], "initializing")
        shutil.rmtree(self.root / ".gg-lock")
        # legacy: owner.json without protocol/guard
        lockdir = self.root / ".gg-lock"
        lockdir.mkdir()
        (lockdir / "owner.json").write_text("{}")
        self.assertEqual(
            gg_lock.inspect_lock(self.root)["structure"], "legacy")
        shutil.rmtree(lockdir)
        # ready
        self._make_ready()
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["structure"], "ready")
        self.assertIsNone(st["reason"])
        # damaged: corrupt protocol bytes
        (lockdir / "protocol.json").write_text("{not json")
        self.assertEqual(
            gg_lock.inspect_lock(self.root)["structure"], "damaged")
        shutil.rmtree(lockdir)
        self._make_ready()
        # damaged: replaced guard
        os.unlink(lockdir / "guard")
        (lockdir / "guard").write_bytes(b"Z")
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["structure"], "damaged")
        self.assertEqual(st["reason"], "guard_replaced")
        # damaged: symlinked lock dir
        shutil.rmtree(lockdir)
        real = self.root / "real"
        real.mkdir()
        (self.root / ".gg-lock").symlink_to(real, target_is_directory=True)
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["structure"], "damaged")
        self.assertEqual(st["reason"], "lock_dir_symlink")

    def test_copied_workspace_blocked_rename_allowed(self):
        proto = self._make_ready()
        copy = Path(self.tmp.name) / "copied"
        shutil.copytree(self.root, copy)
        # copytree gives every control object a new inode: recorded
        # identities no longer match, so the copy fails closed and
        # normal writes are refused.
        st = gg_lock.inspect_lock(copy)
        self.assertNotEqual(st["structure"], "ready")
        with self.assertRaises(gg_lock.LockError):
            gg_lock.Lock(copy).__enter__()
        # A plain rename keeps the same inode: acquisition still works.
        moved = self.root.with_name(self.root.name + "-moved")
        self.addCleanup(lambda: shutil.rmtree(moved, ignore_errors=True))
        os.rename(self.root, moved)
        with gg_lock.Lock(moved) as cap:
            self.assertEqual(cap.workspace_id, proto["workspace_id"])


class LockAcquireCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-acq-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proto = gg_lock.install_new(self.root)

    def test_acquire_release_owner_lifecycle(self):
        owner = self.root / ".gg-lock" / "owner.json"
        self.assertFalse(owner.exists())
        with gg_lock.Lock(self.root) as cap:
            self.assertTrue(owner.exists())
            record = json.loads(owner.read_text())
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(
                record["protocol_sha256"], cap.protocol_sha256)
            gg_lock.assert_held(cap, self.root)
        self.assertFalse(owner.exists())
        # guard/protocol stay, probe is free again.
        self.assertTrue((self.root / ".gg-lock" / "guard").exists())
        self.assertEqual(
            gg_lock.probe_lock(self.root)["lock_state"], "free")

    def test_repeat_acquire_release(self):
        guard = self.root / ".gg-lock" / "guard"
        ident0 = gg_fs.identity(guard, kind="file")
        for _ in range(3):
            with gg_lock.Lock(self.root) as cap:
                gg_lock.assert_held(cap, self.root)
        self.assertTrue(gg_fs.same_identity(
            gg_fs.identity(guard, kind="file"), ident0))
        self.assertEqual(guard.read_bytes(), b"\x00")

    def test_held_lock_blocks_second_writer_and_unlock(self):
        holder = _spawn_holder(self.root)
        try:
            with self.assertRaises(gg_lock.LockError) as cm:
                gg_lock.Lock(self.root).__enter__()
            self.assertEqual(cm.exception.status, "busy")
            # Candidate unlock cannot break a real held guard.
            with self.assertRaises(gg_lock.LockError):
                gg_lock.cleanup_owner(self.root)
            self.assertEqual(
                gg_lock.probe_lock(self.root)["lock_state"], "busy")
        finally:
            _release_holder(holder)
        # After release a new writer can enter.
        with gg_lock.Lock(self.root) as cap:
            gg_lock.assert_held(cap, self.root)

    def test_capability_forgery_refused(self):
        with gg_lock.Lock(self.root) as cap:
            gg_lock.assert_held(cap, self.root)
            with self.assertRaises(gg_lock.LockError):
                gg_lock.assert_held({"token": cap.token}, self.root)
            forged = gg_lock.Capability()
            forged.token = cap.token
            forged.pid = cap.pid
            forged.root = cap.root
            forged.root_identity = cap.root_identity
            forged.directory_identity = cap.directory_identity
            forged.guard_identity = cap.guard_identity
            forged.protocol_sha256 = cap.protocol_sha256
            forged.released = False
            forged._fd = cap._fd
            with self.assertRaises(gg_lock.LockError) as cm:
                gg_lock.assert_held(forged, self.root)
            self.assertEqual(cm.exception.reason, "not_registered")
            with self.assertRaises(gg_lock.LockError):
                gg_lock.assert_held(gg_lock._RecoveryCapability(),
                                    self.root)
            other = Path(self.tmp.name) / "elsewhere"
            other.mkdir()
            with self.assertRaises(gg_lock.LockError):
                gg_lock.assert_held(cap, other)
        # Released capability is dead.
        with self.assertRaises(gg_lock.LockError):
            gg_lock.assert_held(cap, self.root)

    def test_owner_write_failure_releases_fd(self):
        original = gg_lock._write_owner_file
        gg_lock._write_owner_file = (
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        try:
            with self.assertRaises(gg_lock.LockError) as cm:
                gg_lock.Lock(self.root).__enter__()
            self.assertIn("owner_write_failed", cm.exception.reason)
        finally:
            gg_lock._write_owner_file = original
        # The fd was really closed: another writer can enter.
        with gg_lock.Lock(self.root):
            pass
        self.assertFalse(
            (self.root / ".gg-lock" / "owner.json").exists())

    def test_release_cleanup_error_still_closes(self):
        cap = gg_lock.Lock(self.root).__enter__()
        original = gg_lock._cleanup_owner_record
        gg_lock._cleanup_owner_record = (
            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        try:
            with self.assertRaises(gg_lock.LockCleanupError) as cm:
                gg_lock._release(cap)
            self.assertTrue(cm.exception.release_confirmed)
        finally:
            gg_lock._cleanup_owner_record = original
        self.assertTrue(cap.released)
        with gg_lock.Lock(self.root):
            pass

    def test_cleanup_owner_unlock(self):
        lockdir = self.root / ".gg-lock"
        (lockdir / "owner.json").write_text(
            '{"pid": 999999, "host": "other"}')
        result = gg_lock.cleanup_owner(self.root)
        self.assertTrue(result["owner_cleaned"])
        self.assertFalse((lockdir / "owner.json").exists())
        # Control objects are never deleted by unlock.
        self.assertTrue((lockdir / "guard").exists())
        self.assertTrue((lockdir / "protocol.json").exists())
        self.assertTrue(lockdir.is_dir())

    def test_doctor_writes_nothing(self):
        before = sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*"))
        st = gg_lock.inspect_lock(self.root)
        self.assertEqual(st["lock_state"], "unprobed")
        after = sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*"))
        self.assertEqual(before, after)
        st = gg_lock.probe_lock(self.root)
        self.assertEqual(st["lock_state"], "free")
        self.assertIn("observed_at", st)

    def test_active_recovery_blocks_acquire(self):
        active = self.root / ".gg-recovery-active.json"
        active.write_text("{}")
        with self.assertRaises(gg_lock.LockError) as cm:
            gg_lock.Lock(self.root).__enter__()
        self.assertEqual(cm.exception.reason, "recovery_active")
        # Corrupt/partial active still blocks — fail closed.
        active.write_bytes(b"\x00\x01partial")
        with self.assertRaises(gg_lock.LockError):
            gg_lock.Lock(self.root).__enter__()
        active.unlink()
        with gg_lock.Lock(self.root):
            pass


@unittest.skipUnless(hasattr(os, "fork"), "posix fork required")
class ForkExecCase(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p2b-fork-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        gg_lock.install_new(self.root)

    def test_fork_child_cannot_use_capability(self):
        lock = gg_lock.Lock(self.root)
        cap = lock.__enter__()
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(r)
                try:
                    gg_lock.assert_held(cap, self.root)
                    outcome = b"CHILD_USED_CAP"
                except gg_lock.LockError:
                    outcome = b"REFUSED"
                try:
                    gg_lock._release(cap)
                except Exception:
                    pass
                os.write(w, outcome)
                os.close(w)
            finally:
                os._exit(0)
        os.close(w)
        outcome = os.read(r, 64)
        os.close(r)
        os.waitpid(pid, 0)
        self.assertEqual(outcome, b"REFUSED")
        # The child's close must not release the parent's lock: a
        # separate contender still sees busy.
        self.assertTrue(
            _contender_result(self.root).startswith("BUSY"))
        lock.__exit__(None, None, None)

    def test_negative_control_child_lock_un_is_detected(self):
        """A wrong implementation that LOCK_UNs an inherited fd DOES
        release the parent's lock — the test rig must catch that."""
        import fcntl

        lock = gg_lock.Lock(self.root)
        cap = lock.__enter__()
        # The dup'd fd survives the child's at-fork invalidation, so a
        # buggy child could reach the guard — the negative control.
        fd = os.dup(cap._fd)
        pid = os.fork()
        if pid == 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os._exit(0)
        os.close(fd)
        os.waitpid(pid, 0)
        # Bad pattern detected: contender now acquires.
        self.assertEqual(_contender_result(self.root), "ACQUIRED")
        # Parent capability is now stale regardless.
        gg_lock._release(cap)

    def test_exec_does_not_carry_fd(self):
        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", EXEC_HOLDER, str(self.root)],
            stdout=subprocess.PIPE, env=_env(), text=True)
        self.assertEqual(proc.stdout.readline().strip(), "READY")
        # The holder exec'd a sleeper: its CLOEXEC guard fd is gone, so
        # the lock is released at exec boundary.
        deadline = 60
        acquired = False
        import time
        while deadline and proc.poll() is None:
            try:
                with gg_lock.Lock(self.root):
                    acquired = True
                break
            except gg_lock.LockError:
                deadline -= 1
                time.sleep(0.1)
        proc.stdout.close()
        proc.wait(timeout=15)
        self.assertTrue(acquired, "guard fd leaked across exec")


if __name__ == "__main__":
    unittest.main()
