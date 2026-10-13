"""Real Windows publication acceptance; no platform/port mocks.

Module discovery is deliberately empty on POSIX to preserve the existing
approved skip ledger. PublicationLifecycle can be run explicitly on POSIX
as regression evidence, never as native Windows evidence.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests._harness import SCRIPTS, bind_major, runtime
from tests.test_p2_core import _seed, _output_value
from tests.test_p2_publication import (
    _entries, _export_intent, _import_intent, _paper_intent,
)

core = runtime("gg_core")
fs = runtime("gg_fs")
lock = runtime("gg_lock")
pub = runtime("gg_publication")
winfs = runtime("gg_winfs")


def _child_status(root):
    code = (
        "import sys,json; sys.path.insert(0, sys.argv[1]); import gg_lock; "
        "print(json.dumps(gg_lock.probe_lock(sys.argv[2])))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(SCRIPTS), str(root)],
        capture_output=True, encoding="utf-8", timeout=20,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def _child_probe(root):
    return _child_status(root)["lock_state"]


class PublicationLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="winpub-")
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.root = self.parent / "한글 작업 공간"
        if sys.platform == "win32":
            self.assertIsInstance(lock._PORT, lock._WindowsPort)
            self.assertTrue(winfs.supported())
        core.init(self.root)
        self.assertEqual(core.load(self.root)["revision"], 0)
        self._released(self.root)

    def _released(self, root):
        self.assertFalse((root / ".gg-lock/owner.json").exists())
        self.assertEqual(lock.probe_lock(root)["lock_state"], "free")

    def _seed(self):
        _seed(self.root)
        bind_major(self.root)

    def _cli(self, *args):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg.py"), *map(str, args)],
            cwd=self.parent, env=env, encoding="utf-8", capture_output=True,
            timeout=30,
        )

    def test_cli_init_apply_revision_replay_and_cleanup(self):
        root = self.parent / "CLI 한글 공간"
        result = self._cli("init", root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        change = {"request_id": "native:apply", "ops": [
            {"collection": "tasks", "value": {"id": "t1"}}]}
        (root / "change.json").write_bytes(fs.canonical_json_bytes(change))
        args = ("apply", root, "--change", root / "change.json", "--expected-revision", "0")
        result = self._cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(core.load(root)["revision"], 1)
        before = (root / "project.json").read_bytes()
        result = self._cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root / "project.json").read_bytes(), before)
        self._released(root)

    def test_raw_binary_protocol_payload_roundtrip(self):
        data = b"\n\r\n\x1a\x00\xff" + "한글".encode("utf-8")
        parent = fs.identity(self.root, kind="directory")
        result = fs.publish_bytes_noreplace(self.root / "raw.bin", data,
                                            parent_identity=parent)
        self.assertEqual(result["state"], "installed")
        read, ident = fs.read_bytes(self.root / "raw.bin")
        self.assertEqual(read, data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(ident, fs.identity(self.root / "raw.bin", kind="file"))

    def test_live_guard_identity_assert_held_and_reacquire(self):
        with lock.Lock(self.root) as cap:
            self.assertEqual(fs.identity(self.root / ".gg-lock/guard", kind="file"),
                             fs.fd_identity(cap._fd, kind="file"))
            lock.assert_held(cap, self.root)
            self.assertTrue((self.root / ".gg-lock/owner.json").is_file())
        self._released(self.root)
        with lock.Lock(self.root) as cap:
            lock.assert_held(cap, self.root)
        self._released(self.root)

    def test_binary_output_receipt_hash_replay_and_id_conflict(self):
        self._seed()
        data = "한글\n".encode("utf-8") + b"\r\n"
        (self.root / "원본.md").write_bytes(data)
        p = core.load(self.root)
        value = _output_value(core, self.root, "원본.md", "md", data, p=p)
        first = core.adopt_output(self.root, value, p["revision"], "native:output",
                                  major_id="specialty_crops")
        current = core.load(self.root)
        ref = current["outputs"][value["id"]]["publication_ref"]
        receipt = pub.verify_publication(self.root, publication_ref=ref)
        raw = (self.root / ref["path"]).read_bytes()
        self.assertEqual(raw, fs.canonical_json_bytes(receipt))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
        self.assertEqual(Path(first["path"]).read_bytes(), data)
        self.assertEqual((self.root / "원본.md").read_bytes(), data)
        again = core.adopt_output(self.root, value, p["revision"], "native:output",
                                  major_id="specialty_crops")
        self.assertEqual(first["path"], again["path"])
        changed = dict(value, label="다른 요청")
        with self.assertRaises(core.OperationError) as cm:
            core.adopt_output(self.root, changed, p["revision"], "native:output",
                              major_id="specialty_crops")
        self.assertEqual(cm.exception.result["commit_state"], "not_committed")
        self.assertIn("conflict", cm.exception.result["reason"])
        self.assertEqual(core.load(self.root)["revision"], current["revision"])
        self._released(self.root)

    def test_nested_korean_space_paper_receipt_replay_resume(self):
        self._seed()
        spec = {"author": "합성", "writing_year": 2026, "years": 5,
                "school_profile": {"mode": "school", "school": "합성대학교",
                                   "department": "특용작물학과"}}
        raw = fs.canonical_json_bytes(spec)
        requested = "보고서 공간/중첩 폴더/논문.md"
        (self.root / "paper-spec.json").write_bytes(raw)
        first = core.paper(self.root, "paper-spec.json", raw, requested,
                           major_id="specialty_crops")
        self.assertEqual(first["status"], "generated")
        self.assertTrue((self.root / requested).is_file())
        receipt = pub.find_request(self.root, request_id=first["request_id"])[0]
        self.assertEqual(receipt["request"]["requested_path"], requested)
        before = (self.root / requested).read_bytes()
        again = core.paper(self.root, "paper-spec.json", raw, requested,
                           major_id="specialty_crops")
        self.assertEqual(again["status"], "existing")
        self.assertEqual(first["request_id"], again["request_id"])
        (self.root / requested).unlink()
        resumed = core.paper(self.root, "paper-spec.json", raw, requested,
                             major_id="specialty_crops")
        self.assertEqual(resumed["request_id"], first["request_id"])
        self.assertEqual((self.root / requested).read_bytes(), before)
        self._released(self.root)

    def test_export_receipt_replay_and_foreign_destination_preserved(self):
        self._seed()
        first = core.export(self.root, "review", major_id="specialty_crops")
        self.assertEqual(first["status"], "generated")
        receipt = pub.find_request(self.root, request_id=first["request_id"])[0]
        self.assertEqual(receipt["kind"], "export")
        again = core.export(self.root, "review", major_id="specialty_crops")
        self.assertEqual(again["status"], "existing")
        self.assertEqual(first["request_id"], again["request_id"])
        dest = self.root / "build" / str(core.load(self.root)["revision"]) / "draft"
        dest.mkdir()
        (dest / "foreign.bin").write_bytes(b"foreign\r\n\x1a")
        canonical = (self.root / "project.json").read_bytes()
        with self.assertRaises(core.OperationError) as cm:
            core.export(self.root, "draft", major_id="specialty_crops")
        self.assertEqual(cm.exception.result["commit_state"], "not_committed")
        self.assertEqual((dest / "foreign.bin").read_bytes(), b"foreign\r\n\x1a")
        self.assertEqual((self.root / "project.json").read_bytes(), canonical)
        self._released(self.root)

    def test_import_core_inventory_replay_source_and_foreign_preserved(self):
        source = self.parent / "legacy source"
        (source / "sections").mkdir(parents=True)
        data = "# 개요\n본문\n".encode("utf-8")
        (source / "sections/01-개요.md").write_bytes(data)
        dest = self.parent / "이동 목적지"
        first = core.migrate(source, dest, offline_confirmed=True)
        receipt = json.loads((dest / ".gg-import-publication.json").read_bytes())
        self.assertEqual(receipt["request"]["destination"], str(dest))
        self.assertIn("sections/01-개요.md", [e["path"] for e in receipt["request"]["source_inventory"]])
        self.assertEqual((source / "sections/01-개요.md").read_bytes(), data)
        again = core.migrate(source, dest, offline_confirmed=True)
        self.assertEqual(first["revision"], again["revision"])
        foreign = self.parent / "foreign import"
        foreign.mkdir()
        (foreign / "keep.bin").write_bytes(b"keep")
        with self.assertRaises(core.OperationError):
            core.migrate(source, foreign, offline_confirmed=True)
        self.assertEqual((foreign / "keep.bin").read_bytes(), b"keep")
        self._released(dest)

    def test_import_rebind_retains_live_guard_across_move(self):
        stage = self.parent / "staging workspace"
        stage.mkdir()
        lock.install_new(stage)
        (stage / "payload.txt").write_bytes(b"bytes\n\r\n\x1a")
        files = _entries(stage)
        dest = self.parent / "native moved workspace"
        with lock.Lock(stage) as cap:
            guard = cap.guard_identity
            result = pub.publish_workspace(stage, cap, destination=str(dest),
                intent=_import_intent(dest, files), files=files)
            self.assertEqual(result["state"], "published")
            self.assertEqual(result["cleanup_errors"], [])
            self.assertFalse(stage.exists())
            lock.assert_held(cap, dest)
            self.assertEqual(cap.guard_identity, guard)
            with self.assertRaises(fs.FsError) as missing:
                fs.identity(stage, kind="directory")
            self.assertEqual(missing.exception.reason, "stat_failed")
            self.assertEqual(missing.exception.detail["code"], errno.ENOENT)
            real_identity = fs.identity
            for reason, code in (("stat_failed", errno.EACCES),
                                 ("stat_failed", errno.EIO),
                                 ("windows_identity_failed", errno.ENOENT),
                                 ("symlink_reparse", "symlink")):
                def unavailable(path, *, kind):
                    if Path(path) == stage:
                        raise fs.FsError(reason, code=code, operation="identity", path=path)
                    return real_identity(path, kind=kind)
                with self.subTest(reason=reason, code=code), mock.patch.object(
                        fs, "identity", side_effect=unavailable):
                    with self.assertRaises(lock.LockError):
                        lock.rebind_after_rename(cap, previous_root=stage, new_root=dest)
                    self.assertEqual(cap.root, dest)
                    lock.assert_held(cap, dest)
            self.assertEqual(_child_probe(dest), "unavailable" if sys.platform == "win32" else "busy")
        self._released(dest)
        self.assertEqual(_child_probe(dest), "free")

    def test_two_process_lock_contention_then_release_reacquire(self):
        with lock.Lock(self.root):
            status = _child_status(self.root)
            self.assertEqual(status["lock_state"], "unavailable" if sys.platform == "win32" else "busy")
            self.assertEqual(status["reason"], "busy_or_denied" if sys.platform == "win32" else "guard_busy")
        self.assertEqual(_child_probe(self.root), "free")
        # Child acquires the same guard after the parent released it.
        code = (
            "import sys; sys.path.insert(0,sys.argv[1]); import gg_lock; "
            "cm=gg_lock.Lock(sys.argv[2]); cap=cm.__enter__(); "
            "gg_lock.assert_held(cap,sys.argv[2]); cm.__exit__(None,None,None); "
            "print('reacquired')"
        )
        child = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS), str(self.root)],
                               capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "reacquired")
        self._released(self.root)

    def test_foreign_owner_cleanup_error_keeps_guard_releasable(self):
        with self.assertRaises(lock.LockCleanupError) as cm:
            with lock.Lock(self.root):
                (self.root / ".gg-lock/owner.json").write_bytes(b'{"foreign":true}\n')
        self.assertTrue(cm.exception.release_confirmed)
        self.assertEqual((self.root / ".gg-lock/owner.json").read_bytes(), b'{"foreign":true}\n')
        self.assertEqual(_child_probe(self.root), "free")
        # Holding the real OS guard permits replacement of stale metadata.
        with lock.Lock(self.root) as cap:
            lock.assert_held(cap, self.root)
        self._released(self.root)

    def test_tampered_receipt_and_payload_fail_verification(self):
        stage = self.parent / "stage"
        stage.mkdir()
        (stage / "a.bin").write_bytes(b"original")
        intent = _export_intent()
        with lock.Lock(self.root) as cap:
            result = pub.publish_directory(self.root, cap, staging=stage,
                destination="build/3/draft", intent=intent, files=_entries(stage))
        ref = result["publication_ref"]
        original = (self.root / ref["path"]).read_bytes()
        (self.root / "build/3/draft/a.bin").write_bytes(b"changed")
        with self.assertRaises((ValueError, OSError)):
            pub.verify_publication(self.root, publication_ref=ref)
        (self.root / "build/3/draft/a.bin").write_bytes(b"original")
        (self.root / ref["path"]).write_bytes(original + b" ")
        with self.assertRaises((ValueError, OSError)):
            pub.verify_publication(self.root, publication_ref=ref)
        self._released(self.root)


class WindowsPrimitives(PublicationLifecycle):
    """Native-only additional cases; inherited lifecycle runs only once."""
    def test_junction_parent_read_write_inventory_refused(self):
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"keep")
        junction = self.root / "junction"
        made = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                              capture_output=True, timeout=20)
        self.assertEqual(made.returncode, 0, repr(made.stdout + made.stderr))
        self.addCleanup(os.rmdir, junction)
        with self.assertRaises((OSError, ValueError)):
            pub._read_file(self.root, "junction/keep.txt")
        with self.assertRaises((OSError, ValueError)):
            pub._scan_tree(self.root)
        managed = self.root / "managed.bin"
        managed.write_bytes(b"payload")
        with lock.Lock(self.root) as cap:
            with self.assertRaises((OSError, ValueError)):
                pub.publish_requested_file(self.root, cap,
                    managed={"path": "managed.bin", "sha256": fs.sha256_bytes(b"payload")},
                    requested_path="junction/new.txt")
        self.assertEqual((outside / "keep.txt").read_bytes(), b"keep")
        self.assertFalse((outside / "new.txt").exists())

    def test_windows_aliases_refused_before_write(self):
        (self.root / "managed.bin").write_bytes(b"payload")
        managed = {"path": "managed.bin", "sha256": fs.sha256_bytes(b"payload")}
        canonical = (self.root / "project.json").read_bytes()
        with lock.Lock(self.root) as cap:
            for rel in ("new/file:stream", "new/CON.txt", "new/NUL", "new/aux.md",
                        "new/com1.txt", "new/LPT¹.txt", "new/trailing.", "new/space "):
                with self.subTest(rel=rel), self.assertRaises(ValueError):
                    pub.publish_requested_file(self.root, cap, managed=managed, requested_path=rel)
        self.assertFalse((self.root / "new").exists())
        self.assertEqual((self.root / "project.json").read_bytes(), canonical)
        parent = fs.identity(self.root, kind="directory")
        result = fs.publish_bytes_noreplace(self.root / "file.", b"bytes", parent_identity=parent)
        self.assertEqual(result["state"], "not_installed")
        self.assertFalse((self.root / "file").exists())
        self.assertFalse(list(self.root.glob(".file*")))
        with self.assertRaises(ValueError):
            fs.mkdir_directory(self.root / "aliased.")
        self.assertFalse((self.root / "aliased").exists())
        (self.root / "move-source.bin").write_bytes(b"source")
        result = fs.rename_noreplace(self.root / "move-source.bin", self.root / "move-target.",
            source_identity=fs.identity(self.root / "move-source.bin", kind="file"),
            source_parent_identity=parent, destination_parent_identity=parent)
        self.assertEqual(result["state"], "not_installed")
        self.assertEqual((self.root / "move-source.bin").read_bytes(), b"source")
        self.assertFalse((self.root / "move-target").exists())
        for path in ("C:relative", "\\\\server\\share\\dest", "\\\\?\\C:\\dest", "C:/a/../dest"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pub._check_abs_destination(path)

    def test_native_directory_pin_denies_rename_and_file_read_denies_write(self):
        directory = self.root / "pinned"
        directory.mkdir()
        with winfs.Directory(directory):
            with self.assertRaises(OSError):
                os.rename(directory, self.root / "renamed")
        os.rename(directory, self.root / "renamed")
        target = self.root / "read.bin"
        target.write_bytes(b"read\r\n\x1a")
        fd = winfs.open_file(target, os.O_RDONLY)
        try:
            with self.assertRaises(OSError):
                with target.open("wb"):
                    pass
            self.assertEqual(os.read(fd, 100), b"read\r\n\x1a")
        finally:
            os.close(fd)
        self.assertEqual(target.read_bytes(), b"read\r\n\x1a")

    def test_native_no_replace_and_durability_limit(self):
        parent = fs.identity(self.root, kind="directory")
        target = self.root / "bytes.bin"
        first = fs.publish_bytes_noreplace(target, b"\n\r\n\x1a\x00", parent_identity=parent)
        self.assertEqual(first["state"], "installed")
        self.assertEqual(first["cleanup_errors"], [])
        self.assertEqual(first["durability_notes"][0]["reason"], "dir_fsync_unsupported")
        other = fs.publish_bytes_noreplace(target, b"foreign", parent_identity=parent)
        self.assertEqual(other["state"], "not_installed")
        self.assertEqual(target.read_bytes(), b"\n\r\n\x1a\x00")
        with self.assertRaises(fs.FsError) as cm:
            fs.fsync_dir(self.root)
        self.assertEqual(cm.exception.reason, "dir_fsync_unsupported")


def load_tests(loader, _tests, _pattern):
    if sys.platform != "win32":
        return unittest.TestSuite()
    # WindowsPrimitives inherits lifecycle cases, so collect only that class.
    return loader.loadTestsFromTestCase(WindowsPrimitives)


if __name__ == "__main__":
    unittest.main()
