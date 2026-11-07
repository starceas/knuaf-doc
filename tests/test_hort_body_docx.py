"""원예 본문 DOCX 조립 시험.

합성 원고(가상 학생)를 학교 서식으로 조립했을 때 원예 앞머리 순서(두 표지·
제출·인준·목차), HT2 목차 순서와 쪽 필드, 표 캡션 위치, 절별 재무 자리 표시,
부모님 동의서 미생성, 다른 전공 표기 차단, 3단계 SWOT 진단을 확인한다.
선배 논문 본문·실명은 쓰지 않는다.

목차·재무·앞머리 검사는 문서 전체 문자열이 아니라 목차 구역·절 구역·쪽 경계를
직접 대상으로 한다. 본문 어딘가의 같은 문자열이나 총개수로 조건을 만족하지
못하게 하기 위해서다.
"""
import re
import unittest

from tests._harness import ContractCase, bind_major, runtime

FINANCE = "[확인 필요 — 엑셀 작성 후]"
MONEY = re.compile(r"\d[\d,\.]*\s*(?:원|천원|만원|억)")

# HT2 목차(권장 소절 순서 포함)의 장·절 제목. 앞의 세 자리는 절 단계,
# '4. SWOT 분석'은 Ⅲ-4 한 곳에서만 쓴다.
CHAPTERS = (
    "Ⅰ. 머리말",
    "Ⅱ. 농장현황",
    "1. 농장개요",
    "2. 생산품목",
    "3. 생산기술 및 재배방법",
    "Ⅲ. 영농환경 분석",
    "1. 내부역량 분석",
    "2. 외부환경 분석",
    "3. 모델농장 벤치마킹 분석",
    "4. SWOT 분석",
    "Ⅳ. 농장비전 및 영농목표",
    "1. 농장비전",
    "2. 영농 목표",
    "3. 농장 경영전략",
    "Ⅴ. 영농계획수립",
    "1. 투자계획 및 원리금 상환계획",
    "2. 시설계획",
    "3. 생산계획",
    "4. 생산원가계획",
    "5. 마케팅계획",
    "6. 교육 및 봉사계획",
    "Ⅵ. 맺음말",
)

# 재무 수치가 들어갈 절. 엑셀 작성 전에는 절마다 자리 표시 하나만 둔다.
FINANCE_SLOTS = (
    "1. 내부역량 분석",
    "2. 영농 목표",
    "1. 투자계획 및 원리금 상환계획",
    "3. 생산계획",
    "4. 생산원가계획",
    "5. 마케팅계획",
)

SUMMARY_FINANCE = "사업 규모는 " + FINANCE + "로 둔다."

# HT2 부록 1–18. 엑셀 사실 등록 전에는 각 부록이 자리 표시 하나로만 선다.
APPENDICES = (
    "부록 1. 자산조사",
    "부록 2. 중장기 영농목표",
    "부록 3. 모델농장분석",
    "부록 4. 투자계획",
    "부록 5. 원리금 상환계획",
    "부록 6. 시설계획",
    "부록 7. 판매계획",
    "부록 8. 생산계획",
    "부록 9. 영농자재 구매계획",
    "부록 10. 작부체계",
    "부록 11. 인건비 계획",
    "부록 12. 경비계획",
    "부록 13. 감가상각비 계획",
    "부록 14. 생산원가계획",
    "부록 15. 손익계획",
    "부록 16. 수익성 분석",
    "부록 17. 추정대차대조표",
    "부록 18. 현금흐름계획",
)

BODY = {heading: "합성 " + heading + " 문단이다." for heading in CHAPTERS}
for heading in FINANCE_SLOTS:
    BODY[heading] = BODY[heading] + " 수치는 " + FINANCE + "로 둔다."

TABLE_CAPTION = "표 1. 합성 점검 항목"
TABLE = (
    "| 구분 | 관리 항목 | 목표 범위 |",
    "|---|---|---|",
    "| 온도 | 주간 온도 | 22~25 ℃ |",
    "| 습도 | 상대 습도 | 60~75 % |",
)

PHOTO_GUIDE = "[사진 필요: PHOTO-001 | 합성 농장 전경]"
FIGURE_CAPTION = "그림 1. 합성 재배 도해"

# 목차 첫 구역에 실려야 하는 항목. 그림 캡션이 하나 있으므로 그림 목차도
# 표 목차 바로 뒤에 온다(HT2 순서). 후미 제목도 목차에 실린다.
# HT2는 부록을 목차 한 줄로만 싣고, 부록 개별 제목은 목차에 넣지 않는다.
BACK_MATTER = ("참고문헌", "감사의 글", "부록")
CONTENTS = ["요약", "표 목차", "그림 목차"] + list(CHAPTERS) + list(BACK_MATTER)


