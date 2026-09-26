"""P1+P2 PACKAGE bundle-lineage contract tests.

Covers the manifest schema @3 link to tools/runtime-changes@2.json:
the real candidate tree must verify cleanly, and synthetic trees must
reject every adversarial mutation of the declared-change contract —
including the P2 new-file allowlist (before_sha256=null) rules.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._harness import ContractCase, REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import build_validation_bundle as builder  # noqa: E402
import check_bundle  # noqa: E402


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _synth_tree(tmp, *, change_a=True, tamper=None, new_file=None):
    """Minimal tree: 2 baseline files + tools + declared validation file.

    a.py starts at 'alpha-v1', is changed on disk to 'alpha-v2' and (when
    change_a) declared in runtime-changes.json. b.py stays at baseline.
    new_file, when given, is written to disk and declared with
    before_sha256=null on the approved_new_files allowlist.
    Returns the tree root.
    """
    root = Path(tmp)
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "a.py").write_text("alpha-v1\n", encoding="utf-8")
    (root / "b.py").write_text("beta-v1\n", encoding="utf-8")
    base_files = {"a.py": _sha(root / "a.py"), "b.py": _sha(root / "b.py")}
    baseline = {"schema": "knuaf-doc/baseline-files@1",
                "main": "29abec0ec95369de7ae9e22207349d574f1f46b7",
                "files": base_files}
    (root / "tools" / "baseline-files.json").write_text(
        json.dumps(baseline), encoding="utf-8")

    (root / "a.py").write_text("alpha-v2\n", encoding="utf-8")  # drift
    changes = {}
    if change_a:
        changes["a.py"] = {
            "before_sha256": base_files["a.py"],
            "after_sha256": _sha(root / "a.py"),
            "first_changed": "P1", "owner": "test",
            "reason": "synthetic change", "source": "new",
        }
    approved = []
    if new_file:
        rel, content = new_file
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        approved.append(rel)
        changes[rel] = {
            "before_sha256": None,
            "after_sha256": _sha(target),
            "first_changed": "P2", "owner": "test",
            "reason": "synthetic new file", "source": "new",
        }
    if tamper:
        tamper(changes)
    rc = {
        "schema": "knuaf-doc/runtime-changes@2",
        "stage": "P2",
        "baseline_main": baseline["main"],
        "baseline_spec": "tools/baseline-files.json",
        "baseline_spec_sha256": _sha(root / "tools" / "baseline-files.json"),
        "parent_candidate": {"stage": "P1",
                             "tree_sha256": "0" * 64, "file_count": 0},
        "approved_new_files": approved,
        "changes": changes,
    }
    (root / "tools" / "runtime-changes.json").write_text(
        json.dumps(rc, indent=1), encoding="utf-8")
    (root / "tools" / "validation-provenance.json").write_text(json.dumps({
        "schema": "knuaf-doc/validation-provenance@1",
        "defaults": {"origin": "new"},
        "readme_policy": {},
        "files": {},
    }), encoding="utf-8")
    return root


def _write_manifest(root):
    manifest = builder.build_manifest(root, builder.load_inputs(root)[1])
    (root / "tools" / "validation-bundle.json").write_bytes(
        builder.manifest_bytes(manifest))
    return manifest


def _codes(report):
    return {e["code"] for e in report["errors"]}


class RealTreeTests(ContractCase):
    def test_candidate_verifies_under_v2(self):
        # Inside a nested negative-control run the tree under test is a
        # partial copy (skills/tests/tools + README/LICENSE only), so a
        # honest pass is only expected when every declared file is present.
        # Either way the checker must be truthful: ok on a complete tree,
        # declared_missing on a partial copy.
        report = check_bundle.check(REPO_ROOT)
        manifest = json.loads(
            (REPO_ROOT / "tools/validation-bundle.json").read_text(
                encoding="utf-8"))
        complete = all(
            (REPO_ROOT / name).is_file() for name in manifest["files"])
        if complete:
            self.assertEqual("ok", report["status"],
                             f"errors: {report['errors']!r}")
            rc = report["runtime_changes"]
            self.assertEqual(rc["declared_count"], rc["verified"])
            # P12 adds the D4 identity catalogue, loader and tests, while
            # retaining P11's declared changes on the accepted parent.
            self.assertEqual(102, rc["verified"])
            self.assertEqual(75, rc["new_count"])
        else:
            self.assertNotEqual("ok", report["status"])
            self.assertIn("declared_missing", _codes(report))

    def test_manifest_v2_link(self):
        manifest = json.loads(
            (REPO_ROOT / "tools/validation-bundle.json").read_text(
                encoding="utf-8"))
        self.assertEqual("knuaf-doc/validation-bundle@3",
                         manifest["schema"])
        link = manifest["runtime_changes"]
        self.assertEqual("tools/runtime-changes.json", link["spec"])
        self.assertEqual(
            _sha(REPO_ROOT / "tools" / "runtime-changes.json"),
            link["spec_sha256"])
        self.assertEqual("P12", link["stage"])
        self.assertEqual("29abec0ec95369de7ae9e22207349d574f1f46b7",
                         link["baseline_main"])
        # P12's parent is the accepted P11 tree (main deeac74).
        self.assertEqual(102, link["change_count"])
        self.assertEqual(75, link["new_count"])
        self.assertEqual(75, link["approved_new_files"])
        self.assertEqual("P11", link["parent_candidate"]["stage"])
        self.assertEqual(164, link["parent_candidate"]["file_count"])
        self.assertEqual(
            "2ada8256a47e5e37fa26ba2c9e6aa90bb609f4da1ef60ccfe33dcee03fdba4ce",
            link["parent_candidate"]["tree_sha256"])

    def test_change_entries_carry_before_after(self):
        rc = json.loads(
            (REPO_ROOT / "tools/runtime-changes.json").read_text(
                encoding="utf-8"))
        baseline = json.loads(
            (REPO_ROOT / "tools/baseline-files.json").read_text(
                encoding="utf-8"))["files"]
        approved = set(rc["approved_new_files"])
        for name, meta in rc["changes"].items():
            self.assertEqual(_sha(REPO_ROOT / name), meta["after_sha256"])
            self.assertTrue(meta["reason"])
            self.assertTrue(meta["source"])
            if name in baseline:
                self.assertEqual(baseline[name], meta["before_sha256"])
                # Earlier stage labels retained; P11 first-changes the
                # common interview-guide docs.
                self.assertIn(meta["first_changed"],
                              {"P1", "P2", "P3", "P5", "P7", "P9", "P10", "P11", "P12"})
                self.assertIn(meta["owner"],
                              {"A", "B", "C", "P4", "D", "P6", "P7", "P8",
                               "P9", "P10", "P11", "P12"})
            else:
                # P2 through P12 new files: pre-declared allowlist only.
                self.assertIn(name, approved)
                self.assertIsNone(meta["before_sha256"])
                self.assertIn(meta["first_changed"],
                              {"P2", "P3", "P4", "P5", "P6", "P8", "P9",
                               "P10", "P11", "P12"})
                self.assertIn(meta["owner"],
                              {"A", "B", "C", "S", "P4", "P5", "P6", "P7",
                               "P8", "P9", "P10", "P11", "P12"})


class SyntheticLineageTests(ContractCase):
    def test_clean_synthetic_tree_passes(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            _write_manifest(root)
            report = check_bundle.check(root)
            self.assertEqual("ok", report["status"],
                             f"errors: {report['errors']!r}")
            self.assertEqual("declared_change",
                             report["baseline"]["states"]["a.py"])
            self.assertEqual("ok", report["baseline"]["states"]["b.py"])

    def test_false_before_sha_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, tamper=lambda c: c["a.py"].update(
                before_sha256="0" * 64))
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_before_mismatch", _codes(report))

    def test_false_after_sha_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            # declared after matches disk at write time; then drift the file
            _write_manifest(root)
            (root / "a.py").write_text("alpha-v3\n", encoding="utf-8")
            report = check_bundle.check(root)
            self.assertIn("runtime_change_after_mismatch", _codes(report))

    def test_undeclared_drift_rejected_and_blocks_build(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, change_a=False)
            # builder must refuse: a.py drifted with no declared entry
            with self.assertRaises(builder.RuntimeChangeError):
                builder.build_manifest(
                    root, builder.load_inputs(root)[1])
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("undeclared_runtime_change", _codes(report))

    def test_non_baseline_change_entry_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            def add(c):
                c["tools/runtime-changes.json"] = {
                    "before_sha256": "0" * 64, "after_sha256": "1" * 64,
                    "first_changed": "P1", "owner": "x", "reason": "x",
                    "source": "new"}
            root = _synth_tree(tmp, tamper=add)
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_self_exempt", _codes(report))

    def test_not_baseline_path_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            def add(c):
                c["skills/other.py"] = {
                    "before_sha256": "0" * 64, "after_sha256": "1" * 64,
                    "first_changed": "P1", "owner": "x", "reason": "x",
                    "source": "new"}
            root = _synth_tree(tmp, tamper=add)
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_not_baseline", _codes(report))

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            def add(c):
                c["../escape.py"] = {
                    "before_sha256": "0" * 64, "after_sha256": "1" * 64,
                    "first_changed": "P1", "owner": "x", "reason": "x",
                    "source": "new"}
            root = _synth_tree(tmp, tamper=add)
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_path_escape", _codes(report))

    def test_duplicate_change_key_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            rc_path = root / "tools" / "runtime-changes.json"
            doc = json.loads(rc_path.read_text(encoding="utf-8"))
            entry = json.dumps(doc["changes"]["a.py"])
            raw = (
                '{"schema": "knuaf-doc/runtime-changes@2",'
                '"stage": "P2",'
                f'"baseline_main": {json.dumps(doc["baseline_main"])},'
                '"baseline_spec": "tools/baseline-files.json",'
                f'"baseline_spec_sha256": '
                f'{json.dumps(doc["baseline_spec_sha256"])},'
                '"changes": {"a.py": %s, "a.py": %s}}'
            ) % (entry, entry)
            rc_path.write_text(raw, encoding="utf-8")
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_changes_duplicate_key", _codes(report))

    def test_noop_change_entry_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            def noop(c):
                c["a.py"]["after_sha256"] = c["a.py"]["before_sha256"]
            root = _synth_tree(tmp, tamper=noop)
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_noop", _codes(report))

    def test_missing_declared_file_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            _write_manifest(root)
            (root / "a.py").unlink()
            report = check_bundle.check(root)
            self.assertIn("runtime_change_missing", _codes(report))

    def test_double_declared_path_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            manifest = _write_manifest(root)
            manifest["files"]["a.py"] = {
                "sha256": _sha(root / "a.py"), "origin": "new"}
            (root / "tools" / "validation-bundle.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            report = check_bundle.check(root)
            self.assertIn("runtime_change_double_declared", _codes(report))

    def test_manifest_link_tamper_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            manifest = _write_manifest(root)
            manifest["runtime_changes"]["spec_sha256"] = "f" * 64
            (root / "tools" / "validation-bundle.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            report = check_bundle.check(root)
            self.assertIn("manifest_runtime_changes", _codes(report))

    def test_missing_change_list_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, change_a=False)
            (root / "tools" / "runtime-changes.json").unlink()
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_changes_unreadable", _codes(report))

    # --- P2 new-file contract (before_sha256=null + allowlist) ---

    def test_declared_new_file_passes(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, new_file=("scripts/gg_new.py", "x = 1\n"))
            _write_manifest(root)
            report = check_bundle.check(root)
            self.assertEqual("ok", report["status"],
                             f"errors: {report['errors']!r}")
            self.assertEqual(1, report["runtime_changes"]["new_count"])
            # declared new file must not leak into manifest files[]
            manifest = json.loads(
                (root / "tools" / "validation-bundle.json").read_text())
            self.assertNotIn("scripts/gg_new.py", manifest["files"])

    def test_new_file_not_allowlisted_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            rc_path = root / "tools" / "runtime-changes.json"
            doc = json.loads(rc_path.read_text(encoding="utf-8"))
            target = root / "scripts" / "planted.py"
            target.parent.mkdir(exist_ok=True)
            target.write_text("x = 2\n", encoding="utf-8")
            doc["changes"]["scripts/planted.py"] = {
                "before_sha256": None, "after_sha256": _sha(target),
                "first_changed": "P2", "owner": "x", "reason": "x",
                "source": "new"}
            rc_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_new_not_allowlisted",
                          _codes(report))

    def test_baseline_masquerading_as_new_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            rc_path = root / "tools" / "runtime-changes.json"
            doc = json.loads(rc_path.read_text(encoding="utf-8"))
            doc["approved_new_files"] = ["b.py"]
            doc["changes"]["b.py"] = {
                "before_sha256": None, "after_sha256": _sha(root / "b.py"),
                "first_changed": "P2", "owner": "x", "reason": "x",
                "source": "new"}
            rc_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_new_is_baseline", _codes(report))

    def test_declared_new_missing_on_disk_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, new_file=("scripts/gg_new.py", "x = 1\n"))
            _write_manifest(root)
            (root / "scripts" / "gg_new.py").unlink()
            report = check_bundle.check(root)
            self.assertIn("runtime_change_missing", _codes(report))

    def test_declared_new_after_tamper_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, new_file=("scripts/gg_new.py", "x = 1\n"))
            _write_manifest(root)
            (root / "scripts" / "gg_new.py").write_text(
                "x = 2\n", encoding="utf-8")
            report = check_bundle.check(root)
            self.assertIn("runtime_change_after_mismatch", _codes(report))

    def test_new_file_without_stage_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp, new_file=("scripts/gg_new.py", "x = 1\n"))
            rc_path = root / "tools" / "runtime-changes.json"
            doc = json.loads(rc_path.read_text(encoding="utf-8"))
            doc["changes"]["scripts/gg_new.py"]["first_changed"] = ""
            rc_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_new_no_stage", _codes(report))

    def test_allowlist_naming_baseline_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            rc_path = root / "tools" / "runtime-changes.json"
            doc = json.loads(rc_path.read_text(encoding="utf-8"))
            doc["approved_new_files"] = ["b.py"]
            rc_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            _write_manifest_silent(root)
            report = check_bundle.check(root)
            self.assertIn("runtime_change_allowlist_invalid", _codes(report))

    def test_undeclared_new_file_rejected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-lin-") as tmp:
            root = _synth_tree(tmp)
            _write_manifest(root)  # manifest first — the plant stays undeclared
            (root / "scripts").mkdir(exist_ok=True)
            (root / "scripts" / "planted.py").write_text(
                "x = 3\n", encoding="utf-8")
            report = check_bundle.check(root)
            self.assertIn("undeclared_file", _codes(report))


def _write_manifest_silent(root):
    """Write a manifest ignoring runtime-change refusal (adversarial aid)."""
    try:
        _write_manifest(root)
    except builder.RuntimeChangeError:
        (root / "tools" / "validation-bundle.json").write_text(
            json.dumps({"schema": "knuaf-doc/validation-bundle@3",
                        "baseline": {}, "runtime_changes": {},
                        "files": {}, "readme_policy": {}}),
            encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
