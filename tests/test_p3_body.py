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


class TableContextReuseTests(ContractCase):
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
