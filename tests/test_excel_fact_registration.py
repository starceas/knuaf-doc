"""Synthetic native receipts exercise validation, not actual Excel execution."""
import copy
import json
from pathlib import Path
from unittest import mock
import zipfile
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from tests._harness import ContractCase, bind_major, runtime, section_op, write_text


class ExcelFactRegistrationTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.school = runtime("gg_school_excel")
        self.root = self.make_project()
        bind_major(self.root)
        self.context = self.mc.output_context(self.root, "specialty_crops")
        self.workbook = self.root / "recalculated.xlsx"
        book = Workbook()
        book.active.title = "계획"
        book.active["A1"] = "=10*25"
        book.active["B1"] = "=20*25"
        book.save(self.workbook)
        book.close()
        self.cache("250")
        self.receipt = self.root / "native.json"
        self.profile = "school_17_sheet_v1"
        self.native = {
            "status": "converted", "engine": "Microsoft Excel",
            "engine_result": "GG_OFFICE_SESSION|closed|saved|synthetic",
            "major_authorization": [self.mc.authorize_output(
                self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context).to_dict()],
            "office_session": {"app": "excel", "reason": "closed_confirmed",
                               "cleanup": "closed", "save_state": "saved"},
            "published_office_sha256": self.core.digest(self.workbook.read_bytes()),
            "validation": {"workbook_profile": self.profile, "finance_profile": self.profile,
                           "structure_issues": [], "school_issues": [],
                           "excel": {"valid": True, "sha256": self.core.digest(self.workbook.read_bytes())}}}
        self.mapping = {"schema": self.facts.MAP_SCHEMA, "workbook_profile": self.profile,
                        "cells": [{"fact_id": "sales", "field_id": "매출액", "sheet": "계획",
                                   "cell": "A1", "period": "2026년", "unit": "천원", "scope": "합성농장"}]}
        self.rebind()

    def cache(self, value, kind=None):
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        with zipfile.ZipFile(self.workbook) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        name = "xl/worksheets/sheet1.xml"
        xml = ET.fromstring(parts[name])
        node = xml.find(f".//{{{ns}}}c[@r='A1']")
        if kind is None:
            node.attrib.pop("t", None)
        else:
            node.set("t", kind)
        cache = node.find(f"{{{ns}}}v")
        if cache is not None:
            node.remove(cache)
        if value is not None:
            ET.SubElement(node, f"{{{ns}}}v").text = value
        parts[name] = ET.tostring(xml)
        with zipfile.ZipFile(self.workbook, "w") as archive:
            for path, raw in parts.items():
                archive.writestr(path, raw)

    def rebind(self):
        actual = self.core.digest(self.workbook.read_bytes())
        self.native["published_office_sha256"] = actual
        self.native["validation"]["excel"]["sha256"] = actual
        self.receipt.write_text(json.dumps(self.native))
        self.mapping["workbook"] = {"sha256": actual}
        self.mapping["receipt"] = {"sha256": self.core.digest(self.receipt.read_bytes())}

    def build(self):
        return self.facts.build_registration(self.root, self.workbook.name, self.receipt.name,
                                             self.mapping, source_id="excel-plan", context=self.context)

    def test_registers_cache_with_provenance_and_generic_body_crosscheck(self):
        change = self.build()
        before = self._tree_bytes(self.root)
        self.assertNotIn("excel-plan", self.core.load(self.root)["sources"])
        self.assertEqual(before, self._tree_bytes(self.root))
        self.core.apply(self.root, change, self.core.load(self.root)["revision"])
        project = self.core.load(self.root)
        fact = project["facts"]["sales"]
        self.assertEqual((fact["value"], fact["value_type"], fact["kind"]), ("250", "decimal", "observation"))
        self.assertEqual(project["sources"]["excel-plan"]["hash"], self.mapping["workbook"]["sha256"])
        locator = fact["source_refs"][0]["locator"]
        for part in ("계획!A1", "period=2026년", "unit=천원", self.mapping["workbook"]["sha256"]):
            self.assertIn(part, locator)
        items, blocked = self.core._finance_check_items(project)
        self.assertIsNone(blocked)
        self.assertEqual(items[0]["value"], "250")
        self.assertEqual(self.school.crosscheck_body("합성농장 2026년 매출액은 250 천원이다.", items), [])
        self.assertTrue(self.school.crosscheck_body("합성농장 2026년 매출액은 251 천원이다.", items))
        write_text(self.root, "sections/body.md", "합성농장 2026년 매출액은 251 천원이다.")
        self.core.apply(self.root, {"request_id": "body", "ops": [
            section_op("body", "계획", "sections/body.md")]}, project["revision"])
        rows = self.core.checks(self.root, self.core.load(self.root))
        self.assertTrue(any(row["check_id"] == "body_finance_crosscheck" and row["status"] == "fail"
                            for row in rows))

    def test_absent_error_string_boolean_and_nonfinite_caches_are_held(self):
        for value, kind in ((None, None), ("#DIV/0!", "e"), ("250", "str"),
                            ("1", "b"), ("NaN", None), ("INF", None), ("not-a-number", None)):
            self.cache(value, kind)
            self.rebind()
            before = self._tree_bytes(self.root)
            with self.subTest(value=value, kind=kind), self.assertRaises(self.facts.RegistrationHeld):
                self.build()
            self.assertEqual(before, self._tree_bytes(self.root))

    def test_native_proof_and_hash_bound_profile_required(self):
        original = copy.deepcopy(self.native)
        for defect in ("operation", "missing_saved", "no_engine_result", "engine", "output_hash",
                       "receipt_hash", "map_hash", "profile", "authorization"):
            self.native = copy.deepcopy(original)
            if defect == "operation":
                self.native = {"schema": "gg-xlsx-fill-receipt/v1"}
            elif defect == "missing_saved":
                self.native["office_session"]["save_state"] = "unknown"
            elif defect == "no_engine_result":
                self.native["engine_result"] = "Excel completed"
            elif defect == "engine":
                self.native["engine"] = "Python"
            elif defect == "authorization":
                self.native["major_authorization"] = []
            if defect != "operation":
                self.rebind()
            else:
                self.receipt.write_text(json.dumps(self.native))
                self.mapping["receipt"]["sha256"] = self.core.digest(self.receipt.read_bytes())
            if defect == "output_hash":
                self.native["published_office_sha256"] = "0" * 64
                self.receipt.write_text(json.dumps(self.native))
                self.mapping["receipt"]["sha256"] = self.core.digest(self.receipt.read_bytes())
            elif defect == "receipt_hash":
                self.mapping["receipt"]["sha256"] = "0" * 64
            elif defect == "map_hash":
                self.mapping["workbook"]["sha256"] = "0" * 64
            elif defect == "profile":
                self.mapping["workbook_profile"] = "fruit_16_sheet_v1"
            with self.subTest(defect=defect), self.assertRaises((self.facts.RegistrationHeld, self.mc.OutputHeldError)):
                self.build()
            self.mapping["workbook_profile"] = self.profile

    def test_conflicting_or_incomplete_mapping_is_held(self):
        row = copy.deepcopy(self.mapping["cells"][0])
        for rows in ([], [dict(row, cell="Z99")], [dict(row, sheet="없음")],
                     [dict(row, unit="kg")], [dict(row, period="")],
                     [row, dict(row, cell="B1")], [row, dict(row, fact_id="other")]):
            self.mapping["cells"] = rows
            with self.subTest(rows=rows), self.assertRaises(self.facts.RegistrationHeld):
                self.build()

    def test_windows_native_saved_marker_is_accepted_without_mac_session(self):
        self.native.pop("office_session")
        self.native["engine_result"] = "Excel process completed; Excel 16.synthetic"
        self.rebind()
        change = self.build()
        self.assertEqual(change["ops"][1]["value"]["value"], "250")

    def test_existing_fact_with_different_value_cannot_be_replaced(self):
        change = self.build()
        self.core.apply(self.root, change, self.core.load(self.root)["revision"])
        self.cache("251")
        self.native["major_authorization"] = [self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context).to_dict()]
        self.rebind()
        before = self._tree_bytes(self.root)
        with self.assertRaisesRegex(self.facts.RegistrationHeld, "existing_fact_conflict"):
            self.build()
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_duplicate_cell_nodes_cannot_select_an_arbitrary_cache(self):
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        with zipfile.ZipFile(self.workbook) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        name = "xl/worksheets/sheet1.xml"
        xml = ET.fromstring(parts[name])
        row = xml.find(f".//{{{ns}}}row")
        duplicate = copy.deepcopy(row.find(f"{{{ns}}}c"))
        duplicate.find(f"{{{ns}}}v").text = "251"
        row.append(duplicate)
        parts[name] = ET.tostring(xml)
        with zipfile.ZipFile(self.workbook, "w") as archive:
            for part, raw in parts.items():
                archive.writestr(part, raw)
        self.rebind()
        with self.assertRaisesRegex(self.facts.RegistrationHeld, "duplicate_workbook_cell"):
            self.build()
