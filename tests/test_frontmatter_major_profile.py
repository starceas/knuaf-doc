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

    # --- 앞부분 줄 크기: 식량작물은 옛 값, 낙농은 프로필 값 -----------------

    def spec(self, major):
        return runtime("gg_document").school_frontmatter_spec(major)

    # F8 이전 `_peer_form_text_size`에 박혀 있던 (pt, 굵기). 줄 글자로만
    # 종류를 가른 그 함수의 결과를 그대로 옮겼다.
    LEGACY_COVER_TEXT = {
        "농업전문학사 학위논문": (18, True),      # spec.degree
        "영농창업계획": (32, True),              # spec.plan_label
        "이 논문을 농업전문학사 학위논문으로 제출함": (18, True),  # 이 논문을 …
        "가상학생의 농업전문학사 학위논문을 인준함": (18, True),   # … 인준함
        "위원장 박교수 (인)": (18, True),         # 위원장
        "위  원 이교수 (인)": (18, True),         # 위  원
        "지도교수 김교수": (18, True),            # 지도교수
        "가상학생": (20, False),                 # 그 밖
    }

    def test_food_crops_cover_text_keeps_the_hardcoded_sizes(self):
        spec = self.spec("food_crops")
        profile = self.gmf.profile_for("food_crops")
        for text, expected in self.LEGACY_COVER_TEXT.items():
            with self.subTest(text=text):
                self.assertEqual(expected,
                                 self.layout._peer_form_text_size(
                                     text, spec, None))
                self.assertEqual(expected,
                                 self.layout._peer_form_text_size(
                                     text, spec, profile))

    def test_dairy_cover_text_comes_from_its_profile(self):
        """낙농은 굵기가 False다 — 매핑이 틀리면 여기서 걸린다."""
        spec = self.spec("dairy")
        profile = self.gmf.profile_for("dairy")
        for text in ("농업전문학사 학위논문", "이 논문을 농업전문학사 학위논문으로 제출함",
                     "가상학생의 농업전문학사 학위논문을 인준함",
                     "위원장 박교수 (인)", "지도교수 김교수"):
            with self.subTest(text=text):
                self.assertEqual((18, False),
                                 self.layout._peer_form_text_size(
                                     text, spec, profile))
        self.assertEqual((32, True),
                         self.layout._peer_form_text_size(
                             "영농창업계획", spec, profile))
        self.assertEqual((20, False),
                         self.layout._peer_form_text_size(
                             "가상학생", spec, profile))

    # --- 요약 번호형 소제목: `summary_item_pt` ------------------------------

    def summary_item(self, doc, text="1. 농장 개요"):
        paragraph = next(p for p in doc.paragraphs if p.text.strip() == text)
        return (round(paragraph.runs[0].font.size.pt, 1),
                bool(paragraph.runs[0].bold))

    def test_summary_item_size_comes_from_the_profile(self):
        self.assertEqual((16.0, True), self.summary_item(self.convert()))
        with self.patched(summary_item_pt=(14, False)):
            self.assertEqual((14.0, False), self.summary_item(self.convert()))

    def test_dairy_summary_item_uses_its_own_value(self):
        profile = replace(self.gmf.DAIRY_PROFILE, summary_item_pt=(15, False))
        with mock.patch.object(self.layout, "profile_for",
                               lambda major_id: profile
                               if major_id == "dairy" else None), \
                mock.patch.object(self.layout, "_FRONTMATTER_PROFILE_MAJORS",
                                  ("food_crops", "dairy")):
            self.assertEqual((15.0, False),
                             self.summary_item(self.convert("dairy")))

    # --- 바닥글 쪽번호 꾸밈: `page_label` ------------------------------------

    def page_fields(self, paragraph):
        from docx.oxml.ns import qn
        return paragraph._p.findall(qn("w:fldSimple"))

    def test_food_crops_footers_keep_the_bare_page_field(self):
        """`page_label=None`이면 바닥글은 지금까지처럼 `PAGE` 필드 하나뿐이다.

        글자 run이 하나라도 붙으면 쪽번호에 `- `·` -`가 딸려 나온다는 뜻이라
        여기서 걸린다. 앞부분(로마)·본문(아라비아) 두 구역만 본다 — 겉표지는
        바닥글 참조 자체가 없어야 하므로 `footer`에 손대지 않는다.
        """
        from docx.oxml.ns import qn

        doc = self.convert()
        for section in (doc.sections[1], doc.sections[-1]):
            paragraph = section.footer.paragraphs[0]
            self.assertEqual([], [run.text for run in paragraph.runs])
            self.assertEqual(["PAGE"],
                             [f.get(qn("w:instr"))
                              for f in self.page_fields(paragraph)])

    def test_absent_page_label_writes_the_legacy_footer_xml(self):
        """도우미의 `None` 경로는 옛 손코딩과 XML 한 글자까지 같다."""
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        doc = Document()
        legacy = doc.add_paragraph()
        legacy.alignment = WD_ALIGN_PARAGRAPH.CENTER
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        legacy._p.append(field)

        fresh = doc.add_paragraph()
        self.layout.page_number_field(fresh, None)

        self.assertEqual(legacy._p.xml, fresh._p.xml)

    def test_page_label_writes_prefix_suffix_at_its_size(self):
        """낙농 프로필을 직접 넘기면 `- `·` -` run과 9pt가 들어간다."""
        from docx import Document
        from docx.oxml.ns import qn

        profile = self.gmf.DAIRY_PROFILE
        paragraph = Document().add_paragraph()
        self.layout.page_number_field(paragraph, profile)

        self.assertEqual(["- ", " -"], [run.text for run in paragraph.runs])
        self.assertEqual([9.0, 9.0],
                         [run.font.size.pt for run in paragraph.runs])
        fields = self.page_fields(paragraph)
        self.assertEqual(["PAGE"], [f.get(qn("w:instr")) for f in fields])
        # 필드 안 결과 run도 같은 크기를 들고 있어야 Word가 9pt로 그린다.
        inner = fields[0].findall(qn("w:r"))
        self.assertEqual(1, len(inner))
        self.assertEqual(18, int(inner[0].find(qn("w:rPr")).find(qn("w:sz"))
                                 .get(qn("w:val"))))

    def test_body_footer_reads_the_label_from_the_docx_profile(self):
        """빌더가 프로필을 바닥글까지 넘기는지 — 값을 바꾸면 출력이 따라온다."""
        labeled = replace(self.gmf.FOOD_CROPS_PROFILE,
                          page_label=self.gmf._freeze(
                              {"prefix": "[", "suffix": "]", "pt": 8}))
        with mock.patch.object(self.builder, "docx_profile",
                               lambda major_id: labeled
                               if major_id == "food_crops" else None):
            doc = self.convert()
        body = doc.sections[-1].footer.paragraphs[0]
        self.assertEqual(["[", "]"], [run.text for run in body.runs])
        self.assertEqual([8.0, 8.0],
                         [run.font.size.pt for run in body.runs])

    def test_bare_manuscript_has_no_footer_section(self):
        """학교 앞부분이 없는 원고 경로는 바닥글을 만들지 않는다.

        쪽번호 꾸밈(`page_label`)을 프로필에서 읽는 곳은 학교 서식 경로의 두
        구역(`new_numbered_section`·본문 구역)뿐이다. 앞부분 계획이 서지 않는
        원고는 그 전에도 지금도 바닥글 문단을 만들지 않으므로, 이 경로에는
        `- 1 -` 글자 run도 `PAGE` 필드도 붙지 않는다(범위 경계).
        """
        root = self.make_project()
        bind_major(root, "dairy")
        doc = self.builder.convert(
            "# 1. 머리말\n\n합성 본문.\n",
            context=self.mc.output_context(root, "dairy"))
        self.assertEqual([], [run.text
                              for run in doc.sections[-1].footer.paragraphs[0].runs])
        # 쪽번호 필드도 붙지 않는다: 쪽번호를 매기는 구역 자체가 없다.
        self.assertEqual([], self.page_fields(doc.sections[-1].footer.paragraphs[0]))


if __name__ == "__main__":
    unittest.main()
