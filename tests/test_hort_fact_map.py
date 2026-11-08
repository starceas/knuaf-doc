"""The horticulture cell map is one common gg-xlsx-fact-map/v1 file.

The map lists the body Ⅴ chapter's core monetary figures and the 18-sheet cell,
period and unit each one comes from.  These tests lock four things: the exact
twenty (fact_id, sheet, cell) pairs and the value each one registers, the map
review gate that refuses a ratio cell or an excluded cell even when the
workbook carries a numeric cache there, byte-exact hash binding of workbook and
receipt, and the dedicated horticulture receipt chain.

The native manifest used here is a synthetic fixture, not Excel execution; the
real recalculation is exercised outside the suite.
"""
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from tests._harness import ContractCase, runtime

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "skills/knuaf-doc/references/hort-env-systems"
MAP_PATH = REF / "finance-fact-map.json"
PARAMS_PATH = REF / "finance-params.json"
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}

# The reviewed map, frozen independently of the file under test: a swapped or
# dropped row must fail even though the file itself stays self-consistent.
EXPECTED_ROWS = (
    ("hort-invest-total", "4.투자계획", "C13", "천원"),
    ("hort-invest-year1", "4.투자계획", "C8", "천원"),
    ("hort-loan-principal-2027", "5. 원리금상환계획", "D8", "천원"),
    ("hort-loan-interest-2027", "5. 원리금상환계획", "E8", "천원"),
    ("hort-facility-construction", "6.시설계획", "C76", "천원"),
    ("hort-sales-2027", " 7. 판매계획", "O25", "천원"),
    ("hort-sales-2028", " 7. 판매계획", "O34", "천원"),
    ("hort-sales-2029", " 7. 판매계획", "O43", "천원"),
    ("hort-sales-2030", " 7. 판매계획", "O52", "천원"),
    ("hort-sales-2031", " 7. 판매계획", "O61", "천원"),
    ("hort-material-2027", "9. 영농자재구매계획", "G15", "천원"),
    ("hort-labor-2027", "11. 인건비계획", "I17", "천원"),
    ("hort-overhead-2027", "12 .경비계획", "C26", "천원"),
    ("hort-depreciation-2027", "13. 감가상각비계획 ", "E12", "천원"),
    ("hort-expense-2027", "14. 생산원가계획", "C12", "천원"),
    ("hort-production-cost-2027", "14. 생산원가계획", "C25", "천원"),
    ("hort-revenue-2027", "15. 손익계획", "C6", "천원"),
    ("hort-income-2027", "16. 수익성분석", "G23", "천원"),
    ("hort-net-income-2027", "16. 수익성분석", "G24", "천원"),
    ("hort-assets-opening", "17. 추정대차대조표", "C30", "천원"),
)

# One distinct cache value per row, so a swapped pair changes two values.
EXPECTED_VALUES = {row[0]: 1000 + 7 * index
                   for index, row in enumerate(EXPECTED_ROWS)}


def _map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _params():
    return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["parameters"]


