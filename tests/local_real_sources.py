"""Explicit local C1/C1b integration checks against three pinned originals.

Run: python3 -m unittest tests.local_real_sources -v
Exit 0: all PASS; 1: at least one FAIL; 2: at least one NOT_RUN.
This module is intentionally outside unittest discovery's test*.py pattern.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests._harness import ContractCase, bind_major
from tests.test_workbook_audit_evaluator import audit_module
from tests.test_workbook_c1_correction import (
    ROOT, SPEC_PATH, S3, S9, S10, S11, S14, S15, AUDIT_CLI)
from tests.test_workbook_c1_specialty_e2e import SpecialtyChainTests

REAL_SOURCES = {
    "X01": Path("/Users/nara/Desktop/Storage/창업논문/창업논문 엑셀_강동현.xlsx"),
    "X02": Path("/Users/nara/Desktop/Storage/창업논문/창업논문 엑셀_강동현_대식물.xlsx"),
    "SEO": Path("/Users/nara/Desktop/Storage/진셍고트/restored/sources/originals/seo-minseo-finance.xlsx"),
}


class LocalRealSourceChecks(ContractCase):
    run_cli = SpecialtyChainTests.run_cli
    assert_chain = SpecialtyChainTests.assert_chain

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
        cash_row = {"X01": "17", "X02": "18", "SEO": "18"}[ref]
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
                    text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stderr[-1000:])
                report = json.loads(result.stdout)
                self.assertFalse(report["ok"])
                for src, target in zip(("C48", "C68", "C88", "C108"),
                                       ("D30", "E30", "F30", "G30")):
                    self.assertIn(
                        f"exclusion XR-18 {S9}!{src} moved {S11}!{target}",
                        report["failures"])

    def check_seo_chain(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project = self.make_project()
            bind_major(project, "specialty_crops")
            self.assert_chain(
                source, SPEC_PATH, tmp / "seo-map.json",
                tmp / "seo-patched.xlsx", tmp / "seo-receipt.json", project, 63)

    def check_source(self, ref):
        source = REAL_SOURCES[ref]
        self.check_mutants(ref, source)
        self.check_xr04_rates(ref)
        self.check_xr11_vectors(ref)
        self.check_c1b_mutants(ref)
        self.check_xr06_baseline_change(ref)
        self.check_candidate_cli(ref, source)
        self.check_materialize_accept(ref, source)
        if ref == "SEO":
            self.check_seo_chain(source)


def load_tests(loader, tests, pattern):
    """Make NOT_RUN a separate process outcome, never a passing test."""
    statuses = {}
    details = {}
    for ref, source in REAL_SOURCES.items():
        if not source.is_file():
            statuses[ref] = "NOT_RUN"
            details[ref] = "source absent"
            continue
        case = LocalRealSourceChecks('runTest')
        result = unittest.TestResult()
        # Reuse unittest lifecycle and assertions; each source is independent.
        case.runTest = lambda ref=ref, case=case: case.check_source(ref)
        case.run(result)
        if result.wasSuccessful() and result.testsRun == 1:
            statuses[ref] = "PASS"
            details[ref] = "pinned source, mutants, candidate CLI" + (
                ", receipt chain" if ref == "SEO" else "")
        else:
            statuses[ref] = "FAIL"
            details[ref] = ((result.failures or result.errors)[0][1].splitlines()[-1]
                            if result.failures or result.errors else "check did not run")
    print("C1 local real-source checks")
    print("SOURCE  STATUS    DETAIL")
    for ref in REAL_SOURCES:
        print(f"{ref:<6}  {statuses[ref]:<7}   {details[ref]}")
    if "NOT_RUN" in statuses.values():
        raise SystemExit(2)
    if "FAIL" in statuses.values():
        raise SystemExit(1)
    def require_pass(ref):
        if statuses[ref] != "PASS":
            raise AssertionError(f"{ref}: {statuses[ref]}")
    return unittest.TestSuite(
        unittest.FunctionTestCase(lambda ref=ref: require_pass(ref),
                                  description=f"{ref} local original")
        for ref in REAL_SOURCES)


if __name__ == "__main__":
    unittest.main()
