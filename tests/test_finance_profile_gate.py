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
from dataclasses import replace

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
                    if major == "industrial_insects":
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

    @mock.patch.object(sys, "platform", "linux")
    def test_office_direct_matrix_holds_before_staging(self):
        # Synthetic staging boundary, independent of the macOS Excel hold.
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

    @mock.patch.object(sys, "platform", "linux")
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
                if major in ("specialty_crops", "fruit_trees"):
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
                if major != "industrial_insects":
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
                if major != "industrial_insects":
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

    @mock.patch.object(sys, "platform", "linux")
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

    @mock.patch.object(sys, "platform", "linux")
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

    @mock.patch.object(sys, "platform", "linux")
    def test_specialty_legacy_render_receipt_states_no_content_check(self):
        # Mock engine only; this is not evidence of macOS Excel support.
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
                           return_value=SimpleNamespace(
                               sheetnames=[], close=lambda: None)), \
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
                with self.assertRaises(self.mc.OutputHeldError) as held:
                    school.school_workbook(
                        spec, out, context=self.context(major))
                if major == "specialty_crops":
                    # Template route only: held before the generator body.
                    self.assertEqual(held.exception.reason,
                                     "specialty_template_route_required")
                check.assert_not_called()
                self.assertFalse(out.exists())

    def test_specialty_profile_variants_never_reach_the_generator(self):
        # Missing, empty or near-miss profile names are held earlier with
        # their own reason; none slips past the template-route gate.
        school = runtime("gg_school_excel")
        wrapper = runtime("build_excel_finance_template")
        root = self.roots["specialty_crops"]
        expected = {"school_17_sheet_v1": "specialty_template_route_required",
                    None: "finance_profile_evidence_required",
                    "": "finance_profile_evidence_required",
                    "School_17_Sheet_V1": "unsupported_finance_profile",
                    " school_17_sheet_v1": "unsupported_finance_profile"}
        for profile, reason in expected.items():
            spec = {"major_id": "specialty_crops", "profile": profile}
            (root / "variant.json").write_text(json.dumps(spec), encoding="utf-8")
            with self.subTest(profile=profile), \
                    mock.patch.object(school, "check_unsupported",
                                      side_effect=AssertionError("SCHOOL_REACHED")), \
                    mock.patch.object(wrapper, "validate_economic_inputs",
                                      side_effect=AssertionError("VALIDATION_REACHED")):
                with self.assertRaises(self.mc.OutputHeldError) as held:
                    school.school_workbook(spec, root / "v.xlsx",
                                           context=self.context("specialty_crops"))
                self.assertEqual(held.exception.reason, reason)
                with self.assertRaises(self.mc.OutputHeldError) as held:
                    wrapper.build(root, "variant.json", "v.xlsx", major_id="specialty_crops")
                self.assertEqual(held.exception.reason, reason)
                self.assertFalse((root / "v.xlsx").exists())



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
                with self.assertRaises(self.mc.OutputHeldError) as held:
                    wrapper.build(root, spec.name, "output.xlsx",
                                  major_id=major)
                if major == "specialty_crops":
                    self.assertEqual(held.exception.reason,
                                     "specialty_template_route_required")
                check.assert_not_called()
                self.assertFalse((root / "output.xlsx").exists())
        # A supported specialty profile still reaches economic validation.
        root = self.roots["specialty_crops"]
        spec = root / "wrapper-annual.json"
        spec.write_text(json.dumps({"major_id": "specialty_crops",
                                    "profile": "single_annual_cash_v1"}),
                        encoding="utf-8")
        with mock.patch.object(wrapper, "validate_economic_inputs",
                               side_effect=AssertionError(
                                   "VALIDATION_REACHED")) as check:
            with self.assertRaisesRegex(AssertionError, "VALIDATION_REACHED"):
                wrapper.build(root, spec.name, "output.xlsx",
                              major_id="specialty_crops")
            check.assert_called_once()
        self.assertFalse((root / "output.xlsx").exists())

    def test_windows_private_instance_creation_failure_is_reported(self):
        import contextlib
        import io
        helper = runtime("gg_office_win")
        client = SimpleNamespace(DispatchEx=mock.Mock(
            side_effect=RuntimeError("private_instance_unavailable")))
        pythoncom = SimpleNamespace(CoInitialize=mock.Mock(), CoUninitialize=mock.Mock())
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, {
                "pythoncom": pythoncom,
                "win32com": SimpleNamespace(client=client), "win32com.client": client}), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                helper.run_excel("synthetic.xlsx", "unused.pdf")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("private_instance_unavailable", stderr.getvalue())
        pythoncom.CoUninitialize.assert_called_once()


