"""Hash-bound workbook publication authority and 17-sheet-only inspection."""
from dataclasses import replace
import json
from pathlib import Path
from unittest import mock

from openpyxl import Workbook
from tests._harness import ContractCase, bind_major, runtime, section_op, write_text


class WorkbookPublicationProfileTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.school = runtime("gg_school_excel")
        self.root = self.make_project()
        bind_major(self.root)
        write_text(self.root, "sections/body.md", "# 검토문\n합성 입력 검토.\n")
        self.core.apply(self.root, {"request_id": "body", "ops": [
            section_op("body", "검토문", "sections/body.md")]},
            self.core.load(self.root)["revision"])
        self.context = self.mc.output_context(self.root, "specialty_crops")
        self.registry = self.mc.default_registry()
        self.module = self.registry.resolve("specialty_crops")
        modules = {major: self.registry.resolve(major)
                   for major in self.registry.major_ids}
        self.current_module = self.module
        patch = mock.patch.object(self.registry, "resolve", side_effect=lambda major:
                                  self.current_module if major == "specialty_crops" else modules[major])
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(self.mc, "default_registry", return_value=self.registry)
        patch.start()
        self.addCleanup(patch.stop)

    def set_capability(self, supported):
        self.current_module = replace(self.module, finance_capabilities=tuple(
            replace(cap, status="unsupported") if not supported and
            cap.profile == "school_17_sheet_v1" else cap
            for cap in self.module.finance_capabilities))

    def output(self, *, profile="school_17_sheet_v1", receipt=True):
        book = Workbook()
        book.active["A1"] = 10
        path = self.root / "synthetic.xlsx"
        book.save(path)
        book.close()
        project = self.core.load(self.root)
        refs = [{"collection": "sections", "id": "body"}]
        value = {"id": "workbook", "path": path.name, "format": "xlsx",
                 "file_hash": self.core.digest(path.read_bytes()),
                 "target_refs": refs,
                 "input_fingerprint": self.core.fingerprint(self.root, project, refs)}
        companions = []
        if receipt:
            manifest = {"status": "converted", "engine": "Microsoft Excel",
                        "published_office_sha256": value["file_hash"],
                        "validation": {"workbook_profile": profile, "finance_profile": profile,
                                       "structure_issues": [], "school_issues": [],
                                       "excel": {"valid": True, "sha256": value["file_hash"]}}}
            raw = json.dumps(manifest).encode()
            write_text(self.root, "native.json", raw.decode())
            pointer = {"path": "native.json", "sha256": self.core.digest(raw)}
            value["native_manifest"] = pointer
            companions.append(pointer)
        return project, value, companions

    def adopt(self, project, value, companions, request="adopt"):
        return self.core.adopt_output(self.root, value, project["revision"], request,
                                      companion_files=companions, major_id="specialty_crops")

    def test_declared_output_and_caller_profile_do_not_authorize_workbook(self):
        # A caller-written label never substitutes for receipt evidence.
        for profile in ("school_17_sheet_v1", "fruit_16_sheet_v1"):
            project, value, companions = self.output(receipt=False)
            value["workbook_profile"] = profile
            before = self._tree_bytes(self.root)
            with self.assertRaises(self.core.OperationError) as held:
                self.adopt(project, value, companions)
            self.assertEqual(held.exception.result["reason"], "finance_profile_evidence_invalid")
            self.assertEqual(self._tree_bytes(self.root), before)
        # Specialty's historical receipt-less workbook route is restored.
        project, value, companions = self.output(receipt=False)
        result = self.adopt(project, value, companions)
        self.assertEqual(result["status"], "adopted")
        self.assertNotIn("workbook_profile",
                         self.core.load(self.root)["outputs"]["workbook"])

    def test_adoption_persists_receipt_profile_and_rechecks_replay(self):
        project, value, companions = self.output()
        result = self.adopt(project, value, companions)
        self.assertEqual(result["status"], "adopted")
        stored = self.core.load(self.root)["outputs"]["workbook"]
        self.assertEqual(stored["workbook_profile"], "school_17_sheet_v1")
        self.assertTrue(stored["native_manifest"]["path"].startswith(".gg-artifacts/"))
        (self.root / "synthetic.xlsx").unlink()
        (self.root / "native.json").unlink()
        self.assertEqual(self.adopt(project, value, companions)["status"], "existing")
        self.set_capability(False)
        with self.assertRaises(self.core.OperationError) as held:
            self.adopt(project, value, companions)
        self.assertEqual(held.exception.result["reason"], "unsupported_finance_profile")

    def test_bad_receipt_hash_output_hash_profile_and_capability_hold(self):
        for defect in ("receipt_hash", "output_hash", "profile", "capability", "missing_companion"):
            project, value, companions = self.output()
            if defect == "receipt_hash":
                value["native_manifest"] = dict(value["native_manifest"], sha256="0" * 64)
            elif defect == "output_hash":
                data = json.loads((self.root / "native.json").read_text())
                data["published_office_sha256"] = "0" * 64
                raw = json.dumps(data).encode()
                (self.root / "native.json").write_bytes(raw)
                pointer = {"path": "native.json", "sha256": self.core.digest(raw)}
                value["native_manifest"] = pointer
                companions = [pointer]
            elif defect == "profile":
                value["workbook_profile"] = "fruit_16_sheet_v1"
            elif defect == "capability":
                self.set_capability(False)
            else:
                companions = []
            before = self._tree_bytes(self.root)
            with self.subTest(defect=defect), self.assertRaises(self.core.OperationError):
                self.adopt(project, value, companions)
            self.assertEqual(self._tree_bytes(self.root), before)
            self.set_capability(True)

    def test_export_rechecks_receipt_and_current_profile_before_publication(self):
        project, value, companions = self.output()
        self.adopt(project, value, companions)
        report = [{"check_id": "output_xlsx", "status": "pass", "target": "workbook"}]
        with mock.patch.object(self.core, "gate", return_value=report):
            self.set_capability(False)
            with self.assertRaises(self.core.OperationError) as held:
                self.core.export(self.root, "submission_candidate", major_id="specialty_crops")
        self.assertEqual(held.exception.result["reason"], "unsupported_finance_profile")
        self.assertFalse((self.root / "build").exists())
        self.set_capability(True)
        stored = self.core.load(self.root)["outputs"]["workbook"]
        (self.root / stored["native_manifest"]["path"]).write_text("{}")
        with mock.patch.object(self.core, "gate", return_value=report):
            with self.assertRaises(self.core.OperationError) as held:
                self.core.export(self.root, "submission_candidate", major_id="specialty_crops")
        self.assertEqual(held.exception.result["reason"], "finance_profile_evidence_invalid")

    def test_paper_only_draft_export_does_not_require_workbook_profile(self):
        self.set_capability(False)
        result = self.core.export(self.root, "draft", major_id="specialty_crops")
        from pathlib import Path
        self.assertIn("합성 입력 검토", Path(result["path"]).read_text())

    def test_other_profiles_never_open_17_sheet_inspector_or_locators(self):
        for profile in (None, "fruit_16_sheet_v1", "dairy_hanwoo_28_sheet_v1"):
            project, value, _ = self.output(profile=profile)
            value["workbook_profile"] = profile
            project["outputs"] = {"workbook": value}
            with mock.patch("openpyxl.load_workbook", side_effect=AssertionError("17-sheet reader reached")):
                issues = self.school.inspect_outputs(self.root, project)
                self.assertEqual(issues[0][0], "school_excel_17_scope")
                rows = self.school.crosscheck_body_workbook("매출 10원", self.root / value["path"],
                                                           "합성", workbook_profile=profile)
                self.assertEqual(rows[0]["status"], "not_checked")

    def test_core_marks_unsupported_plan_and_inspection_not_checked(self):
        project, value, _ = self.output(profile="fruit_16_sheet_v1")
        value["workbook_profile"] = "fruit_16_sheet_v1"
        project["outputs"] = {"workbook": value}
        with mock.patch.object(self.core, "_finance_plan_supported", return_value=False):
            rows = self.core.checks(self.root, project)
        scopes = {row["check_id"]: row["status"] for row in rows
                  if row["check_id"] in ("finance_plan_review_scope", "school_excel_17_scope")}
        self.assertEqual(scopes, {"finance_plan_review_scope": "not_checked",
                                  "school_excel_17_scope": "not_checked"})

    def test_marked_fruit_source_never_enters_specialty_plan_review(self):
        project, value, _ = self.output(profile="fruit_16_sheet_v1")
        project["sources"]["fruit-plan"] = {"path": value["path"], "hash": value["file_hash"],
                                            "workbook_profile": "fruit_16_sheet_v1"}
        self.assertEqual(self.core._finance_plan_candidates(self.root, project), [])

    def test_authorized_submission_copies_verified_managed_workbook(self):
        project, value, companions = self.output()
        self.adopt(project, value, companions)
        report = [{"check_id": "output_xlsx", "status": "pass", "target": "workbook"}]
        with mock.patch.object(self.core, "gate", return_value=report):
            result = self.core.export(self.root, "submission_candidate", major_id="specialty_crops")
        from pathlib import Path
        exported = Path(result["path"]).with_suffix(".xlsx")
        self.assertEqual(self.core.digest(exported.read_bytes()), value["file_hash"])

    def _native_output(self, root, path, profile, *, output_id="workbook"):
        digest = self.core.digest(path.read_bytes())
        manifest = {"status": "converted", "engine": "Microsoft Excel",
                    "published_office_sha256": digest,
                    "validation": {"workbook_profile": profile,
                                   "finance_profile": profile,
                                   "structure_issues": [], "school_issues": [],
                                   "excel": {"valid": True, "sha256": digest}}}
        raw = json.dumps(manifest).encode()
        write_text(root, "native.json", raw.decode())
        pointer = {"path": "native.json", "sha256": self.core.digest(raw)}
        project = self.core.load(root)
        refs = self.core.sort_target_refs(self.core._export_refs(project))
        value = {"id": output_id, "path": path.name, "format": "xlsx",
                 "file_hash": digest, "target_refs": refs,
                 "input_fingerprint": self.core.fingerprint(root, project, refs),
                 "native_manifest": pointer}
        return project, value, [pointer]

    def test_registered_profile_rejects_labels_and_changed_formulas(self):
        from tests.test_finance_profile_gate import register_synthetic_form
        book = Workbook()
        sheets = self.school.profile_structure("fruit_16_sheet_v1")["sheets"]
        book.active.title = sheets[0]
        book.active["B1"] = "=A1*2"
        for title in sheets[1:]:
            book.create_sheet(title)
        register_synthetic_form(self, book)
        for defect in ("none", "empty", "changed", "moved"):
            root = self.make_project()
            bind_major(root, "fruit_trees")
            write_text(root, "body.md", "# synthetic\nstudent plan\n")
            self.core.apply(root, {"request_id": "body", "ops": [
                section_op("body", "synthetic", "body.md")]}, self.core.load(root)["revision"])
            book.active["A1"] = 40
            book.active["B1"] = "=A1*2" if defect == "none" else None
            book.active["C1"] = None
            if defect == "changed":
                book.active["B1"] = "=A1*3"
            if defect == "moved":
                book.active["C1"] = "=A1*2"
            path = root / "fruit.xlsx"
            book.save(path)
            project, value, companions = self._native_output(
                root, path, "fruit_16_sheet_v1", output_id="fruit")
            if defect == "none":
                result = self.core.adopt_output(
                    root, value, project["revision"], "fruit",
                    companion_files=companions, major_id="fruit_trees")
                self.assertEqual(result["status"], "adopted")
                report = [{"check_id": "output_xlsx", "status": "pass", "target": "fruit"}]
                with mock.patch.object(self.core, "gate", return_value=report):
                    self.assertEqual(self.core.export(root, "submission_candidate",
                                     major_id="fruit_trees")["status"], "generated")
            else:
                with self.assertRaises(self.core.OperationError) as held:
                    self.core.adopt_output(
                        root, value, project["revision"], "fruit",
                        companion_files=companions, major_id="fruit_trees")
                self.assertEqual(held.exception.result["reason"],
                                 "finance_profile_evidence_invalid")
                self.assertIn(sheets[0], str(held.exception))
        book.close()

    def test_horticulture_rejects_unverified_workbook_companion_and_pdf(self):
        for kind in ("xlsx", "companion", "pdf", "forged_native"):
            root = self.make_project()
            bind_major(root, "hort_env_systems")
            book = Workbook()
            path = root / "hort.xlsx"
            book.save(path)
            book.close()
            project, value, companions = self._native_output(
                root, path, "hort_18_sheet_reconstructed_v1", output_id="hort")
            if kind != "forged_native":
                value.pop("native_manifest")
                companions = []
            if kind == "companion":
                companions.append({"path": path.name, "sha256": value["file_hash"]})
                path = write_text(root, "paper.md", "# synthetic\n")
                value.update(path=path.name, format="md", file_hash=self.core.digest(path.read_bytes()))
            elif kind == "pdf":
                path = root / "hort.pdf"
                path.write_bytes(b"%PDF-1.4\n%%EOF\n")
                value.update(path=path.name, format="pdf", file_hash=self.core.digest(path.read_bytes()))
            with self.subTest(kind=kind), self.assertRaises(self.core.OperationError) as held:
                self.core.adopt_output(root, value, project["revision"], "hort",
                    companion_files=companions, major_id="hort_env_systems")
            self.assertEqual(held.exception.result["reason"], "hort_transform_receipt_invalid")
            context = self.mc.output_context(root, "hort_env_systems")
            with self.assertRaises(self.core.OperationError):
                self.core._export_authorize(root,
                    {self.mc.OUTPUT_SCHOOL_PAPER, self.mc.OUTPUT_SCHOOL_WORKBOOK},
                    context, outputs=(dict(value, companions=companions),))

    def test_horticulture_transform_adoption_replay_export_and_staleness(self):
        from tests.test_hort_finance_pipeline import Chain
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        canonical = (chain.root / "project.json").read_bytes()
        chain.root = self.make_project()
        (chain.root / "project.json").write_bytes(canonical)
        write_text(chain.root, "answer.txt", "synthetic evidence\n")
        write_text(chain.root, "body.md", "# synthetic\nstudent plan\n")
        project = self.core.load(chain.root)
        self.core.apply(chain.root, {"request_id": "body", "ops": [
            section_op("body", "synthetic", "body.md")]}, project["revision"])
        chain._run_to_transform()
        root = chain.root
        path = root / "hort.xlsx"
        path.write_bytes(chain.out.read_bytes())
        receipt = root / "transform.json"
        receipt.write_bytes(chain.transform_rec.read_bytes())
        pointer = {"path": receipt.name, "sha256": self.core.digest(receipt.read_bytes())}
        project, value, _ = self._native_output(root, path, "unused", output_id="hort")
        value.pop("native_manifest")
        value["template_receipt"] = pointer
        result = self.core.adopt_output(root, value, project["revision"], "hort",
            companion_files=[pointer], major_id="hort_env_systems")
        self.assertEqual(result["status"], "adopted")
        self.assertEqual(self.core.adopt_output(root, value, project["revision"], "hort",
            companion_files=[pointer], major_id="hort_env_systems")["status"], "existing")
        report = [{"check_id": "output_xlsx", "status": "pass", "target": "hort"}]
        with mock.patch.object(self.core, "gate", return_value=report):
            self.assertEqual(self.core.export(root, "submission_candidate",
                             major_id="hort_env_systems")["status"], "generated")
        from tests.test_hort_finance_pipeline import _native_workbook
        import gg_hort_workbook as hort
        _native_workbook(chain.native, chain.roster,
                         chain.docs[hort.FILES[1]]["parameters"], chain.facts)
        # Native proof was issued against the same pre-adoption project.
        with mock.patch.object(hort, "_authorize", return_value=self.mc.authorize_output(
                self.mc.OUTPUT_SCHOOL_WORKBOOK, self.mc.output_context(root, "hort_env_systems"),
                project=project)):
            native = chain._issue_native()
        for key in ("published_office", "published_pdf"):
            original = Path(native[key])
            dest = root / original.name
            dest.write_bytes(original.read_bytes())
            native[key] = str(dest.resolve())
        native_path = write_text(root, "native-proof.json", json.dumps(native))
        native_pointer = {"path": native_path.name,
                          "sha256": self.core.digest(native_path.read_bytes())}
        for fmt, key in (("xlsx", "published_office"), ("pdf", "published_pdf")):
            artifact = Path(native[key])
            current = self.core.load(root)
            refs = [{"collection": "sections", "id": "body"}]
            native_value = {"id": "native_" + fmt, "path": artifact.name, "format": fmt,
                "file_hash": self.core.digest(artifact.read_bytes()), "target_refs": refs,
                "input_fingerprint": self.core.fingerprint(root, current, refs),
                "native_manifest": native_pointer}
            self.assertEqual(self.core.adopt_output(root, native_value, current["revision"],
                "native_" + fmt, companion_files=[native_pointer],
                major_id="hort_env_systems")["status"], "adopted")
            native_report = [{"check_id": "output_native", "status": "pass", "target": "native_" + fmt}]
            with mock.patch.object(self.core, "gate", return_value=native_report):
                self.assertEqual(self.core.export(root, "submission_candidate",
                                 major_id="hort_env_systems")["status"], "generated")
        current = self.core.load(root)
        paper = root / "companion-paper.md"
        paper.write_text("# synthetic\nstudent plan\n", encoding="utf-8")
        refs = [{"collection": "sections", "id": "body"}]
        companion_value = {"id": "companion_paper", "path": paper.name, "format": "md",
            "file_hash": self.core.digest(paper.read_bytes()), "target_refs": refs,
            "input_fingerprint": self.core.fingerprint(root, current, refs),
            "native_manifest": native_pointer}
        workbook = root / Path(native["published_office"]).name
        companion = {"path": workbook.name, "sha256": self.core.digest(workbook.read_bytes())}
        self.assertEqual(self.core.adopt_output(root, companion_value, current["revision"],
            "companion_paper", companion_files=[native_pointer, companion],
            major_id="hort_env_systems")["status"], "adopted")
        stored = self.core.load(root)["outputs"]["companion_paper"]
        self.core._export_authorize(root, {self.mc.OUTPUT_SCHOOL_PAPER},
            self.mc.output_context(root, "hort_env_systems"), outputs=(stored,))
        current = self.core.load(root)
        self.core.apply(root, {"request_id": "changed", "ops": []}, current["revision"])
        with mock.patch.object(self.core, "gate", return_value=report):
            with self.assertRaises(self.core.OperationError):
                self.core.export(root, "submission_candidate", major_id="hort_env_systems")

    def test_pdf_needs_workbook_profile_evidence_and_matching_format(self):
        pdf = self.root / "sheet.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        project = self.core.load(self.root)
        refs = self.core.sort_target_refs(self.core._export_refs(project))
        base = {"id": "sheet", "path": "sheet.pdf",
                "file_hash": self.core.digest(pdf.read_bytes()),
                "target_refs": refs,
                "input_fingerprint": self.core.fingerprint(self.root, project, refs)}
        with self.assertRaises(self.core.OperationError) as held:
            self.core.adopt_output(self.root, dict(base, format="xlsx"),
                                   project["revision"], "pdf",
                                   major_id="specialty_crops")
        self.assertEqual(held.exception.result["reason"], "output_format_mismatch")
        with self.assertRaises(self.core.OperationError) as held:
            self.core.adopt_output(self.root, dict(base, format="pdf"),
                                   project["revision"], "pdf2",
                                   major_id="specialty_crops")
        self.assertEqual(held.exception.result["reason"],
                         "finance_profile_evidence_invalid")

    def test_export_rejects_format_that_disagrees_with_bytes(self):
        (self.root / "sheet.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        with self.assertRaises(self.core.OperationError) as held:
            self.core._export_authorize(
                self.root,
                {self.mc.OUTPUT_SCHOOL_PAPER, self.mc.OUTPUT_SCHOOL_WORKBOOK},
                self.context, outputs=({"path": "sheet.pdf", "format": "xlsx"},))
        self.assertEqual(held.exception.result["reason"], "output_format_mismatch")
