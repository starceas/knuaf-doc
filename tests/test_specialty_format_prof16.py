"""특용작물 전용 서식 — 지도교수 수정(v0.6.4) 첫 묶음.

잠그는 것:
- 단계 제목 크기: 제목(Ⅰ.) 20 · 부제목(1.) 18 · 소제목(가.) 이하 16pt 통일.
- 표·그림 제목 끝 온점 제거(본문 캡션과 표·그림 목차 항목 모두).
- 목차·표 목차·그림 목차 제목 20pt.
- 다른 전공(낙농·식량작물 프로필, 프로필 없는 곤충)은 값이 그대로다.

가짜 원고·가짜 이름만 쓴다.
"""

import unittest

from docx.enum.text import WD_ALIGN_PARAGRAPH

from tests._harness import ContractCase, bind_major, runtime

BODY = (
    "# Ⅰ. 서론\n"
    "## 1. 배경\n"
    "### 가. 목적\n"
    "#### 1) 세부\n"
    "본문 글.\n\n"
    "표 1. 계획의 전제와 확정 순서.\n"
    "| 연도 | 면적 |\n"
    "|--|--|\n"
    "| 2027 | 100 |\n"
    "(출처: 합성 자료.)\n\n"
    "그림 1. 합성 배치도.\n"
)


