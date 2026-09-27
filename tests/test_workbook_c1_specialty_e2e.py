"""End-to-end C1 specialty path on synthetic sources.

Builds a synthetic workbook with the same 30 SEO correction targets,
binds a synthetic C1 spec to its bytes, runs materialize ->
gg_excel_formula_patch under the real specialty_crops authorization path,
and checks the receipt chain (source sha -> spec sha -> map sha ->
output sha).  No real workbook bytes are used or shipped.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from tests._harness import ContractCase, bind_major
from tests.test_workbook_audit_evaluator import audit_module

ROOT = Path(__file__).resolve().parents[1]
AUDIT_CLI = ROOT / "skills/knuaf-doc/scripts/gg_workbook_audit.py"
PATCH_CLI = ROOT / "skills/knuaf-doc/scripts/gg_excel_formula_patch.py"

NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
S9 = "9 .경비계획"
S10 = "10. 감가상각비계획 "
S11 = "11. 생산원가계획"
S15 = "15.추정소득분석"


def build_workbook(path):
    """Write a minimal xlsx holding SEO's defective 30 target formulas."""
    formulas = {
        S11: {
            "D25": "SUM(C26:C27)", "E25": "SUM(C26:C27)",
            "F25": "SUM(C26:C27)", "G25": "SUM(C26:C27)",
            "D35": "D31", "E35": "E31+$C$38", "F35": "F31+$C$38",
            "G35": "G31+$C$38",
            "D30": "'9 .경비계획'!$C29", "E30": "'9 .경비계획'!$C29",
            "F30": "'9 .경비계획'!$C29", "G30": "'9 .경비계획'!$C29",
            "D29": "0", "E29": "0", "F29": "0", "G29": "0",
            "C16": "C17+C20+C25+C21+C29+C30",
            "D16": "D17+D20+D25+D21+D29+D30",
            "E16": "E17+E20+E25+E21+E29+E30",
            "F16": "F17+F20+F25+F21+F29+F30",
            "G16": "G17+G20+G25+G21+G29+G30",
            "C25": "SUM(C26:C27)", "C29": "'9 .경비계획'!$C28",
            "C30": "'9 .경비계획'!$C29", "C35": "C31+C32-C33-C34",
        },
        S15: {
            "F21": "'11. 생산원가계획'!C26", "H21": "'11. 생산원가계획'!E26",
            "I21": "'11. 생산원가계획'!F26", "H22": "'11. 생산원가계획'!E27",
            "I22": "'11. 생산원가계획'!F27",
            "F14": "'11. 생산원가계획'!C29", "G14": "'11. 생산원가계획'!C29",
            "H14": "'11. 생산원가계획'!C29", "I14": "'11. 생산원가계획'!C29",
            "E21": "'11. 생산원가계획'!C26", "G21": "'11. 생산원가계획'!E26",
            "E22": "'11. 생산원가계획'!C27", "F22": "'11. 생산원가계획'!D27",
            "G22": "'11. 생산원가계획'!E27",
        },
        S9: {},
        S10: {},
    }
    values = {
        S10: {"H20": "23000", "H32": "23000", "H44": "23000",
              "H56": "23000", "H8": "23000"},
        S9: {},
        S11: {"D26": "5", "D27": "5", "D28": "5",
              "E26": "5", "E27": "5", "E28": "5",
              "F26": "5", "F27": "5", "F28": "5",
              "G26": "5", "G27": "5", "G28": "5",
              "C26": "5", "C27": "5", "C28": "5",
              "D31": "5", "D32": "5", "D33": "5", "D34": "5",
              "E31": "5", "E32": "5", "E33": "5", "E34": "5",
              "F31": "5", "F32": "5", "F33": "5", "F34": "5",
              "G31": "5", "G32": "5", "G33": "5", "G34": "5",
              "C17": "5", "C20": "5", "C21": "5",
              "D17": "5", "D20": "5", "D21": "5",
              "E17": "5", "E20": "5", "E21": "5",
              "F17": "5", "F20": "5", "F21": "5",
              "G17": "5", "G20": "5", "G21": "5"},
        S15: {},
    }
    sheets = ["목록", S11, S15, S9, S10]
    book = (
        '<workbook ' + NS +
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships"><sheets>'
        + "".join('<sheet name="{0}" sheetId="{1}" r:id="rId{1}"/>'
                 .format(name.replace("&", "&amp;").replace("<", "&lt;"),
                         i + 1)
                 for i, name in enumerate(sheets))
        + "</sheets></workbook>")
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">'
            + "".join('<Relationship Id="rId{0}" Type="http://schemas.'
                      'openxmlformats.org/officeDocument/2006/relationships/'
                      'worksheet" Target="worksheets/sheet{0}.xml"/>'
                      .format(i + 1) for i in range(len(sheets)))
            + "</Relationships>")
    with ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        for i, name in enumerate(sheets):
            rows = {}
            for cell, text in formulas.get(name, {}).items():
                import re as _re
                row = int(_re.match(r"[A-Z]+([0-9]+)", cell).group(1))
                rows.setdefault(row, []).append(
                    '<c r="{0}"><f>{1}</f><v>0</v></c>'.format(cell, text))
            for cell, num in values.get(name, {}).items():
                import re as _re
                row = int(_re.match(r"[A-Z]+([0-9]+)", cell).group(1))
                rows.setdefault(row, []).append(
                    '<c r="{0}"><v>{1}</v></c>'.format(cell, num))
            body = "".join('<row r="{0}">{1}</row>'.format(
                r, "".join(cs)) for r, cs in sorted(rows.items()))
            z.writestr("xl/worksheets/sheet{0}.xml".format(i + 1),
                       "<worksheet " + NS + "><sheetData>" + body
                       + "</sheetData></worksheet>")


