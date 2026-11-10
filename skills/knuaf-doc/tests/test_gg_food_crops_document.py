import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gg_document  # noqa: E402
import gg_frontmatter as fm  # noqa: E402
import gg_major_contract as mc  # noqa: E402

PENDING = fm.PENDING


def headings(state, lines):
    return [(state.read(line)[0], line) for line in lines]


class FoodCropsSummaryNumberingTests(unittest.TestCase):
    """요약 안의 아라비아 1~5는 하위 항목이고, 본문은 1. 머리말에서 시작한다."""

    SUMMARY = [
        "요약",
        "1. 농장 개요",
        "2. 시장전망",
        "3. 재배기술",
        "4. 투자금액",
        "5. 매출 및 이익",
        "가. 벼",
        "(1) 세부",
    ]

    def test_arabic_summary_items_are_level_two_and_body_starts_at_one(self):
        state = gg_document._HeadingState(arabic_summary=True)
        levels = [level for level, _ in headings(state, self.SUMMARY)]
        self.assertEqual(levels, [None, 2, 2, 2, 2, 2, 3, 6])
        self.assertFalse(state.body)
        self.assertEqual(state.read("1. 머리말"), (1, "1. 머리말"))
        self.assertTrue(state.body)
        self.assertEqual(state.read("2. 영농환경 분석"), (1, "2. 영농환경 분석"))

    def test_a_summary_subsection_is_not_the_body_chapter(self):
        state = gg_document._HeadingState(arabic_summary=True)
        for line in self.SUMMARY:
            state.read(line)
        # 4장까지 온 뒤에도 요약 번호는 본문 장으로 승격되지 않는다.
        self.assertEqual(state.summary_number, 5)
        self.assertFalse(state.body)

    def test_without_the_flag_the_old_behaviour_is_unchanged(self):
        state = gg_document._HeadingState()
        levels = [level for level, _ in headings(state, self.SUMMARY)]
        self.assertEqual(levels, [None, 1, 1, 1, 1, 1, 3, 6])
        self.assertTrue(state.body)
        # 본문이 이미 시작됐으므로 1. 머리말은 이어지는 번호가 아니다.
        self.assertEqual(state.read("1. 머리말"), (2, "1. 머리말"))

    def test_food_crops_parse_keeps_summary_items_below_the_chapter(self):
        text = "\n\n".join(self.SUMMARY + [
            "감사의 글", "목차", "1. 머리말", "줄글", "2. 영농환경 분석"])
        nodes = gg_document.parse(text, major_id="food_crops")
        levels = [n["level"] for n in nodes if n["kind"] == "heading"]
        self.assertEqual(levels, [2, 2, 2, 2, 2, 3, 6, 1, 1])
        other = gg_document.parse(text)
        other_levels = [n["level"] for n in other if n["kind"] == "heading"]
        self.assertEqual(other_levels[0], 1)


class FoodCropsFrontmatterSpecTests(unittest.TestCase):
    def test_spec_order_physical_forms_department_and_cover_count(self):
        spec = gg_document.school_frontmatter_spec("food_crops")
        self.assertEqual(spec.orders, ((
            "겉표지", "표제면", "제출서", "인준서", "요약", "감사의 글", "목차"),))
        self.assertEqual(spec.physical_forms, (
            ("겉표지",), ("표제면",), ("제출서",), ("인준서",),
            ("요약", "감사의 글")))
        self.assertEqual(spec.department, "식량작물학과")
        self.assertEqual(spec.cover_count, 2)
        self.assertEqual(spec.notices, ())
        self.assertEqual(spec.layout_confidence, "source_order")
        # 학부모 동의서는 앞부분이 아니라 문서 맨 끝이다.
        for role in spec.orders[0]:
            self.assertNotEqual(role, "학부모 동의서")

    def test_cover_department_matches_the_registered_markers(self):
        self.assertEqual(
            mc.marker_conflicts({"department": "식량작물학과"}, "food_crops"), [])


