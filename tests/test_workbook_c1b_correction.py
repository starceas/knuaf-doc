"""C1b policy-item corrections (D8 r3): synthetic vectors, mutants, dispositions.

Covers XR-04 (영농잉여 on the 총수입 row), XR-05 (later-year 투자 계 includes
the loan column), XR-06 (annual charge reads the labelled 잔존가격율 cell)
and XR-11 (unpaid imputed 자가 노동비 excluded from cash outflow, owner
option A) on synthetic fixtures only — the per-source checks on the pinned
originals live in tests/local_real_sources.py.  No example values are
shipped.
"""
import json
from pathlib import Path
import unittest

from tests.test_workbook_audit_evaluator import audit_module
from tests.test_workbook_c1_correction import (
    SPEC_PATH, S3, S10, S11, S14, S15, synthetic_book)

DELTA = 7
DISPOSITIONS_PATH = (Path(__file__).resolve().parents[1]
                     / "skills/knuaf-doc/references/common-workbooks"
                     "/corrections/c1-dispositions.json")


class SpecShapeTests(unittest.TestCase):
    """The C1b rows bind every table coordinate, per source shape."""

    def setUp(self):
        self.a = audit_module()
        self.spec = self.a.c1_load_spec(SPEC_PATH)

    def test_c1b_row_shapes(self):
        for ref, rows in self.spec["files"].items():
            c1b = [r for r in rows
                   if r["finding_id"] in ("XR-04", "XR-05", "XR-06", "XR-11")]
            self.assertTrue(c1b)
            for row in c1b:
                if row["class"] == "formula_repoint":
                    self.assertTrue(row["expected_formula"].startswith("="))
                    self.assertTrue(row["new_formula"].startswith("="))
                else:
                    self.assertEqual(row["class"], "value_to_formula")
                    # Only X01 15!E7 is bound by cell digest.
                    self.assertEqual(ref, "X01")
                    self.assertEqual(row["sheet"], S15)
                    self.assertEqual(row["cell"], "E7")
                    self.assertEqual(len(row["expected_cell_digest"]), 64)
        # no value_to_formula rows outside the X01 digest pair (XR-22 + XR-04)
        v2f = [(ref, r["cell"]) for ref, rows in self.spec["files"].items()
               for r in rows if r["class"] == "value_to_formula"]
        self.assertEqual(v2f, [("X01", c) for c in
                               ("H20", "H32", "H44", "H56", "E7")])


class SyntheticVectorTests(unittest.TestCase):
    """C1b vectors on the X01-shaped synthetic book."""

    def setUp(self):
        self.a, self.book = synthetic_book()
        self.spec = self.a.c1_load_spec(SPEC_PATH)
        self.rows = self.a.c1_rows(self.spec, "X01")
        self.patched = self.a.apply_c1(self.book, self.rows)

    def resp(self, in_key, keys):
        return self.a._c1_responses(self.patched, in_key, keys)

    def test_xr04_first_year_chain(self):
        # 15!E6 +δ -> E7 +δ -> E27 +δ (X01 digest-bound value_to_formula).
        out = self.resp((S15, "E6"), [(S15, "E7"), (S15, "E27")])
        self.assertEqual(out[(S15, "E7")], DELTA)
        self.assertEqual(out[(S15, "E27")], DELTA)

    def test_xr06_rate_chain_drives_all_five_blocks(self):
        # H9 is the stored rate input; the chain H21->H33->H45->H57 links
        # every later block, so one perturbation moves all five charges.
        targets = [(S10, "H" + str(r)) for r in (12, 24, 36, 48, 60)]
        out = self.resp((S10, "H9"), targets)
        for key in targets:
            # cost 100, life 10: -(100*7)/10 = -70
            self.assertEqual(out[key], -70, key)
        # wrong block's rate cell does not move the first block.
        out = self.resp((S10, "H21"), [(S10, "H12")])
        self.assertEqual(out[(S10, "H12")], 0)

    def test_xr11_imputed_labour_is_cash_neutral(self):
        cash = (S14, "D17")
        self.assertEqual(self.resp((S11, "C12"), [cash])[cash], 0)
        self.assertEqual(self.resp((S11, "C21"), [cash])[cash], 0)
        self.assertEqual(self.resp((S11, "C15"), [cash])[cash], DELTA)
        self.assertEqual(self.resp((S11, "C5"), [cash])[cash], DELTA)
        self.assertEqual(self.resp((S11, "C31"), [cash])[cash], DELTA)

    def test_xr11_cross_year_exclusion(self):
        # C12 belongs to year 1 only; the year-2 cash cell must not move.
        out = self.resp((S11, "C12"),
                        [(S14, c + "17") for c in "EFGH"])
        for key in out:
            self.assertEqual(out[key], 0, key)
        out = self.resp((S11, "D12"), [(S14, "E17")])
        self.assertEqual(out[(S14, "E17")], 0)

    def test_xr05_loan_cell_moves_total(self):
        out = self.resp((S3, "H25"), [(S3, "E25")])
        self.assertEqual(out[(S3, "E25")], DELTA)
        # F/G still respond through the widened range.
        self.assertEqual(self.resp((S3, "F26"), [(S3, "E26")])[(S3, "E26")],
                         DELTA)
        self.assertEqual(self.resp((S3, "G27"), [(S3, "E27")])[(S3, "E27")],
                         DELTA)
        # a different row's loan cell does not move E25.
        self.assertEqual(self.resp((S3, "H26"), [(S3, "E25")])[(S3, "E25")],
                         0)