class SpecialtyFormatProf16Tests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.gmf = runtime("gg_major_format")
        self.mc = runtime("gg_major_contract")

    def convert(self, major):
        root = self.make_project()
        bind_major(root, major)
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "summary": "합성 요약", "thanks": "합성 감사"})
        text = "\n\n".join(lines) + "\n\n" + BODY
        return self.builder.convert(
            text, context=self.mc.output_context(root, major))

    @staticmethod
    def size_of(doc, startswith):
        # 목차 항목 줄(`Ⅰ. 서론\t…`)이 아니라 제목 스타일 문단만 본다.
        paragraph = next(p for p in doc.paragraphs
                         if p.style.name.startswith("Heading")
                         and p.text.strip().startswith(startswith))
        sizes = {run.font.size.pt for run in paragraph.runs if run.font.size}
        return sizes

    # --- 값 표 ---------------------------------------------------------------

    def test_value_table_only_for_specialty(self):
        self.assertEqual(
            dict(self.gmf.specialty_level_pt("specialty_crops")),
            {1: 20, 2: 18, 3: 16, 4: 16, 5: 16, 6: 16, 7: 16, 8: 16, 9: 16})
        for major in ("food_crops", "dairy", "fruit_trees", "industrial_insects",
                      "hort_env_systems", None):
            self.assertIsNone(self.gmf.specialty_level_pt(major), major)

    def test_strip_caption_period_only_for_specialty(self):
        strip = self.gmf.strip_caption_period
        self.assertEqual(strip("표 1. 계획의 전제와 확정 순서.", "specialty_crops"),
                         "표 1. 계획의 전제와 확정 순서")
        self.assertEqual(strip("그림 2. 배치도 .", "specialty_crops"), "그림 2. 배치도")
        # 중간 온점·소수점은 건드리지 않는다.
        self.assertEqual(strip("표 3. 평균 5.5 배 증가", "specialty_crops"),
                         "표 3. 평균 5.5 배 증가")
        # 말줄임으로 끝나는 제목은 `...`와 `…` 모두 그대로 둔다.
        self.assertEqual(strip("표 4. 이어지는 계획...", "specialty_crops"),
                         "표 4. 이어지는 계획...")
        self.assertEqual(strip("표 4. 이어지는 계획…", "specialty_crops"),
                         "표 4. 이어지는 계획…")
        self.assertEqual(strip("표 4. 이어지는 계획….", "specialty_crops"),
                         "표 4. 이어지는 계획…")
        self.assertEqual(strip("표 1. 계획.", "food_crops"), "표 1. 계획.")
        self.assertEqual(strip("표 1. 계획.", None), "표 1. 계획.")

    def test_specialty_has_no_format_profile(self):
        # 덮개만 쓰고 프로필(구역 여백·로마 쪽번호·장 새 쪽)은 켜지 않는다.
        self.assertFalse(self.gmf.format_enabled("specialty_crops"))
        self.assertIsNone(self.gmf.enabled_profile("specialty_crops"))
        self.assertNotIn("specialty_crops", self.gmf.ENABLED_FORMAT_MAJORS)

    # --- DOCX 출력 -----------------------------------------------------------

    def test_specialty_heading_sizes_20_18_16_16(self):
        doc = self.convert("specialty_crops")
        self.assertEqual(self.size_of(doc, "Ⅰ. 서론"), {20.0})
        self.assertEqual(self.size_of(doc, "1. 배경"), {18.0})
        self.assertEqual(self.size_of(doc, "가. 목적"), {16.0})
        self.assertEqual(self.size_of(doc, "1) 세부"), {16.0})

    def test_specialty_caption_has_no_trailing_period(self):
        doc = self.convert("specialty_crops")
        captions = [p.text for p in doc.paragraphs
                    if p.text.startswith(("표 1.", "그림 1."))
                    and "\t" not in p.text]
        self.assertTrue(captions)
        for text in captions:
            self.assertFalse(text.rstrip().endswith("."), text)
        # 표·그림 목차 항목도 온점이 없다.
        entries = [p.text for p in doc.paragraphs
                   if p.text.startswith(("표 1.", "그림 1.")) and "\t" in p.text]
        self.assertTrue(entries)
        for text in entries:
            self.assertFalse(text.split("\t")[0].rstrip().endswith("."), text)

    def test_toc_titles_are_20pt_already_in_the_base_path(self):
        # 목차·표 목차·그림 목차 제목 20pt(수정 10번)는 특용작물 덮개가 바꾼 값이 아니다:
        # 기본 경로가 이미 20pt라 base에서도 통과한다. 값이 흔들리지 않게만 잠근다.
        doc = self.convert("specialty_crops")
        for title in ("목    차", "표 목차", "그림 목차"):
            # 목차 항목 줄(`표 목차\t…`)과 구분하려고 가운데 정렬 제목 문단만 본다.
            paragraph = next(
                p for p in doc.paragraphs
                if p.text == title and p.alignment == WD_ALIGN_PARAGRAPH.CENTER)
            self.assertEqual(
                {run.font.size.pt for run in paragraph.runs if run.font.size},
                {20.0}, title)

    # --- 목차 내어쓰기(12번) ----------------------------------------------------

    def test_specialty_toc_entries_hang_and_keep_page_number_lane(self):
        doc = self.convert("specialty_crops")
        entry = next(p for p in doc.paragraphs
                     if p.text.startswith("표 1.") and "\t" in p.text)
        fmt = entry.paragraph_format
        # 내어쓰기 폭은 라벨(`표 1. `) 폭 추정과 같다(고정 10mm 아님). 단위 변환
        # (twips 반올림) 오차는 0.1 mm 안쪽만 허용한다.
        width = self.label_width_mm("표 1. ")
        self.assertAlmostEqual(-fmt.first_line_indent.mm, width, delta=0.1)
        self.assertAlmostEqual(fmt.left_indent.mm, 5 + width, delta=0.1)
        self.assertAlmostEqual(fmt.right_indent.mm, 14, delta=0.1)
        # 번호 없는 줄(`요약` 등)과 다른 전공 목차는 그대로다.
        food = self.convert("food_crops")
        other = next(p for p in food.paragraphs
                     if p.text.startswith("표 1.") and "\t" in p.text)
        self.assertEqual(other.paragraph_format.first_line_indent, 0)
        self.assertIsNone(other.paragraph_format.right_indent)

    @staticmethod
    def label_width_mm(prefix, size=12.0):
        """독립 계산: 표 칸 폭 추정과 같은 글자 폭으로 라벨 폭(mm)을 잰다."""
        gdtl = runtime("gg_docx_table_layout")
        return sum(gdtl._char_em(ch) for ch in prefix) * size * gdtl.PT_TO_MM

    def test_toc_wrapped_line_starts_where_the_title_starts(self):
        # 내려간 줄 시작 위치(left_indent) = 제목 시작 위치(들여쓰기 + 라벨 폭).
        # 라벨이 길수록(`그림 10. ` > `표 1. `) 내어쓰기도 넓어져 번호 아래로
        # 들어가지 않는다(검토 중요 지적: 고정 10mm는 `그림 10.`에서 번호를 침범).
        from docx import Document
        long_title = "합성 제목이 한 줄에 다 들어가지 않을 만큼 길게 이어지는 설명 글"
        prefixes = ("표 1. ", "그림 1. ", "표 10. ", "그림 10. ")
        entries = [(prefix + long_title, "bm%d" % i, 2)
                   for i, prefix in enumerate(prefixes)]
        doc = Document()
        self.fm_layout = runtime("gg_frontmatter_layout")
        self.fm_layout.contents(doc, entries, "신명조", "표 목차", "gg_tables", 9002,
                                hanging=True)
        found = [p for p in doc.paragraphs if "\t" in p.text or long_title in p.text]
        self.assertEqual(len(found), len(prefixes))
        widths = []
        for prefix, para in zip(prefixes, found):
            fmt = para.paragraph_format
            width = self.label_width_mm(prefix)
            widths.append(width)
            # 제목 시작 = 번호 줄 시작(왼쪽 들여쓰기 5mm) + 라벨 폭.
            title_start = fmt.left_indent.mm + fmt.first_line_indent.mm + width
            self.assertAlmostEqual(fmt.left_indent.mm, title_start, delta=0.1, msg=prefix)
            # 내어쓰기가 라벨보다 좁지 않다(번호 아래에서 시작하지 않는다).
            self.assertGreaterEqual(-fmt.first_line_indent.mm + 0.1, width, prefix)
        self.assertLess(widths[0], widths[2])      # 표 1. < 표 10.
        self.assertLess(widths[2], widths[3])      # 표 10. < 그림 10.

    def test_dynamic_toc_styles_hang_only_for_specialty(self):
        from docx import Document
        from docx.shared import Pt
        builder = self.builder
        plain, hung = Document(), Document()
        builder.add_toc(plain, "신명조")
        builder.add_toc(hung, "신명조", hanging=True)
        self.assertEqual(plain.styles["TOC 2"].paragraph_format.left_indent, Pt(14))
        self.assertIsNone(plain.styles["TOC 2"].paragraph_format.first_line_indent)
        self.assertEqual(hung.styles["TOC 2"].paragraph_format.left_indent, Pt(34))
        self.assertEqual(hung.styles["TOC 2"].paragraph_format.first_line_indent, Pt(-20))

    # --- 표 칸 정렬(11·13번) -----------------------------------------------------

    def test_year_column_detection(self):
        idx = self.gmf.year_column_indexes
        self.assertEqual(idx([["연도", "면적"], ["2027", "100"]]), {0})
        self.assertEqual(idx([["구분", "면적"], ["2027년", "100"], ["2028년", "110"]]), {0})
        self.assertEqual(idx([["구분", "금액"], ["1차 년도", "1"], ["2차", "2"]]), {0})
        self.assertEqual(idx([["품목", "면적"], ["고추", "100"]]), set())
        self.assertEqual(idx([]), set())

    def test_year_column_detection_counterexamples(self):
        idx = self.gmf.year_column_indexes
        # 머리칸이 연도형이 아니면 값이 연도 꼴이어도 우연일 수 있다: 본문 행이 둘
        # 이상이고 1씩 이어질 때만 연도 열로 본다.
        self.assertEqual(idx([["면적(㎡)", "비고"], ["2000", "a"], ["2020", "b"]]), set())
        self.assertEqual(idx([["구분", "금액"], ["x", "2020"], ["y", "2020"]]), set())
        self.assertEqual(idx([["구분", "2027"], ["합계", "2027"]]), set())      # 본문 1행
        self.assertEqual(idx([["구분", "값"], ["2027", "1"]]), set())            # 본문 1행
        self.assertEqual(idx([["구분", "값"], ["2028", "1"], ["2027", "2"]]), set())  # 내림차순
        self.assertEqual(idx([["구분", "값"], ["2027", "1"], ["2028", "2"]]), {0})
        self.assertEqual(idx([["구분", "값"], ["2027", "1"], ["2027", "2"]]), set())  # 같은 값
        # 머리칸 패턴: `연도/…`·`기간`·`연도 (차)`는 값이 숫자 하나여도 연도 열이다.
        self.assertEqual(idx([["연도/구분", "a"], ["x", "1"]]), {0})
        self.assertEqual(idx([["기간", "a"], ["2027~2028", "1"], ["2028~2029", "2"]]), {0})
        self.assertEqual(idx([["연도 (차)", "a"], ["1", "1"]]), {0})

    def test_year_column_detection_allows_a_total_row_and_skipped_years(self):
        idx = self.gmf.year_column_indexes
        # 검토 재현: 맨 끝 합계 행이 있어도 연도 열이다.
        self.assertEqual(
            idx([["구분", "값"], ["2027", "1"], ["2028", "2"], ["합계", "3"]]), {0})
        for tail in ("계", "평균", "합 계"):
            self.assertEqual(
                idx([["구분", "값"], ["2027", "1"], ["2028", "2"], [tail, "3"]]), {0}, tail)
        # 오름차순이면 해를 건너뛰어도 된다.
        self.assertEqual(
            idx([["구분", "값"], ["2025", "1"], ["2026", "2"], ["2028", "3"]]), {0})
        # 합계 행을 뺀 뒤 본문이 1행뿐이거나, 합계 행이 중간에 끼면 연도 열이 아니다.
        self.assertEqual(idx([["구분", "값"], ["2027", "1"], ["합계", "3"]]), set())
        self.assertEqual(
            idx([["구분", "값"], ["2027", "1"], ["합계", "3"], ["2028", "2"]]), set())
        # 멀리 떨어진 두 해(면적 2000·2020)는 여전히 연도 열이 아니다.
        self.assertEqual(idx([["면적(㎡)", "비고"], ["2000", "a"], ["2020", "b"]]), set())

    def test_table_too_wide_for_the_smallest_font_is_reported_in_docx(self):
        # 어절 하나가 20자인 세 열은 9pt로 줄여도 본문 폭에 안 들어간다: 조용히 넘기지
        # 않고 표 배치 보고에 실패로 올린다. 다른 전공은 이 보고를 만들지 않는다.
        words = " | ".join(["가" * 20, "나" * 20, "다" * 20])
        text = "# Ⅰ. 서론\n\n본문 글.\n\n| %s |\n|--|--|--|\n| %s |\n" % (words, words)
        for major, expected in (("specialty_crops", 1), ("fruit_trees", 0)):
            with self.subTest(major=major):
                root = self.make_project()
                bind_major(root, major)
                reports, specialty_reports = [], []
                self.builder.convert(text, table_reports=reports,
                                     specialty_table_reports=specialty_reports,
                                     context=self.mc.output_context(root, major))
                # 특작 보고는 넓은 표 검사 보고(`table_reports`)와 섞이지 않는다:
                # 그쪽 개수(`wide_tables_checked`)는 이 표 때문에 늘지 않는다.
                self.assertEqual(reports, [])
                flagged = [r for r in specialty_reports if r.get("specialty_widths")]
                self.assertEqual(len(flagged), expected)
                if expected:
                    self.assertIs(flagged[0]["feasible"], False)
                    self.assertEqual(flagged[0]["n_cols"], 3)

    def test_manifest_keeps_specialty_table_reports_apart_from_wide_table_checks(self):
        import json
        import subprocess
        import sys
        from tests._harness import SCRIPTS, write_text
        words = " | ".join(["가" * 20, "나" * 20, "다" * 20])
        text = "# Ⅰ. 서론\n\n본문 글.\n\n| %s |\n|--|--|--|\n| %s |\n" % (words, words)
        for major, expected in (("specialty_crops", 1), ("fruit_trees", 0)):
            with self.subTest(major=major):
                root = self.make_project()
                bind_major(root, major)
                write_text(root, "body.md", text)
                proc = subprocess.run(
                    [sys.executable, "-B", str(SCRIPTS / "build_docx.py"), str(root),
                     "--in", "body.md", "--out", "build/out.docx", "--major", major],
                    capture_output=True, text=True, encoding="utf-8", cwd=root)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                manifest = json.loads((root / "build/out.manifest.json")
                                      .read_text(encoding="utf-8"))
                layout = manifest["table_layout"]
                # 넓은 표 검사 수·불가 수·상태는 특작 표 때문에 바뀌지 않는다.
                self.assertEqual(layout["wide_tables_checked"], 0)
                self.assertEqual(layout["infeasible_count"], 0)
                self.assertEqual(layout["status"], "ok")
                self.assertEqual(layout["reports"], [])
                if expected:
                    self.assertEqual(len(layout["specialty_table_reports"]), 1)
                    self.assertIn("특용작물 표 1개", manifest["notice"])
                else:
                    self.assertNotIn("specialty_table_reports", layout)

    def test_specialty_table_year_centered_rest_left(self):
        doc = self.convert("specialty_crops")
        table = next(t for t in doc.tables
                     if t.rows[0].cells[0].text.strip() == "연도")
        for row in table.rows:
            self.assertEqual(row.cells[0].paragraphs[0].alignment,
                             WD_ALIGN_PARAGRAPH.CENTER)
            self.assertEqual(row.cells[1].paragraphs[0].alignment,
                             WD_ALIGN_PARAGRAPH.LEFT)

    def test_other_majors_table_alignment_untouched(self):
        doc = self.convert("food_crops")
        table = next(t for t in doc.tables
                     if t.rows[0].cells[0].text.strip() == "연도")
        for row in table.rows:
            for cell in row.cells:
                self.assertIsNone(cell.paragraphs[0].alignment)

    # --- 표·그림 아래 설명줄(15번) -----------------------------------------------

    def test_specialty_wide_notes_stay_with_table(self):
        layout = runtime("gg_body_layout")
        nodes = [
            {"kind": "paragraph", "text": "본문."},
            {"kind": "caption", "label": "표", "text": "표 1. 제목"},
            {"kind": "table", "rows": [["a"], ["b"]]},
            {"kind": "paragraph", "text": "※ 자료 : 합성 자료"},
            {"kind": "paragraph", "text": "(출처 : 합성 기관)"},
            {"kind": "paragraph", "text": "다음 본문."},
        ]
        # 공통 규칙: `※ 자료`는 설명줄로 보지 않아 표와 줄 사이가 벌어진다.
        self.assertEqual(layout.spacer_positions(nodes), {1, 3})
        # 특용작물: 두 줄 다 표에 붙고, 다음 본문 앞에만 빈 줄이 든다.
        self.assertEqual(
            layout.spacer_positions(nodes, layout.NOTE_SPECIALTY), {1, 5})

    # --- 칸 안 줄바꿈 정리(표 안 어절 갈림) ----------------------------------------

    def test_content_widths_give_long_columns_more_room(self):
        layout = runtime("gg_docx_table_layout")
        rows = [["연도", "재배면적(㎡)", "주요 내용"],
                ["2027", "1,000", "시설 설치 및 재배 시작 후 첫 수확을 진행"],
                ["2028", "1,200", "면적 확대"]]
        widths, pt, ok = layout.content_widths_mm(rows, 150.0)
        self.assertTrue(ok)
        self.assertEqual(pt, 12.0)
        self.assertAlmostEqual(sum(widths), 150.0, places=6)
        # 같은 폭으로 나눈 50 mm보다 짧은 열은 줄고 긴 글 열은 는다.
        self.assertLess(widths[0], 50.0)
        self.assertGreater(widths[2], 50.0)
        # 가장 긴 어절(`재배면적(㎡)`)은 한 줄에 들어간다: 12pt 전각 6자 이상.
        self.assertGreater(widths[1], 6 * 12 * 25.4 / 72 * 0.8)

    def test_content_widths_shrink_font_one_step_when_a_word_cannot_fit(self):
        layout = runtime("gg_docx_table_layout")
        rows = [["가" * 9, "나" * 9, "다" * 9, "라" * 9],
                ["x", "y", "z", "w"]]
        widths, pt, ok = layout.content_widths_mm(rows, 150.0)
        self.assertTrue(ok)
        self.assertLess(pt, 12.0)
        self.assertEqual(pt, int(pt))  # 한 단계(1pt)씩 내린다.
        self.assertAlmostEqual(sum(widths), 150.0, places=6)

    def test_specialty_body_table_widths_sum_to_text_area(self):
        doc = self.convert("specialty_crops")
        table = next(t for t in doc.tables
                     if t.rows[0].cells[0].text.strip() == "연도")
        total = sum(column.width for column in table.columns)
        self.assertAlmostEqual(total / 36000.0, 150.0, delta=0.2)

    def test_other_majors_table_widths_stay_equal(self):
        doc = self.convert("food_crops")
        table = next(t for t in doc.tables
                     if t.rows[0].cells[0].text.strip() == "연도")
        widths = {column.width for column in table.columns}
        self.assertLessEqual(max(widths) - min(widths), 40)  # 끝 열 반올림 보정뿐

    # --- 겉표지 날짜(9번) --------------------------------------------------------

    def test_cover_date_is_graduation_month(self):
        profile = self.fm.normalize_school_profile({
            "major_id": "specialty_crops",
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile)
        cover = lines[:lines.index("표제면")]
        self.assertIn("2027년 2월", cover)
        self.assertNotIn("2026년 12월", cover)

    # --- 다른 전공은 그대로 ----------------------------------------------------

    def test_other_majors_keep_their_heading_sizes(self):
        expected = {
            # 프로필 있는 전공: 프로필 표 그대로(4단계가 특작과 다르다).
            # (낙농은 `Ⅰ.`을 2단계로 읽으므로 3·4단계만 대조한다.)
            "food_crops": {"1. 배경": 18, "가. 목적": 16, "1) 세부": 14},
            "dairy": {"가. 목적": 16, "1) 세부": 15},
        }
        for major, sizes in expected.items():
            try:
                doc = self.convert(major)
            except ValueError:
                # 전공별 앞머리 규칙이 이 합성 원고를 받지 않으면 값 표만 대조한다.
                profile = self.gmf.profile_for(major)
                self.assertEqual(profile.level_pt[4], sizes["1) 세부"], major)
                continue
            for text, size in sizes.items():
                self.assertEqual(self.size_of(doc, text), {float(size)},
                                 (major, text))

    def test_other_majors_keep_caption_period(self):
        # 특작이 아니면 글을 그대로 둔다(빌더 입력 그대로).
        self.assertEqual(
            self.gmf.strip_caption_period("표 1. 계획.", "hort_env_systems"),
            "표 1. 계획.")


HWPX_NS = ('xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
           'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
           'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
           'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"')

HWPX_BODY = (
    "# Ⅰ. 서론\n\n## 1. 배경\n\n### 가. 목적\n\n본문 글이다.\n\n"
    "표 1. 계획의 전제.\n\n"
    "| 연도 | 면적 | 비고 |\n|--|--:|--|\n| 2027 | 100 | 정식 후 바로 수확한다 |\n\n"
    "(출처: 합성 자료.)\n\n다음 문단이다.\n\n"
)


def _para_pr(pid, horizontal, level=None):
    heading = ('<hh:heading type="OUTLINE" idRef="0" level="%d"/>' % level
               if level is not None else '<hh:heading type="NONE" idRef="0" level="0"/>')
    return ('<hh:paraPr id="%s" tabPrIDRef="0"><hh:align horizontal="%s" '
            'vertical="BASELINE"/>%s<hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
            '<hc:left value="0" unit="HWPUNIT"/><hc:right value="0" unit="HWPUNIT"/>'
            '<hc:prev value="0" unit="HWPUNIT"/><hc:next value="0" unit="HWPUNIT"/>'
            '</hh:margin></hh:paraPr>' % (pid, horizontal, heading))


def _char_pr(cid, height):
    return ('<hh:charPr id="%s" height="%d" textColor="#000000" borderFillIDRef="1">'
            '<hh:fontRef hangul="0" latin="0"/></hh:charPr>' % (cid, height))


def _hwpx_header():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hh:head %s>'
        '<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="1">'
        '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/></hh:fontface>'
        '</hh:fontfaces><hh:charProperties itemCnt="3">%s%s%s</hh:charProperties>'
        '<hh:paraProperties itemCnt="3">%s%s%s</hh:paraProperties></hh:head>'
        % (HWPX_NS, _char_pr(0, 1000), _char_pr(1, 1800), _char_pr(2, 1400),
           _para_pr(0, "JUSTIFY"), _para_pr(1, "LEFT", 0), _para_pr(2, "LEFT", 1)))


