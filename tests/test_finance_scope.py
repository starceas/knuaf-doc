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


class ClaimsPartialBlockTests(ContractCase):
    """v0.4.8 ⓒ: a claims list that names only PART of the eligible
    monetary facts must block, never shrink the checked set silently.

    Before this change ``_finance_check_items`` switched to "check only
    the claimed facts" as soon as every section carried a claims list;
    one claim out of twenty dropped the other nineteen without a word.
    The all-empty case already blocked — a partial list now blocks too,
    with the same (student-facing, count-only) reason shape.

    The forbidden-term list is duplicated here on purpose: this branch
    must not import W-C's ``tests/test_student_language.py``.  The
    integration pass re-checks the same returned string against W-C's
    list to catch drift between the two copies.
    """

    FORBIDDEN = (
        "정본", "project.json", "import", "프로세스", "잠금", "revision",
        "fact", "claims", "바인딩", "개정", "RDA 팩", "격리", "18시트",
        "영수증 사슬", "지도", "field_id", "사실 ID",
    )

    @staticmethod
    def _fact(fid, value="1000", unit="천원", field_id="매출액",
              **over):
        fact = {"field_id": field_id, "answer_state": "provided",
                "value": value, "unit": unit, "period": "2026년",
                "scope": "농장 전체"}
        fact.update(over)
        return fact

    def _project(self, claimed, *, extra_facts=None):
        facts = {
            "m1": self._fact("m1", "1000"),
            "m2": self._fact("m2", "2000", field_id="이익"),
            "m3": self._fact("m3", "3000", field_id="비용"),
        }
        facts.update(extra_facts or {})
        return {
            "facts": facts,
            "sections": {"fin": {"claims": [
                {"fact_id": fid} for fid in claimed]}},
        }

    def test_partial_claims_block_instead_of_shrinking(self):
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(self._project(["m1"]))
        self.assertEqual([], items)
        self.assertEqual(
            core._CLAIMS_PARTIAL_BLOCK.format(n=3, m=1, k=2), blocked)

    def test_all_eligible_claimed_keeps_the_existing_check(self):
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["m1", "m2", "m3"]))
        self.assertIsNone(blocked)
        self.assertEqual(["m1", "m2", "m3"],
                         [i["fact_id"] for i in items])

    def test_missing_only_non_monetary_fact_is_not_blocked(self):
        """Completeness is judged against ELIGIBLE monetary facts only:
        a claims list that omits a kg fact still runs the check."""
        core = runtime("gg_core")
        p = self._project(["m1", "m2", "m3"], extra_facts={
            "kg": self._fact("kg", "12", unit="kg", field_id="판매량")})
        items, blocked = core._finance_check_items(p)
        self.assertIsNone(blocked)
        self.assertEqual(["m1", "m2", "m3"],
                         [i["fact_id"] for i in items])

    def test_claims_naming_unknown_facts_do_not_count_as_coverage(self):
        """A claim naming a fact id that is not in ``p['facts']`` adds no
        coverage — the block still names the real counts."""
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["m1", "ghost"]))
        self.assertEqual([], items)
        self.assertEqual(
            core._CLAIMS_PARTIAL_BLOCK.format(n=3, m=1, k=2), blocked)

    def test_duplicate_claims_do_not_inflate_the_claimed_count(self):
        """A claims list that repeats one fact id names ONE amount: the
        counts stay N=3, M=1, K=2 and the student is never told that two
        of three amounts were chosen while two were also missing."""
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["m1", "m1", "m1"]))
        self.assertEqual([], items)
        self.assertEqual(
            core._CLAIMS_PARTIAL_BLOCK.format(n=3, m=1, k=2), blocked)
        self.assertIn("빠진 금액 2개", blocked)

    def test_duplicate_claims_keep_n_equals_m_plus_k(self):
        """The count invariant holds for every partial claims set: the
        eligible amounts are either claimed or reported missing."""
        core = runtime("gg_core")
        cases = {
            ("m1",): (3, 1, 2),
            ("m1", "m1"): (3, 1, 2),
            ("m2", "m1", "m2"): (3, 2, 1),
        }
        for claimed, (n, m, k) in cases.items():
            with self.subTest(claimed=claimed):
                _, blocked = core._finance_check_items(self._project(claimed))
                self.assertEqual(
                    core._CLAIMS_PARTIAL_BLOCK.format(n=n, m=m, k=k), blocked)
                self.assertEqual(n, m + k)

    def test_duplicate_full_coverage_runs_the_check_once_per_fact(self):
        """Redundant claims over every eligible amount do not block and do
        not duplicate the checked items."""
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["m1", "m2", "m3", "m1", "m3"]))
        self.assertIsNone(blocked)
        self.assertEqual(["m1", "m2", "m3"], [i["fact_id"] for i in items])

    def test_no_eligible_facts_and_empty_claims_is_silent(self):
        core = runtime("gg_core")
        p = {"facts": {"kg": self._fact("kg", "12", unit="kg")},
             "sections": {"fin": {"claims": []}}}
        self.assertEqual(([], None), core._finance_check_items(p))

    def test_blocked_reason_is_count_only_and_speaks_to_students(self):
        core = runtime("gg_core")
        _, blocked = core._finance_check_items(self._project(["m1"]))
        for term in self.FORBIDDEN:
            with self.subTest(term=term):
                self.assertNotIn(term, blocked)
        for fid in ("m1", "m2", "m3"):
            self.assertNotIn(fid, blocked)
        # N/M/K are the real counts: 3 eligible, 1 claimed, 2 missing.
        self.assertIn("3", blocked)
        self.assertIn("1", blocked)
        self.assertIn("2", blocked)

    def test_checks_emits_one_blocked_row_and_no_fail_rows(self):
        """The caller already renders a blocked ``body_finance_crosscheck``
        row for a non-None reason — no caller change needed."""
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")
        write_text(root, "fin.md", "농장 전체 2026년 매출액은 1,000천원이다.\n")
        facts = [
            fact_op(fid, field_id, value, "천원", value_type="decimal",
                    period="2026년", scope="농장 전체", claim_id=fid)
            for fid, field_id, value in (
                ("m1", "매출액", "1000"),
                ("m2", "이익", "2000"),
                ("m3", "비용", "3000"),
            )
        ]
        claims = [section_claim(facts[0]["value"],
                                "농장 전체 2026년 매출액은 1,000천원이다")]
        p = core.apply(root, {"request_id": "fin-partial", "ops": [
            source_op(claims={**claim_of(facts[0]["value"], "m1"),
                              **claim_of(facts[1]["value"], "m2"),
                              **claim_of(facts[2]["value"], "m3")}),
            *facts,
            section_op("fin", "재무", "fin.md", claims=claims),
        ]}, 0)
        rows = [r for r in core.checks(root, p)
                if r["check_id"] == "body_finance_crosscheck"]
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("body", rows[0]["target"])
        self.assertEqual("blocked", rows[0]["status"])
        self.assertEqual(
            core._CLAIMS_PARTIAL_BLOCK.format(n=3, m=1, k=2),
            rows[0]["reason"])


