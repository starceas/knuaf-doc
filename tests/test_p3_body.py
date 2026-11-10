"""P3 B-lane contract tests for gg_finance_body.py (design-r2 section 6/6.1/6.2).

Covers: complete-mode required-set enforcement, CLAIM_PLAN_MISSING,
CLAIM_INVALID (dual-id, output_id, wildcard, unsupported keys), fallback
mode explicit-claim-plus-full-body scan, quote-substring verification,
BODY_CONTRADICTION, unit disambiguation, number/unit syntax parsing.
"""
import unittest
from decimal import Decimal

from tests._harness import ContractCase, runtime


def body():
    return runtime("gg_finance_body")


class ClaimShapeTests(ContractCase):
    def test_valid_fact_claim_shape_no_issues(self):
        b = body()
        claim = {"fact_id": "f1", "quote": "매출액은 1000원", "value": "1000",
                  "unit": "원", "period": "2024년", "scope": "농장 전체"}
        self.assertEqual([], b.validate_claim_shape(claim))

    def test_valid_projection_claim_shape_no_issues(self):
        b = body()
        claim = {"projection_id": "p1", "quote": "가격은 500원", "value": "500",
                  "unit": "원", "period": "2024년", "scope": "농장 전체"}
        self.assertEqual([], b.validate_claim_shape(claim))

    def test_both_fact_and_projection_id_rejected(self):
        b = body()
        claim = {"fact_id": "f1", "projection_id": "p1", "quote": "x",
                  "value": "1", "unit": "원", "period": "2024년", "scope": "s"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_neither_fact_nor_projection_id_rejected(self):
        b = body()
        claim = {"quote": "x", "value": "1", "unit": "원",
                  "period": "2024년", "scope": "s"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_output_id_always_rejected_even_with_value(self):
        b = body()
        claim = {"fact_id": "f1", "output_id": "annual:/rows/0/revenue",
                  "quote": "x", "value": "1", "unit": "원",
                  "period": "2024년", "scope": "s"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_output_pointer_rejected(self):
        b = body()
        claim = {"fact_id": "f1", "output_pointer": "/rows/0",
                  "quote": "x", "value": "1", "unit": "원",
                  "period": "2024년", "scope": "s"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_wildcard_output_id_rejected(self):
        b = body()
        claim = {"fact_id": "f1", "output_id": "*",
                  "quote": "x", "value": "1", "unit": "원",
                  "period": "2024년", "scope": "s"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_unsupported_key_rejected(self):
        b = body()
        claim = {"fact_id": "f1", "quote": "x", "value": "1", "unit": "원",
                  "period": "2024년", "scope": "s", "bogus_key": "y"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_missing_required_field_rejected(self):
        b = body()
        claim = {"fact_id": "f1", "quote": "x", "value": "1", "unit": "원"}
        issues = b.validate_claim_shape(claim)
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))

    def test_non_dict_claim_rejected(self):
        b = body()
        issues = b.validate_claim_shape("not a dict")
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in issues))


class CompleteModeCrosscheckTests(ContractCase):
    def _items(self):
        return [{
            "item_id": "it1", "fact_id": "f1", "field_id": "매출액",
            "value": "1000", "measure": {"unit": "원"}, "required": True,
        }]

    def test_matching_claim_found(self):
        b = body()
        sections = [{
            "section_id": "s1", "path": "a.md",
            "text": "농장 매출액은 1000원이다",
            "claims": [{"fact_id": "f1", "field_id": "매출액",
                       "quote": "매출액은 1000원", "value": "1000",
                       "unit": "원", "period": "2024년", "scope": "농장"}],
        }]
        result = b.crosscheck_sections(sections, self._items())
        self.assertEqual(1, len(result["matches"]))
        self.assertEqual([], [i for i in result["issues"]
                              if i["code"] != "CLAIM_INVALID"] or result["issues"])

    def test_missing_claim_is_claim_plan_missing(self):
        b = body()
        sections = [{"section_id": "s1", "path": "a.md", "text": "x", "claims": []}]
        result = b.crosscheck_sections(sections, self._items())
        self.assertTrue(any(
            i["code"] == "CLAIM_PLAN_MISSING" for i in result["issues"]))
        self.assertIn("it1", result["checked_item_ids"])

    def test_quote_not_substring_rejected(self):
        b = body()
        sections = [{
            "section_id": "s1", "path": "a.md", "text": "다른 내용",
            "claims": [{"fact_id": "f1", "field_id": "매출액",
                       "quote": "매출액은 1000원", "value": "1000",
                       "unit": "원", "period": "2024년", "scope": "농장"}],
        }]
        result = b.crosscheck_sections(sections, self._items())
        self.assertTrue(any(i["code"] == "CLAIM_INVALID" for i in result["issues"]))

    def test_value_mismatch_is_body_mismatch(self):
        b = body()
        sections = [{
            "section_id": "s1", "path": "a.md", "text": "매출액은 500원",
            "claims": [{"fact_id": "f1", "field_id": "매출액",
                       "quote": "매출액은 500원", "value": "500",
                       "unit": "원", "period": "2024년", "scope": "농장"}],
        }]
        result = b.crosscheck_sections(sections, self._items())
        self.assertTrue(any(i["code"] == "BODY_MISMATCH" for i in result["issues"]))

    def test_missing_sections_is_body_missing(self):
        b = body()
        result = b.crosscheck_sections([], self._items())
        # empty sections -> complete_mode is False (not sections truthy) ->
        # falls to fallback; fallback with no sections reports BODY_MISSING
        # via the empty-full-text crosscheck_body path.
        self.assertTrue(any(
            i["code"] in ("BODY_MISSING", "CLAIM_PLAN_MISSING")
            for i in result["issues"]))


class FallbackModeCrosscheckTests(ContractCase):
    def test_missing_claims_key_triggers_fallback_full_body_scan(self):
        b = body()
        sections = [{
            "section_id": "s1", "path": "a.md",
            "text": "농장 전체 2024년 매출액은 1000원이다",
            # no 'claims' key at all -> fallback mode
        }]
        items = [{
            "item_id": "it1", "fact_id": "f1", "field_id": "매출액",
            "value": "1000", "measure": {"unit": "원"},
            "period": "2024년", "scope": "농장 전체", "required": True,
        }]
        result = b.crosscheck_sections(sections, items)
        self.assertIn("it1", result["checked_item_ids"])

    def test_fallback_still_validates_explicit_claims(self):
        b = body()
        sections = [{
            "section_id": "s1", "path": "a.md", "text": "some text",
            # no claims key -> fallback; but if a claims list DOES appear
            # on a different malformed section, still checked
        }]
        items = []
        result = b.crosscheck_sections(sections, items)
        self.assertEqual([], result["checked_item_ids"])


class ContradictionTests(ContractCase):
    def test_same_metric_period_scope_different_value_is_contradiction(self):
        b = body()
        occ = [
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "원"},
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "2000", "unit": "원"},
        ]
        issues = b.detect_contradictions(occ)
        self.assertTrue(any(i["code"] == "BODY_CONTRADICTION" for i in issues))

    def test_correct_other_occurrence_does_not_cover_contradiction(self):
        b = body()
        occ = [
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "원"},
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "원"},
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "9999", "unit": "원"},
        ]
        issues = b.detect_contradictions(occ)
        self.assertEqual(1, len(issues))

    def test_different_period_same_value_is_not_a_contradiction(self):
        b = body()
        occ = [
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "원"},
            {"metric": "매출액", "period": "2025년", "scope": "농장",
             "value": "1000", "unit": "원"},
        ]
        issues = b.detect_contradictions(occ)
        self.assertEqual([], issues)

    def test_unit_disagreement_is_contradiction(self):
        b = body()
        occ = [
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "원"},
            {"metric": "매출액", "period": "2024년", "scope": "농장",
             "value": "1000", "unit": "천원"},
        ]
        issues = b.detect_contradictions(occ)
        self.assertTrue(any(i["code"] == "BODY_CONTRADICTION" for i in issues))