class SyntheticMutantTests(unittest.TestCase):
    """Every C1b mutant must be rejected on the synthetic book."""

    def setUp(self):
        self.a, self.book = synthetic_book()
        self.spec = self.a.c1_load_spec(SPEC_PATH)
        self.mutants = self.a.c1_mutant_specs(self.spec, "X01")

    def check(self, name):
        report = self.a.c1_verify(
            self.book, self.a.c1_rows(self.mutants[name], "X01"), "X01")
        self.assertFalse(report["ok"], name)
        return report

    def test_xr04_byproduct_dropped_rejected(self):
        report = self.check("xr04_byproduct_dropped")
        self.assertTrue(any(f.startswith("vector XR-04")
                            for f in report["failures"]))

    def test_xr05_loan_dropped_rejected(self):
        report = self.check("xr05_loan_dropped")
        self.assertTrue(any(f.startswith("vector XR-05")
                            for f in report["failures"]))

    def test_xr06_mutants_rejected(self):
        for name in ("xr06_residual_constant", "xr06_wrong_block_rate",
                     "xr06_wrong_column_rate"):
            with self.subTest(mutant=name):
                report = self.check(name)
                self.assertTrue(any(f.startswith("vector XR-06")
                                    for f in report["failures"]),
                                report["failures"][:3])

    def test_xr11_mutants_rejected(self):
        for name in ("xr11_depreciation_kept", "xr11_double_subtraction",
                     "xr11_wrong_year", "xr11_sign_flip"):
            with self.subTest(mutant=name):
                report = self.check(name)
                self.assertTrue(
                    any("XR-11" in f or "현금흐름계획" in f
                        for f in report["failures"]),
                    report["failures"][:3])

    def test_xr11_paid_labour_removed_rejected_by_reference(self):
        # C31-C21-C11 nets to zero under every vector and probe (it only
        # reads cells already in the downstream graph), so the approved-
        # reference closure is what rejects it — C11 is not an approved
        # input of the cash row.
        report = self.check("xr11_paid_labour_removed")
        self.assertIn(
            "reference XR-11 " + S14 + "!D17 reads " + S11
            + "!C11 outside the approved inputs",
            report["failures"])


class ConstantOffsetMutantTests(unittest.TestCase):
    """D8-FIX1 regression: delta-equal constant/scale mutants are rejected
    by the value-equivalence check even when every vector delta matches."""

    def setUp(self):
        self.a, self.book = synthetic_book()
        self.spec = self.a.c1_load_spec(SPEC_PATH)
        self.mutants = self.a.c1_mutant_specs(self.spec, "X01")

    def test_constant_offset_mutants_rejected_by_value(self):
        for name in ("xr05_constant_offset", "xr11_constant_offset"):
            with self.subTest(mutant=name):
                report = self.a.c1_verify(
                    self.book, self.a.c1_rows(self.mutants[name], "X01"),
                    "X01")
                self.assertFalse(report["ok"], name)
                self.assertTrue(any(f.startswith("value ")
                                    for f in report["failures"]),
                                report["failures"][:3])

    def test_scale_mutants_rejected(self):
        for name in ("xr05_scale", "xr11_scale"):
            with self.subTest(mutant=name):
                report = self.a.c1_verify(
                    self.book, self.a.c1_rows(self.mutants[name], "X01"),
                    "X01")
                self.assertFalse(report["ok"], name)


class DispositionCountTests(unittest.TestCase):
    """FIX1-2: every spec row count is mirrored by its disposition entry."""

    def test_per_source_disposition_cell_counts_match_spec(self):
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        disp = json.loads(DISPOSITIONS_PATH.read_text(encoding="utf-8"))
        for ref, rows in spec["files"].items():
            counts = {}
            for row in rows:
                counts[row["finding_id"]] = counts.get(row["finding_id"], 0) + 1
            findings = {f["id"]: f for f in disp["files"][ref]["findings"]}
            for finding_id, count in counts.items():
                with self.subTest(ref=ref, finding=finding_id):
                    self.assertIn(finding_id, findings,
                                  ref + " dispositions miss " + finding_id)
                    entry = findings[finding_id]
                    self.assertEqual("corrected", entry["disposition"],
                                     (ref, finding_id))
                    self.assertEqual(count, entry["spec_cells"],
                                     (ref, finding_id))


if __name__ == "__main__":
    unittest.main()
