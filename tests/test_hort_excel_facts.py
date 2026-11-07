"""Horticulture workbooks enter fact registration through their own chain.

The 18-sheet workbook declares no generic workbook profile: its numbers are
accepted only from a native Excel manifest bound to a verified transform
receipt.  Every fixture here is synthetic (no student workbook, no Office
engine); the native manifest is written by the fixture, not by Excel.
"""
import json
from pathlib import Path

from tests._harness import ContractCase, runtime

PROFILE = "hort_18_sheet_reconstructed_v1"
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}

class HortExcelFactRegistrationTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.hort = runtime("gg_hort_workbook")
        from tests.test_hort_finance_pipeline import Chain, _native_workbook
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        # The chain fixture writes project.json directly; move that canonical
        # state onto a lock-installed project so gg_core.apply can commit.
        root = self.make_project()
        (root / "project.json").write_bytes((chain.root / "project.json").read_bytes())
        chain.root = root
        chain._run_to_transform()
        _native_workbook(chain.native, chain.roster,
                         chain.docs[self.hort.FILES[1]]["parameters"], chain.facts)
        chain._issue_native()
        self.chain = chain
        self.root = root
        self.sheet = chain.roster[0]

    def stage(self, *, native=None):
        """Copy the native workbook/PDF/receipt into the project root."""
        root = self.root
        for source, name in ((self.chain.native, "recalc.xlsx"),
                             (self.chain.pdf, "recalc.pdf")):
            (root / name).write_bytes(source.read_bytes())
        record = dict(native if native is not None
                      else json.loads(self.chain.native_rec.read_text(encoding="utf-8")))
        record["published_office"] = str((root / "recalc.xlsx").resolve())
        record["published_pdf"] = str((root / "recalc.pdf").resolve())
        record["engine_result"] = "GG_OFFICE_SESSION|closed|saved|synthetic"
        record["office_session"] = dict(SESSION)
        receipt = root / "native.json"
        receipt.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                      indent=2), encoding="utf-8")
        return {"workbook": self.core.digest((root / "recalc.xlsx").read_bytes()),
                "receipt": self.core.digest(receipt.read_bytes())}

    def mapping(self, hashes, *, profile=PROFILE):
        return {"schema": self.facts.MAP_SCHEMA,
                "workbook_profile": profile,
                "workbook": {"sha256": hashes["workbook"]},
                "receipt": {"sha256": hashes["receipt"]},
                "cells": [{"fact_id": "hort.plan.sales", "field_id": "매출액",
                           "sheet": self.sheet, "cell": "A1", "period": "2027",
                           "unit": "천원", "scope": "농장"}]}

    def build(self, mapping):
        return self.facts.build_registration(
            self.root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan",
            context=self.mc.output_context(self.root, "hort_env_systems"))

    def test_native_chain_registers_horticulture_fact(self):
        change = self.build(self.mapping(self.stage()))
        source, fact = change["ops"]
        self.assertEqual(source["value"]["workbook_profile"], PROFILE)
        self.assertEqual(fact["value"]["excel_provenance"]["workbook_profile"], PROFILE)
        self.assertEqual((fact["value"]["value"], fact["value"]["value_type"]), ("5", "decimal"))
        before = self._tree_bytes(self.root)
        self.assertNotIn("excel-plan", self.core.load(self.root)["sources"])
        self.assertEqual(before, self._tree_bytes(self.root))
        self.core.apply(self.root, change, self.core.load(self.root)["revision"])
        project = self.core.load(self.root)
        self.assertEqual(project["facts"]["hort.plan.sales"]["value"], "5")
        self.assertEqual(project["sources"]["excel-plan"]["workbook_profile"], PROFILE)
        self.assertIsNone(self.core._finance_check_items(project)[1])

    def test_horticulture_without_transform_chain_is_held(self):
        native = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        native.pop("validation")
        hashes = self.stage(native=dict(
            native, validation={"finance_profile": PROFILE,
                                "structure_issues": [], "school_issues": []}))
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(self.mapping(hashes))
        self.assertEqual(held.exception.reason, "hort_transform_receipt_invalid")
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_foreign_transform_receipt_cannot_stand_in(self):
        native = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        binding = dict(native["validation"]["template_receipt"])
        binding["sha256"] = "0" * 64
        native["validation"] = dict(native["validation"], template_receipt=binding)
        hashes = self.stage(native=native)
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.mc.OutputHeldError):
            self.build(self.mapping(hashes))
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_generic_school_profile_label_is_held(self):
        hashes = self.stage()
        before = self._tree_bytes(self.root)
        with self.assertRaisesRegex(self.facts.RegistrationHeld,
                                    "fact_map_profile_mismatch"):
            self.build(self.mapping(hashes, profile="school_17_sheet_v1"))
        self.assertEqual(before, self._tree_bytes(self.root))
