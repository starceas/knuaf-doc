"""Specialty-crop template route: crop-category labels, daily-labor gender
warning and the professor-template print layout of sheet 7.

All workbooks are synthetic stand-ins carrying only the cells a contract
reads; no professor or student file is opened.  Hashes of the registered
template are simulated where the contract binds to them.
"""
import json
import tempfile
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

S5, S6, S7, S8 = " 5. 판매계획", "6. 생산계획", "7. 영농자재소요계획", "8. 노무비계획"
X01_SHA = "5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80"


def labelled_template(path):
    """Stand-in with the X01 crop-bound labels, a formula and numbers."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "3.투자계획"
    wb.active["C24"] = "계"
    wb.create_sheet("10. 감가상각비계획 ")
    sales, prod, mat, labor = (wb.create_sheet(n) for n in (S5, S6, S7, S8))
    sales["B30"] = "미상품과"
    sales["D30"] = "=100%-D25-D29"
    prod["B17"], prod["E25"], prod["E27"] = "묘목수", "묘목수(1년차)", "묘목수(2년차 이후)"
    prod["H8"] = 1000
    mat["C4"], mat["C5"] = "묘목비(원/10a)", 5000
    labor["O24"] = labor["O38"] = "고용(남자, 단가 동일)"
    wb.save(path)


def labor_book(path, *, male, female, task_male=(), task_female=()):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = S8
    for cells, days in ((("G11", "J11", "M11", "P11", "S11"), male),
                        (("G12", "J12", "M12", "P12", "S12"), female)):
        for cell, value in zip(cells, days):
            ws[cell] = value
    for row, (task, m) in enumerate(task_male, 26):
        ws[f"B{row}"], ws[f"O{row}"] = task, m
    for row, (task, f) in enumerate(task_female, 26):
        ws[f"B{row}"], ws[f"R{row}"] = task, f
    wb.save(path)


class CropLabelTests(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tpl = runtime("gg_excel_template")
        self.fill = runtime("gg_excel_fill")
        self.src = self.root / "src.xlsx"
        labelled_template(self.src)
        real = self.tpl.sha256
        patcher = mock.patch.object(
            self.tpl, "sha256",
            side_effect=lambda p: X01_SHA if Path(p) == self.src else real(p))
        patcher.start()
        self.addCleanup(patcher.stop)

    def relabel(self, category, choices=None, name="out.xlsx"):
        project = self.make_project()
        bind_major(project)
        ctx = runtime("gg_major_contract").output_context(project, "specialty_crops")
        fill_map, values = self.tpl.crop_label_map(self.src, category, choices)
        m, v = self.root / (name + ".m.json"), self.root / (name + ".v.json")
        m.write_text(json.dumps(fill_map, ensure_ascii=False), encoding="utf-8")
        v.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        out = self.root / name
        with mock.patch.object(self.fill, "sha256",
                               side_effect=lambda p: X01_SHA if Path(p) == self.src
                               else self.tpl.sha256.side_effect(p)):
            self.fill.fill_copy(self.src, m, v, out, context=ctx)
        return out

    def test_herbaceous_labels_replace_tree_terms_and_keep_numbers(self):
        out = self.relabel("herbaceous", {f"{S6}!B17": "재배시설"})
        from openpyxl import load_workbook
        wb = load_workbook(out)
        self.assertEqual(wb[S5]["B30"].value, "미상품")
        self.assertEqual(wb[S6]["B17"].value, "재배시설")
        self.assertEqual(wb[S6]["E25"].value, "정식 주수(1년차)")
        self.assertEqual(wb[S7]["C4"].value, "종자·육묘비(원/10a)")
        self.assertEqual(wb[S8]["O24"].value, "고용(남자)")
        self.assertEqual(wb[S5]["D30"].value, "=100%-D25-D29")
        wb.close()
        result = self.tpl.crop_label_check(self.src, out)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["labelsChanged"]), 7)

    def test_woody_perennial_keeps_tree_terms(self):
        _, values = self.tpl.crop_label_map(self.src, "woody_perennial")
        changed = {(v["sheet"], v["cell"]): v["value"] for v in values["values"]}
        # Only the male-hire header loses its misleading "단가 동일" note.
        self.assertEqual(changed, {(S8, "O24"): "고용(남자)", (S8, "O38"): "고용(남자)"})

    def test_unlisted_label_category_or_moved_label_refused(self):
        with self.assertRaisesRegex(ValueError, "not a listed"):
            self.tpl.crop_label_map(self.src, "herbaceous", {f"{S5}!B30": "불량품"})
        with self.assertRaisesRegex(ValueError, "unknown crop category"):
            self.tpl.crop_label_map(self.src, "orchard")
        with self.assertRaisesRegex(ValueError, "outside the label table"):
            self.tpl.crop_label_map(self.src, "herbaceous", {f"{S5}!B31": "미상품"})
        from openpyxl import load_workbook
        wb = load_workbook(self.src)
        wb[S6]["B17"] = "주수"
        wb.save(self.src)
        wb.close()
        with self.assertRaisesRegex(ValueError, "expected original label"):
            self.tpl.crop_label_map(self.src, "herbaceous")

    def test_unregistered_template_refused(self):
        other = self.root / "other.xlsx"
        labelled_template(other)
        with self.assertRaisesRegex(ValueError, "registered X01/X02"):
            self.tpl.crop_label_map(other, "herbaceous")

    def test_check_fails_on_number_formula_or_unlisted_text_change(self):
        out = self.relabel("herbaceous")
        from openpyxl import load_workbook
        for label, edit in (
                ("number", lambda wb: wb[S6].__setitem__("H8", 999)),
                ("formula", lambda wb: wb[S5].__setitem__("D30", "=1-D25")),
                ("other_text", lambda wb: wb[S5].__setitem__("B31", "합계")),
                ("free_label", lambda wb: wb[S5].__setitem__("B30", "불량품"))):
            with self.subTest(change=label):
                bad = self.root / f"bad-{label}.xlsx"
                wb = load_workbook(out)
                edit(wb)
                wb.save(bad)
                wb.close()
                result = self.tpl.crop_label_check(self.src, bad)
                self.assertEqual(result["status"], "fail")
                self.assertTrue(result["violations"])


class LaborReviewTests(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tpl = runtime("gg_excel_template")

    def review(self, **kw):
        path = self.root / "labor.xlsx"
        labor_book(path, **kw)
        return self.tpl.labor_review(path)

    def test_male_only_hired_days_warn_with_task_list(self):
        issues = self.review(male=(0, 60, 60, 60, 60), female=(0, 0, 0, 0, 0),
                             task_male=[("수확 및 선별", 60)])
        self.assertEqual([i["code"] for i in issues], ["LABOR_HIRED_MALE_ONLY"])
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertEqual(issues[0]["evidence"]["tasks"][0]["task"], "수확 및 선별")

    def test_task_rows_alone_trigger_warning(self):
        issues = self.review(male=(0,) * 5, female=(0,) * 5,
                             task_male=[("정식", 12)])
        self.assertEqual([i["code"] for i in issues], ["LABOR_HIRED_MALE_ONLY"])

    def test_female_or_split_or_no_hire_is_silent(self):
        for label, kw in (
                ("female", dict(male=(0,) * 5, female=(0, 60, 60, 60, 60),
                                task_female=[("수확", 60)])),
                ("split", dict(male=(10,) * 5, female=(50,) * 5,
                               task_male=[("정식", 10)], task_female=[("수확", 50)])),
                ("none", dict(male=(0,) * 5, female=(0,) * 5))):
            with self.subTest(case=label):
                self.assertEqual(self.review(**kw), [])

    def test_review_plan_surfaces_warning(self):
        from tests.test_skillgaps_wp3_finance import plan_workbook
        from openpyxl import load_workbook
        path = self.root / "plan.xlsx"
        plan_workbook(path)
        before = [i["code"] for i in runtime("gg_finance_tables").review_plan(path)["issues"]]
        self.assertNotIn("LABOR_HIRED_MALE_ONLY", before)
        wb = load_workbook(path)
        ws = wb.create_sheet(S8)
        for cell in ("J11", "M11", "P11", "S11"):
            ws[cell] = 60
        wb.save(path)
        wb.close()
        plan = runtime("gg_finance_tables").review_plan(path)
        item = next(i for i in plan["issues"] if i["code"] == "LABOR_HIRED_MALE_ONLY")
        self.assertEqual(item["check_id"], "finance_plan_review")
        self.assertEqual(item["severity"], "warning")


class MaterialSheetPrintTests(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pr = runtime("gg_excel_print")
        self.tpl = runtime("gg_excel_template")
        self.mc = runtime("gg_major_contract")

    def source(self, *, x02=False):
        from openpyxl import Workbook
        wb = Workbook()
        inv = wb.active
        inv.title = "3.투자계획"
        inv["C25" if x02 else "C24"] = "계"
        dep = wb.create_sheet("10. 감가상각비계획 ")
        if x02:
            dep["J7"] = "대식물"
        for name in (S7, "12 손익계획"):
            wb.create_sheet(name)["B2"] = name
        path = self.root / ("x02.xlsx" if x02 else "x01.xlsx")
        wb.save(path)
        return path

    def apply(self, src, sheet, major="specialty_crops", name="out"):
        project = self.make_project()
        bind_major(project, major)
        ctx = self.mc.output_context(project, major)
        m = self.root / f"{name}.map.json"
        m.write_text(json.dumps({"schema": self.pr.SCHEMA,
                                 "source": {"sha256": self.tpl.sha256(src)},
                                 "sheets": [{"sheet": sheet, "fit_width": 1,
                                             "fit_height": 0, "reason": "합성"}]},
                                ensure_ascii=False), encoding="utf-8")
        out = self.root / f"{name}.xlsx"
        return self.pr.apply(src, m, out, self.root / f"{name}.json", context=ctx), out

    def test_x01_material_sheet_layout_cannot_change(self):
        src = self.source()
        with self.assertRaisesRegex(ValueError, "영농자재소요계획.*X01"):
            self.apply(src, S7)
        self.assertFalse((self.root / "out.xlsx").exists())

    def test_other_sheet_and_other_layout_unchanged(self):
        receipt, _ = self.apply(self.source(), "12 손익계획", name="pl")
        self.assertEqual(receipt["changes"][0]["sheet"], "12 손익계획")
        receipt, _ = self.apply(self.source(x02=True), S7, name="x02out")
        self.assertEqual(receipt["changes"][0]["sheet"], S7)
