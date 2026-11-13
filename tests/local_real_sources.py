"""Explicit local checks against pinned X01/X02 and food-crops originals.

Run: python3 -m unittest tests.local_real_sources -v
Exit 0: no FAIL (unavailable inputs are SKIP — SKIP is not PASS);
1: at least one FAIL.
This module is intentionally outside unittest discovery's test*.py pattern.

The food-crops section is the 2판 `clear` receipt measured on the school's
own 16-sheet form. The dairy section does the same for the school's 28-sheet
hanwoo form. Neither file is copied into the repository: point
``KNUAF_REAL_FOOD_CROPS_XLSX`` / ``KNUAF_REAL_DAIRY_XLSX`` at them for the run.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries

from tests._harness import ContractCase, bind_major, runtime
from tests.test_workbook_audit_evaluator import audit_module
from tests.test_workbook_c1_correction import (
    ROOT, SPEC_PATH, S3, S9, S10, S11, S14, S15, AUDIT_CLI)

SOURCE_ENV = {
    "X01": "KNUAF_REAL_X01_XLSX",
    "X02": "KNUAF_REAL_X02_XLSX",
}
REAL_SOURCES = {
    ref: Path(value) if (value := os.environ.get(variable)) else None
    for ref, variable in SOURCE_ENV.items()
}


class LocalRealSourceChecks(ContractCase):

    def setUp(self):
        self.a = audit_module()
        self.spec = self.a.c1_load_spec(SPEC_PATH)

    def check_mutants(self, ref, source):
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
                    first = report["failures"][0]
                    if name == "broken_carry" and ref == "X01":
                        # The C1b XR-06 rate chain also reads the carry
                        # cells, so the break is caught by whichever
                        # vector walks it first.
                        self.assertTrue(
                            first.startswith(("vector XR-22",
                                              "vector XR-06")),
                            first[:150])
                    else:
                        self.assertTrue(first.startswith(reason),
                                        first[:150])

    def _book_rows(self, ref):
        source = REAL_SOURCES[ref]
        self.assertTrue(source.is_file(), ref + " source absent")
        book = self.a.c1_precondition_check(self.spec, ref, source)
        rows = self.a.c1_rows(self.spec, ref)
        return book, rows, self.a.apply_c1(book, rows)

    def check_xr04_rates(self, ref):
        """Synthetic 주산물 100 / 부산물 20 / 비용 60 inputs: the corrected
        rate is 0.5; the main-only and by-product-only mutants give 0.6 and
        3.0 and are rejected by the verifier."""
        if ref == "X01":
            return  # X01 keeps its own percent scale; the vector covers it
        with self.subTest(ref=ref):
            book, _, patched = self._book_rows(ref)
            for cell in "FGHI":
                ov = {(S15, cell + "5"): 100, (S15, cell + "6"): 20,
                      (S15, cell + "26"): 60}
                self.assertAlmostEqual(
                    patched.evaluate(S15, cell + "28", overrides=ov), 0.5)
            mutants = self.a.c1_mutant_specs(self.spec, ref)
            ov = {(S15, "F5"): 100, (S15, "F6"): 20, (S15, "F26"): 60}
            main = self.a.apply_c1(
                book, self.a.c1_rows(
                    mutants["xr04_rate_on_main_crop"], ref))
            self.assertAlmostEqual(
                main.evaluate(S15, "F28", overrides=ov), 0.6)
            byp = self.a.apply_c1(
                book, self.a.c1_rows(
                    mutants["xr04_rate_on_byproduct"], ref))
            self.assertAlmostEqual(
                byp.evaluate(S15, "F28", overrides=ov), 3.0)
            for name in ("xr04_rate_on_main_crop",
                         "xr04_rate_on_byproduct",
                         "xr04_surplus_main_only"):
                report = self.a.c1_verify(
                    book, self.a.c1_rows(mutants[name], ref), ref)
                self.assertFalse(report["ok"], name)

    def check_xr11_vectors(self, ref):
        cash_row = {"X01": "17", "X02": "18"}[ref]
        with self.subTest(ref=ref):
            _, _, patched = self._book_rows(ref)
            for cash_col, y in zip("DEFGH", "CDEFG"):
                cash = (S14, cash_col + cash_row)
                # imputed self labour: cash unchanged in every year
                out = self.a._c1_responses(
                    patched, (S11, y + "12"),
                    [(S14, c + cash_row) for c in "DEFGH"])
                for key, delta in out.items():
                    self.assertEqual(delta, 0,
                                     (ref, y + "12", key, delta))
                # depreciation stays cash-neutral
                out = self.a._c1_responses(
                    patched, (S11, y + "21"), [cash])
                self.assertEqual(out[cash], 0)
                # hired labour and other cash cost still outflow
                self.assertEqual(
                    self.a._c1_responses(
                        patched, (S11, y + "15"), [cash])[cash],
                    self.a.C1_DELTA)
                self.assertEqual(
                    self.a._c1_responses(
                        patched, (S11, y + "5"), [cash])[cash],
                    self.a.C1_DELTA)
                # production cost still flows through
                self.assertEqual(
                    self.a._c1_responses(
                        patched, (S11, y + "31"), [cash])[cash],
                    self.a.C1_DELTA)

    def check_c1b_mutants(self, ref):
        with self.subTest(ref=ref):
            book, rows, _ = self._book_rows(ref)
            self.assertTrue(
                self.a.c1_verify(book, rows, ref)["ok"])
            mutants = self.a.c1_mutant_specs(self.spec, ref)
            for name, mutant in mutants.items():
                with self.subTest(ref=ref, mutant=name):
                    report = self.a.c1_verify(
                        book, self.a.c1_rows(mutant, ref), ref)
                    self.assertFalse(report["ok"], name)
            report = self.a.c1_verify(
                book,
                self.a.c1_rows(mutants["xr11_paid_labour_removed"], ref),
                ref)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any(f.startswith("reference XR-11")
                    and "outside the approved inputs" in f
                    for f in report["failures"]),
                report["failures"])

    def check_xr06_baseline_change(self, ref):
        # The only baseline value change the rate link produces is X02's
        # J column: J9 is a stored 0, so the charge rises to cost/life.
        book, rows, _ = self._book_rows(ref)
        report = self.a.c1_verify(book, rows, ref,
                                  include_baseline_changes=True)
        dep = {c["cell"] for c in report["baseline_changes"]
               if c["sheet"] == S10}
        if ref == "X02":
            self.assertTrue(dep)
            self.assertTrue(all(c.startswith(("J", "K")) for c in dep), dep)
        else:
            self.assertEqual(dep, set(), (ref, dep))

    def check_materialize_accept(self, ref, source):
        """materialize-c1 -> verify the map as a candidate accepts."""
        with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
            map_path = Path(tmp) / (ref.lower() + "-c1b-map.json")
            self.a.c1_materialize(self.spec, ref, source, map_path,
                                  spec_path=SPEC_PATH)
            map_doc = json.loads(map_path.read_text(encoding="utf-8"))
            self.assertEqual(map_doc["source"]["sha256"],
                             self.spec["sources"][ref]["sha256"])
            book = self.a.c1_precondition_check(self.spec, ref, source)
            rows = self.a.c1_map_precondition_check(map_doc, source, book)
            report = self.a.c1_verify(book, rows, ref)
            self.assertTrue(report["ok"], report["failures"][:5])

    def check_candidate_cli(self, ref, source):
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
                    text=True, encoding="utf-8", check=False)
                self.assertEqual(result.returncode, 1, result.stderr[-1000:])
                report = json.loads(result.stdout)
                self.assertFalse(report["ok"])
                for src, target in zip(("C48", "C68", "C88", "C108"),
                                       ("D30", "E30", "F30", "G30")):
                    self.assertIn(
                        f"exclusion XR-18 {S9}!{src} moved {S11}!{target}",
                        report["failures"])

    def check_source(self, ref):
        source = REAL_SOURCES[ref]
        self.check_mutants(ref, source)
        self.check_xr04_rates(ref)
        self.check_xr11_vectors(ref)
        self.check_c1b_mutants(ref)
        self.check_xr06_baseline_change(ref)
        self.check_candidate_cli(ref, source)
        self.check_materialize_accept(ref, source)


FOOD_CROPS_ENV = "KNUAF_REAL_FOOD_CROPS_XLSX"
FOOD_CROPS_SOURCE = (Path(value) if (value := os.environ.get(FOOD_CROPS_ENV))
                     else None)
FOOD_MAP = (ROOT / "skills/knuaf-doc/references/food-crops"
            / "corrections/fc-clear-map.json")
FOOD_CLEARED = 396
FOOD_PRESERVED = 79

DAIRY_ENV = "KNUAF_REAL_DAIRY_XLSX"
DAIRY_SOURCE = (Path(value) if (value := os.environ.get(DAIRY_ENV))
                else None)
DAIRY_MAP = (ROOT / "skills/knuaf-doc/references/dairy"
             / "corrections/dairy-clear-map.json")
DAIRY_CLEARED = 1070
DAIRY_PRESERVED = 5463
DAIRY_FORMULAS = 22828
DAIRY_MERGED_RANGES = 2114
DAIRY_CLEARED_MERGE_TOP_LEFT = 86
# Every function the 28-sheet form calls, and how many formulas call it.
# None of them can distinguish an empty cell from a 0, which is why the map
# may blank the 681 zero cells it clears: SUM adds a blank as 0, and
# ROUNDUP/ROUNDDOWN/PMT/LOG treat a blank argument as 0.
DAIRY_FORMULA_FUNCTIONS = {"SUM": 3181, "ROUNDUP": 72, "ROUNDDOWN": 72,
                           "PMT": 4, "LOG": 2, "AVERAGE": 1}


def _cells_of(rng):
    c1, r1, c2, r2 = range_boundaries(rng)
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            yield f"{get_column_letter(c)}{r}"


def _formula_usage(path):
    """Called functions and ``""`` comparisons across every formula."""
    functions = {}
    empty_compare = 0
    book = openpyxl.load_workbook(path)
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if not (isinstance(value, str) and value.startswith("=")):
                    continue
                if '""' in value:
                    empty_compare += 1
                for name in re.findall(r"([A-Z][A-Z0-9.]*)\(", value.upper()):
                    functions[name] = functions.get(name, 0) + 1
    book.close()
    return functions, empty_compare


def _formula_records(path):
    """Every ``<f>`` in the package: text plus attributes, sheet by sheet."""
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    records, cached = set(), 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet"):
                continue
            root = ET.fromstring(archive.read(name))
            for cell in root.iter(main + "c"):
                formula = cell.find(main + "f")
                if formula is None:
                    continue
                records.add((name, cell.get("r"), formula.text,
                             tuple(sorted(formula.attrib.items()))))
                value = cell.find(main + "v")
                if value is not None and value.text is not None:
                    cached += 1
    return records, cached


class FoodCropsRealSourceChecks(ContractCase):
    """The 2판 clear receipt on the school's real 16-sheet form."""

    def test_the_real_form_produces_the_reviewed_blank_copy(self):
        source = FOOD_CROPS_SOURCE
        self.assertTrue(source.is_file(), "food-crops source absent")
        tpl = runtime("gg_excel_template")
        school = runtime("gg_school_excel")
        mc = runtime("gg_major_contract")
        profile = school.profile_structure("food_crops_16_sheet_v1")
        self.assertEqual(tpl.sha256(source), profile["sha256"])
        self.assertEqual(len(profile["sheets"]), 16)
        original = source.read_bytes()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime("gg_core").init(root)
            bind_major(root, "food_crops")
            out = root / "blank.xlsx"
            receipt = tpl.blank_copy(source, FOOD_MAP, out,
                                     context=mc.output_context(root, "food_crops"))
            self.assertEqual(receipt["workbook_profile"],
                             "food_crops_16_sheet_v1")
            self.assertEqual(len(receipt["cleared"]), FOOD_CLEARED)
            self.assertEqual(len(receipt["preserved"]), FOOD_PRESERVED)
            self.assertEqual(receipt["unclassifiedNumericCells"], 0)
            self.assertEqual(receipt["unclassifiedTextCells"], 0)
            self.assertEqual(receipt["formulaCellsProtected"], 0)
            self.assertEqual(receipt["missingCells"], [])
            self.assertEqual(receipt["output"]["status"], "blank_template")
            # Only the reviewed structure survives: plan years and the
            # form's own source notes, nothing a student typed earlier.
            self.assertTrue(all(entry["reason"].startswith("검토된")
                                for entry in receipt["preserved"]))
            self.assertEqual(
                {entry["sheet"] for entry in receipt["preserved"]} - set(profile["sheets"]),
                set())
            self.assertEqual(source.read_bytes(), original)

            before, cached_before = _formula_records(source)
            after, cached_after = _formula_records(out)
            self.assertEqual(len(before), 1256)
            self.assertEqual(after, before)
            self.assertEqual(cached_before, 1256)
            self.assertEqual(cached_after, 0)


