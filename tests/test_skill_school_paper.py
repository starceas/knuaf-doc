"""gg_school_paper: the first artifact a student gets, and the input to build_docx.

Migrated from the GUI contract suite (cb72ae1).  The contract that matters is
honesty about missing values: SKILL.md says unsupplied values stay
``[확인 필요]``.  A regression that turns a missing number into 0 or an empty
cell would put an invented figure in a submitted thesis.

The frozen renderer keeps its generation logic unchanged; the caller-body splice
lives in ``core.paper`` and is covered by test_p1_gui_inherit.
"""
import re
import unittest

from tests._harness import ContractCase, runtime

MINIMAL = {"title": "테스트 논문", "student": "홍길동",
           "department": "특용작물전공", "advisor": "김교수",
           "writing_year": 2026}


class SchoolPaperTests(ContractCase):
    def setUp(self):
        self.sp = runtime("gg_school_paper")
        self._ctx = None

    def ctx(self):
        """Policy B: the frozen renderer needs the project context."""
        if self._ctx is None:
            from tests._harness import bind_major
            project = self.make_project()
            bind_major(project)
            self._ctx = runtime("gg_major_contract").output_context(
                project, "specialty_crops")
        return self._ctx

    def paper(self, spec):
        return self.sp.paper(spec, context=self.ctx())

    # --- years(): the 5-year plan window is derived, not supplied ---------

    def test_years_derives_a_five_year_window_from_the_writing_year(self):
        writing, start, end, span = self.sp.years({"writing_year": 2026})
        self.assertEqual((2026, 2027, 2031), (writing, start, end))
        self.assertEqual([2027, 2028, 2029, 2030, 2031], span)

    def test_years_refuses_a_writing_year_it_cannot_trust(self):
        for bad in (None, "2026", 2026.0, True, 1899, 2101, {}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "작성연도"):
                    self.sp.years({"writing_year": bad})

    # --- need(): missing stays missing ------------------------------------

    def test_need_takes_the_first_supplied_key(self):
        self.assertEqual(
            "값", self.sp.need({"a": "", "b": "  ", "c": "값"}, "a", "b", "c"))

    def test_need_falls_back_to_the_pending_marker_never_to_blank(self):
        for spec in ({}, {"a": None}, {"a": ""}, {"a": "   "}, None, 42, ""):
            with self.subTest(spec=spec):
                got = self.sp.need(spec, "a")
                self.assertEqual("[확인 필요]", got)
                self.assertEqual(self.sp.PENDING, got)
                self.assertNotIn(got, ("", "0", "None"))

    def test_need_accepts_a_bare_string_as_the_value(self):
        self.assertEqual("직접 값", self.sp.need("직접 값", "a"))

    # --- the generated skeleton -------------------------------------------

    def test_paper_emits_the_six_school_chapters_in_order(self):
        out = self.paper(MINIMAL)
        chapters = ["Ⅰ. 머리말", "Ⅱ. 외부환경분석", "Ⅲ. 영농계획수립",
                    "Ⅳ. 재무계획", "Ⅴ. 맺음말 및 발전방향", "Ⅵ. 참고문헌"]
        positions = [out.find(c) for c in chapters]
        self.assertTrue(all(p >= 0 for p in positions),
                        dict(zip(chapters, positions)))
        self.assertEqual(sorted(positions), positions)

    def test_paper_keeps_supplied_values_and_marks_the_rest_pending(self):
        out = self.paper(MINIMAL)
        for supplied in ("테스트 논문", "김교수"):
            self.assertIn(supplied, out)
        self.assertGreater(out.count(self.sp.PENDING), 50)

    def test_paper_never_invents_a_submission_date(self):
        out = self.paper(MINIMAL)
        self.assertIsNone(re.search(r"20\d{2}\s*년\s*\d{1,2}\s*월", out))

    def test_paper_refuses_a_spec_without_a_writing_year(self):
        with self.assertRaisesRegex(ValueError, "작성연도"):
            self.paper({k: v for k, v in MINIMAL.items()
                           if k != "writing_year"})

    # --- tables the school form fixes -------------------------------------

    def test_table_renders_a_pipe_table_build_docx_can_parse(self):
        md = self.sp.table(["가", "나"], [[1, 2], ["a", "b"]])
        rows = md.splitlines()
        self.assertEqual("|가|나|", rows[0])
        self.assertEqual("|---|---|", rows[1])
        self.assertEqual("|1|2|", rows[2])
        self.assertTrue(all(r.startswith("|") and r.endswith("|")
                            for r in rows))

    def test_disaster_table_keeps_every_school_category_even_when_unanswered(self):
        md = self.sp.disaster_table({})
        for category in self.sp.DISASTER:
            self.assertIn(category, md)
        self.assertIn(self.sp.PENDING, md)

    def test_climate_table_keeps_every_school_row_and_season(self):
        md = self.sp.climate_table({})
        for row in self.sp.CLIMATE:
            self.assertIn(row, md)
        for season in self.sp.SHORT_SEASONS:
            self.assertIn(season, md)
        self.assertIn(self.sp.PENDING, md)

    def test_submitted_date_is_printed_when_supplied(self):
        out = self.paper({**MINIMAL, "submitted": "2026년 12월"})
        self.assertIn("2026년 12월", out)

    # --- the unchanged legacy paths ---------------------------------------

    def test_paper_without_a_school_profile_is_byte_for_byte_the_legacy_output(self):
        self.assertEqual(self.sp._legacy_paper(MINIMAL),
                         self.paper(MINIMAL))


if __name__ == "__main__":
    unittest.main()
