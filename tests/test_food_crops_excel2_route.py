"""식량작물 엑셀 2판 — N1·N2 경로: 잠금 해제와 전공별 기본 프로필.

1판까지 `school_excel_workbook`은 식량작물에서 잠겨 있었고, 수식 고치기와
템플릿 복제 도구는 `school_17_sheet_v1`을 손으로 박아 두었다. 이 시험은 세
가지를 잠근다: (1) 16시트 식량작물 양식만 이 전공에서 통과하고 17시트 이름은
거절된다, (2) 기본 프로필이 정본의 전공을 따라가고 표에 없는 전공은 지금까지의
17시트 문자열 그대로다, (3) `patch_copy`·`create`가 그 기본값을 쓰므로 다른
전공의 동작이 바뀌지 않는다.

학교 양식 원본은 쓰지 않는다. 아래 통합문서는 수식 한 칸짜리 합성이이고,
실제 16시트 지도·바이트 고정은 `test_food_crops_excel2_blank_copy.py`가 맡는다.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime

FOOD = "food_crops"
SPECIALTY = "specialty_crops"
FRUIT = "fruit_trees"
FOOD_PROFILE = "food_crops_16_sheet_v1"
SCHOOL_PROFILE = "school_17_sheet_v1"
FRUIT_PROFILE = "fruit_16_sheet_v1"


def _cli(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8")


class RouteTests(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.tpl = runtime("gg_excel_template")
        self.patch = runtime("gg_excel_formula_patch")

    def _authorization(self, major_id):
        module = self.mc.default_registry().resolve(major_id)
        return self.mc.OutputAuthorization(
            output=self.mc.OUTPUT_SCHOOL_WORKBOOK, major_id=major_id,
            module_version=module.module_version,
            contract_version=self.mc.CONTRACT_VERSION, project_revision=1,
            binding_evidence="synthetic")

    def _synthetic(self, root):
        """One-sheet workbook: a label, a number and a formula."""
        from openpyxl import Workbook
        book = Workbook()
        book.active.title = "Visible"
        book.active["A1"] = "합성 표제"
        book.active["B2"] = 7
        book.active["C3"] = "=1+1"
        source = Path(root) / "synthetic.xlsx"
        book.save(source)
        book.close()
        return source

    def _patch_map(self, root, source):
        path = Path(root) / "patch-map.json"
        path.write_text(json.dumps({
            "schema": self.patch.MAP_SCHEMA,
            "source": {"sha256": self.tpl.sha256(source)},
            "patches": [{"sheet": "Visible", "cell": "C3",
                         "expected_formula": "=1+1", "new_formula": "=1+2",
                         "reason": "합성 보정",
                         "evidence_ref": {"source_id": "synthetic",
                                          "revision": 1,
                                          "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        return path

    def test_the_school_workbook_output_is_authorized_for_food_crops(self):
        root = self.make_project()
        bind_major(root, FOOD)
        authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK,
            self.mc.output_context(root, FOOD))
        self.assertEqual(authorization.major_id, FOOD)
        self.assertEqual(authorization.output, self.mc.OUTPUT_SCHOOL_WORKBOOK)
        self.assertEqual(
            authorization.to_dict()["module_version"],
            self.mc.default_registry().resolve(FOOD).module_version)

    def test_only_the_16_sheet_profile_is_supported_here(self):
        authorization = self._authorization(FOOD)
        self.mc.require_finance_profile(authorization, FOOD_PROFILE)
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.mc.require_finance_profile(authorization, SCHOOL_PROFILE)
        self.assertEqual(caught.exception.reason, "unsupported_finance_profile")
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.mc.require_finance_profile(authorization, FRUIT_PROFILE)
        self.assertEqual(caught.exception.reason, "unsupported_finance_profile")

    def test_the_default_profile_follows_the_bound_major(self):
        self.assertEqual(
            self.mc.default_workbook_profile(self._authorization(FOOD)),
            FOOD_PROFILE)
        for major in (SPECIALTY, FRUIT, "hort_env_systems",
                      "industrial_insects", "dairy"):
            with self.subTest(major=major):
                self.assertEqual(
                    self.mc.default_workbook_profile(self._authorization(major)),
                    SCHOOL_PROFILE)

    def test_patch_copy_defaults_to_the_majors_own_profile(self):
        from tests.test_finance_profile_gate import register_synthetic_form
        food_root = self.make_project()
        bind_major(food_root, FOOD)
        food_ctx = self.mc.output_context(food_root, FOOD)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            mapping = self._patch_map(temp, source)
            # A registered profile is structure-bound, so the one-sheet
            # synthetic book only patches once its fingerprints are the
            # registered ones. This isolates the registry, not the check.
            from openpyxl import load_workbook
            book = load_workbook(source)
            register_synthetic_form(self, book, profile=FOOD_PROFILE)
            book.close()
            out = Path(temp) / "food-patched.xlsx"
            receipt = self.patch.patch_copy(source, mapping, out,
                                            context=food_ctx)
            self.assertTrue(out.exists())
            self.assertEqual(receipt["patched"][0]["new_formula"], "1+2")
            self.assertEqual(receipt["majorAuthorization"]["major_id"], FOOD)

            refused = Path(temp) / "food-refused.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                self.patch.patch_copy(source, mapping, refused, context=food_ctx,
                                      profile=SCHOOL_PROFILE)
            self.assertEqual(caught.exception.reason,
                             "unsupported_finance_profile")
            self.assertFalse(refused.exists())

        specialty_root = self.make_project()
        bind_major(specialty_root, SPECIALTY)
        specialty_ctx = self.mc.output_context(specialty_root, SPECIALTY)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            mapping = self._patch_map(temp, source)
            out = Path(temp) / "specialty-patched.xlsx"
            receipt = self.patch.patch_copy(source, mapping, out,
                                            context=specialty_ctx)
            self.assertTrue(out.exists())
            self.assertEqual(receipt["majorAuthorization"]["major_id"],
                             SPECIALTY)

    def test_the_patch_cli_checks_the_form_before_making_the_folder(self):
        """The CLI cannot reach the patch engine with a workbook that is not
        the registered 16-sheet form, and a refusal leaves no folder behind."""
        root = self.make_project()
        bind_major(root, FOOD)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            mapping = self._patch_map(temp, source)
            out = Path(temp) / "not-yet" / "cli-patched.xlsx"
            good = _cli("gg_excel_formula_patch.py", "--source", source,
                        "--map", mapping, "--out", out,
                        "--receipt", str(out) + ".receipt.json",
                        "--project", root, "--major", FOOD)
            self.assertEqual(good.returncode, 2, good.stdout + good.stderr)
            self.assertEqual(json.loads(good.stdout)["reason"],
                             "workbook_structure_mismatch")
            self.assertFalse(out.exists())
            self.assertFalse(out.parent.exists())

            refused = Path(temp) / "cli-refused.xlsx"
            bad = _cli("gg_excel_formula_patch.py", "--source", source,
                       "--map", mapping, "--out", refused,
                       "--receipt", str(refused) + ".receipt.json",
                       "--project", root, "--major", FOOD,
                       "--profile", SCHOOL_PROFILE)
            self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
            self.assertEqual(json.loads(bad.stdout)["reason"],
                             "unsupported_finance_profile")
            self.assertFalse(refused.exists())

    def test_the_patch_entry_refuses_to_overwrite_and_makes_no_folder(self):
        """Every refusal lands before a write: the source itself, an existing
        file, and (through the CLI) a folder that does not exist yet."""
        from tests.test_finance_profile_gate import register_synthetic_form
        from openpyxl import load_workbook
        root = self.make_project()
        bind_major(root, FOOD)
        context = self.mc.output_context(root, FOOD)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            mapping = self._patch_map(temp, source)
            book = load_workbook(source)
            register_synthetic_form(self, book, profile=FOOD_PROFILE)
            book.close()

            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError,
                                        "refusing to write over the source"):
                self.patch.patch_copy(source, mapping, source, context=context)
            self.assertEqual(source.read_bytes(), before)

            existing = Path(temp) / "already-here.xlsx"
            existing.write_bytes(b"sentinel")
            with self.assertRaises(FileExistsError):
                self.patch.patch_copy(source, mapping, existing, context=context)
            self.assertEqual(existing.read_bytes(), b"sentinel")

        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            mapping = self._patch_map(temp, source)
            out = Path(temp) / "two" / "deep" / "cli.xlsx"
            refused = _cli("gg_excel_formula_patch.py", "--source", source,
                           "--map", mapping, "--out", out,
                           "--receipt", str(out) + ".receipt.json",
                           "--project", root, "--major", FOOD)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertEqual(json.loads(refused.stdout)["reason"],
                             "workbook_structure_mismatch")
            self.assertFalse(out.parent.exists())

    def test_filling_values_stays_closed_for_food_crops(self):
        """Only the blank copy is open; filling values is refused at the
        entry point, before any output exists."""
        fill = runtime("gg_excel_fill")
        root = self.make_project()
        bind_major(root, FOOD)
        context = self.mc.output_context(root, FOOD)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            fill_map = Path(temp) / "fill-map.json"
            fill_map.write_text(json.dumps({
                "schema": "gg-xlsx-fill-map/v1",
                "template": {"sha256": self.tpl.sha256(source)},
                "entries": [{"sheet": "Visible", "cell": "B2", "role": "input",
                             "period": "2030년", "source_note": "합성",
                             "editable": True}]}, ensure_ascii=False),
                encoding="utf-8")
            values = Path(temp) / "values.json"
            values.write_text(json.dumps(
                {"schema": "gg-xlsx-fill-values/v1", "values": []}),
                encoding="utf-8")

            out = Path(temp) / "filled.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                fill.fill_copy(source, fill_map, values, out, context=context)
            self.assertEqual(caught.exception.reason,
                             "fill_unsupported_for_major")
            self.assertFalse(out.exists())

            receipt = Path(temp) / "filled.receipt.json"
            result = _cli("gg_excel_fill.py", "--template", source,
                          "--map", fill_map, "--values", values,
                          "--out", out, "--receipt", receipt,
                          "--project", root, "--major", FOOD)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["reason"],
                             "fill_unsupported_for_major")
            self.assertFalse(out.exists())
            self.assertFalse(receipt.exists())

    def test_create_copies_an_explicit_food_crops_source(self):
        root = self.make_project()
        bind_major(root, FOOD)
        context = self.mc.output_context(root, FOOD)
        with tempfile.TemporaryDirectory() as temp:
            source = self._synthetic(temp)
            out = Path(temp) / "create.xlsx"
            receipt = self.tpl.create_workbook(source, out, context=context)
            self.assertEqual(receipt["status"], "template_copy")
            self.assertEqual(receipt["source"]["sha256"],
                             self.tpl.sha256(source))
            self.assertEqual(out.read_bytes(), source.read_bytes())
            # The profile gate passed; the hold that remains is the one the
            # contract has always had for a major without a bundled form.
            missing = Path(temp) / "no-source.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                self.tpl.create_workbook(None, missing, context=context,
                                         template_ref="X01")
            self.assertEqual(caught.exception.reason,
                             "workbook_survey_required")
            self.assertFalse(missing.exists())
