"""식량작물 DOCX·HWPX 조립 시험 (지어낸 원고).

앞부분은 요약~그림 목차까지 로마 대문자 쪽번호, 본문은 아라비아 1부터.
본문 장(`2.`~`4.`)·맺음말·참고문헌·학부모 동의서는 새 쪽에서 시작하고,
학부모 동의서는 문서의 마지막 1단계 제목이다.

선배 논문 본문·실명·실제 학교 서식 값은 쓰지 않는다.
"""

import io
import json
import re
import sys
import unittest
import zipfile
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime
from tests.test_skillgaps_wp2_hwpx import FAKE_CLI, para as kordoc_para, \
    section as kordoc_section

CHAPTERS = ("# 1. 머리말", "# 2. 영농환경 분석", "# 3. 영농계획 수립",
            "# 4. 재무계획")
CLOSERS = ("# 맺음말 및 발전방향", "# 참고문헌", "# 학부모 동의서")


def section(*paragraphs):
    head = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">')
    return head + "".join(paragraphs) + "</hs:sec>"


def para(text, para_ref="0", attrs=""):
    return ('<hp:p paraPrIDRef="' + para_ref + '" styleIDRef="0"' + attrs + '>'
            '<hp:run charPrIDRef="0"><hp:t>' + text + '</hp:t></hp:run></hp:p>')


def hwpx_bytes(sec):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("Contents/header.xml", "<hh:head/>")
        archive.writestr("Contents/section0.xml", sec)
    return buffer.getvalue()


class FoodCropsBuildTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.hwpx = runtime("build_hwpx")
        self.mc = runtime("gg_major_contract")

    def manuscript(self):
        profile = self.fm.normalize_school_profile({
            "major_id": "food_crops",
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
            text or self.manuscript(),
            context=self.mc.output_context(root, major))

    # --- DOCX -------------------------------------------------------------

    def test_front_matter_from_summary_uses_upper_roman_page_numbers(self):
        from docx.oxml.ns import qn
        doc = self.convert()
        self.assertEqual(3, len(doc.sections))
        cover, front, body = doc.sections
        self.assertIsNone(cover._sectPr.find(qn("w:pgNumType")))
        number = front._sectPr.find(qn("w:pgNumType"))
        self.assertEqual("1", number.get(qn("w:start")))
        self.assertEqual("upperRoman", number.get(qn("w:fmt")))
        self.assertIn("PAGE", front.footer._element.xml)
        self.assertEqual(1, len(front._sectPr.findall(qn("w:pgNumType"))))

    def test_body_restarts_at_arabic_one(self):
        from docx.oxml.ns import qn
        doc = self.convert()
        number = doc.sections[-1]._sectPr.find(qn("w:pgNumType"))
        self.assertEqual("decimal", number.get(qn("w:fmt")))
        self.assertEqual("1", number.get(qn("w:start")))
        self.assertEqual(1, len(doc.sections[-1]._sectPr.findall(qn("w:pgNumType"))))

    def test_each_chapter_and_closer_starts_a_new_page(self):
        doc = self.convert()
        breaks = [p.text for p in doc.paragraphs
                  if p.paragraph_format.page_break_before]
        self.assertEqual(["2. 영농환경 분석", "3. 영농계획 수립", "4. 재무계획",
                          "맺음말 및 발전방향", "참고문헌", "학부모 동의서"], breaks)

    def test_body_first_chapter_has_no_page_break_of_its_own(self):
        doc = self.convert()
        first = next(p for p in doc.paragraphs if p.text == "1. 머리말")
        self.assertFalse(first.paragraph_format.page_break_before)

    def test_table_and_figure_contents_are_emitted_without_entries(self):
        doc = self.convert()
        texts = [p.text.strip() for p in doc.paragraphs]
        self.assertIn("표 목차", texts)
        self.assertIn("그림 목차", texts)

    def test_parent_consent_is_the_last_level_one_heading(self):
        doc = self.convert()
        chapters = [p.text.strip() for p in doc.paragraphs
                    if p.style.name == "Heading 1"]
        self.assertEqual("학부모 동의서", chapters[-1])
        self.assertNotIn("학부모 동의서", chapters[:-1])

    def test_chapter_breaks_without_frontmatter_still_start_a_new_page(self):
        # Roh C1 중요2: 앞머리 없는 정상 DOCX 경로에서도 장·마무리 제목은
        # 새 쪽에서 시작해야 한다.
        text = "\n\n".join(["# 1. 머리말", "합성 본문", "# 2. 영농환경 분석",
                            "합성 본문", "# 맺음말 및 발전방향", "합성 본문",
                            "# 참고문헌", "합성 본문", "# 학부모 동의서",
                            "합성 본문"])
        doc = self.convert(text=text)
        self.assertEqual(
            ["2. 영농환경 분석", "맺음말 및 발전방향", "참고문헌", "학부모 동의서"],
            [p.text for p in doc.paragraphs
             if p.paragraph_format.page_break_before])
        first = next(p for p in doc.paragraphs if p.text == "1. 머리말")
        self.assertFalse(first.paragraph_format.page_break_before)

    def test_other_majors_get_no_chapter_page_breaks(self):
        # 산업곤충은 `school_paper` 출력을 지원하지 않아 빌더에 닿지 않는다.
        for major in ("specialty_crops", "fruit_trees",
                      "hort_env_systems", "dairy"):
            with self.subTest(major=major):
                doc = self.convert(major, text="# 1. 머리말\n\n합성 본문 내용")
                self.assertEqual([], [p.text for p in doc.paragraphs
                                      if p.paragraph_format.page_break_before])

    def test_summary_and_thanks_prose_is_not_centred(self):
        # 눈 확인(F-8): 요약·감사의 글은 줄글이라 양쪽 맞춤이어야 한다.
        # 겉표지·표제면·제출서·인준서는 학교 양식대로 가운데 그대로.
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        doc = self.convert()
        paragraphs = doc.paragraphs
        start = next(i for i, p in enumerate(paragraphs)
                     if p.text.strip() == "요약")
        thanks = next(i for i, p in enumerate(paragraphs)
                      if p.text.strip() == "감사의 글")
        for index in range(start + 1, thanks):
            text = paragraphs[index].text.strip()
            if text:
                self.assertEqual(WD_ALIGN_PARAGRAPH.JUSTIFY,
                                 paragraphs[index].paragraph_format.alignment,
                                 text)
        # 겉표지는 학교 양식대로 가운데 그대로다.
        self.assertEqual(WD_ALIGN_PARAGRAPH.CENTER,
                         paragraphs[0].paragraph_format.alignment)

    def test_summary_and_thanks_titles_are_black_with_the_document_style_untouched(self):
        # 눈 확인(F-9): 제목만 검정. Heading 1 스타일은 다른 곳이 쓰므로 그대로.
        from docx.shared import RGBColor
        doc = self.convert()
        for title in ("요약", "감사의 글"):
            paragraph = next(p for p in doc.paragraphs
                             if p.text.strip() == title)
            self.assertEqual("Heading 1", paragraph.style.name)
            for run in paragraph.runs:
                self.assertEqual(RGBColor(0, 0, 0), run.font.color.rgb, title)
        style = doc.styles["Heading 1"].font.color
        self.assertNotEqual(RGBColor(0, 0, 0), style.rgb)

    def test_contents_lists_summary_and_thanks_before_the_lists(self):
        # 양식 목차 순서: 요약 → 감사의 글 → 표 목차 → 그림 목차 → 본문.
        doc = self.convert()
        texts = [p.text.strip() for p in doc.paragraphs]
        start = next(i for i, t in enumerate(texts) if t.startswith("목")
                     and "차" in t)
        self.assertEqual(
            ["목    차", "요약", "감사의 글", "표 목차", "그림 목차",
             "1. 머리말", "2. 영농환경 분석", "3. 영농계획 수립", "4. 재무계획",
             "맺음말 및 발전방향", "참고문헌", "학부모 동의서"],
            texts[start:start + 12])

    def test_contents_summary_and_thanks_entries_carry_page_references(self):
        doc = self.convert()
        paragraphs = doc.paragraphs
        start = next(i for i, p in enumerate(paragraphs)
                     if p.text.strip().startswith("목") and "차" in p.text)
        expected = {"요약": "gg_summary", "감사의 글": "gg_thanks",
                    "표 목차": "gg_tables", "그림 목차": "gg_figures"}
        for offset, (label, anchor) in enumerate(expected.items(), 1):
            paragraph = paragraphs[start + offset]
            self.assertEqual(label, paragraph.text.strip())
            self.assertIn("PAGEREF " + anchor, paragraph._p.xml)

    def test_dairy_preliminary_prose_keeps_its_previous_alignment(self):
        # 불변: 낙농은 공용 기본값이라 문단 정렬을 지정하지 않는다(예전 그대로).
        profile = self.fm.normalize_school_profile({
            "major_id": "dairy",
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "summary": "합성 요약 내용", "thanks": "합성 감사 내용",
            "parent_consent": "합성 동의 내용"})
        text = "\n\n".join(lines + ["# 1. 머리말", "합성 본문 내용"])
        doc = self.convert("dairy", text=text)
        texts = [p.text.strip() for p in doc.paragraphs]
        index = texts.index("감사의 글")
        self.assertIsNone(doc.paragraphs[index + 1].paragraph_format.alignment)

    # --- HWPX -------------------------------------------------------------

    def test_food_crops_breaks_arabic_chapters_and_closers(self):
        sec = section(
            para("1. 머리말", "1"),
            para("본문 문단이다."),
            para("본문 속 1. 항목", "0"),
            para("2. 영농환경 분석", "1"),
            para("맺음말 및 발전방향", "1"),
            para("참고문헌", "1"),
            para("학부모 동의서", "1"),
        )
        _, chapters = self.hwpx.add_chapter_breaks(sec, major_id="food_crops")
        self.assertEqual(["1. 머리말", "2. 영농환경 분석", "맺음말 및 발전방향",
                          "참고문헌", "학부모 동의서"], chapters)

    def test_default_major_still_breaks_only_roman_chapters(self):
        sec = section(
            para("1. 머리말", "1"),
            para("Ⅰ. 머리말", "1"),
            para("맺음말 및 발전방향", "1"),
        )
        _, chapters = self.hwpx.add_chapter_breaks(sec)
        self.assertEqual(["Ⅰ. 머리말"], chapters)

    def test_apply_layout_with_major_marks_food_crops_chapters(self):
        data, report = self.hwpx.apply_layout(
            hwpx_bytes(section(para("1. 머리말", "1"),
                               para("2. 영농환경 분석", "1"))),
            major_id="food_crops")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertEqual(["1. 머리말", "2. 영농환경 분석"], report["chapter_breaks"])
        self.assertEqual(2, len(re.findall(r'<hp:p\b[^>]*pageBreak="1"', xml)))

    def test_apply_layout_without_major_is_unchanged(self):
        sec = section(para("Ⅰ. 머리말", "1"), para("1. 머리말", "1"))
        data, report = self.hwpx.apply_layout(hwpx_bytes(sec))
        self.assertEqual(["Ⅰ. 머리말"], report["chapter_breaks"])
        again, second = self.hwpx.apply_layout(data)
        self.assertEqual(data, again)
        self.assertEqual([], second["chapter_breaks"])

    def test_export_passes_the_major_into_layout(self):
        root = self.make_project()
        bind_major(root, "food_crops")
        (Path(root) / "body.md").write_text(
            "1. 머리말\n\n합성 본문이다.\n", encoding="utf-8")
        work = Path(root) / "fake"
        work.mkdir()
        control = {"section": kordoc_section(
            kordoc_para("1. 머리말", "1"), kordoc_para("본문이다."),
            kordoc_para("2. 영농환경 분석", "1"))}
        (work / "control.json").write_text(
            json.dumps(control, ensure_ascii=False), encoding="utf-8")
        (work / "fake_kordoc.py").write_text(FAKE_CLI, encoding="utf-8")
        self.hwpx.export(root, "body.md", "out.hwpx",
                         cli_command=[sys.executable, str(work / "fake_kordoc.py")],
                         context=self.mc.output_context(root, "food_crops"))
        manifest = json.loads(
            (Path(root) / "out.hwpx.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(["1. 머리말", "2. 영농환경 분석"],
                         manifest["hwpx"]["layout"]["chapter_breaks"])


if __name__ == "__main__":
    unittest.main()
