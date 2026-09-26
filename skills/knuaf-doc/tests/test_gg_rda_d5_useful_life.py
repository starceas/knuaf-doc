"""D5 source-cell correction, trust migration, and rejection boundaries."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "references/benchmark-packs"
PACK = PACKS / "common/rda-econ-2025"
sys.path.insert(0, str(ROOT / "scripts"))

import gg_rda_candidates as candidates
import gg_rda_lookup as lookup
import gg_rda_provenance as provenance
import gg_rda_research as research


class D5UsefulLifeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = json.loads((PACK / "corrections/pp133-135-cells.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
        cls.raw = (PACK / "records.jsonl").read_bytes().splitlines(keepends=True)
        cls.records = [json.loads(line) for line in cls.raw]
        cls.entries = {
            provenance.normalize_audit_key(row["audit_key"]): row
            for row in cls.manifest["rows"]
        }

    def test_every_cell_matches_text_mapping_and_identity(self):
        self.assertEqual(64, len({c["row_id"] for c in self.mapping["cells"]}))
        self.assertEqual(107, len(self.mapping["cells"]))
        self.assertEqual(2707, len(self.raw))
        self.assertEqual(2707, len(self.entries))
        self.assertEqual(len(self.raw), self.manifest["total_physical_rows"])
        self.assertEqual(hashlib.sha256(b"".join(self.raw)).hexdigest(),
                         self.manifest["records_file_sha256"])
        ids = set()
        dash_count = 0
        for i, cell in enumerate(self.mapping["cells"], 2601):
            rec = self.records[i - 1]
            loc = rec["locator"]
            with self.subTest(cell=cell["cell_id"]):
                self.assertEqual(cell["record_id"], rec["record_id"])
                self.assertEqual(cell["physical_page"], loc["pdf_page"])
                self.assertEqual(cell["printed_page"], loc["printed_page"])
                self.assertEqual(cell["table_title"], loc["table"])
                self.assertEqual(cell["subtable"], loc["subtable"])
                self.assertEqual(cell["parent_labels"], loc["parent_labels"])
                self.assertEqual(cell["row_label"], loc["row_label"])
                self.assertEqual(cell["column_label"], loc["column_label"])
                self.assertEqual(cell["cell_ordinal"], loc["cell_ordinal"])
                self.assertEqual(cell["cell_id"], loc["source_cell_id"])
                self.assertEqual(cell["specification"], rec["basis"]["specification"])
                self.assertEqual(" > ".join(cell["parent_labels"]) or cell["table_title"],
                                 rec["basis"]["category"])
                self.assertEqual(cell["printed_value"], rec["metrics"]["useful_life_years"])
                self.assertEqual(cell["footnotes"],
                                 rec["caveats"][:len(cell["footnotes"])])
                self.assertEqual("extracted", rec["extraction"]["status"])
                self.assertEqual("exploratory", self.manifest["rows"][i - 1]["catalog_status"])
                self.assertEqual(
                    "skills/knuaf-doc/references/benchmark-packs/common/rda-econ-2025/corrections/pp133-135-cells.json#" + cell["cell_id"],
                    self.manifest["rows"][i - 1]["source_observation"]["prep_receipt_ref"])
                self.assertEqual(cell["adult_age_printed"],
                                 rec["metrics"].get("adult_age_years"))
                if cell["printed_value"] == "-":
                    dash_count += 1
                    self.assertFalse(research._numeric_ok(rec["metrics"]["useful_life_years"]))
            ids.add(rec["record_id"])
        self.assertEqual(107, len(ids))
        self.assertEqual(8, dash_count)
        self.assertEqual("8개월", self.records[-1]["metrics"]["adult_age_years"])
        self.assertEqual("2.5", self.records[-1]["metrics"]["useful_life_years"])

    def test_manifest_links_and_derived_authority(self):
        pin = provenance.PINNED_PACKS["rda.econ.2025"]
        self.assertEqual(2707, pin["record_lines"])
        self.assertEqual(self.manifest["records_file_sha256"], pin["records_file_sha256"])
        self.assertEqual({"verified_observation_count": 105,
                          "exploratory_count": 131,
                          "quarantined_count": 2471},
                         self.manifest["status_summary"])
        duplicate_refs = 0
        for i, (line, row) in enumerate(zip(self.raw, self.manifest["rows"]), 1):
            with self.subTest(line=i):
                self.assertEqual(i, row["physical_line"])
                self.assertEqual(hashlib.sha256(line).hexdigest(), row["raw_line_sha256"])
                self.assertEqual(self.manifest["records_file_sha256"],
                                 row["audit_key"]["records_file_sha256"])
                self.assertEqual(row["raw_line_sha256"],
                                 row["audit_key"]["raw_line_sha256"])
                primary = row["duplicate_link"]["primary_audit_key"]
                if primary:
                    duplicate_refs += 1
                    self.assertEqual(pin["records_file_sha256"],
                                     primary["records_file_sha256"])
        self.assertEqual(7, duplicate_refs)
        digest = research.catalog_content_digest(self.entries)
        self.assertEqual(digest, research.PINNED_CATALOG_AUTHORITIES[
            "accepted-catalog-20260921/rda.econ.2025"])
        registry = json.loads((ROOT / "references/builtin-sources.json").read_text(encoding="utf-8"))
        self.assertEqual(pin["records_file_sha256"], registry["p4_trust"]["packs"]["rda.econ.2025"]["records_file_sha256"])
        entry = next(e for e in registry["entries"] if e["source_id"] == "rda.econ.2025")
        for artifact in entry["artifacts"]:
            path = PACKS / artifact["relative_path"]
            self.assertEqual(path.stat().st_size, artifact["bytes"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
        for coverage in entry["coverage"]["statistical_observation"]["keys"]:
            key = coverage["key_ref"]
            self.assertIn(key["physical_jsonl_line_1based"], (1, 19))
            self.assertEqual(pin["records_file_sha256"], key["records_file_sha256"])
            if coverage["catalog_digest"] is not None:
                self.assertEqual(digest, coverage["catalog_digest"])
        receipt = json.loads((ROOT.parents[1] / "docs/validation/d5-rda-useful-life.json").read_text(encoding="utf-8"))
        self.assertEqual(pin["records_file_sha256"], receipt["new_records_sha256"])
        self.assertEqual(digest, receipt["new_catalog_content_digest"])

    def test_rejected_rows_unreachable_but_unaffected_quarantine_stays_searchable(self):
        ctx = research.resolver_context(
            catalog=research.accept_catalog(
                self.entries,
                verified_indexes={"rda.econ.2025": provenance.build_audit_index(
                    PACK / "records.jsonl", "rda.econ.2025",
                    self.manifest["records_file_sha256"])},
                authority="accepted-catalog-20260921/rda.econ.2025"),
            pack_ids={"rda.econ.2025"})
        for i in range(68, 122):
            rec, mrow = self.records[i - 1], self.manifest["rows"][i - 1]
            key = mrow["audit_key"]
            with self.subTest(line=i):
                self.assertEqual("rejected", rec["extraction"]["status"])
                self.assertEqual("quarantined", mrow["catalog_status"])
                self.assertTrue(any(x.startswith("label_not_in_source:") for x in rec["caveats"]))
                raw = lookup.lookup_rda_data(rec["basis"]["item_name"], "전국",
                                             kind="useful_life", audit_key=key)
                self.assertEqual("not_found", raw["status"])
                self.assertEqual([], raw["records"])
                approved = candidates.lookup_approved_candidates(
                    rec["basis"]["item_name"], "전국", "hort_env_systems",
                    kind="useful_life", audit_key=key, use_scope="plan_input")
                self.assertEqual("not_found", approved["status"])
                self.assertEqual([], approved["candidates"])
                proposed = research.propose("2025", {"metric": "useful_life_years"},
                                            key, context=ctx)
                self.assertEqual("unresolved", proposed["status"])
                self.assertEqual("extraction_rejected", proposed["reason"])
        first = self.records[0]
        old_quarantine = lookup.lookup_rda_data(
            first["basis"]["item_name"], "전국", kind="useful_life",
            audit_key=self.manifest["rows"][0]["audit_key"])
        self.assertEqual("unique", old_quarantine["status"])
        self.assertEqual("quarantined", old_quarantine["records"][0]["verification"]["catalog_status"])
        self.assertNotIn("catalog_status", old_quarantine["records"][0])
        still_a_candidate = candidates.lookup_approved_candidates(
            first["basis"]["item_name"], "전국", "hort_env_systems",
            kind="useful_life", audit_key=self.manifest["rows"][0]["audit_key"],
            use_scope="plan_input")
        self.assertEqual(1, len(still_a_candidate["candidates"]))
        self.assertEqual("quarantined", still_a_candidate["candidates"][0]["observation_status"])

    def test_all_new_cells_searchable_but_never_approved(self):
        for i, cell in enumerate(self.mapping["cells"], 2601):
            rec = self.records[i - 1]
            key = self.manifest["rows"][i - 1]["audit_key"]
            with self.subTest(cell=cell["cell_id"]):
                raw = lookup.lookup_rda_data(
                    rec["basis"]["item_name"], "전국", kind="useful_life",
                    year=2025, audit_key=key)
                self.assertEqual("unique", raw["status"])
                self.assertEqual("exploratory", raw["records"][0]["verification"]["catalog_status"])
                self.assertNotIn("catalog_status", raw["records"][0])
                result = candidates.lookup_approved_candidates(
                    rec["basis"]["item_name"], "전국", "hort_env_systems",
                    kind="useful_life", year=2025, audit_key=key,
                    use_scope="plan_input")
                self.assertEqual("unapproved", result["status"])
                self.assertEqual([], result["approved_candidates"])
                self.assertEqual("exploratory", result["candidates"][0]["observation_status"])
        key = self.manifest["rows"][2600]["audit_key"]
        ctx = research.resolver_context(
            catalog=research.accept_catalog(
                self.entries,
                verified_indexes={"rda.econ.2025": provenance.build_audit_index(
                    PACK / "records.jsonl", "rda.econ.2025",
                    self.manifest["records_file_sha256"])},
                authority="accepted-catalog-20260921/rda.econ.2025"),
            pack_ids={"rda.econ.2025"})
        proposed = research.propose("2025", {"metric": "useful_life_years"},
                                    key, context=ctx)
        self.assertEqual("unit_mapping_unknown", proposed["reason"])

    def test_hort_page_prohibition_stays_bound_to_133_through_135(self):
        profile = json.loads((ROOT / "references/hort-env-systems/profile.json")
                             .read_text(encoding="utf-8"))
        rule = next(r for r in profile["prohibited_rows"]
                    if r["pack_id"] == "rda.econ.2025" and r["kind"] == "useful_life")
        self.assertEqual([133, 134, 135], rule["locator_pdf_pages"])
        self.assertEqual({133, 134, 135},
                         {r["locator"]["pdf_page"] for r in self.records[2600:]})


if __name__ == "__main__":
    unittest.main()
