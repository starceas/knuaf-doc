"""DOCX 빌더가 전공 서식 값을 프로필 표에서 읽는지 (F2).

빌더가 `food_crops` 이름을 직접 비교하던 자리를 프로필 조회로 바꿨다. 이
시험은 그 치환이 진짜인지 — 즉 프로필 값을 고치면 출력이 따라 바뀌고,
값이 같으면 지금까지와 같은지 — 를 잰다. 프로필 표에 값이 있는 전공이라도
`build_docx.DOCX_PROFILE_MAJORS`에 없으면 기본 경로를 타야 한다(낙농은
아직 그 자리다: 이 단위는 식량작물 치환만 다룬다).

가짜 원고·가짜 이름만 쓴다.
"""

import unittest
from dataclasses import replace
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

CHAPTERS = ("# 1. 머리말", "# 2. 영농환경 분석", "# 3. 영농계획 수립")
CLOSERS = ("# 맺음말 및 발전방향", "# 참고문헌", "# 학부모 동의서")


def margins(section):
    from docx.oxml.ns import qn
    mar = section._sectPr.find(qn("w:pgMar"))
    keys = ("top", "right", "bottom", "left", "header", "footer", "gutter")
    return {key: int(mar.get(qn("w:" + key))) for key in keys}


class DocxMajorProfileTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.gmf = runtime("gg_major_format")
        self.mc = runtime("gg_major_contract")

    # --- 가짜 원고 ---------------------------------------------------------

    def manuscript(self, major="food_crops"):
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "summary": "1. 농장 개요\n\n합성 요약 내용",
            "thanks": "합성 감사 내용"})
        body = []
        for chapter in CHAPTERS + CLOSERS:
            body += [chapter, "합성 본문 " + chapter[2:]]
        return "\n\n".join(lines + body)

    def convert(self, major="food_crops", text=None):
        root = self.make_project()
        bind_major(root, major)
        return self.builder.convert(
            text or self.manuscript(major),
            context=self.mc.output_context(root, major))

    def patched(self, **changes):
        """프로필 표를 건드리지 않고, 이 빌드만 다른 프로필을 보게 한다."""
        profile = replace(self.gmf.FOOD_CROPS_PROFILE, **changes)
        return mock.patch.object(
            self.builder, "docx_profile",
            lambda major_id: profile if major_id == "food_crops" else None)

    # --- 값 표가 옛 하드코딩과 같다 ---------------------------------------

    def test_food_crops_profile_equals_the_values_the_builder_hardcoded(self):
        profile = self.gmf.profile_for("food_crops")
        # LEVEL_PT
        self.assertEqual({1: 20, 2: 18, 3: 16, 4: 14, 5: 13, 6: 13, 7: 13,
                          8: 13, 9: 13}, dict(profile.level_pt))
        # gg_hwpx.FOOD_CROPS_SECTION_FORMATS (구역별 여백·쪽번호)
        from gg_hwpx import FOOD_CROPS_SECTION_FORMATS
        self.assertEqual(
            [dict(row["margin_mm"]) for row in FOOD_CROPS_SECTION_FORMATS],
            [dict(row["margin_mm"]) for row in profile.section_formats])
        self.assertEqual([dict(row["page_number"]) for row in FOOD_CROPS_SECTION_FORMATS],
                         [dict(row["page_number"]) for row in profile.section_formats])
        # 목차 제목 22pt, 동의서 20/14/10 굵기
        self.assertEqual(22, profile.toc_title_pt)
        self.assertEqual((20, True), self.gmf.front_title(profile, "학부모 동의서"))
        self.assertEqual((14, True), tuple(profile.consent_body_pt))
        self.assertEqual((10, False), tuple(profile.consent_note_pt))

    def test_food_crops_notice_is_the_string_the_manifest_used(self):
        # 문구는 매니페스트 `notice`에 그대로 실린다. 한 글자만 달라도
        # 기존 산출물 매니페스트 의미가 바뀐다.
        notice = self.gmf.profile_for("food_crops").finish_notice
        self.assertIn("쪽 번호(요약은 Ⅰ부터, 본문은 1부터)", notice)
        self.assertIn("여백(위30/아래30/머리0/꼬리10/좌30/우30/제본0 mm)", notice)

    # --- 프로필 값을 고치면 출력이 따라온다 --------------------------------

    def test_body_section_margins_come_from_the_profile(self):
        default = margins(self.convert().sections[-1])
        wide = {**dict(self.gmf.FOOD_CROPS_PROFILE.section_formats[2]["margin_mm"]),
                "left": 25, "right": 26}
        formats = list(self.gmf.FOOD_CROPS_PROFILE.section_formats)
        formats[2] = {**dict(formats[2]), "margin_mm": wide}
        with self.patched(section_formats=tuple(formats)):
            patched = margins(self.convert().sections[-1])
        self.assertNotEqual(default, patched)
        self.assertEqual(self.builder.Mm(25).twips, patched["left"])
        self.assertEqual(self.builder.Mm(26).twips, patched["right"])

    def test_heading_sizes_come_from_the_profile(self):
        def heading_pt(doc, text):
            # 목차 줄에도 같은 글자가 있으므로 본문 제목 스타일만 고른다.
            paragraph = next(p for p in doc.paragraphs
                             if p.style.name == "Heading 1"
                             and p.text.strip() == text)
            return round(paragraph.runs[0].font.size.pt, 1)

        self.assertEqual(20.0, heading_pt(self.convert(), "1. 머리말"))
        with self.patched(level_pt=self.gmf._freeze(
                {1: 26, 2: 18, 3: 16, 4: 14, 5: 13, 6: 13, 7: 13,
                 8: 13, 9: 13})):
            self.assertEqual(26.0, heading_pt(self.convert(), "1. 머리말"))

    def test_contents_title_size_comes_from_the_profile(self):
        def toc_pt(doc):
            paragraph = next(p for p in doc.paragraphs
                             if p.text.strip() == "목    차")
            return round(paragraph.runs[0].font.size.pt, 1)

        self.assertEqual(22.0, toc_pt(self.convert()))
        with self.patched(toc_title_pt=24):
            self.assertEqual(24.0, toc_pt(self.convert()))

    def test_consent_sizes_come_from_the_profile(self):
        def sizes(doc):
            body = next(p for p in doc.paragraphs
                        if p.text.strip() == "합성 본문 학부모 동의서")
            return (round(body.runs[0].font.size.pt, 1), body.runs[0].bold)

        self.assertEqual((14.0, True), sizes(self.convert()))
        with self.patched(consent_body_pt=(13, False)):
            self.assertEqual((13.0, False), sizes(self.convert()))

    # --- 장 새 쪽도 프로필이 정한다 ---------------------------------------

    def test_chapter_page_breaks_follow_the_profile_flag(self):
        nodes = [{"kind": "heading", "level": 1, "text": "# %d." % i}
                 for i in range(1, 4)]
        self.assertEqual({id(n) for n in nodes[1:]},
                         self.builder.chapter_break_node_ids(nodes, "food_crops"))
        with self.patched(chapter_break_nodes=False):
            self.assertEqual(set(),
                             self.builder.chapter_break_node_ids(nodes, "food_crops"))

    # --- 표에 있어도 목록에 없으면 기본 경로 -------------------------------

    def test_majors_outside_the_profile_list_keep_the_legacy_path(self):
        self.assertIsNotNone(self.gmf.profile_for("dairy"))
        self.assertIsNone(self.builder.docx_profile("dairy"))
        self.assertIsNone(self.builder.docx_profile("specialty_crops"))
        self.assertIs(self.gmf.FOOD_CROPS_PROFILE,
                      self.builder.docx_profile("food_crops"))
        # 기본 경로 여백은 종전 그대로여야 한다(위20/아래15/좌30/우30/머리15/꼬리15).
        root = self.make_project()
        bind_major(root, "dairy")
        doc = self.builder.convert(
            "# 1. 머리말\n\n합성 본문.\n",
            context=self.mc.output_context(root, "dairy"))
        self.assertEqual(
            {"top": self.builder.Mm(20).twips, "bottom": self.builder.Mm(15).twips,
             "left": self.builder.Mm(30).twips, "right": self.builder.Mm(30).twips,
             "header": self.builder.Mm(15).twips,
             "footer": self.builder.Mm(15).twips, "gutter": 0},
            margins(doc.sections[0]))


if __name__ == "__main__":
    unittest.main()