def manuscript(fm, major="hort_env_systems", **metadata):
    profile = fm.normalize_school_profile({
        "major_id": major,
        "school_profile": {
            "title": "합성 시설원예 계획", "author": "가상학생",
            "advisor": "가상지도교수",
            "submission_date": "2026년 12월",
            "graduation_date": "2027년 2월", **metadata}})
    lines = fm.frontmatter_lines(profile, artifacts={
        "summary": "합성 요약 내용", "thanks": "합성 감사 내용"})
    out = list(lines) + ["", "요약", "", "합성 요약 내용. " + SUMMARY_FINANCE,
                         "", "표 목차", "", "그림 목차"]
    for heading in CHAPTERS:
        out += ["", heading, "", BODY[heading]]
        if heading == "1. 농장개요":
            out += ["", PHOTO_GUIDE, "", FIGURE_CAPTION]
        if heading == "3. 생산기술 및 재배방법":
            out += ["", TABLE_CAPTION, ""] + list(TABLE)
    out += ["", "# 참고문헌", "", "가상기관. 합성 서지. 2024.",
            "", "# 감사의 글", "", "합성 감사 문단이다.", "", "# 부록"]
    for title in APPENDICES:
        out += ["", "### " + title, "", FINANCE]
    return "\n".join(out)


class HortBodyDocxTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.gd = runtime("gg_document")
        self.builder = runtime("build_docx")
        self.mc = runtime("gg_major_contract")

    def convert(self, text=None, major="hort_env_systems"):
        root = self.make_project()
        bind_major(root, major)
        doc = self.builder.convert(
            text or manuscript(self.fm),
            context=self.mc.output_context(root, major))
        output = root / "paper.docx"
        doc.save(output)
        return self.builder.Document(output)

    def pages(self, doc):
        """문서를 실제 쪽 경계로 나눈다.

        문단 안의 명시적 쪽 나눔은 개수만큼, ``page_break_before``는 문단
        앞에서, 구역 나눔(``w:pPr/w:sectPr``)은 문단 뒤에서 새 쪽을 연다.
        한 문단에 나눔이 둘이면 두 쪽으로 갈라진다.
        """
        from docx.oxml.ns import qn
        pages = [[]]
        for paragraph in doc.paragraphs:
            if paragraph.paragraph_format.page_break_before and pages[-1]:
                pages.append([])
            pages[-1].append(paragraph.text)
            breaks = sum(1 for b in paragraph._p.iter(qn("w:br"))
                         if b.get(qn("w:type")) == "page")
            section = paragraph._p.find(qn("w:pPr") + "/" + qn("w:sectPr")) is not None
            for _ in range(breaks + (1 if section else 0)):
                pages.append([])
        return ["\n".join(line for line in page if line.strip()) for page in pages]

    def contents_entries(self, doc):
        """목차 구역의 항목만, 쪽 필드(PAGEREF)가 있는 문단에서 읽는다."""
        from docx.oxml.ns import qn
        started, entries = False, []
        for paragraph in doc.paragraphs:
            if not started:
                if paragraph.text.strip() == "목    차":
                    started = True
                continue
            if paragraph._p.find(qn("w:pPr") + "/" + qn("w:sectPr")) is not None:
                break
            if any("PAGEREF" in (field.get(qn("w:instr")) or "")
                   for field in paragraph._p.iter(qn("w:fldSimple"))):
                entries.append(paragraph.text.strip())
        return entries

    def sections(self, doc):
        """제목별 본문 문단(제목 다음부터 다음 제목 전까지)."""
        sections, current = {}, None
        for paragraph in doc.paragraphs:
            style = paragraph.style.name if paragraph.style is not None else ""
            text = paragraph.text.strip()
            if style.startswith("Heading") and text:
                current = text
                sections.setdefault(current, [])
            elif current is not None:
                sections[current].append(paragraph.text)
        return sections

    def test_frontmatter_is_two_covers_then_submission_approval_contents(self):
        pages = self.pages(self.convert())
        self.assertGreaterEqual(len(pages), 6)
        for page in pages[:2]:
            self.assertIn("농업전문학사 학위논문", page)
            self.assertIn("원예환경시스템전공", page)
            self.assertNotIn("지도교수", page)
        self.assertIn("신청서·추천서는 학교 서식으로 따로 제출", pages[0])
        self.assertIn("지도교수 가상지도교수", pages[2])
        self.assertIn("이 논문을 농업전문학사 학위논문으로 제출함", pages[2])
        self.assertIn("인준함", pages[3])
        self.assertEqual(pages[3].count("(인)"), 3)
        self.assertIn("목    차", pages[4])
        self.assertTrue(pages[5].startswith("요약"), pages[5][:40])
        joined = "\n".join(pages)
        self.assertNotIn("부모님 동의서", joined)
        self.assertNotIn("동의서", joined)
        self.assertNotIn("보호자", joined)

    def test_contents_lists_ht2_order_with_page_fields(self):
        doc = self.convert()
        self.assertEqual(self.contents_entries(doc), CONTENTS)
        levels = {}
        for paragraph in doc.paragraphs:
            style = paragraph.style.name if paragraph.style is not None else ""
            text = paragraph.text.strip()
            if style.startswith("Heading") and text:
                levels[text] = int(style.split()[-1])
        for heading in CHAPTERS + BACK_MATTER:
            with self.subTest(heading=heading):
                expected = 1 if heading[0] in "ⅠⅡⅢⅣⅤⅥ" or heading in BACK_MATTER else 2
                self.assertEqual(levels.get(heading), expected)

    def test_table_caption_prints_directly_above_its_table(self):
        from docx.text.paragraph import Paragraph

        doc = self.convert()
        self.assertEqual(len(doc.tables), 1)
        order, captions = [], 0
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = Paragraph(child, doc).text
                order.append(text)
                captions += text == TABLE_CAPTION
            elif tag == "tbl":
                order.append("<table>")
        self.assertEqual(captions, 1)
        table = order.index("<table>")
        previous = [text for text in order[:table] if text.strip()]
        self.assertEqual(previous[-1], TABLE_CAPTION)

    def test_finance_slots_stay_marked_in_each_section(self):
        doc = self.convert()
        sections = self.sections(doc)
        for heading in FINANCE_SLOTS:
            with self.subTest(section=heading):
                body = "\n".join(sections[heading])
                self.assertEqual(body.count(FINANCE), 1)
                self.assertEqual(MONEY.findall(body), [])
        summary = next(page for page in self.pages(doc)
                       if page.startswith("요약"))
        self.assertEqual(summary.count(FINANCE), 1)
        self.assertEqual(MONEY.findall(summary), [])
        for back in ("참고문헌", "감사의 글"):
            with self.subTest(section=back):
                self.assertNotIn(FINANCE, "\n".join(sections[back]))
        self.assertEqual(
            sum(text.count(FINANCE) for text in
                (p.text for p in doc.paragraphs)),
            len(FINANCE_SLOTS) + 1 + len(APPENDICES))

    def test_appendix_titles_and_placeholders_are_assembled(self):
        doc = self.convert()
        sections = self.sections(doc)
        headings = [paragraph.text.strip() for paragraph in doc.paragraphs
                    if paragraph.style is not None
                    and paragraph.style.name.startswith("Heading")]
        appendix_headings = [text for text in headings
                             if re.fullmatch(r"부록 \d+\. .+", text)]
        self.assertEqual(tuple(appendix_headings), APPENDICES)
        for title in APPENDICES:
            with self.subTest(appendix=title):
                body = "\n".join(sections[title])
                self.assertEqual(body.count(FINANCE), 1)
                self.assertEqual(MONEY.findall(body), [])
        self.assertEqual(headings.count("부록"), 1)
        entries = self.contents_entries(doc)
        self.assertEqual(entries[-1], "부록")
        self.assertEqual([entry for entry in entries
                          if re.fullmatch(r"부록 \d+\. .+", entry)], [])

    def test_another_major_marker_is_held(self):
        text = manuscript(self.fm, department="원예학부 과수전공")
        root = self.make_project()
        bind_major(root, "hort_env_systems")
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.builder.convert(
                text, context=self.mc.output_context(root, "hort_env_systems"))
        self.assertEqual(caught.exception.reason, "major_marker_conflict")

    def test_swot_heading_below_section_level_is_reported(self):
        text = manuscript(self.fm).replace(
            "\n4. SWOT 분석\n", "\n4. SWOT 분석\n\n가. SWOT 비교\n")
        doc = self.convert(text)
        levels = {paragraph.text.strip(): paragraph.style.name
                  for paragraph in doc.paragraphs
                  if paragraph.style is not None}
        self.assertEqual(levels.get("가. SWOT 비교"), "Heading 3")
        issues = self.gd.check(text, None, "hort_env_systems")
        self.assertEqual([i for i in issues if i[0] == "school_swot"],
                         [("school_swot", "선택 전공 목차의 SWOT는 Ⅲ-4")])


if __name__ == "__main__":
    unittest.main()
