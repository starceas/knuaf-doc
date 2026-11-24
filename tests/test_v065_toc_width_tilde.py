"""규칙집 v0.6.5 — 목차 폭 되돌리기(12번) + 범위 물결 가운데(4번).

잠그는 것:
- 목차 항목(내어쓰기 켠 전공)은 왼쪽 내어쓰기는 유지하되 오른쪽 여백을 걸지 않는다.
  쪽번호 오른쪽 탭(RIGHT·점선 리더·PAGEREF 필드)은 본문 폭(150 mm)과 같다.
  (이전: right_indent 14 mm가 오른쪽을 깎아 목차가 가운데로 좁아져 보였다.)
- 동적 목차 스타일 TOC 1~3도 오른쪽 여백이 없다.
- HWPX: kordoc에 넘기는 마크다운은 목차 구간 문단 폭을 좁히는 표기를 갖지 않는다
  (한글 쪽 빌더는 목차 항목 줄을 만들지 않으므로 마크다운 불변식으로 한정).
- 범위 물결(숫자~숫자·부호·% 포함)만 가운데 물결(U+223C)로 바꾼다. 변환 층은
  원고 마크다운이 아니라 **완성된 문서의 표시 글자**(DOCX w:t, HWPX hp:t)다:
  그림 경로·링크 대상·주소·코드·원문 해시는 그대로이고, 원문 글자와 출력 글자를
  비교하는 곳(장 제목 새 쪽·표 수치)은 같은 규칙으로 맞춰 비교한다.

가짜 원고·가짜 이름만 쓴다.
"""

import base64
import hashlib
import unittest

from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER

from tests._harness import ContractCase, bind_major, runtime

BODY = (
    "# Ⅰ. 서론\n"
    "## 1. 배경\n"
    "재배 면적은 3~5 ha이고 수확량은 10 ~ 12 톤이다.\n\n"
    "표 1. 합성 계획의 전제와 확정 순서가 한 줄에 다 들어가지 않을 만큼 길게 이어지는 설명 글\n"
    "| 연도 | 면적 |\n"
    "|--|--|\n"
    "| 2027 | 100 |\n"
    "(출처: 합성 자료.)\n\n"
    "그림 1. 합성 배치도.\n"
)

TILDE = "~"
CT = "∼"          # 가운데 물결
BODY_WIDTH_MM = 150.0

# 1x1 PNG(그림 경로 시험용 실물).
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class _Docx(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.mc = runtime("gg_major_contract")
        self.gd = runtime("gg_document")

    def convert(self, major, body=BODY, root=None, base="."):
        root = root or self.make_project()
        bind_major(root, major)
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "summary": "합성 요약", "thanks": "합성 감사"})
        text = "\n\n".join(lines) + "\n\n" + body
        doc = self.builder.convert(
            text, base=base, root=root, context=self.mc.output_context(root, major))
        return text, doc

    @staticmethod
    def toc_entries(doc, startswith):
        return [p for p in doc.paragraphs
                if p.text.startswith(startswith) and "\t" in p.text]


