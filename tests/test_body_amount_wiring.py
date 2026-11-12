"""Wiring of gg_finance_amounts.detect_amount_gaps into checks().

The module shipped with zero callers (IMPL-NOTES §1). These tests drive the
real ``gg_core.checks()`` path — no direct call to ``detect_amount_gaps`` —
and pin the four things the wiring decides: which rows reach the student
report, that section order (not dict order) shapes the wording, that a
legacy ``crosscheck_body`` failure suppresses the duplicate row, and that a
project with no checked amount still reads no section text.
"""
import unittest

from tests._harness import (
    ContractCase,
    claim_of,
    fact_op,
    runtime,
    section_op,
    source_op,
    write_text,
)

CONFLICT_MARK = "서로 다릅니다"


def amount_rows(core, root, p):
    """Rows this wiring owns: body crosscheck rows carrying the §7 wording."""
    return [
        r for r in core.checks(root, p)
        if r["check_id"] == "body_finance_crosscheck"
        and CONFLICT_MARK in r["reason"]
    ]


class BodyAmountWiringTests(ContractCase):
    def _project(self, *, sections, facts=()):
        """``sections`` = [(sid, title, path, order, body_text)]."""
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")
        ops = [source_op()] + list(facts)
        for sid, title, path, order, text in sections:
            write_text(root, path, text)
            ops.append(section_op(sid, title, path, order=order))
        p = core.apply(root, {"request_id": "amount-wiring", "ops": ops}, 0)
        return core, root, p

    def _fact(self, value="1000"):
        """One provided monetary fact; its registered value is irrelevant to
        the amount-gap check (only the body text is read)."""
        return fact_op(
            "sales", "매출액", value, "천원", value_type="decimal",
            period="2027년", scope="합성농장", claim_id="c1")

    def test_conflicting_amounts_across_sections_reach_the_report(self):
        """Same (label, year, scope) written two ways in two TOC sections is
        one student-facing row — the whole point of the wiring."""
        core, root, p = self._project(
            sections=[
                ("intro", "머리말", "intro.md", 1,
                 "합성농장 2027년 매출액은 1,000천원이다.\n"),
                ("plan", "사업계획", "plan.md", 2,
                 "합성농장 2027년 영업이익은 300천원이다.\n"),
                ("cost", "비용계획", "cost.md", 3,
                 "합성농장 2027년 영업이익은 400천원이다.\n"),
            ],
            facts=[self._fact()],
        )
        rows = amount_rows(core, root, p)
        self.assertEqual(1, len(rows), [r["reason"] for r in rows])
        row = rows[0]
        self.assertEqual("fail", row["status"])
        self.assertEqual("error", row["severity"])
        self.assertEqual(["submission_candidate"], row["required_for"])
        self.assertIn("2027년 영업이익", row["reason"])
        self.assertIn("300천원", row["reason"])
        self.assertIn("400천원", row["reason"])

    def test_section_order_not_registration_order_shapes_wording(self):
        """``order`` decides the sentence order: the section registered last
        but ordered first is named first, so '본문 N번째 절' matches the
        document the student reads."""
        core, root, p = self._project(
            sections=[
                ("late", "뒷장", "late.md", 9,
                 "합성농장 2027년 영업이익은 400천원이다.\n"),
                ("early", "앞장", "early.md", 2,
                 "합성농장 2027년 영업이익은 300천원이다.\n"),
            ],
            facts=[self._fact()],
        )
        rows = amount_rows(core, root, p)
        self.assertEqual(1, len(rows), [r["reason"] for r in rows])
        reason = rows[0]["reason"]
        self.assertLess(reason.index("300천원"), reason.index("400천원"))
        self.assertLess(reason.index("앞장 절"), reason.index("뒷장 절"))

    def test_consistent_amounts_produce_no_row(self):
        """Same value in both sections is not a conflict — the wiring must
        not turn a clean manuscript red."""
        core, root, p = self._project(
            sections=[
                ("a", "가", "a.md", 1,
                 "합성농장 2027년 영업이익은 300천원이다.\n"),
                ("b", "나", "b.md", 2,
                 "합성농장 2027년 영업이익은 300천원이다.\n"),
            ],
            facts=[self._fact()],
        )
        self.assertEqual([], amount_rows(core, root, p))

    def test_legacy_crosscheck_failure_suppresses_duplicate(self):
        """When crosscheck_body already failed on the same (label, year,
        scope), the student sees one row, not two."""
        fault = self._fact("1000")
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")
        write_text(root, "one.md", "합성농장 2027년 매출액은 1,000천원이다.\n")
        write_text(root, "two.md", "합성농장 2027년 매출액은 2,000천원이다.\n")
        ops = [
            source_op(claims=claim_of(fault["value"])),
            fault,
            section_op("one", "가", "one.md", order=1),
            section_op("two", "나", "two.md", order=2),
        ]
        p = core.apply(root, {"request_id": "amount-suppress", "ops": ops}, 0)
        report = core.checks(root, p)
        legacy = [
            r for r in report
            if r["check_id"] == "body_finance_crosscheck"
            and CONFLICT_MARK not in r["reason"]
        ]
        self.assertTrue(
            any("기간·단위·범위" in r["reason"] for r in legacy),
            [r["reason"] for r in legacy],
        )
        self.assertEqual([], amount_rows(core, root, p))

    def test_no_checked_amount_reads_no_section_text(self):
        """Without a monetary fact there is nothing to compare, and the
        elif/else split must keep its not_checked row instead of a silent
        pass (G3-03) — the wiring stays inside the ``elif items`` branch."""
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")
        write_text(root, "a.md", "합성농장 2027년 영업이익은 300천원이다.\n")
        write_text(root, "b.md", "합성농장 2027년 영업이익은 400천원이다.\n")
        ops = [
            source_op(),
            section_op("a", "가", "a.md", order=1),
            section_op("b", "나", "b.md", order=2),
        ]
        p = core.apply(root, {"request_id": "amount-empty", "ops": ops}, 0)
        report = core.checks(root, p)
        self.assertEqual([], amount_rows(core, root, p))
        scope_rows = [
            r for r in report
            if r["check_id"] == "body_finance_crosscheck_scope"
        ]
        self.assertEqual(1, len(scope_rows))
        self.assertEqual("not_checked", scope_rows[0]["status"])


if __name__ == "__main__":
    unittest.main()