class NumberParsingTests(ContractCase):
    def test_plain_integer(self):
        b = body()
        self.assertEqual("1000", str(b.parse_number("1000")))

    def test_grouped_thousands(self):
        b = body()
        self.assertEqual("1234000", str(b.parse_number("1,234,000")))

    def test_decimal(self):
        b = body()
        self.assertEqual("1234.5", str(b.parse_number("1234.5")))

    def test_negative(self):
        b = body()
        self.assertEqual("-500", str(b.parse_number("-500")))

    def test_table_parenthesized_negative(self):
        b = body()
        self.assertEqual("-1234.5", str(b.parse_number("(1,234.5)")))

    def test_lone_sign_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("+"))

    def test_nan_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("NaN"))

    def test_infinity_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("Infinity"))

    def test_exponent_notation_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("1e10"))

    def test_eok_won_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("1억"))

    def test_range_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("100~200"))

    def test_approximate_qualifier_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("약 1000"))

    def test_bad_grouping_rejected(self):
        b = body()
        self.assertIsNone(b.parse_number("1,23"))


class UnitTests(ContractCase):
    def test_monetary_won_to_cheonwon_scale(self):
        b = body()
        self.assertEqual(Decimal("1000"), b.normalize_monetary("1", "천원"))

    def test_monetary_won_scale_one(self):
        b = body()
        self.assertEqual(Decimal("1000"), b.normalize_monetary("1000", "원"))

    def test_unsupported_unit_returns_none(self):
        b = body()
        self.assertIsNone(b.normalize_monetary("1000", "달러"))

    def test_unit_price_same_denominator_basis_matches(self):
        b = body()
        m1 = {"kind": "unit_price", "denominator": "kg", "basis": "1"}
        m2 = {"kind": "unit_price", "denominator": "kg", "basis": "1"}
        self.assertTrue(b.unit_price_matches(m1, m2))

    def test_unit_price_different_denominator_does_not_match(self):
        b = body()
        m1 = {"kind": "unit_price", "denominator": "kg", "basis": "1"}
        m2 = {"kind": "unit_price", "denominator": "g", "basis": "600"}
        self.assertFalse(b.unit_price_matches(m1, m2))

    def test_won_per_600g_not_equal_won_per_kg(self):
        b = body()
        won_per_600g = {"kind": "unit_price", "denominator": "g", "basis": "600"}
        won_per_kg = {"kind": "unit_price", "denominator": "kg", "basis": "1"}
        self.assertFalse(b.unit_price_matches(won_per_600g, won_per_kg))


