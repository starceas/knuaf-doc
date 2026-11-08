"""Fruit map consumer behavior with synthetic values; no school file or Office."""
import copy
import hashlib
import json
from unittest import mock

from openpyxl import Workbook, load_workbook

from tests._harness import ContractCase, REPO_ROOT, bind_major, runtime


REFERENCE = REPO_ROOT / "skills/knuaf-doc/references/fruit-trees"


class FruitExcelMapTests(ContractCase):
    def setUp(self):
        self.tpl = runtime("gg_excel_template")
        self.school = runtime("gg_school_excel")
        self.mapping = json.loads((REFERENCE / "fruit-finance-input-map.json").read_text())
        self.root = self.make_project()
        bind_major(self.root, "fruit_trees")
        mc = runtime("gg_major_contract")
        self.context = mc.output_context(self.root, "fruit_trees")

    def synthetic_source(self):
        book = Workbook()
        book.remove(book.active)
        sheets = self.school.profile_structure("fruit_16_sheet_v1")["sheets"]
        for name in sheets:
            book.create_sheet(name)
        for entry in self.mapping["entries"]:
            book[entry["sheet"]][entry["range"]] = "synthetic input" if entry["action"] == "clear" else "synthetic structure"
        # Formula and merge follower protection remain a writer responsibility.
        book[sheets[0]]["A100"] = "=1+2"
        book[sheets[0]].merge_cells("A101:B101")
        book[sheets[0]]["A101"] = "synthetic merged heading"
        book["_____data"].sheet_state = "hidden"
        source = self.root / "synthetic.xlsx"
        book.save(source)
        registry = json.loads(self.school.WORKBOOK_REFERENCE_SET.read_text())
        profile = next(p for p in registry["workbook_profiles"] if p["profile"] == "fruit_16_sheet_v1")
        profile["formula_fingerprints"] = {
            sheet.title: hashlib.sha256(json.dumps(sorted(
                (cell.coordinate, cell.value) for row in sheet for cell in row
                if cell.data_type == "f"), ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest() for sheet in book}
        book.close()
        registry_path = self.root / "synthetic-reference.json"
        registry_path.write_text(json.dumps(registry))
        patch = mock.patch.object(self.school, "WORKBOOK_REFERENCE_SET", registry_path)
        patch.start()
        self.addCleanup(patch.stop)
        mapping = copy.deepcopy(self.mapping)
        mapping["source"] = {"sha256": self.tpl.sha256(source)}
        return source, mapping

    def test_reviewed_map_clears_inputs_and_preserves_structure(self):
        source, mapping = self.synthetic_source()
        original = source.read_bytes()
        self.tpl._check_map_layout(mapping, source)
        path = self.root / "map.json"
        path.write_text(json.dumps(mapping))
        output = self.root / "blank.xlsx"
        receipt = self.tpl.blank_copy(source, path, output, context=self.context)
        book = load_workbook(output)
        for entry in self.mapping["entries"]:
            with self.subTest(sheet=entry["sheet"], cell=entry["range"]):
                expected = None if entry["action"] == "clear" else "synthetic structure"
                self.assertEqual(book[entry["sheet"]][entry["range"]].value, expected)
        self.assertEqual(book.worksheets[0]["A100"].value, "=1+2")
        self.assertIn("A101:B101", book.worksheets[0].merged_cells)
        self.assertEqual(book["_____data"].sheet_state, "hidden")
        self.assertEqual(receipt["missingCells"], [])
        self.assertEqual(source.read_bytes(), original)
        book.close()

    def test_wrong_source_hash_and_unreviewed_layout_refuse_output(self):
        source, mapping = self.synthetic_source()
        for changed, error in ((dict(mapping, source={"sha256": "0" * 64}), RuntimeError),
                               (dict(mapping, mapStatus="manual_map_required"), ValueError),
                               (dict(mapping, layout={"mapOrigin": "reviewed_custom", "variant": "x01"}), ValueError)):
            with self.subTest(change=changed.get("mapStatus"), layout=changed.get("layout")):
                path = self.root / "bad-map.json"
                path.write_text(json.dumps(changed))
                output = self.root / "refused.xlsx"
                with self.assertRaises(error):
                    self.tpl.blank_copy(source, path, output, context=self.context)
                self.assertFalse(output.exists())

    def test_map_is_structure_only_and_protects_reference_regions(self):
        self.assertEqual(self.mapping["workbook_profile"], "fruit_16_sheet_v1")
        allowed = {"sheet", "range", "role", "action", "explicit", "reason"}
        cells = set()
        for entry in self.mapping["entries"]:
            self.assertEqual(set(entry), allowed)
            self.assertIn(entry["action"], {"clear", "preserve"})
            self.assertTrue(entry["explicit"])
            self.assertTrue(entry["reason"].strip())
            key = entry["sheet"], entry["range"]
            self.assertNotIn(key, cells)
            cells.add(key)
            if entry["sheet"] == "_____data":
                self.assertEqual(entry["action"], "preserve")
        actions = {(e["sheet"], e["range"]): e["action"] for e in self.mapping["entries"]}
        for key in (("1.재무상태", "F8"), ("1.재무상태", "N43"),
                    ("2.영농목표", "N28"), ("9.경비계획", "U6"),
                    ("12.손익계획", "C22")):
            self.assertEqual(actions[key], "clear")
        for key in (("1.재무상태", "B6"), ("1.재무상태", "AL46"),
                    ("9.경비계획", "B12"), ("9.경비계획", "O15")):
            self.assertEqual(actions[key], "preserve")

    def test_fact_draft_cannot_be_mistaken_for_native_registration(self):
        mapping = json.loads((REFERENCE / "fruit-finance-fact-map.draft.json").read_text())
        self.assertNotEqual(mapping["schema"], runtime("gg_excel_facts").MAP_SCHEMA)
        self.assertNotIn("workbook", mapping)
        self.assertNotIn("receipt", mapping)
        cells = set()
        facts = set()
        for row in mapping["cells"]:
            self.assertNotIn("value", row)
            self.assertNotIn((row["sheet"], row["cell"]), cells)
            self.assertNotIn(row["fact_id"], facts)
            cells.add((row["sheet"], row["cell"]))
            facts.add(row["fact_id"])
            self.assertEqual(row["unit"], "천원")
            self.assertEqual(row["status"], "held_pending_native_and_formula_review")
        first = {row["field_id"]: row for row in mapping["cells"] if row["period"] == "2027"}
        self.assertEqual(first["sales.net_profit"]["cell"], "C34")
        self.assertEqual(first["finance.agricultural_income"]["cell"], "E26")
        self.assertNotEqual(first["sales.net_profit"]["sheet"], first["finance.agricultural_income"]["sheet"])
