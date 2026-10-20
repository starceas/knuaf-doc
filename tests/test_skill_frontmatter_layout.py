"""gg_frontmatter_layout: the school's fixed form bands, rendered to DOCX.

Migrated from the GUI contract suite (cb72ae1).  When a value does not fit,
SKILL.md says the generator must not crop it — it must say so.  These tests
hold the module to refusing rather than silently trimming, and to keeping
lines it has no slot for instead of dropping them.
"""
import unittest

from tests._harness import ContractCase, runtime

BASE = {
    "mode": "school", "title": "도라지 재배 창업계획", "author": "홍길동",
    "department": "특용작물전공", "advisor": "김교수",
    "submission_date": "2026년 12월", "graduation_date": "2027년 2월",
    "committee": {"chair": "박교수", "members": ["이교수", "최교수", "정교수"]},
}


class FrontmatterLayoutTests(ContractCase):
    def setUp(self):
        self.fl = runtime("gg_frontmatter_layout")
        self.fm = runtime("gg_frontmatter")
        self.doc_mod = runtime("gg_document")
        self.Document = runtime("build_docx").Document

    def render(self, **over):
        """Drive the real path build_docx uses: lines → parse → plan → render."""
        profile = self.fm.normalize_school_profile(
            {"school_profile": {**BASE, **over}})
        text = ("\n\n".join(self.fm.frontmatter_lines(profile))
                + "\n\n목차\n\nⅠ. 머리말\n\n본문\n")
        nodes = self.doc_mod.parse(text)
        doc = self.Document()
        self.fl.render_forms(doc, nodes,
                             self.doc_mod.school_frontmatter_plan(nodes),
                             "신명조")
        return doc

    def all_text(self, doc):
        out = [p.text for p in doc.paragraphs]

        def walk(table):
            for row in table.rows:
                for cell in row.cells:
                    out.extend(p.text for p in cell.paragraphs)
                    for nested in cell.tables:
                        walk(nested)

        for t in doc.tables:
            walk(t)
        return "\n".join(out)

    # --- measurement: Korean is full width, ASCII is not ------------------

    def test_a_korean_character_counts_as_one_band_unit(self):
        self.assertIn("도라지 재배 창업계획", self.all_text(self.render()))

    # --- the ordinary case ------------------------------------------------

    def test_a_normal_profile_renders_the_three_forms(self):
        text = self.all_text(self.render())
        for value in ("도라지 재배 창업계획", "홍길동", "특용작물전공",
                      "한국농수산대학교", "농업전문학사 학위논문"):
            self.assertIn(value, text)

    def test_the_advisor_and_committee_reach_the_page(self):
        text = self.all_text(self.render())
        self.assertIn("김교수", text)
        for member in ("박교수", "이교수", "최교수", "정교수"):
            self.assertIn(member, text)

    def test_both_dates_reach_the_page_without_being_merged(self):
        text = self.all_text(self.render())
        self.assertIn("2026년 12월", text)
        self.assertIn("2027년 2월", text)

    # --- too long is refused, never cropped -------------------------------

    def test_a_title_that_overflows_its_band_is_refused_not_trimmed(self):
        with self.assertRaisesRegex(ValueError, "표지 제목/부제가 허용 영역을 초과"):
            self.render(title="가" * 200)

    def test_the_refusal_says_what_to_do(self):
        with self.assertRaisesRegex(ValueError, "제목을 줄이거나 별도 양식 프로필"):
            self.render(title="가" * 200)

    def test_a_title_that_still_fits_is_kept_whole(self):
        title = "도라지 및 더덕 복합재배를 통한 산지형 특용작물 창업 영농계획 수립"
        self.assertIn(title, self.all_text(self.render(title=title)))

    def test_a_single_line_field_that_overflows_is_refused(self):
        for field, limit in (("department", 22), ("author", 22)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "한 줄 필드가 너무 김"):
                    self.render(**{field: "가" * (limit + 1)})

    def test_a_single_line_field_exactly_at_the_limit_is_accepted(self):
        for field, limit in (("department", 22), ("author", 22)):
            with self.subTest(field=field):
                value = "가" * limit
                self.assertIn(value, self.all_text(self.render(**{field: value})))

    def test_an_advisor_line_that_overflows_is_refused(self):
        with self.assertRaisesRegex(
                ValueError, "지도교수 표시가 학교 양식의 한 줄 영역을 초과"):
            self.render(advisor="가" * 24)

    # --- structure the form depends on ------------------------------------

    def test_a_truncated_frontmatter_yields_no_plan_at_all(self):
        nodes = self.doc_mod.parse(
            "겉표지\n\n농업전문학사 학위논문\n\n목차\n\nⅠ. 머리말\n\n본문\n")
        self.assertIsNone(self.doc_mod.school_frontmatter_plan(nodes))

    def test_a_complete_frontmatter_yields_a_plan_with_every_form(self):
        profile = self.fm.normalize_school_profile({"school_profile": BASE})
        text = ("\n\n".join(self.fm.frontmatter_lines(profile))
                + "\n\n목차\n\nⅠ. 머리말\n\n본문\n")
        plan = self.doc_mod.school_frontmatter_plan(self.doc_mod.parse(text))
        self.assertIsNotNone(plan)
        for form in ("겉표지", "표제면", "제출서", "인준서", "목차"):
            self.assertIn(form, plan["indices"])

    def test_more_than_two_title_paragraphs_are_refused(self):
        profile = self.fm.normalize_school_profile({"school_profile": BASE})
        lines = self.fm.frontmatter_lines(profile)
        at = lines.index("겉표지") + 4
        lines[at:at] = ["군더더기 제목 줄 1", "군더더기 제목 줄 2"]
        text = "\n\n".join(lines) + "\n\n목차\n\nⅠ. 머리말\n\n본문\n"
        nodes = self.doc_mod.parse(text)
        with self.assertRaisesRegex(ValueError, "제목과 부제는 각 한 문단"):
            self.fl.render_forms(self.Document(), nodes,
                                 self.doc_mod.school_frontmatter_plan(nodes),
                                 "신명조")

    # --- lines with no slot are shown, not dropped ------------------------

    def test_an_unassigned_line_is_kept_on_the_page(self):
        profile = self.fm.normalize_school_profile({"school_profile": BASE})
        lines = self.fm.frontmatter_lines(profile)
        note = "검토 메모: 지도교수 확인 대기"
        lines.insert(lines.index("인준서"), note)
        text = "\n\n".join(lines) + "\n\n목차\n\nⅠ. 머리말\n\n본문\n"
        nodes = self.doc_mod.parse(text)
        doc = self.Document()
        self.fl.render_forms(doc, nodes,
                             self.doc_mod.school_frontmatter_plan(nodes),
                             "신명조")
        self.assertIn(note, self.all_text(doc))

    def test_an_unassigned_block_that_overflows_is_refused_rather_than_dropped(self):
        profile = self.fm.normalize_school_profile({"school_profile": BASE})
        lines = self.fm.frontmatter_lines(profile)
        lines.insert(lines.index("인준서"), "가" * 200)
        text = "\n\n".join(lines) + "\n\n목차\n\nⅠ. 머리말\n\n본문\n"
        nodes = self.doc_mod.parse(text)
        with self.assertRaisesRegex(ValueError, "초과"):
            self.fl.render_forms(self.Document(), nodes,
                                 self.doc_mod.school_frontmatter_plan(nodes),
                                 "신명조")

    # --- the contents page carries real page references -------------------

    def test_the_contents_uses_page_reference_fields_not_typed_numbers(self):
        doc = self.Document()
        self.fl.contents(doc, [("Ⅰ. 머리말", "gg_body_1", 1)], "신명조")
        xml = doc.element.xml
        self.assertIn("PAGEREF gg_body_1", xml)
        self.assertIn("fldSimple", xml)

    def test_a_bookmark_is_written_with_the_name_the_contents_points_at(self):
        doc = self.Document()
        p = doc.add_paragraph("Ⅰ. 머리말")
        self.fl.bookmark(p, "gg_body_1", 9001)
        xml = doc.element.xml
        self.assertIn('w:name="gg_body_1"', xml)
        self.assertIn("bookmarkStart", xml)
        self.assertIn("bookmarkEnd", xml)


if __name__ == "__main__":
    unittest.main()