class DairyRealSourceChecks(ContractCase):
    """The dairy clear receipt on the school's real 28-sheet hanwoo form.

    The synthetic test proves the writer; this one proves the reviewed map
    addresses the real workbook — that every one of the 1,070 cleared cells
    exists in the form, that the 5,463 kept cells keep their value, and that
    all 22,828 formulas come through untouched with their cached answers
    dropped so Excel cannot show stale numbers.
    """

    def test_the_real_form_produces_the_reviewed_blank_copy(self):
        source = DAIRY_SOURCE
        self.assertTrue(source.is_file(), "dairy source absent")
        tpl = runtime("gg_excel_template")
        school = runtime("gg_school_excel")
        mc = runtime("gg_major_contract")
        profile = school.profile_structure("dairy_hanwoo_28_sheet_v1")
        self.assertEqual(tpl.sha256(source), profile["sha256"])
        self.assertEqual(len(profile["sheets"]), 28)
        original = source.read_bytes()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime("gg_core").init(root)
            bind_major(root, "dairy")
            out = root / "blank.xlsx"
            receipt = tpl.blank_copy(source, DAIRY_MAP, out,
                                     context=mc.output_context(root, "dairy"))
            self.assertEqual(receipt["workbook_profile"],
                             "dairy_hanwoo_28_sheet_v1")
            self.assertEqual(len(receipt["cleared"]), DAIRY_CLEARED)
            self.assertEqual(len(receipt["preserved"]), DAIRY_PRESERVED)
            self.assertEqual(receipt["formulaCellsProtected"], 0)
            self.assertEqual(receipt["mergedCellsProtected"], 0)
            self.assertEqual(receipt["unclassifiedNumericCells"], 0)
            self.assertEqual(receipt["unclassifiedTextCells"], 0)
            self.assertEqual(receipt["missingCells"], [])
            self.assertEqual(receipt["output"]["status"], "blank_template")
            self.assertIn("cached_formula_values_invalidated",
                          receipt["output"]["calculationStatus"])
            self.assertEqual(source.read_bytes(), original)

            # Only the reviewed structure and the owner's kept reference
            # values survive: the receipt echoes the map's own preserve
            # sentences, and each cleared cell reports the sample it dropped.
            mapping = json.loads(DAIRY_MAP.read_text(encoding="utf-8"))
            reviewed = {entry["reason"] for entry in mapping["entries"]
                        if entry["action"] == "preserve"}
            reasons = {entry["reason"] for entry in receipt["preserved"]}
            self.assertEqual(reasons, reviewed)
            self.assertEqual(len(reasons), 13)
            self.assertEqual(
                {entry["sheet"] for entry in receipt["preserved"]},
                set(profile["sheets"]))
            self.assertTrue(all("oldValue" in entry
                                for entry in receipt["cleared"]))
            price = [entry for entry in receipt["preserved"]
                     if "시세" in entry["reason"]]
            self.assertEqual(len(price), 72)

            before, cached_before = _formula_records(source)
            after, cached_after = _formula_records(out)
            self.assertEqual(len(before), DAIRY_FORMULAS)
            self.assertEqual(after, before)
            self.assertEqual(cached_before, DAIRY_FORMULAS)
            self.assertEqual(cached_after, 0)

            # R-01: every address the student table lists still holds a
            # formula with a numeric literal in it, on the form and the copy.
            from tests.test_dairy_blank_copy import FORMULA_CONSTANT_ADDRESSES
            self.assertEqual(receipt["notices"],
                             list(tpl.BLANK_COPY_NOTICES["dairy_hanwoo_28_sheet_v1"]))
            const_book = openpyxl.load_workbook(out)
            for sheet, cell in FORMULA_CONSTANT_ADDRESSES:
                with self.subTest(constant=f"{sheet}!{cell}"):
                    value = const_book[sheet][cell].value
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.startswith("="))
                    self.assertRegex(value, r"[^A-Z$]\d")
            const_book.close()

            # Structure the writer must reproduce exactly: merged ranges,
            # sheet order and visibility, and the merge top-lefts whose sample
            # text the map clears.
            src_book = openpyxl.load_workbook(source)
            out_book = openpyxl.load_workbook(out)
            self.assertEqual(out_book.sheetnames, src_book.sheetnames)
            self.assertEqual(
                [ws.sheet_state for ws in out_book.worksheets],
                [ws.sheet_state for ws in src_book.worksheets])
            merged = 0
            cleared_top_left = 0
            clear_cells = {(e["sheet"], cell)
                           for e in mapping["entries"] if e["action"] == "clear"
                           for cell in _cells_of(e.get("range") or e["cell"])}
            for name in src_book.sheetnames:
                src_ranges = sorted(str(r) for r in
                                    src_book[name].merged_cells.ranges)
                out_ranges = sorted(str(r) for r in
                                    out_book[name].merged_cells.ranges)
                self.assertEqual(out_ranges, src_ranges, name)
                merged += len(src_ranges)
                for rng in src_ranges:
                    c1, r1, _, _ = range_boundaries(rng)
                    top = f"{get_column_letter(c1)}{r1}"
                    if (name, top) in clear_cells:
                        cleared_top_left += 1
                        self.assertIsNone(out_book[name][top].value)
            self.assertEqual(merged, DAIRY_MERGED_RANGES)
            self.assertEqual(cleared_top_left, DAIRY_CLEARED_MERGE_TOP_LEFT)
            src_book.close()
            out_book.close()

            # Why a 0 and a blank calculate the same here: the form uses no
            # function that can tell them apart, and no "" comparison.
            functions, empty_compare = _formula_usage(source)
            self.assertEqual(functions, DAIRY_FORMULA_FUNCTIONS)
            self.assertEqual(empty_compare, 0)


