"""W-E2 — ``gg_finance_amounts.detect_amount_gaps``.

The first block are the two cuts from ``VERDICT-CONFIRM2.md`` (I1, I2):
both must yield **no** row, because a false block on a student body is
worse than a missed one.  The rest lock the extraction, rounding and
wording contract from ``DESIGN-W-E2.md`` §3-§8.
"""

import unittest

from tests import _harness


def sections(*pairs):
    """(section_id, title, text) triples → module input, in order."""
    return [
        {"section_id": sid, "path": f"body/{sid}.md", "title": title,
         "text": text}
        for sid, title, text in pairs
    ]


def item(field_id, scope, period="2028년", fact_id=None, unit="천원"):
    return {
        "fact_id": fact_id or f"{field_id}-{scope}",
        "field_id": field_id,
        "value": None,
        "unit": unit,
        "period": period,
        "scope": scope,
    }


def items(*specs):
    return [item(*spec) for spec in specs]


class AmountGapCases(unittest.TestCase):
    def setUp(self):
        self.gaps = _harness.runtime("gg_finance_amounts").detect_amount_gaps

    # -- I1: no sentence-level year/scope inheritance ------------------

    def test_clause_without_own_year_and_scope_is_not_compared(self):
        rows = self.gaps(
            sections(
                ("V-4", "Ⅴ-4 사업성 분석",
                 "합성농장 1차년도 매출액은 1,000천원이며 "
                 "2028년 영업이익은 500천원이다."),
                ("VI", "Ⅵ 결론",
                 "합성농장 2028년 매출액은 2,000천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    # -- I2: multi-scope caption tables are dropped --------------------

    def test_caption_naming_two_farms_is_not_compared(self):
        body = sections(
            ("V-4", "Ⅴ-4 사업성 분석",
             "합성농장·쌍둥이농장 합계 계획 (단위: 천원)\n\n"
             "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 1,800 |\n"),
            ("VI", "Ⅵ 결론",
             "합성농장 계획 (단위: 천원)\n\n"
             "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 1,500 |\n"),
        )
        self.assertEqual(
            [], self.gaps(body, items(("매출액", "합성농장"))))
        self.assertEqual(
            [],
            self.gaps(body, items(("매출액", "합성농장"),
                                  ("매출액", "쌍둥이농장"))))

    # -- must fire ----------------------------------------------------

    def test_own_year_and_own_scope_in_two_sections(self):
        rows = self.gaps(
            sections(
                ("V-4", "Ⅴ-4 사업성 분석",
                 "합성농장 2027년 영업이익은 300천원이다."),
                ("VI", "Ⅵ 결론",
                 "합성농장 2027년 영업이익은 500천원이다."),
            ),
            items(("영업이익", "합성농장", "2027년")),
        )
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("BODY_SECTION_AMOUNT_CONFLICT", row["code"])
        self.assertEqual("body_finance_crosscheck", row["check_id"])
        self.assertEqual("fail", row["status"])
        self.assertEqual("error", row["severity"])
        self.assertIsNone(row["fact_id"])
        self.assertEqual(["submission_candidate"], row["required_for"])
        self.assertEqual("V-4", row["location"]["section_id"])
        self.assertEqual(
            "Ⅴ-4 사업성 분석 절의 2027년 영업이익 300천원과 Ⅵ 결론 절의 "
            "500천원이 서로 다릅니다. 두 절에 적은 금액을 실제로 계획한 "
            "한 값으로 통일하세요.",
            row["reason"])
        self.assertEqual(2, len(row["amounts"]))
        self.assertEqual(1, len(row["pairs"]))

    def test_transposed_table_against_sentence(self):
        rows = self.gaps(
            sections(
                ("V-4", "Ⅴ-4 사업성 분석",
                 "부록 15 합성농장 2028년 계획 (단위: 천원)\n\n"
                 "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 500 |\n"),
                ("VI", "Ⅵ 결론",
                 "합성농장 2028년 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(2, len(rows[0]["amounts"]))
        self.assertEqual(
            "Ⅴ-4 사업성 분석 절의 2028년 매출액 500천원과 Ⅵ 결론 절의 "
            "800천원이 서로 다릅니다. 두 절에 적은 금액을 실제로 계획한 "
            "한 값으로 통일하세요.",
            rows[0]["reason"])

    def test_year_in_first_column_table(self):
        rows = self.gaps(
            sections(
                ("V-4", "Ⅴ-4 사업성 분석",
                 "합성농장 계획 (단위: 천원)\n\n"
                 "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 500 |\n"),
                ("VI", "Ⅵ 결론",
                 "합성농장 계획 (단위: 천원)\n\n"
                 "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 800 |\n"),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(rows[0]["pairs"]))

    def test_five_sections(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 영업이익은 300천원이다."),
                ("b", "Ⅶ 마무리", "합성농장 2028년 영업이익은 300천원이다."),
                ("c", "Ⅵ 결론", "합성농장 2028년 영업이익은 500천원이다."),
                ("d", "부록 1", "합성농장 2028년 영업이익은 700천원이다."),
                ("e", "부록 2", "합성농장 2028년 영업이익은 800천원이다."),
            ),
            items(("영업이익", "합성농장")),
        )
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(5, len(row["amounts"]))
        self.assertEqual(9, len(row["pairs"]))
        self.assertEqual(
            "5개 절의 2028년 영업이익 금액이 서로 다릅니다 — "
            "Ⅴ-4 사업성 분석·Ⅶ 마무리 절: 300천원, Ⅵ 결론 절: 500천원, "
            "부록 1 절: 700천원, 부록 2 절: 800천원. 모든 절에 적은 금액을 "
            "실제로 계획한 한 값으로 통일하세요.",
            row["reason"])
        self.assertEqual(136, len(row["reason"]))

    def test_four_sections(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2029년 매출액은 1,000천원이다."),
                ("b", "", "합성농장 2029년 매출액은 800천원이다."),
                ("c", "Ⅵ 결론", "합성농장 2029년 매출액은 700천원이다."),
                ("d", "", "합성농장 2029년 매출액은 600천원이다."),
                ("e", "부록", "별첨 자료는 따로 낸다."),
            ),
            items(("매출액", "합성농장", "2029년")),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(4, len(rows[0]["amounts"]))
        self.assertEqual(6, len(rows[0]["pairs"]))
        self.assertEqual(
            "Ⅴ-4 사업성 분석 절의 2029년 매출액 1,000천원, 본문 2번째 절의 "
            "800천원, Ⅵ 결론 절의 700천원, 본문 4번째 절의 600천원이 서로 "
            "다릅니다. 네 절에 적은 금액을 실제로 계획한 한 값으로 "
            "통일하세요.",
            rows[0]["reason"])
        self.assertEqual(123, len(rows[0]["reason"]))

    # -- rounding -----------------------------------------------------

    def test_three_sections_differ_only_at_sub_unit_precision(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 1,000.1천원이다."),
                ("c", "Ⅶ 마무리", "합성농장 2028년 매출액은 1,000.4천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(rows[0]["pairs"]))

    def test_equal_after_rounding_is_silent(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 1,000.4천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    def test_one_won_difference_is_reported(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 1,001천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))

    def test_same_value_in_won_and_thousand_won(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 300천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 300,000원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    def test_units_do_not_hide_a_real_difference(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 300천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 500,000원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))

    def test_decimal_thousand_won_against_won(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 매출액은 4,289.7천원이다."),
                ("b", "Ⅵ 결론",
                 "합성농장 2028년 매출액은 4,290,000원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))

    def test_decimal_thousand_won_equal_after_rounding(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 매출액은 4,289.7천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 4,290천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    # -- extraction must-misses --------------------------------------

    def test_item_word_before_label_is_dropped(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 토마토 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론",
                 "합성농장 2028년 딸기 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    def test_half_year_qualifier_is_dropped(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 상반기 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론",
                 "합성농장 2028년 하반기 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    def test_plain_year_without_particle(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "계획 개요를 적는다."),
                ("b", "", "합성농장 2029년 매출액 1,000천원"),
                ("c", "Ⅵ 결론", "마무리 문단이다."),
                ("d", "", "부록 안내 문단이다."),
                ("e", "", "합성농장 2029년 매출액 800천원"),
            ),
            items(("매출액", "합성농장", "2029년")),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual(
            "본문 2번째 절의 2029년 매출액 1,000천원과 본문 5번째 절의 "
            "800천원이 서로 다릅니다. 두 절에 적은 금액을 실제로 계획한 "
            "한 값으로 통일하세요.",
            rows[0]["reason"])
        self.assertEqual(87, len(rows[0]["reason"]))

    # -- boundaries ---------------------------------------------------

    def test_no_items_means_no_scope_vocabulary(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 800천원이다."),
            ),
            [],
        )
        self.assertEqual([], rows)

    def test_same_section_sentence_and_table_never_pairs(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 매출액은 1,000천원이다.\n\n"
                 "합성농장 계획 (단위: 천원)\n\n"
                 "| 연도 | 매출액 |\n| --- | --- |\n| 2028년 | 800 |\n"),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual([], rows)

    def test_em_dash_and_other_periods_are_ignored(self):
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석",
                 "합성농장 2028년 매출액은 약 9,999만원으로 추정된다."),
                ("b", "Ⅵ 결론",
                 "합성농장 2028년 매출액은 약 1,000만원으로 전망된다."),
            ),
            [item("매출액", "합성농장", unit="만원")],
        )
        self.assertEqual([], rows)

    # -- §6 legacy suppression ---------------------------------------

    def test_legacy_failure_on_same_triple_suppresses(self):
        legacy = [{
            "fact_id": "매출액-합성농장",
            "status": "fail",
            "reason": "기간·단위·범위·출력 반올림 값 불일치",
        }]
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
            legacy_issues=legacy,
        )
        self.assertEqual([], rows)

    def test_legacy_failure_on_another_year_does_not_suppress(self):
        legacy = [{
            "fact_id": "매출액-합성농장",
            "status": "fail",
            "reason": "기간·단위·범위·출력 반올림 값 불일치",
        }]
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2029년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2029년 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
            legacy_issues=legacy,
        )
        self.assertEqual(1, len(rows))

    # -- student language --------------------------------------------

    def test_reason_has_no_forbidden_student_terms(self):
        from tests.test_student_language import FORBIDDEN_STUDENT_TERMS
        rows = self.gaps(
            sections(
                ("a", "Ⅴ-4 사업성 분석", "합성농장 2028년 매출액은 1,000천원이다."),
                ("b", "Ⅵ 결론", "합성농장 2028년 매출액은 800천원이다."),
            ),
            items(("매출액", "합성농장")),
        )
        self.assertEqual(1, len(rows))
        for text in (rows[0]["reason"], rows[0]["remedy"]):
            lowered = text.lower()
            hits = [t for t in FORBIDDEN_STUDENT_TERMS if t in lowered]
            self.assertEqual([], hits, text)


if __name__ == "__main__":
    unittest.main()
