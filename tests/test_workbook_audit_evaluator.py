"""Independent, hand-computed XLSX fixtures for the D6 evaluator."""
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/knuaf-doc/scripts/gg_workbook_audit.py"


def audit_module():
    spec = importlib.util.spec_from_file_location("gg_workbook_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(path):
    """Two tiny sheets; every expected result below is computed without the tool."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    a = f'''<worksheet {ns}><sheetData>
      <row r="1"><c r="A1"><v>100</v></c><c r="B1"><v>5</v></c>
        <c r="C1"><f>A1+B1*2</f><v>110</v></c>
        <c r="D1"><f>-B1+10%</f><v>-4.9</v></c>
        <c r="E1"><f>SUM(A1:B1)</f><v>105</v></c>
        <c r="F1"><f>AVERAGE(A1:B1)</f><v>52.5</v></c>
        <c r="G1"><f>SUM(A2:B2)</f><v>0</v></c>
        <c r="H1"><f>AVERAGE(A2:B2)</f><v>#DIV/0!</v></c>
        <c r="I1"><f>B1&gt;3</f><v>1</v></c>
        <c r="J1" t="e"><f>#N/A+1</f><v>#N/A</v></c></row>
      <row r="2"><c r="C2"><f>Other!A1+A1</f><v>107</v></c>
        <c r="D2"><f>1/0</f><v>#DIV/0!</v></c>
        <c r="E2"><f>D2+1</f><v>#DIV/0!</v></c>
        <c r="F2"><f>PMT(10%,2,100)</f><v>-57.6190476190476</v></c>
        <c r="G2"><f t="shared" si="0" ref="G2:I2">A1+B1</f><v>105</v></c>
        <c r="H2"><f t="shared" si="0"/><v>115</v></c>
        <c r="I2"><f>42</f><v>42</v></c>
        <c r="K2"><f>SUM(Other!A1:A1)</f><v>7</v></c></row>
      <row r="3"><c r="A3"><f>A2</f><v>0</v></c>
        <c r="G3"><f t="shared" si="1" ref="G3:I3">A1+B1</f><v>105</v></c>
        <c r="I3"><f t="shared" si="1"/><v>105.1</v></c></row>
      <row r="4"><c r="B4"><v>10</v></c>
        <c r="C4"><f>SUM(A4,B4)</f><v>10</v></c>
        <c r="D4"><f>SUM(A4:B4)</f><v>10</v></c>
        <c r="E4"><f>AVERAGE(A4,B4)</f><v>10</v></c>
        <c r="F4"><f>AVERAGE(A4:B4)</f><v>10</v></c>
        <c r="G4"><f>A4+B4</f><v>10</v></c>
        <c r="H4"><f>A4=0</f><v>1</v></c>
        <c r="I4"><f>A4</f><v>0</v></c>
        <c r="J4"><f>AVERAGE(A4+0,B4)</f><v>5</v></c></row>
    </sheetData></worksheet>'''
    b = f'''<worksheet {ns}><sheetData><row r="1"><c r="A1"><v>7</v></c></row></sheetData></worksheet>'''
    book = f'''<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Main" sheetId="1" r:id="rId1"/><sheet name="Other" sheetId="2" r:id="rId2"/></sheets></workbook>'''
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'''
    with ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", a)
        z.writestr("xl/worksheets/sheet2.xml", b)


class EvaluatorFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xlsx"
        fixture(self.path)
        self.a = audit_module()
        self.book = self.a.Workbook.load(self.path)

    def test_arithmetic_percent_comparison_and_cross_sheet(self):
        expected = {"C1": 110, "D1": -4.9, "I1": True, "C2": 107}
        for cell, value in expected.items():
            self.assertAlmostEqual(self.book.evaluate("Main", cell), value)

    def test_aggregates_blank_and_typed_errors(self):
        for cell, value in {"E1": 105, "F1": 52.5, "G1": 0}.items():
            self.assertAlmostEqual(self.book.evaluate("Main", cell), value)
        for cell in ("H1", "D2", "E2"):
            self.assertEqual(self.book.evaluate("Main", cell), "#DIV/0!")
        self.assertEqual(self.book.evaluate("Main", "J1"), "#N/A")

    def test_blank_scalar_reference_range_arithmetic_and_comparison(self):
        # Excel ignores the empty A4 reference in either AVERAGE form, while
        # A4+0 supplies a numeric zero to AVERAGE and changes its denominator.
        expected = {"C4":10,"D4":10,"E4":10,"F4":10,"G4":10,
                    "H4":True,"I4":0,"J4":5}
        for cell, value in expected.items():
            self.assertEqual(self.book.evaluate("Main",cell), value, cell)

    def test_empty_a1_scalar_reference_fixture(self):
        b = self.a.Workbook()
        b.sheets = ["Blank"]
        b.values["Blank","A2"] = 10
        formulas = {"B1":"SUM(A1,A2)","B2":"AVERAGE(A1,A2)",
                    "B3":"A1+A2","B4":"A1=0","B5":"A1"}
        for cell, formula in formulas.items():
            b.formulas["Blank",cell] = self.a.FORMULA(formula,None,None)
        for cell, expected in {"B1":10,"B2":10,"B3":10,"B4":True,"B5":0}.items():
            self.assertEqual(b.evaluate("Blank",cell), expected, cell)

    def test_empty_a1_range_fixture(self):
        b = self.a.Workbook()
        b.sheets = ["Blank"]
        b.values["Blank","A2"] = 10
        for cell, formula in {"B1":"SUM(A1:A2)",
                              "B2":"AVERAGE(A1:A2)"}.items():
            b.formulas["Blank",cell] = self.a.FORMULA(formula,None,None)
        self.assertEqual(b.evaluate("Blank","B1"),10)
        self.assertEqual(b.evaluate("Blank","B2"),10)

    def test_pmt_excel_order(self):
        self.assertTrue(math.isclose(self.book.evaluate("Main", "F2"),
                                     -57.6190476190476, rel_tol=1e-12))

    def test_shared_member_and_own_formula_override(self):
        self.assertEqual(self.book.formulas[("Main", "H2")].text, "B1+C1")
        self.assertEqual(self.book.formulas[("Main", "H2")].anchor, "G2")
        self.assertEqual(self.book.formulas[("Main", "I2")].text, "42")
        self.assertIsNone(self.book.formulas[("Main", "I2")].anchor)
        self.assertEqual(self.book.evaluate("Main", "H2"), 115)
        self.assertEqual(self.book.evaluate("Main", "I2"), 42)
        self.assertEqual(self.book.evaluate("Main", "K2"), 7)
        self.assertNotIn(("Main", "H3"), self.book.formulas)
        self.assertEqual(self.book.formulas[("Main", "I3")].text, "C1+D1")
        self.assertAlmostEqual(self.book.evaluate("Main", "I3"), 105.1)

    def test_cache_gate_and_unsupported_are_separate(self):
        self.assertEqual(self.book.evaluate("Main", "A3"), 0)
        entries, coverage = self.a.inventory(self.book)
        by_cell = {x["cell"]:x for x in entries if x["sheet"] == "Main"}
        self.assertEqual(by_cell["E1"]["ranges"][0]["scope"], "same_sheet")
        self.assertEqual(by_cell["K2"]["ranges"][0]["scope"], "cross_sheet")
        self.assertEqual(coverage["structural_formula_count"],
                         coverage["evaluator_supported_count"])
        self.assertEqual(coverage["structural_formula_count"],
                         coverage["cache_agreement_count"])
        self.book.formulas["Main", "K1"] = self.a.FORMULA("FOO(A1)", None, 0)
        self.book.formulas["Main", "L1"] = self.a.FORMULA("A1+1", None, 999)
        _, coverage = self.a.inventory(self.book)
        self.assertEqual(len(coverage["unsupported_syntax"]), 1)
        self.assertEqual(len(coverage["cache_disagreements"]), 1)
        self.assertNotIn("cached_value", coverage["cache_disagreements"][0])
        _, local = self.a.inventory(self.book, include_cache_values=True)
        self.assertEqual(local["cache_disagreements"][0]["cached_value"], 999)
        self.assertEqual(local["cache_disagreements"][0]["evaluated_value"], 101)
        self.assertEqual(coverage["evaluator_supported_count"] + 1,
                         coverage["structural_formula_count"])


if __name__ == "__main__":
    unittest.main()
