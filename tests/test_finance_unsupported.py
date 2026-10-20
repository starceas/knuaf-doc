"""gg_finance rejection reasons must name the condition that actually
blocked the plan (JUDGE-A #5, GUI 8c56de0).

The refusal itself is correct — a five-year plan whose depreciation is
stretched to a 19-year life is wrong.  Only the message was wrong: three
conditions shared one sentence, so a period mismatch blamed "owner
salary / partial investment" that never applied.
"""
import unittest

from tests._harness import ContractCase, runtime


def spec(**over):
    base = dict(
        profile="single_annual_cash_v1",
        crops=["딸기"],
        accounting_basis="cash_pre_tax_no_inventory",
        source_refs=["answers.md#재무"],
        unit="원",
        quantity_unit="kg",
        land="0",
        facility="195000000",
        equity="65000000",
        loan="150000000",
        loan_rate="0.015",
        discount_rate="0.03",
        salvage="0",
        start_year=2027,
        years=5,
        life=5,
        grace=5,
        term=10,
        periods=[{}] * 5,
        repayment="equal_principal",
        investment_basis="school_farm_new_business",
        owner_labor_in_costs=False,
    )
    base.update(over)
    return base


class FinanceGuardMessageTests(ContractCase):
    def _calculate(self, **over):
        return runtime("gg_finance")._calculate(spec(**over))

    def test_pmt_refusal_names_the_repayment_method(self):
        r = self._calculate(repayment="equal_payment")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("원금균등", r["reason"])
        self.assertIn("PMT", r["reason"])
        self.assertIn("값을 바꿔 맞추지 말 것", r["reason"])

    def test_period_mismatch_names_both_numbers(self):
        r = self._calculate(life=19)
        self.assertEqual("unsupported", r["status"])
        self.assertIn("분석기간 5년과 주 투자 내용연수 19년이 다름", r["reason"])
        self.assertNotIn("경영주 급여", r["reason"])
        self.assertNotIn("기존 사업 일부 투자", r["reason"])

    def test_owner_salary_names_only_itself(self):
        r = self._calculate(owner_labor_in_costs=True)
        self.assertEqual("unsupported", r["status"])
        self.assertIn("경영주 급여", r["reason"])
        self.assertNotIn("내용연수", r["reason"])

    def test_partial_investment_names_only_itself(self):
        r = self._calculate(investment_basis="existing_business_partial")
        self.assertEqual("unsupported", r["status"])
        self.assertIn("기존 사업 일부 투자", r["reason"])
        self.assertNotIn("경영주 급여", r["reason"])

    def test_several_conditions_are_all_named(self):
        r = self._calculate(life=19, owner_labor_in_costs=True)
        self.assertEqual("unsupported", r["status"])
        self.assertIn("경영주 급여", r["reason"])
        self.assertIn("내용연수 19년", r["reason"])

    def test_a_supported_plan_gets_past_these_guards(self):
        # Empty period rows stop the calculation later; reaching that point
        # proves the support-range guards were passed.
        with self.assertRaises(KeyError):
            self._calculate()


if __name__ == "__main__":
    unittest.main()
