"""Cultivation-method equipment, grade and channel price fit checks.

Synthetic workbooks reproduce only the labels of the school 17-sheet layout
that ``gg_plan_fit`` reads. All names and numbers are synthetic.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from openpyxl import Workbook

from tests._harness import SCRIPTS, ContractCase, runtime
from tests.test_skill_school_verify import _source, base_spec

GRADE_ROWS = ("상품", "중품", "하품")


def plan_book(path, *, investments=(("비닐하우스", 50000), ("고설베드", 20000)),
              opening=(), depreciation=(("비닐하우스", 50000),),
              tasks=(("정식·베드관리", 5), ("수확 및 선별", 30)),
              prices=None, history=None, shares=None, notes=(),
              sga=(0, 0, 0, 0, 0), formula_cells=()):
    """Build a minimal school-layout workbook and save it to ``path``."""
    prices = prices or {("purchase", "상품"): 3000, ("direct", "상품"): 6000}
    shares = shares or {("purchase", "상품"): 0.7, ("direct", "상품"): 0.3}
    history = history or {}
    wb = Workbook()
    base = wb.active
    base.title = "1. 기초재무상태조사"
    base["B6"] = "(1) 토지"
    base["B8"], base["E8"], base["H8"] = "종류", "평가액(천원)", "비고"
    for i, (name, amount, note) in enumerate(opening):
        base.cell(9 + i, 2, name)
        base.cell(9 + i, 5, amount)
        base.cell(9 + i, 8, note)
    base["B12"] = "(2) 건물및구축물"

    inv = wb.create_sheet("3.투자계획")
    inv["C18"], inv["E18"] = "투자항목", "계"
    for i, (name, amount) in enumerate(investments):
        inv.cell(19 + i, 3, name)
        inv.cell(19 + i, 5, amount)
    inv.cell(19 + len(investments), 3, "계")

    sales = wb.create_sheet(" 5. 판매계획")
    hist_cols = (5, 7, 9, 11, 13)
    for channel, row, label in (("purchase", 7, "평균가격(수매)"),
                                ("direct", 11, "평균가격(직거래)")):
        for c, year in zip(hist_cols, range(2021, 2026)):
            sales.cell(row - 1, c, year)
        sales.cell(row - 1, 15, "평균가격")
        sales.cell(row, 2, label)
        for offset, grade in enumerate(GRADE_ROWS):
            sales.cell(row + offset, 3, grade)
            price = prices.get((channel, grade))
            if price is not None:
                sales.cell(row + offset, 15, price)
            for c, value in zip(hist_cols, history.get((channel, grade), ())):
                sales.cell(row + offset, c, value)
    for i, text in enumerate(notes):
        sales.cell(14 + i, 2, text)
    for c in range(4, 14, 2):
        sales.cell(20, c, "판매비율")
        sales.cell(20, c + 1, "판매량")
    for channel, row, label in (("purchase", 22, "수매시장"),
                                ("direct", 26, "직거래")):
        sales.cell(row, 2, label)
        for offset, grade in enumerate(GRADE_ROWS):
            sales.cell(row + offset, 3, grade)
            for c in range(4, 14, 2):
                sales.cell(row + offset, c, shares.get((channel, grade), 0))

    labor = wb.create_sheet("8. 노무비계획")
    labor["B24"], labor["I24"] = "항목", "작업일수"
    for i, (name, days) in enumerate(tasks):
        labor.cell(26 + i, 2, name)
        labor.cell(26 + i, 9, days)
    labor.cell(26 + len(tasks), 2, "합계")

    dep = wb.create_sheet("10. 감가상각비계획 ")
    dep["C6"] = "영농시설"
    dep["B8"] = "취  득  가  격"
    for i, (name, cost) in enumerate(depreciation):
        dep.cell(7, 3 + i, name)
        dep.cell(8, 3 + i, cost)

    pl = wb.create_sheet("12 손익계획")
    pl["B11"], pl["B12"], pl["B13"] = "IV. 판매비와 관리비", " 1. 포장비", " 2. 운반비"
    for c, value in enumerate(sga, start=3):
        pl.cell(13, c, value)
    pl["B15"] = "V. 영업이익"

    for sheet, cell, formula in formula_cells:
        wb[sheet][cell] = formula
    wb.save(path)
    return path


class PlanFitTests(ContractCase):
    def setUp(self):
        self.pf = runtime("gg_plan_fit")
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-plan-fit-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.count = 0

    GOOD_NOTES = (
        "* 수매 단가: 합성 기관 농가수취가 5개년 평균. 등급 구분 없는 자료라 상품 단일 등급, 중·하품 0은 계획 가정.",
        "* 직거래 단가: 합성 기관 소매가 5개년 평균. 택배비는 판매관리비 운반비로 계상.",
    )

    def review(self, method="protected_soilless", allow=None, **book):
        book.setdefault("notes", self.GOOD_NOTES)
        book.setdefault("sga", (100, 200, 300, 300, 300))
        self.count += 1
        path = plan_book(self.dir / ("plan-%d.xlsx" % self.count), **book)
        return self.pf.review_fit(path, method=method, allow=allow)

    @staticmethod
    def codes(result, severity=None):
        return [i["code"] for i in result["issues"]
                if severity is None or i["severity"] == severity]

    def test_plan_matching_its_method_passes(self):
        result = self.review()
        self.assertEqual("pass", result["status"], result["issues"])
        self.assertEqual([], self.codes(result))

    def test_zero_amount_equipment_row_is_blocked_in_every_sheet(self):
        result = self.review(
            investments=(("비닐하우스", 50000), ("관리기", 0)),
            depreciation=(("비닐하우스", 50000), ("트랙터(미보유)", 0)))
        unowned = [i["evidence"]["cell"] for i in result["issues"]
                   if i["code"] == "UNOWNED_ROW"]
        self.assertEqual(["C20", "D7"], unowned)
        self.assertEqual("fail", result["status"])

    def test_tillage_machine_depends_on_cultivation_method(self):
        rows = (("비닐하우스", 50000), ("소형 관리기", 3000))
        soilless = self.review(investments=rows)
        self.assertIn("EQUIPMENT_METHOD_MISMATCH", self.codes(soilless, "error"))
        soil = self.review(method="시설토경", investments=rows,
                           tasks=(("경운", 4), ("수확", 30)))
        self.assertEqual("pass", soil["status"], soil["issues"])
        self.assertEqual([], self.codes(soil))

    def test_open_field_rejects_hydroponic_equipment(self):
        result = self.review(method="노지",
                             investments=(("양액공급기", 8000),),
                             depreciation=(("양액공급기", 8000),))
        self.assertIn("EQUIPMENT_METHOD_MISMATCH", self.codes(result, "error"))

    def test_student_confirmed_exception_downgrades_to_warning(self):
        result = self.review(
            investments=(("비닐하우스", 50000), ("소형 관리기", 3000)),
            allow={"소형 관리기": "하우스 바닥 정리에 사용(학생 확인)"})
        self.assertEqual(["EQUIPMENT_METHOD_EXCEPTION"], self.codes(result))
        self.assertEqual("pass", result["status"])

    def test_tillage_task_with_days_fails_and_empty_label_warns(self):
        worked = self.review(tasks=(("경운", 6), ("수확", 30)))
        self.assertIn("TASK_METHOD_MISMATCH", self.codes(worked, "error"))
        label = self.review(tasks=(("경운", 0), ("수확", 30)))
        self.assertEqual(["TASK_METHOD_LABEL"], self.codes(label))
        self.assertEqual("pass", label["status"])

    def test_opening_zero_row_is_a_warning_with_its_note(self):
        result = self.review(opening=(("전", 0, "첫해 매입 예정"),))
        issue = next(i for i in result["issues"] if i["code"] == "OPENING_ZERO_ROW")
        self.assertEqual("warning", issue["severity"])
        self.assertEqual("첫해매입예정", issue["evidence"]["note"])

    def test_unsold_grade_must_not_carry_a_copied_price(self):
        copied = self.review(prices={("purchase", "상품"): 3000, ("purchase", "중품"): 3000,
                                     ("direct", "상품"): 6000})
        cells = [i["evidence"]["cell"] for i in copied["issues"]
                 if i["code"] == "SALES_UNUSED_PRICE"]
        self.assertEqual(["O8"], cells)
        history_only = self.review(history={("direct", "하품"): (1, 2, 3, 4, 5)})
        self.assertIn("SALES_UNUSED_PRICE", self.codes(history_only))
        zero = self.review(prices={("purchase", "상품"): 3000, ("purchase", "중품"): 0,
                                   ("direct", "상품"): 6000})
        self.assertNotIn("SALES_UNUSED_PRICE", self.codes(zero))

    def test_sold_grades_need_distinct_descending_prices(self):
        shares = {("purchase", "상품"): 0.5, ("purchase", "중품"): 0.2,
                  ("direct", "상품"): 0.3}
        equal = self.review(shares=shares, prices={
            ("purchase", "상품"): 3000, ("purchase", "중품"): 3000.02,
            ("direct", "상품"): 6000})
        self.assertIn("GRADE_PRICE_EQUAL", self.codes(equal, "error"))
        reversed_ = self.review(shares=shares, prices={
            ("purchase", "상품"): 2000, ("purchase", "중품"): 2500,
            ("direct", "상품"): 6000})
        self.assertIn("GRADE_PRICE_ORDER", self.codes(reversed_, "error"))
        ordered = self.review(shares=shares, prices={
            ("purchase", "상품"): 3000, ("purchase", "중품"): 2000,
            ("direct", "상품"): 6000})
        self.assertEqual("pass", ordered["status"], ordered["issues"])

    def test_sold_grade_without_price_fails(self):
        result = self.review(prices={("purchase", "상품"): 3000})
        self.assertIn("SALES_PRICE_MISSING", self.codes(result, "error"))

    def test_two_channels_with_the_same_price_fail(self):
        result = self.review(prices={("purchase", "상품"): 3000, ("direct", "상품"): 3000})
        self.assertIn("CHANNEL_PRICE_EQUAL", self.codes(result, "error"))

    def test_single_channel_plan_is_not_compared_across_channels(self):
        result = self.review(shares={("purchase", "상품"): 1.0},
                             prices={("purchase", "상품"): 3000})
        self.assertEqual("pass", result["status"], result["issues"])

    def test_channel_cost_needs_selling_expense_or_a_note(self):
        bare = self.review(notes=bare_notes(), sga=(0, 0, 0, 0, 0))
        self.assertEqual(["CHANNEL_COST_UNREFLECTED"] * 2, self.codes(bare, "error"))
        expensed = self.review(notes=bare_notes(), sga=(0, 50, 50, 50, 50))
        self.assertNotIn("CHANNEL_COST_UNREFLECTED", self.codes(expensed))
        noted = self.review(sga=(0, 0, 0, 0, 0))
        self.assertNotIn("CHANNEL_COST_UNREFLECTED", self.codes(noted))

    def test_missing_basis_notes_warn_without_blocking(self):
        result = self.review(notes=())
        self.assertEqual("pass", result["status"])
        self.assertEqual({"GRADE_BASIS_NOTE_MISSING", "CHANNEL_SOURCE_NOTE_MISSING"},
                         set(self.codes(result, "warning")))

    def test_uncached_price_formula_blocks_instead_of_guessing(self):
        result = self.review(formula_cells=((" 5. 판매계획", "O8", "=O7"),))
        self.assertEqual("blocked", result["status"])
        self.assertIn("PLAN_FIT_UNCACHED", self.codes(result))
        self.assertNotIn("SALES_UNUSED_PRICE", self.codes(result))

    def test_without_method_equipment_is_not_judged(self):
        result = self.review(method=None,
                             investments=(("비닐하우스", 50000), ("관리기", 4000)))
        self.assertEqual(["CULTIVATION_METHOD_UNDECLARED"], self.codes(result))

    def test_method_aliases_resolve_and_unknown_is_refused(self):
        self.assertEqual("protected_soilless", self.pf.resolve_method("고설 수경"))
        self.assertEqual("open_field", self.pf.resolve_method("open_field"))
        self.assertEqual("mushroom", self.pf.resolve_method("균상재배"))
        with self.assertRaises(ValueError):
            self.pf.resolve_method("공중재배")

    def test_cli_exit_codes(self):
        good = plan_book(self.dir / "cli-good.xlsx", notes=self.GOOD_NOTES,
                         sga=(1, 1, 1, 1, 1))
        bad = plan_book(self.dir / "cli-bad.xlsx", notes=self.GOOD_NOTES,
                        sga=(1, 1, 1, 1, 1),
                        prices={("purchase", "상품"): 3000, ("direct", "상품"): 3000})
        script = str(SCRIPTS / "gg_plan_fit.py")

        def run(*args):
            proc = subprocess.run([sys.executable, script, *args],
                                  capture_output=True, text=True, encoding="utf-8")
            return proc.returncode, json.loads(proc.stdout)

        self.assertEqual(0, run(str(good), "--method", "고설수경")[0])
        code, out = run(str(bad), "--method", "고설수경")
        self.assertEqual((1, "fail"), (code, out["status"]))
        self.assertEqual(2, run(str(good), "--method", "공중재배")[0])
        self.assertEqual(2, run(str(good), "--allow", "이유없음")[0])


def bare_notes():
    return ("* 등급: 상품 단일 등급 가정, 수매 도매 경락가와 직거래 소매가 평균.",)


class LegacyChannelPriceTests(ContractCase):
    """The legacy generator path refuses one channel's mean copied to both."""

    def setUp(self):
        self.sv = runtime("gg_school_verify")
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-plan-fit-legacy-")
        self.addCleanup(tmp.cleanup)
        self.book = Path(tmp.name) / "any.xlsx"
        Workbook().save(self.book)

    def reasons(self, spec):
        return [i["reason"] for i in self.sv.verify_school_recalculated(spec, self.book)]

    def same_price_spec(self, **over):
        values = [6, 8, 10, 8, 8]
        return base_spec(purchase_price="8", direct_price="8", price_sources=[
            _source("student.purchase", "수매", values),
            _source("student.direct", "직거래", values)], **over)

    def test_equal_channel_prices_fail(self):
        self.assertTrue(any("수매·직거래 단가 동일" in r
                            for r in self.reasons(self.same_price_spec())))

    def test_distinct_channel_prices_pass_this_rule(self):
        self.assertFalse(any("단가 동일" in r for r in self.reasons(base_spec())))

    def test_single_active_channel_is_not_compared(self):
        spec = self.same_price_spec(purchase_share="1", direct_share="0")
        self.assertFalse(any("단가 동일" in r for r in self.reasons(spec)))