def _plain(pid, cid, text):
    return ('<hp:p paraPrIDRef="%s" styleIDRef="0"><hp:run charPrIDRef="%s">'
            '<hp:t>%s</hp:t></hp:run></hp:p>' % (pid, cid, text))


def _table(rows, widths):
    cells = []
    for r, row in enumerate(rows):
        tcs = "".join(
            '<hp:tc name="" header="0"><hp:subList id="">%s</hp:subList>'
            '<hp:cellAddr colAddr="%d" rowAddr="%d"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            '<hp:cellSz width="%d" height="1500"/></hp:tc>' % (_plain(0, 0, text), c, r, widths[c])
            for c, text in enumerate(row))
        cells.append("<hp:tr>%s</hp:tr>" % tcs)
    return ('<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
            '<hp:tbl id="1" rowCnt="%d" colCnt="%d"><hp:sz width="%d" height="3000"/>%s'
            '</hp:tbl></hp:run></hp:p>' % (len(rows), len(rows[0]), sum(widths), "".join(cells)))


class SpecialtyHwpxFormatTests(ContractCase):
    """특용작물 한글(HWPX) 덮개 — 합성 kordoc 모양 입력으로 XML 값을 잠근다."""

    def setUp(self):
        self.gh = runtime("gg_hwpx")
        self.gd = runtime("gg_document")

    def build_zip(self):
        import io
        import zipfile
        section = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hs:sec %s>%s</hs:sec>'
            % (HWPX_NS, "".join([
                _plain(1, 0, "Ⅰ. 서론"), _plain(2, 0, "1. 배경"), _plain(2, 0, "가. 목적"),
                _plain(0, 0, "본문 글이다."), _plain(0, 0, "표 1. 계획의 전제"),
                _table([["연도", "면적", "비고"], ["2027", "100", "정식 후 바로 수확한다"]],
                       [10000, 10000, 22520]),
                _plain(0, 0, "(출처: 합성 자료.)"), _plain(0, 0, "다음 문단이다.")])))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/header.xml", _hwpx_header())
            archive.writestr("Contents/section0.xml", section)
        return buffer.getvalue()

    def transform(self, major):
        import io
        import zipfile
        nodes = self.gd.parse(HWPX_BODY, major_id=major)
        data, applied = self.gh.transform_hwpx(
            self.build_zip(), font=None, margins_mm=None, page_numbers=False,
            nodes=nodes, plan=None, major_id=major)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return (archive.read("Contents/header.xml").decode(),
                    archive.read("Contents/section0.xml").decode(), applied)

    @staticmethod
    def height_of(header, cid):
        import re
        return int(re.search(r'<hh:charPr id="%s" height="(\d+)"' % cid, header)[1])

    @staticmethod
    def align_of(header, pid):
        import re
        return re.search(r'<hh:paraPr id="%s"[^>]*><hh:align horizontal="(\w+)"' % pid,
                         header)[1]

    def paragraph_for(self, section, text):
        import re
        return re.search(r"<hp:p\b[^>]*>(?:(?!</hp:p>).)*%s.*?</hp:p>" % re.escape(text),
                         section, re.S)[0]

    def test_caption_markdown_drops_only_the_final_period(self):
        text = ("표 1. 계획의 전제.\n표 2. 비용 (단위: 천원).\n"
                "그림 1. 배치도. (출처 : 합성 자료)\n본문이다.\n표 3. 소수 1.5\n")
        out = self.gh.specialty_caption_markdown(text)
        self.assertEqual(out, "표 1. 계획의 전제\n표 2. 비용 (단위: 천원)\n"
                              "그림 1. 배치도 (출처 : 합성 자료)\n본문이다.\n표 3. 소수 1.5\n")
        plain = "본문.\n표 1. 소수 1.5\n"
        self.assertIs(self.gh.specialty_caption_markdown(plain), plain)

    def test_heading_sizes_follow_levels(self):
        header, section, applied = self.transform("specialty_crops")
        sizes = {}
        import re
        for text in ("Ⅰ. 서론", "1. 배경", "가. 목적"):
            ref = re.search(r'charPrIDRef="(\d+)"', self.paragraph_for(section, text))[1]
            sizes[text] = self.height_of(header, ref)
        self.assertEqual(sizes, {"Ⅰ. 서론": 2000, "1. 배경": 1800, "가. 목적": 1600})
        self.assertEqual(applied["specialty"]["headings"], 3)
        self.assertEqual(applied["specialty"]["unmatched"], [])

    def test_table_year_column_centered_others_left(self):
        import re
        header, section, applied = self.transform("specialty_crops")
        table = re.search(r"<hp:tbl\b.*?</hp:tbl>", section, re.S)[0]
        by_text = {}
        for cell in re.findall(r"<hp:tc\b.*?</hp:tc>", table, re.S):
            text = re.search(r"<hp:t>(.*?)</hp:t>", cell)[1]
            by_text[text] = self.align_of(
                header, re.search(r'paraPrIDRef="(\d+)"', cell)[1])
        self.assertEqual(by_text["연도"], "CENTER")
        self.assertEqual(by_text["2027"], "CENTER")
        for text in ("면적", "비고", "100", "정식 후 바로 수확한다"):
            self.assertEqual(by_text[text], "LEFT", text)

    def test_table_widths_fill_body_width_and_keep_each_row_whole(self):
        import re
        header, section, applied = self.transform("specialty_crops")
        table = re.search(r"<hp:tbl\b.*?</hp:tbl>", section, re.S)[0]
        total = self.gh.mm_to_hwpunit(150)
        rows = re.findall(r"<hp:tr>.*?</hp:tr>", table, re.S)
        for row in rows:
            widths = [int(w) for w in re.findall(r'<hp:cellSz width="(\d+)"', row)]
            self.assertEqual(sum(widths), total)
        self.assertEqual(int(re.search(r'<hp:sz width="(\d+)"', table)[1]), total)
        self.assertEqual(applied["specialty"]["table_widths"], 1)

    def test_blank_line_before_caption_and_after_note(self):
        import re
        header, section, applied = self.transform("specialty_crops")
        # 제목 ↔ 본문 사이에는 빈 줄이 없고, 본문 → 표 제목 앞과 설명줄 뒤에만 빈 줄이 든다.
        self.assertEqual(applied["specialty"]["blank_lines"], 2)
        caption = self.paragraph_for(section, "표 1. 계획의 전제")
        note = self.paragraph_for(section, "(출처: 합성 자료.)")
        before = section[:section.index(caption)]
        self.assertTrue(before.rstrip().endswith("<hp:t></hp:t></hp:run></hp:p>"))
        after = section[section.index(note) + len(note):]
        self.assertTrue(after.lstrip().startswith("<hp:p"))
        self.assertIn("<hp:t></hp:t>", after.split("다음 문단이다.")[0])
        # 표 바로 뒤 설명줄은 붙어 있다(사이에 빈 문단 없음).
        between = section[section.index("</hp:tbl>"):section.index(note)]
        self.assertNotIn("<hp:t></hp:t>", between)

    def transform_with(self, section_xml):
        """합성 section으로 특작 덮개만 돌린다(표·빈 줄 시험용)."""
        import io
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/header.xml", _hwpx_header())
            archive.writestr("Contents/section0.xml", section_xml)
        nodes = self.gd.parse(HWPX_BODY, major_id="specialty_crops")
        data, applied = self.gh.transform_hwpx(
            buffer.getvalue(), font=None, margins_mm=None, page_numbers=False,
            nodes=nodes, plan=None, major_id="specialty_crops")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read("Contents/section0.xml").decode(), applied

    def section_xml(self, table):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hs:sec %s>%s</hs:sec>'
                % (HWPX_NS, "".join([
                    _plain(1, 0, "Ⅰ. 서론"), _plain(2, 0, "1. 배경"), _plain(2, 0, "가. 목적"),
                    _plain(0, 0, "본문 글이다."), _plain(0, 0, "표 1. 계획의 전제"), table,
                    _plain(0, 0, "(출처: 합성 자료.)"), _plain(0, 0, "다음 문단이다.")])))

    def test_same_shape_table_with_other_rows_and_header_is_not_formatted(self):
        # 열 수(3)만 같고 줄 수·머리 첫 칸이 다른 표는 같은 표로 보지 않는다.
        other = _table([["품목", "a", "b"], ["x", "1", "2"], ["y", "3", "4"], ["z", "5", "6"]],
                       [13000, 14000, 15520])
        section, applied = self.transform_with(self.section_xml(other))
        self.assertEqual(applied["specialty"]["tables"], 0)
        self.assertEqual(applied["specialty"]["unmatched"], [{"table_shape": 5}])
        self.assertEqual(section.count('<hp:cellSz width="13000"'), 4)  # 폭 그대로

    def test_table_is_matched_by_either_row_count_or_header_cell(self):
        import re
        base = _table([["연도", "면적", "비고"], ["2027", "100", "정식"]], [14000, 14000, 14520])
        # 줄 수(rowCnt)가 어긋나도 머리 첫 칸이 같으면 같은 표다.
        row_off = base.replace('rowCnt="2"', 'rowCnt="9"')
        _section, applied = self.transform_with(self.section_xml(row_off))
        self.assertEqual(applied["specialty"]["tables"], 1)
        # 머리 첫 칸이 달라도 줄 수가 같으면 같은 표다.
        head_off = base.replace("<hp:t>연도</hp:t>", "<hp:t>해</hp:t>")
        _section, applied = self.transform_with(self.section_xml(head_off))
        self.assertEqual(applied["specialty"]["tables"], 1)
        # 둘 다 어긋나면 아니다.
        both = row_off.replace("<hp:t>연도</hp:t>", "<hp:t>해</hp:t>")
        _section, applied = self.transform_with(self.section_xml(both))
        self.assertEqual(applied["specialty"]["tables"], 0)
        self.assertEqual(applied["specialty"]["unmatched"], [{"table_shape": 5}])

    def test_blank_line_keeps_the_neighbor_attributes_but_gets_its_own_id(self):
        import re
        header, section, applied = self.transform("specialty_crops")
        blank = re.search(r"(<hp:p\b[^>]*>)<hp:run charPrIDRef=\"\d+\"><hp:t></hp:t>", section)
        self.assertIsNotNone(blank)
        # 바탕글 스타일로 새 문단 모양을 가진다(이웃의 문단 모양·스타일을 그대로 따르지 않는다).
        self.assertIn('styleIDRef="0"', blank[1])
        self.assertIn('id="', blank[1])

    def test_blank_line_ids_are_unique_in_the_document(self):
        import re
        table = _table([["연도", "면적", "비고"], ["2027", "100", "정식"]], [14000, 14000, 14520])
        xml = self.section_xml(table)
        # 문단마다 id를 준다(이웃 id가 그대로 복제되면 중복이 생기는 모양). 표 안 문단 id도 센다.
        counter = iter(range(10, 1000))
        xml = re.sub(r"<hp:p ", lambda m: '<hp:p id="%d" ' % next(counter), xml)
        section, applied = self.transform_with(xml)
        self.assertEqual(applied["specialty"]["blank_lines"], 2)
        ids = re.findall(r'<hp:p\b[^>]*?\sid="(\d+)"', section)
        self.assertEqual(len(ids), len(set(ids)))
        before = re.findall(r'<hp:p\b[^>]*?\sid="(\d+)"', xml)
        self.assertEqual(len(ids), len(before) + 2)
        self.assertGreater(min(int(i) for i in set(ids) - set(before)), max(int(i) for i in before))

    def test_blank_line_has_no_numbering_bullet_or_indent(self):
        import re
        # 이웃 문단(`본문 글이다.`)이 개요 번호·들여쓰기 문단 모양(paraPr 1)을 쓰는 경우.
        table = _table([["연도", "면적", "비고"], ["2027", "100", "정식"]], [14000, 14000, 14520])
        xml = self.section_xml(table).replace(
            _plain(0, 0, "본문 글이다."), _plain(1, 0, "본문 글이다."))
        header_xml = _hwpx_header().replace(
            '<hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/>',
            '<hc:intent value="-900" unit="HWPUNIT"/><hc:left value="900" unit="HWPUNIT"/>')
        self.assertIn("-900", header_xml)
        import io
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/header.xml", header_xml)
            archive.writestr("Contents/section0.xml", xml)
        nodes = self.gd.parse(HWPX_BODY, major_id="specialty_crops")
        data, applied = self.gh.transform_hwpx(
            buffer.getvalue(), font=None, margins_mm=None, page_numbers=False,
            nodes=nodes, plan=None, major_id="specialty_crops")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            section = archive.read("Contents/section0.xml").decode()
            header = archive.read("Contents/header.xml").decode()
        blank = re.search(r'<hp:p\b[^>]*?paraPrIDRef="(\d+)"[^>]*>'
                          r'<hp:run charPrIDRef="\d+"><hp:t></hp:t>', section)
        self.assertIsNotNone(blank)
        self.assertNotEqual(blank[1], "1")  # 이웃의 번호 붙는 문단 모양을 그대로 쓰지 않는다
        pr = re.search(r'<hh:paraPr\b[^>]*?\sid="%s"[^>]*>.*?</hh:paraPr>' % blank[1],
                       header, re.S)[0]
        self.assertIn('<hh:heading type="NONE" idRef="0" level="0"/>', pr)
        self.assertIn('<hc:intent value="0"', pr)
        self.assertIn('<hc:left value="0"', pr)
        # 원래 문단 모양(1)은 고치지 않는다.
        original = re.search(r'<hh:paraPr\b[^>]*?\sid="1"[^>]*>.*?</hh:paraPr>', header, re.S)[0]
        self.assertIn('type="OUTLINE"', original)
        self.assertIn('<hc:left value="900"', original)

    def test_blank_line_turns_off_page_and_column_breaks(self):
        # 이웃 문단에 쪽·단 나눔이 있으면 빈 줄까지 나눔이 두 번 들어가지 않게 끈다.
        import re
        table = _table([["연도", "면적", "비고"], ["2027", "100", "정식"]], [14000, 14000, 14520])
        xml = self.section_xml(table).replace(
            _plain(0, 0, "본문 글이다."),
            '<hp:p id="7" paraPrIDRef="0" styleIDRef="0" pageBreak="1" columnBreak="1" merged="0">'
            '<hp:run charPrIDRef="0"><hp:t>본문 글이다.</hp:t></hp:run></hp:p>')
        section, applied = self.transform_with(xml)
        blank = re.search(r'(<hp:p\b[^>]*>)<hp:run charPrIDRef="\d+"><hp:t></hp:t>', section)[1]
        self.assertNotIn('id="7"', blank)
        self.assertIn('merged="0"', blank)
        self.assertIn('pageBreak="0"', blank)
        self.assertIn('columnBreak="0"', blank)

    def test_table_too_wide_for_the_smallest_font_is_reported(self):
        # 어절 하나가 20자인 세 열: 9pt로 줄여도 세 열의 어절 폭 합이 본문 폭(150mm)을
        # 넘는다. 조용히 넘기지 않고 보고에 표 번호를 남기고 안내문 한 줄이 붙는다.
        words = ["가" * 20, "나" * 20, "다" * 20]
        md = HWPX_BODY.replace(
            "| 연도 | 면적 | 비고 |\n|--|--:|--|\n| 2027 | 100 | 정식 후 바로 수확한다 |",
            "| %s |\n|--|--|--|\n| %s |" % (" | ".join(words), " | ".join(words)))
        self.assertNotEqual(md, HWPX_BODY)
        table = _table([words, words], [14000, 14000, 14520])
        import io
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/header.xml", _hwpx_header())
            archive.writestr("Contents/section0.xml", self.section_xml(table))
        nodes = self.gd.parse(md, major_id="specialty_crops")
        _data, applied = self.gh.transform_hwpx(
            buffer.getvalue(), font=None, margins_mm=None, page_numbers=False,
            nodes=nodes, plan=None, major_id="specialty_crops")
        report = applied["specialty"]
        self.assertEqual(report["unmatched"], [])
        self.assertEqual(report["tables"], 1)
        self.assertEqual(len(report["table_text_too_wide"]), 1)
        notice = runtime("build_hwpx").specialty_notice(applied)
        self.assertIn("표 1개", notice)
        # 문제가 없으면 안내문도 없다.
        self.assertEqual(runtime("build_hwpx").specialty_notice({}), "")
        self.assertEqual(runtime("build_hwpx").specialty_notice(
            {"specialty": {"unmatched": [], "table_text_too_wide": []}}), "")
        self.assertIn("1곳", runtime("build_hwpx").specialty_notice(
            {"specialty": {"unmatched": [{"table": 5}]}}))

    def test_specialty_notice_has_a_line_for_skipped_table_widths(self):
        notice = runtime("build_hwpx").specialty_notice(
            {"specialty": {"table_widths_skipped": "gg_docx_table_layout_unavailable"}})
        self.assertIn("표 칸 폭 정리를 하지 못해", notice)
        self.assertNotIn("gg_docx_table_layout_unavailable", notice)

    def test_specialty_notice_speaks_plainly_about_the_stop_reason(self):
        notice = runtime("build_hwpx").specialty_notice
        for code in ("section_xml_unreadable", "paragraph_count_mismatch",
                     "specialty_style_missing", "something_new"):
            with self.subTest(code=code):
                text = notice({"specialty": {"status": code}})
                self.assertIn("적용하지 못했다", text)
                self.assertNotIn(code, text)
                self.assertNotIn("_", text)
        self.assertIn("문단 수", notice({"specialty": {"status": "paragraph_count_mismatch"}}))

    def test_other_majors_are_untouched(self):
        # 같은 입력을 특작 아닌 전공 id로 변환하면 덮개를 지나지 않는다.
        import re
        header, section, applied = self.transform("industrial_insects")
        self.assertNotIn("specialty", applied)
        self.assertEqual(section.count("<hp:t></hp:t>"), 0)
        ref = re.search(r'charPrIDRef="(\d+)"', self.paragraph_for(section, "Ⅰ. 서론"))[1]
        self.assertEqual(self.height_of(header, ref), 1000)
        table = re.search(r"<hp:tbl\b.*?</hp:tbl>", section, re.S)[0]
        self.assertEqual(set(re.findall(r'paraPrIDRef="(\d+)"', table)), {"0"})


if __name__ == "__main__":
    unittest.main()
