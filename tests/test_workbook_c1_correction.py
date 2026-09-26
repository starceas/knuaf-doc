"""Synthetic C1 spec/precondition/vector/mutant tests (D7 r5, --check style).

Real-file verification (canonical PASS + mutant FAIL on the three pinned
sources) runs through ``gg_workbook_audit.py c1-check``; the real workbooks
are never part of the shipped tree, so every test here uses synthetic
fixtures only.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from tests.test_workbook_audit_evaluator import audit_module

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = (ROOT / "skills/knuaf-doc/references/common-workbooks"
             "/corrections/c1-spec.json")

S9 = "9 .경비계획"
S10 = "10. 감가상각비계획 "
S11 = "11. 생산원가계획"
S15 = "15.추정소득분석"
REAL_SOURCES = {
    "X01": Path("/Users/nara/Desktop/Storage/창업논문/창업논문 엑셀_강동현.xlsx"),
    "X02": Path("/Users/nara/Desktop/Storage/창업논문/창업논문 엑셀_강동현_대식물.xlsx"),
    "SEO": Path("/Users/nara/Desktop/Storage/진셍고트/restored/sources/originals/seo-minseo-finance.xlsx"),
}
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
        self.assertEqual(counts, {"X01": 34, "X02": 30, "SEO": 30})

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
            self.assertEqual(self.spec["files"][ref], base)

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
        self.assertEqual(len(report["vectors"]), 80)
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
                                        "duplicated_term"})
        reasons = {"year_swap": "vector XR-19", "dropped_term": "vector XR-09",
                   "sign_flip": "vector XR-09", "broken_carry": "vector XR-22",
                   "duplicated_term": "vector XR-20"}
        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                report = self.a.c1_verify(
                    self.book, self.a.c1_rows(mutant, "X01"), "X01")
                self.assertFalse(report["ok"], name)
                if name.startswith("off_by_one") or name == "extra_input":
                    self.assertTrue(any(f.startswith("exclusion XR-18") for f in
                                        report["failures"]), (name, report["failures"]))
                else:
                    self.assertTrue(any(f.startswith(reasons[name]) for f in
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


class RealSourceOracleTests(unittest.TestCase):
    """Pinned local files: each year and mutant reason; candidate CLI paths."""

    def setUp(self):
        self.a = audit_module()
        self.spec = self.a.c1_load_spec(SPEC_PATH)

    def test_each_year_and_other_mutants_have_specific_rejection(self):
        for ref, source in REAL_SOURCES.items():
            if not source.is_file():
                continue  # portable synthetic tests still run without local originals
            with self.subTest(ref=ref):
                book = self.a.c1_precondition_check(self.spec, ref, source)
                rows = self.a.c1_rows(self.spec, ref)
                self.assertTrue(self.a.c1_verify(book, rows, ref)["ok"])
                mutants = self.a.c1_mutant_specs(self.spec, ref)
                for year, (src, target) in enumerate(zip(
                        ("C48", "C68", "C88", "C108"),
                        ("D30", "E30", "F30", "G30")), 1):
                    with self.subTest(ref=ref, year=year):
                        bad = self.a.c1_rows(mutants[f"off_by_one_y{year}"], ref)
                        report = self.a.c1_verify(book, bad, ref)
                        expected = f"exclusion XR-18 {S9}!{src} moved {S11}!{target}"
                        self.assertFalse(report["ok"])
                        self.assertIn(expected, report["failures"])
                        good_delta = self.a._c1_responses(
                            self.a.apply_c1(book, rows), (S9, src), [(S11, target)])
                        bad_delta = self.a._c1_responses(
                            self.a.apply_c1(book, bad), (S9, src), [(S11, target)])
                        self.assertEqual(good_delta[(S11, target)], 0)
                        self.assertEqual(bad_delta[(S11, target)], self.a.C1_DELTA)
                extra = self.a.c1_verify(
                    book, self.a.c1_rows(mutants["extra_input"], ref), ref)
                self.assertIn(f"exclusion XR-18 {S9}!C48 moved {S11}!D30",
                              extra["failures"])
                for name, reason in (("year_swap", "vector XR-19"),
                                     ("dropped_term", "vector XR-09"),
                                     ("sign_flip", "vector XR-09"),
                                     ("duplicated_term", "vector XR-20"),
                                     ("broken_carry", "vector XR-22")):
                    if name not in mutants:
                        continue
                    with self.subTest(ref=ref, mutant=name):
                        report = self.a.c1_verify(
                            book, self.a.c1_rows(mutants[name], ref), ref,
                            fail_fast=True)
                        self.assertFalse(report["ok"])
                        self.assertTrue(report["failures"][0].startswith(reason),
                                        report["failures"][0][:150])

    def test_verify_cli_rejects_candidate_spec_and_map(self):
        for ref, source in REAL_SOURCES.items():
            if not source.is_file():
                continue
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                mutant = self.a.c1_mutant_specs(self.spec, ref)["off_by_one"]
                spec_path = tmp / "candidate-spec.json"
                spec_path.write_text(json.dumps(mutant, ensure_ascii=False),
                                     encoding="utf-8")
                map_path = tmp / "candidate-map.json"
                map_doc = self.a.c1_materialize(
                    self.spec, ref, source, map_path, spec_path=SPEC_PATH)
                for patch in map_doc["patches"]:
                    if patch["sheet"] == S11 and patch["cell"] in (
                            "D30", "E30", "F30", "G30"):
                        patch["new_formula"] = "'9 .경비계획'!C" + {
                            "D30": "50", "E30": "70", "F30": "90", "G30": "110"
                        }[patch["cell"]]
                map_path.write_text(json.dumps(map_doc, ensure_ascii=False),
                                    encoding="utf-8")
                for option, path in (("--candidate", spec_path),
                                     ("--candidate", map_path)):
                    result = subprocess.run(
                        [sys.executable, str(AUDIT_CLI), "verify-c1", "--ref",
                         ref, "--source", str(source), option, str(path)],
                        cwd=ROOT, env=os.environ.copy(), capture_output=True,
                        text=True, check=False)
                    self.assertEqual(result.returncode, 1, result.stderr[-1000:])
                    report = json.loads(result.stdout)
                    self.assertFalse(report["ok"])
                    for src, target in zip(("C48", "C68", "C88", "C108"),
                                           ("D30", "E30", "F30", "G30")):
                        self.assertIn(
                            f"exclusion XR-18 {S9}!{src} moved {S11}!{target}",
                            report["failures"])


if __name__ == "__main__":
    unittest.main()
