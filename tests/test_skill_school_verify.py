"""gg_school_verify: the independent recalculation behind the submission gate.

Migrated from the GUI contract suite (cb72ae1), with the input contract of
this tree: three separated growth rates live in ``price_assumptions`` and the
retired single ``inflation`` multiplier is refused.  These tests assert the
accounting identities the module claims, not one frozen output.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tests._harness import ContractCase, runtime

D = Decimal
HISTORY_YEARS = [2021, 2022, 2023, 2024, 2025]


def _source(sid, channel, values):
    return {
        "id": sid, "agency": "합성 공공기관(테스트)",
        "agency_kind": "public_corporation",
        "statistic_name": "합성 단가", "item_name": "합성작목 단가",
        "unit": "원/kg", "base_year": None,
        "observations": [{"year": y, "value": v}
                         for y, v in zip(HISTORY_YEARS, values)],
        "url": "https://synthetic.example.go.kr/price/test",
        "evidence_locator": "합성 위치",
        "dimensions": {"region": "전국", "crop": "합성작목",
                       "channel": channel, "grade": "상품"},
        "allowed_roles": ["sales"], "unconfirmed_years": [],
        "retrieved_at": "2026-10-07", "revision": 1,
    }


def base_spec(**over):
    spec = dict(
        profile="school_17_sheet_v1", writing_year=2026, start_year=2027,
        source_refs=["synthetic:합성-원답변"], unit="천원", quantity_unit="kg",
        production_evidence={k: "합성 근거" for k in
                             ("area", "seedlings", "yield", "growth",
                              "commodity")},
        crops=["합성작목"], region="전국", price_dimensions={"unit": "원/kg"},
        area_m2="2000", production_kg="5000",
        purchase_price="8", direct_price="12",
        purchase_share="0.6", direct_share="0.4",
        land="0", facility="60000", equipment="20000",
        equity="40000", loan="40000", loan_rate="0.02", salvage="6000",
        materials="5000", labor="4000", packing="1000", transport="800",
        household="12000", repair_facility_rate="0.01",
        repair_equipment_rate="0.02", utility_per_10a="300",
        grace=2, term=5, life=10,
        price_assumptions={
            "general": {"source_id": "official.kosis.cpi.total",
                        "base_year": 2020, "application_base_year": 2027},
            "wage": {"source_id": "official.kosis.farm_purchase.labor",
                     "base_year": 2020, "application_base_year": 2027},
            "sales": {
                "application_base_year": 2027,
                "anchors": {
                    "purchase": {"source_id": "student.purchase",
                                 "base_year": None,
                                 "target_dimensions": {"channel": "수매",
                                                       "grade": "상품"}},
                    "direct": {"source_id": "student.direct",
                               "base_year": None,
                               "target_dimensions": {"channel": "직거래",
                                                     "grade": "상품"}},
                },
            },
        },
        price_sources=[
            _source("student.purchase", "수매", [6, 8, 10, 8, 8]),
            _source("student.direct", "직거래", [10, 12, 14, 12, 12]),
        ],
    )
    spec.update(over)
    return spec


class SchoolVerifyTests(ContractCase):
    def setUp(self):
        self.sv = runtime("gg_school_verify")

    def plan(self, **over):
        return self.sv.calculate_school_expected(
            base_spec(**over))["expected_years"]

    # --- the identities the module exists to check ------------------------

    def test_the_balance_sheet_balances_every_year(self):
        for i, y in enumerate(self.plan(), 1):
            self.assertEqual(y["total_assets"], y["total_liab_eq"], i)

    def test_cash_rolls_forward_from_one_year_to_the_next(self):
        years = self.plan()
        for i in range(1, len(years)):
            self.assertEqual(years[i]["cash"],
                             years[i - 1]["cash"] + years[i]["net_cf"], i + 1)

    # grace/term combinations worth separating: the default never exhausts
    # the loan, and a term that does not divide evenly leaves a residue the
    # last payment must be capped to.
    SCHEDULES = (
        {"grace": 2, "term": 5},
        {"grace": 0, "term": 3},
        {"grace": 0, "term": 7},
        {"grace": 5, "term": 5},
    )

    def test_repayment_is_never_negative(self):
        for schedule in self.SCHEDULES:
            with self.subTest(**schedule):
                self.assertTrue(all(y["principal"] >= 0
                                    for y in self.plan(**schedule)))

    def test_the_loan_balance_only_falls_and_never_goes_negative(self):
        for schedule in self.SCHEDULES:
            with self.subTest(**schedule):
                balances = [y["ending_loan"]
                            for y in self.plan(**schedule)]
                self.assertEqual(sorted(balances, reverse=True), balances)
                self.assertTrue(all(b >= 0 for b in balances))

    def test_total_repaid_never_exceeds_the_loan(self):
        for schedule in self.SCHEDULES:
            with self.subTest(**schedule):
                years = self.plan(**schedule)
                self.assertLessEqual(
                    sum(y["principal"] for y in years), D("40000"))

    def test_the_grace_period_is_honoured_then_repayment_starts(self):
        years = self.plan(grace=2, term=5)
        self.assertEqual([0, 0], [y["principal"] for y in years[:2]])
        self.assertTrue(all(y["principal"] > 0 for y in years[2:]))

    def test_a_grace_period_covering_the_window_repays_nothing(self):
        self.assertTrue(all(y["principal"] == 0
                            for y in self.plan(grace=5, term=5)))

    def test_interest_follows_the_outstanding_balance_down(self):
        years = self.plan()
        interests = [y["interest"] for y in years if y["principal"] > 0]
        self.assertEqual(sorted(interests, reverse=True), interests)

    # --- depreciation stops where the school form says it stops -----------

    def test_facility_book_value_floors_at_salvage_and_never_below(self):
        books = [y["fac_book"] for y in self.plan(life=2)]
        self.assertEqual(D("6000"), min(books))
        self.assertTrue(all(b >= D("6000") for b in books))

    def test_equipment_book_value_floors_at_zero(self):
        years = self.plan(life=2)
        self.assertEqual(0, min(y["eq_book"] for y in years))
        self.assertTrue(all(y["eq_book"] >= 0 for y in years))

    def test_book_values_only_fall(self):
        for key in ("fac_book", "eq_book"):
            with self.subTest(key=key):
                values = [y[key] for y in self.plan()]
                self.assertEqual(sorted(values, reverse=True), values)

    # --- revenue is derived, not asserted ---------------------------------

    def test_revenue_is_the_sum_of_the_two_sales_channels(self):
        for y in self.plan():
            self.assertEqual(y["revenue"],
                             y["purchase_rev"] + y["direct_rev"])

    def test_selling_nothing_produces_no_revenue(self):
        for y in self.plan(production_kg="0"):
            self.assertEqual(0, y["revenue"])

    # --- a stated price must match the evidence behind it -----------------

    def test_a_stated_price_that_contradicts_its_own_history_is_refused(self):
        spec = base_spec()
        spec["price_sources"][0] = _source(
            "student.purchase", "수매", [1, 1, 1, 1, 1])
        with self.assertRaisesRegex(ValueError, "purchase_price.*불일치"):
            self.sv.calculate_school_expected(spec)

    def test_a_direct_price_that_contradicts_its_own_history_is_refused(self):
        spec = base_spec()
        spec["price_sources"][1] = _source(
            "student.direct", "직거래", [1, 1, 1, 1, 1])
        with self.assertRaisesRegex(ValueError, "direct_price.*불일치"):
            self.sv.calculate_school_expected(spec)

    def test_a_matching_history_becomes_the_effective_price(self):
        r = self.sv.calculate_school_expected(base_spec())
        self.assertEqual(D("8"), r["effective_purchase_price"])

    # --- the plan window --------------------------------------------------

    def test_the_plan_preserves_a_confirmed_start_year(self):
        spec = base_spec(start_year=2029)
        spec["price_assumptions"]["general"]["application_base_year"] = 2029
        spec["price_assumptions"]["wage"]["application_base_year"] = 2029
        spec["price_assumptions"]["sales"]["application_base_year"] = 2029
        result = self.sv.calculate_school_expected(spec)
        self.assertEqual([2029, 2030, 2031, 2032, 2033], result["years"])
        self.assertEqual(result["years"],
                         [year["year"] for year in result["expected_years"]])

    def test_the_plan_covers_the_five_years_after_the_writing_year(self):
        r = self.sv.calculate_school_expected(base_spec())
        self.assertEqual([2027, 2028, 2029, 2030, 2031], r["years"])
        self.assertEqual(r["years"], [y["year"] for y in r["expected_years"]])

    def test_the_retired_single_inflation_multiplier_is_refused(self):
        spec = base_spec()
        spec["inflation"] = "1.02"
        with self.assertRaisesRegex(ValueError, "폐기"):
            self.sv.calculate_school_expected(spec)

    # --- to_d: Excel junk must not become a number ------------------------

    def test_to_d_accepts_the_numeric_forms_a_sheet_really_holds(self):
        self.assertEqual(D("600"), self.sv.to_d(600))
        self.assertEqual(D("600.5"), self.sv.to_d(600.5))
        self.assertEqual(D("600.5"), self.sv.to_d("600.5"))
        self.assertEqual(D("600.5"), self.sv.to_d(D("600.5")))

    def test_to_d_refuses_anything_it_cannot_trust(self):
        for junk, why in (
            (None, "값 누락"), (True, "잘못된 타입"), (False, "잘못된 타입"),
            ("", "수치 아님"), ("#REF!", "수치 아님"),
            ("#DIV/0!", "수치 아님"), ("1,200", "수치 아님"),
            ("약 600", "수치 아님"), (float("inf"), "비유한"),
            (float("nan"), "비유한"), ([600], "잘못된 타입"),
        ):
            with self.subTest(junk=junk):
                with self.assertRaisesRegex(ValueError, why):
                    self.sv.to_d(junk, "C5")

    def test_to_d_names_the_cell_in_its_error(self):
        with self.assertRaisesRegex(ValueError, "C5"):
            self.sv.to_d(None, "C5")

    # --- the one place an #N/A is tolerated -------------------------------

    def test_an_unused_grade_cell_may_only_hold_the_exact_allowed_values(self):
        for allowed in (None, "", "[해당 없음: 상품만 판매]"):
            self.assertIs(
                True, self.sv.is_valid_unused_grade_cell(allowed, allowed))

    def test_a_near_miss_in_an_unused_grade_cell_is_not_tolerated(self):
        for value in ("해당 없음", " [해당 없음: 상품만 판매]",
                      "[해당 없음: 상품만 판매] ", "0", 0, "#N/A", "N/A", "없음"):
            with self.subTest(value=value):
                self.assertIs(False,
                              self.sv.is_valid_unused_grade_cell(value, None))
                self.assertIs(False,
                              self.sv.is_valid_unused_grade_cell(None, value))

    def test_error_tokens_are_the_ones_excel_actually_produces(self):
        for token in ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NULL!",
                      "#NUM!", "#N/A"):
            self.assertIn(token, self.sv.ERROR_TOKENS)


class RecalculatedCopyTests(ContractCase):
    """The gates that read a recalculated workbook end to end.

    No Excel here: a copy of a shipped form with its formulas replaced by
    plain numbers is what a recalculated workbook looks like to openpyxl.
    The cash-flow row numbers are read from the copy's own labels, so a row
    error in that path is invisible until something walks it — these tests
    walk it on both editions.
    """

    CASH_FLOW = "14. 현금흐름계획"
    BALANCE = "13. 추정대차대조표"
    # The link block's own wording: an earlier block also reports E23/E24, so
    # asserting the cell alone would not prove this block ran.
    LINK_REASON = "대차대조표 현금 잔액과 현금흐름 수지균형 롤포워드 불일치"
    # Reported only on the cash-flow net row, which is 22 in the first
    # edition and 23 in the second.
    NET_REASON = "현금수지균형(유입-유출) 산식 불일치"

    def setUp(self):
        self.sv = runtime("gg_school_verify")

    def synthetic(self, name, *, balance_rows=True):
        """A shipped form with numbers where its formulas were."""
        from openpyxl import load_workbook
        source = (Path(__file__).resolve().parents[1] / "skills" / "knuaf-doc"
                  / "assets" / "templates" / name)
        book = load_workbook(source)
        for sheet in book:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "f":
                        cell.value = 1000
        if balance_rows:
            # The balance-sheet block reads rows 6..16 before the cash link it
            # is checked by; a synthetic sheet must carry numbers there or the
            # block stops early and the link is never reached.
            sheet = book[self.BALANCE]
            for row in range(6, 17):
                for column in range(3, 8):
                    sheet.cell(row, column).value = 1000
        sheet = book["1. 기초재무상태조사"]
        sheet["C9"] = 1000
        holder = tempfile.TemporaryDirectory(prefix="knuaf-recalc-")
        self.addCleanup(holder.cleanup)
        out = Path(holder.name) / name
        book.save(out)
        book.close()
        return out

    def test_the_cash_link_is_walked_on_the_first_edition(self):
        issues = self.sv.verify_school_recalculated(
            base_spec(), self.synthetic("finance-template.xlsx"))
        self.assert_no_unhandled_name(issues)
        pairs = {(issue.get("cell"), issue.get("reason")) for issue in issues}
        # The cash-flow block's own verdict names the edition's net row...
        self.assertIn((self.CASH_FLOW + "!E22", self.NET_REASON), pairs, pairs)
        self.assertNotIn((self.CASH_FLOW + "!E23", self.NET_REASON), pairs, pairs)
        # ...and the balance-sheet link is a separate block with its own
        # wording; this is the assertion that it actually ran.
        self.assertIn((self.BALANCE + "!D6", self.LINK_REASON), pairs, pairs)

    def test_the_cash_link_is_walked_on_the_second_edition(self):
        spec = base_spec(profile="school_17_sheet_v2",
                         production_yield_kg_per_10a=["100", "110", "120",
                                                      "130", "140"])
        spec.pop("production_kg")
        issues = self.sv.verify_school_recalculated(
            spec, self.synthetic("finance-template-v2.xlsx"))
        self.assert_no_unhandled_name(issues)
        pairs = {(issue.get("cell"), issue.get("reason")) for issue in issues}
        # The second edition moved the rows down by one: a verifier that
        # still names the first edition's row reports the wrong cell.
        self.assertIn((self.CASH_FLOW + "!E23", self.NET_REASON), pairs, pairs)
        self.assertNotIn((self.CASH_FLOW + "!E24", self.NET_REASON), pairs, pairs)
        self.assertIn((self.BALANCE + "!D6", self.LINK_REASON), pairs, pairs)

    def assert_no_unhandled_name(self, issues):
        """A crash would surface as a NameError message or a bare exception."""
        for issue in issues:
            reason = str(issue.get("reason"))
            self.assertNotIn("NameError", reason, issue)
            self.assertNotIn("not defined", reason, issue)


if __name__ == "__main__":
    unittest.main()