class TocWidthTests(_Docx):
    def test_specialty_toc_entry_has_no_right_indent(self):
        _, doc = self.convert("specialty_crops")
        entries = self.toc_entries(doc, "표 1.")
        self.assertTrue(entries)
        for entry in entries:
            right = entry.paragraph_format.right_indent
            self.assertTrue(right is None or right.mm == 0,
                            "목차 항목 오른쪽 여백이 본문 폭을 깎는다: %r" % (right,))

    def test_specialty_toc_entry_keeps_hanging_and_right_dotted_tab_at_body_width(self):
        _, doc = self.convert("specialty_crops")
        entry = self.toc_entries(doc, "표 1.")[0]
        fmt = entry.paragraph_format
        # 내어쓰기(왼쪽)는 유지한다.
        self.assertLess(fmt.first_line_indent.mm, 0)
        self.assertGreater(fmt.left_indent.mm, 0)
        section = doc.sections[-1]
        body_width = (section.page_width.mm - section.left_margin.mm
                      - section.right_margin.mm)
        self.assertAlmostEqual(body_width, BODY_WIDTH_MM, delta=0.5)
        stops = list(fmt.tab_stops)
        self.assertEqual(len(stops), 1)
        # 쪽번호는 오른쪽 정렬 탭 + 점선 리더 + 쪽번호 필드(PAGEREF)로 붙는다.
        self.assertEqual(stops[0].alignment, WD_TAB_ALIGNMENT.RIGHT)
        self.assertEqual(stops[0].leader, WD_TAB_LEADER.DOTS)
        self.assertAlmostEqual(stops[0].position.mm, body_width, delta=0.5)
        self.assertIn("PAGEREF", entry._p.xml)
        right = fmt.right_indent.mm if fmt.right_indent is not None else 0.0
        self.assertLessEqual(stops[0].position.mm, body_width - right + 0.5)

    def test_layout_contents_hanging_has_no_right_indent(self):
        from docx import Document
        layout = runtime("gg_frontmatter_layout")
        long_title = "합성 제목이 한 줄에 다 들어가지 않을 만큼 길게 이어지는 설명 글"
        entries = [("표 1. " + long_title, "bm0", 2), ("1. 요약 항목", "bm1", 1)]
        doc = Document()
        layout.contents(doc, entries, "신명조", "표 목차", "gg_tables", 9002,
                        hanging=True)
        found = [p for p in doc.paragraphs if "\t" in p.text]
        self.assertEqual(len(found), len(entries))
        for para in found:
            right = para.paragraph_format.right_indent
            self.assertTrue(right is None or right.mm == 0, para.text)
            stops = list(para.paragraph_format.tab_stops)
            self.assertEqual(len(stops), 1, para.text)
            self.assertEqual(stops[0].alignment, WD_TAB_ALIGNMENT.RIGHT)
            self.assertEqual(stops[0].leader, WD_TAB_LEADER.DOTS)
            self.assertEqual(round(stops[0].position.mm), 150)
            self.assertIn("PAGEREF", para._p.xml)

    def test_dynamic_toc_styles_have_no_right_indent(self):
        from docx import Document
        hung = Document()
        self.builder.add_toc(hung, "신명조", hanging=True)
        for name in ("TOC 1", "TOC 2", "TOC 3"):
            style = hung.styles[name].paragraph_format
            self.assertTrue(style.right_indent is None or style.right_indent.mm == 0, name)
            self.assertLess(style.first_line_indent.pt, 0, name)   # 내어쓰기 유지
            stops = list(style.tab_stops)
            self.assertEqual(len(stops), 1, name)
            self.assertEqual(stops[0].alignment, WD_TAB_ALIGNMENT.RIGHT, name)
            self.assertEqual(stops[0].leader, WD_TAB_LEADER.DOTS, name)
            self.assertEqual(round(stops[0].position.mm), 150, name)

    def test_other_majors_toc_has_no_right_indent_either(self):
        _, doc = self.convert("food_crops")
        entry = self.toc_entries(doc, "표 1.")[0]
        self.assertIsNone(entry.paragraph_format.right_indent)

    def test_hwpx_markdown_sent_to_kordoc_has_no_toc_width_narrowing(self):
        # 범위: 마크다운 시험이다. 한글 쪽 빌더는 목차 항목 줄을 만들지 않아 실제 출력 XML에
        # 목차 폭 결함의 재현 표면이 없다 — 넘기는 글에 폭을 좁히는 표기가 없음만 잠근다.
        hwpx = runtime("gg_hwpx")
        out = hwpx.normalize_markdown(("# 목    차\n\n표 목차\n\n" + BODY).encode("utf-8"))
        for token in ("right_indent", "margin-right", "w:right", "rightMargin"):
            self.assertNotIn(token, out)


