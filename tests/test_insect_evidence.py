"""Selected aggregate receipts never become approved financial inputs.

Covers DESIGN 6.2 (conflict axis is read from receipt comparisons, never
hardcoded — audit finding #5), 6.3 (route + required axes + revérification),
14.2 (unsupported output and capability fixed codes), and the return
contract of 14.8.  Fixtures are synthetic; no real R0/R1 bytes.
"""

import dataclasses
import hashlib
import json
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

contract = runtime("gg_major_contract")
evidence = runtime("gg_insect_evidence")
source = runtime("gg_insect_source_receipt")


# All values below are invented — R1 values must never be copied into the
# package while its rights are unconfirmed (DESIGN 16.3).  Structure is
# kept: R1 sales differ by a fraction display rounding explains (except
# cricket, exact), farm_count differs by one, locus sums by one.
def sample_receipt(sales_total_r1="42010.3", farm_count_r1="2294",
                   comparisons_relations=None):
    specs = [
        ("total", "42010", sales_total_r1, "C23"),
        ("cricket", "2500", "2500", "X23"),
        ("fly", "8300", "8299.7", "U23"),
        ("farm_count", "2293", farm_count_r1, "B23"),
    ]
    selected = {
        "R0": {("farm_count" if k == "farm_count" else "sales_" + k): r0
               for k, r0, _, _ in specs},
        "R1": {("farm_count" if k == "farm_count" else "sales_" + k): r1
               for k, _, r1, _ in specs},
    }
    digest = hashlib.sha256(json.dumps(
        selected, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    relations = comparisons_relations or {}
    metric_rows = [
        ("sales_total", "42010", sales_total_r1),
        ("sales_cricket", "2500", "2500"),
        ("sales_fly", "8300", "8299.7"),
        ("reported_farm_count", "2293", farm_count_r1),
    ]
    comparisons = []
    for metric, r0, r1 in metric_rows:
        if metric in relations:
            relation = relations[metric]
        elif metric == "reported_farm_count":
            relation = ("exact_numeric_match" if r0 == r1
                        else "source_conflict")
        else:
            relation = ("exact_numeric_match" if r0 == r1
                        else "display_rounding_consistent")
        comparisons.append({"metric": metric, "relation": relation,
                            "r1_minus_r0_KRW_million": "0"})
    locus = ({"status": "extracted", "basis": "runtime_extracted",
              "cause": "unresolved",
              "R0": {"physical_page": 4, "row": "기타(반딧불이, 나비 등)",
                     "farm_count_2024": "305"},
              "R1": {"farm_count_cells": {"P23": "40", "Y23": "20",
                                          "AB23": "108", "AE23": "138"},
                     "farm_count_2024_total": "306"},
              "species_farm_counts": []})
    return {
        "schema": source.SCHEMA,
        "official_pins": True,
        "conflict_locus": locus,
        "audit_notes": [
            {"basis": "independent_audit_note",
             "note": "synthetic audit note; no real source content"}],
        "sources": {
            "R0": {"sha256": source.PDF_SHA256,
                   "visual_crosscheck": "not_performed_by_cli"},
            "R1": {"sha256": source.XLSX_SHA256},
        },
        "survey": {"use_scope": "industry_context_only"},
        "publication": "not_promoted_to_catalog_or_manifest",
        "selection_sha256": digest,
        "observations": [
            {"aggregate": key,
             "unit": "count" if key == "farm_count" else "KRW_million",
             "R0": {"value": r0, "physical_page": 4,
                    "table": "참고 2 연도별 주요 통계"},
             "R1": {"value": r1, "sheet": source.SHEET, "cell": cell}}
            for key, r0, r1, cell in specs
        ],
        "comparisons": comparisons,
    }


class InsectEvidenceTests(ContractCase):
    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root, "industrial_insects")
        self.project = runtime("gg_core").load(self.root)
        self.reg = contract.default_registry()
        self.module = self.reg.resolve("industrial_insects")

    def review(self, receipt=None):
        with mock.patch.object(
            evidence.source_receipt, "verify_sources",
            return_value=sample_receipt() if receipt is None else receipt,
        ) as verify:
            result = evidence.review_sources(
                self.reg, self.project, "private-r0.pdf", "private-r1.xlsx"
            )
        verify.assert_called_once_with("private-r0.pdf", "private-r1.xlsx")
        return result

    def test_all_rows_unapproved_and_farm_count_quarantined(self):
        result = self.review()
        self.assertEqual(result["pack_status"], "empty_slot")
        self.assertFalse(result["canonical_write"])
        self.assertEqual("review_only", result["status"])
        by_key = {row["aggregate"]: row for row in result["observations"]}
        for row in by_key.values():
            self.assertFalse(row["approval_candidate"])
            self.assertEqual(row["axes"]["observation"], "exploratory")
            self.assertEqual(row["axes"]["rights"], "unconfirmed")
            self.assertEqual(row["axes"]["acquisition"], "bytes_held")
            self.assertIn("rights", row["promotion_blockers"])
            self.assertEqual(row["use_scope"], "industry_context")
        self.assertEqual(by_key["farm_count"]["axes"]["conflict"],
                         "unresolved")
        self.assertEqual(by_key["fly"]["axes"]["applicability"], "unverified")
        self.assertEqual(by_key["total"]["axes"]["conflict"], "none")

    def test_farm_count_row_carries_conflict_locus_verbatim(self):
        receipt = sample_receipt()
        result = self.review(receipt)
        by_key = {row["aggregate"]: row for row in result["observations"]}
        self.assertEqual(receipt["conflict_locus"],
                         by_key["farm_count"]["conflict_locus"])
        for key, row in by_key.items():
            if key != "farm_count":
                self.assertNotIn("conflict_locus", row)

    def test_audit_notes_passed_to_result_top_level(self):
        receipt = sample_receipt()
        result = self.review(receipt)
        self.assertEqual(receipt["audit_notes"], result["audit_notes"])

    def test_locus_never_changes_conflict_or_approval(self):
        # DESIGN 15.2/16.4: extracted and not_extracted loci alike must leave
        # the conflict axis and approval state untouched.
        for locus in (
            {"status": "extracted", "basis": "runtime_extracted",
             "cause": "unresolved",
             "R0": {"physical_page": 4, "row": "기타(반딧불이, 나비 등)",
                    "farm_count_2024": "305"},
             "R1": {"farm_count_cells": {"P23": "40"}, "total": "306"},
             "species_farm_counts": []},
            {"status": "not_extracted", "cause": "unresolved"},
        ):
            with self.subTest(status=locus["status"]):
                receipt = sample_receipt()
                receipt["conflict_locus"] = locus
                result = self.review(receipt)
                farm = {r["aggregate"]: r for r in result["observations"]}[
                    "farm_count"]
                self.assertEqual(locus, farm["conflict_locus"])
                self.assertEqual("unresolved", farm["axes"]["conflict"])
                self.assertFalse(farm["approval_candidate"])
                self.assertIn("conflict", farm["promotion_blockers"])

    def test_required_axes_from_module_declaration_present(self):
        required = set(self.module.evidence_applicability["required_axes"])
        result = self.review()
        for row in result["observations"]:
            self.assertTrue(required <= set(row["axes"]))

    def test_display_rounding_consistent_means_no_conflict(self):
        # 42010.3 displays as 42010 in R0's integer cells -> relation none.
        result = self.review()
        self.assertEqual(
            "none",
            {r["aggregate"]: r for r in result["observations"]}
            ["total"]["axes"]["conflict"])

    def test_unresolved_numeric_difference_blocks(self):
        # CODE #5 counterexample: an R1 total that cannot be explained by
        # display rounding must surface as unresolved, not silently none.
        receipt = sample_receipt(comparisons_relations={
            "sales_total": "unresolved_numeric_difference"})
        result = self.review(receipt)
        by_key = {row["aggregate"]: row for row in result["observations"]}
        self.assertEqual(by_key["total"]["axes"]["conflict"], "unresolved")
        self.assertIn("conflict", by_key["total"]["promotion_blockers"])

    def test_matching_farm_count_clears_conflict_axis(self):
        receipt = sample_receipt(
            farm_count_r1="2293",
            comparisons_relations={
                "reported_farm_count": "exact_numeric_match"})
        result = self.review(receipt)
        by_key = {row["aggregate"]: row for row in result["observations"]}
        self.assertEqual(by_key["farm_count"]["axes"]["conflict"], "none")

    def test_fly_applicability_unverified_regardless_of_relation(self):
        receipt = sample_receipt(comparisons_relations={
            "sales_fly": "exact_numeric_match"})
        result = self.review(receipt)
        by_key = {row["aggregate"]: row for row in result["observations"]}
        self.assertEqual(by_key["fly"]["axes"]["applicability"], "unverified")

    def test_detached_receipt_cannot_be_submitted(self):
        # review_sources accepts file paths only — a prebuilt receipt object
        # is not evidence (DESIGN 6.3).
        import inspect

        params = list(inspect.signature(evidence.review_sources).parameters)
        self.assertEqual(["registry", "project", "pdf_path", "xlsx_path"],
                         params)

    def test_unpinned_or_overclaimed_receipt_rejected(self):
        for mutation in (
            lambda r: r.update(official_pins=False),
            lambda r: r["sources"]["R0"].update(sha256="0" * 64),
            lambda r: r["sources"]["R0"].update(visual_crosscheck="passed"),
            lambda r: r["survey"].update(use_scope="farm_price"),
            lambda r: r.update(publication="catalog_promoted"),
            lambda r: r["observations"].pop(),
            lambda r: r["comparisons"].pop(),
            lambda r: r["comparisons"][0].update(relation="made_up"),
            lambda r: r.pop("conflict_locus"),
            lambda r: r.update(conflict_locus={"status": "invented"}),
            lambda r: r.update(conflict_locus={
                "status": "extracted", "cause": "resolved"}),
            lambda r: r.update(audit_notes="not-a-list"),
        ):
            receipt = sample_receipt()
            mutation(receipt)
            with self.assertRaises(ValueError):
                self.review(receipt)

    def test_output_undeclared_gets_fixed_code(self):
        module = dataclasses.replace(
            self.module,
            supported_outputs=tuple(
                o for o in self.module.supported_outputs
                if o != "evidence_review"))
        reg = contract.ModuleRegistry((module,), pack_owner=None)
        with mock.patch.object(evidence.source_receipt, "verify_sources") \
                as verify:
            with self.assertRaisesRegex(
                    ValueError, "insect_output_unsupported:evidence_review"):
                evidence.review_sources(reg, self.project,
                                        "private.pdf", "private.xlsx")
        verify.assert_not_called()

    def test_evidence_capability_unsupported_gets_fixed_code(self):
        module = dataclasses.replace(
            self.module,
            capabilities={**dict(self.module.capabilities),
                          "evidence": "unsupported"})
        reg = contract.ModuleRegistry((module,), pack_owner=None)
        with mock.patch.object(evidence.source_receipt, "verify_sources") \
                as verify:
            with self.assertRaisesRegex(
                    ValueError, "insect_capability_unsupported:evidence"):
                evidence.review_sources(reg, self.project,
                                        "private.pdf", "private.xlsx")
        verify.assert_not_called()

    def test_other_major_cannot_review_insect_receipt(self):
        root = self.make_project()
        bind_major(root, "specialty_crops")
        project = runtime("gg_core").load(root)
        with self.assertRaises(ValueError):
            evidence.review_sources(self.reg, project,
                                    "private.pdf", "private.xlsx")

    def test_source_failure_is_sanitized(self):
        with mock.patch.object(evidence.source_receipt, "verify_sources",
                               side_effect=source.ReceiptError(
                                   "hash_mismatch", "R0")):
            with self.assertRaisesRegex(
                    ValueError, "source_R0_hash_mismatch") as caught:
                evidence.review_sources(
                    self.reg, self.project,
                    "/secret/student.pdf", "/secret/student.xlsx")
        self.assertNotIn("/secret", str(caught.exception))

    def test_return_contract_has_no_generated_body_or_finance(self):
        # DESIGN 14.8: review output carries no rendered body, no computed
        # finance keys, and no output authorization.
        result = self.review()

        def keys(node, found):
            if isinstance(node, dict):
                for key, value in node.items():
                    found.add(key)
                    keys(value, found)
            elif isinstance(node, list):
                for item in node:
                    keys(item, found)

        found = set()
        keys(result, found)
        forbidden = {"body", "rendered_paper", "calculated", "computed",
                     "result", "projected_revenue", "farm_price",
                     "authorization", "authorized"}
        self.assertEqual(set(), found & forbidden)


if __name__ == "__main__":
    unittest.main()
