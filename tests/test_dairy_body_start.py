"""Dairy preliminary ranges and late Roman chapters on real builder paths."""
from tests._harness import ContractCase, bind_major, runtime


class DairyBodyStartTests(ContractCase):
    def setUp(self):
        self.gd = runtime("gg_document")
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.mc = runtime("gg_major_contract")

    def manuscript(self, first="Ⅰ. 머리말", major="dairy"):
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "합성 동의 문장",
            "summary": "Ⅰ. 사업목적\n\n합성 요약\n\nⅡ. 투자분석\n\n합성 요약",
            "thanks": "합성 감사 문장"})
        return "\n\n".join(lines + [first, "합성 본문", "Ⅺ. 중장기 목표",
                                      "합성 목표", "Ⅻ. 참고문헌", "합성 출처"])

    def convert(self, text):
        root = self.make_project()
        bind_major(root, "dairy")
        return self.builder.convert(text, context=self.mc.output_context(root, "dairy"))

    def test_roman_or_arabic_intro_keeps_lists_in_preliminary_range(self):
        for first in ("Ⅰ. 머리말", "Ⅰ． 머리말", "Ⅰ 머리말", "I. 머리말", "1. 머리말"):
            with self.subTest(first=first):
                nodes = self.gd.parse(self.manuscript(first), major_id="dairy")
                plan = self.gd.school_frontmatter_plan(nodes, "dairy")
                self.assertEqual(first, nodes[plan["body"]]["text"])
                preliminary = [n.get("text") for n in nodes[plan["toc"]:plan["body"]]]
                self.assertIn("표 목차", preliminary)
                self.assertIn("그림 목차", preliminary)
                self.assertEqual((plan["toc"], plan["body"]),
                                 self.gd.school_toc_range(nodes, "dairy"))

    def test_contents_intro_entry_does_not_win_over_actual_body_intro(self):
        text = self.manuscript().replace(
            "목차\n", "목차\n\nⅠ. 머리말\n\nⅡ. 농업환경분석\n", 1)
        nodes = self.gd.parse(text, major_id="dairy")
        plan = self.gd.school_frontmatter_plan(nodes, "dairy")
        self.assertEqual("합성 본문", nodes[plan["body"] + 1]["text"])
        self.assertIn("그림 목차", [n.get("text") for n in nodes[:plan["body"]]])

    def test_acknowledgment_contents_marker_accepts_late_roman_chapter(self):
        for chapter in ("Ⅺ. 중장기 목표", "Ⅻ． 참고문헌", "Ⅺ 중장기 목표"):
            with self.subTest(chapter=chapter):
                text = self.manuscript().split("Ⅰ. 머리말", 1)[0]
                text += "감사의 글\n\n" + chapter + "\n\n합성 본문"
                nodes = self.gd.parse(text, major_id="dairy")
                plan = self.gd.school_frontmatter_plan(nodes, "dairy")
                self.assertEqual(chapter, nodes[plan["body"]]["text"])

    def test_roman_intro_list_titles_render_as_frontmatter_titles(self):
        doc = self.convert(self.manuscript())
        for title in ("표 목차", "그림 목차"):
            with self.subTest(title=title):
                # TOC entries contain a tab/page-reference field; stripping
                # makes their 12pt label look like the standalone page title.
                paragraphs = [p for p in doc.paragraphs if p.text == title]
                self.assertEqual(1, len(paragraphs), "duplicate standalone list title")
                paragraph = paragraphs[0]
                self.assertEqual(22, paragraph.runs[0].font.size.pt)
                self.assertTrue(paragraph.runs[0].bold)
        intro = next(p for p in doc.paragraphs if p.text == "Ⅰ. 머리말")
        self.assertEqual("Heading 1", intro.style.name)
        self.assertFalse(intro.paragraph_format.page_break_before)

    def test_xi_and_xii_are_chapters_and_start_new_docx_pages(self):
        for separator in (". ", "． ", " "):
            with self.subTest(separator=separator):
                text = self.manuscript().replace("Ⅺ. ", "Ⅺ" + separator).replace(
                    "Ⅻ. ", "Ⅻ" + separator)
                nodes = self.gd.parse(text, major_id="dairy")
                titles = ["Ⅺ" + separator + "중장기 목표", "Ⅻ" + separator + "참고문헌"]
                for title in titles:
                    node = next(n for n in nodes if n.get("text") == title)
                    self.assertEqual(("heading", 1), (node["kind"], node["level"]))
                doc = self.convert(text)
                for title in titles:
                    paragraph = next(p for p in doc.paragraphs if p.text == title)
                    self.assertEqual("Heading 1", paragraph.style.name)
                    self.assertTrue(paragraph.paragraph_format.page_break_before)

    def test_xi_and_xii_start_new_hwpx_pages(self):
        builder = runtime("build_hwpx")
        text = self.manuscript()
        titles = ["Ⅺ. 중장기 목표", "Ⅻ. 참고문헌"]
        chapters = builder.chapter_break_texts(text, "dairy")
        self.assertEqual(titles, chapters)
        xml = ('<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
               + ''.join('<hp:p paraPrIDRef="1"><hp:run><hp:t>' + title
                         + '</hp:t></hp:run></hp:p>' for title in titles)
               + '</hp:sec>')
        updated, actual = builder.add_chapter_breaks(
            xml, major_id="dairy", chapter_texts=chapters)
        from xml.etree import ElementTree as ET
        root = ET.fromstring(updated)
        hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
        self.assertEqual(titles, actual)
        self.assertEqual(titles, [''.join(p.itertext()) for p in root.findall(hp + "p")
                                  if p.get("pageBreak") == "1"])

    def test_food_crops_roman_intro_retains_existing_fallback_boundary(self):
        nodes = self.gd.parse(self.manuscript(major="food_crops"), major_id="food_crops")
        plan = self.gd.school_frontmatter_plan(nodes, "food_crops")
        self.assertEqual(plan["toc"] + 1, plan["body"])

    def test_food_crops_arabic_intro_retains_actual_body_boundary(self):
        nodes = self.gd.parse(self.manuscript("1. 머리말", "food_crops"),
                              major_id="food_crops")
        plan = self.gd.school_frontmatter_plan(nodes, "food_crops")
        self.assertEqual("1. 머리말", nodes[plan["body"]]["text"])
