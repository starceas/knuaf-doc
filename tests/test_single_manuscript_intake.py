"""Single manuscript intake: real bytes/engines; synthetic student answers."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

from docx import Document

from tests._harness import ContractCase, SCRIPTS, runtime, write_text


class SingleManuscriptIntakeTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.intake = runtime("gg_manuscript_intake")

    def _input(self, root, name="원고.docx"):
        document = Document()
        document.add_paragraph("시험용 합성 원고: " + name)
        document.save(root / name)
        lines = ["현재 원고는 " + name + "입니다.", "제 원고입니다.",
                 "이 원고로 이어 쓸게요."]
        write_text(root, "interview/answer.txt", "\n".join(lines) + "\n")
        claims = {}
        for n, (field, value) in enumerate((
                ("intake.current_manuscript", name),
                ("intake.file_owner", "self"),
                ("intake.work_basis", "resume")), 1):
            claims["c%d" % n] = {
                "field_id": field, "value": value, "kind": "reported_fact",
                "value_type": "text", "unit": "", "period": None,
                "scope": "project", "answer_state": "provided",
                "locator": "line %d" % n, "quote": lines[n - 1]}
        spec = {"path": "interview/answer.txt", "claims": claims}
        self._spec(root, spec)
        return root / name, spec

    def _spec(self, root, spec):
        write_text(root, "answer-source.json", json.dumps(spec, ensure_ascii=False))

    def _run(self, root, name="원고.docx", request="intake", revision=None):
        if revision is None:
            revision = self.core.load(root)["revision"]
        return self.intake.run(root, name, "answer-source.json", revision, request)

    def _old_evidence(self, root):
        write_text(root, "old.txt", "이미 보존한 근거\n")
        self.core.apply(root, {"request_id": "old-evidence", "ops": [
            {"collection": "sources", "value": {"id": "old", "path": "old.txt"}}]}, 0)
        return (root / "old.txt").read_bytes()

    def test_cli_initializes_and_links_exact_preserved_original_and_answer(self):
        temp = tempfile.TemporaryDirectory(prefix="knuaf-single-intake-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        original, spec = self._input(root)
        original_bytes = original.read_bytes()
        answer_bytes = (root / spec["path"]).read_bytes()
        result = subprocess.run([
            sys.executable, "-B", str(SCRIPTS / "gg.py"), "intake-manuscript",
            str(root), original.name, "--input", "answer-source.json",
            "--expected-revision", "0", "--request-id", "cli-intake"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        value = json.loads(result.stdout)
        project = self.core.load(root)
        self.assertEqual((value["status"], value["commit_state"]),
                         ("registered", "committed"))
        source = project["sources"][value["source_id"]]
        self.assertEqual((root / source["path"]).read_bytes(), original_bytes)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(source["hash"], hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(source["file_owner"], "self")
        answer = project["sources"][value["answer_source_id"]]
        self.assertEqual((root / answer["path"]).read_bytes(), answer_bytes)
        self.assertEqual((root / spec["path"]).read_bytes(), answer_bytes)
        self.assertEqual(answer["claims"], spec["claims"])
        for field, expected in (("intake.current_manuscript", original.name),
                                ("intake.work_basis", "resume")):
            facts = [f for f in project["facts"].values() if f["field_id"] == field]
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]["value"], expected)
            ref = facts[0]["source_refs"][0]
            self.assertEqual((ref["id"], ref["revision"]), (answer["id"], answer["revision"]))
            self.assertEqual(answer["claims"][ref["claim_id"]]["field_id"], field)
        task = next(iter(project["tasks"].values()))
        self.assertEqual({ref["claim_id"] for ref in task["source_refs"]}, {"c1", "c2", "c3"})
        self.assertEqual(value["resolution"]["status"], "unmapped")
        self.assertEqual(value["resolution"]["reasons"], ["source_unmatched"])
        self.assertEqual(project["outputs"], {})
        self.assertFalse(any(f["field_id"] == "common.major_id" for f in project["facts"].values()))

    def test_mixed_folder_registers_only_the_explicitly_selected_file(self):
        root = self.make_project()
        chosen, _ = self._input(root, "내 원고[최종].docx")
        document = Document()
        document.add_paragraph("선택하지 않은 다른 원고")
        other = root / "다른 원고.docx"
        document.save(other)
        other_bytes = other.read_bytes()
        value = self._run(root, chosen.name)
        self.assertEqual(value["status"], "registered", value)
        snapshots = list((root / "sources/snapshots").rglob("*.docx"))
        self.assertEqual([p.name for p in snapshots], [chosen.name])
        self.assertEqual(other.read_bytes(), other_bytes)
        project = self.core.load(root)
        self.assertEqual({s["hash"] for sid, s in project["sources"].items()
                          if sid == value["source_id"]}, {hashlib.sha256(chosen.read_bytes()).hexdigest()})
        self.assertNotIn(hashlib.sha256(other_bytes).hexdigest(),
                         {s["hash"] for s in project["sources"].values()})

    def test_missing_answer_writes_no_registration_or_canonical_change(self):
        root = self.make_project()
        _, spec = self._input(root)
        spec["path"] = "interview/missing.txt"
        self._spec(root, spec)
        before = self._tree_bytes(root)
        value = self._run(root)
        self.assertEqual((value["status"], value["stage"]), ("incomplete", "answer"))
        self.assertEqual(value["commit_state"], "not_committed")
        self.assertEqual(self._tree_bytes(root), before)

    def test_unknown_selection_ownership_or_current_intent_is_not_confirmation(self):
        for claim_id in ("c1", "c2", "c3"):
            with self.subTest(claim=claim_id):
                root = self.make_project()
                _, spec = self._input(root)
                spec["claims"][claim_id].update(answer_state="unknown", value=None)
                self._spec(root, spec)
                before = self._tree_bytes(root)
                value = self._run(root)
                self.assertEqual((value["status"], value["reason"]),
                                 ("incomplete", "answer_confirmation_required"))
                self.assertEqual(self._tree_bytes(root), before)

    def test_ownership_only_and_unanchored_or_duplicate_claims_are_incomplete(self):
        for change in ("ownership_only", "quote", "locator", "duplicate"):
            with self.subTest(change=change):
                root = self.make_project()
                _, spec = self._input(root)
                if change == "ownership_only":
                    spec["claims"] = {"c2": spec["claims"]["c2"]}
                elif change == "quote":
                    spec["claims"]["c2"]["quote"] = "없던 답을 만들었다"
                elif change == "locator":
                    spec["claims"]["c1"]["locator"] = "line 99"
                else:
                    spec["claims"]["duplicate"] = copy.deepcopy(spec["claims"]["c1"])
                self._spec(root, spec)
                before = self._tree_bytes(root)
                value = self._run(root)
                self.assertEqual(value["status"], "incomplete")
                self.assertEqual(value["commit_state"], "not_committed")
                self.assertEqual(self._tree_bytes(root), before)

    def test_original_change_after_scan_is_rejected_and_earlier_evidence_remains(self):
        root = self.make_project()
        old = self._old_evidence(root)
        original, _ = self._input(root)
        write_receipt = self.intake.intake.write_scan_receipt

        def change_after_scan(*args, **kw):
            receipt = write_receipt(*args, **kw)
            original.write_bytes(b"changed after sealed scan")
            return receipt

        with mock.patch.object(self.intake.intake, "write_scan_receipt", change_after_scan):
            value = self._run(root)
        self.assertEqual((value["status"], value["reason"]),
                         ("incomplete", "selection_bytes_mismatch"))
        self.assertEqual(value["commit_state"], "not_committed")
        self.assertEqual(self.core.load(root)["revision"], 1)
        self.assertEqual((root / "old.txt").read_bytes(), old)
        self.assertTrue(any((root / path).is_file() for path in value["preserved_paths"]))
        self.assertEqual(self.core.load(root)["facts"], {})

    def test_revision_change_after_scan_does_not_overwrite_canonical_state(self):
        root = self.make_project()
        old = self._old_evidence(root)
        self._input(root)
        write_text(root, "concurrent.txt", "別の作業\n")
        resolve = self.intake.reuse.resolve_reuse

        def advance_revision(*args, **kw):
            result = resolve(*args, **kw)
            self.core.apply(root, {"request_id": "concurrent", "ops": [
                {"collection": "sources", "value": {"id": "concurrent", "path": "concurrent.txt"}}]}, 1)
            return result

        with mock.patch.object(self.intake.reuse, "resolve_reuse", advance_revision):
            value = self._run(root, revision=1)
        self.assertEqual((value["stage"], value["reason"]), ("register", "revision_conflict"))
        self.assertEqual(value["status"], "incomplete")
        project = self.core.load(root)
        self.assertEqual(project["revision"], 2)
        self.assertEqual(set(project["sources"]), {"old", "concurrent"})
        self.assertEqual((root / "old.txt").read_bytes(), old)
        self.assertTrue((root / value["preserved_paths"][0]).is_file())

    def test_final_apply_conflict_preserves_committed_snapshot_without_false_success(self):
        root = self.make_project()
        old = self._old_evidence(root)
        original, _ = self._input(root)
        source_bytes = original.read_bytes()
        write_text(root, "concurrent.txt", "다른 작업\n")
        apply = self.core.apply

        def conflict_before_apply(folder, change, expected):
            apply(folder, {"request_id": "concurrent", "ops": [
                {"collection": "sources", "value": {"id": "concurrent", "path": "concurrent.txt"}}]}, expected)
            return apply(folder, change, expected)

        with mock.patch.object(self.core, "apply", conflict_before_apply):
            value = self._run(root, revision=1)
        self.assertEqual((value["status"], value["stage"], value["reason"]),
                         ("incomplete", "apply", "revision_conflict"))
        self.assertEqual(value["commit_state"], "not_committed")
        self.assertEqual(value["registration_revision"], 2)
        snapshots = list((root / "sources/snapshots").rglob("*.docx"))
        self.assertEqual([p.read_bytes() for p in snapshots], [source_bytes])
        self.assertIn(str(snapshots[0].parent.relative_to(root)), value["preserved_paths"])
        project = self.core.load(root)
        self.assertEqual(project["facts"], {})
        self.assertEqual(set(project["sources"]), {"old", "concurrent"})
        self.assertEqual((root / "old.txt").read_bytes(), old)
        self.assertEqual(original.read_bytes(), source_bytes)

    def test_existing_choice_fact_is_updated_not_duplicated(self):
        root = self.make_project()
        self._input(root)
        self.core.apply(root, {"request_id": "old-choice", "ops": [
            {"collection": "facts", "value": {
                "id": "old-current-record", "field_id": "intake.current_manuscript",
                "kind": "reported_fact", "value": None, "value_type": "text",
                "unit": "", "period": None, "scope": "project",
                "answer_state": "not_provided", "verification": "unreviewed",
                "source_refs": []}}]}, 0)
        value = self._run(root)
        self.assertEqual(value["status"], "registered", value)
        project = self.core.load(root)
        self.assertEqual(project["facts"]["old-current-record"]["value"], "원고.docx")
        self.assertEqual(project["facts"]["old-current-record"]["revision"], 2)
        self.assertEqual([fid for fid, f in project["facts"].items()
                          if f["field_id"] == "intake.current_manuscript"], ["old-current-record"])
