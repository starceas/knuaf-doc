"""Body finance crosscheck scope on the deployed main runtime.

Covers the reconciliation contract: which facts enter the body crosscheck,
and that non-financial facts are never finance errors (the R6 over-block
regression must stay absent on main) while real monetary facts are never
silently dropped (the H9/R6 under-check defect stays a documented defect).
"""
import unittest

from tests._harness import (
    ContractCase,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    write_text,
)


class FinanceScopeTests(ContractCase):
    def _project_with_fact(self, *, fact, section_id, title, path,
                           body_text, quotes):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")
        write_text(root, path, body_text)
        claims = [section_claim(fact["value"], q) for q in quotes]
        ops = [
            source_op(claims=claim_of(fact["value"])),
            fact,
            section_op(section_id, title, path, claims=claims),
        ]
        p = core.apply(root, {"request_id": "fin-fixture", "ops": ops}, 0)
        return core, root, p

    def test_nonfinancial_fact_no_finance_block(self):
        """R6 over-block must be absent on main: a text farm name with a
        body claim produces no body_finance_crosscheck finding."""
        fact = fact_op("farm_name", "farm_name", "행복농장", None,
                       claim_id="c1")
        core, root, p = self._project_with_fact(
            fact=fact, section_id="intro", title="머리말", path="intro.md",
            body_text="농장명은 행복농장이다.\n",
            quotes=["농장명은 행복농장이다"],
        )
        report = core.checks(root, p)
        self.assertEqual(
            [],
            [r for r in report if r["check_id"] == "body_finance_crosscheck"],
        )
        items, blocked = core._finance_check_items(p)
        self.assertEqual((items, blocked), ([], None))

    def test_won_unit_fact_is_checked(self):
        """H9/R6-undercheck FIXED (gate=P3, C-lane integration): a provided
        monetary fact whose unit is '원' (not '천원') now enters the check
        set instead of being silently dropped. Same test ID preserved per
        DESIGN.md §9's fixed-transition rule; signature unchanged
        (_finance_check_items(p) -> (items, blocked))."""
        core = runtime("gg_core")
        p = {
            "facts": {"income": {
                "field_id": "매출액", "answer_state": "provided",
                "value": "1000000", "unit": "원"}},
            "sections": {"finance": {}},
        }
        items, blocked = core._finance_check_items(p)
        self.assertIsNone(blocked)
        self.assertEqual(1, len(items))
        self.assertEqual("income", items[0]["fact_id"])
        self.assertEqual("원", items[0]["unit"])

    def test_ratio_unit_fact_still_out_of_scope(self):
        """R6-overblock guard, extended: a non-monetary unit (e.g. ratio)
        must stay excluded from the finance body check even after the H9
        fix widens eligibility to both currency units."""
        core = runtime("gg_core")
        p = {
            "facts": {"margin": {
                "field_id": "이익률", "answer_state": "provided",
                "value": "0.2", "unit": "ratio"}},
            "sections": {"finance": {}},
        }
        items, blocked = core._finance_check_items(p)
        self.assertEqual([], items)
        self.assertIsNone(blocked)

    def test_thousand_won_claim_matches_body(self):
        """Positive: a matching 'scope period label은 값단위' sentence
        satisfies the body crosscheck."""
        fact = fact_op("sales", "매출액", "1000", "천원",
                       value_type="decimal", period="2026년",
                       scope="농장 전체", claim_id="c1")
        core, root, p = self._project_with_fact(
            fact=fact, section_id="fin", title="재무", path="fin.md",
            body_text="농장 전체 2026년 매출액은 1,000천원이다.\n",
            quotes=["매출액은 1,000천원"],
        )
        report = core.checks(root, p)
        self.assertEqual(
            [],
            [r for r in report if r["check_id"] == "body_finance_crosscheck"],
        )

    def test_thousand_won_claim_conflict_reported(self):
        """Positive: a conflicting amount in the same claim position is an
        error, never covered by the correct occurrence elsewhere."""
        fact = fact_op("sales", "매출액", "1000", "천원",
                       value_type="decimal", period="2026년",
                       scope="농장 전체", claim_id="c1")
        core, root, p = self._project_with_fact(
            fact=fact, section_id="fin", title="재무", path="fin.md",
            body_text="농장 전체 2026년 매출액은 2,000천원이다.\n",
            quotes=["매출액은 2,000천원"],
        )
        findings = [
            r for r in core.checks(root, p)
            if r["check_id"] == "body_finance_crosscheck"
        ]
        self.assertEqual(1, len(findings))
        self.assertEqual("fail", findings[0]["status"])

    def test_all_empty_claims_blocks(self):
        """Positive: an all-empty claims set blocks instead of silently
        disabling the crosscheck."""
        core = runtime("gg_core")
        p = {
            "facts": {"sales": {
                "field_id": "매출액", "answer_state": "provided",
                "value": "1000", "unit": "천원", "period": "2026년",
                "scope": "농장 전체"}},
            "sections": {"fin": {"claims": []}},
        }
        items, blocked = core._finance_check_items(p)
        self.assertEqual([], items)
        self.assertIsNotNone(blocked)


if __name__ == "__main__":
    unittest.main()
