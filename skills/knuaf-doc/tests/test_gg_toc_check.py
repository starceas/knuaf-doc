import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gg_core  # noqa: E402
import gg_frontmatter_layout as fl  # noqa: E402
import gg_toc_check  # noqa: E402

ENTRIES = [("Ⅰ. 서론", "gg_body_1", 1), ("1. 배경", "gg_body_2", 2)]


def _document():
    document = Document()
    fl.contents(document, ENTRIES, "바탕")
    return document


def _refresh(document, start=1):
    for number, field in enumerate(
            document.element.body.iter(qn("w:fldSimple")), start):
        for text in field.iter(qn("w:t")):
            text.text = str(number)


def _page_reference(paragraph, bookmark, result):
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGEREF " + bookmark + " \\h")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = result
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def _type_number(paragraph, number):
    """Replace the entry's field with a hand-typed page number."""
    for field in list(paragraph._p.findall(qn("w:fldSimple"))):
        paragraph._p.remove(field)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = number
    run.append(text)
    paragraph._p.append(run)


class TocCheckTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.folder = Path(self._temp.name)

    def save(self, document, name="paper.docx"):
        path = self.folder / name
        document.save(path)
        return path

    def finding(self, document):
        path = self.save(document)
        result = gg_toc_check.check(path)
        self.assertEqual(len(result["findings"]), 1)
        return result["findings"][0]

    def test_refreshed_field_numbers_pass(self):
        document = _document()
        _refresh(document)
        fl.contents(document, [("표 1 가상 표", "gg_body_3", 2)],
                   "바탕", "표 목차", "gg_tables", 9002)
        _refresh(document)
        finding = self.finding(document)
        self.assertEqual(finding["id"], "TOC-PAGE-NUMBERS")
        self.assertEqual(finding["status"], "pass")
        self.assertEqual(finding["items"], [])

    def test_hand_typed_page_number_fails(self):
        document = _document()
        _refresh(document)
        _type_number(document.paragraphs[1], "1")
        finding = self.finding(document)
        self.assertEqual(finding["status"], "fail")
        self.assertEqual([item["reason"] for item in finding["items"]],
                         ["hand_typed_page_number"])
        self.assertEqual(finding["items"][0]["value"], "1")

    def test_hand_typed_roman_number_fails(self):
        document = _document()
        _refresh(document)
        _type_number(document.paragraphs[2], "iv")
        finding = self.finding(document)
        self.assertEqual([item["reason"] for item in finding["items"]],
                         ["hand_typed_page_number"])

    def test_unrefreshed_field_fails(self):
        finding = self.finding(_document())
        self.assertEqual(finding["status"], "fail")
        self.assertEqual({item["reason"] for item in finding["items"]},
                         {"field_not_refreshed"})
        self.assertEqual(len(finding["items"]), len(ENTRIES))

    def test_entry_naming_a_sub_list_does_not_split_the_area(self):
        document = Document()
        fl.contents(document, [("요약", "gg_summary", 0),
                               ("표 목차", "gg_tables", 0),
                               ("Ⅰ. 서론", "gg_body_1", 1)], "바탕")
        finding = self.finding(document)
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(len(finding["items"]), 3)

    def test_word_complex_field_numbers_pass(self):
        document = Document()
        document.add_paragraph("목    차")
        paragraph = document.add_paragraph()
        paragraph.add_run("Ⅰ. 서론")
        paragraph.add_run("\t")
        for kind, text in (("begin", None),
                           ("instr", " PAGEREF _Toc1 \\h "),
                           ("separate", None),
                           ("result", "3"),
                           ("end", None)):
            run = OxmlElement("w:r")
            if kind == "instr":
                element = OxmlElement("w:instrText")
                element.text = text
            elif kind == "result":
                element = OxmlElement("w:t")
                element.text = text
            else:
                element = OxmlElement("w:fldChar")
                element.set(qn("w:fldCharType"), kind)
            run.append(element)
            paragraph._p.append(run)
        document.add_page_break()
        finding = self.finding(document)
        self.assertEqual(finding["status"], "pass")

    def test_body_cross_reference_is_not_a_toc_item(self):
        document = _document()
        _refresh(document)
        document.add_page_break()
        paragraph = document.add_paragraph("표 1 참조\t")
        _page_reference(paragraph, "gg_body_9", "?")
        finding = self.finding(document)
        self.assertEqual(finding["status"], "pass")

    def test_document_without_toc_has_no_finding(self):
        document = Document()
        document.add_paragraph("본문만 있음")
        path = self.save(document)
        self.assertEqual(gg_toc_check.check(path)["findings"], [])

    def test_empty_toc_area_fails(self):
        document = Document()
        document.add_paragraph("목    차")
        document.add_page_break()
        finding = self.finding(document)
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(finding["items"], [{"para": "",
                                             "reason": "toc_empty"}])

    def test_break_variants_check_content_before_closing_the_toc_area(self):
        for variant in ("separate_page", "inline_page", "section", "hand_typed_last"):
            with self.subTest(variant=variant):
                document = Document()
                fl.contents(document, [("Ⅰ. 가상 장", "gg_body_1", 1)], "바탕")
                _refresh(document)
                entry = document.paragraphs[-1]
                if variant == "separate_page":
                    document.add_page_break()
                elif variant == "section":
                    entry._p.get_or_add_pPr().append(OxmlElement("w:sectPr"))
                else:
                    if variant == "hand_typed_last":
                        entry = document.add_paragraph("Ⅱ. 다음 장\t7")
                    entry.add_run().add_break(WD_BREAK.PAGE)
                body = document.add_paragraph("본문 교차참조\t")
                _page_reference(body, "gg_body_9", "?")
                finding = self.finding(document)
                self.assertEqual(finding["status"],
                                 "fail" if variant == "hand_typed_last" else "pass")
                self.assertEqual(
                    [item["reason"] for item in finding["items"]],
                    ["hand_typed_page_number"] if variant == "hand_typed_last" else [])
                if variant == "hand_typed_last":
                    self.assertEqual(finding["items"][0]["value"], "7")

    def test_page_break_inside_run_excludes_following_body_content(self):
        document = Document()
        fl.contents(document, [("Ⅰ. 가상 장", "gg_body_1", 1)], "바탕")
        _refresh(document)
        entry = document.paragraphs[-1]
        entry.add_run().add_break(WD_BREAK.PAGE)
        entry.add_run("본문 교차참조\t")
        _page_reference(entry, "gg_body_9", "?")
        finding = self.finding(document)
        self.assertEqual((finding["status"], finding["items"]), ("pass", []))

    def test_title_with_inline_page_break_is_still_an_empty_toc(self):
        document = Document()
        title = document.add_paragraph("목    차")
        title.add_run().add_break(WD_BREAK.PAGE)
        body = document.add_paragraph("본문 교차참조\t")
        _page_reference(body, "gg_body_9", "1")
        finding = self.finding(document)
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(finding["items"], [{"para": "", "reason": "toc_empty"}])

    def test_evidence_json_matches_docx_hash(self):
        document = _document()
        _refresh(document)
        path = self.save(document)
        evidence = self.folder / "render-evidence.json"
        code = gg_toc_check.main([str(path), "--json", str(evidence)])
        self.assertEqual(code, 0)
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(payload["docx_sha256"],
                         hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(payload["findings"][0]["status"], "pass")

    def test_fail_evidence_blocks_render_review(self):
        evidence = self.folder / "render-evidence.json"
        path = self.save(_document())
        self.assertEqual(
            gg_toc_check.main([str(path), "--json", str(evidence)]), 2)
        self.assertIn("render", gg_core.visual_review_blocks(evidence))

    def test_pass_evidence_does_not_block_render_review(self):
        document = _document()
        _refresh(document)
        evidence = self.folder / "render-evidence.json"
        path = self.save(document)
        self.assertEqual(
            gg_toc_check.main([str(path), "--json", str(evidence)]), 0)
        self.assertNotIn("render", gg_core.visual_review_blocks(evidence))


    def spanning_toc_with_hand_typed_page(self, target_index):
        document = Document()
        document.add_paragraph("목    차")

        def append(paragraph, tag, value=None, attribute=None):
            run = OxmlElement("w:r")
            element = OxmlElement("w:" + tag)
            if value is not None:
                element.text = value
            if attribute:
                name, content = attribute
                element.set(qn("w:" + name), content)
            run.append(element)
            paragraph._p.append(run)

        entries = []
        for index, title in enumerate(("Ⅰ. 서론", "1. 배경", "2. 방법")):
            paragraph = document.add_paragraph()
            entries.append(paragraph)
            if index == 0:
                append(paragraph, "fldChar", attribute=("fldCharType", "begin"))
                append(paragraph, "instrText", ' TOC \\o "1-9" \\h ')
                append(paragraph, "fldChar",
                       attribute=("fldCharType", "separate"))
            append(paragraph, "t", title)
            append(paragraph, "tab")
            if index == target_index:
                append(paragraph, "t", "7")
            else:
                append(paragraph, "fldChar",
                       attribute=("fldCharType", "begin"))
                append(paragraph, "instrText",
                       f" PAGEREF _Toc{index + 1} \\h ")
                append(paragraph, "fldChar",
                       attribute=("fldCharType", "separate"))
                append(paragraph, "t", str(index + 2))
                append(paragraph, "fldChar",
                       attribute=("fldCharType", "end"))
        append(entries[-1], "fldChar", attribute=("fldCharType", "end"))
        document.add_page_break()
        return document

    def test_spanning_toc_first_entry_hand_typed_page_fails(self):
        finding = self.finding(self.spanning_toc_with_hand_typed_page(0))
        self.assertEqual(finding["status"], "fail")
        self.assertIn("hand_typed_page_number",
                      {item["reason"] for item in finding["items"]})

    def test_spanning_toc_middle_entry_hand_typed_page_fails(self):
        finding = self.finding(self.spanning_toc_with_hand_typed_page(1))
        self.assertEqual(finding["status"], "fail")
        self.assertIn("hand_typed_page_number",
                      {item["reason"] for item in finding["items"]})

    def test_spanning_toc_last_entry_hand_typed_page_fails(self):
        finding = self.finding(self.spanning_toc_with_hand_typed_page(2))
        self.assertEqual(finding["status"], "fail")
        self.assertEqual([item["reason"] for item in finding["items"]],
                         ["hand_typed_page_number"])
        self.assertEqual(finding["items"][0]["value"], "7")

    def test_unrelated_page_field_does_not_protect_a_hand_typed_tail(self):
        document = Document()
        document.add_paragraph("목    차")
        entry = document.add_paragraph("Ⅰ. 가상 장 ")
        _page_reference(entry, "gg_body_1", "1")
        entry.add_run("\t7")
        document.add_page_break()
        finding = self.finding(document)
        self.assertEqual(finding["status"], "fail")
        self.assertEqual([item["reason"] for item in finding["items"]],
                         ["hand_typed_page_number"])
        self.assertEqual(finding["items"][0]["value"], "7")

    def test_simple_toc_cached_result_is_not_a_hand_typed_number(self):
        document = Document()
        document.add_paragraph("목    차")
        entry = document.add_paragraph()
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), " TOC \\h ")
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "Ⅰ. 가상 장\t1"
        run.append(text)
        field.append(run)
        entry._p.append(field)
        document.add_page_break()
        finding = self.finding(document)
        self.assertEqual((finding["status"], finding["items"]), ("pass", []))

    def test_nested_hyperlink_title_is_part_of_the_refreshed_toc_result(self):
        for title_inside in (False, True):
            for page in ("1", "?"):
                for simple_page in (False, True):
                    with self.subTest(title_inside=title_inside, page=page,
                                      simple_page=simple_page):
                        document = Document()
                        document.add_paragraph("목    차")
                        entry = document.add_paragraph()

                        def append(tag, value=None, **attributes):
                            run = OxmlElement("w:r")
                            element = OxmlElement("w:" + tag)
                            element.text = value
                            for key, content in attributes.items():
                                element.set(qn("w:" + key), content)
                            run.append(element)
                            entry._p.append(run)

                        append("fldChar", fldCharType="begin")
                        append("instrText", " TOC \\h ")
                        append("fldChar", fldCharType="separate")
                        if not title_inside:
                            append("t", "Ⅰ. 가상 장")
                            append("tab")
                        append("fldChar", fldCharType="begin")
                        append("instrText", " HYPERLINK \\l gg_body_1 ")
                        append("fldChar", fldCharType="separate")
                        if title_inside:
                            append("t", "Ⅰ. 가상 장")
                            append("tab")
                        if simple_page:
                            _page_reference(entry, "gg_body_1", page)
                        else:
                            append("fldChar", fldCharType="begin")
                            append("instrText", " PAGEREF gg_body_1 \\h ")
                            append("fldChar", fldCharType="separate")
                            append("t", page)
                            append("fldChar", fldCharType="end")
                        append("fldChar", fldCharType="end")
                        append("fldChar", fldCharType="end")
                        document.add_page_break()
                        finding = self.finding(document)
                        self.assertEqual(finding["status"], "pass" if page == "1" else "fail")
                        self.assertEqual(
                            [item["reason"] for item in finding["items"]],
                            [] if page == "1" else ["field_not_refreshed"])


    def test_word_toc_spans_paragraphs_and_page_refs_are_distinguished(self):
        document = Document()
        document.add_paragraph("목    차")

        def field_char(paragraph, kind):
            run = OxmlElement("w:r")
            char = OxmlElement("w:fldChar")
            char.set(qn("w:fldCharType"), kind)
            run.append(char)
            paragraph._p.append(run)

        def instruction(paragraph, text):
            run = OxmlElement("w:r")
            node = OxmlElement("w:instrText")
            node.text = text
            run.append(node)
            paragraph._p.append(run)

        def text(paragraph, value):
            run = OxmlElement("w:r")
            node = OxmlElement("w:t")
            node.text = value
            run.append(node)
            paragraph._p.append(run)

        first = document.add_paragraph()
        field_char(first, "begin")
        instruction(first, ' TOC \\o "1-9" \\h \\z \\u ')
        field_char(first, "separate")
        text(first, "Ⅰ. 서론")
        text(first, "\t")
        field_char(first, "begin")
        instruction(first, " HYPERLINK \\l _Toc1 ")
        field_char(first, "separate")
        field_char(first, "begin")
        instruction(first, " PAGEREF _Toc1 \\h ")
        field_char(first, "separate")
        text(first, "2")
        field_char(first, "end")
        field_char(first, "end")

        second = document.add_paragraph()
        text(second, "1. 배경")
        text(second, "\t")
        field_char(second, "begin")
        instruction(second, " HYPERLINK \\l _Toc2 ")
        field_char(second, "separate")
        field_char(second, "begin")
        instruction(second, " PAGEREF _Toc2 \\h ")
        field_char(second, "separate")
        text(second, "8")
        field_char(second, "end")
        field_char(second, "end")
        field_char(second, "end")
        document.add_page_break()

        finding = self.finding(document)
        self.assertEqual(finding["status"], "pass")

        hand_typed = Document()
        hand_typed.add_paragraph("목    차")
        entry = hand_typed.add_paragraph()
        field_char(entry, "begin")
        instruction(entry, ' TOC \\o "1-9" \\h ')
        field_char(entry, "separate")
        text(entry, "Ⅰ. 서론")
        text(entry, "\t")
        text(entry, "2")
        end = hand_typed.add_paragraph()
        field_char(end, "end")
        hand_finding = self.finding(hand_typed)
        self.assertEqual(hand_finding["status"], "fail")
        self.assertEqual(
            [item["reason"] for item in hand_finding["items"]],
            ["hand_typed_page_number"])


if __name__ == "__main__":
    unittest.main()
