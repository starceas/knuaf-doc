"""Synthetic pre-staging checks for the finance profile migration.

Every negative case asserts that the next writer/native boundary was not
reached. No Office application or real teaching workbook is used.
"""
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime


MAJORS = ("specialty_crops", "fruit_trees", "industrial_insects",
          "hort_env_systems")
HORT_PROFILE = "hort_18_sheet_reconstructed_v1"


class FinanceGateTests(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.office = runtime("gg_office")
        self.roots = {}
        for major in MAJORS:
            root = self.make_project()
            bind_major(root, major)
            self.roots[major] = root

    def context(self, major):
        return self.mc.output_context(self.roots[major], major)

    def xlsx(self, major, name="synthetic.xlsx"):
        path = self.roots[major] / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
        return path

    def authorization(self, major):
        return self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context(major))

    def test_named_profile_matrix_follows_policy_b(self):
        profiles = (None, "school_17_sheet_v1",
                    "single_annual_cash_v1", HORT_PROFILE)
        for major in MAJORS:
            for profile in profiles:
                with self.subTest(major=major, profile=profile):
                    if major in ("fruit_trees", "industrial_insects"):
                        with self.assertRaises(self.mc.OutputHeldError):
                            self.authorization(major)
                        continue
                    auth = self.authorization(major)
                    allowed = ((major == "specialty_crops" and profile in
                                ("school_17_sheet_v1", "single_annual_cash_v1"))
                               or (major == "hort_env_systems" and
                                   profile == HORT_PROFILE))
                    if allowed:
                        self.assertIsNone(
                            self.mc.require_finance_profile(auth, profile))
                    else:
                        with self.assertRaises(self.mc.OutputHeldError):
                            self.mc.require_finance_profile(auth, profile)

    def test_office_direct_matrix_holds_before_staging(self):
        for major in MAJORS:
            path = self.xlsx(major)
            with self.subTest(major=major), mock.patch.object(
                    self.office, "stage_job", side_effect=AssertionError(
                        "STAGE_REACHED")) as stage:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "STAGE_REACHED"):
                        self.office.process_excel(
                            path, self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_excel(
                            path, self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_not_called()

    def test_office_spec_and_batch_matrix_before_staging(self):
        for major in MAJORS:
            path = self.xlsx(major)
            spec = self.roots[major] / "spec.json"
            spec.write_text(json.dumps({"major_id": major,
                                        "profile": "school_17_sheet_v1"}),
                            encoding="utf-8")
            with self.subTest(major=major, entry="spec"), \
                    mock.patch.object(self.office, "stage_job",
                                      side_effect=AssertionError(
                                          "STAGE_REACHED")) as stage:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "STAGE_REACHED"):
                        self.office.process_excel(
                            path, self.roots[major] / "out", spec_file=spec,
                            context=self.context(major))
                    stage.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_excel(
                            path, self.roots[major] / "out", spec_file=spec,
                            context=self.context(major))
                    stage.assert_not_called()

    def test_office_template_receipt_matrix_before_staging(self):
        for major in MAJORS:
            source = self.xlsx(major)
            receipt = self.roots[major] / "invalid-receipt.json"
            receipt.write_text('{"schema":"wrong"}', encoding="utf-8")
            with self.subTest(major=major), mock.patch.object(
                    self.office, "stage_job") as stage:
                if major == "specialty_crops":
                    with self.assertRaises(ValueError):
                        self.office.process_excel(
                            source, self.roots[major] / "out",
                            template_receipt=receipt,
                            context=self.context(major))
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_excel(
                            source, self.roots[major] / "out",
                            template_receipt=receipt,
                            context=self.context(major))
                stage.assert_not_called()

    def test_office_word_four_major_matrix(self):
        for major in MAJORS:
            source = self.roots[major] / "synthetic.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", "<document/>")
            with self.subTest(major=major), mock.patch.object(
                    self.office, "stage_job",
                    side_effect=AssertionError("STAGE_REACHED")) as stage:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "STAGE_REACHED"):
                        self.office.process_word(
                            source, self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_word(
                            source, self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_not_called()
            with self.subTest(major=major, entry="batch_word"), \
                    mock.patch.object(self.office, "stage_job",
                                      side_effect=AssertionError(
                                          "STAGE_REACHED")) as stage:
                if major == "specialty_crops":
                    result = self.office.process_batch(
                        [source], self.roots[major] / "out",
                        context=self.context(major))
                    self.assertIn("STAGE_REACHED", str(result))
                    stage.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_batch(
                            [source], self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_not_called()

    def test_office_batch_excel_four_major_matrix(self):
        for major in MAJORS:
            path = self.xlsx(major)
            with self.subTest(major=major), mock.patch.object(
                    self.office, "stage_job",
                    side_effect=AssertionError("STAGE_REACHED")) as stage:
                if major == "specialty_crops":
                    result = self.office.process_batch(
                        [path], self.roots[major] / "out",
                        context=self.context(major))
                    self.assertIn("STAGE_REACHED", str(result))
                    stage.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_batch(
                            [path], self.roots[major] / "out",
                            context=self.context(major))
                    stage.assert_not_called()

    def test_spec_missing_malformed_and_wrong_profile_hold_before_stage(self):
        for major in ("specialty_crops", "hort_env_systems"):
            path = self.xlsx(major)
            spec = self.roots[major] / "spec.json"
            for contents in ("{", "[]",
                             '{"profile":"school_17_sheet_v1",'
                             '"profile":"school_17_sheet_v1"}',
                             '{"profile":NaN}',
                             json.dumps({"major_id": major}),
                             json.dumps({"major_id": major,
                                         "profile": HORT_PROFILE})):
                spec.write_text(contents, encoding="utf-8")
                with self.subTest(major=major, spec=contents), mock.patch.object(
                        self.office, "stage_job") as stage:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_excel(
                            path, self.roots[major] / "out", spec_file=spec,
                            context=self.context(major))
                    stage.assert_not_called()

    def test_hort_receipt_schema_and_output_hash_hold(self):
        major = "hort_env_systems"
        source = self.xlsx(major)
        auth = self.authorization(major)
        receipt = self.roots[major] / "transform.json"
        receipt.write_text("{}", encoding="utf-8")
        base = {
            "schema": "knuaf-hort-finance-receipt/v1", "stage": "transform",
            "status": "pass", "profile": HORT_PROFILE,
            "major_id": major, "module_version": auth.module_version,
            "contract_version": auth.contract_version,
            "project_revision": auth.project_revision,
            "binding_evidence": auth.binding_evidence,
            "project_root": str(self.roots[major].resolve()),
            "output_artifacts": [{"sha256": self.office.compute_sha256(
                source.read_bytes())}],
        }
        for change in ({"schema": "wrong"},
                       {"output_artifacts": [{"sha256": "0" * 64}]},
                       {"project_revision": auth.project_revision - 1},
                       {"project_root": str(self.roots["specialty_crops"])}):
            data = dict(base, **change)
            checker = SimpleNamespace(check_transform_receipt=lambda _: data)
            with self.subTest(change=change), mock.patch.dict(
                    sys.modules, {"gg_hort_workbook": checker}):
                with self.assertRaises(self.mc.OutputHeldError):
                    self.office._hort_transform_binding(
                        source, receipt, auth, self.context(major))
        checker = SimpleNamespace(check_transform_receipt=lambda _: base)
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": checker}):
            binding = self.office._hort_transform_binding(
                source, receipt, auth, self.context(major))
        self.assertEqual(binding["authority"], "hort_transform_receipt")
        self.assertEqual(binding["input_sha256"], base["output_artifacts"][0]["sha256"])
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": checker}), \
                mock.patch.object(self.office, "stage_job",
                                  side_effect=AssertionError("STAGE_REACHED")) as stage:
            with self.assertRaisesRegex(AssertionError, "STAGE_REACHED"):
                self.office.process_excel(
                    source, self.roots[major] / "out",
                    template_receipt=receipt, context=self.context(major))
            stage.assert_called_once()
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": checker}), \
                mock.patch.object(self.office, "stage_job", return_value=(
                    self.roots[major], source, source,
                    self.roots[major] / "unused.pdf", "0" * 64)), \
                mock.patch.object(self.office, "_run_excel_engine") as engine:
            with self.assertRaises(self.mc.OutputHeldError):
                self.office.process_excel(
                    source, self.roots[major] / "out",
                    template_receipt=receipt, context=self.context(major))
            engine.assert_not_called()

    def test_hort_lineage_uses_transform_checker(self):
        major = "hort_env_systems"
        source = self.xlsx(major)
        digest = self.office.compute_sha256(source.read_bytes())
        receipt = self.roots[major] / "transform.json"
        receipt.write_text(json.dumps({"schema":
            "knuaf-hort-finance-receipt/v1"}), encoding="utf-8")
        checked = {
            "input_artifacts": [{"path": str(source), "sha256": digest}],
            "output_artifacts": [{"path": str(source), "sha256": digest}],
        }
        checker = SimpleNamespace(check_transform_receipt=lambda _: checked)
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": checker}):
            result = self.office.verify_template_lineage(source, [receipt])
        self.assertEqual(result["authority"], "hort_transform_lineage_only")
        self.assertEqual(result["status"], "pass")
        operation = self.roots[major] / "fill.json"
        operation.write_text(json.dumps({"schema": "gg-xlsx-fill-receipt/v1"}),
                             encoding="utf-8")
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": checker}):
            with self.assertRaisesRegex(ValueError, "only lead to native"):
                self.office.verify_template_lineage(source,
                                                    [receipt, operation])

    def test_specialty_legacy_render_receipt_states_no_content_check(self):
        major = "specialty_crops"
        source = self.xlsx(major)
        root = self.roots[major]
        engine = SimpleNamespace(returncode=0, stdout="synthetic", stderr="")
        stage_result = (root, source, source, root / "unused.pdf",
                        self.office.compute_sha256(source.read_bytes()))
        with mock.patch.object(self.office, "stage_job",
                               return_value=stage_result), \
                mock.patch.object(self.office, "_run_excel_engine",
                                  return_value=engine), \
                mock.patch.object(self.office, "validate_pdf_file",
                                  return_value={"pages": 1}), \
                mock.patch.object(self.office, "workbook_worksheet_count",
                                  return_value=1), \
                mock.patch("openpyxl.load_workbook",
                           return_value=SimpleNamespace(sheetnames=[])), \
                mock.patch.object(self.office, "_publish_outputs",
                                  side_effect=lambda **kw: kw["extra_validation"]):
            result = self.office.process_excel(
                source, root / "out", context=self.context(major))
        self.assertEqual(result["finance_profile"], "legacy_unprofiled_render")
        self.assertEqual(result["financial_content_validation"], "not_run")

    def test_batch_preflights_every_workbook_before_first_stage(self):
        # The current CLI cannot attach a per-input transform receipt.
        major = "hort_env_systems"
        files = [self.xlsx(major, "first.xlsx"),
                 self.xlsx(major, "second.xlsx")]
        with mock.patch.object(self.office, "stage_job") as stage:
            with self.assertRaises(self.mc.OutputHeldError):
                self.office.process_batch(files, self.roots[major] / "out",
                                          context=self.context(major))
            stage.assert_not_called()

    def test_direct_template_writers_four_major_matrix(self):
        calls = (
            ("gg_excel_template", "blank_copy", 3),
            ("gg_excel_fill", "fill_copy", 4),
            ("gg_excel_formula_patch", "patch_copy", 3),
            ("gg_excel_print", "apply", 4),
        )
        for major in MAJORS:
            p = self.roots[major] / "missing"
            for name, method, arity in calls:
                with self.subTest(major=major, entry=name):
                    module = runtime(name)
                    try:
                        getattr(module, method)(*[p] * arity,
                                                context=self.context(major))
                    except self.mc.OutputHeldError as error:
                        self.assertNotEqual(major, "specialty_crops")
                        self.assertIn(error.reason,
                                      ("unsupported_output",
                                       "unsupported_finance_profile"))
                    except (OSError, ValueError):
                        self.assertEqual(major, "specialty_crops")
                    else:
                        self.fail("missing input unexpectedly produced output")
                    self.assertFalse(p.exists())

    def test_generic_and_school_generators_cannot_route_hort(self):
        finance = runtime("gg_finance")
        school = runtime("gg_school_excel")
        root = self.roots["hort_env_systems"]
        out = root / "blocked.xlsx"
        spec = {"major_id": "hort_env_systems", "profile": HORT_PROFILE}
        with mock.patch.object(finance, "_calculate") as calculate:
            with self.assertRaises(self.mc.OutputHeldError):
                finance.workbook(spec, out, context=self.context("hort_env_systems"))
            calculate.assert_not_called()
        with self.assertRaises(self.mc.OutputHeldError):
            school.school_workbook(spec, out,
                                   context=self.context("hort_env_systems"))
        self.assertFalse(out.exists())

    def test_generic_finance_generator_four_major_matrix(self):
        finance = runtime("gg_finance")
        for major in MAJORS:
            out = self.roots[major] / "generic.xlsx"
            spec = {"major_id": major, "profile": "single_annual_cash_v1"}
            with self.subTest(major=major), \
                    mock.patch.object(finance, "_calculate",
                                      side_effect=AssertionError(
                                          "CALCULATE_REACHED")) as calculate:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "CALCULATE_REACHED"):
                        finance.workbook(spec, out, context=self.context(major))
                    calculate.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        finance.workbook(spec, out, context=self.context(major))
                    calculate.assert_not_called()
                self.assertFalse(out.exists())

    def test_school_generator_four_major_matrix(self):
        school = runtime("gg_school_excel")
        for major in MAJORS:
            out = self.roots[major] / "school.xlsx"
            spec = {"major_id": major, "profile": "school_17_sheet_v1"}
            with self.subTest(major=major), \
                    mock.patch.object(school, "check_unsupported",
                                      side_effect=AssertionError(
                                          "SCHOOL_REACHED")) as check:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "SCHOOL_REACHED"):
                        school.school_workbook(
                            spec, out, context=self.context(major))
                    check.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        school.school_workbook(
                            spec, out, context=self.context(major))
                    check.assert_not_called()
                self.assertFalse(out.exists())

    def test_build_wrapper_holds_hort_before_economic_validation(self):
        wrapper = runtime("build_excel_finance_template")
        major = "hort_env_systems"
        root = self.roots[major]
        spec = root / "spec.json"
        spec.write_text(json.dumps({"major_id": major,
                                    "profile": HORT_PROFILE}),
                        encoding="utf-8")
        with mock.patch.object(wrapper, "validate_economic_inputs") as check:
            with self.assertRaises(self.mc.OutputHeldError):
                wrapper.build(root, "spec.json", "output.xlsx",
                              major_id=major)
            check.assert_not_called()
        self.assertFalse((root / "output.xlsx").exists())

    def test_build_wrapper_four_major_matrix(self):
        wrapper = runtime("build_excel_finance_template")
        for major in MAJORS:
            root = self.roots[major]
            profile = (HORT_PROFILE if major == "hort_env_systems"
                       else "school_17_sheet_v1")
            spec = root / "wrapper-spec.json"
            spec.write_text(json.dumps({"major_id": major,
                                        "profile": profile}),
                            encoding="utf-8")
            with self.subTest(major=major), \
                    mock.patch.object(wrapper, "validate_economic_inputs",
                                      side_effect=AssertionError(
                                          "VALIDATION_REACHED")) as check:
                if major == "specialty_crops":
                    with self.assertRaisesRegex(AssertionError,
                                                "VALIDATION_REACHED"):
                        wrapper.build(root, spec.name, "output.xlsx",
                                      major_id=major)
                    check.assert_called_once()
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        wrapper.build(root, spec.name, "output.xlsx",
                                      major_id=major)
                    check.assert_not_called()
                self.assertFalse((root / "output.xlsx").exists())

    def test_windows_direct_helper_stays_blocked_before_com_import(self):
        helper = runtime("gg_office_win")
        with mock.patch.object(helper.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"pythoncom": None}):
            with self.assertRaises(SystemExit) as caught:
                helper.run_excel("synthetic.xlsx", "unused.pdf")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