def load_tests(loader, tests, pattern):
    """Report unavailable inputs as visible skips, never passing checks."""
    statuses = {}
    details = {}
    for ref, source in REAL_SOURCES.items():
        if source is None:
            statuses[ref] = "SKIP"
            details[ref] = "SKIP — PASS 아님: " + SOURCE_ENV[ref] + " is unset"
            continue
        if not source.is_file():
            statuses[ref] = "SKIP"
            details[ref] = ("SKIP — PASS 아님: " + SOURCE_ENV[ref]
                            + " file is absent")
            continue
        case = LocalRealSourceChecks('runTest')
        result = unittest.TestResult()
        # Reuse unittest lifecycle and assertions; each source is independent.
        case.runTest = lambda ref=ref, case=case: case.check_source(ref)
        case.run(result)
        if result.wasSuccessful() and result.testsRun == 1:
            statuses[ref] = "PASS"
            details[ref] = "pinned source, mutants, candidate CLI"
        else:
            statuses[ref] = "FAIL"
            details[ref] = ((result.failures or result.errors)[0][1].splitlines()[-1]
                            if result.failures or result.errors else "check did not run")
    # The food-crops original is a separate form with its own journey; it is
    # reported on its own row so a missing school file never hides a C1 result.
    if FOOD_CROPS_SOURCE is None:
        statuses["FC"] = "SKIP"
        details["FC"] = "SKIP — PASS 아님: " + FOOD_CROPS_ENV + " is unset"
    elif not FOOD_CROPS_SOURCE.is_file():
        statuses["FC"] = "SKIP"
        details["FC"] = "SKIP — PASS 아님: " + FOOD_CROPS_ENV + " file is absent"
    else:
        case = FoodCropsRealSourceChecks('runTest')
        result = unittest.TestResult()
        case.runTest = lambda case=case: case.test_the_real_form_produces_the_reviewed_blank_copy()
        case.run(result)
        if result.wasSuccessful() and result.testsRun == 1:
            statuses["FC"] = "PASS"
            details["FC"] = "16-sheet form, 396/79, formula records intact"
        else:
            statuses["FC"] = "FAIL"
            details["FC"] = ((result.failures or result.errors)[0][1].splitlines()[-1]
                             if result.failures or result.errors else "check did not run")
    if DAIRY_SOURCE is None:
        statuses["DAIRY"] = "SKIP"
        details["DAIRY"] = "SKIP — PASS 아님: " + DAIRY_ENV + " is unset"
    elif not DAIRY_SOURCE.is_file():
        statuses["DAIRY"] = "SKIP"
        details["DAIRY"] = "SKIP — PASS 아님: " + DAIRY_ENV + " file is absent"
    else:
        case = DairyRealSourceChecks('runTest')
        result = unittest.TestResult()
        case.runTest = lambda case=case: case.test_the_real_form_produces_the_reviewed_blank_copy()
        case.run(result)
        if result.wasSuccessful() and result.testsRun == 1:
            statuses["DAIRY"] = "PASS"
            details["DAIRY"] = ("28-sheet form, 1070/5463, "
                                "22828 formula records intact")
        else:
            statuses["DAIRY"] = "FAIL"
            details["DAIRY"] = ((result.failures or result.errors)[0][1].splitlines()[-1]
                                if result.failures or result.errors else "check did not run")
    print("C1 local real-source checks (SKIP means the input was absent: "
          "not a pass)")
    print("SOURCE  STATUS    DETAIL")
    for ref in statuses:
        print(f"{ref:<6}  {statuses[ref]:<7}   {details[ref]}")
    if "FAIL" in statuses.values():
        raise SystemExit(1)
    def require_pass(ref):
        if statuses[ref] == "SKIP":
            raise unittest.SkipTest(details[ref])
        if statuses[ref] != "PASS":
            raise AssertionError(f"{ref}: {statuses[ref]}")
    return unittest.TestSuite(
        unittest.FunctionTestCase(lambda ref=ref: require_pass(ref),
                                  description=f"{ref} local original")
        for ref in statuses)


if __name__ == "__main__":
    unittest.main()