class RangeTildeUnitTests(_Docx):
    def test_range_tilde_becomes_center_tilde(self):
        f = self.gd.center_range_tildes
        self.assertEqual(f("면적 3~5 ha"), "면적 3" + CT + "5 ha")
        self.assertEqual(f("2027~2031년"), "2027" + CT + "2031년")
        self.assertEqual(f("22.5~25.0 ℃"), "22.5" + CT + "25.0 ℃")

    def test_range_boundaries_signed_and_unit_ranges_are_included(self):
        # 정한 경계: 앞이 숫자·%·℃·°, 뒤가 숫자(부호 -·+·− 허용)인 물결은 범위다.
        f = self.gd.center_range_tildes
        self.assertEqual(f("-5~-1 ℃"), "-5" + CT + "-1 ℃")
        self.assertEqual(f("-5 ~ +3 ℃"), "-5 " + CT + " +3 ℃")
        self.assertEqual(f("10%~12%"), "10%" + CT + "12%")
        self.assertEqual(f("5℃~10℃"), "5℃" + CT + "10℃")
        self.assertEqual(f("10,000~12,000원"), "10,000" + CT + "12,000원")

    def test_spaces_around_range_tilde_are_kept(self):
        f = self.gd.center_range_tildes
        self.assertEqual(f("10 ~ 12 톤"), "10 " + CT + " 12 톤")
        self.assertEqual(f("10 ~12"), "10 " + CT + "12")

    def test_non_range_tilde_is_untouched(self):
        f = self.gd.center_range_tildes
        for text in ("약 ~5 ha", "5~ ha", "A~B", "가~나", "~~취소~~", "3~~5", "~30%",
                     "물결 ~ 만 있다", "끝 ~", "~", "A~-5", "약~5"):
            self.assertEqual(f(text), text, text)

    def test_url_and_code_in_display_text_are_untouched(self):
        f = self.gd.center_range_tildes
        url = "https://example.org/archive3~5"
        self.assertEqual(f("출처 " + url), "출처 " + url)
        self.assertEqual(f("(" + url + ")"), "(" + url + ")")
        self.assertEqual(f("`3~5`"), "`3~5`")
        self.assertEqual(f("``3~5``"), "``3~5``")
        # 보호 구간 밖의 범위는 같은 글에서도 바뀐다.
        self.assertEqual(f("`3~5` 와 3~5 " + url), "`3~5` 와 3" + CT + "5 " + url)

    def test_idempotent(self):
        f = self.gd.center_range_tildes
        once = f("3~5 와 7~9")
        self.assertEqual(f(once), once)
        self.assertEqual(f(""), "")

    def test_xml_conversion_only_touches_hp_t_text(self):
        hwpx = runtime("gg_hwpx")
        xml = ('<hp:p><hp:run><hp:t>면적 3~5 ha</hp:t></hp:run>'
               '<hp:pic href="fig3~5.png" name="3~5"/>'
               '<hp:t a="3~5">출처 https://x.org/a3~5 이고 7~9</hp:t></hp:p>')
        out = hwpx.center_range_tildes_xml(xml)
        self.assertIn("<hp:t>면적 3" + CT + "5 ha</hp:t>", out)
        self.assertIn('<hp:pic href="fig3~5.png" name="3~5"/>', out)
        self.assertIn('a="3~5">출처 https://x.org/a3~5 이고 7' + CT + "9</hp:t>", out)
        self.assertEqual(hwpx.center_range_tildes_xml("<hp:t>없음</hp:t>"), "<hp:t>없음</hp:t>")

    def test_compare_table_numbers_applies_same_rule_to_both_sides(self):
        hwpx = runtime("gg_hwpx")
        source = "| 구분 | 값 |\n|--|--|\n| 온도 | 22~25 |\n"
        parsed = "| 구분 | 값 |\n|--|--|\n| 온도 | 22" + CT + "25 |\n"
        self.assertTrue(hwpx.compare_table_numbers(source, parsed)["match"])
        self.assertTrue(hwpx.compare_table_numbers(source, source)["match"])
        wrong = "| 구분 | 값 |\n|--|--|\n| 온도 | 22" + CT + "26 |\n"
        self.assertFalse(hwpx.compare_table_numbers(source, wrong)["match"])


