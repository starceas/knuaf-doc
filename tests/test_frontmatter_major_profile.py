"""앞부분 배치(`gg_frontmatter_layout`)가 전공 서식 표를 읽는지 (F4).

`_render_peer_forms`는 `major_id == 'food_crops'`를 직접 비교하고 제목 크기를
`{'요약': 22, '감사의 글': 20, '학부모 동의서': 20}`로 들고 있었다. 이 단위는
그 자리를 `gg_major_format` 프로필 조회로 바꿨다. 이 시험은 (1) 식량작물
출력이 옛 값 그대로인지, (2) 프로필 값을 고치면 출력이 따라오는지(치환이
진짜인지), (3) 낙농은 게이트로 옛 경로를 타는지를 잰다.

가짜 원고·가짜 이름만 쓴다.
"""

import unittest
from dataclasses import replace
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

CHAPTERS = ("# 1. 머리말", "# 2. 영농환경 분석", "# 3. 영농계획 수립")
CLOSERS = ("# 맺음말 및 발전방향", "# 참고문헌", "# 학부모 동의서")

# F4 이전 코드에 박혀 있던 앞부분 제목 크기. 식량작물 앞부분에 실제로
# 그려지는 두 역할만 쓴다(학부모 동의서는 본문 끝 제목이라 이 경로 밖).
LEGACY_TITLE_PT = {"요약": 22.0, "감사의 글": 20.0}


class FrontmatterMajorProfileTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.layout = runtime("gg_frontmatter_layout")
        self.builder = runtime("build_docx")
        self.gmf = runtime("gg_major_format")
        self.mc = runtime("gg_major_contract")

    # --- 가짜 원고 ---------------------------------------------------------

    def manuscript(self, major):
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "동의·날인 확인 대기",
            "summary": "1. 농장 개요\n\n합성 요약 내용",
            "thanks": "합성 감사 내용"})
        body = []
        for chapter in CHAPTERS + CLOSERS:
            body += [chapter, "합성 본문 " + chapter[2:]]
        return "\n\n".join(lines + body)

    def convert(self, major="food_crops"):
        root = self.make_project()
        bind_major(root, major)
        return self.builder.convert(
            self.manuscript(major),
            context=self.mc.output_context(root, major))

    def front_heading(self, doc, text):
        """앞부분 제목 문단(`Heading 1`). 목차 줄은 다른 스타일이라 걸러진다."""
        return next(p for p in doc.paragraphs
                    if p.style.name == "Heading 1" and p.text.strip() == text)

    def title_pt(self, doc, text):
        return round(self.front_heading(doc, text).runs[0].font.size.pt, 1)

    def margins(self, section):
        from docx.oxml.ns import qn
        mar = section._sectPr.find(qn("w:pgMar"))
        return {key: int(mar.get(qn("w:" + key)))
                for key in ("top", "right", "bottom", "left")}

    # --- 식량작물: 옛 값 그대로 --------------------------------------------

    def test_food_crops_titles_keep_the_values_the_module_hardcoded(self):
        doc = self.convert()
        for role, pt in LEGACY_TITLE_PT.items():
            self.assertEqual(pt, self.title_pt(doc, role))

    def test_food_crops_front_margins_keep_the_measured_band(self):
        # front 구역 30 mm 사방 — 옛 `FOOD_CROPS_SECTION_FORMATS[1]`.
        self.assertEqual(
            {key: self.builder.Mm(30).twips
             for key in ("top", "right", "bottom", "left")},
            self.margins(self.convert().sections[1]))

    # --- 치환이 진짜인가: 프로필을 고치면 출력이 따라온다 -------------------

    def patched(self, **changes):
        profile = replace(self.gmf.FOOD_CROPS_PROFILE, **changes)
        return mock.patch.object(self.layout, "profile_for",
                                 lambda major_id: profile
                                 if major_id == "food_crops" else None)

    def test_front_titles_come_from_the_profile(self):
        titles = dict(self.gmf.FOOD_CROPS_PROFILE.front_titles)
        titles["요약"] = (24, True)
        titles["감사의 글"] = (18, True)
        with self.patched(front_titles=self.gmf._freeze(titles)):
            doc = self.convert()
        self.assertEqual(24.0, self.title_pt(doc, "요약"))
        self.assertEqual(18.0, self.title_pt(doc, "감사의 글"))

    def test_front_margin_comes_from_the_profile(self):
        formats = list(self.gmf.FOOD_CROPS_PROFILE.section_formats)
        front = dict(formats[1])
        front["margin_mm"] = {**dict(front["margin_mm"]), "top": 25}
        formats[1] = front
        with self.patched(section_formats=tuple(formats)):
            margins = self.margins(self.convert().sections[1])
        self.assertEqual(self.builder.Mm(25).twips, margins["top"])

    # --- 낙농은 게이트로 옛 경로 -------------------------------------------

    def test_dairy_gate_matches_the_docx_builder(self):
        self.assertNotIn("dairy", self.layout._FRONTMATTER_PROFILE_MAJORS)
        self.assertEqual(tuple(self.builder.DOCX_PROFILE_MAJORS),
                         tuple(self.layout._FRONTMATTER_PROFILE_MAJORS))

    def test_dairy_keeps_the_legacy_path_while_the_gate_is_shut(self):
        from docx.oxml.ns import qn
        doc = self.convert("dairy")
        # 옛 `major_id == 'food_crops'` 결과: 로마 대문자 구역을 열지 않고,
        # 앞부분 제목 run에는 크기를 굽지 않는다(스타일 그대로).
        self.assertEqual([None],
                         [run.font.size
                          for run in self.front_heading(doc, "요약").runs])
        # 요약 구역은 종전 경로의 소문자 로마 쪽번호다.
        self.assertEqual("lowerRoman",
                         doc.sections[1]._sectPr.find(qn("w:pgNumType"))
                         .get(qn("w:fmt")))

    def test_opening_the_gate_for_dairy_changes_the_output(self):
        """게이트가 낙농을 막는 유일한 이유임을 보인다(치환이 살아 있다)."""
        from docx.oxml.ns import qn
        with mock.patch.object(self.layout, "_FRONTMATTER_PROFILE_MAJORS",
                               ("food_crops", "dairy")):
            doc = self.convert("dairy")
        # 낙농 프로필의 앞부분 제목은 전부 22pt(견본 실측).
        self.assertEqual(22.0, self.title_pt(doc, "요약"))
        self.assertEqual(22.0, self.title_pt(doc, "감사의 글"))
        # 앞부분 구역이 로마 대문자로 열리고 여백도 프로필 값(30 mm)이다.
        self.assertEqual("upperRoman",
                         doc.sections[1]._sectPr.find(qn("w:pgNumType"))
                         .get(qn("w:fmt")))
        self.assertEqual(self.builder.Mm(30).twips,
                         self.margins(doc.sections[1])["top"])


if __name__ == "__main__":
    unittest.main()
