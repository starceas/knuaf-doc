"""Synthetic school-form what-if regressions; no school/student files."""
from contextlib import ExitStack, redirect_stdout
from decimal import Decimal
import hashlib
import io
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests._harness import ContractCase, runtime, write_text, fact_op, source_op, claim_of
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS


class SolveTests(unittest.TestCase):
    def setUp(self):
        self.need = runtime("gg_need")

    def test_root_and_upward_rounding_reach_goal(self):
        calls = []
        def read(x):
            calls.append(x)
            return 3 * x - 10
        result = self.need._solve(read, 500, 1850.3)
        self.assertEqual(621, result)
        self.assertGreaterEqual(read(result), 1850.3)
        self.assertIn(620.1, calls)
        self.assertIn(621, calls)

    def test_nonpositive_slope_and_already_met_and_small_input(self):
        for read, x, goal in ((lambda x: -x, 500, 100),
                              (lambda x: 1, 500, 2),
                              (lambda x: x, 500, 400),
                              (lambda x: x, .99, 2),
                              (lambda x: x, 0, 2)):
            with self.subTest(x=x, goal=goal):
                self.assertIsNone(self.need._solve(read, x, goal))

    def test_nonlinear_and_nonfinite_are_rejected(self):
        for read, x, goal in ((lambda x: x*x, 500, 400000),
                              (lambda x: float("nan"), 500, 900),
                              (lambda x: x, float("inf"), 900),
                              (lambda x: x, 500, float("nan"))):
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    self.need._solve(read, x, goal)

    def test_root_position_is_checked_not_only_probe_slopes(self):
        def read(x):
            return x if x <= 510 else x + 1
        with self.assertRaises(ValueError):
            self.need._solve(read, 500, 620)

    def test_ceiling_is_recalculated(self):
        def read(x):
            return 0 if x == 621 else x
        with self.assertRaises(ValueError):
            self.need._solve(read, 500, 620.1)