class SpecialtyChainTests(ContractCase):
    """P2: materialize -> patch --project --major specialty_crops -> receipt."""

    def run_cli(self, script, *args):
        result = subprocess.run([sys.executable, str(script), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True,
                                env=os.environ.copy(), check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result

    def assert_chain(self, source, spec_path, map_path, out, receipt_path,
                     project, expected_rows):
        self.run_cli(AUDIT_CLI, "materialize-c1", "--ref", "SEO", "--source",
                     source, "--spec", spec_path, "--out", map_path)
        persisted_map = json.loads(map_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted_map["source"]["sha256"],
                         hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(persisted_map["spec"]["sha256"],
                         hashlib.sha256(spec_path.read_bytes()).hexdigest())
        self.assertEqual(len(persisted_map["patches"]), expected_rows)
        self.run_cli(PATCH_CLI, "--source", source, "--map", map_path,
                     "--out", out, "--receipt", receipt_path,
                     "--project", project, "--major", "specialty_crops")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["source"]["sha256"],
                         persisted_map["source"]["sha256"])
        self.assertEqual(receipt["map"]["sha256"],
                         hashlib.sha256(map_path.read_bytes()).hexdigest())
        self.assertEqual(receipt["output"]["sha256"],
                         hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(receipt["majorAuthorization"]["major_id"],
                         "specialty_crops")
        self.assertEqual(len(receipt["patched"]), expected_rows)
        self.assertEqual({(p["sheet"], p["cell"]) for p in receipt["patched"]},
                         {(p["sheet"], p["cell"]) for p in
                          persisted_map["patches"]})
        return persisted_map, receipt

    def test_materialize_patch_receipt_chain_on_synthetic(self):
        a = audit_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "synthetic-specialty.xlsx"
            build_workbook(source)
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

            # Synthetic spec bound to the synthetic bytes, using SEO's
            # precise 30-row target set and original formula preconditions.
            shipped = json.loads(
                (Path(__file__).resolve().parents[1]
                 / "skills/knuaf-doc/references/common-workbooks"
                 "/corrections/c1-spec.json").read_text(encoding="utf-8"))
            spec = {
                "schema": "knuaf-c1-correction-spec/v1",
                "sources": {"SEO": {"source_id": "synthetic-c1-e2e",
                                    "sha256": source_sha}},
                "files": {"SEO": []},
            }
            spec["files"]["SEO"] = shipped["files"]["SEO"]
            spec_path = tmp / "syn-spec.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False),
                                 encoding="utf-8")
            map_path = tmp / "syn-map.json"
            project = self.make_project()
            bind_major(project, "specialty_crops")
            out = tmp / "patched.xlsx"
            receipt_path = tmp / "patch-receipt.json"
            map_doc, receipt = self.assert_chain(
                source, spec_path, map_path, out, receipt_path, project, 30)
            self.assertFalse(any("expected_value" in p for p in map_doc["patches"]))

            # the patched copy really carries the corrected formulas
            patched = a.Workbook.load(out)
            self.assertEqual(patched.formulas[(S11, "D25")].text,
                             "SUM(D26:D27)")
            self.assertNotIn((S10, "H20"), patched.formulas)
            self.assertEqual(patched.formulas[(S15, "I14")].text,
                             "'11. 생산원가계획'!G29")
            self.assertEqual(patched.formulas[(S11, "G16")].text,
                             "G17+G20+G25+G21+G29+G30+G28")

    def test_precondition_blocks_unbound_source(self):
        a = audit_module()
        shipped = a.c1_load_spec()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "synthetic.xlsx"
            build_workbook(source)
            with self.assertRaises(ValueError):
                a.c1_precondition_check(shipped, "X01", source)


if __name__ == "__main__":
    unittest.main()