class RangeTildeDocxTests(_Docx):
    def test_docx_output_range_tilde_is_center(self):
        for major in ("specialty_crops", "food_crops", "dairy"):
            _, doc = self.convert(major)
            joined = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("3" + CT + "5 ha", joined, major)
            self.assertIn("10 " + CT + " 12 톤", joined, major)
            self.assertNotIn("3~5", joined, major)

    def test_docx_table_cell_range_is_center_but_code_cell_is_kept(self):
        body = ("# Ⅰ. 서론\n\n표 1. 합성 범위표\n"
                "| 구분 | 값 |\n|--|--|\n| 온도 | -5~-1 ℃ |\n| 습도 | 10%~12% |\n"
                "| 식 | ``3~5`` |\n(출처: 합성 자료.)\n")
        _, doc = self.convert("specialty_crops", body)
        cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
        self.assertIn("-5" + CT + "-1 ℃", cells)
        self.assertIn("10%" + CT + "12%", cells)
        self.assertIn("``3~5``", cells)

    def test_docx_non_range_tilde_stays(self):
        body = BODY + "\n약 ~7 정도이고 A~B 기호는 그대로다.\n"
        _, doc = self.convert("specialty_crops", body)
        joined = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("약 ~7 정도이고 A~B 기호는 그대로다.", joined)

    def test_docx_figure_path_with_parens_and_range_tilde_still_embeds(self):
        # 리뷰 C-01: 내어쓰기 괄호가 든 그림 경로(dir(x)/fig3~5.png)가 변환에 깨지면
        # "그림 파일 없음"으로 발행이 멈췄다. 변환은 경로가 아니라 표시 글자에만 걸린다.
        root = self.make_project()
        figdir = root / "dir(x)"
        figdir.mkdir()
        (figdir / "fig3~5.png").write_bytes(PNG)
        body = BODY + "\n![시험](dir(x)/fig3~5.png)\n"
        _, doc = self.convert("specialty_crops", body, root=root, base=root)
        self.assertEqual(len(doc.inline_shapes), 1)

    def test_docx_long_source_url_keeps_text_and_link_target(self):
        # 리뷰 I-01: 65자 넘는 출처 주소의 끝 `archive3~5`가 표시와 링크 대상 모두 바뀌었다.
        url = "https://example.org/" + "a" * 60 + "/archive3~5"
        self.assertGreater(len(url), 65)
        # 주소 뒤는 괄호가 아니라 공백·줄끝이다(기존 주소 감지가 `\S+`라 `)`까지 잡는다).
        body = BODY + "\n출처: " + url + "\n"
        _, doc = self.convert("specialty_crops", body)
        # 하이퍼링크 안 글자는 paragraph.text에 안 잡히는 판이 있어 w:t를 직접 모은다.
        from docx.oxml.ns import qn
        shown = "".join(t.text or "" for t in doc.element.body.iter(qn("w:t")))
        self.assertIn(url, shown)
        self.assertNotIn(url.replace("~", CT), shown)
        targets = [rel.target_ref for rel in doc.part.rels.values() if rel.is_external]
        self.assertIn(url, targets)
        self.assertFalse([t for t in targets if CT in t])

    def test_docx_plain_url_text_is_kept(self):
        # 이 저장소 DOCX 원고는 본문에서 마크다운 링크 `[글](주소)`·백틱 코드를 받지 않는다
        # (미지원 요소). 본문은 맨 주소(괄호 든 주소 포함)만, 두 백틱 코드는 표 셀 시험
        # (`test_docx_table_cell_range_is_center_but_code_cell_is_kept`)이 맡는다.
        short = "https://example.org/a(b)/x3~5"
        body = BODY + "\n출처 " + short + " 이고 범위 7~9.\n"
        _, doc = self.convert("specialty_crops", body)
        joined = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn(short, joined)
        self.assertIn("범위 7" + CT + "9.", joined)

    def test_input_text_is_not_mutated(self):
        source, _ = self.convert("specialty_crops")
        before = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.gd.center_range_tildes(source)
        self.assertEqual(hashlib.sha256(source.encode("utf-8")).hexdigest(), before)
        self.assertIn("3" + TILDE + "5 ha", source)


