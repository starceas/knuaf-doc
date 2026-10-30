"""Public history/restore contracts; synthetic projects, no external tools."""
import json
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, fact_op, runtime, source_op, write_text


class FactEnumTests(ContractCase):
    def test_invalid_enum_identifies_field_without_leaking_value(self):
        core = runtime("gg_core")
        for field in ("kind", "answer_state", "verification"):
            for value in ("private-answer", [], {}, None):
                with self.subTest(field=field, value=value):
                    root = self.make_project()
                    write_text(root, "answer.txt", "synthetic\n")
                    fact = fact_op("f", "farm.name", "synthetic", None)
                    fact["value"][field] = value
                    before = self._tree_bytes(root)
                    with self.assertRaises(core.OperationError) as caught:
                        core.apply(root, {"request_id": "bad", "ops": [source_op(), fact]}, 0)
                    result = caught.exception.result
                    self.assertEqual("not_committed", result["commit_state"])
                    self.assertEqual({"code": "fact_enum_invalid", "field": field, "index": 0}, result["detail"])
                    self.assertNotIn("private-answer", json.dumps(result))
                    self.assertEqual(before, self._tree_bytes(root))


class HistoryRestoreTests(ContractCase):
    def seed(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "synthetic\n")
        core.apply(root, {"request_id": "seed", "ops": [source_op(), fact_op("f", "farm.name", "old", None)]}, 0)
        fact = fact_op("f", "farm.name", "new", None)
        core.apply(root, {"request_id": "edit", "ops": [fact]}, 1)
        return core, root

    def test_history_verifies_snapshots_without_writing(self):
        core, root = self.seed()
        before = self._tree_bytes(root)
        rows = core.history(root)
        self.assertEqual([0, 1, 2], [r["revision"] for r in rows])
        self.assertEqual([True, True, None], [r["snapshot_ok"] for r in rows])
        self.assertTrue(rows[-1]["current"])
        self.assertEqual(before, self._tree_bytes(root))
        write_text(root, "migration/revision-1.json", "{}")
        self.assertFalse(core.history(root)[1]["snapshot_ok"])

    def test_restore_is_new_commit_keeps_requests_and_external_bytes(self):
        core, root = self.seed()
        before = core.load(root)
        write_text(root, "answer.txt", "edited outside the ledger\n")
        write_text(root, "build/existing.bin", "preserved")
        result = core.restore(root, 1, 2)
        p = core.load(root)
        self.assertEqual(("committed", 3, 1), (result["commit_state"], result["revision"], result["restored_from"]))
        self.assertEqual("old", p["facts"]["f"]["value"])
        self.assertEqual(3, p["facts"]["f"]["revision"])
        self.assertEqual(3, p["facts"]["f"]["source_refs"][0]["revision"])
        self.assertEqual(core.digest(before), p["parent_hash"])
        self.assertEqual(before, json.loads((root / result["pre_restore_snapshot"]).read_text()))
        self.assertEqual(before["history"], p["history"][:-1])
        for key, value in before["requests"].items():
            self.assertEqual(value, p["requests"][key])
        self.assertEqual("edited outside the ledger\n", (root / "answer.txt").read_text())
        self.assertEqual("preserved", (root / "build/existing.bin").read_text())
        self.assertNotEqual(p["sources"]["answer"]["hash"], core.digest((root / "answer.txt").read_bytes()))
        core.restore(root, 2, 3)
        self.assertEqual("new", core.load(root)["facts"]["f"]["value"])

    def test_retry_does_not_commit_twice(self):
        core, root = self.seed()
        first = core.restore(root, 1, 2)
        before = self._tree_bytes(root)
        self.assertEqual(first, core.restore(root, 1, 2))
        self.assertEqual(before, self._tree_bytes(root))
        core.apply(root, {"request_id": "after", "ops": []}, 3)
        core.restore(root, 1, 2)
        self.assertEqual(4, core.load(root)["revision"])

    def test_invalid_or_stale_revision_never_writes(self):
        core, root = self.seed()
        for revision, expected in ((True, 2), (1, True), (-1, 2), (2, 2), (0, 1), (1, "2")):
            with self.subTest(revision=revision, expected=expected):
                before = self._tree_bytes(root)
                with self.assertRaises(core.OperationError) as caught:
                    core.restore(root, revision, expected)
                self.assertEqual("not_committed", caught.exception.result["commit_state"])
                self.assertEqual(before, self._tree_bytes(root))

    def test_tampered_missing_and_escaping_snapshots_are_rejected(self):
        for damage in ("tamper", "missing", "escape"):
            with self.subTest(damage=damage):
                core, root = self.seed()
                path = root / "migration/revision-1.json"
                if damage == "tamper":
                    path.write_text("{}")
                else:
                    path.unlink()
                    if damage == "escape":
                        path.symlink_to(root.parent / "outside-snapshot.json")
                before = self._tree_bytes(root) if damage != "escape" else (root / "project.json").read_bytes()
                with self.assertRaises(core.OperationError) as caught:
                    core.restore(root, 1, 2)
                self.assertEqual("snapshot_invalid", caught.exception.result["reason"])
                after = self._tree_bytes(root) if damage != "escape" else (root / "project.json").read_bytes()
                self.assertEqual(before, after)

    def test_hash_matched_but_invalid_schema_or_project_is_rejected(self):
        for damage in ("schema", "project", "revision"):
            with self.subTest(damage=damage):
                core, root = self.seed()
                path = root / "migration/revision-1.json"
                snap = json.loads(path.read_text())
                if damage == "schema":
                    snap["facts"]["f"]["kind"] = "bad"
                elif damage == "project":
                    snap["project_id"] = "foreign"
                else:
                    snap["revision"] = 0
                path.write_text(json.dumps(snap))
                p = core.load(root)
                p["history"][1]["hash"] = core.digest(snap)
                (root / "project.json").write_text(json.dumps(p))
                before = self._tree_bytes(root)
                self.assertFalse(core.history(root)[1]["snapshot_ok"])
                with self.assertRaises(core.OperationError):
                    core.restore(root, 1, 2)
                self.assertEqual(before, self._tree_bytes(root))

    def test_conflicting_pre_restore_snapshot_is_preserved(self):
        core, root = self.seed()
        write_text(root, "migration/revision-2.json", "{}")
        before = self._tree_bytes(root)
        with self.assertRaises(core.OperationError) as caught:
            core.restore(root, 1, 2)
        self.assertEqual("restore_preflight_failed", caught.exception.result["reason"])
        self.assertEqual(before, self._tree_bytes(root))

    def test_active_guard_and_recovery_block_restore(self):
        core, root = self.seed()
        lock = runtime("gg_lock")
        with lock.Lock(root):
            before = self._tree_bytes(root)
            with self.assertRaises(lock.LockError):
                core.restore(root, 1, 2)
            self.assertEqual(before, self._tree_bytes(root))
        write_text(root, ".gg-recovery-active.json", "{}")
        before = self._tree_bytes(root)
        with self.assertRaises(lock.LockError):
            core.restore(root, 1, 2)
        self.assertEqual(before, self._tree_bytes(root))

    def test_commit_failure_reports_actual_storage_state_and_snapshot(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                core, root = self.seed()
                real = core.atomic
                def fail(path, data):
                    if Path(path).name == "project.json":
                        if landed:
                            real(path, data)
                        raise OSError("synthetic commit fault")
                    return real(path, data)
                with mock.patch.object(core, "atomic", fail):
                    with self.assertRaises(core.OperationError) as caught:
                        core.restore(root, 1, 2)
                result = caught.exception.result
                self.assertEqual("committed_cleanup_pending" if landed else "not_committed", result["commit_state"])
                self.assertEqual(3 if landed else 2, core.load(root)["revision"])
                self.assertEqual(["migration/revision-2.json"], result["preserved_paths"])

    def test_release_failure_preserves_committed_result(self):
        core, root = self.seed()
        lock = runtime("gg_lock")
        class CleanupFailure(lock.Lock):
            def __exit__(self, *exc):
                super().__exit__(*exc)
                raise lock.LockCleanupError([OSError("synthetic")], release_confirmed=True)
        with mock.patch.object(lock, "Lock", CleanupFailure):
            with self.assertRaises(core.OperationError) as caught:
                core.restore(root, 1, 2)
        self.assertEqual("committed_cleanup_pending", caught.exception.result["commit_state"])
        self.assertEqual(3, core.load(root)["revision"])

    def test_restore_invalidates_reviews_outputs_and_running_execution(self):
        core, root = self.seed()
        p = core.load(root)
        refs = [{"collection": "facts", "id": "f"}]
        output = {"id": "out", "revision": 1, "path": "build/out.md", "format": "md", "file_hash": core.digest(b"old"), "target_refs": refs, "input_fingerprint": core.fingerprint(root, p, refs), "publication_ref": {"immutable": "synthetic"}}
        p["outputs"]["out"] = output
        p["reviews"]["r"] = {"id": "r", "revision": 1, "target_refs": refs, "input_fingerprint": core.fingerprint(root, p, refs)}
        p["approvals"]["a"] = {"id": "a", "revision": 1, "target_refs": refs, "input_fingerprint": core.fingerprint(root, p, refs)}
        p["tasks"]["t"] = {"id": "t", "revision": 1, "execution": {"status": "running"}}
        (root / "project.json").write_text(json.dumps(p))
        core.apply(root, {"request_id": "snapshot", "ops": []}, 2)
        core.restore(root, 2, 3)
        restored = core.load(root)
        self.assertEqual(output["publication_ref"], restored["outputs"]["out"]["publication_ref"])
        self.assertEqual("unknown", restored["tasks"]["t"]["execution"]["status"])
        core.apply(root, {"request_id": "noop", "ops": []}, 4)
        for collection, key in (("reviews", "r"), ("approvals", "a"), ("outputs", "out")):
            self.assertTrue(core.load(root)[collection][key]["stale"])
