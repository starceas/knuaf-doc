"""Tiny public-only OOXML fixture for the H01 transformer regression suite."""
from __future__ import annotations

from pathlib import Path
import zipfile

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
C = "http://schemas.openxmlformats.org/package/2006/content-types"


def build(path: Path, roster: list[str]):
    if len(roster) != 18:
        raise ValueError("fixture requires eighteen sheet names")
    sheets = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
                     for i, name in enumerate(roster, 1))
    workbook = (f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{sheets}</sheets>'
                '<calcPr calcId="1"/></workbook>')
    rels = "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" '
                   f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, 19))
    rels += f'<Relationship Id="rId19" Type="{R}/calcChain" Target="calcChain.xml"/>'
    relationships = f'<Relationships xmlns="{P}">{rels}</Relationships>'
    types = (f'<Types xmlns="{C}"><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/calcChain.xml" '
             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
             '</Types>')
    styles = (f'<styleSheet xmlns="{M}"><numFmts count="0"/><cellXfs count="1">'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '</cellXfs></styleSheet>')
    shared = f'<sst xmlns="{M}" count="1" uniqueCount="1"><si><t>fixture</t></si></sst>'
    for_sheet_one = (f'<worksheet xmlns="{M}"><sheetData>'
                     '<row r="1"><c r="A1"><v>3</v></c><c r="C1" t="s"><v>0</v></c>'
                     '<c r="D1"><f>1+1</f><v>2</v></c><c r="E1"><f>2+2</f><v>4</v></c>'
                     '<c r="H1"><v>8</v></c>'
                     '<c r="I1"><f t="shared" si="1" ref="I1:K1">A1+1</f><v>4</v></c>'
                     '<c r="J1"><v>99</v></c><c r="K1"><f t="shared" si="1"/><v>5</v></c></row>'
                     '<row r="2"><c r="B2" s="0"/></row>'
                     '<row r="5"><c r="B5" t="inlineStr"><is><t>merged</t></is></c></row>'
                     '</sheetData><mergeCells count="1"><mergeCell ref="B5:C5"/></mergeCells></worksheet>')
    final = (f'<worksheet xmlns="{M}"><sheetData><row r="10">'
             '<c r="D10"><f t="array" ref="D10:E10">SUM(A1:A2)</f><v>1</v></c>'
             '<c r="E10"><v>1</v></c></row></sheetData></worksheet>')
    empty = f'<worksheet xmlns="{M}"><sheetData/></worksheet>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", relationships)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/calcChain.xml", f'<calcChain xmlns="{M}"/>')
        for i in range(1, 19):
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       for_sheet_one if i == 1 else final if i == 18 else empty)
        z.writestr("xl/media/opaque.bin", b"synthetic opaque member")


def entries(sheet):
    return [
        {"sheet": sheet, "cell": "A1", "action": "formula", "before_state": "number", "after_formula": "=2+3"},
        {"sheet": sheet, "cell": "B2", "action": "label_template", "before_state": "blank_node", "after_value": "draft"},
        {"sheet": sheet, "cell": "C1", "action": "formula", "before_state": "shared_string", "after_formula": "=A1+1"},
        {"sheet": sheet, "cell": "D1", "action": "input_param", "before_state": "formula_normal", "param_key": "num"},
        {"sheet": sheet, "cell": "E1", "action": "clear", "before_state": "formula_normal"},
        {"sheet": sheet, "cell": "F1", "action": "input_param", "before_state": "absent", "param_key": "zero",
         "number_format": "#,##0;-#,##0;;@"},
        {"sheet": sheet, "cell": "G1", "action": "label_template", "before_state": "absent", "after_value": "new"},
        {"sheet": sheet, "cell": "H1", "action": "clear", "before_state": "number"},
        {"sheet": sheet, "cell": "I1", "action": "formula", "before_state": "formula_shared_anchor", "after_formula": "=A1+4"},
        {"sheet": sheet, "cell": "K1", "action": "clear", "before_state": "formula_shared_member"},
    ]