class NeedTests(ContractCase):
    def setUp(self):
        self.need = runtime("gg_need")
        self.core = runtime("gg_core")
        self.real_gate_fact = self.core._finance_gate_fact
        self.audit = runtime("gg_workbook_audit")
        self.root = self.make_project()
        write_text(self.root, "plan.xlsx", "synthetic workbook placeholder")
        write_text(self.root, "body.md", "synthetic body")
        self.book = self.audit.Workbook()
        self.book.values = {("6. 생산계획", "T7"): 500,
                            ("6. 생산계획", "T8"): 3300,
                            ("12 손익계획", "G4"): 2030}
        self.formula(("6. 생산계획", "T9"), "T7*T8/1000", 1650)
        self.formula(("6. 생산계획", "H9"), "1+1", 2)
        self.formula(("12 손익계획", "G7"), "'6. 생산계획'!T9*10", 16500)
        self.formula(("12 손익계획", "G26"), "G7-1000", 15500)
        self.gap = {"status": "ok", "year": 2030,
                    "goal_exact_won": Decimal("19460000"),
                    "plan_exact_won": Decimal("15500000"),
                    "shown": {"goal_won": 19460000}}
        self.crop = "합성작목"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, kwargs in (
                ("gg_core._finance_plan_supported", {"return_value": True}),
                ("gg_core._finance_plan_candidates", {"return_value": ["plan.xlsx"]}),
                ("gg_workbook_audit.Workbook.load", {"return_value": self.book}),
                ("gg_core._finance_gate_fact", {"side_effect": self.fact})):
            self.stack.enter_context(patch(target, **kwargs))
        self.reader = unittest.mock.Mock(side_effect=lambda root, p: self.gap)
        self.stack.enter_context(patch.dict(sys.modules, {
            "gg_target_gap": SimpleNamespace(read=self.reader)}))

    def fact(self, p, field):
        if field == "common.major_id":
            return {"value": "specialty_crops"}
        return {"value": self.crop} if self.crop is not None else None

    def formula(self, key, text, cached):
        self.book.formulas[key] = self.audit.FORMULA(text, None, cached)
        self.book.values[key] = cached
        self.book._ast.pop(key, None)

    def text(self):
        return self.need.need_text(self.root)

    def hashes(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def test_units_exact_input_and_student_assumptions(self):
        self.gap["shown"]["goal_won"] = 19460001
        text = self.text()
        self.assertIn("500kg → 620kg", text)
        self.assertIn("1,650kg → 2,046kg", text)
        self.assertIn("늘어난 396kg", text)
        self.assertIn("19,460,001원", text)
        self.assertIn("가정", text)
        self.assertIn("확인되지 않은 계산", text)
        self.assertIn(self.need.ENDING, text)
        self.assertNotIn("3,300", text)
        self.assertNotIn("3300", text)
        for term in (*FORBIDDEN_STUDENT_TERMS, "적어도", "현실적", "필요해요", "되어야", "2배"):
            self.assertNotIn(term, text)
        self.reader.assert_called_once()
        self.assertGreaterEqual(self.book.evaluate(*self.need.PROFIT,
            overrides={("6. 생산계획", "T7"): 620}), 19460)

    def test_all_upstream_cache_failures_hide_numbers(self):
        for cached in (None, "#VALUE!", 16501, float("nan")):
            with self.subTest(cached=cached):
                self.formula(self.need.SALES, "'6. 생산계획'!T9*10", cached)
                self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_wrong_edition_and_link_formula(self):
        self.formula(("6. 생산계획", "T9"), "T7*T8/1001", 1650)
        self.assertEqual(self.need.UNAVAILABLE, self.text())
        self.formula(("6. 생산계획", "T9"), "T7*T8/1000", 1650)
        del self.book.formulas["6. 생산계획", "H9"]
        self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_multiple_candidate_workbooks_fail_closed(self):
        with patch.object(self.core, "_finance_plan_candidates", return_value=["a.xlsx", "b.xlsx"]):
            self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_current_b2_non_ok_contract_and_mismatches_hide_numbers(self):
        for state in ("none", "absent", "invalid"):
            self.gap["status"] = state
            self.assertEqual(self.need.UNAVAILABLE, self.text())
        self.gap["status"] = "ok"
        self.gap["year"] = 2029
        self.assertEqual(self.need.UNAVAILABLE, self.text())
        self.gap["year"] = 2030
        self.gap["plan_exact_won"] += 1000
        self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_missing_or_multiple_crop_records_hide_numbers(self):
        for crop in (None, [], ["a", "b"], ""):
            self.crop = crop
            self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_other_sales_hide_numbers(self):
        self.formula(self.need.SALES, "'6. 생산계획'!T9*10+100", 16600)
        self.formula(self.need.PROFIT, "G7-1000", 15600)
        self.gap["plan_exact_won"] = Decimal("15600000")
        self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_twice_boundary_is_applied_after_rounding(self):
        self.gap["goal_exact_won"] = Decimal("32000000")
        self.assertIn("500kg → 1,000kg", self.text())
        self.gap["goal_exact_won"] += Decimal("1")
        self.assertEqual(self.need.LARGE, self.text())

    def test_path_escape_and_errors_do_not_leak_details(self):
        with patch.object(self.core, "_finance_plan_candidates", return_value=["../secret.xlsx"]):
            self.assertEqual(self.need.UNAVAILABLE, self.text())
        with patch.object(self.audit.Workbook, "load", side_effect=ValueError("/private/G26/9999")):
            self.assertEqual(self.need.UNAVAILABLE, self.text())

    def test_cli_and_repeated_questions_never_change_project_files(self):
        before = self.hashes()
        self.assertIn("620kg", self.text())
        self.assertEqual(before, self.hashes())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, self.need.main([str(self.root)]))
        self.assertIn("620kg", out.getvalue())
        self.assertEqual(before, self.hashes())

    def test_calculation_without_original_answer_is_not_verified(self):
        self.text()
        before = self.hashes()
        fact = fact_op("yield", "production.yield", "620", "kg/10a")
        fact["value"]["verification"] = "claim_supported"
        with self.assertRaisesRegex(self.core.OperationError, "원자료/위치 누락"):
            self.core.apply(self.root, {"request_id": "unbacked", "ops": [fact]}, 0)
        self.assertEqual(before, self.hashes())

    def test_separate_student_answer_uses_existing_claim_recording_path(self):
        write_text(self.root, "answer.txt", "생산량은 620kg으로 정했어요. 시험재배 수량을 확인했고 늘어난 물량은 계약 판매해요.")
        fact = fact_op("yield", "production.yield", "620", "kg/10a", claim_id="yield-answer")
        fact["value"]["verification"] = "claim_supported"
        source = source_op(claims=claim_of(fact["value"], "yield-answer"))
        p = self.core.apply(self.root, {"request_id": "student-choice", "ops": [source, fact]}, 0)
        self.assertEqual("620", p["facts"]["yield"]["value"])
        self.assertEqual("answer", p["facts"]["yield"]["source_refs"][0]["id"])

    def test_missing_b2_module_is_safe(self):
        with patch.dict(sys.modules, {"gg_target_gap": None}):
            self.assertEqual(self.need.UNAVAILABLE, self.text())


if __name__ == "__main__":
    unittest.main()
