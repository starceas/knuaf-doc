"""Explicit local C1 integration checks against three pinned originals.

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
from tests.test_workbook_c1_correction import ROOT, SPEC_PATH, S9, S11, AUDIT_CLI
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
                    self.assertTrue(report["failures"][0].startswith(reason),
                                    report["failures"][0][:150])

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
                tmp / "seo-patched.xlsx", tmp / "seo-receipt.json", project, 30)

    def check_source(self, ref):
        source = REAL_SOURCES[ref]
        self.check_mutants(ref, source)
        self.check_candidate_cli(ref, source)
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
