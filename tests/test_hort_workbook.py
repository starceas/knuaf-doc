"""I1 transformer tests use only generated public fixture bytes."""
from __future__ import annotations

import copy
from decimal import Decimal
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
sys.path.insert(0, str(ROOT / "tests/fixtures/hort_finance"))
import gg_hort_workbook as h
from synthetic import build, entries

TMP = ROOT / ".hw-work/fin/lanes/I1-TRANSFORMER/tmp"


class Transformer(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xlsx"
        self.roster = h._json(h.REF / "profile.json")["workbook_reference"]["sheets"]
        build(self.path, self.roster)
        self.patch = mock.patch.object(h, "SOURCE_SHA", h.file_digest(self.path))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.map = {"entries": entries(self.roster[0])}
        self.params = {"num": {"data_type": "decimal", "unit_code": "KRW_1000"},
                       "zero": {"data_type": "decimal", "unit_code": "KRW_1000"}}

    def test_transition_matrix_sparse_shared_style_and_zip(self):
        payload, counts = h._zip_transform(self.path, self.map,
                                            {"num": Decimal("7"), "zero": 0},
                                            self.params)
        self.assertEqual(counts["shared_groups"], 1)
        self.assertEqual(counts["created_cells"], 2)
        with zipfile.ZipFile(self.path) as before, zipfile.ZipFile(__import__("io").BytesIO(payload)) as after:
            self.assertNotIn("xl/calcChain.xml", after.namelist())
            self.assertEqual(before.read("xl/media/opaque.bin"), after.read("xl/media/opaque.bin"))
            last = ET.fromstring(after.read("xl/worksheets/sheet18.xml"))
            protected = next(c for c in last.iter("{%s}c" % h.M) if c.get("r") == "D10")
            self.assertEqual(protected.find("{%s}f" % h.M).get("ref"), "D10:E10")
            self.assertIsNone(protected.find("{%s}v" % h.M))
            root = ET.fromstring(after.read("xl/worksheets/sheet1.xml"))
            cells = {c.get("r"): c for c in root.iter("{%s}c" % h.M)}
            self.assertEqual([c.get("r") for c in root.iter("{%s}c" % h.M) if c.get("r", "").endswith("1")],
                             ["A1", "C1", "D1", "E1", "F1", "G1", "H1", "I1", "J1", "K1"])
            self.assertEqual(cells["D1"].find("{%s}v" % h.M).text, "7")
            self.assertEqual(cells["F1"].find("{%s}v" % h.M).text, "0")
            self.assertIsNone(cells["A1"].find("{%s}v" % h.M))
            self.assertIsNone(cells["I1"].find("{%s}v" % h.M))
            self.assertIsNone(cells["K1"].find("{%s}f" % h.M))
            self.assertEqual(cells["J1"].find("{%s}v" % h.M).text, "99")
            self.assertEqual(cells["B2"].get("t"), "inlineStr")
            self.assertNotEqual(cells["F1"].get("s"), "0")
            rels = after.read("xl/_rels/workbook.xml.rels")
            self.assertNotIn(b"calcChain", rels)
            workbook = ET.fromstring(after.read("xl/workbook.xml"))
            self.assertEqual(workbook.find("{%s}calcPr" % h.M).get("fullCalcOnLoad"), "1")

    def test_before_state_mismatch_refuses(self):
        bad = copy.deepcopy(self.map)
        bad["entries"][0]["before_state"] = "blank_node"
        with self.assertRaisesRegex(h.Held, "before_state_mismatch"):
            h._zip_transform(self.path, bad, {"num": 7, "zero": 0}, self.params)

    def test_merged_nonanchor_refuses(self):
        bad = copy.deepcopy(self.map)
        bad["entries"].append({"sheet": self.roster[0], "cell": "C5", "action": "clear",
                               "before_state": "absent"})
        with self.assertRaisesRegex(h.Held, "merged_nonanchor_target"):
            h._zip_transform(self.path, bad, {"num": 7, "zero": 0}, self.params)

    def test_array_range_refuses(self):
        bad = copy.deepcopy(self.map)
        bad["entries"].append({"sheet": self.roster[-1], "cell": "E10", "action": "clear",
                               "before_state": "array_member"})
        with self.assertRaisesRegex(h.Held, "array_target"):
            h._zip_transform(self.path, bad, {"num": 7, "zero": 0}, self.params)

    def test_source_hash_refuses(self):
        with mock.patch.object(h, "SOURCE_SHA", "0" * 64):
            with self.assertRaisesRegex(h.Held, "source_hash_mismatch"):
                h._preflight(self.path, self.map)


class FactContract(unittest.TestCase):
    def setUp(self):
        self.spec = {"canonical_cell": "Synthetic!A1", "data_type": "decimal",
                     "unit_code": "KRW_1000", "allowed_answer_states":
                     ["provided", "unknown", "not_asked", "explicit_none"],
                     "explicit_none_calculation": 0, "allowed_range": {"min": 0, "max": 10},
                     "required_when": "always"}

    def test_explicit_none_derivation_receipt_preserves_source(self):
        source = {"id": "f1", "field_id": "hort_env_systems.fin.amount",
                  "answer_state": "explicit_none", "value": None, "revision": 4,
                  "reason": "synthetic absent", "source_refs": [{"id": "s1", "locator": "synthetic"}]}
        project = {"facts": {"f1": source}}
        values, derivations, _ = h._facts(project, {"amount": self.spec})
        self.assertEqual(values["amount"], 0)
        self.assertIsNone(source["value"])
        self.assertEqual(source["answer_state"], "explicit_none")
        self.assertEqual(derivations[0]["rule_id"], h.ZERO_RULE)
        self.assertEqual(derivations[0]["target_cell"], "Synthetic!A1")

    def test_unknown_and_range_are_held(self):
        source = {"field_id": "hort_env_systems.fin.amount", "answer_state": "unknown",
                  "value": None}
        with self.assertRaisesRegex(h.Held, "answer_state_held"):
            h._facts({"facts": {"f1": source}}, {"amount": self.spec})
        source.update(answer_state="provided", value="11")
        with self.assertRaisesRegex(h.Held, "fact_range_invalid"):
            h._facts({"facts": {"f1": source}}, {"amount": self.spec})


class VerificationPrimitives(unittest.TestCase):
    def test_pipeline_ready_still_fail_closed(self):
        self.assertTrue(h.PIPELINE_READY)
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "receipt.json"
            bad.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(h.Held, "transform_receipt_invalid"):
                h.check_transform_receipt(bad)
        for call in (lambda: h.plan("unused", "unused", str(Path("unused") / "p.json")),
                     lambda: h.transform("unused", "unused", "unused",
                                         "unused", "unused")):
            with self.assertRaises((h.Held, OSError, KeyError, ValueError)):
                call()

    def test_raw_cache_tolerance_and_missing_interest_counterexample(self):
        self.assertEqual(h.compare_raw_cache("0.5", "0.5009", "KRW_1000"), Decimal("0.0009"))
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.compare_raw_cache(Decimal(10) * Decimal("0.05"), 0, "KRW_1000")
        with self.assertRaisesRegex(h.Held, "cache_numeric_invalid"):
            h.compare_raw_cache("0.5", None, "KRW_1000")
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.compare_raw_cache("0.5", "0.500000002", "RATIO")
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.compare_raw_cache(5, "5.0001", "COUNT")

    def test_accounting_identity_exact(self):
        self.assertTrue(h.check_identity("10.0", 10))
        with self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
            h.check_identity("10", "9.999")

    def test_imported_common_helpers_signature_and_behavior(self):
        expected = {h.workbook_sheets: ("z",), h.load_shared: ("z",),
                    h.merged_cells: ("root",), h.expand_ref: ("ref",),
                    h._serialize_dom: ("xml_bytes", "source_name")}
        for fn, names in expected.items():
            self.assertEqual(tuple(inspect.signature(fn).parameters), names)
        self.assertEqual(h.expand_ref("B2:C3"), ["B2", "C2", "B3", "C3"])
        raw = ('<worksheet xmlns="'+h.M+'"><sheetData/><mergeCells count="1">'
               '<mergeCell ref="B5:C5"/></mergeCells></worksheet>').encode()
        dom = h._serialize_dom(raw, "synthetic.xml")
        anchors, nonanchors = h.merged_cells(ET.fromstring(dom.toxml()))
        self.assertEqual((anchors, nonanchors), ({"B5"}, {"C5"}))


if __name__ == "__main__":
    unittest.main()
