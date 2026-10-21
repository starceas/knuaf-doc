"""WP-3 regression: formula removal drops the stale calcChain part."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from tests._harness import ContractCase, bind_major, runtime
from tests.test_template_wage_link import fixture as wage_fixture

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills" / "knuaf-doc" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def inject_calcchain(path):
    with zipfile.ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    parts["xl/calcChain.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<c r="X27" i="9"/><c r="Z28" i="9"/></calcChain>').encode("utf-8")
    content_types = parts["[Content_Types].xml"].decode("utf-8")
    parts["[Content_Types].xml"] = content_types.replace(
        "</Types>",
        '<Override PartName="/xl/calcChain.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
        "</Types>").encode("utf-8")
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    parts["xl/_rels/workbook.xml.rels"] = rels.replace(
        "</Relationships>",
        '<Relationship Id="rIdCalcChain" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/calcChain" Target="calcChain.xml"/>'
        "</Relationships>").encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in parts.items():
            target.writestr(name, data)


class CalcChainTests(ContractCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "src.xlsx"
        wage_fixture(self.src)
        inject_calcchain(self.src)
        self.patch = runtime("gg_excel_formula_patch")
        project = self.make_project()
        bind_major(project)
        self.context = runtime("gg_major_contract").output_context(
            project, "specialty_crops")

    def apply(self, data, name):
        out = self.root / name
        map_path = self.root / (name + ".map.json")
        map_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        receipt = self.patch.patch_copy(self.src, map_path, out, context=self.context)
        return out, receipt

    def test_formula_clear_removes_calcchain_part_and_references(self):
        plan = self.patch.materialize_d8(self.src, prepare_observations=True)
        out, receipt = self.apply(plan, "cleared.xlsx")
        self.assertTrue(receipt.get("calcChainRemoved"))
        with zipfile.ZipFile(out) as archive:
            names = set(archive.namelist())
            self.assertNotIn("xl/calcChain.xml", names)
            self.assertNotIn("/calcChain", archive.read("[Content_Types].xml").decode())
            self.assertNotIn("/calcChain", archive.read("xl/_rels/workbook.xml.rels").decode())
            workbook = archive.read("xl/workbook.xml").decode()
            self.assertIn('fullCalcOnLoad="1"', workbook)
        self.assertEqual(
            sum(p["operation"] == "prepare_wage_input" for p in receipt["patched"]), 6)

    def test_formula_replace_keeps_calcchain(self):
        plan = self.patch.materialize_d8(self.src)
        out, receipt = self.apply(plan, "replaced.xlsx")
        self.assertNotIn("calcChainRemoved", receipt)
        with zipfile.ZipFile(out) as archive:
            self.assertIn("xl/calcChain.xml", archive.namelist())


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
