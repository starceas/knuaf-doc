"""P1+P2 PACKAGE bundle-lineage contract tests.

Covers the manifest schema @3 link to tools/runtime-changes@2.json:
the real candidate tree must verify cleanly, and synthetic trees must
reject every adversarial mutation of the declared-change contract —
including the P2 new-file allowlist (before_sha256=null) rules.
"""
import hashlib
import json
import os
import shutil
import subprocess
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
    # Minimal valid deployment inputs; these trees test bundle lineage.
    (root / "docs").mkdir()
    (root / "docs" / "asset-disposition.json").write_text(json.dumps({
        "assets": [], "required_builtin_source_ids": []}), encoding="utf-8")
    skill = root / "skills" / "knuaf-doc"
    (skill / "references").mkdir(parents=True)
    (skill / "install-manifest.json").write_text(
        json.dumps({"files": {}}), encoding="utf-8")
    (skill / "references" / "builtin-sources.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8")
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
        self.assertEqual("P28", link["stage"])
        self.assertEqual("29abec0ec95369de7ae9e22207349d574f1f46b7",
                         link["baseline_main"])
        # P28's parent is the accepted P27 tree (main d08e98e, 233 files).
        self.assertEqual("P27", link["parent_candidate"]["stage"])
        self.assertEqual(233, link["parent_candidate"]["file_count"])
        self.assertEqual(
            "78383df4921946e8f37a7a8ae354f08667ff4317d2285c0317a535cbb9e97cfb",
            link["parent_candidate"]["tree_sha256"])
        # The ancestor list ends at the parent; the current stage is not
        # one of its own ancestors.
        lineage = link["stage_lineage"]
        self.assertEqual(
            {k: link["parent_candidate"][k]
             for k in ("stage", "file_count", "tree_sha256")},
            {k: lineage[-1][k] for k in ("stage", "file_count", "tree_sha256")})
        self.assertNotIn("P28", [row["stage"] for row in lineage])
        # D5 (P15) pinned its own parent, the P14 tree; keep that pin on
        # the P14 ancestor row now that P15 is itself an ancestor.
        d5 = json.loads((REPO_ROOT / "docs/validation/d5-rda-useful-life.json")
                        .read_text(encoding="utf-8"))
        p14 = [row for row in link["stage_lineage"] if row["stage"] == "P14"]
        self.assertEqual([d5["parent_tree_sha256"]],
                         [row["tree_sha256"] for row in p14])

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
                              {"P1", "P2", "P3", "P5", "P7", "P9", "P10", "P11", "P12", "P13", "P15", "P25", "P26", "P27", "P28"})
                self.assertIn(meta["owner"],
                              {"A", "B", "C", "P4", "D", "P6", "P7", "P8",
                               "P9", "P10", "P11", "P12", "P13", "P14", "P15",
                               "P16", "P17", "P18", "P20", "P21", "P22", "P23", "P24", "P25", "P26", "P27", "P28"})
            else:
                # New files require the pre-declared allowlist.
                self.assertIn(name, approved)
                self.assertIsNone(meta["before_sha256"])
                self.assertIn(meta["first_changed"],
                              {"P2", "P3", "P4", "P5", "P6", "P8", "P9",
                               "P10", "P11", "P12", "P13", "P14", "P15",
                               "P17", "P18", "P19", "P20", "P21", "P22", "P25", "P26", "P27", "P28", "dairy-0.1.0", "dairy-f1"})
                self.assertIn(meta["owner"],
                              {"A", "B", "C", "S", "P4", "P5", "P6", "P7",
                               "P8", "P9", "P10", "P11", "P12", "P13",
                               "P14", "P15", "P16", "P17", "P18", "P19",
                               "P20", "P21", "P22", "P23", "P24", "P25", "P26", "P27", "P28", "dairy-0.1.0", "dairy-f1"})



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

    # sha256[:16] of each " | " part of the install-manifest reason at 809e925,
    # in order. Later merges may insert or append parts; history must survive.
    _REASON_809E925_PARTS = (
            "28bd990c0b5fb2e8",
            "c895920786e852a1",
            "91d0938417ff74e8",
            "e2714bbeefbfd2d4",
            "2af241bc2769a182",
            "e9e3bec4ec5ad428",
            "0cbbe954c28a6437",
            "cff08819bcdb7e11",
            "34839dac7ca80d7b",
            "e13cf1e5a0095a1e",
            "af00296b62be5f05",
            "d7a822c4c3afa0b6",
            "884ca1074174e94d",
            "fa7a641f7637656a",
            "aae0f1f268dcdbda",
            "34a8a3f742b2e1db",
            "529a6f6e2f0584b5",
            "36e1b0f361c4cc6f",
            "ebc198819fa2dd47",
            "b130ce65e6a9254a",
            "241fa7d6ee0a0960",
            "9dd9a3fc0ecdd216",
            "88e9bf2e441ddcb7",
            "24b237bd4d6a3e16",
            "b5e79a98697ce884",
            "4b20485d12de0623",
            "70fbaec2fc12fd35",
            "0daf746665d5fdaa",
            "b2d115ffd383a0c0",
            "3c6b24e74554d5ba",
            "8c5973eeefa239be",
            "c4c6e3185bb00f4b",
            "22a4c6afc76e3517",
            "495a8a134d376c25",
            "4d1d983e0030120a",
            "eb88d4397969a25a",
            "0457b93e3b6ab19c",
            "8b7429c9d14152c8",
            "7c20cd6834db9522",
            "62774aaf92b29004",
            "cb99d9626b616a93",
            "b543caf0e0a8db0c",
            "a9b7e333e2a363f5",
            "e60877097a698b4e",
            "19b2f4620926b5c0",
            "3773375bfe9fe291",
            "08e08034d20e75eb",
            "9d4601113f5e3415",
            "ba087ceda6dc04c0",
            "6a8d163a81f793c7",
    )

    def test_install_manifest_reason_preserves_809e925_history(self):
        reason = json.loads((REPO_ROOT / "tools/runtime-changes.json").read_text(encoding="utf-8"))["changes"]["skills/knuaf-doc/install-manifest.json"]["reason"]
        current = [hashlib.sha256(part.encode("utf-8")).hexdigest()[:16]
                   for part in reason.split(" | ")]
        it = iter(current)
        missing = [h for h in self._REASON_809E925_PARTS if h not in it]
        self.assertEqual([], missing,
                         "809e925 install-manifest reason parts lost or reordered")
        self.assertIn("Astra first-read follow-up:", reason)


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


