"""Horticulture workbooks enter fact registration through their own chain.

The 18-sheet workbook declares no generic workbook profile: its numbers are
accepted only from a native Excel manifest bound to a verified transform
receipt.  Every fixture here is synthetic (no student workbook, no Office
engine); the native manifest is written by the fixture, not by Excel.
"""
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

from tests._harness import ContractCase, runtime

PROFILE = "hort_18_sheet_reconstructed_v1"
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

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
        hashes = self.stage()
        mapping = self.mapping(hashes)
        before = self._tree_bytes(self.root)
        change = self.build(mapping)
        self.assertEqual(before, self._tree_bytes(self.root))
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

    def test_native_receipt_with_failure_flags_is_held(self):
        # The shared profile check refuses a receipt that records an engine
        # error, a nonzero exit or stderr; the horticulture chain must keep
        # that refusal instead of trusting the success markers on top of it.
        original = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        cases = {"error": "synthetic conversion failure",
                 "returncode": 1,
                 "engine_stderr": "synthetic Excel failure"}
        for field, value in cases.items():
            native = dict(original, **{field: value})
            hashes = self.stage(native=native)
            with self.subTest(field=field):
                held = self._held(lambda: self.build(self.mapping(hashes)))
                self.assertEqual(held.reason, "hort_transform_receipt_invalid")
                self.assertIn("failed run", str(held))

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

    def _held(self, build):
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            build()
        self.assertEqual(before, self._tree_bytes(self.root))
        return held.exception

    def test_native_authorization_must_match_the_current_grant(self):
        # The dedicated chain is the replacement for require_workbook_profile,
        # so it must reject a receipt whose recorded grant is absent or stale.
        original = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        cases = {"missing": None,
                 "other_module": dict(original["major_authorization"][0],
                                      module_version="0.0.0")}
        for label, recorded in cases.items():
            native = dict(original)
            if recorded is None:
                native.pop("major_authorization")
            else:
                native["major_authorization"] = [recorded]
            hashes = self.stage(native=native)
            with self.subTest(label=label):
                held = self._held(lambda: self.build(self.mapping(hashes)))
                self.assertEqual(held.reason, "hort_transform_receipt_invalid")

    def test_horticulture_artifacts_cannot_register_under_another_major(self):
        hashes = self.stage()
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.facts.build_registration(
                self.root, "recalc.xlsx", "native.json", self.mapping(hashes),
                source_id="excel-plan",
                context=self.mc.output_context(self.root, "fruit_trees"))
        self.assertEqual(held.exception.reason, "major_binding_mismatch")

    def test_receipt_from_another_project_cannot_stand_in(self):
        # A genuine receipt issued by another project's transform run, with
        # its own correct hash — not a corrupted copy of this one.
        from tests.test_hort_finance_pipeline import Chain
        foreign = Chain(methodName="test_full_chain_plan_transform_native_verify")
        foreign.setUp()
        self.addCleanup(foreign.doCleanups)
        foreign._run_to_transform()
        native = json.loads(self.chain.native_rec.read_text(encoding="utf-8"))
        binding = dict(native["validation"]["template_receipt"])
        transform = str(foreign.transform_rec.resolve())
        binding.update(path=transform,
                       sha256=self.core.digest(Path(transform).read_bytes()),
                       input_sha256=self.core.digest(foreign.out.read_bytes()))
        native["validation"] = dict(native["validation"],
                                    template_receipt=binding,
                                    previous_receipt_sha256=binding["sha256"])
        native["input_file"] = str(foreign.out.resolve())
        native["input_sha256"] = binding["input_sha256"]
        hashes = self.stage(native=native)
        held = self._held(lambda: self.build(self.mapping(hashes)))
        self.assertEqual(held.reason, "hort_transform_receipt_invalid")
        self.assertIn("project mismatch", str(held.__cause__))

    def test_valid_receipt_cannot_bless_a_tampered_workbook(self):
        # The map hash is refreshed to the altered file, so the front-door
        # hash check passes and only the receipt/output binding can refuse.
        hashes = self.stage()
        workbook = self.root / "recalc.xlsx"
        with zipfile.ZipFile(workbook) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        sheet = "xl/worksheets/sheet1.xml"
        xml = ET.fromstring(parts[sheet])
        xml.find(f".//{{{NS}}}c[@r='A1']/{{{NS}}}v").text = "999"
        parts[sheet] = ET.tostring(xml)
        with zipfile.ZipFile(workbook, "w") as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        mapping = self.mapping(hashes)
        mapping["workbook"]["sha256"] = self.core.digest(workbook.read_bytes())
        held = self._held(lambda: self.build(mapping))
        self.assertEqual(held.reason, "hort_transform_receipt_invalid")
        self.assertIn("native_receipt_stale", str(held.__cause__))

    def test_absent_transform_receipt_file_is_held(self):
        hashes = self.stage()
        self.chain.transform_rec.unlink()
        held = self._held(lambda: self.build(self.mapping(hashes)))
        self.assertEqual(held.reason, "hort_transform_receipt_invalid")

    def test_missing_finance_capability_still_holds_the_chain(self):
        # The dedicated chain ends in require_finance_profile; a module that
        # withdraws the capability must not register through it.
        from dataclasses import replace
        from unittest import mock
        registry = self.mc.default_registry()
        module = registry.resolve("hort_env_systems")
        modules = {major: registry.resolve(major) for major in registry.major_ids}
        faulty = replace(module, finance_capabilities=tuple(
            replace(cap, status="unsupported") if cap.profile == PROFILE else cap
            for cap in module.finance_capabilities))
        for patch in (mock.patch.object(registry, "resolve",
                                        side_effect=lambda major:
                                        faulty if major == "hort_env_systems"
                                        else modules[major]),
                      mock.patch.object(self.mc, "default_registry",
                                        return_value=registry)):
            patch.start()
            self.addCleanup(patch.stop)
        hashes = self.stage()
        held = self._held(lambda: self.build(self.mapping(hashes)))
        self.assertEqual(held.reason, "unsupported_finance_profile")