def register_synthetic_form(case, book, profile=None):
    """Isolate the school reference registry, never weaken the runtime gate.

    ``profile``을 주면 그 등록 프로필의 지문을 합성 양식에 맞춘다. 주지
    않으면 첫 항목(일반 과수)이다 — FY1처럼 두 번째 프로필을 시험하려면
    이름으로 골라야 한다.
    """
    import hashlib
    import tempfile
    school = runtime("gg_school_excel")
    data = json.loads(school.WORKBOOK_REFERENCE_SET.read_text(encoding="utf-8"))
    if profile is None:
        entry = data["workbook_profiles"][0]
    else:
        entry = next(e for e in data["workbook_profiles"]
                     if e.get("profile") == profile)
    entry["sheets"] = book.sheetnames
    entry["formula_fingerprints"] = {
        sheet.title: hashlib.sha256(json.dumps(sorted(
            (cell.coordinate, cell.value) for row in sheet for cell in row
            if cell.data_type == "f"), ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest() for sheet in book}
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    path = Path(temp.name) / "reference.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    patch = mock.patch.object(school, "WORKBOOK_REFERENCE_SET", path)
    patch.start()
    case.addCleanup(patch.stop)


class WorkbookProfileGateTests(ContractCase):
    """Exercise real major contracts; isolate only the native staging boundary."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.office = runtime("gg_office")
        self.tpl = runtime("gg_excel_template")
        self.fill = runtime("gg_excel_fill")
        self.root = self.make_project()
        bind_major(self.root)
        self.context = self.mc.output_context(self.root, "specialty_crops")
        self.auth = self.mc.authorize_output(self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context)
        self.fruit_root = self.make_project()
        bind_major(self.fruit_root, "fruit_trees")
        self.fruit_context = self.mc.output_context(self.fruit_root, "fruit_trees")
        self.fruit_auth = self.mc.authorize_output(self.mc.OUTPUT_SCHOOL_WORKBOOK, self.fruit_context)

    def artifacts(self, profile):
        from openpyxl import Workbook
        expected = runtime("gg_school_excel").profile_structure(profile)
        names = (expected["sheets"] if expected
                 else ["입력"] + [f"합성{index}" for index in range(1, 16)])
        source = self.root / "synthetic.xlsx"
        book = Workbook()
        book.active.title = names[0]
        book.active["A1"] = 25
        book.active["B1"] = "=A1*2"
        for name in names[1:]:
            book.create_sheet(name)
        if expected:
            register_synthetic_form(self, book)
        book.save(source)
        book.close()
        clear = self.root / "clear.json"
        fill = self.root / "fill.json"
        values = self.root / "values.json"
        marker = {} if profile is None else {"workbook_profile": profile}
        clear.write_text(json.dumps({"schema": "gg-xlsx-template-map/v1", **marker,
            "source": {"sha256": self.tpl.sha256(source)},
            "layout": {"variant": "unknown", "mapOrigin": "reviewed_custom"},
            "entries": [{"sheet": names[0], "cell": "A1", "action": "clear"}]}))
        fill.write_text(json.dumps({"schema": self.fill.MAP_SCHEMA, **marker,
            "template": {"sha256": self.tpl.sha256(source)},
            "entries": [{"sheet": names[0], "cell": "A1", "role": "farm.area",
                         "period": "2026", "editable": True, "source_note": "synthetic"}]}))
        values.write_text(json.dumps({"schema": self.fill.VALUES_SCHEMA,
            "values": [{"sheet": names[0], "cell": "A1", "value": 40,
                        "value_type": "integer", "answer_state": "provided",
                        "evidence_ref": {"source_id": "synthetic", "revision": 1,
                                         "locator": "A1", "origin": "synthetic"}}]}))
        return source, clear, fill, values

    def test_clear_and_fill_contract_matrix_before_writer(self):
        cases = (("specialty_crops", "school_17_sheet_v1", True),
                 ("specialty_crops", None, True),
                 ("fruit_trees", "fruit_16_sheet_v1", True),
                 ("fruit_trees", None, False),
                 ("fruit_trees", "school_17_sheet_v1", False),
                 ("fruit_trees", "single_annual_cash_v1", False),
                 ("hort_env_systems", "fruit_16_sheet_v1", False))
        for index, (major, profile, allowed) in enumerate(cases):
            source, clear, fill, values = self.artifacts(profile)
            if major == "specialty_crops":
                context = self.context
            elif major == "fruit_trees":
                context = self.fruit_context
            else:
                root = self.make_project()
                bind_major(root, major)
                context = self.mc.output_context(root, major)
            with self.subTest(major=major, profile=profile):
                for operation in ("clear", "fill"):
                    out = self.root / str(index) / operation / "copy.xlsx"
                    call = (lambda: self.tpl.blank_copy(source, clear, out, context=context)
                            ) if operation == "clear" else (
                            lambda: self.fill.fill_copy(source, fill, values, out, context=context))
                    if allowed:
                        receipt = call()
                        self.assertEqual(receipt["workbook_profile"], profile or "school_17_sheet_v1")
                        self.assertEqual(receipt["output"]["sha256"], self.tpl.sha256(out))
                    else:
                        with self.assertRaises(self.mc.OutputHeldError) as held:
                            call()
                        self.assertEqual(held.exception.reason, "unsupported_finance_profile")
                        self.assertFalse(out.parent.exists())

    def test_declared_but_unsupported_capability_and_calculation_only_hold(self):
        registry = self.mc.default_registry()
        module = registry.resolve("fruit_trees")
        for declared, unsupported in ((("fruit_16_sheet_v1",), True),
                                     (("single_annual_cash_v1",), False)):
            changed = replace(module, workbook_profiles=declared,
                              finance_capabilities=tuple(
                                  replace(cap, status="unsupported") if unsupported and
                                  cap.profile == "fruit_16_sheet_v1" else cap
                                  for cap in module.finance_capabilities))
            source, clear, fill, values = self.artifacts("fruit_16_sheet_v1")
            with mock.patch.object(registry, "resolve", side_effect=lambda major:
                                   changed if major == "fruit_trees" else self.mc.SPECIALTY_CROPS_MODULE), \
                    mock.patch.object(self.mc, "default_registry", return_value=registry):
                for op in (lambda: self.tpl.blank_copy(source, clear, self.root / "held" / "c.xlsx",
                                                       context=self.fruit_context),
                           lambda: self.fill.fill_copy(source, fill, values, self.root / "held" / "f.xlsx",
                                                       context=self.fruit_context)):
                    with self.assertRaises(self.mc.OutputHeldError):
                        op()
                with self.assertRaises(self.mc.OutputHeldError):
                    self.office._preflight_excel_profile(source, [self.fruit_auth], self.fruit_context,
                                                          spec={"profile": "fruit_16_sheet_v1"})
            self.assertFalse((self.root / "held").exists())

    @mock.patch.object(sys, "platform", "linux")
    def test_office_spec_holds_wrong_profile_before_stage(self):
        source, *_ = self.artifacts("fruit_16_sheet_v1")
        for profile in ("fruit_16_sheet_v1", "single_annual_cash_v1", None, "school_17_sheet_v1"):
            spec = self.root / "spec.json"
            spec.write_text(json.dumps({"profile": profile}))
            with mock.patch.object(self.office, "stage_job", side_effect=RuntimeError("stage reached")) as stage:
                if profile == "fruit_16_sheet_v1":
                    with self.assertRaisesRegex(RuntimeError, "stage reached"):
                        self.office.process_excel(source, self.root / "out", spec_file=spec,
                                                  context=self.fruit_context)
                else:
                    with self.assertRaises(self.mc.OutputHeldError):
                        self.office.process_excel(source, self.root / "out", spec_file=spec,
                                                  context=self.fruit_context)
                    stage.assert_not_called()

    def test_receipt_profile_hash_and_lineage_are_bound(self):
        source, clear, fill, values = self.artifacts("fruit_16_sheet_v1")
        auth = self.fruit_auth
        output = self.root / "filled.xlsx"
        data = self.fill.fill_copy(source, fill, values, output, context=self.fruit_context)
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps(data))
        binding, profile = self.office._preflight_excel_profile(
            output, [auth], self.context, template_receipt=receipt)
        self.assertEqual(profile, "fruit_16_sheet_v1")
        self.assertEqual(binding["input_sha256"], self.tpl.sha256(output))
        lineage = self.office.verify_template_lineage(output, [receipt])
        self.assertEqual(lineage["workbook_profile"], profile)
        for changed in ({"workbook_profile": "school_17_sheet_v1"},
                        {"output": dict(data["output"], sha256="0" * 64)}):
            receipt.write_text(json.dumps(dict(data, **changed)))
            with self.assertRaises(ValueError):
                self.office._preflight_excel_profile(output, [auth], self.context,
                                                      template_receipt=receipt)

    def test_batch_without_profile_evidence_holds_before_any_stage(self):
        source, *_ = self.artifacts("fruit_16_sheet_v1")
        with mock.patch.object(self.office, "stage_job") as stage:
            with self.assertRaises(self.mc.OutputHeldError):
                self.office.process_batch([source, source], self.root / "out",
                                          context=self.fruit_context)
            stage.assert_not_called()

    def test_clear_receipt_profile_and_native_lineage_agree(self):
        source, clear, _, _ = self.artifacts("fruit_16_sheet_v1")
        auth = self.fruit_auth
        output = self.root / "blank.xlsx"
        data = self.tpl.blank_copy(source, clear, output, context=self.fruit_context)
        receipt = self.root / "clear-receipt.json"
        receipt.write_text(json.dumps(data))
        binding, profile = self.office._preflight_excel_profile(
            output, [auth], self.context, template_receipt=receipt)
        pdf = self.root / "native.pdf"
        pdf.write_bytes(b"%PDF-1.4 synthetic")
        native = {"status": "converted", "engine": "Microsoft Excel",
                  "input_sha256": self.tpl.sha256(output),
                  "published_office": str(output), "published_office_sha256": self.tpl.sha256(output),
                  "published_pdf": str(pdf), "published_pdf_sha256": self.tpl.sha256(pdf),
                  "validation": {"structure_issues": [], "template_receipt": binding,
                                 "workbook_profile": profile, "finance_profile": profile}}
        native_path = self.root / "native.json"
        native_path.write_text(json.dumps(native))
        self.assertEqual(self.office.verify_template_lineage(output, [receipt, native_path])[
            "workbook_profile"], "fruit_16_sheet_v1")
        native["validation"]["workbook_profile"] = "school_17_sheet_v1"
        native_path.write_text(json.dumps(native))
        with self.assertRaisesRegex(ValueError, "profile differs"):
            self.office.verify_template_lineage(output, [receipt, native_path])

if __name__ == "__main__":
    import unittest
    unittest.main()
