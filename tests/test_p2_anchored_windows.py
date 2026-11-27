"""Native Windows pin and recovery contracts; portable preflight refusals."""
import ctypes
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests._harness import SCRIPTS, runtime

gg_fs = runtime("gg_fs")
gg_lock = runtime("gg_lock")
win = runtime("gg_winfs")


def upgrade(root, expected, resume_id=None, *, confirmed=True):
    return gg_lock._upgrade_offline_anchored(
        root, offline_confirmed=confirmed, expected_identity=expected, resume_id=resume_id)


def identity(root: Path):
    return gg_fs.identity(root, kind="directory")


def snapshot(root: Path):
    return {p.relative_to(root): None if p.is_dir() else p.read_bytes() for p in root.rglob("*")}


def outcome(result):
    return result["status"], result["reason"], result["commit_state"]


class WindowsPreflight(unittest.TestCase):
    def test_refusals_never_write(self):  # §5, T8/T11/T12
        approved = dict(platform="win32", kind="directory", device=1, inode=2**53 + 1)
        cases = (
            ({"confirmed": False}, approved, True, None, "offline_confirmation_required"),
            ({"resume_id": r"C:\x"}, approved, True, None, "recovery_id_invalid"),
            ({}, dict(approved, inode=float(approved["inode"])), True, None, "target_changed"),
            ({}, approved, False, None, "anchor_unavailable"),
            ({}, approved, True, FileNotFoundError(errno.ENOENT, "missing"), "target_changed"),
            ({}, approved, True, win._InfoFailure("object kind mismatch"), "target_changed"),
            ({}, approved, True, win._InfoFailure("symlink_reparse"), "symlink_reparse"),
            ({}, approved, True, win._InfoFailure("identity_unavailable"), "identity_unavailable"),
            ({}, approved, True, PermissionError(errno.EACCES, "denied"), "windows_identity_failed"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project.json").write_bytes(b"{}")
            before = snapshot(root)
            for kwargs, expected, supported, error, reason in cases:
                with self.subTest(reason=reason), patch.object(gg_lock, "sys", SimpleNamespace(platform="win32")), \
                        patch.object(win, "supported", return_value=supported), \
                        patch.object(win, "Directory", side_effect=error or AssertionError("preflight opened a pin")):
                    self.assertEqual(outcome(upgrade(root, expected, **kwargs)), ("blocked", reason, "not_committed"))
                    self.assertEqual(snapshot(root), before)


@unittest.skipUnless(sys.platform == "win32", "native Windows filesystem")
class WindowsPin(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="gg-win-anchor-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()

    def child(self, code, *args, check=True):
        return subprocess.run([sys.executable, "-I", "-B", "-S", "-c", code, *map(str, args)],
                              cwd=self.base, capture_output=True, text=True, timeout=30, check=check)

    def test_pin_denies_root_and_ancestor_moves_and_root_delete(self):  # W-E1
        code = """
import os, sys
operation, src, dst = sys.argv[1:]
try:
    os.rmdir(src) if operation == 'rmdir' else os.rename(src, dst)
except OSError as error:
    print(error.winerror)
else:
    print(0)
"""
        for operation in ("rename-root", "rename-parent", "rmdir"):
            with self.subTest(operation=operation):
                root = self.base / operation / "root"
                root.mkdir(parents=True)
                expected = identity(root)
                src = root.parent if operation == "rename-parent" else root
                dst = src.with_name(src.name + "-moved")
                with win.Directory(root) as pin:
                    self.assertIn(int(self.child(code, operation, src, dst).stdout), (5, 32))
                    self.assertEqual(win.pinned_identity(pin), expected)
                    self.assertEqual(identity(root), expected)
                self.assertEqual(self.child(code, operation, src, dst).stdout.strip(), "0")

    def test_child_quarantine_moves_preserve_identity(self):  # W-E2–E4
        for method in ("path", "absolute-handle", "relative-handle"):
            with self.subTest(method=method), ExitStack() as ends:
                root = self.base / method
                src = root / ".gg-lock"
                dst = root / ".gg-recovery" / "id" / "original-lock"
                src.mkdir(parents=True)
                dst.parent.mkdir(parents=True)
                (src / "guard").write_bytes(b"0")
                root_id, source_id = identity(root), identity(src)
                with win.Directory(root):
                    if method == "path":
                        os.rename(src, dst)
                    elif method == "absolute-handle":
                        win.rename_directory_noreplace(src, dst, source_identity=source_id)
                    else:
                        source = win._open(src, win._READ | win._DELETE, 3, "directory", share=7)
                        ends.callback(win._close, source)
                        parent = win._open(dst.parent, win._ATTR | 0x24, 3, "directory", share=7)
                        ends.callback(win._close, parent)
                if method == "relative-handle":
                    name = dst.name.encode("utf-16-le")
                    offset = win._RenameInfo.FileName.offset
                    buf = ctypes.create_string_buffer(max(ctypes.sizeof(win._RenameInfo), offset + len(name) + 2))
                    info = win._RenameInfo.from_buffer(buf)
                    info.RootDirectory, info.FileNameLength = parent, len(name)
                    ctypes.memmove(ctypes.addressof(buf) + offset, name, len(name))
                    if not win._api().SetFileInformationByHandle(source, 3, buf, len(buf)):
                        self.assertEqual(ctypes.get_last_error(), 87)  # measured W-E4 rejection
                        ends.close()
                        self.assertFalse(dst.exists())
                        self.assertEqual((identity(root), identity(src)), (root_id, source_id))
                        continue
                ends.close()  # release DELETE access before re-opening through identity()
                self.assertFalse(src.exists())
                self.assertEqual((identity(root), identity(dst)), (root_id, source_id))

    def workspace(self, root: Path, *, legacy=True) -> Path:
        root.mkdir(parents=True)
        (root / "project.json").write_bytes(b'{"schema_version":2,"revision":0}')
        if legacy:
            (root / ".gg-lock").mkdir()
            (root / ".gg-lock" / "legacy.txt").write_bytes(b"old lock")
        return root

    def completed(self, root, expected, result, disposition="legacy_or_unknown"):
        self.assertEqual(outcome(result), ("completed", None, "committed"), result)
        rid = result["recovery_id"]
        rdir = root / gg_lock.RECOVERY_DIR / rid
        claim = json.loads((rdir / "claim.json").read_bytes())
        self.assertEqual((claim["root_identity"], claim["disposition"]), (expected, disposition))
        receipt = rdir / "30-completed.json"
        self.assertEqual(Path(result["receipt_path"]), receipt)
        self.assertEqual(result["receipt_sha256"], gg_fs.sha256_bytes(receipt.read_bytes()))
        previous = None
        for stage in gg_lock.STAGES:
            data = (rdir / (stage + ".json")).read_bytes()
            record = json.loads(data)
            self.assertEqual((record["recovery_id"], record["stage"], record["previous_sha256"]),
                             (rid, stage, previous))
            previous = gg_fs.sha256_bytes(data)
        self.assertFalse((root / gg_lock.ACTIVE_NAME).exists())
        status = gg_lock.inspect_lock(root)
        self.assertEqual((status["structure"], status["protocol"]["recovery_id"]), ("ready", rid))
        return rdir / "original-lock"

    def test_recovery_preserves_or_rekeys_workspace_lineage(self):  # TW repair and copy
        root = self.workspace(self.base / "original", legacy=False)
        workspace_id = gg_lock.install_new(root)["workspace_id"]
        copied = Path(shutil.copytree(root, self.base / "copy"))
        guard = root / ".gg-lock" / "guard"
        replacement = guard.with_name("replacement")
        replacement.write_bytes(b"X")
        os.replace(replacement, guard)
        for target, disposition in ((root, "same_workspace_repair"), (copied, "copied_workspace")):
            with self.subTest(disposition=disposition):
                approved, old_lock = identity(target), identity(target / ".gg-lock")
                original = self.completed(target, approved, upgrade(target, approved), disposition)
                self.assertEqual(identity(original), old_lock)
                current = gg_lock.inspect_lock(target)["protocol"]["workspace_id"]
                self.assertEqual(current == workspace_id, target == root)

    def test_resume_after_process_exit_at_each_stage(self):  # TW interruption and resume
        code = """
import json, os, sys
scripts, root, approved, stage = sys.argv[1:]
sys.path.insert(0, scripts)
import gg_lock
def fault(name):
    if name == stage:
        os._exit(73)
gg_lock._FAULT = fault
gg_lock._upgrade_offline_anchored(root, offline_confirmed=True, expected_identity=json.loads(approved))
"""
        for stage in ("anchored", "after_active", "after_00-prepared", "after_quarantine",
                      "after_10-quarantined", "after_install", "after_20-installed", "after_30-completed"):
            with self.subTest(stage=stage):
                root = self.workspace(self.base / stage)
                expected = identity(root)
                child = self.child(code, SCRIPTS, root, json.dumps(expected), stage, check=False)
                self.assertEqual(child.returncode, 73, child.stderr)
                active = root / gg_lock.ACTIVE_NAME
                rid = None if stage == "anchored" else json.loads(active.read_bytes())["recovery_id"]
                original = self.completed(root, expected, upgrade(root, expected, rid))
                self.assertEqual((original / "legacy.txt").read_bytes(), b"old lock")

    def test_relative_root_survives_process_cwd_change(self):  # TW/I-1, no recovery subprocess
        approved = self.workspace(self.base / "approved" / "root")
        decoy = self.workspace(self.base / "decoy" / "root")
        before, expected = snapshot(decoy), json.loads(json.dumps(identity(approved)))
        cwd = Path.cwd()

        def change_cwd(stage):
            if stage == "anchored":
                os.chdir(decoy.parent)
            if stage in ("anchored", "after_quarantine", "after_30-completed"):
                with self.assertRaises(OSError) as blocked:
                    approved.rename(approved.with_name("moved"))
                self.assertIn(blocked.exception.winerror, (5, 32))

        try:
            os.chdir(approved.parent)
            with patch.object(gg_lock, "_FAULT", change_cwd), patch.object(subprocess, "Popen", side_effect=AssertionError):
                result = upgrade(Path("root"), expected)
        finally:
            os.chdir(cwd)
        self.completed(approved, expected, result)
        self.assertEqual(snapshot(decoy), before)

    def test_changed_missing_file_and_unc_roots_leave_trees_untouched(self):  # TW/T1/T12
        root = self.workspace(self.base / "root")
        expected = identity(root)
        moved = self.base / "moved"
        root.rename(moved)
        self.workspace(root)
        before = snapshot(self.base)
        for path, reason in ((root, "target_changed"), (self.base / "missing", "target_changed"),
                             (root / "project.json", "target_changed"),
                             (r"\\server\share\folder", "windows_identity_failed")):
            with self.subTest(path=path):
                self.assertEqual(outcome(upgrade(path, expected)), ("blocked", reason, "not_committed"))
                self.assertEqual(snapshot(self.base), before)
