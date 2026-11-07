"""The horticulture cell map is one common gg-xlsx-fact-map/v1 file.

The map lists the body Ⅴ chapter's core monetary figures and the 18-sheet cell,
period and unit each one comes from.  These tests lock two things: the shipped
map's rows are structurally registrable, and a map whose schema, hashes,
profile, cell rows or units do not line up is held before any fact is written.

The native manifest used here is a synthetic fixture, not Excel execution; the
real recalculation is exercised outside the suite.
"""
import json
import re
import sys
from pathlib import Path

from tests._harness import ContractCase, runtime

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "skills/knuaf-doc/references/hort-env-systems"
MAP_PATH = REF / "finance-fact-map.json"
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}


def _map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


class MapFileTests(ContractCase):
    def setUp(self):
        self.map = _map()
        self.facts = runtime("gg_excel_facts")

    def test_rows_name_real_sheets_cells_and_body_nodes(self):
        self.assertEqual(self.map["schema"], self.facts.MAP_SCHEMA)
        roster = json.loads((REF / "profile.json").read_text(
            encoding="utf-8"))["workbook_reference"]["sheets"]
        profile = json.loads((REF / "profile.json").read_text(encoding="utf-8"))
        nodes = {section["item"] for section in profile["precedents"][1]["sections"]}
        cells = self.map["cells"]
        self.assertGreaterEqual(len(cells), 10)
        self.assertLessEqual(len(cells), 20)
        self.assertEqual(len({row["fact_id"] for row in cells}), len(cells))
        self.assertEqual(len({(row["sheet"], row["cell"]) for row in cells}),
                         len(cells))
        for row in cells:
            with self.subTest(row=row["fact_id"]):
                self.assertTrue(CELL.fullmatch(row["cell"]))
                self.assertIn(row["sheet"], roster)
                self.assertIn(row["unit"], {"원", "천원"})
                self.assertIn(row["node"], nodes)
                self.assertTrue(row["body_label"].strip())
                self.assertTrue(re.fullmatch(r"\d{4}", row["period"]))

    def test_units_stay_monetary_and_ratios_are_excluded(self):
        excluded = {(row["sheet"], row["range"])
                    for row in self.map["excluded_series"]}
        self.assertIn(("16. 수익성분석", "G27:K27"), excluded)
        self.assertIn(("14. 생산원가계획", "C28:G28"), excluded)
        covered = {row["sheet"] for row in self.map["cells"]}
        self.assertTrue({"16. 수익성분석", "14. 생산원가계획"} <= covered)
        for row in self.map["cells"]:
            with self.subTest(row=row["fact_id"]):
                self.assertEqual(row["unit"], "천원")


class MapRefusalTests(ContractCase):
    """A bad map is held with its reason and leaves the project untouched."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.hort = runtime("gg_hort_workbook")
        from tests.test_hort_finance_pipeline import Chain, _native_workbook
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        root = self.make_project()
        (root / "project.json").write_bytes(
            (chain.root / "project.json").read_bytes())
        chain.root = root
        chain._run_to_transform()
        _native_workbook(chain.native, chain.roster,
                         chain.docs[self.hort.FILES[1]]["parameters"],
                         chain.facts)
        chain._issue_native()
        self.chain = chain
        self.root = root
        self.hashes = self.stage()

    def stage(self):
        root = self.root
        for source, name in ((self.chain.native, "recalc.xlsx"),
                             (self.chain.pdf, "recalc.pdf")):
            (root / name).write_bytes(source.read_bytes())
        record = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        record["published_office"] = str((root / "recalc.xlsx").resolve())
        record["published_pdf"] = str((root / "recalc.pdf").resolve())
        record["engine_result"] = "GG_OFFICE_SESSION|closed|saved|synthetic"
        record["office_session"] = dict(SESSION)
        receipt = root / "native.json"
        receipt.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                      indent=2), encoding="utf-8")
        return {"workbook": self.core.digest((root / "recalc.xlsx").read_bytes()),
                "receipt": self.core.digest(receipt.read_bytes())}

    def bound(self, **overrides):
        mapping = _map()
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": self.hashes["workbook"]}
        mapping["receipt"] = {"path": "native.json", "sha256": self.hashes["receipt"]}
        mapping.update(overrides)
        return mapping

    def build(self, mapping):
        return self.facts.build_registration(
            self.root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan",
            context=self.mc.output_context(self.root, "hort_env_systems"))

    def held(self, mapping, reason):
        before = self._tree_bytes(self.root)
        with self.assertRaisesRegex(self.facts.RegistrationHeld, reason):
            self.build(mapping)
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_shipped_map_is_held_until_a_run_binds_its_hashes(self):
        self.held(_map(), "fact_map_hash_mismatch")

    def test_foreign_schema_is_held(self):
        self.held(self.bound(schema="gg-xlsx-fact-map-draft/v1"),
                  "fact_map_schema_invalid")

    def test_missing_cells_are_held(self):
        self.held(self.bound(cells=[]), "cell_mapping_required")

    def test_foreign_profile_label_is_held(self):
        self.held(self.bound(workbook_profile="school_17_sheet_v1"),
                  "fact_map_profile_mismatch")

    def test_duplicate_cell_rows_are_held(self):
        mapping = self.bound()
        row = {"fact_id": "hort-duplicate", "field_id": "finance.duplicate",
               "sheet": self.chain.roster[0], "cell": "A1", "period": "2027",
               "scope": "student_farm", "unit": "천원"}
        mapping["cells"] = [row, dict(row, fact_id="hort-duplicate-2")]
        self.held(mapping, "cell_or_fact_mapping_conflict")

    def test_non_monetary_unit_is_held(self):
        mapping = self.bound()
        mapping["cells"] = [dict(mapping["cells"][0], unit="원/kg"),
                            *mapping["cells"][1:]]
        self.held(mapping, "monetary_unit_required")

    def test_cell_without_native_cache_is_held(self):
        mapping = self.bound()
        mapping["cells"] = [dict(mapping["cells"][0], cell="G27",
                                 sheet="16. 수익성분석", unit="천원"),
                            *mapping["cells"][1:]]
        self.held(mapping, "numeric_cache_required")


if __name__ == "__main__":
    sys.exit(__import__("unittest").main())
