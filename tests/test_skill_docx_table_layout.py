"""gg_docx_table_layout: keeping numbers off two lines in the school's tables.

Migrated from the GUI contract suite (cb72ae1).  A figure broken across lines
("121,24" / "6.7") reads as two numbers.  The module widens columns and steps
the font down to prevent that — but it must never buy a fit with the values
themselves or with a font size nobody can read.
"""
import unittest

from tests._harness import ContractCase, runtime

FINANCE = [["연도", "매출액", "경비", "순이익"],
           ["2027", "121,246.7", "98,432.1", "22,814.6"],
           ["2028", "133,371.4", "104,287.5", "29,083.9"]]
WIDE = [["항목"] + ["%d년차" % i for i in range(1, 9)],
        ["매출액"] + ["1,234,567.8"] * 8]
TEXT_ONLY = [["구분", "내용"], ["작목", "도라지"], ["지역", "전북 정읍시 북면"]]


class TableLayoutTests(ContractCase):
    def setUp(self):
        self.tl = runtime("gg_docx_table_layout")
        self.Document = runtime("build_docx").Document

    def table_of(self, rows):
        doc = self.Document()
        t = doc.add_table(rows=len(rows), cols=len(rows[0]))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
        return doc, t

    # --- what a number is -------------------------------------------------

    def test_figures_a_thesis_actually_prints_are_treated_as_numbers(self):
        for token in ("1,200", "12.5", "121,246.7", "(121,246.7)", "-5", "−5",
                      "–5", "+3", "95%", "12.5%", "2026", "0", "(5)", "１２３"):
            with self.subTest(token=token):
                self.assertIs(True, self.tl.is_numeric_token(token))

    def test_prose_is_not_treated_as_a_number(self):
        for token in ("", "   ", "도라지", "600평", "abc", "...", "–", "%"):
            with self.subTest(token=token):
                self.assertIs(False, self.tl.is_numeric_token(token))

    def test_a_wider_number_needs_more_room(self):
        at10 = self.tl.token_width_mm
        self.assertGreater(at10("121,246.7", 10.0), at10("1,200", 10.0))
        self.assertGreater(at10("1,200", 10.0), at10("5", 10.0))

    def test_a_smaller_font_needs_less_room(self):
        self.assertLess(self.tl.token_width_mm("121,246.7", 9.0),
                        self.tl.token_width_mm("121,246.7", 10.0))

    # --- the ordinary case ------------------------------------------------

    def test_a_finance_table_fits_at_full_size(self):
        _, t = self.table_of(FINANCE)
        report = self.tl.fit_table(t)
        self.assertIs(True, report["applied"])
        self.assertIs(True, report["feasible"])
        self.assertEqual(10.0, report["font_pt"])
        self.assertIs(False, report["rotated_to_landscape"])
        self.assertEqual([], report["warnings"])

    def test_column_widths_are_assigned_within_the_text_area(self):
        _, t = self.table_of(FINANCE)
        report = self.tl.fit_table(t, text_area_mm=150.0)
        self.assertEqual(4, len(report["columns"]))
        self.assertTrue(all(w > 0 for w in report["columns"]))
        self.assertLessEqual(sum(report["columns"]), 150.0 + 0.5)

    def test_a_table_with_no_numbers_is_left_alone(self):
        _, t = self.table_of(TEXT_ONLY)
        report = self.tl.fit_table(t)
        self.assertIs(False, report["applied"])
        self.assertIsNone(report["columns"])
        self.assertTrue(any("no numeric cells" in w
                            for w in report["warnings"]))

    # --- the two things it must never trade away --------------------------

    def test_no_cell_text_is_ever_changed(self):
        for rows in (FINANCE, WIDE):
            for landscape in (True, False):
                with self.subTest(rows=rows[0], landscape=landscape):
                    _, t = self.table_of(rows)
                    before = [[c.text for c in row.cells] for row in t.rows]
                    self.tl.fit_table(t, allow_landscape=landscape)
                    after = [[c.text for c in row.cells] for row in t.rows]
                    self.assertEqual(before, after)

    def test_the_font_never_drops_below_the_readable_floor(self):
        for landscape in (True, False):
            with self.subTest(landscape=landscape):
                _, t = self.table_of(WIDE)
                report = self.tl.fit_table(
                    t, min_font_pt=9.0, allow_landscape=landscape)
                self.assertGreaterEqual(report["font_pt"], 9.0)

    def test_an_impossible_table_reports_failure_instead_of_faking_a_fit(self):
        _, t = self.table_of(WIDE)
        report = self.tl.fit_table(t, allow_landscape=False)
        self.assertIs(False, report["feasible"])
        self.assertEqual(9.0, report["font_pt"])
        self.assertIs(False, report["rotated_to_landscape"])
        self.assertTrue(any("cannot fit" in w for w in report["warnings"]))
        self.assertTrue(any("values preserved" in w
                            for w in report["warnings"]))

    def test_a_custom_floor_is_respected_too(self):
        _, t = self.table_of(WIDE)
        report = self.tl.fit_table(t, min_font_pt=9.5, allow_landscape=False)
        self.assertGreaterEqual(report["font_pt"], 9.5)

    # --- rotation is opt-in, and it says so -------------------------------

    def test_rotation_only_happens_when_it_is_allowed(self):
        _, t = self.table_of(WIDE)
        self.assertIs(
            False, self.tl.fit_table(t, allow_landscape=False)["rotated_to_landscape"])

    def test_rotation_when_allowed_is_reported_not_silent(self):
        _, t = self.table_of(WIDE)
        report = self.tl.fit_table(t, allow_landscape=True)
        self.assertIs(True, report["rotated_to_landscape"])
        self.assertIs(True, report["feasible"])
        self.assertTrue(any("landscape" in w for w in report["warnings"]))
        self.assertGreater(report["text_area_mm"], 150.0)

    # --- the report shape build_docx records ------------------------------

    def test_every_report_carries_the_fields_build_docx_writes(self):
        _, t = self.table_of(FINANCE)
        report = self.tl.fit_table(t)
        self.assertGreaterEqual(
            set(report),
            {"applied", "feasible", "font_pt", "columns", "warnings",
             "rotated_to_landscape", "text_area_mm"})

    def test_fit_tables_reports_one_entry_per_table(self):
        doc = self.Document()
        tables = []
        for rows in (FINANCE, TEXT_ONLY, WIDE):
            t = doc.add_table(rows=len(rows), cols=len(rows[0]))
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    t.cell(r, c).text = val
            tables.append(t)
        reports = self.tl.fit_tables(tables, allow_landscape=False)
        self.assertEqual(3, len(reports))
        self.assertEqual([True, False, True], [r["applied"] for r in reports])


if __name__ == "__main__":
    unittest.main()