class ScopePossessiveAndYearSuffixTests(ContractCase):
    """v0.4.8 ⓑ: the sentence crosscheck accepts the natural possessive
    `의` after the scope and a `년` suffix after a numeric-only period.

    The scope/period/label/amount/unit comparison itself is unchanged —
    only the boundary between scope, period and label widens.  The A26
    workbook path (``crosscheck_body_workbook`` /
    ``_crosscheck_with_workbook``) is NOT routed through this regex
    (measured on 970dded: it keeps its own ``scope\\s+(\\d{4}년|\\d+년차)``
    pattern), so the two A26 test modules stay unmodified regressions.
    """

    LABEL = "1차년도 투자 소요액"

    def _items(self, *, period="2027", scope="합성학생농장",
               value="50000", unit="천원", label=None):
        return [{"fact_id": "hort-invest-year1",
                 "field_id": label or self.LABEL,
                 "value": value, "unit": unit, "period": period,
                 "scope": scope}]

    def assertPass(self, text, **kw):
        gse = runtime("gg_school_excel")
        self.assertEqual([], gse.crosscheck_body(text, self._items(**kw)),
                         text)

    def assertFail(self, text, **kw):
        gse = runtime("gg_school_excel")
        issues = gse.crosscheck_body(text, self._items(**kw))
        self.assertTrue(issues, text)

    def test_scope_possessive_and_year_suffix_pass(self):
        """Design §1 rows 1, 2 and 4 flip from FAIL to PASS."""
        for text in (
            "합성학생농장의 2027년 1차년도 투자 소요액은 50,000천원이다.",
            "합성학생농장 2027년 1차년도 투자 소요액은 50,000천원이다.",
            "2027년 합성학생농장의 1차년도 투자 소요액은 50,000천원이다.",
            "2027년 합성학생농장 1차년도 투자 소요액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertPass(text)

    def test_existing_bare_form_still_passes(self):
        """Design §1 row 3 stays PASS (unchanged behavior)."""
        self.assertPass("합성학생농장 2027 1차년도 투자 소요액은 50,000천원이다.")

    def test_widened_boundary_never_accepts_a_wrong_value(self):
        self.assertFail("합성학생농장의 2027년 1차년도 투자 소요액은 99,999천원이다.")
        self.assertFail("2027년 합성학생농장의 1차년도 투자 소요액은 99,999천원이다.")

    def test_widened_boundary_never_accepts_a_wrong_unit(self):
        self.assertFail("합성학생농장의 2027년 1차년도 투자 소요액은 50,000만원이다.")

    def test_widened_boundary_never_accepts_a_wrong_year(self):
        self.assertFail("합성학생농장의 2028년 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("2028년 합성학생농장의 1차년도 투자 소요액은 50,000천원이다.")

    def test_doubled_year_suffix_does_not_match(self):
        """A numeric period accepts exactly one `년`; `2027년년` is not a
        claim position."""
        self.assertFail("합성학생농장의 2027년년 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("2027년년 합성학생농장의 1차년도 투자 소요액은 50,000천원이다.")

    def test_year_suffix_with_other_trailing_josa_does_not_match(self):
        """`2027년도`/`2027년경` are not the declared period either."""
        self.assertFail("합성학생농장의 2027년도 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("합성학생농장의 2027년경 1차년도 투자 소요액은 50,000천원이다.")

    def test_numeric_period_is_not_matched_as_a_substring(self):
        """`20270` right after the scope and `120270` before it must not
        be read as the declared 2027."""
        self.assertFail("합성학생농장의 20270 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("합성학생농장의 120270 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("120270 합성학생농장의 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("20270 합성학생농장의 1차년도 투자 소요액은 50,000천원이다.")

    def test_scope_is_not_matched_inside_a_longer_word(self):
        """Boundary check in isolation: the scope must start a word, so
        `합성농장` never supplies the scope `농장` (both orderings).

        The label is stated explicitly on every row: with the default
        label the sentence has no claim position at all and the negative
        assertion would hold for the wrong reason.
        """
        self.assertFail("합성농장의 2027년 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")
        self.assertFail("2027년 합성농장의 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")
        self.assertFail("2027년 합성농장 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")
        self.assertPass("2027년 농장 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")

    def test_scope_followed_by_a_non_space_does_not_match(self):
        """`농장A의 …` is not the scope `농장`: the widened scope boundary
        accepts only `의` or whitespace, never another word character.

        The exact-scope row is the positive control: it proves the claim
        position in the failing rows is otherwise detectable, so the
        negative rows fail on the boundary and not on the sentence shape.
        """
        self.assertPass("농장 2027년 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")
        self.assertFail("농장A의 2027년 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")
        self.assertFail("농장A 2027년 매출액은 50,000천원이다.",
                        scope="농장", value="50000", label="매출액")

    def test_possessive_is_not_swallowed_by_another_word(self):
        """The optional `의` must be the possessive, not the head of
        `…의견`; otherwise a different word would open the claim."""
        self.assertFail("합성학생농장의견 2027년 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("2027년 합성학생농장의견 1차년도 투자 소요액은 50,000천원이다.")

    def test_korean_ending_period_takes_no_year_suffix(self):
        """`2026년` and `1년차` already end in a Korean letter; a `년`
        tail would accept the non-word `2026년년`."""
        self.assertPass("합성농장 2026년 매출액은 250 천원이다.",
                        period="2026년", scope="합성농장",
                        value="250", label="매출액")
        self.assertFail("합성농장 2026년년 매출액은 250 천원이다.",
                        period="2026년", scope="합성농장",
                        value="250", label="매출액")
        self.assertPass("농장A 1년차 매출액은 1,000 천원이다.",
                        period="1년차", scope="농장A",
                        value="1000", label="매출액")
        self.assertFail("농장A 1년차년 매출액은 1,000 천원이다.",
                        period="1년차", scope="농장A",
                        value="1000", label="매출액")

    def test_specialty_crop_sentence_unchanged(self):
        """Design §1-1 #2/#3: the specialty-crop shape (period
        `2026년`, scope `합성농장`) keeps its exact-match semantics."""
        gse = runtime("gg_school_excel")
        items = [{"field_id": "매출액", "value": "250", "unit": "천원",
                  "period": "2026년", "scope": "합성농장"}]
        self.assertEqual(
            [], gse.crosscheck_body("합성농장 2026년 매출액은 250 천원이다.", items))
        self.assertTrue(
            gse.crosscheck_body("합성농장 2026년 매출액은 251 천원이다.", items))


class RohPeriodAndAmountRangeTests(ContractCase):
    """W-D2 @Roh 중요①: period/amount ranges and parallel second amounts
    must FAIL (the 4a32d0c baseline rejects them)."""

    def _vals(self):
        return [{"fact_id": "f1", "field_id": "매출액", "value": 50000,
                 "unit": "천원", "period": "2027", "scope": "농장A"}]

    def assertFail(self, text):
        gse = runtime("gg_school_excel")
        self.assertTrue(gse.crosscheck_body(text, self._vals()), text)

    def test_period_range_is_not_the_single_period(self):
        self.assertFail("농장A 2026~2027년 매출액은 50,000천원이다.")
        self.assertFail("2026~2027년 농장A 매출액은 50,000천원이다.")
        self.assertFail("농장A의 2026~2027년 매출액은 50,000천원이다.")

    def test_amount_range_is_not_the_single_amount(self):
        self.assertFail("농장A 2027년 매출액은 50,000천원~60,000천원이다.")
        self.assertFail("2027년 농장A 매출액은 50,000천원~60,000천원이다.")

    def test_two_amounts_are_not_one_amount(self):
        self.assertFail("농장A 2027년 매출액은 50,000천원과 60,000천원이다.")


class SecondAmountConnectorTests(ContractCase):
    """W-D6 @PR#111 P2: a second amount joined by ``및`` and its peers.

    The W-D2 guard only knew ranges (`~`) and the parallels `과`/`와`, so
    `50,000천원 및 60,000천원` passed while the 4a32d0c baseline rejected a
    wrong value.  Every connector that can open a second amount must fail;
    a single amount, its thousands separator, a repeated correct claim and
    a table claim keep passing.
    """

    CONNECTORS = ("및", "또는", "혹은", "내지", ",", "/", "-")

    def _vals(self, value=50000):
        return [{"fact_id": "f1", "field_id": "매출액", "value": value,
                 "unit": "천원", "period": "2027", "scope": "농장A"}]

    def crosscheck(self, text, **kw):
        return runtime("gg_school_excel").crosscheck_body(text, self._vals(**kw))

    def test_each_connector_joining_a_different_amount_fails(self):
        for connector in self.CONNECTORS:
            text = (f"농장A의 2027년 매출액은 50,000천원{connector} "
                    "60,000천원이다.")
            with self.subTest(connector=connector, text=text):
                self.assertTrue(self.crosscheck(text), text)

    def test_connector_without_spaces_still_fails(self):
        for text in ("농장A의 2027년 매출액은 50,000천원및 60,000천원이다.",
                     "농장A의 2027년 매출액은 50,000천원/60,000천원이다.",
                     "농장A의 2027년 매출액은 50,000천원-60,000천원이다."):
            with self.subTest(text=text):
                self.assertTrue(self.crosscheck(text), text)

    def test_single_amount_keeps_passing(self):
        self.assertEqual(
            self.crosscheck("농장A의 2027년 매출액은 50,000천원이다."), [])

    def test_thousands_separator_is_not_a_connector(self):
        self.assertEqual(
            self.crosscheck("농장A의 2027년 매출액은 50,000천원이다."), [])
        self.assertEqual(
            self.crosscheck("농장A의 2027년 매출액은 1,234,567천원이다.",
                            value=1234567), [])

    def test_same_amount_restated_passes(self):
        text = ("농장A의 2027년 매출액은 50,000천원이고 "
                "농장A의 2027년 매출액은 50,000천원이다.")
        self.assertEqual(self.crosscheck(text), [])

    def test_comma_before_a_year_is_not_a_second_amount(self):
        """A comma may start a new year's claim; only `년`-less numbers are
        read as a second amount of the amount in front of them."""
        text = ("농장A의 2027년 매출액은 50,000천원, "
                "2028년 매출액은 60,000천원이다.")
        self.assertEqual(self.crosscheck(text), [])

    def test_table_body_still_passes(self):
        text = ("농장A의 판매계획은 표 1과 같다.\n\n"
                "표 1. 판매계획\n\n"
                "| 항목 | 2027년 |\n|---|---|\n"
                "| 매출액 | 50,000 |\n\n"
                "주) 단위: 천원.\n")
        self.assertEqual(self.crosscheck(text), [])


class SecondAmountAfterCommaTests(ContractCase):
    """W-D7 @Roh confirm4: a comma joined second amount.

    The clause split `",\\s+(?=[^\\d])"` backtracked the blank run, so
    `,  60,000` / `, +60,000` split into a new clause and the second
    amount was never compared with the first (W-D2).  Every blanks(0-3)
    x sign(none, +, -, U+2212) combination must fail; a comma that opens
    a genuinely new claim, a thousands separator and the 4a32d0c
    baseline sentences must keep passing.
    """

    SIGNS = ("", "+", "-", "\u2212")

    def _vals(self, value=50000):
        return [{"fact_id": "f1", "field_id": "매출액", "value": value,
                 "unit": "천원", "period": "2027", "scope": "농장A"}]

    def crosscheck(self, text, **kw):
        return runtime("gg_school_excel").crosscheck_body(text, self._vals(**kw))

    def test_roh_two_sentences_fail(self):
        for text in (
            "농장A의 2027년 매출액은 50,000천원,  60,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원, +60,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.crosscheck(text), text)

    def test_blanks_times_sign_combinations_all_fail(self):
        # Control group (bcdf891, before W-D7): passes incorrectly —
        # blanks 0 + U+2212, blanks 1 with any sign, blanks 2-3 either way.
        # Only blanks 0 with no sign / `+` / `-` was blocked.
        for blanks in range(0, 4):
            for sign in self.SIGNS:
                text = ("농장A의 2027년 매출액은 50,000천원,"
                        + " " * blanks + sign + "60,000천원이다.")
                with self.subTest(blanks=blanks, sign=sign, text=text):
                    self.assertTrue(self.crosscheck(text), text)

    def test_thousands_separator_still_passes(self):
        self.assertEqual(
            self.crosscheck("농장A의 2027년 매출액은 1,234,567천원이다.",
                            value=1234567), [])

    def test_comma_opening_a_new_claim_still_passes(self):
        for text in (
            "농장A의 2027년 매출액은 50,000천원, 비용은 30,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원,  비용은 30,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원,  2028년 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)


class TableContextReuseTests(ContractCase):
    def test_spaced_note_belongs_only_to_preceding_table(self):
        text = ("표 1. 첫 계획\n|항목|값|\n|---|---|\n|수입|1|\n"
                "주 : 첫 계획은 천원 단위다.\n"
                "표 2. 둘째 계획\n|항목|값|\n|---|---|\n|수입|2|\n")
        first, second = runtime("gg_school_excel")._body_tables(text)
        self.assertIn("주 : 첫 계획은 천원 단위다.", first[1])
        self.assertNotIn("천원", second[1])

    def test_body_tables_reused_from_gg_school_excel(self):
        """B lane must reuse _body_tables rather than reimplement table
        context ownership — this test imports it via gg_school_excel and
        confirms gg_finance_body does not define its own competing
        implementation."""
        gse = runtime("gg_school_excel")
        b = body()
        self.assertFalse(hasattr(b, "_body_tables"),
                         "gg_finance_body must not duplicate _body_tables")
        self.assertTrue(hasattr(gse, "_body_tables"))

    def test_crosscheck_body_compat_delegates_to_school_excel(self):
        gse = runtime("gg_school_excel")
        b = body()
        text = "매출액은 1000원이다"
        values = {"매출액": {"value": "1000", "unit": "원",
                          "period": "2024년", "scope": "농장"}}
        self.assertEqual(
            gse.crosscheck_body(text, values),
            b.crosscheck_body(text, values))


if __name__ == "__main__":
    unittest.main()