class RohContextAmountTests(ContractCase):
    """W-D2 @Roh 중요②: reference (context) amounts are not required body
    claims — they stay checkable when claimed, but omitting them from
    claims must NOT block. The plan-only partial block still blocks."""

    @staticmethod
    def _fact(value="1000", field_id="매출액", **over):
        fact = {"field_id": field_id, "answer_state": "provided",
                "value": value, "unit": "천원", "period": "2026년",
                "scope": "농장 전체"}
        fact.update(over)
        return fact

    def _project(self, claimed, extra_facts=None):
        facts = {
            "plan1": self._fact("50000", finance_role="plan"),
            "ctx1": self._fact("30000", field_id="전년 매출액",
                               finance_role="context"),
        }
        facts.update(extra_facts or {})
        return {
            "facts": facts,
            "sections": {"fin": {"claims": [
                {"fact_id": fid} for fid in claimed]}},
        }

    def test_plan_claimed_and_context_omitted_does_not_block(self):
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(self._project(["plan1"]))
        self.assertIsNone(blocked)
        self.assertEqual(["plan1"], [i["fact_id"] for i in items])

    def test_claimed_context_amount_stays_checkable(self):
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["plan1", "ctx1"]))
        self.assertIsNone(blocked)
        self.assertEqual(["plan1", "ctx1"], [i["fact_id"] for i in items])

    def test_plan_partial_block_still_blocks(self):
        core = runtime("gg_core")
        items, blocked = core._finance_check_items(
            self._project(["plan1"], extra_facts={
                "plan2": self._fact("70000", field_id="소득",
                                    finance_role="plan")}))
        self.assertEqual([], items)
        self.assertEqual(
            core._CLAIMS_PARTIAL_BLOCK.format(n=2, m=1, k=1), blocked)

    def test_context_only_and_empty_claims_is_silent(self):
        core = runtime("gg_core")
        p = {"facts": {"ctx1": self._fact("30000", field_id="전년 매출액",
                                          finance_role="context")},
             "sections": {"fin": {"claims": []}}}
        self.assertEqual(([], None), core._finance_check_items(p))


if __name__ == "__main__":
    unittest.main()
