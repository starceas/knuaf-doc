"""Frontmatter order warning (형님 결정 2): 실물 양식 순서가 기준이다.

실물 순서(겉표지 → 표제면·제출서 → 인준서 → 목차)는 경고 없음, 옛 글 순서
(겉표지 → 인준서 → 표제면 → 제출서 → 목차)는 막지 않고 통과하되 경고,
둘 다 아닌 순서는 기존대로 계획 없음(None).
"""
import json
import subprocess
import sys
import unittest
import zipfile

from tests._harness import (
    ContractCase, SCRIPTS, bind_major, runtime, write_text,
)

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
        self.assertIn("옛 원고 호환 순서", profile["source_order_note"])
        # 내부 설정값(layout 키가 따로 담는다)은 사람이 읽는 문구에 넣지 않는다.
        for text in (profile["source_order_note"], profile["order_warning"],
                     plan["order_warning"]):
            self.assertNotIn("logical_legacy", text)

    def test_forms_note_names_the_physical_form_order_as_default(self):
        profile, _ = self.plan_for()
        self.assertIn("6~9쪽 실물 양식 순서", profile["source_order_note"])
        # 끝 괄호로 같은 근거를 되풀이하지 않는다.
        self.assertFalse(profile["source_order_note"].endswith(")"))

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


class FrontmatterOrderWarningReportTests(ContractCase):
    """DOCX 리포트와 HWPX 요약이 같은 키 `school_frontmatter.order_warning`을 싣는다."""

    WARNING_PART = "학교 작성요령 6~9쪽 양식 기준 순서와 다름"

    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root)
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")

    def paper(self, official):
        spec = {"writing_year": 2026, "start_year": 2027, "title": "합성 영농계획"}
        if official:
            spec["school_profile"] = {
                "mode": "school", "layout": "forms_1_to_4",
                "school": "합성대학교", "department": "특용작물학과"}
        return runtime("gg_school_paper").paper(spec, context=self.context)

    def build(self, text, name):
        write_text(self.root, "body.md", text)
        out = "build/" + name + ".docx"
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "build_docx.py"), str(self.root),
             "--in", "body.md", "--out", out, "--major", "specialty_crops"],
            capture_output=True, text=True, encoding="utf-8", cwd=self.root)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        manifest = json.loads((self.root / out).with_suffix(".manifest.json")
                              .read_text(encoding="utf-8"))
        with zipfile.ZipFile(self.root / out) as archive:
            body = archive.read("word/document.xml").decode("utf-8")
        return manifest["school_frontmatter"], body, proc

    def test_docx_report_official_order_has_none(self):
        report, _, _ = self.build(self.paper(official=True), "official")
        self.assertIn("order_warning", report)
        self.assertIsNone(report["order_warning"])
        self.assertIn("6~9쪽 실물 양식 순서", report["source_order_note"])
        self.assertFalse(report["source_order_note"].endswith(")"))

    def test_docx_report_legacy_order_has_warning_and_body_is_untouched(self):
        report, body, proc = self.build(self.paper(official=False), "legacy")
        self.assertIn(self.WARNING_PART, report["order_warning"])
        self.assertNotIn("logical_legacy", report["order_warning"])
        self.assertNotIn("logical_legacy", report["source_order_note"])
        # 경고는 리포트에만 있다 — DOCX 본문과 표준출력에는 새지 않는다.
        self.assertNotIn(self.WARNING_PART, body)
        self.assertNotIn(self.WARNING_PART, proc.stdout + proc.stderr)

    def test_hwpx_summary_carries_the_same_key(self):
        build_hwpx = runtime("build_hwpx")
        official = build_hwpx.school_frontmatter_summary(
            self.paper(official=True), "specialty_crops")
        legacy = build_hwpx.school_frontmatter_summary(
            self.paper(official=False), "specialty_crops")
        self.assertIn("order_warning", official)
        self.assertIsNone(official["order_warning"])
        self.assertIn(self.WARNING_PART, legacy["order_warning"])
        self.assertNotIn("logical_legacy", legacy["order_warning"])


if __name__ == "__main__":
    unittest.main()
