"""Frontmatter order warning (형님 결정 2): 실물 양식 순서가 기준이다.

실물 순서(겉표지 → 표제면·제출서 → 인준서 → 목차)는 경고 없음, 옛 글 순서
(겉표지 → 인준서 → 표제면 → 제출서 → 목차)는 막지 않고 통과하되 경고,
둘 다 아닌 순서는 기존대로 계획 없음(None).
"""
import unittest

from tests._harness import ContractCase, runtime

BASE = {
    "mode": "school", "title": "합성 창업계획", "author": "가나다",
    "department": "특용작물전공", "advisor": "라마바",
    "submission_date": "2026년 12월", "graduation_date": "2027년 2월",
    "committee": {"chair": "사아자", "members": ["차카타", "파하갸", "냐댜랴"]},
}
BODY = "\n\n목차\n\nⅠ. 머리말\n\n본문\n"


class FrontmatterOrderWarningTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.doc_mod = runtime("gg_document")

    def plan_for(self, **profile_over):
        profile = self.fm.normalize_school_profile(
            {"school_profile": {**BASE, **profile_over}})
        text = "\n\n".join(self.fm.frontmatter_lines(profile))
        # frontmatter_lines already ends with the 목차 marker.
        text += "\n\nⅠ. 머리말\n\n본문\n"
        return profile, self.doc_mod.school_frontmatter_plan(self.doc_mod.parse(text))

    def test_official_order_has_no_warning(self):
        profile, plan = self.plan_for()
        self.assertEqual(profile["layout"], "forms_1_to_4")
        self.assertIsNone(profile["order_warning"])
        self.assertTrue(plan["official_order"])
        self.assertIsNone(plan["order_warning"])

    def test_legacy_order_passes_with_warning(self):
        profile, plan = self.plan_for(layout="logical_legacy")
        self.assertIsNotNone(plan, "옛 글 순서 원고는 막지 않는다")
        self.assertEqual(plan["order"], ("겉표지", "인준서", "표제면", "제출서", "목차"))
        self.assertFalse(plan["official_order"])
        self.assertIn("학교 작성요령 6~9쪽 양식 기준 순서와 다름", plan["order_warning"])
        self.assertNotIn("형님", plan["order_warning"])
        self.assertIn("학교 작성요령 6~9쪽 양식 기준 순서와 다름", profile["order_warning"])
        self.assertNotIn("형님", profile["order_warning"])
        self.assertNotIn("형님", profile["source_order_note"])
        self.assertIn("옛 원고 호환용", profile["source_order_note"])

    def test_forms_note_names_the_physical_form_order_as_default(self):
        profile, _ = self.plan_for()
        self.assertIn("6~9쪽 실물 양식 순서", profile["source_order_note"])

    def test_neither_order_keeps_existing_behaviour(self):
        text = "\n\n".join(["겉표지", "제출서", "표제면", "인준서", "목차"]) + BODY
        nodes = self.doc_mod.parse(text)
        self.assertIsNone(self.doc_mod.school_frontmatter_plan(nodes))

    def test_peer_majors_have_no_order_warning(self):
        for major in ("fruit_trees", "hort_env_systems"):
            with self.subTest(major=major):
                profile = self.fm.normalize_school_profile(
                    {"school_profile": {**BASE, "major_id": major}})
                self.assertIsNone(profile["order_warning"])


if __name__ == "__main__":
    unittest.main()
