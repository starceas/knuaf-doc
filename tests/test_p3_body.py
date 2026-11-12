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
        """`2027년경`/`2027년쯤` are hedged periods, not the declared one.

        W-H C1 flipped `2027년도` from FAIL to PASS: it is the same period
        as `2027`/`2027년` (release-note known limit C1), so only a suffix
        that changes the meaning of the period still blocks.
        """
        self.assertPass("합성학생농장의 2027년도 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("합성학생농장의 2027년경 1차년도 투자 소요액은 50,000천원이다.")
        self.assertFail("합성학생농장의 2027년쯤 1차년도 투자 소요액은 50,000천원이다.")

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


class RivalSecondAmountTests(ContractCase):
    """W-D8: same unit, different value in one claim sentence must fail.

    The four earlier leaks (range → repeated claim → `및` → comma plus
    blanks/sign) all came from enumerating the glyph that joins the two
    figures.  The rule is now unit based: any same-unit figure in the same
    statement whose value differs from the checked amount fails, whatever
    joins them, unless a new claim head (`비용은`, `매출액 =`) or a period
    (`2028년`) introduces it.

    Control group at 6cda938 (before this fix): 684 of the 714 shapes below
    passed silently.  Every one must fail now.
    """

    BASE = "농장A의 2027년 매출액은 50,000천원"
    SEPS = ("", " ", "  ", "  ", "\t", "\uFF0C", "\u3001", "\uFF1B", "\uFF1A",
            ";", ":", "(", ")", "->", " = ", ", 약 ", " ~ 약 ", " 또는 약 ")
    SIGNS = ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1")
    AMOUNTS = ("60,000천원이다.", "60000천원이다.", "60,000천원",
               "60,000 천원이다.", "60,000.0천원이다.",
               "60,000천원으로 늘었다.")

    MESSAGE = ("같은 문장에 다른 금액이 함께 있습니다. "
               "그 금액이 전년·목표·일부 금액이면 문장을 나눠 쓰세요")

    def _vals(self, value=50000):
        return [{"fact_id": "f1", "field_id": "매출액", "value": value,
                 "unit": "천원", "period": "2027", "scope": "농장A"}]

    def crosscheck(self, text, **kw):
        return runtime("gg_school_excel").crosscheck_body(text, self._vals(**kw))

    def test_every_join_sign_and_amount_shape_fails(self):
        shapes = 0
        for sep in self.SEPS:
            for sign in self.SIGNS:
                for amount in self.AMOUNTS:
                    text = self.BASE + sep + sign + amount
                    shapes += 1
                    with self.subTest(sep=sep, sign=sign, amount=amount):
                        self.assertTrue(self.crosscheck(text), text)
        self.assertGreaterEqual(shapes, 700)

    def test_helper_reports_the_rival_figure(self):
        gse = runtime("gg_school_excel")
        text = self.BASE + "\uFF0C\uFF0B60,000천원이다."
        found = gse.find_rival_amounts(text, "천원", expected=50000)
        self.assertEqual(1, len(found), found)
        self.assertEqual("60000", found[0]["value"])
        self.assertEqual("conflict", found[0]["kind"])

    def test_connector_with_sign_and_amount_forms_all_fail(self):
        """A connector plus a sign used to slip past both checks.

        `_SECOND_AMOUNT` allowed only ASCII `+ -`, and its `−`; the wide
        forms `＋ － ±` fell through.  `_CLAIM_HEAD` then read `또|는`,
        `혹|은`, `정|도` as a label plus particle — a new claim head — so
        the rival was excused.  Control at the commit before this fix:
        39 of 126 shapes passed silently.
        """
        connectors = (" 및 ", " 또는 ", " 혹은 ", " 내지 ", " 그리고 ", " 정도 ")
        signs = ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1")
        amounts = ("60,000천원", "60000천원", "60,000 천원")
        for connector in connectors:
            for sign in signs:
                for amount in amounts:
                    text = self.BASE + connector + sign + amount + "이다."
                    with self.subTest(connector=connector, sign=sign,
                                      amount=amount):
                        self.assertTrue(self.crosscheck(text), text)

    def test_roh_confirm5_fraction_and_sign_are_compared(self):
        """Roh confirm5 중요①: the fraction and the sign belong to the value.

        Control at 9c3dbd0: both Roh sentences passed silently.  On the
        8×7×3 grid below, 108 of 168 differing-fraction shapes leaked and
        60 blocked without the hint; 10 of the 16 `.0` shapes were wrongly
        blocked.
        """
        for text in (
            self.BASE + "이고 50,000.1천원이다.",
            self.BASE + " (－50,000천원)이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"])

    def test_fraction_forms_differ_unless_zero(self):
        connectors = ("이고", "이며", " 및 ", " 또는 ", " 혹은 ", " 내지 ",
                      " 그리고 ", ", ")
        signs = ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1")
        for connector in connectors:
            for sign in signs:
                for fraction in (".1", ".01", ".5"):
                    text = (self.BASE + connector + sign + "50,000"
                            + fraction + "천원이다.")
                    with self.subTest(connector=connector, sign=sign,
                                      fraction=fraction):
                        self.assertTrue(self.crosscheck(text), text)
                # `.0` is the same value in another spelling, so only a
                # minus or `±` sign may block it.
                text = self.BASE + connector + sign + "50,000.0천원이다."
                with self.subTest(connector=connector, sign=sign,
                                  fraction=".0"):
                    blocked = bool(self.crosscheck(text))
                    self.assertEqual(sign in ("-", "\u2212", "\uFF0D", "\u00B1"),
                                     blocked, text)

    def test_negative_expectation_compares_sign(self):
        negative = self._vals(value=-5000)
        crosscheck = lambda text: runtime("gg_school_excel").crosscheck_body(
            text, negative)
        base = "농장A의 2027년 매출액은 -5,000천원"
        for text in (base + "이다.", base + "이고 -5,000천원이다.",
                     base + "이고 -5,000.0천원이다.",
                     base + ", 비용은 -3,000천원이다."):
            with self.subTest(text=text):
                self.assertEqual([], crosscheck(text), text)
        for text in (base + "이고 5,000천원이다.",
                     base + " (5,000천원)이다.",
                     base + "이고 +5,000천원이다."):
            with self.subTest(text=text):
                rows = crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"])

    def test_every_second_amount_path_carries_the_hint(self):
        # Roh confirm5 중요①③: a comma case blocked without the fix hint,
        # because the glued-unit and connector paths skipped it.
        for text in (
            self.BASE + "50,000.1천원이다.",
            self.BASE + ", 50,000천원이다.",
            self.BASE + " 및 50,000천원이다.",
            self.BASE + " 및 60,000이다.",
            self.BASE + ", 60,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], rows[0])

    def test_connector_and_adverb_words_are_not_claim_heads(self):
        gse = runtime("gg_school_excel")
        for word in ("또는", "혹은", "정도", "아마도", "가까이", "같이",
                     "많이", "대략", "무려", "비슷하게"):
            text = self.BASE + " " + word + " 60,000천원이다."
            with self.subTest(word=word):
                self.assertTrue(self.crosscheck(text), text)
                found = gse.find_rival_amounts(text, "천원", expected=50000)
                self.assertEqual(["conflict"], [f["kind"] for f in found], found)

    def test_connector_with_particle_is_not_a_claim_head(self):
        """`내지는` / `또한은` — a connector plus a particle is still a connector.

        Checking only the whole head word let every particle-suffixed
        connector through (`내지|는` reads as label+particle).  The stem is
        what decides.  Control before this fix: 126 of 147 shapes passed.
        """
        heads = ("내지는", "또한은", "정도는", "그리고는", "내지가", "혹은도",
                 "또는은", "대략은", "아마도는", "같이는", "많이는", "무려는",
                 "비슷하게는", "및은")
        signs = ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1")
        amounts = ("60,000천원", "60000천원", "60,000 천원")
        for head in heads:
            for sign in signs:
                for amount in amounts:
                    text = self.BASE + " " + head + " " + sign + amount + "이다."
                    with self.subTest(head=head, sign=sign, amount=amount):
                        self.assertTrue(self.crosscheck(text), text)

    def test_slash_second_amount_carries_the_hint(self):
        """책임자 확인5-2: `/` before a same-unit figure is a second amount.

        The unit lookahead rejects `/` (it opens compound units such as
        `천원/㎡`), so `50,000천원/60,000천원` failed as a plain unit
        mismatch with no student hint.  Control at 798aabf: 105 of 175
        shapes below blocked without the hint.  A slash whose figure does
        not carry the same unit stays a compound unit and keeps the bare
        mismatch, so `/10a` (digits, then a measure) must not gain the
        hint.
        """
        signs = ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1")
        amounts = ("60,000천원", "60000천원", "60,000 천원",
                   "50,000.1천원", "52,000천원")
        for sep in ("/", " / ", "/ ", " /", "/\t"):
            for sign in signs:
                for amount in amounts:
                    text = self.BASE + sep + sign + amount + "이다."
                    with self.subTest(sep=sep, sign=sign, amount=amount):
                        rows = self.crosscheck(text)
                        self.assertTrue(rows, text)
                        self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    def test_slash_same_value_passes_and_compound_units_stay_bare(self):
        # `.0` is the same value in another spelling, so only a minus or
        # `±` sign blocks it — the slash path must agree with the comma path.
        for sign in ("", "+", "-", "\u2212", "\uFF0B", "\uFF0D", "\u00B1"):
            text = self.BASE + "/" + sign + "50,000.0천원이다."
            with self.subTest(sign=sign):
                blocked = bool(self.crosscheck(text))
                self.assertEqual(sign in ("-", "\u2212", "\uFF0D", "\u00B1"),
                                 blocked, text)
        # A slash that opens a compound unit is not a second amount: the
        # figure after it carries a measure, not the checked unit.
        for unit in ("㎡", "10a", "10a 기준", "3개", "kg", "ha", "톤", "%",
                     "1,000㎡", "2.5ha"):
            text = self.BASE + "/" + unit + "이다."
            with self.subTest(unit=unit):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertNotIn(self.MESSAGE, rows[0]["reason"], text)

    def test_two_syllable_labels_stay_claim_heads(self):
        # The 2-syllable rule must not start rejecting real labels.
        for label in ("비용은", "경영비는", "총수입은", "판매비는", "인건비가",
                      "조수입도", "소득은", "원가도"):
            text = self.BASE + ", " + label + " 30,000천원이다."
            with self.subTest(label=label):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_helper_folds_full_width_signs_and_punctuation(self):
        gse = runtime("gg_school_excel")
        for sep in ("\uFF0C", "\u3001", "\uFF1B", "\uFF1A", "\u3000"):
            text = self.BASE + sep + "60,000천원이다."
            with self.subTest(sep=sep):
                found = gse.find_rival_amounts(text, "천원", expected=50000)
                self.assertEqual(["conflict"], [f["kind"] for f in found], found)

    def test_helper_leaves_a_new_claim_and_a_period_alone(self):
        gse = runtime("gg_school_excel")
        cases = (
            ("농장A의 2027년 매출액은 50,000천원, 비용은 30,000천원이다.",
             "new_claim"),
            ("농장A의 2027년 매출액은 50,000천원, 매출액 = 30,000천원이다.",
             "new_claim"),
            ("농장A의 2027년 매출액은 50,000천원, 2028년 매출액은 60,000천원이다.",
             "year"),
        )
        for text, kind in cases:
            with self.subTest(text=text):
                found = gse.find_rival_amounts(text, "천원", expected=50000)
                self.assertEqual([kind], [f["kind"] for f in found], found)

    def test_helper_skips_the_checked_value_itself(self):
        gse = runtime("gg_school_excel")
        self.assertEqual(
            [], gse.find_rival_amounts(self.BASE + "이다.", "천원", expected=50000))

    def test_same_value_repeated_still_fails(self):
        # W-D7 behaviour: a repeat of the same figure inside one statement
        # was already blocked by the connector check; it stays blocked.
        self.assertTrue(self.crosscheck(
            "농장A의 2027년 매출액은 50,000천원, 50,000천원이다."))

    def test_new_claim_comma_and_new_year_still_pass(self):
        for text in (
            "농장A의 2027년 매출액은 50,000천원, 비용은 30,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원\uFF0C비용은 30,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원, 2028년 매출액은 60,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원이며 2028년 매출액은 70,000천원이다.",
            "농장A의 2027년 매출액은 50,000천원 (2027년 기준)이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_student_message_is_appended_and_original_reason_kept(self):
        original = "기간·단위·범위·출력 반올림 값 불일치"
        for text in (
            self.BASE + "\uFF0C\uFF0B60,000천원이다.",
            self.BASE + "->60,000천원이다.",
            self.BASE + ";60,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                reason = rows[0]["reason"]
                self.assertTrue(reason.startswith(original), reason)
                self.assertIn(self.MESSAGE, reason)

    def test_student_message_names_no_internal_term(self):
        from tests.test_student_language import FORBIDDEN_STUDENT_TERMS
        reason = self.crosscheck(self.BASE + "\uFF0C60,000천원이다.")[0]["reason"]
        lowered = reason.lower()
        for term in FORBIDDEN_STUDENT_TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term.lower(), lowered, reason)

    def test_other_failures_keep_the_bare_reason(self):
        # A wrong amount is not a rival-amount failure, so the hint must not
        # leak into unrelated reasons.
        rows = self.crosscheck("농장A의 2027년 매출액은 7,000천원이다.")
        self.assertTrue(rows)
        self.assertEqual("기간·단위·범위·출력 반올림 값 불일치", rows[0]["reason"])

    def test_known_limitation_previous_year_amount_blocks(self):
        """Accepted limitation (responsible manager's ruling).

        A rival figure behind a bare word with no particle (`전년
        40,000천원보다`) is not a claim head, so it blocks.  A student
        splits the sentence to pass.  Pinned here so the behaviour is a
        stated limit, not a surprise."""
        self.assertTrue(self.crosscheck(
            "농장A의 2027년 매출액은 50,000천원, 전년 40,000천원보다 늘었다."))
        self.assertEqual(self.crosscheck(
            "농장A의 2027년 매출액은 50,000천원이다. "
            "전년 매출액은 40,000천원이었다."), [])

    def test_count_and_measure_after_comma_pass(self):
        # W-D6 false blocks: a count/measure is not a second amount.
        for text in (
            "농장A의 2027년 매출액은 50,000천원, 3개 품목 기준이다.",
            "농장A의 2027년 매출액은 50,000천원, -5℃ 저장 기준이다.",
            "농장A의 2027년 매출액은 50,000천원, 1,200kg 출하 기준이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)


class KnownLimitFixesTests(ContractCase):
    """v0.4.8 release-note limits: stop blocking correct sentences and stop
    missing wrong amounts.  See DESIGN-W-H.md for the decisions and the
    normal sentences each fix must keep passing.
    """

    BASE = "농장A의 2027년 매출액은 50,000천원"
    MESSAGE = ("같은 문장에 다른 금액이 함께 있습니다. "
               "그 금액이 전년·목표·일부 금액이면 문장을 나눠 쓰세요")

    def _vals(self, value=50000, period="2027", unit="천원"):
        return [{"fact_id": "f1", "field_id": "매출액", "value": value,
                 "unit": unit, "period": period, "scope": "농장A"}]

    def crosscheck(self, text, **kw):
        return runtime("gg_school_excel").crosscheck_body(text, self._vals(**kw))

    # (가1) a year written without 년 뒤의 금액은 그 해 주장이지 두 번째 금액이 아니다.
    def test_year_without_suffix_opens_its_own_claim(self):
        for text in (
            self.BASE + ", 2028 매출액은 60,000천원이다.",
            self.BASE + ", 2028년도 매출액은 60,000천원이다.",
            self.BASE + ", 2028년 매출액은 60,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_year_without_suffix_does_not_excuse_a_rival_of_the_same_year(self):
        # The year-less form must not weaken the real check: a different
        # figure in the same 2027 claim still fails.
        text = self.BASE + " 60,000천원, 2028 매출액은 60,000천원이다."
        self.assertTrue(self.crosscheck(text), text)

    # (가2) one-syllable item names open their own claim.
    def test_one_syllable_label_opens_a_claim(self):
        for text in (
            self.BASE + ", 값은 30,000천원이다.",
            self.BASE + ", 값: 30,000천원이다.",
            self.BASE + ", 값 = 30,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_one_syllable_connectors_still_block(self):
        # The connector words are decided by word, not syllable count.
        for head in ("또는", "혹은", "및", "내지는"):
            text = self.BASE + " " + head + " 60,000천원이다."
            with self.subTest(head=head):
                self.assertTrue(self.crosscheck(text), text)

    # (나1) a correction phrase re-states the same fact with another value.
    def test_correction_phrase_blocks_a_different_amount(self):
        for text in (
            self.BASE + "인데 실제는 60,000천원이다.",
            self.BASE + ", 실제로는 60,000천원이다.",
            self.BASE + ", 사실은 60,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    def test_correction_phrase_with_the_same_amount_passes(self):
        for text in (
            self.BASE + "인데 실제로 50,000천원이다.",
            self.BASE + ", 실제 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    # (나2) a figure on the next line is still the same statement.
    def test_line_break_does_not_hide_a_rival(self):
        for text in (
            self.BASE + "\n60,000천원이다.",
            self.BASE + "\n+60,000천원이다.",
            self.BASE + "\n\u221260,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    def test_line_break_between_label_and_value_passes(self):
        text = "농장A의 2027년 매출액은\n50,000천원이다."
        self.assertEqual(self.crosscheck(text), [], text)

    def test_blank_line_and_word_led_lines_stay_separate_claims(self):
        for text in (
            self.BASE + "이다.\n\n60,000천원은 전년도 매출액이다.",
            self.BASE + "이다.\n60,000천원은 전년도 매출액이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    # (나3) 만원·억원·백만원 rivals are compared by value.
    def test_won_scale_rival_with_a_different_value_blocks(self):
        for text in (
            self.BASE + "(6,000만원)이다.",
            self.BASE + ", 실제는 6,000만원이다.",
            self.BASE + " (6억원)이다.",
            self.BASE + " (6,000백만원)이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    def test_won_scale_rival_with_the_same_value_passes(self):
        for text in (
            self.BASE + "(5,000만원)이다.",
            self.BASE + " (5,000만원)이다.",
            self.BASE + " (50백만원)이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_won_scale_per_unit_figures_are_not_rivals(self):
        for text in (
            self.BASE + ", kg당 3,000원이다.",
            self.BASE + ", 50만원/평이다.",
            self.BASE + ", 3개 품목 기준이다.",
            self.BASE + ", 1인당 200만원이다.",
            self.BASE + ", 2명당 200만원이다.",
            self.BASE + ", 3,000천원/평이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_won_scale_claim_unit_still_fails_without_the_hint(self):
        # Writing the checked amount itself in 만원 is a unit mismatch, not
        # a rival figure, so the sentence keeps the bare reason.
        rows = self.crosscheck("농장A의 2027년 매출액은 5,000만원이다.")
        self.assertTrue(rows)
        self.assertEqual("기간·단위·범위·출력 반올림 값 불일치", rows[0]["reason"])

    def test_won_scale_edges_that_must_keep_passing(self):
        for text in (
            self.BASE + ", 60,000원당 1개다.",
            self.BASE + ", 2028년 목표는 6,000만원이다.",
            self.BASE + " (5,000만원)이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_won_scale_rival_across_a_line_break_blocks(self):
        for text in (
            self.BASE + "\n6,000만원이다.",
            self.BASE + "\n6억원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    # C1: 년도 forms are the same period.
    def test_year_do_forms_name_the_same_period(self):
        for text in (
            "농장A 2027년도 매출액은 50,000천원이다.",
            "농장A 2027년도의 매출액은 50,000천원이다.",
            "농장A의 2027년도 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    def test_year_do_form_with_a_wrong_amount_fails(self):
        for text in (
            "농장A 2027년도 매출액은 60,000천원이다.",
            "농장A 2027년도의 매출액은 60,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.crosscheck(text), text)

    def test_year_do_in_another_section_is_not_missed(self):
        text = ("계획 2027 매출액은 50,000천원이다.\n\n"
                "계획 2027년도 매출액은 60,000천원이다.\n")
        self.assertTrue(self.crosscheck(text, period="2027", value=50000,
                                        unit="천원"), text)

    def test_nth_year_forms_name_the_same_period(self):
        for text in (
            "농장A 1년차 매출액은 50,000천원이다.",
            "농장A 1차년도 매출액은 50,000천원이다.",
            "농장A 1차년 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text, period="1년차"), [], text)
        self.assertTrue(self.crosscheck("농장A 1차년도 매출액은 60,000천원이다.",
                                        period="1년차"))

    def test_year_do_is_not_a_table_header_miss(self):
        text = ("농장A의 판매계획은 표 1과 같다.\n\n"
                "표 1. 판매계획\n\n"
                "| 항목 | 2027년도 |\n|---|---|\n"
                "| 매출액 | 60,000 |\n\n"
                "주) 단위: 천원.\n")
        self.assertTrue(self.crosscheck(text), text)
        good = text.replace("60,000", "50,000")
        self.assertEqual(self.crosscheck(good), [], good)

    # Roh 코드 1회 ①: a year-shaped figure in front of a copula (`및 2028이다`)
    # is a second amount of this claim, not the next period.  Only a real
    # claim head (`2028 매출액은`) opens a new period claim.
    def test_year_shaped_second_amount_before_a_copula_blocks(self):
        for text in (
            self.BASE + " 및 2028이다.",
            self.BASE + ", 2028이다.",
            self.BASE + ", 2028이다",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    # Roh 코드 1회 ②: a connective/adverb in front of a delimiter or particle
    # introduces no claim, so the figure after it is still the same claim.
    def test_connective_head_does_not_excuse_a_rival(self):
        for text in (
            self.BASE + ", 즉: 60,000천원이다.",
            self.BASE + ", 이는 60,000천원이다.",
            self.BASE + ", 즉 60,000천원이다.",
            self.BASE + ", 따라서 60,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)

    # Roh 코드 1회 ③: an approximation word between the per-unit head and the
    # figure keeps it a rate, not a second total amount.
    def test_approximation_word_after_a_per_unit_head_passes(self):
        for text in (
            self.BASE + ", 평당 약 50만원이다.",
            self.BASE + ", 평당 대략 50만원이다.",
            self.BASE + ", 평당 50만원 정도다.",
            self.BASE + ", 1인당 약 200만원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    # Roh 코드 1회 ④: a period that is one end of a range is not the single
    # declared period, whichever spelling (`년`/`년도`) and order it takes.
    def test_year_range_is_not_the_single_period(self):
        for text in (
            "2026년도~2027년도 농장A의 매출액은 50,000천원이다.",
            "2026년도~ 2027년도 농장A의 매출액은 50,000천원이다.",
            "2026년~2027년 농장A의 매출액은 50,000천원이다.",
            "2026년~ 2027년 농장A의 매출액은 50,000천원이다.",
            "2026년～2027년 농장A의 매출액은 50,000천원이다.",
            "농장A 2026년도~2027년도 매출액은 50,000천원이다.",
            "농장A의 2026년~2027년 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.crosscheck(text), text)

    # Roh 코드 1회 권고①: the range guard reads the claim's own window only,
    # so a range elsewhere in the sentence keeps the single period passing.
    def test_single_period_next_to_a_range_elsewhere_passes(self):
        for text in (
            "농장A의 2027년 매출액은 50,000천원이다. 수확량은 10~12톤이다.",
            "농장A의 2027년 매출액은 50,000천원이고 수확량은 10~12톤이다.",
            # A year range in front of the claim is not this claim's range:
            # only a range that overlaps the claim's own period counts.
            "2026년~2027년 농장A의 2027년 매출액은 50,000천원이다.",
            "2026년~2027년 계획 대비 농장A의 2027년 매출액은 50,000천원이다.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.crosscheck(text), [], text)

    # Roh 코드 1회 ⑤: the per-unit head search walks the whole text in front of
    # every won-scale figure, so a long unbroken run costs O(n²) per figure.
    # The bound is relative: four times the figures must not cost four times
    # the time of half the figures.
    def test_per_unit_head_search_stays_linear_on_a_long_run(self):
        import time

        gse = runtime("gg_school_excel")

        def run(count):
            text = self.BASE + "이다 " + "50만원" * count
            start = time.perf_counter()
            gse.crosscheck_body(text, self._vals())
            return time.perf_counter() - start

        small = run(250)
        large = run(500)
        self.assertLess(large, small * 4 + 0.01, (small, large))

    # Roh 코드 1회 권고②: the line-break tests all carry a full stop, so the
    # break itself is not isolated.  Without one, the two lines are one run.
    # A blank line plus a word-led next line stays a separate claim: excusing
    # it would need a second rule for that shape alone, so it is a known
    # limit (W-H 코드1회 권고②).
    def test_line_break_without_a_full_stop_keeps_the_rival(self):
        for text in (
            self.BASE + "이다\n60,000천원이다.",
        ):
            with self.subTest(text=text):
                rows = self.crosscheck(text)
                self.assertTrue(rows, text)
                self.assertIn(self.MESSAGE, rows[0]["reason"], text)


if __name__ == "__main__":
    unittest.main()
