"""원예 본문 DOCX 조립 시험.

합성 원고(가상 학생)를 학교 서식으로 조립했을 때 앞머리 순서·목차 순서·
표 캡션 위치·재무 자리 표시가 HT2 기준대로 나오는지, 다른 전공 표기가 섞인
원고와 3단계 SWOT 제목이 막히는지 확인한다. 선배 논문 본문·실명은 쓰지 않는다.
"""
import unittest

from tests._harness import ContractCase, bind_major, runtime

FINANCE = "[확인 필요 — 엑셀 작성 후]"

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

# 재무 수치가 들어갈 자리. 엑셀 작성 전에는 숫자를 쓰지 않는다.
FINANCE_SLOTS = (
    "1. 내부역량 분석",
    "2. 영농 목표",
    "1. 투자계획 및 원리금 상환계획",
    "3. 생산계획",
    "4. 생산원가계획",
    "5. 마케팅계획",
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
    out = list(lines) + ["", "요약", "", "합성 요약 내용", "", "표 목차", "", "그림 목차"]
    for heading in CHAPTERS:
        out += ["", heading, "", BODY[heading]]
        if heading == "3. 생산기술 및 재배방법":
            out += ["", TABLE_CAPTION, ""] + list(TABLE)
    out += ["", "# 참고문헌", "", "가상기관. 합성 서지. 2024.",
            "", "# 감사의 글", "", "합성 감사 문단이다."]
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
        from docx.oxml.ns import qn
        pages = [[]]
        for paragraph in doc.paragraphs:
            pages[-1].append(paragraph.text)
            if any(b.get(qn("w:type")) == "page" for b in paragraph._p.iter(qn("w:br"))):
                pages.append([])
        return ["\n".join(page) for page in pages]

    def test_frontmatter_is_two_covers_then_submission_approval_contents(self):
        pages = self.pages(self.convert())
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

    def test_contents_follow_ht2_order(self):
        text = "\n".join(self.pages(self.convert()))
        entries = ["요약", "표 목차"] + list(CHAPTERS)
        positions = [text.index(entry) for entry in entries]
        self.assertEqual(positions, sorted(positions))

    def test_table_caption_prints_above_its_table(self):
        from docx.text.paragraph import Paragraph

        doc = self.convert()
        self.assertEqual(len(doc.tables), 1)
        order = []
        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                order.append(Paragraph(child, doc).text)
            elif tag == "tbl":
                order.append("<table>")
        self.assertLess(order.index(TABLE_CAPTION), order.index("<table>"))

    def test_finance_slots_stay_marked_until_workbook_exists(self):
        text = "\n".join(self.pages(self.convert()))
        self.assertEqual(text.count(FINANCE), len(FINANCE_SLOTS))

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
        issues = self.gd.check(text, None, "hort_env_systems")
        self.assertEqual([i for i in issues if i[0] == "school_swot"],
                         [("school_swot", "선택 전공 목차의 SWOT는 Ⅲ-4")])


if __name__ == "__main__":
    unittest.main()
