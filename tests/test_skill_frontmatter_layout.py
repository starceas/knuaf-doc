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


class PeerFrontmatterTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.gd = runtime("gg_document")
        self.builder = runtime("build_docx")
        self.mc = runtime("gg_major_contract")

    def manuscript(self, major, **metadata):
        profile = self.fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월", **metadata}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "동의·날인 확인 대기", "summary": "합성 요약 내용",
            "thanks": "합성 감사 내용"})
        chapter = "1. 머리말" if major == "dairy" else "Ⅰ. 머리말"
        return "\n\n".join(lines + ["# " + chapter, "합성 본문 내용"])

    def convert(self, major, text=None):
        from tests._harness import bind_major
        root = self.make_project()
        bind_major(root, major)
        doc = self.builder.convert(text or self.manuscript(major),
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

    def test_fruit_s01_cover_and_shared_submission_approval_page(self):
        pages = self.pages(self.convert("fruit_trees"))
        self.assertIn("영농창업/승계 발전계획", pages[0])
        self.assertIn("원예학부 과수전공", pages[0])
        self.assertNotIn("으로 제출함", pages[0])
        self.assertIn("이 논문을 농업전문학사 학위 논문으로 제출함", pages[1])
        self.assertIn("가상학생의 농업전문학사 학위논문을 인준함", pages[1])
        self.assertIn("2026년 12월", pages[1])
        self.assertIn("2027년 2월", pages[0])
        self.assertEqual(pages[1].count("(인)"), 3)
        self.assertIn("목    차", pages[2])

    def test_fruit_preliminary_numbers_continue_from_cover_and_body_restarts(self):
        from docx.oxml.ns import qn
        text = self.manuscript("fruit_trees").replace(
            "# Ⅰ. 머리말", "요약\n\n합성 요약 산문\n\n# Ⅰ. 머리말")
        doc = self.convert("fruit_trees", text)
        first, preliminary, body = doc.sections
        first_number = first._sectPr.find(qn("w:pgNumType"))
        self.assertIsNotNone(first_number)
        self.assertEqual("1", first_number.get(qn("w:start")))
        self.assertEqual("lowerRoman", first_number.get(qn("w:fmt")))
        self.assertNotIn("PAGE", first.footer._element.xml)
        number = preliminary._sectPr.find(qn("w:pgNumType"))
        self.assertEqual("lowerRoman", number.get(qn("w:fmt")))
        self.assertIsNone(number.get(qn("w:start")))
        self.assertIn("PAGE", preliminary.footer._element.xml)
        number = body._sectPr.find(qn("w:pgNumType"))
        self.assertEqual("decimal", number.get(qn("w:fmt")))
        self.assertEqual("1", number.get(qn("w:start")))

    def test_fruit_page_number_elements_keep_schema_order(self):
        from docx.oxml.ns import qn
        text = self.manuscript("fruit_trees").replace(
            "# Ⅰ. 머리말", "요약\n\n합성 요약 산문\n\n# Ⅰ. 머리말")
        doc = self.convert("fruit_trees", text)
        self.assertEqual(3, len(doc.sections))
        for index, section in enumerate(doc.sections):
            children = [child.tag for child in section._sectPr]
            numbers = [tag for tag in children if tag == qn("w:pgNumType")]
            self.assertEqual(1, len(numbers), f"section {index}: {children}")
            position = children.index(qn("w:pgNumType"))
            for successor in ("w:cols", "w:docGrid"):
                tag = qn(successor)
                if tag in children:
                    self.assertLess(position, children.index(tag),
                                    f"section {index}: {children}")

    def test_fruit_without_preliminary_pages_still_restarts_body_at_one(self):
        from docx.oxml.ns import qn
        doc = self.convert("fruit_trees")
        self.assertEqual(2, len(doc.sections))
        number = doc.sections[-1]._sectPr.find(qn("w:pgNumType"))
        self.assertEqual("decimal", number.get(qn("w:fmt")))
        self.assertEqual("1", number.get(qn("w:start")))
        self.assertEqual(1, len(doc.sections[-1]._sectPr.findall(qn("w:pgNumType"))))

    def test_horticulture_ht2_two_covers_submission_approval_and_notice(self):
        pages = self.pages(self.convert("hort_env_systems"))
        for page in pages[:2]:
            self.assertIn("농업전문학사 학위논문", page)
            self.assertIn("합성 영농계획", page)
            self.assertIn("원예환경시스템전공", page)
            self.assertNotIn("영농창업계획", page)
        self.assertIn("신청서·추천서는 학교 서식으로 따로 제출", pages[0])
        self.assertIn("이 논문을 농업전문학사 학위논문으로 제출함", pages[2])
        self.assertIn("인준함", pages[3])
        self.assertIn("목    차", pages[4])
        all_text = "\n".join(pages)
        self.assertNotIn("논문지도교수 신청서", all_text)
        self.assertNotIn("논문심사위원 추천서", all_text)

    def test_horticulture_advisor_is_on_submission_not_second_cover(self):
        text = self.manuscript("hort_env_systems", advisor="가상지도교수")
        pages = self.pages(self.convert("hort_env_systems", text))
        self.assertNotIn("가상지도교수", pages[0])
        self.assertNotIn("가상지도교수", pages[1])
        self.assertIn("지도교수 가상지도교수", pages[2])
        self.assertIn("으로 제출함", pages[2])

    def test_same_markers_do_not_allow_another_major_frontmatter(self):
        for source, bound in (("fruit_trees", "hort_env_systems"),
                              ("hort_env_systems", "fruit_trees"),
                              ("fruit_trees", "specialty_crops"),
                              ("specialty_crops", "hort_env_systems")):
            with self.subTest(source=source, bound=bound):
                text = self.manuscript(source, department=self.gd.school_frontmatter_spec(source).department)
                with self.assertRaises(self.mc.OutputHeldError) as caught:
                    self.convert(bound, text)
                self.assertEqual(caught.exception.reason, "major_marker_conflict")

    def test_title_naming_another_major_is_not_a_department(self):
        text = self.manuscript("hort_env_systems", title="원예학부 과수전공")
        pages = self.pages(self.convert("hort_env_systems", text))
        self.assertIn("원예학부 과수전공", pages[0])
        self.assertIn("원예환경시스템전공", pages[0])

    def test_matching_cover_does_not_hide_conflicting_submission_department(self):
        text = self.manuscript("hort_env_systems")
        before, submission = text.split("제출서", 1)
        submission = submission.replace("원예환경시스템전공", "원예학부 과수전공", 1)
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.convert("hort_env_systems", before + "제출서" + submission)
        self.assertEqual(caught.exception.reason, "major_marker_conflict")

    def test_dairy_keeps_preliminary_prose_before_contents_and_body(self):
        doc = self.convert("dairy")
        text = "\n".join(p.text for p in doc.paragraphs)
        markers = ["으로 제출함", "인준함", "학부모 동의서", "동의·날인 확인 대기",
                   "요약", "합성 요약 내용", "감사의 글", "합성 감사 내용", "목    차",
                   "표 목차", "그림 목차", "합성 본문 내용"]
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("학교 양식 확인 필요", text)
        self.assertEqual(doc.tables, [])
        self.assertEqual(text.count("합성 본문 내용"), 1)

    def test_major_selection_does_not_infer_from_title(self):
        text = self.manuscript("dairy")
        nodes = self.gd.parse(text)
        self.assertIsNone(self.gd.school_frontmatter_plan(nodes, "fruit_trees"))
        self.assertIsNone(self.gd.school_frontmatter_plan(nodes, "unregistered_probe_major"))
        with self.assertRaisesRegex(ValueError, "필수 항목 또는 순서"):
            self.convert("fruit_trees", text)

    def test_unassigned_peer_content_is_preserved(self):
        text = self.manuscript("fruit_trees").replace("인준서", "합성 검토 메모\n\n인준서", 1)
        doc = self.convert("fruit_trees", text)
        self.assertIn("합성 검토 메모", "\n".join(p.text for p in doc.paragraphs))

    def test_conflicting_major_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "불일치"):
            self.fm.normalize_school_profile({"major_id": "fruit_trees",
                "school_profile": {"major_id": "dairy"}})

    def test_extra_committee_member_is_not_silently_discarded(self):
        profile = self.fm.normalize_school_profile({"major_id": "fruit_trees",
            "school_profile": {"committee": {"members": ["가상위원1", "가상위원2", "가상위원3"]}}})
        with self.assertRaisesRegex(ValueError, "2명까지"):
            self.fm.frontmatter_lines(profile)


if __name__ == "__main__":
    unittest.main()
