"""Synthetic C1 spec/precondition/vector/mutant tests (D7 r5, --check style).

Real-file verification is an explicit local integration step in
``tests/local_real_sources.py``; suite tests use synthetic fixtures only.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_workbook_audit_evaluator import audit_module

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = (ROOT / "skills/knuaf-doc/references/common-workbooks"
             "/corrections/c1-spec.json")

S9 = "9 .경비계획"
S10 = "10. 감가상각비계획 "
S11 = "11. 생산원가계획"
S15 = "15.추정소득분석"
S14 = "14. 현금흐름계획"
S8 = "8. 노무비계획"
S3 = "3.투자계획"
P11 = "'11. 생산원가계획'!"
AUDIT_CLI = ROOT / "skills/knuaf-doc/scripts/gg_workbook_audit.py"
SHEETS = ["목록", "1. 기초재무상태조사", "2. 중장기영농목표", "3.투자계획",
          "4. 원리금상환계획", " 5. 판매계획", "6. 생산계획",
          "7. 영농자재소요계획", "8. 노무비계획", S9, S10, S11,
          "12 손익계획", "13. 추정대차대조표", "14. 현금흐름계획", S15,
          "참고1. 모델농장분석"]


def synthetic_book():
    """In-memory workbook carrying the same defective formulas at the same
    coordinates as the pinned sources (values are synthetic)."""
    a = audit_module()
    b = a.Workbook()
    b.sheets = list(SHEETS)

    def f(sheet, cell, text):
        b.formulas[sheet, cell] = a.FORMULA(text, None, None)

    def v(sheet, cell, value):
        b.values[sheet, cell] = value

    # Sheet 11 year columns C..G; row detail cells are plain inputs.
    for c in "CDEFG":
        for r in (26, 27, 28, 31, 32, 33, 34):
            v(S11, c + str(r), 100)
        f(S11, c + "16", "{}17+{}20+{}25+{}21+{}29+{}30".format(*([c] * 6)))
        v(S11, c + "17", 0); v(S11, c + "20", 0); v(S11, c + "21", 0)
    f(S11, "C25", "SUM(C26:C27)")
    for c in "DEFG":
        f(S11, c + "25", "SUM(C26:C27)")          # XR-02 defect
        f(S11, c + "29", "0")                      # XR-19 defect
        f(S11, c + "30", "'9 .경비계획'!$C29")       # XR-18 defect
    f(S11, "C29", "'9 .경비계획'!$C28")
    f(S11, "C30", "'9 .경비계획'!$C29")
    f(S11, "C35", "C31+C32-C33-C34")
    f(S11, "D35", "D31")                            # XR-09 defect
    for c in "EFG":
        f(S11, c + "35", c + "31+$C$38")            # XR-09 defect

    # Sheet 15 cells (E..I years 1..5); already-correct cells keep their map.
    for cell, src in {"E21": "C26", "F21": "C26", "G21": "E26",
                      "H21": "E26", "I21": "F26"}.items():
        f(S15, cell, "'11. 생산원가계획'!" + src)
    for cell, src in {"E22": "C27", "F22": "D27", "G22": "E27",
                      "H22": "E27", "I22": "F27"}.items():
        f(S15, cell, "'11. 생산원가계획'!" + src)
    for cell in ("F14", "G14", "H14", "I14"):
        f(S15, cell, "'11. 생산원가계획'!C29")        # XR-19 defect

    # Sheet 9 expense blocks: year blocks end at C50/C70/C90/C110 subtotals.
    for r in list(range(28, 51)) + list(range(55, 71)) + \
             list(range(75, 91)) + list(range(95, 111)):
        v(S9, "C" + str(r), 0)
    for sub, members in ((50, (35, 38, 40, 44, 47, 48, 49)),
                         (70, (55, 58, 60, 64, 67, 68, 69)),
                         (90, (75, 78, 80, 84, 87, 88, 89)),
                         (110, (95, 98, 100, 104, 107, 108, 109))):
        f(S9, "C" + str(sub), "+".join("C" + str(m) for m in members))

    # Sheet 10: H carry cells are stored constants (X01 XR-22 defect);
    # other asset columns already carry formulas.
    v(S10, "H8", 100)
    for cell in ("H20", "H32", "H44", "H56"):
        v(S10, cell, 100)
    for col in "CDEF":
        prev = "8"
        for row in (20, 32, 44, 56):
            f(S10, col + str(row), col + prev)
            prev = str(row)
        v(S10, col + "8", 50)
    for row in (20, 32, 44, 56):
        f(S10, "G" + str(row), "SUM(C{0}:F{0})".format(row))
        f(S10, "I" + str(row), "SUM(H{0}:H{0})".format(row))

    # --- C1b defect structures (X01 shape, same coordinates as the
    # pinned source) ----------------------------------------------------
    # Sheet 15 surplus block: row 5 주산물, 6 부산물, 7 계, 26 비용, 27 영농잉여,
    # 28 영농잉여율.  X01 already uses row 7; E7 is a stored 0 (XR-04 defect).
    v(S15, "E5", 100)
    v(S15, "E6", 20)
    v(S15, "E7", 0)                                  # XR-04 value_to_formula defect
    f(S15, "E26", "SUM(E20:E25)")
    for r in range(20, 26):
        v(S15, "E" + str(r), 12)
    f(S15, "E27", "E7-E26")
    v(S15, "E28", 0)                                 # HELD first-year rate
    for c in "FGHI":
        v(S15, c + "5", 100)
        v(S15, c + "6", 20)
        f(S15, c + "7", "SUM({0}5,{0}6)".format(c))
        v(S15, c + "26", 60)
        f(S15, c + "27", "{0}7-{0}26".format(c))
        f(S15, c + "28", "{0}27/{0}7*100".format(c))  # X01 percent scale

    # Sheet 3 investment: first-year 계 rows already sum F:H (loan included),
    # later-year rows drop the loan column (XR-05 defect).
    for r in range(19, 29):
        for c in "EFGH":
            v(S3, c + str(r), 0)
    for r in (19, 20, 21, 22):
        f(S3, "E" + str(r), "SUM(F{0}:H{0})".format(r))
    for r in (25, 26, 27, 28):
        f(S3, "E" + str(r), "SUM(F{0}:G{0})".format(r))

    # Sheet 10 H column: residual-rate input row 9 (stored 0.1) and the rate
    # chain; the five annual rows hard-code 0.1 instead (XR-06 defect).
    v(S10, "H9", 0.1)
    v(S10, "H10", 10)
    prev_rate, prev_life = "H9", "H10"
    for base in (8, 20, 32, 44, 56):
        rate, life = "H" + str(base + 1), "H" + str(base + 2)
        if base == 8:
            pass  # H9/H10 are stored inputs
        else:
            f(S10, rate, prev_rate)
            f(S10, life, prev_life)
        f(S10, "H" + str(base + 4),
          "(H{0}-H{0}*0.1)/H{1}".format(base, base + 2))
        prev_rate, prev_life = rate, life

    # Sheet 11 cost structure: C31 = C5+C11+C16; 노무비 C11 sums the 급여
    # 자가노동비 link C12 and the 고용노동비 link C15; depreciation C21.
    labour_cols = {"C": "H", "D": "K", "E": "N", "F": "Q", "G": "T"}
    for c in "CDEFG":
        f(S11, c + "5", "SUM({0}6:{0}10)".format(c))
        for r in range(6, 11):
            v(S11, c + str(r), 100)
        f(S11, c + "11", "SUM({0}12:{0}15)".format(c))
        f(S11, c + "12", "'8. 노무비계획'!" + labour_cols[c] + "10")
        v(S11, c + "13", 0)
        v(S11, c + "14", 0)
        f(S11, c + "15", "'8. 노무비계획'!" + labour_cols[c] + "13")
        f(S11, c + "21", "SUM({0}22:{0}24)".format(c))
        for r in (22, 23, 24):
            v(S11, c + str(r), 100)
        f(S11, c + "31", "{0}5+{0}11+{0}16".format(c))

    # Sheet 8 labour year columns: row 10 = 자가 노동비 소계, row 13 = 고용
    # 노동비 소계.
    for col in "HKNQT":
        v(S8, col + "8", 0)
        v(S8, col + "9", 0)
        f(S8, col + "10", "{0}8+{0}9".format(col))
        v(S8, col + "11", 0)
        v(S8, col + "12", 0)
        f(S8, col + "13", "{0}11+{0}12".format(col))

    # Sheet 14 cash outflow: 당기총생산 원가 without the imputed-labour term
    # (XR-11 defect; X01's E column carries a leading space in the stored text).
    for d, y in zip("DEFGH", "CDEFG"):
        f(S14, d + "17", P11 + y + "31-" + P11 + y + "21")
    b.formulas[S14, "E17"] = a.FORMULA(
        " " + b.formulas[S14, "E17"].text, None, None)
    return a, b


class SpecShapeTests(unittest.TestCase):
    def setUp(self):
        self.a = audit_module()
        self.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    def test_schema_sources_and_row_counts(self):
        self.assertEqual(self.spec["schema"], "knuaf-c1-correction-spec/v1")
        self.assertEqual(set(self.spec["sources"]), {"X01", "X02", "SEO"})
        for meta in self.spec["sources"].values():
            self.assertEqual(len(meta["sha256"]), 64)
            int(meta["sha256"], 16)
        counts = {k: len(v) for k, v in self.spec["files"].items()}
        # C1 (30/30/30) + C1b policy items (15/28/33).
        self.assertEqual(counts, {"X01": 49, "X02": 58, "SEO": 63})

    def test_rows_are_whitelisted_and_table_consistent(self):
        allowed = {"finding_id", "sheet", "cell", "class",
                   "expected_formula", "expected_cell_digest", "new_formula"}
        for ref, rows in self.spec["files"].items():
            for row in rows:
                self.assertLessEqual(set(row), allowed, row)
                self.assertTrue(row["new_formula"].startswith("="))
                if row["class"] == "formula_repoint":
                    self.assertTrue(row["expected_formula"].startswith("="))
                else:
                    self.assertEqual(len(row["expected_cell_digest"]), 64)
                    self.assertEqual(ref, "X01")
        # The 30 group-A rows must be identical across the three sources.
        base = self.spec["files"]["X01"][:30]
        for ref in ("X02", "SEO"):
            self.assertEqual(self.spec["files"][ref][:30], base)
        # Tail rows differ per source shape: X01 also carries the XR-22
        # carry-over rows; the C1b policy findings are XR-04/05/06/11.
        self.assertEqual(
            {r["finding_id"] for r in self.spec["files"]["X01"][30:]},
            {"XR-22", "XR-04", "XR-05", "XR-06", "XR-11"})
        for ref in ("X02", "SEO"):
            self.assertEqual(
                {r["finding_id"] for r in self.spec["files"][ref][30:]},
                {"XR-04", "XR-05", "XR-06", "XR-11"})

    def test_no_numeric_example_values_shipped(self):
        text = SPEC_PATH.read_text(encoding="utf-8")
        self.assertNotIn("expected_value", text)
        self.assertNotIn('"v"', text)


class PreconditionTests(unittest.TestCase):
    def setUp(self):
        self.a = audit_module()
        self.spec = self.a.c1_load_spec(SPEC_PATH)

    def test_shared_precondition_rejects_wrong_source_and_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.xlsx"
            bad.write_bytes(b"not the pinned source")
            with self.assertRaises(ValueError) as ctx:
                self.a.c1_precondition_check(self.spec, "X01", bad)
            self.assertIn("source hash mismatch", str(ctx.exception))

            spec = copy.deepcopy(self.spec)
            spec["sources"]["X01"]["sha256"] = hashlib.sha256(
                bad.read_bytes()).hexdigest()
            with self.assertRaises(Exception):
                self.a.c1_precondition_check(spec, "X01", bad)

    def test_row_validation(self):
        spec = copy.deepcopy(self.spec)
        spec["files"]["X02"][0]["expected_formula"] = None
        with self.assertRaises(ValueError):
            self.a.c1_rows(spec, "X02")
        spec = copy.deepcopy(self.spec)
        spec["files"]["X02"][0]["cell"] = "D25"
        spec["files"]["X02"][1]["cell"] = "D25"
        with self.assertRaises(ValueError):
            self.a.c1_rows(spec, "X02")


class VectorAndMutantTests(unittest.TestCase):
    def setUp(self):
        self.a, self.book = synthetic_book()
        self.spec = self.a.c1_load_spec(SPEC_PATH)
        self.rows = self.a.c1_rows(self.spec, "X01")

    def test_canonical_spec_passes_on_synthetic(self):
        report = self.a.c1_verify(self.book, self.rows, "X01")
        self.assertTrue(report["ok"], report["failures"][:5])
        self.assertEqual(len(report["vectors"]), 124)
        self.assertGreater(report["exclusion_probes"], 0)
        self.assertFalse(report["failures"])

    def test_apply_c1_leaves_original_untouched(self):
        before = dict(self.book.formulas)
        patched = self.a.apply_c1(self.book, self.rows)
        self.assertEqual(self.book.formulas, before)
        self.assertEqual(patched.formulas[(S11, "D25")].text, "SUM(D26:D27)")
        self.assertEqual(patched.formulas[(S10, "H20")].text, "H8")
        self.assertNotIn((S10, "H20"), self.book.formulas)

    def test_every_mutant_fails_on_synthetic(self):
        mutants = self.a.c1_mutant_specs(self.spec, "X01")
        self.assertEqual(set(mutants), {"year_swap", "off_by_one",
                                        "off_by_one_y1", "off_by_one_y2",
                                        "off_by_one_y3", "off_by_one_y4",
                                        "extra_input", "dropped_term",
                                        "sign_flip", "broken_carry",
                                        "duplicated_term",
                                        "xr04_byproduct_dropped",
                                        "xr05_loan_dropped",
                                        "xr05_constant_offset",
                                        "xr05_scale",
                                        "xr06_residual_constant",
                                        "xr06_wrong_block_rate",
                                        "xr06_wrong_column_rate",
                                        "xr11_depreciation_kept",
                                        "xr11_paid_labour_removed",
                                        "xr11_double_subtraction",
                                        "xr11_wrong_year",
                                        "xr11_sign_flip",
                                        "xr11_constant_offset",
                                        "xr11_scale"})
        reasons = {"year_swap": "vector XR-19", "dropped_term": "vector XR-09",
                   "sign_flip": "vector XR-09",
                   "duplicated_term": "vector XR-20",
                   "xr04_byproduct_dropped": "vector XR-04",
                   "xr05_loan_dropped": "vector XR-05",
                   "xr05_constant_offset": "value XR-05",
                   "xr05_scale": ("vector XR-05", "value XR-05"),
                   "xr06_residual_constant": "vector XR-06",
                   "xr06_wrong_block_rate": "vector XR-06",
                   "xr06_wrong_column_rate": "vector XR-06",
                   "xr11_double_subtraction": "vector XR-11",
                   "xr11_wrong_year": "vector XR-11",
                   "xr11_sign_flip": "vector XR-11",
                   "xr11_paid_labour_removed": "reference XR-11",
                   "xr11_constant_offset": "value XR-11",
                   "xr11_scale": ("vector XR-11", "value XR-11")}
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                report = self.a.c1_verify(
                    self.book, self.a.c1_rows(mutant, "X01"), "X01")
                self.assertFalse(report["ok"], name)
                if name.startswith("off_by_one") or name == "extra_input":
                    self.assertTrue(any(f.startswith("exclusion XR-18") for f in
                                        report["failures"]), (name, report["failures"]))
                elif name == "broken_carry":
                    # The carry break surfaces through the XR-06 chain's
                    # observation of H44 as well as XR-22's own vector.
                    self.assertTrue(
                        any("H44" in f or f.startswith("vector XR-22")
                            for f in report["failures"]),
                        (name, report["failures"]))
                elif name == "xr11_depreciation_kept":
                    self.assertTrue(
                        any("14. 현금흐름계획" in f
                            for f in report["failures"]),
                        (name, report["failures"]))
                else:
                    reason = reasons[name]
                    prefixes = (reason if isinstance(reason, tuple)
                                else (reason,))
                    self.assertTrue(any(f.startswith(prefixes) for f in
                                        report["failures"]), (name, report["failures"]))

    def test_off_by_one_subtotal_mutant_discriminates(self):
        mutant = self.a.c1_mutant_specs(self.spec, "X01")["off_by_one"]
        report = self.a.c1_verify(
            self.book, self.a.c1_rows(mutant, "X01"), "X01")
        self.assertFalse(report["ok"])
        # C48 (an XR-19 source) must move an XR-18 target under the mutant:
        # the required N3 discrimination (correct Δ0, mutant Δ+δ).
        self.assertTrue(any("exclusion" in f and "XR-18" in f for f in
                            report["failures"]), report["failures"][:6])


if __name__ == "__main__":
    unittest.main()
