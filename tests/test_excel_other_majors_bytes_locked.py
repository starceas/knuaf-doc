"""식량작물 2판이 다른 전공의 엑셀 산출물을 건드리지 않았는지 고정한다.

N2는 `patch_copy`/`create`/`blank_copy`의 기본 프로필을 '전공별 표'로 바꿨다.
표에 없는 전공은 지금까지의 `school_17_sheet_v1` 문자열을 그대로 받으므로
결과 바이트가 같아야 한다. 아래 해시는 변경 전 트리(`807903b`)와 변경 후
트리에서 같은 스크립트를 돌려 얻은 값이고, 두 트리에서 동일했다.

만들어 낸 xlsx의 sha256은 zip 메타데이터까지 포함하므로 이 상수가 곧
"도구 산출물이 한 바이트도 달라지지 않았다"는 뜻이다. 다른 전공은 애초에
산출물을 만들지 못하고(정책 B 거절) 파일도 남기지 않는다 — 그것도 고정한다.
"""

import hashlib
import json
import tempfile
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime

BUNDLED_SHA256 = "6d3c9fe5e5992108a8bab18c0098edb0d808f0d14c099878a53939b17de88c7d"
SPECIALTY_CREATE = "6d3c9fe5e5992108a8bab18c0098edb0d808f0d14c099878a53939b17de88c7d"
SPECIALTY_BLANK = "2f81143e01729565b0f7b9cf3d8ac0a012d483106c964b0b18b1c71fe7c433d5"
SPECIALTY_PATCH = "5c8f0951c47532d9045d7c5ed3e625052a07d8e9dd9025a5a05fc6127be6481d"
SPECIALTY_PATCH_CACHES = 1643
SPECIALTY_BLANK_UNCLASSIFIED_TEXT = 12
SALES = " 5. 판매계획"
HELD_MAJORS = ("fruit_trees", "hort_env_systems")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class OtherMajorsLockedTests(ContractCase):
    def setUp(self):
        self.tpl = runtime("gg_excel_template")
        self.patch = runtime("gg_excel_formula_patch")
        self.mc = runtime("gg_major_contract")
        self.bundled = self.tpl.BUNDLED_TEMPLATE
        self.assertEqual(_sha(self.bundled), BUNDLED_SHA256)
        self.root = self.make_project()

    def _fixtures(self, root):
        clear_map = Path(root) / "clear.json"
        clear_map.write_text(json.dumps({
            "schema": "gg-xlsx-template-map/v1",
            "source": {"sha256": _sha(self.bundled)},
            "layout": {"mapOrigin": "default_x01"},
            "mapStatus": "ok",
            "entries": [
                {"sheet": SALES, "cell": "B2", "action": "clear",
                 "role": "input.student_estimate", "explicit": True,
                 "reason": "합성"},
                {"sheet": SALES, "cell": "B4", "action": "preserve",
                 "role": "structure.source_note", "explicit": True,
                 "reason": "합성"},
            ]}, ensure_ascii=False), encoding="utf-8")
        patch_map = Path(root) / "patch.json"
        patch_map.write_text(json.dumps({
            "schema": self.patch.MAP_SCHEMA,
            "source": {"sha256": _sha(self.bundled)},
            "patches": [{"sheet": "3.투자계획", "cell": "C8",
                         "expected_formula": "=SUM(D8:F8)",
                         "new_formula": "=SUM(D8:D8)",
                         "reason": "합성",
                         "evidence_ref": {"source_id": "s", "revision": 1,
                                          "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        return clear_map, patch_map

    def _context(self, major):
        bind_major(self.root, major)
        return self.mc.output_context(self.root, major)

    def test_the_bundled_17_sheet_path_is_byte_identical(self):
        ctx = self._context("specialty_crops")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            created = Path(temp) / "create.xlsx"
            self.tpl.create_workbook(None, created, context=ctx,
                                     template_ref="X01",
                                     reference_inventory={"files": [],
                                                          "surveys": []})
            self.assertEqual(_sha(created), SPECIALTY_CREATE)

            blank = Path(temp) / "blank.xlsx"
            receipt = self.tpl.blank_copy(self.bundled, clear_map, blank,
                                          context=ctx)
            self.assertEqual(_sha(blank), SPECIALTY_BLANK)
            self.assertEqual(receipt["output"]["status"], "partial")
            self.assertEqual(receipt["unclassifiedTextCells"],
                             SPECIALTY_BLANK_UNCLASSIFIED_TEXT)
            self.assertEqual(receipt["workbook_profile"],
                             "school_17_sheet_v1")

            patched = Path(temp) / "patched.xlsx"
            receipt = self.patch.patch_copy(self.bundled, patch_map, patched,
                                            context=ctx)
            self.assertEqual(_sha(patched), SPECIALTY_PATCH)
            self.assertEqual(receipt["formulaCachesInvalidated"],
                             SPECIALTY_PATCH_CACHES)

            # An explicit 17-sheet choice is still this major's own profile.
            again = Path(temp) / "patched-explicit.xlsx"
            self.patch.patch_copy(self.bundled, patch_map, again, context=ctx,
                                  profile="school_17_sheet_v1")
            self.assertEqual(_sha(again), SPECIALTY_PATCH)

    def test_peers_without_the_17_sheet_form_stay_closed_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            for major in HELD_MAJORS:
                ctx = self._context(major)
                for name, call in (
                        ("create", lambda out: self.tpl.create_workbook(
                            None, out, context=ctx, template_ref="X01",
                            reference_inventory={"files": [], "surveys": []})),
                        ("blank", lambda out: self.tpl.blank_copy(
                            self.bundled, clear_map, out, context=ctx)),
                        ("patch", lambda out: self.patch.patch_copy(
                            self.bundled, patch_map, out, context=ctx)),
                        ("patch-explicit", lambda out: self.patch.patch_copy(
                            self.bundled, patch_map, out, context=ctx,
                            profile="school_17_sheet_v1"))):
                    out = Path(temp) / ("%s-%s.xlsx" % (major, name))
                    with self.subTest(major=major, call=name):
                        with self.assertRaises(self.mc.OutputHeldError) as caught:
                            call(out)
                        self.assertEqual(caught.exception.reason,
                                         "unsupported_finance_profile")
                        self.assertFalse(out.exists())

    def test_dairy_refuses_the_17_sheet_form_as_a_different_workbook(self):
        """Dairy's school form is 28 sheets, so the bundled 17-sheet file is
        refused on structure before anything is written — never copied as if
        it were the dairy form, and never silently cleared with its map.

        This is the dairy counterpart of the food-crops case below: the
        profile is now registered, so the refusal is the structural one and
        the source is read to prove it, instead of the old policy-B hold that
        only had to know the profile was unregistered.
        """
        ctx = self._context("dairy")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            for label, extra in (("default", {}),
                                 ("own-profile",
                                  {"profile": "dairy_hanwoo_28_sheet_v1"})):
                patched = Path(temp) / ("dairy-patch-%s.xlsx" % label)
                with self.subTest(call=label):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, patched,
                                              context=ctx, **extra)
                    self.assertEqual(held.exception.reason,
                                     "workbook_structure_mismatch")
                    self.assertIn("28", str(held.exception))
                    self.assertFalse(patched.exists())

            # A 17-sheet label is another major's form, not a narrower choice.
            for label in ("school_17_sheet_v1", "food_crops_16_sheet_v1"):
                refused = Path(temp) / ("dairy-named-%s.xlsx" % label)
                with self.subTest(call=label):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, refused,
                                              context=ctx, profile=label)
                    self.assertEqual(held.exception.reason,
                                     "unsupported_finance_profile")
                    self.assertFalse(refused.exists())

            blank = Path(temp) / "dairy-blank.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as held:
                self.tpl.blank_copy(self.bundled, clear_map, blank, context=ctx)
            self.assertEqual(held.exception.reason,
                             "unsupported_finance_profile")
            self.assertFalse(blank.exists())

    def test_the_insect_major_has_no_school_workbook_output_at_all(self):
        ctx = self._context("industrial_insects")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            out = Path(temp) / "insects.xlsx"
            for call in (
                    lambda: self.tpl.create_workbook(
                        None, out, context=ctx, template_ref="X01",
                        reference_inventory={"files": [], "surveys": []}),
                    lambda: self.tpl.blank_copy(self.bundled, clear_map, out,
                                                context=ctx),
                    lambda: self.patch.patch_copy(self.bundled, patch_map, out,
                                                  context=ctx)):
                with self.assertRaises(self.mc.OutputHeldError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, "unsupported_output")
                self.assertFalse(out.exists())

    def test_food_crops_cannot_reuse_the_17_sheet_map(self):
        """The new profile is a different form, so the old map is refused
        rather than clearing the wrong cells — both ways in."""
        ctx = self._context("food_crops")
        with tempfile.TemporaryDirectory() as temp:
            clear_map, patch_map = self._fixtures(temp)
            blank = Path(temp) / "food-blank.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                self.tpl.blank_copy(self.bundled, clear_map, blank, context=ctx)
            self.assertEqual(caught.exception.reason,
                             "unsupported_finance_profile")
            self.assertFalse(blank.exists())

            # The public patch entry checks the source's real structure, not
            # just its own profile label: the 17-sheet bundle is not the
            # registered 16-sheet school form, with or without the name.
            for label, extra in (("default", {}),
                                 ("named", {"profile": "food_crops_16_sheet_v1"})):
                patched = Path(temp) / ("food-patch-%s.xlsx" % label)
                with self.subTest(call=label):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, patched,
                                              context=ctx, **extra)
                    self.assertEqual(held.exception.reason,
                                     "workbook_structure_mismatch")
                    self.assertFalse(patched.exists())

            # Nothing but this major's own default profile is accepted: a
            # named profile never reopens another major's patch route.
            for major, named in (("fruit_trees", "fruit_16_sheet_v1"),
                                 ("specialty_crops", "food_crops_16_sheet_v1"),
                                 ("food_crops", "school_17_sheet_v1")):
                fresh = self.make_project()
                bind_major(fresh, major)
                named_ctx = self.mc.output_context(fresh, major)
                refused = Path(temp) / ("named-%s.xlsx" % major)
                with self.subTest(major=major, profile=named):
                    with self.assertRaises(self.mc.OutputHeldError) as held:
                        self.patch.patch_copy(self.bundled, patch_map, refused,
                                             context=named_ctx, profile=named)
                    self.assertEqual(held.exception.reason,
                                     "unsupported_finance_profile")
                    self.assertFalse(refused.exists())