class FoodCropsCommitteeLimitTests(unittest.TestCase):
    def profile(self, major, members):
        return {"major_id": major,
                "school_profile": {"committee": {"members": members}}}

    def test_food_crops_accepts_four_members_and_refuses_five(self):
        accepted = fm.normalize_school_profile(
            self.profile("food_crops", ["가상위원1", "가상위원2", "가상위원3",
                                        "가상위원4"]))
        self.assertEqual(len(accepted["committee"]["members"]), 4)
        with self.assertRaisesRegex(ValueError, "식량작물 인준서 위원은 4명까지"):
            fm.normalize_school_profile(
                self.profile("food_crops", ["가상위원1", "가상위원2", "가상위원3",
                                            "가상위원4", "가상위원5"]))

    def test_dairy_keeps_its_three_member_limit_and_message(self):
        dairy = fm.normalize_school_profile(
            self.profile("dairy", ["가상위원1", "가상위원2", "가상위원3"]))
        self.assertEqual(len(dairy["committee"]["members"]), 3)
        with self.assertRaisesRegex(ValueError, "위원 3명까지 허용"):
            fm.normalize_school_profile(
                self.profile("dairy", ["가상위원1", "가상위원2", "가상위원3",
                                       "가상위원4"]))


class FoodCropsFrontmatterLinesTests(unittest.TestCase):
    def lines(self, **over):
        profile = fm.normalize_school_profile({"major_id": "food_crops",
            "school_profile": {"title": "합성 벼 재배 계획", "author": "가상학생",
                               "submission_date": "2026년 12월",
                               "graduation_date": "2027년 2월", **over}})
        return profile, fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "동의·날인 확인 대기", "summary": "합성 요약 내용",
            "thanks": "합성 감사 내용"})

    def test_marker_order_and_trailing_lists(self):
        _, lines = self.lines()
        markers = ["겉표지", "표제면", "제출서", "인준서", "요약", "감사의 글", "목차"]
        positions = [lines.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("학부모 동의서", lines)
        self.assertEqual(lines[-2:], ["표 목차", "그림 목차"])

    def test_advisor_line_sits_in_the_title_page_block(self):
        _, lines = self.lines(advisor="가상지도교수")
        advisor = lines.index("지도교수 가상지도교수")
        self.assertLess(lines.index("표제면"), advisor)
        self.assertLess(advisor, lines.index("제출서"))

    def test_second_cover_repeats_the_title_block(self):
        _, lines = self.lines()
        cover = lines.index("겉표지")
        title_page = lines.index("표제면")
        submission = lines.index("제출서")
        cover_block = lines[cover + 1:title_page]
        title_block = lines[title_page + 1:submission]
        self.assertEqual(cover_block[:3], title_block[:3])
        self.assertIn("합성 벼 재배 계획", title_block)

    def test_the_four_member_committee_and_artifacts_reach_the_page(self):
        _, lines = self.lines(committee={"chair": "가상위원장",
            "members": ["가상위원1", "가상위원2", "가상위원3", "가상위원4"]})
        text = "\n".join(lines)
        self.assertEqual(text.count("(인)"), 5)
        self.assertIn("요약", lines)
        self.assertIn("감사의 글", lines)
        self.assertIn("합성 요약 내용", lines)


class FoodCropsPlanTests(unittest.TestCase):
    def manuscript(self):
        profile, lines = FoodCropsFrontmatterLinesTests().lines()
        body = ["1. 농장 개요", "가. 예시", "", "1. 머리말", "줄글",
                "2. 영농환경 분석", "학부모 동의서"]
        text = "\n\n".join(lines + body)
        return text

    def test_plan_finds_the_body_at_the_arabic_first_chapter(self):
        nodes = gg_document.parse(self.manuscript(), major_id="food_crops")
        plan = gg_document.school_frontmatter_plan(nodes, "food_crops")
        self.assertIsNotNone(plan)
        self.assertEqual(nodes[plan["body"]]["text"], "1. 머리말")
        self.assertTrue(plan["official_order"])
        for marker in ("겉표지", "표제면", "제출서", "인준서", "요약",
                       "감사의 글", "목차"):
            self.assertIn(marker, plan["indices"])

    def test_the_same_text_is_refused_for_another_major(self):
        nodes = gg_document.parse(self.manuscript(), major_id="food_crops")
        # 낙농은 요약보다 앞에 학부모 동의서를 요구하므로 이 배치를 아예 읽지 않는다.
        self.assertIsNone(gg_document.school_frontmatter_plan(nodes, "dairy"))
        with self.assertRaises(mc.OutputHeldError) as caught:
            gg_document.school_frontmatter_plan(nodes, "fruit_trees")
        self.assertEqual(caught.exception.reason, "major_marker_conflict")


class FoodCropsTableCaptionTests(unittest.TestCase):
    """식량작물은 제목 없는 표가 정상(SWOT 등) — 오류가 아니라 알림."""

    TABLE = "| 가상 항목 | 가상 값 |\n| --- | --- |\n| 하나 | 둘 |\n"

    def codes(self, text, major):
        return [cid for cid, _ in gg_document.check(text, None, major_id=major)
                if cid in {"missing_caption", "table_caption_optional"}]

    def test_a_title_less_table_is_only_an_advisory_for_food_crops(self):
        self.assertEqual(self.codes(self.TABLE, "food_crops"),
                         ["table_caption_optional"])
        self.assertIn("table_caption_optional",
                      gg_document.POLICY_ADVISORY_CHECKS)

    def test_the_advisory_text_says_it_is_acceptable(self):
        reasons = [reason for cid, reason in
                   gg_document.check(self.TABLE, None, major_id="food_crops")
                   if cid == "table_caption_optional"]
        self.assertEqual(reasons, ["표 제목 없음 — 의도면 OK"])

    def test_a_captioned_table_still_passes_in_place(self):
        text = "표 1. 가상 표\n" + self.TABLE
        self.assertEqual(self.codes(text, "food_crops"), [])

    def test_other_majors_still_report_the_missing_caption_as_an_error(self):
        for major in ("dairy", "fruit_trees", "hort_env_systems", None):
            with self.subTest(major=major):
                self.assertEqual(self.codes(self.TABLE, major),
                                 ["missing_caption"])
                self.assertNotIn("missing_caption",
                                 gg_document.POLICY_ADVISORY_CHECKS)


class FoodCropsTemplatePlaceholderTests(unittest.TestCase):
    """양식 원본 자리표시가 원고에 남으면 오류(식량작물만)."""

    PLACEHOLDERS = (
        ("표 ??", "1. 머리말\n\n표 ??. 가상 표 제목\n"),
        ("그림 ??", "1. 머리말\n\n그림 ??. 가상 그림 제목\n"),
        ("○○○", "1. 머리말\n\n○○○의 학위논문을 인준함\n"),
        ("<작성방법>", "1. 머리말\n\n<작성방법> 가상 안내 상자\n"),
    )

    def codes(self, text, major="food_crops"):
        return [cid for cid, _ in gg_document.check(text, None, major_id=major)]

    def test_each_template_placeholder_is_an_error(self):
        for label, text in self.PLACEHOLDERS:
            with self.subTest(placeholder=label):
                self.assertIn("template_placeholder_left", self.codes(text))
                self.assertNotIn("template_placeholder_left",
                                 gg_document.POLICY_ADVISORY_CHECKS)

    def test_filled_values_do_not_trip_the_check(self):
        text = "1. 머리말\n\n표 1. 가상 표 제목\n\n| 가 |\n| --- |\n| 1 |\n\n가상학생의 농업전문학사 학위논문을 인준함\n"
        self.assertNotIn("template_placeholder_left", self.codes(text))

    def test_other_majors_keep_their_previous_result(self):
        for label, text in self.PLACEHOLDERS:
            for major in ("dairy", "fruit_trees", None):
                with self.subTest(placeholder=label, major=major):
                    self.assertNotIn("template_placeholder_left",
                                     self.codes(text, major))


if __name__ == "__main__":
    unittest.main()