class RangeTildeHwpxTests(_Docx):
    def test_hwpx_normalize_step_keeps_the_source_tilde(self):
        hwpx = runtime("gg_hwpx")
        self.assertIn(TILDE, hwpx.normalize_markdown("면적 3~5 ha\n".encode("utf-8")))

    def test_hwpx_build_sends_unchanged_markdown_to_kordoc(self):
        # 변환은 출력 XML 쪽이다 — kordoc에 넘기는 마크다운(그림 경로·주소·원문)은 그대로다.
        import sys
        from unittest import mock

        hwpx = runtime("gg_hwpx")
        root = self.make_project()
        bind_major(root, "food_crops")
        context = self.mc.output_context(root, "food_crops")
        authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_PAPER, context)
        fake = root / "fake_kordoc.py"
        fake.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'generate':\n"
            "    Path(args[args.index('-o') + 1]).write_bytes(b'raw')\n"
            "    Path(__file__).with_name('sent.md').write_text(\n"
            "        Path(args[-1]).read_text(encoding='utf-8'), encoding='utf-8')\n",
            encoding="utf-8")
        source = root / "in.md"
        original = ("재배 면적은 3~5 ha, 수확은 10 ~ 12 톤.\n\n"
                    "![그림](fig3~5.png)\n\n"
                    "출처 https://example.org/archive3~5\n").encode("utf-8")
        (root / "fig3~5.png").write_bytes(PNG)
        source.write_bytes(original)

        class Stop(Exception):
            pass

        with mock.patch.object(hwpx, "transform_hwpx", side_effect=Stop):
            with self.assertRaises(Stop):
                hwpx.build(source, root / "out" / "r.hwpx", context=context,
                           authorization=authorization,
                           cli_command=[sys.executable, str(fake)])
        sent = (root / "sent.md").read_text(encoding="utf-8")
        self.assertIn("3~5 ha", sent)
        self.assertIn("10 ~ 12 톤", sent)
        self.assertIn("fig3~5.png", sent)
        self.assertIn("https://example.org/archive3~5", sent)
        self.assertNotIn(CT, sent)
        self.assertEqual(source.read_bytes(), original)

    def test_dairy_chapter_title_with_range_still_gets_page_break(self):
        # 리뷰 I-02: 출력 글자는 가운데 물결인데 장 제목 목록은 원문 글자라 대조가 어긋나
        # 낙농 `2. 농업환경분석(2026~2027년)`의 새 쪽이 빠졌다. 같은 규칙으로 맞춰 비교한다.
        build_hwpx = runtime("build_hwpx")
        md = ("# 1. 서론\n\n본문.\n\n# 2. 농업환경분석(2026~2027년)\n\n본문.\n\n"
              "# 3. 결론\n\n본문.\n")
        texts = build_hwpx.chapter_break_texts(md, "dairy")
        self.assertIn("2. 농업환경분석(2026" + CT + "2027년)", texts)
        xml = "".join(
            '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run></hp:p>' % t
            for t in ("1. 서론", "2. 농업환경분석(2026" + CT + "2027년)", "3. 결론"))
        updated, chapters = build_hwpx.add_chapter_breaks(
            xml, major_id="dairy", chapter_texts=texts)
        self.assertEqual(chapters, ["2. 농업환경분석(2026" + CT + "2027년)", "3. 결론"])
        self.assertEqual(updated.count('pageBreak="1"'), 2)

    def test_chapter_titles_without_range_are_unchanged(self):
        build_hwpx = runtime("build_hwpx")
        md = "# 1. 서론\n\n# 2. 분석\n\n# 3. 결론\n"
        self.assertEqual(build_hwpx.chapter_break_texts(md, "dairy"),
                         ["2. 분석", "3. 결론"])


if __name__ == "__main__":
    unittest.main()