class WiringIgnoreTests(ContractCase):
    """Per-worktree harness wiring must never enter the ledger.

    ``.githooks/`` (three symlinks) and ``.harness.conf`` are created by
    harness-init inside each worktree and kept out of git through the
    worktree's own ``info/exclude``. Before this fix the ledger read them as
    repository files: ``--write-manifest`` produced 116 entries instead of
    112, and a test run in a wired worktree failed with ``undeclared_file``.
    """

    def _tree(self, tmp):
        """A synthetic tree with baseline files plus branch-local wiring."""
        _synth_tree(tmp)
        root = Path(tmp)
        (root / ".githooks").mkdir()
        for name in ("commit-msg", "pre-commit", "pre-push"):
            (root / ".githooks" / name).write_text("#!/bin/sh\n",
                                                   encoding="utf-8")
        (root / ".harness.conf").write_text('PROJECT_NAME="x"\n',
                                            encoding="utf-8")
        return root

    def test_wiring_is_absent_from_ledger_and_scan(self):
        """Wiring files never reach the bundle nor the undeclared scan.

        The filter applies inside a repository (that is where "tracked" has a
        meaning); the fixture is therefore initialised and the wiring is left
        untracked, exactly like ``harness-init`` leaves it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            self._git(root, "init", "-q")
            # Only the fixture's own files are tracked; the wiring stays
            # untracked exactly as harness-init leaves it.
            self._git(root, "add", "-A", "--", "a.py", "b.py", "tools",
                      "tests")
            listed = check_bundle.disk_files(root)
            self.assertNotIn(".harness.conf", listed)
            for name in ("commit-msg", "pre-commit", "pre-push"):
                self.assertNotIn(".githooks/" + name, listed)
            # The ledger built over the same tree is hook-free.
            _write_manifest(root)
            manifest = json.loads(
                (root / "tools/validation-bundle.json").read_text(
                    encoding="utf-8"))
            names = set(manifest["files"])
            self.assertNotIn(".harness.conf", names)
            self.assertFalse([n for n in names if n.startswith(".githooks/")])
            self.assertEqual("ok", check_bundle.check(root)["status"])

    def test_tracked_file_with_the_same_name_is_kept(self):
        """A repository that really tracks ``.harness.conf`` still lists it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            # git-track the wiring name: the ignore must yield to reality.
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(root), "add", "-f",
                            ".harness.conf", ".githooks/pre-commit"],
                           check=True, capture_output=True)
            listed = check_bundle.disk_files(root)
            self.assertIn(".harness.conf", listed)
            self.assertIn(".githooks/pre-commit", listed)

    def test_partial_tree_without_wiring_stays_clean(self):
        """A tree that has no wiring behaves exactly as before."""
        with tempfile.TemporaryDirectory() as tmp:
            _synth_tree(tmp)
            root = Path(tmp)
            self.assertEqual(sorted(check_bundle.disk_files(root)),
                             sorted(n for n in check_bundle.disk_files(root)
                                    if not n.startswith(".")))

    def _git(self, root, *args):
        """Run git in ``root`` with the same clean environment the tool uses."""
        return subprocess.run(["git", "-C", str(root)] + list(args),
                              capture_output=True, check=True)

    def test_git_unavailable_stops_instead_of_hiding(self):
        """Git 조회가 불가능하면 **추측하지 않고 멈춘다** (C-01).

        추적 여부를 '안 됨'으로 단정하면 실제 추적 파일이 장부에서 조용히
        빠진다. 검토자가 재현한 세 경우(실행 불가·비저장소·빈 대체 인덱스)
        중 '실행 불가'를 잠근다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            self._git(root, "init", "-q")
            self._git(root, "add", "-f", ".harness.conf")
            real = os.environ.get("PATH", "")
            os.environ["PATH"] = str(Path(tmp) / "empty-bin")
            try:
                with self.assertRaises(check_bundle.TrackingUnavailable):
                    check_bundle.disk_files(root)
            finally:
                os.environ["PATH"] = real

    def test_inherited_index_variable_does_not_hide_tracked_files(self):
        """상속된 ``GIT_INDEX_FILE``이 추적 판정을 흔들지 않는다 (C-01).

        훅 안에서는 Git이 임시 인덱스를 가리키는 ``GIT_INDEX_FILE``을 준다.
        그대로 물려받으면 추적 파일이 '비추적'으로 보인다 — 걷어내고 이
        트리의 인덱스로 답한다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            self._git(root, "init", "-q")
            self._git(root, "add", "-f", ".harness.conf", ".githooks/pre-commit")
            before = check_bundle.disk_files(root)
            self.assertIn(".harness.conf", before)
            real = os.environ.get("GIT_INDEX_FILE")
            os.environ["GIT_INDEX_FILE"] = str(Path(tmp) / "nonexistent-index")
            try:
                after = check_bundle.disk_files(root)
            finally:
                if real is None:
                    os.environ.pop("GIT_INDEX_FILE", None)
                else:
                    os.environ["GIT_INDEX_FILE"] = real
            self.assertEqual(sorted(before), sorted(after))
            self.assertIn(".harness.conf", after)

    def test_non_repository_tree_keeps_wiring_visible(self):
        """저장소가 아닌 사본에서는 필터를 끈다 — 조용히 숨기지 않는다 (C-01).

        .git 없는 내보내기 사본에서 배선을 숨기면 '추적 정보 상실'이 된다.
        대신 일반 파일로 세어 undeclared_file로 **시끄럽게** 실패한다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            listed = check_bundle.disk_files(root)     # git 저장소 아님
            self.assertIn(".harness.conf", listed)
            self.assertIn(".githooks/pre-commit", listed)

    def test_pathspec_metacharacters_do_not_match_other_files(self):
        """파일명의 pathspec 문자를 리터럴로 다룬다 (I-01).

        ``.githooks/pre1``만 추적하고 ``.githooks/pre[1]``은 비추적일 때,
        패턴으로 물으면 전자를 후자로 오인해 비추적 파일을 장부에 넣는다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            (root / ".githooks" / "pre1").write_text("x\n", encoding="utf-8")
            (root / ".githooks" / "pre[1]").write_text("y\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", "-f", ".githooks/pre1")
            listed = check_bundle.disk_files(root)
            self.assertIn(".githooks/pre1", listed)        # 추적됨 — 보존
            self.assertNotIn(".githooks/pre[1]", listed)   # 비추적 — 제외

    def test_partially_tracked_githooks_keeps_only_tracked(self):
        """``.githooks`` 일부만 추적되면 추적된 것만 남는다 (R-01)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            self._git(root, "init", "-q")
            self._git(root, "add", "-f", ".githooks/pre-commit")
            listed = check_bundle.disk_files(root)
            self.assertIn(".githooks/pre-commit", listed)
            self.assertNotIn(".githooks/commit-msg", listed)
            self.assertNotIn(".githooks/pre-push", listed)

    def test_githooks_as_a_file_is_out_of_scope_and_visible(self):
        """``.githooks``가 **파일**이면 필터 대상이 아니다 (R-01).

        harness-init이 만드는 것은 ``.githooks/`` **디렉터리 아래**의 훅뿐이다.
        같은 이름의 일반 파일은 저장소 파일이므로 추적 여부와 무관하게 목록에
        남는다 — 무시 범위를 넓히면 진짜 파일이 조용히 사라진다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            shutil.rmtree(root / ".githooks")
            (root / ".githooks").write_text("not a directory\n", encoding="utf-8")
            self._git(root, "init", "-q")
            self._git(root, "add", "-f", "a.py", "b.py", "tools", "tests")
            self.assertIn(".githooks", check_bundle.disk_files(root))
            self._git(root, "add", "-f", ".githooks")
            self.assertIn(".githooks", check_bundle.disk_files(root))

    def test_real_symlinked_wiring_stays_out_of_the_ledger(self):
        """harness-init과 같은 **심링크** 배선으로 장부를 만든다 (C-01 재현).

        앞 시험은 배선을 일반 파일로 만들었다. 실제 워크트리는
        ``.githooks/*``가 harness 코어를 가리키는 심링크이므로, 그 모양
        그대로 장부에서 빠지는지 잠근다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            shutil.rmtree(root / ".githooks")
            core = Path(tmp) / "core"
            core.mkdir()
            (root / ".githooks").mkdir()
            for name in ("commit-msg", "pre-commit", "pre-push"):
                (core / name).write_text("#!/bin/sh\n", encoding="utf-8")
                (root / ".githooks" / name).symlink_to(core / name)
            self._git(root, "init", "-q")
            self._git(root, "add", "-A", "--", "a.py", "b.py", "tools", "tests")
            _write_manifest(root)
            names = set(json.loads(
                (root / "tools/validation-bundle.json").read_text(
                    encoding="utf-8"))["files"])
            self.assertFalse([n for n in names if n.startswith(".githooks/")])
            self.assertNotIn(".harness.conf", names)
            self.assertEqual("ok", check_bundle.check(root)["status"])

    def test_unqueryable_repository_refuses_the_ledger_via_cli(self):
        """저장소가 있는데 조회가 실패하면 CLI가 멈추고 장부를 안 건드린다.

        검토자 반례 A: ``.git``이 있어도 ``rev-parse``가 128로 죽는 트리.
        종전에는 이것을 '저장소 아님'으로 읽어 배선을 제외했다 — 추적 파일이
        조용히 빠질 수 있다. 이제 exit 1이고 기존 장부 바이트는 그대로다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            _write_manifest(root)
            before = (root / "tools/validation-bundle.json").read_bytes()
            # A real repository directory whose gitdir pointer dangles:
            # git exists, rev-parse exits 128.
            (root / ".git").write_text("gitdir: /nonexistent\n",
                                       encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools"
                                      / "build_validation_bundle.py"),
                 "--root", str(root), "--write-manifest"],
                capture_output=True, text=True)
            self.assertEqual(1, proc.returncode, proc.stderr)
            self.assertIn("추적 여부를 확인할 수 없어", proc.stderr)
            self.assertEqual(before,
                             (root / "tools/validation-bundle.json").read_bytes())

    def test_fake_git_exit_128_on_a_real_repository_refuses_the_ledger(self):
        """실제 저장소인데 git이 128로 죽어도 장부를 만들지 않는다 (C-01 A).

        처분서가 요구한 (a) 그대로다: ``.git``이 멀쩡히 있는 진짜 저장소에
        ``rev-parse``가 128로 끝나는 가짜 git을 물린다. 종전 판정은 이 종료
        코드를 '저장소 아님'으로 읽어 배선을 제외했고, 그 순간 추적되는
        파일도 함께 사라질 수 있었다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            self._git(root, "init", "-q")
            self._git(root, "add", "-A", "--", "a.py", "b.py", "tools",
                      "tests")
            _write_manifest(root)
            before = (root / "tools/validation-bundle.json").read_bytes()
            fake_bin = Path(tmp) / "fake-bin"
            fake_bin.mkdir()
            fake_git = fake_bin / "git"
            fake_git.write_text("#!/bin/sh\nexit 128\n", encoding="utf-8")
            fake_git.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools"
                                      / "build_validation_bundle.py"),
                 "--root", str(root), "--write-manifest"],
                capture_output=True, text=True, env=env)
            self.assertEqual(1, proc.returncode, proc.stderr)
            self.assertIn("추적 여부를 확인할 수 없어", proc.stderr)
            self.assertIn("exit 128", proc.stderr)
            self.assertEqual(before,
                             (root / "tools/validation-bundle.json").read_bytes())

    def test_export_copy_inside_another_repository_keeps_wiring_visible(self):
        """남의 저장소 안에 놓인 ``.git`` 없는 사본은 그 색인을 물려받지 않는다.

        검토자 반례 B: 사본의 ``rev-parse --show-toplevel``은 바깥 저장소를
        돌려준다. 그것을 근거로 배선을 숨기면 사본의 파일이 사라진다.
        최상위 경로가 ``root``와 정확히 같을 때만 색인을 신뢰한다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            outer.mkdir()
            self._git(outer, "init", "-q")
            (outer / "README").write_text("x\n", encoding="utf-8")
            self._git(outer, "add", "-A", "--", "README")
            export = outer / "untracked-copy"
            export.mkdir()
            _synth_tree(export)
            (export / ".githooks").mkdir()
            (export / ".githooks" / "pre-commit").write_text(
                "#!/bin/sh\n", encoding="utf-8")
            (export / ".harness.conf").write_text('x="1"\n', encoding="utf-8")
            listed = check_bundle.disk_files(export)
            self.assertIn(".harness.conf", listed)
            self.assertIn(".githooks/pre-commit", listed)

    def test_ignore_stays_narrow(self):
        """Only the wiring names are ignored — not every dotted file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            for name in (".env", ".ruff_cache/x", ".github/workflows/ci.yml"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n", encoding="utf-8")
            listed = check_bundle.disk_files(root)
            self.assertIn(".env", listed)
            self.assertIn(".github/workflows/ci.yml", listed)


if __name__ == "__main__":
    unittest.main()