def _inject_caches(path, cells):
    """Write plain numeric caches (no ``t`` attribute) into an xlsx in place."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    workbook = ET.fromstring(members["xl/workbook.xml"])
    rels = ET.fromstring(members["xl/_rels/workbook.xml.rels"])
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    sheets = {}
    for sheet in workbook.find(f"{{{namespace}}}sheets"):
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[rid].lstrip("/")
        name = target if target.startswith("xl/") else "xl/" + target
        sheets[sheet.get("name")] = name
    for (sheet, ref), value in cells.items():
        member = sheets[sheet]
        xml = ET.fromstring(members[member])
        data = xml.find(f"{{{namespace}}}sheetData")
        row_number = int(re.search(r"[0-9]+", ref).group())
        row = None
        for candidate in data.findall(f"{{{namespace}}}row"):
            if int(candidate.get("r")) == row_number:
                row = candidate
                break
        if row is None:
            row = ET.SubElement(data, f"{{{namespace}}}row", {"r": str(row_number)})
        for candidate in list(row.findall(f"{{{namespace}}}c")):
            if candidate.get("r") == ref:
                row.remove(candidate)
        cell = ET.SubElement(row, f"{{{namespace}}}c", {"r": ref})
        ET.SubElement(cell, f"{{{namespace}}}v").text = str(value)
        rows = row.findall(f"{{{namespace}}}c")
        for position, candidate in enumerate(rows):
            row.remove(candidate)
        rows.sort(key=lambda item: (int(re.sub(r"[A-Z]", "", item.get("r"))),
                                    item.get("r")))
        for candidate in rows:
            row.append(candidate)
        members[member] = ET.tostring(xml, encoding="utf-8",
                                      xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


class MapFileTests(ContractCase):
    def setUp(self):
        self.map = _map()
        self.facts = runtime("gg_excel_facts")
        self.hort = runtime("gg_hort_workbook")

    def test_rows_name_real_sheets_cells_and_body_nodes(self):
        self.assertEqual(self.map["schema"], self.facts.MAP_SCHEMA)
        profile = json.loads((REF / "profile.json").read_text(encoding="utf-8"))
        roster = profile["workbook_reference"]["sheets"]
        nodes = {section["item"] for section in profile["precedents"][1]["sections"]}
        cells = self.map["cells"]
        self.assertEqual(len(cells), 20)
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

    def test_map_is_exactly_the_reviewed_row_set(self):
        shipped = {(row["fact_id"], row["sheet"], row["cell"], row["unit"])
                   for row in self.map["cells"]}
        self.assertEqual(set(EXPECTED_ROWS), shipped)

    def test_map_passes_the_review_gate(self):
        rows = self.hort.check_fact_map(self.map, _params())
        self.assertEqual(len(rows), len(EXPECTED_ROWS))

    def test_units_stay_monetary_and_ratios_are_excluded(self):
        excluded = {(row["sheet"], row["range"])
                    for row in self.map["excluded_series"]}
        self.assertIn(("16. 수익성분석", "G27:K27"), excluded)
        self.assertIn(("14. 생산원가계획", "C28:G28"), excluded)
        for row in self.map["cells"]:
            with self.subTest(row=row["fact_id"]):
                self.assertEqual(row["unit"], "천원")

    def test_reviewed_cells_never_inside_an_excluded_series(self):
        for row in self.map["cells"]:
            for span in self.map["excluded_series"]:
                if span["sheet"] != row["sheet"]:
                    continue
                with self.subTest(row=row["fact_id"], span=span["range"]):
                    self.assertFalse(_inside(span["range"], row["cell"]))


def _inside(span, cell):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?",
                         span)
    low, first, high, last = match.groups()
    high = high or low
    last = last or first
    column, row = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell).groups()
    return (min(low, high) <= column <= max(low, high)
            and int(first) <= int(row) <= int(last))


class _ChainCase(ContractCase):
    """Synthetic native chain shared by the registration and binding cases."""

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
        self.chain = chain
        self.root = root

    def issue_native(self, caches):
        _inject_caches(self.chain.native, caches)
        self.chain._issue_native()
        return self.stage()

    def stage(self, record=None):
        root = self.root
        for source, name in ((self.chain.native, "recalc.xlsx"),
                             (self.chain.pdf, "recalc.pdf")):
            (root / name).write_bytes(source.read_bytes())
        if record is None:
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

    def bound(self, hashes, **overrides):
        mapping = _map()
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": hashes["workbook"]}
        mapping["receipt"] = {"path": "native.json", "sha256": hashes["receipt"]}
        mapping.update(overrides)
        return mapping

    def build(self, mapping):
        return self.facts.build_registration(
            self.root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan",
            context=self.mc.output_context(self.root, "hort_env_systems"))

    def registered(self, change):
        return {op["value"]["id"]: (op["value"]["excel_provenance"]["sheet"],
                                    op["value"]["excel_provenance"]["cell"],
                                    op["value"]["value"])
                for op in change["ops"] if op["collection"] == "facts"}

    def held(self, mapping, reason):
        before = self._tree_bytes(self.root)
        with self.assertRaisesRegex(self.facts.RegistrationHeld, reason):
            self.build(mapping)
        self.assertEqual(before, self._tree_bytes(self.root))


class RegistrationTests(_ChainCase):
    """A real registration: the exact cell each reviewed row reads."""

    def test_every_reviewed_row_reads_its_own_cell(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        change = self.build(self.bound(hashes))
        got = self.registered(change)
        want = {fact_id: (sheet, cell, str(value))
                for fact_id, sheet, cell, _ in EXPECTED_ROWS
                for value in (EXPECTED_VALUES[fact_id],)}
        self.assertEqual(want, got)
        self.assertEqual(len(change["ops"]), 21)

    def test_swapped_rows_do_not_register_the_reviewed_values(self):
        # The same cache set, but the two investment rows exchanged in the map:
        # structure still passes, so only the value binding can catch it.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        rows = mapping["cells"]
        first = next(index for index, row in enumerate(rows)
                     if row["fact_id"] == "hort-invest-total")
        second = next(index for index, row in enumerate(rows)
                      if row["fact_id"] == "hort-invest-year1")
        rows[first]["cell"], rows[second]["cell"] = (
            rows[second]["cell"], rows[first]["cell"])
        got = self.registered(self.build(mapping))
        self.assertEqual(got["hort-invest-total"][2], str(EXPECTED_VALUES["hort-invest-year1"]))
        self.assertNotEqual(got["hort-invest-total"][2],
                            str(EXPECTED_VALUES["hort-invest-total"]))


class MapGateTests(ContractCase):
    """The review gate refuses what the adapter alone cannot see."""

    def setUp(self):
        self.hort = runtime("gg_hort_workbook")
        self.params = _params()

    def gate(self, mapping):
        return self.hort.check_fact_map(mapping, self.params)

    def test_ratio_cell_disguised_as_money_is_refused(self):
        # 16!G27 is 농가수익률 (0%): a numeric cache 0.25 exists there, and the
        # row claims 천원, so only the map's unit/exclusion review can stop it.
        mapping = _map()
        for row in mapping["cells"]:
            if row["fact_id"] == "hort-net-income-2027":
                row["cell"] = "G27"
        with self.assertRaisesRegex(self.hort.Held, "fact_map_excluded_cell"):
            self.gate(mapping)

    def test_registry_ratio_cell_disguised_as_money_is_refused(self):
        # 7!O75 is sales.y1.marketability_rate (RATIO in the registry); the map
        # row says 천원 while a numeric cache 0.25 sits in the cell.
        mapping = _map()
        mapping["cells"] = [{"fact_id": "hort-marketability", "field_id": "finance.rate",
                             "sheet": " 7. 판매계획", "cell": "O75", "period": "2027",
                             "scope": "student_farm", "unit": "천원",
                             "body_label": "상품화율", "node": "ch5_sec3"},
                            *mapping["cells"]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_unit_mismatch"):
            self.gate(mapping)

    def test_duplicate_row_is_refused(self):
        mapping = _map()
        mapping["cells"] = [*mapping["cells"],
                            dict(mapping["cells"][0], fact_id="hort-duplicate")]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_row_conflict"):
            self.gate(mapping)

    def test_unknown_sheet_or_cell_is_refused(self):
        mapping = _map()
        mapping["cells"] = [dict(mapping["cells"][0], sheet="19. 없는시트"),
                            *mapping["cells"][1:]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_sheet_unknown"):
            self.gate(mapping)
        mapping = _map()
        mapping["cells"] = [dict(mapping["cells"][0], cell="C13:D13"),
                            *mapping["cells"][1:]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_cell_invalid"):
            self.gate(mapping)


class BindingTests(_ChainCase):
    """Hash binding and the horticulture receipt chain, not just the map shape."""

    def test_shipped_map_is_held_until_a_run_binds_its_hashes(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        self.held(_map(), "fact_map_hash_mismatch")

    def test_foreign_schema_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, schema="gg-xlsx-fact-map-draft/v1"),
                  "fact_map_schema_invalid")

    def test_missing_cells_are_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, cells=[]), "cell_mapping_required")

    def test_foreign_profile_label_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, workbook_profile="school_17_sheet_v1"),
                  "fact_map_profile_mismatch")

    def test_wrong_workbook_hash_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": "f" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_wrong_receipt_hash_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["receipt"] = {"path": "native.json", "sha256": "e" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_workbook_bytes_changed_after_binding_are_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        (self.root / "recalc.xlsx").write_bytes(
            (self.root / "recalc.xlsx").read_bytes() + b"\0")
        self.held(mapping, "fact_map_hash_mismatch")

    def test_receipt_without_horticulture_chain_is_held(self):
        # General Excel save/close proof stays; the hort transform binding is
        # gone.  Re-hash the manifest and rebind the map so the hash gate is
        # not what refuses it.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        record = json.loads((self.root / "native.json").read_text(encoding="utf-8"))
        record.pop("validation")
        hashes = self.stage(record)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(self.bound(hashes))
        self.assertEqual(held.exception.reason, "hort_transform_receipt_invalid")

    def test_foreign_project_chain_is_held(self):
        from tests.test_hort_finance_pipeline import Chain, _native_workbook
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        other = Chain(methodName="test_full_chain_plan_transform_native_verify")
        other.setUp()
        self.addCleanup(other.doCleanups)
        other._run_to_transform()
        record = json.loads((self.root / "native.json").read_text(encoding="utf-8"))
        binding = dict(record["validation"]["template_receipt"])
        binding["path"] = str(other.transform_rec.resolve())
        binding["sha256"] = self.core.digest(other.transform_rec.read_bytes())
        record["validation"] = dict(record["validation"], template_receipt=binding,
                                    previous_receipt_sha256=binding["sha256"])
        hashes = self.stage(record)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(self.bound(hashes))
        self.assertEqual(held.exception.reason, "hort_transform_receipt_invalid")


if __name__ == "__main__":
    sys.exit(__import__("unittest").main())
