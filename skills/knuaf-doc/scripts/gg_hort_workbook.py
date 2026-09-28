#!/usr/bin/env python3
"""Source-bound H01 finance transformation. No Excel or PDF engine runs here.

The public map contains no teaching-cell values. Student facts and receipts are
private runtime inputs; a failed preflight never writes an output workbook.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import posixpath
import re
from pathlib import Path
import sys
import zipfile
from xml.dom import Node
from xml.etree import ElementTree as ET

from gg_excel_template import (workbook_sheets, load_shared, merged_cells,
                               expand_ref, _serialize_dom, local, text_value)

HERE = Path(__file__).resolve().parent
REF = HERE.parent / "references" / "hort-env-systems"
FILES = ("finance-transform.json", "finance-params.json", "finance-verify-classes.json")
SOURCE_SHA = "e3c9defe376fa74413641408900f6bb656017a389fb9745d9dac221a10951c1c"
_H01_SHA = SOURCE_SHA
PROFILE = "hort_18_sheet_reconstructed_v1"
SCHEMA = "knuaf-hort-finance-receipt/v1"
M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
C = "http://schemas.openxmlformats.org/package/2006/content-types"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
CELL = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
# Lane-lead ruling (I3D): the two embedded textbook pictures on this sheet
# are teaching examples and are stripped from the student workbook; the map
# writes this label at each removed anchor's origin cell instead.
_TEACHER_IMAGE_SHEET = "6.시설계획"
_TEACHER_IMAGE_LABEL = "학생 시설 도면·견적서 첨부"
ZERO_RULE = "HFIN-V3-EXPLICIT-NONE-ZERO-1"
BLANK_RULE = "HFIN-V3-EXPLICIT-NONE-BLANK-1"
CATEGORY_RULE = "HFIN-V3-CATEGORY-ABSENCE-ZERO-1"
# Disposed by the lane lead: coordinate registry, machine predicates and the
# area-label formula are implemented in this file and the public contracts.
PIPELINE_READY = True

_TRUE_COND = {"all": []}
_FALSE_COND = {"any": []}


class Held(ValueError):
    """Safe, machine-readable refusal before publication."""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def contracts(base=REF):
    base = Path(base)
    docs = {name: _json(base / name) for name in FILES}
    expected = ("knuaf-hort-finance-transform/v2", "knuaf-hort-finance-params/v2",
                "knuaf-hort-finance-verify-classes/v2")
    for name, schema in zip(FILES, expected):
        doc = docs[name]
        if doc.get("schema") != schema or doc.get("source_sha256") != SOURCE_SHA:
            raise Held("public_contract_invalid")
    tr, params, verify_classes = docs[FILES[0]], docs[FILES[1]], docs[FILES[2]]
    entries = tr.get("entries")
    registry = verify_classes.get("registry")
    if (not isinstance(entries, list) or tr.get("entry_count") != 4103
            or len(entries) != 4103 or len({(e.get("sheet"), e.get("cell")) for e in entries}) != 4103
            or params.get("param_count") != 1379
            or len(params.get("parameters", {})) != 1379
            or not isinstance(registry, list)
            or verify_classes.get("registry_count") != 5466
            or len(registry) != 5466
            or len({(r.get("sheet"), r.get("cell")) for r in registry}) != 5466):
        raise Held("public_contract_invalid")
    return docs


def _direct(parent, name):
    return next((n for n in parent.childNodes if n.nodeType == Node.ELEMENT_NODE
                 and n.namespaceURI == M and n.localName == name), None)


def _children(parent, name):
    return [n for n in parent.childNodes if n.nodeType == Node.ELEMENT_NODE
            and n.namespaceURI == M and n.localName == name]


def _node(doc, name):
    return doc.createElementNS(M, name)


def _text(parent):
    return "".join(n.data for n in parent.childNodes
                   if n.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE))


def _cell_state(cell, array=False):
    if array:
        return "array_member"
    if cell is None:
        return "absent"
    formula = _direct(cell, "f")
    value = _direct(cell, "v")
    inline = _direct(cell, "is")
    if formula is not None:
        if formula.getAttribute("t") == "shared":
            return "formula_shared_anchor" if _text(formula).strip() else "formula_shared_member"
        if formula.getAttribute("t") in ("", "normal"):
            return "formula_normal"
        raise Held("unsupported_source_formula")
    if inline is not None or cell.getAttribute("t") == "inlineStr":
        return "inline_string"
    if value is None:
        return "blank_node"
    return {"s": "shared_string", "b": "bool", "e": "error",
            "": "number", "n": "number"}.get(cell.getAttribute("t"), "unsupported")


def _cells(doc):
    return {c.getAttribute("r"): c for c in doc.getElementsByTagNameNS(M, "c")}


def _sheet_structure(raw, path):
    dom = _serialize_dom(raw, path)
    root = ET.fromstring(raw)
    _, nonanchors = merged_cells(root)
    arrays = set()
    for cell in root.iter("{%s}c" % M):
        formula = cell.find("{%s}f" % M)
        if formula is not None and formula.get("t") == "array":
            arrays.update(expand_ref(formula.get("ref", "")))
    return dom, _cells(dom), nonanchors, arrays


def _preflight(source, tr):
    if file_digest(source) != SOURCE_SHA:
        raise Held("source_hash_mismatch")
    with zipfile.ZipFile(source) as z:
        if z.testzip() is not None:
            raise Held("source_zip_invalid")
        sheets = workbook_sheets(z)
        roster = _json(REF / "profile.json")["workbook_reference"]["sheets"]
        if len(sheets) != 18 or [name for name, _ in sheets] != roster:
            raise Held("sheet_roster_mismatch")
        by_sheet = defaultdict(list)
        for e in tr["entries"]:
            by_sheet[e["sheet"]].append(e)
        if set(by_sheet) - {name for name, _ in sheets}:
            raise Held("sheet_roster_mismatch")
        data = {}
        for name, path in sheets:
            raw = z.read(path)
            dom, cells, nonanchors, arrays = _sheet_structure(raw, path)
            for e in by_sheet[name]:
                ref = e["cell"]
                if not CELL.fullmatch(ref) or ref in nonanchors:
                    raise Held("merged_nonanchor_target")
                if ref in arrays:
                    raise Held("array_target")
                if _cell_state(cells.get(ref)) != e.get("before_state"):
                    raise Held("before_state_mismatch")
            data[name] = (path, raw, dom, cells)
        # The protected array is a source invariant even though it has no map entry.
        if "18. 현금흐름계획" not in data:
            raise Held("sheet_roster_mismatch")
        _, _, dom18, _ = data["18. 현금흐름계획"]
        f = _direct(_cells(dom18).get("D10"), "f") if _cells(dom18).get("D10") else None
        if f is None or f.getAttribute("t") != "array" or f.getAttribute("ref") != "D10:E10":
            raise Held("protected_array_mismatch")
        return sheets, data, load_shared(z)


def _reference_key(ref):
    match = CELL.fullmatch(ref)
    if not match:
        raise Held("coordinate_invalid")
    col = 0
    for ch in match.group(1):
        col = col * 26 + ord(ch) - 64
    return int(match.group(2)), col


def _create_cell(doc, ref):
    data = doc.getElementsByTagNameNS(M, "sheetData")
    if len(data) != 1:
        raise Held("sheet_data_invalid")
    sheet_data = data[0]
    row_num, col_num = _reference_key(ref)
    rows = _children(sheet_data, "row")
    row = next((r for r in rows if r.getAttribute("r") == str(row_num)), None)
    if row is None:
        row = _node(doc, "row")
        row.setAttribute("r", str(row_num))
        next_row = next((r for r in rows if int(r.getAttribute("r")) > row_num), None)
        sheet_data.insertBefore(row, next_row)
    cell = _node(doc, "c")
    cell.setAttribute("r", ref)
    next_cell = next((c for c in _children(row, "c")
                      if _reference_key(c.getAttribute("r"))[1] > col_num), None)
    row.insertBefore(cell, next_cell)
    return cell


def _expand_shared(doc):
    """Expand only observed members; interruptions retain their own content."""
    from openpyxl.formula.translate import Translator
    groups = defaultdict(list)
    for cell in doc.getElementsByTagNameNS(M, "c"):
        formula = _direct(cell, "f")
        if formula is not None and formula.getAttribute("t") == "shared":
            groups[formula.getAttribute("si")].append((cell.getAttribute("r"), formula))
    for members in groups.values():
        anchors = [(ref, f) for ref, f in members if _text(f).strip()]
        if len(anchors) != 1:
            raise Held("shared_group_invalid")
        anchor, af = anchors[0]
        covered = set(expand_ref(af.getAttribute("ref")))
        if not {ref for ref, _ in members} <= covered:
            raise Held("shared_group_invalid")
        formula = _text(af)
        for ref, f in sorted(members, key=lambda item: _reference_key(item[0])):
            translated = formula if ref == anchor else Translator("=" + formula, origin=anchor).translate_formula(ref)[1:]
            for attr in ("t", "si", "ref"):
                if f.hasAttribute(attr):
                    f.removeAttribute(attr)
            for child in list(f.childNodes):
                f.removeChild(child)
            f.appendChild(doc.createTextNode(translated))
    return len(groups)


def _original_value(cell, shared):
    if cell is None:
        raise Held("label_source_missing")
    v = _direct(cell, "v")
    if cell.getAttribute("t") == "s" and v is not None:
        return shared[int(_text(v))]
    inline = _direct(cell, "is")
    if inline is not None:
        return "".join(_text(t) for t in inline.getElementsByTagNameNS(M, "t"))
    if v is not None:
        return _text(v)
    raise Held("label_source_missing")


def _label(e, source_cells, shared, values=None, params=None):
    """Label payload as (cell kind, payload); source-derived labels only.

    'source_prefix_plus_area_m2' is emitted as an Excel formula so the printed
    label follows the area input cell live instead of freezing a string.  The
    prefix text is the retained source label with any trailing number/unit
    stripped; the area coordinate comes from the public label_value_cell.
    """
    key = e.get("label_key")
    if key is None:
        return "string", _substitute_label(e.get("after_value"), values or {})
    sh, ref = e["label_source_cell"].rsplit("!", 1)
    original = _original_value(source_cells[sh].get(ref), shared)
    if key == "copy_retained_label":
        return "string", original
    if key == "source_prefix_plus_area_m2":
        target = e.get("label_value_cell") or ""
        if "!" not in target:
            raise Held("label_source_invalid")
        tsh, tref = target.rsplit("!", 1)
        if tsh != e["sheet"]:
            raise Held("label_source_invalid")
        matched = [key for key, spec in (params or {}).items()
                   if spec.get("canonical_cell") == target]
        if len(matched) != 1 or values is None or values.get(matched[0]) is None:
            raise Held("label_source_invalid")
        area = Decimal(str(values[matched[0]]))
        if not area.is_finite() or area < 0:
            raise Held("label_source_invalid")
        prefix = _label_source_prefix(original).replace('"', '""')
        return "formula", f'="{prefix}"&TEXT({tref},"#,##0")&"㎡"'
    raise Held("label_key_invalid")


def _put(cell, kind, value):
    doc = cell.ownerDocument
    for child in list(cell.childNodes):
        if child.nodeType == Node.ELEMENT_NODE and child.namespaceURI == M and child.localName in ("f", "v", "is"):
            cell.removeChild(child)
    if cell.hasAttribute("t"):
        cell.removeAttribute("t")
    if kind == "blank":
        return
    if kind == "formula":
        if not isinstance(value, str) or not value.startswith("="):
            raise Held("formula_invalid")
        node = _node(doc, "f")
        node.appendChild(doc.createTextNode(value[1:]))
        cell.appendChild(node)
    elif kind == "number":
        try:
            num = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise Held("number_invalid")
        if not num.is_finite() or abs(num) >= Decimal("1e15"):
            raise Held("number_invalid")
        node = _node(doc, "v")
        node.appendChild(doc.createTextNode(format(num, "f")))
        cell.appendChild(node)
    elif kind == "string":
        if not isinstance(value, str):
            raise Held("string_invalid")
        cell.setAttribute("t", "inlineStr")
        inline, text = _node(doc, "is"), _node(doc, "t")
        if value != value.strip():
            text.setAttribute("xml:space", "preserve")
        text.appendChild(doc.createTextNode(value))
        inline.appendChild(text)
        cell.appendChild(inline)
    else:
        raise Held("transition_invalid")


def _invalidate_caches(doc):
    count = 0
    for cell in doc.getElementsByTagNameNS(M, "c"):
        if _direct(cell, "f") is not None:
            value = _direct(cell, "v")
            if value is not None:
                cell.removeChild(value)
                count += 1
    return count


def _style_table(raw, required):
    if not required:
        return raw, {}
    doc = _serialize_dom(raw, "xl/styles.xml")
    numfmts = doc.getElementsByTagNameNS(M, "numFmts")
    xfs = doc.getElementsByTagNameNS(M, "cellXfs")
    if len(xfs) != 1:
        raise Held("styles_invalid")
    if numfmts:
        numfmts = numfmts[0]
    else:
        numfmts = _node(doc, "numFmts")
        doc.documentElement.insertBefore(numfmts, xfs[0])
    used = {int(n.getAttribute("numFmtId")) for n in numfmts.getElementsByTagNameNS(M, "numFmt")}
    next_id = max(used | {163}) + 1
    original_xfs = _children(xfs[0], "xf")
    mapping = {}
    for source_style, fmt in sorted(required):
        if source_style < 0 or source_style >= len(original_xfs):
            raise Held("styles_invalid")
        numfmt = _node(doc, "numFmt")
        numfmt.setAttribute("numFmtId", str(next_id))
        numfmt.setAttribute("formatCode", fmt)
        numfmts.appendChild(numfmt)
        clone = original_xfs[source_style].cloneNode(True)
        clone.setAttribute("numFmtId", str(next_id))
        clone.setAttribute("applyNumberFormat", "1")
        xfs[0].appendChild(clone)
        mapping[(source_style, fmt)] = len(_children(xfs[0], "xf")) - 1
        next_id += 1
    numfmts.setAttribute("count", str(len(_children(numfmts, "numFmt"))))
    xfs[0].setAttribute("count", str(len(_children(xfs[0], "xf"))))
    return doc.toxml(encoding="utf-8"), mapping


def _remove_calcchain(parts):
    chain = "xl/calcChain.xml"
    if chain not in parts:
        raise Held("calcchain_missing")
    del parts[chain]
    rel_path = "xl/_rels/workbook.xml.rels"
    rels = _serialize_dom(parts[rel_path], rel_path)
    removed = 0
    for r in list(rels.getElementsByTagNameNS(P, "Relationship")):
        if r.getAttribute("Type").endswith("/calcChain"):
            r.parentNode.removeChild(r)
            removed += 1
    if removed != 1:
        raise Held("calcchain_relationship_invalid")
    parts[rel_path] = rels.toxml(encoding="utf-8")
    types = _serialize_dom(parts["[Content_Types].xml"], "[Content_Types].xml")
    removed = 0
    for o in list(types.getElementsByTagNameNS(C, "Override")):
        if o.getAttribute("PartName") == "/xl/calcChain.xml":
            o.parentNode.removeChild(o)
            removed += 1
    if removed != 1:
        raise Held("calcchain_content_type_invalid")
    parts["[Content_Types].xml"] = types.toxml(encoding="utf-8")
    wb = _serialize_dom(parts["xl/workbook.xml"], "xl/workbook.xml")
    calc = wb.getElementsByTagNameNS(M, "calcPr")
    if len(calc) != 1:
        raise Held("calcpr_invalid")
    for key, value in (("calcMode", "auto"), ("fullCalcOnLoad", "1"), ("forceFullCalc", "1")):
        calc[0].setAttribute(key, value)
    parts["xl/workbook.xml"] = wb.toxml(encoding="utf-8")


def _rels_name(part):
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _resolve_rel_target(rels_part, target):
    if target.startswith("/"):
        return target[1:]
    base = posixpath.dirname(posixpath.dirname(rels_part))
    return posixpath.normpath(posixpath.join(base, target))


def _element_children(node):
    return [c for c in node.childNodes if c.nodeType == Node.ELEMENT_NODE]


def _remove_teacher_images(parts, sheets):
    """Strip the embedded textbook pictures from _TEACHER_IMAGE_SHEET.

    Every drawing anchor carrying an xdr:pic is removed; parts left
    unreferenced (the drawing itself, its rels, its media, its
    Content_Types override) are dropped.  Returns (exceptions, removed):
    exceptions is the explicit FD-B 6 member list the output may change or
    drop for this transformation.  Identification is purely structural
    (sheet -> sheet rels -> drawing part -> anchors); no media bytes or
    hashes are inspected.  Other drawings, such as the diagonal-stroke
    marks on sheet 18, are untouched.
    """
    exceptions = set()
    entry = next((e for e in sheets if e[0] == _TEACHER_IMAGE_SHEET), None)
    if entry is None:
        return exceptions, 0
    sheet_path = entry[1]
    sheet_rels_path = _rels_name(sheet_path)
    if sheet_rels_path not in parts:
        return exceptions, 0
    sheet_rels = _serialize_dom(parts[sheet_rels_path], sheet_rels_path)
    drawings = []
    for rel in sheet_rels.getElementsByTagNameNS(P, "Relationship"):
        if not rel.getAttribute("Type").endswith("/drawing"):
            continue
        part = _resolve_rel_target(sheet_rels_path, rel.getAttribute("Target"))
        if part not in parts:
            raise Held("drawing_target_missing")
        drawings.append((rel, part))
    if not drawings:
        return exceptions, 0
    removed = 0
    deleted = []
    touched = []
    stale_drawing_rels = []
    orphan_media = []
    removed_rids = []
    for rel, part in drawings:
        dom = _serialize_dom(parts[part], part)
        rels_path = _rels_name(part)
        embed_ids = []
        anchors = [c for c in _element_children(dom.documentElement)
                   if c.namespaceURI == XDR and c.localName.endswith("Anchor")]
        removed_any = False
        for anchor in anchors:
            embeds = [b.getAttributeNS(R, "embed")
                      for b in anchor.getElementsByTagNameNS(A, "blip")
                      if b.getAttributeNS(R, "embed")]
            if not embeds:
                continue
            dom.documentElement.removeChild(anchor)
            embed_ids.extend(embeds)
            removed_any = True
        if not removed_any:
            continue
        removed += 1
        remaining = [c for c in _element_children(dom.documentElement)
                     if c.namespaceURI == XDR and c.localName.endswith("Anchor")]
        if not remaining:
            # No drawing content left: drop the part, its rels file, the
            # sheet's <drawing> reference and the sheet rels entry.
            deleted.extend((part, rels_path))
            removed_rids.append(rel.getAttribute("Id"))
            rel.parentNode.removeChild(rel)
            stale_drawing_rels.append(rels_path)
        else:
            parts[part] = dom.toxml(encoding="utf-8")
            touched.append(part)
            if rels_path in parts:
                d_rels = _serialize_dom(parts[rels_path], rels_path)
                for d_rel in d_rels.getElementsByTagNameNS(P, "Relationship"):
                    if (d_rel.getAttribute("Type").endswith("/image")
                            and d_rel.getAttribute("Id") in embed_ids):
                        orphan_media.append(
                            _resolve_rel_target(rels_path, d_rel.getAttribute("Target")))
                        d_rel.parentNode.removeChild(d_rel)
                parts[rels_path] = d_rels.toxml(encoding="utf-8")
                touched.append(rels_path)
    if not removed:
        return exceptions, 0
    sheet_dom = _serialize_dom(parts[sheet_path], sheet_path)
    sheet_dirty = False
    for d_el in sheet_dom.getElementsByTagNameNS(M, "drawing"):
        if d_el.getAttributeNS(R, "id") in removed_rids:
            d_el.parentNode.removeChild(d_el)
            sheet_dirty = True
    if sheet_dirty:
        parts[sheet_path] = sheet_dom.toxml(encoding="utf-8")
    if removed_rids:
        parts[sheet_rels_path] = sheet_rels.toxml(encoding="utf-8")
        touched.append(sheet_rels_path)
    # Media listed only inside rels files of deleted drawings.
    for rels_path in stale_drawing_rels:
        if rels_path in parts:
            d_rels = _serialize_dom(parts[rels_path], rels_path)
            for d_rel in d_rels.getElementsByTagNameNS(P, "Relationship"):
                if d_rel.getAttribute("Type").endswith("/image"):
                    orphan_media.append(
                        _resolve_rel_target(rels_path, d_rel.getAttribute("Target")))
    # Drop orphaned media unless some surviving rels still references it.
    still_referenced = set()
    for name, raw in parts.items():
        if not name.endswith(".rels") or name in deleted:
            continue
        doc = _serialize_dom(raw, name)
        for rel in doc.getElementsByTagNameNS(P, "Relationship"):
            still_referenced.add(_resolve_rel_target(name, rel.getAttribute("Target")))
    for media in orphan_media:
        if media in parts and media not in still_referenced:
            deleted.append(media)
    for name in deleted:
        parts.pop(name, None)
        exceptions.add(name)
    for name in touched:
        exceptions.add(name)
    if deleted:
        types = _serialize_dom(parts["[Content_Types].xml"], "[Content_Types].xml")
        for o in types.getElementsByTagNameNS(C, "Override"):
            if o.getAttribute("PartName").lstrip("/") in deleted:
                o.parentNode.removeChild(o)
        parts["[Content_Types].xml"] = types.toxml(encoding="utf-8")
        exceptions.add("[Content_Types].xml")
    return exceptions, removed


def _zip_transform(source, tr, values, params=None):
    sheets, data, shared = _preflight(source, tr)
    original_cells = {name: cells for name, (_, _, _, cells) in data.items()}
    labels = {(e["sheet"], e["cell"]): _label(e, original_cells, shared, values, params)
              for e in tr["entries"] if e.get("label_key")}
    by_sheet = defaultdict(list)
    for e in tr["entries"]:
        by_sheet[e["sheet"]].append(e)
    changed = {}
    counts = Counter()
    required_formats = set()
    format_targets = []
    for name, path in sheets:
        _, _, dom, cells = data[name]
        counts["shared_groups"] += _expand_shared(dom)
        for e in by_sheet[name]:
            ref = e["cell"]
            cell = cells.get(ref)
            if cell is None:
                cell = _create_cell(dom, ref)
                cells[ref] = cell
                counts["created_cells"] += 1
            action = e["action"]
            if action == "formula" or action == "label_template" and e.get("after_formula"):
                _put(cell, "formula", e.get("after_formula"))
            elif action == "clear":
                _put(cell, "blank", None)
            elif action == "label_template":
                kind, payload = (labels[(name, ref)] if (name, ref) in labels
                                 else _label(e, original_cells, shared, values, params))
                _put(cell, kind, payload)
            elif action == "input_param":
                pkey = e.get("param_key")
                spec = (params or {}).get(pkey)
                if not pkey or spec is None:
                    raise Held("param_key_missing")
                value = values.get(pkey)
                if value is None:
                    _put(cell, "blank", None)
                elif spec.get("unit_code") == "DATE":
                    _put(cell, "number", _serial(value))
                else:
                    _put(cell, "string" if isinstance(value, str) else "number", value)
            else:
                raise Held("transition_invalid")
            if e.get("number_format"):
                style = int(cell.getAttribute("s") or 0)
                fmt = e["number_format"]
                required_formats.add((style, fmt))
                format_targets.append((cell, style, fmt))
            counts[action] += 1
        counts["cache_invalidated"] += _invalidate_caches(dom)
        changed[path] = dom.toxml(encoding="utf-8")
    with zipfile.ZipFile(source) as zin:
        parts = {info.filename: zin.read(info.filename) for info in zin.infolist()}
        infos = zin.infolist()
    styles, mapping = _style_table(parts["xl/styles.xml"], required_formats)
    for cell, style, fmt in format_targets:
        cell.setAttribute("s", str(mapping[(style, fmt)]))
    for name, path in sheets:
        changed[path] = data[name][2].toxml(encoding="utf-8")
    parts.update(changed)
    parts["xl/styles.xml"] = styles
    _remove_calcchain(parts)
    teacher_exceptions, teacher_removed = _remove_teacher_images(parts, sheets)
    counts["teacher_images_removed"] = teacher_removed
    target_names = set(changed) | {"xl/styles.xml", "xl/workbook.xml",
                                    "xl/_rels/workbook.xml.rels", "[Content_Types].xml",
                                    "xl/calcChain.xml"} | teacher_exceptions
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in infos:
            if info.filename in parts:
                zout.writestr(info, parts[info.filename])
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as check:
        if check.testzip() is not None or set(check.namelist()) != set(parts):
            raise Held("output_zip_invalid")
        for name in set(parts) - target_names:
            if check.read(name) != parts[name]:
                raise Held("zip_member_changed")
    return out.getvalue(), dict(counts)


_AUDIT_ROOT = {
    FILES[0]: {"schema", "source_id", "source_sha256", "entry_count", "entries"},
    FILES[1]: {"schema", "source_id", "source_sha256", "param_count", "parameters", "rules"},
    FILES[2]: {"schema", "source_id", "source_sha256", "classes", "exhaustiveness",
               "explicit_none_zero_keys", "explicit_none_blank_keys", "rule_ids",
               "teacher_example_coords", "registry", "registry_count"},
}
_AUDIT_ENTRY = {"sheet", "cell", "action", "before_state", "after_formula", "after_value",
                "param_key", "map_source", "reason", "number_format", "label_key",
                "label_source_cell", "label_value_cell", "teacher_example"}
_AUDIT_PARAM = {"canonical_cell", "data_type", "unit_code", "per_unit", "period_axis",
                "allowed_range", "required_when", "allowed_answer_states",
                "explicit_none_calculation", "explicit_none_output", "explicit_none_display_strategy",
                "explicit_none_number_format_policy", "explicit_none_number_format_fallback",
                "explicit_none_display_note", "output_conversion", "method_tag",
                "required_if", "forbidden_if", "zero_evidence", "allowed_values",
                "source_lanes", "mapping_status", "validation_status", "specification",
                "conversion_evidence"}
_AUDIT_REGISTRY = {"sheet", "cell", "action", "result_kind", "unit", "printed",
                   "core_reachable", "expected_state", "condition_key_if_conditional",
                   "active_kind"}
_AUDIT_RULE = {
    "year_axis": {"start_year_cell", "plan_year_n", "base_date", "new_loan_years",
                  "loan_summary_years", "last_payment_year", "old_2027", "old_2032",
                  "old_2051", "migration_status"},
    "explicit_none_zero": {"rule_id", "source_value", "source_answer_state", "requires",
                           "derived_value", "receipt_fields", "immutability", "unknown_not_asked",
                           "unlisted_param"},
    "explicit_none_blank": {"rule_id", "removed_keys_count", "kept_zero_keys_count",
                            "verification_order"},
    "category_absence_zero": {"rule_id", "requires", "targets", "positive_or_unknown",
                              "derivation_receipt"},
}
_NUM_TOKEN = re.compile(r"(?<![A-Z0-9_$.])(\d+(?:\.\d+)?)")
_STR_TOKEN = re.compile(r'"((?:[^"]|"")*)"')
_SHEET_TOKEN = re.compile(r"'(?:[^']|'')*'!")
_ALLOWED_CONSTANTS = {str(i): ("Excel structural index or period bound" if i else
                               "zero branch and derived absence") for i in range(26)}
_ALLOWED_CONSTANTS.update({"31": "December month end", "1000": "KRW to KRW_1000",
                           "0.0": "TEXT numeric format", "0.00": "TEXT numeric format"})
_ALLOWED_FORMULA_STRINGS = {"부지", "시설", "재배", "해당 없음", ""}
_BEFORE = {"absent", "blank_node", "number", "shared_string", "inline_string",
           "formula_normal", "formula_shared_anchor", "formula_shared_member",
           "array_member", "bool", "error"}
_ACTIVE_TOKENS = {"number", "string", "empty"}
_ACTIVE_KIND_RE = re.compile(
    r"^(?:number|string|empty)(?:\|(?:number|string|empty))*$")


def _canon_number(value):
    try:
        d = Decimal(str(value))
        if d.is_finite():
            return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else format(d.normalize(), "f")
    except (InvalidOperation, ValueError):
        pass
    return None


def _formula_numbers(value):
    return _NUM_TOKEN.findall(_SHEET_TOKEN.sub("", _STR_TOKEN.sub('""', value)))


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, None
            yield from _walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (i,))
    else:
        yield path, path[-1] if path else "", obj


def _source_audit_tokens(source):
    """Read source values only in memory; diagnostics contain codes and paths."""
    numbers, strings = set(), set()
    with zipfile.ZipFile(source) as z:
        shared = load_shared(z)
        for _, path in workbook_sheets(z):
            root = ET.fromstring(z.read(path))
            for cell in root.iter("{%s}c" % M):
                formula = cell.find("{%s}f" % M)
                value = cell.find("{%s}v" % M)
                inline = cell.find("{%s}is" % M)
                if formula is not None:
                    formula_text = formula.text or ""
                    numbers.update(_canon_number(n) for n in _formula_numbers("=" + formula_text))
                    strings.update(s.replace('""', '"').strip() for s in _STR_TOKEN.findall(formula_text) if s)
                elif inline is not None:
                    text = "".join(t.text or "" for t in inline.iter("{%s}t" % M)).strip()
                    if text:
                        strings.add(text)
                elif value is not None:
                    raw = value.text or ""
                    if cell.get("t") == "s":
                        text = shared[int(raw)].strip()
                        if text:
                            strings.add(text)
                    elif cell.get("t") in (None, "n"):
                        numbers.add(_canon_number(raw))
    numbers.discard(None)
    return numbers, strings


def audit_public_documents(docs, source, expected_sha=SOURCE_SHA):
    """Return precise public-contract violations; never expose source values."""
    errors = []
    def add(code, path):
        errors.append({"code": code, "path": path})
    if file_digest(source) != expected_sha:
        return [{"code": "source_hash_mismatch", "path": "source"}]
    nums, strings = _source_audit_tokens(source)
    with zipfile.ZipFile(source) as z:
        sheet_map = dict(workbook_sheets(z))
        actual = {}
        for name, path in sheet_map.items():
            dom, cells, _, arrays = _sheet_structure(z.read(path), path)
            actual[name] = (cells, arrays)
    for name, allowed in _AUDIT_ROOT.items():
        doc = docs.get(name)
        if not isinstance(doc, dict):
            add("missing_document", name)
            continue
        for field in set(doc) - allowed:
            add("unknown_root_field", name + "." + field)
        if doc.get("source_sha256") != expected_sha:
            add("source_sha_missing_or_mismatch", name + ".source_sha256")
        if doc.get("source_id") != "H01":
            add("source_id_mismatch", name + ".source_id")
        schema = {FILES[0]: "knuaf-hort-finance-transform/v2",
                  FILES[1]: "knuaf-hort-finance-params/v2",
                  FILES[2]: "knuaf-hort-finance-verify-classes/v2"}[name]
        if doc.get("schema") != schema:
            add("schema_mismatch", name + ".schema")
    if len(errors):
        return errors
    tr, pa, vc = (docs[name] for name in FILES)
    entries, params, rules = tr.get("entries"), pa.get("parameters"), pa.get("rules")
    if not isinstance(entries, list) or tr.get("entry_count") != len(entries):
        add("entry_count", FILES[0] + ".entry_count")
    if not isinstance(params, dict) or pa.get("param_count") != len(params):
        add("param_count", FILES[1] + ".param_count")
    if not isinstance(rules, dict) or set(rules) != set(_AUDIT_RULE):
        add("rules_shape", FILES[1] + ".rules")
    if errors:
        return errors
    seen = set()
    for i, e in enumerate(entries):
        path = f"{FILES[0]}.entries[{i}]"
        if not isinstance(e, dict):
            add("entry_type", path)
            continue
        for field in set(e) - _AUDIT_ENTRY:
            add("entry_unknown_field", path + "." + field)
        if any(isinstance(v, (dict, list)) for v in e.values()):
            add("entry_nested_value", path)
        if e.get("before_state") not in _BEFORE:
            add("before_state_missing_or_invalid", path + ".before_state")
        sh, ref = e.get("sheet"), e.get("cell")
        if not isinstance(sh, str) or sh not in actual:
            add("entry_sheet", path + ".sheet")
        if not isinstance(sh, str) or not isinstance(ref, str) or sh not in actual or not CELL.fullmatch(ref):
            add("entry_coordinate", path)
            continue
        if (sh, ref) in seen:
            add("duplicate_transition_coordinate", path)
        seen.add((sh, ref))
        te = e.get("teacher_example")
        if te is not None and (te is not True
                               or e.get("before_state") in ("absent", "blank_node")):
            add("teacher_example_flag", path + ".teacher_example")
        cells, arrays = actual[sh]
        if e.get("before_state") != _cell_state(cells.get(ref), ref in arrays):
            add("before_state_source_mismatch", path + ".before_state")
        if "label_key" in e:
            if sh == "12 .경비계획" and ref in {"B4", "B29", "B54", "B79", "B105"}:
                want = ("source_prefix_plus_area_m2", "12 .경비계획!B29", "12 .경비계획!I4")
            elif sh == " 7. 판매계획" and ref in {"B75", "B85", "B95", "B105", "B115"}:
                want = ("copy_retained_label", "8. 생산계획!B16", None)
            else:
                want = None
            got = (e.get("label_key"), e.get("label_source_cell"), e.get("label_value_cell"))
            if e.get("action") != "label_template" or got != want or e.get("after_formula") or "after_value" in e:
                add("label_key_contract", path + ".label_key")
    for key, p in params.items():
        path = f"{FILES[1]}.parameters.{key}"
        if not isinstance(p, dict):
            add("param_type", path)
            continue
        for field in set(p) - _AUDIT_PARAM:
            add("param_unknown_field", path + "." + field)
        rng = p.get("allowed_range")
        if rng is not None and (not isinstance(rng, dict) or set(rng) - {"min", "max", "max_exclusive", "relative_min", "relative_max"}):
            add("range_unknown_field", path + ".allowed_range")
        if isinstance(rng, dict) and any(isinstance(v, (dict, list)) for v in rng.values()):
            add("range_nested_value", path + ".allowed_range")
        for field, value in p.items():
            if field not in ("allowed_range", "allowed_answer_states", "required_if",
                             "forbidden_if", "allowed_values", "source_lanes") \
                    and isinstance(value, (dict, list)):
                add("param_nested_value", path + "." + field)
        if not isinstance(p.get("allowed_answer_states"), list) or any(
                not isinstance(v, str) for v in p["allowed_answer_states"]):
            add("answer_states_type", path + ".allowed_answer_states")
        for tree_field in ("required_if", "forbidden_if"):
            if p.get(tree_field) is not None:
                _cond_tree_check(p[tree_field], params, path + "." + tree_field, add)
        if p.get("required_if") is None:
            add("required_if_missing", path + ".required_if")
        if p.get("allowed_values") is not None and (
                not isinstance(p["allowed_values"], list)
                or any(not isinstance(v, str) for v in p["allowed_values"])):
            add("allowed_values_type", path + ".allowed_values")
        if p.get("zero_evidence") is not None and not isinstance(p["zero_evidence"], bool):
            add("zero_evidence_type", path + ".zero_evidence")
    registry = vc.get("registry")
    if not isinstance(registry, list) or vc.get("registry_count") != len(registry):
        add("registry_count", FILES[2] + ".registry_count")
        registry = registry if isinstance(registry, list) else []
    seen_coords = set()
    kinds = set(vc.get("classes") or [])
    for i, row in enumerate(registry):
        path = f"{FILES[2]}.registry[{i}]"
        if not isinstance(row, dict):
            add("registry_row_type", path)
            continue
        for field in set(row) - _AUDIT_REGISTRY:
            add("registry_unknown_field", path + "." + field)
        for need in ("sheet", "cell", "action", "result_kind", "unit", "printed",
                     "core_reachable", "expected_state"):
            if need not in row:
                add("registry_missing_field", path + "." + need)
        sh, ref = row.get("sheet"), row.get("cell")
        if sh not in actual or not isinstance(ref, str) or not CELL.fullmatch(ref):
            add("registry_coordinate", path)
            continue
        if (sh, ref) in seen_coords:
            add("registry_duplicate", path)
        seen_coords.add((sh, ref))
        if row.get("result_kind") not in kinds:
            add("registry_kind", path + ".result_kind")
        if row.get("action") not in {"formula", "input_param", "clear", "label_template", "retain"}:
            add("registry_action", path + ".action")
        ck = row.get("condition_key_if_conditional")
        if ck is not None:
            if not isinstance(ck, str):
                add("registry_condition", path)
            elif ck.startswith("param:"):
                if ck.removeprefix("param:") not in params:
                    add("registry_condition_param", path)
            elif ck.startswith("any_inactive:"):
                for group in ck.removeprefix("any_inactive:").split(","):
                    if not _group_members(params, group):
                        add("registry_condition_group", path + "." + group)
            else:
                add("registry_condition", path)
        for field in ("printed", "core_reachable"):
            if not isinstance(row.get(field), bool):
                add("registry_flag_type", path + "." + field)
        if row.get("result_kind") == "conditional_unused_na" and not ck:
            add("registry_condition_missing", path)
        if row.get("action") == "conditional_unused_na":
            add("registry_kind_mismatch", path)
        if row.get("result_kind") == "conditional_unused_na":
            ak = row.get("active_kind")
            if not (isinstance(ak, str) and _ACTIVE_KIND_RE.fullmatch(ak)):
                add("registry_active_kind", path)
        elif row.get("active_kind") is not None:
            add("registry_active_kind", path)
    for e in entries:
        coord = (e["sheet"], e["cell"])
        if coord not in seen_coords:
            add("registry_missing_target", f"{e['sheet']}!{e['cell']}")
    for key, allowed in _AUDIT_RULE.items():
        node = rules.get(key)
        if not isinstance(node, dict) or set(node) - allowed:
            add("rule_unknown_field", f"{FILES[1]}.rules.{key}")
        if isinstance(node, dict) and any(
                isinstance(v, dict) or isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)
                for v in node.values()):
            add("rule_nested_value", f"{FILES[1]}.rules.{key}")
    for field in ("classes", "explicit_none_zero_keys", "explicit_none_blank_keys", "rule_ids"):
        if not isinstance(vc.get(field), list) or any(not isinstance(x, str) for x in vc[field]):
            add("verify_list_type", FILES[2] + "." + field)
    tec = vc.get("teacher_example_coords")
    if (not isinstance(tec, list) or any(
            not isinstance(x, dict) or set(x) != {"sheet", "cell"}
            or x.get("sheet") not in actual
            or not isinstance(x.get("cell"), str) or not CELL.fullmatch(x["cell"])
            for x in tec) or len({(x["sheet"], x["cell"]) for x in tec}) != len(tec)):
        add("teacher_example_coords", FILES[2] + ".teacher_example_coords")
    # These are contract-defined control words, not copied teaching text.
    # The exception is path-specific: arbitrary values nested under these
    # fields still pass through the source-string/numeric/digest audit.
    condition_literals = {"부지", "시설", "재배", "해당 없음"}
    for name, doc in docs.items():
        for path, field, value in _walk(doc):
            loc = name + "." + ".".join(str(x) for x in path)
            if value is None:
                if not isinstance(field, str) or field in strings:
                    add("source_string_key", loc + "." + str(field))
                if isinstance(field, str) and ((field.startswith("before_") and field != "before_state")
                                               or "hash" in field.lower() and field != "source_sha256"):
                    add("forbidden_key", loc + "." + field)
                continue
            approved_control = False
            if name == FILES[1] and len(path) >= 3 and path[0] == "parameters":
                key = path[1]
                if path[-1] == "allowed_values" and isinstance(field, int):
                    approved = ({"hired", "self"} if key.endswith(".labor_kind")
                                else {"confirmed_absent"} if key.startswith("category_absence.")
                                else set())
                    approved_control = value in approved
                elif (path[-1] == "key_equals" and field == 1
                      and ("required_if" in path or "forbidden_if" in path)):
                    approved_control = value in condition_literals
            if approved_control:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if field not in {"entry_count", "param_count", "removed_keys_count",
                                 "kept_zero_keys_count", "registry_count"}:
                    num = _canon_number(value)
                    if num in nums and (field == "after_value" or num not in _ALLOWED_CONSTANTS):
                        add("source_numeric_value", loc)
            if not isinstance(value, str) or field == "source_sha256":
                continue
            if re.fullmatch(r"[a-f0-9]{64}", value):
                add("non_source_digest", loc)
            if field in ("sheet", "canonical_cell"):
                continue
            if field == "after_formula":
                for number in _formula_numbers(value):
                    if _canon_number(number) in nums and number not in _ALLOWED_CONSTANTS:
                        add("source_formula_number", loc)
                for string in _STR_TOKEN.findall(value):
                    if string.replace('""', '"').strip() in strings and string not in _ALLOWED_FORMULA_STRINGS:
                        add("source_formula_string", loc)
                continue
            stripped = value.strip()
            if field == "after_value" and _canon_number(stripped) in nums:
                add("source_numeric_text", loc)
            if stripped in strings:
                add("source_string_value", loc)
            elif len(stripped) >= 8 and any(len(s) >= 8 and s in stripped for s in strings):
                add("source_string_fragment", loc)
    return errors


_ABSENCE_SENTINEL = "해당 없음"
_LABOR_KINDS = ("hired", "self")
_CATEGORY_VALUE = "confirmed_absent"
_LOAN_METHODS = ("원리금균등", "원금균등", "만기일시")
_LABEL_TOKEN_RE = re.compile(r"\{([^{}]+)\}")
# label_template after_value tokens -> (param key, absence fallback).
# A param key of None is a deterministic literal fallback; a missing fact for a
# bound key without a declared fallback refuses the transition (fail closed).
_LABEL_TOKENS = {
    "crop_item": ("sales.crop_item", None),
    "quantity_unit_code": ("sales.quantity_unit", None),
    "per_unit": ("sales.price_per_unit", None),
    "observed_currency": ("price.observed.currency", ""),
    "observed_per_unit": ("price.observed.per_unit", ""),
    "observed_price_source_or_blank": ("price.observed.source", ""),
    "byproduct_name_or_dash": ("sales.byproduct_name", "—"),
    "cycle_note_or_blank": ("crop_cycle.note", ""),
    "cycle_source_or_blank": ("crop_cycle.source", ""),
    "facility_model_format": ("facility.model.format", ""),
    "facility_model_structure": ("facility.model.structure", ""),
}


def _substitute_label(template, values):
    """Resolve {token} placeholders in a label_template after_value.

    Tokens bound to a fact key take the resolved fact value; declared
    absence fallbacks ("" or "—") apply when the fact is legitimately
    absent.  An unmapped token or a missing required value refuses the
    label so a placeholder can never reach the student workbook.
    """
    if not isinstance(template, str) or "{" not in template:
        return template
    def repl(match):
        token = match.group(1)
        if token not in _LABEL_TOKENS:
            raise Held("label_token_unknown", token)
        key, fallback = _LABEL_TOKENS[token]
        value = values.get(key)
        if value is None:
            if fallback is None:
                raise Held("label_token_unresolved", token + ":" + key)
            return fallback
        return str(value)
    out = _LABEL_TOKEN_RE.sub(repl, template)
    if _LABEL_TOKEN_RE.search(out):
        raise Held("label_token_unresolved", out)
    return out

TRUE_COND = {"all": []}
FALSE_COND = {"any": []}
ZERO_RULE = "HFIN-V3-EXPLICIT-NONE-ZERO-1"
CATEGORY_RULE = "HFIN-V3-CATEGORY-ABSENCE-ZERO-1"
# A confirmed-absence fact proves, for the base date and every plan year, that
# an entire line/category is empty.  Category -> (member key prefixes whose
# positive values contradict the absence, derivation receipt targets).
# "sales.*.stock_qty" is special-cased: inventory absence conflicts only with
# positive stock quantities, not with production/sales facts.
_CATEGORIES = {
    "cash": (("opening.cash_",), ()),
    "land": (("opening.land_",), ()),
    "equipment": (("opening.equipment_slot", "asset.opening_equipment."), ()),
    "liability": (("opening.liability_slot",), ()),
    "inventory": (("stock_qty",), ("17. 추정대차대조표!C10:H10",)),
    "opening_facility": ((), ("17. 추정대차대조표!C19",)),
    "other_fixed_assets": ((), ("17. 추정대차대조표!C20:H20",)),
    "biological_assets": ((), ("17. 추정대차대조표!C22:H22",
                               "17. 추정대차대조표!C24:H24")),
    "other_current_liabilities": ((), ("17. 추정대차대조표!C33:H33",)),
    "receipt_fees": ((), ("18. 현금흐름계획!D8:H8",)),
    "asset_sale": ((), ("18. 현금흐름계획!D11:H11",)),
}
_CATEGORY_PREFIX = "category_absence."
# Sheet-17/18 lines with no parameter feed: their absence fact is mandatory.
_ALWAYS_ABSENT_CATEGORIES = (
    "inventory", "opening_facility", "other_fixed_assets", "biological_assets",
    "other_current_liabilities", "receipt_fees", "asset_sale",
)
# Opening members that may only hold a fact once the category absence fact is
# confirmed; while the category fact is absent each member is required.
_CATEGORY_GATED = {
    "opening.cash_krw1000": "cash",
    "opening.land_area_m2": "land",
    "opening.land_value_krw1000": "land",
    "opening.land_extra_area_m2": "land",
    "opening.land_extra_value_krw1000": "land",
    "opening.liability_slot1_krw1000": "liability",
    "opening.liability_slot2_krw1000": "liability",
    "opening.equipment_slot1.quantity": "equipment",
    "opening.equipment_slot1.life_years": "equipment",
    "opening.equipment_slot1.purchase_year": "equipment",
    "opening.equipment_slot1_cost_krw1000": "equipment",
    "opening.equipment_slot2.quantity": "equipment",
    "opening.equipment_slot2.life_years": "equipment",
    "opening.equipment_slot2.purchase_year": "equipment",
    "opening.equipment_slot2_cost_krw1000": "equipment",
}


def _member_of(key, prefix):
    return key == prefix or key.startswith(prefix + ".") or key.startswith(prefix + "_") \
        or (prefix.endswith((".", "_")) and key.startswith(prefix))


def _group_members(params, prefix):
    members = [key for key in params if _member_of(key, prefix)]
    if not members and prefix.startswith("asset.opening_equipment."):
        alt = "opening.equipment_" + prefix.rsplit(".", 1)[1]
        members = [key for key in params if _member_of(key, alt)]
    return members


def _group_active(params, present, resolved, prefix):
    """Any member fact carrying a real (non-zero, non-marker) declaration.

    A member activates its group only when the resolved value is a positive
    number, a string that is not the retained '해당 없음' absence marker, or a
    date.  Zero (provided or derived from explicit_none) is inert, so declaring
    a zero quantity does not force the rest of an unused group to be filled in.
    """
    members = _group_members(params, prefix)
    if prefix.startswith("asset.opening_equipment."):
        alt = "opening.equipment_" + prefix.rsplit(".", 1)[1]
        members += [key for key in params if _member_of(key, alt)]
    for key in members:
        if key not in present:
            continue
        spec = params[key]
        value = resolved.get(key)
        if value is None:
            continue
        if spec.get("unit_code") == "EVIDENCE_REF" and value == _ABSENCE_SENTINEL:
            continue
        if isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value == 0:
            continue
        return True
    return False


def _eval_cond(node, params, present, resolved, spec):
    """Evaluate a required_if/forbidden_if condition tree."""
    if node is None:
        return False
    if not isinstance(node, dict) or len(node) != 1:
        raise Held("condition_tree_invalid")
    (op, arg), = node.items()
    if op == "all":
        return all(_eval_cond(item, params, present, resolved, spec) for item in arg)
    if op == "any":
        return any(_eval_cond(item, params, present, resolved, spec) for item in arg)
    if op == "not":
        return not _eval_cond(arg, params, present, resolved, spec)
    if op == "key_present":
        return arg in present
    if op == "key_equals":
        key, want = arg
        if key not in present:
            return False
        got = resolved.get(key)
        try:
            return Decimal(str(got)) == Decimal(str(want))
        except (InvalidOperation, ValueError, TypeError):
            return got == want
    if op == "group_active":
        return _group_active(params, present, resolved, arg)
    if op == "method_is":
        return (spec.get("method_tag") == arg) if isinstance(arg, str) else arg in (
            spec.get("method_tag"),)
    raise Held("condition_tree_invalid")


def _cond_tree_check(node, params, path, add, depth=0):
    """Structural audit of a public condition tree (no source strings)."""
    if not isinstance(node, dict) or len(node) != 1 or depth > 8:
        add("condition_tree_shape", path)
        return
    (op, arg), = node.items()
    if op in ("all", "any"):
        if not isinstance(arg, list):
            add("condition_tree_shape", path)
            return
        for item in arg:
            _cond_tree_check(item, params, path, add, depth + 1)
        return
    if op == "not":
        _cond_tree_check(arg, params, path, add, depth + 1)
        return
    if op == "key_present":
        if arg not in params:
            add("condition_unknown_key", path + "." + str(arg))
        return
    if op == "key_equals":
        if (not isinstance(arg, list) or len(arg) != 2 or arg[0] not in params
                or isinstance(arg[1], (dict, list))):
            add("condition_tree_shape", path)
            return
        value = arg[1]
        allowed = _ALLOWED_FORMULA_STRINGS | set(_LABOR_KINDS) | {_CATEGORY_VALUE} | {
            "area_unit", "asset_rate", "tax_base_rate", "direct"}
        if isinstance(value, str) and value not in allowed:
            add("condition_value_unapproved", path)
        return
    if op == "group_active":
        if not isinstance(arg, str) or not _group_members(params, arg):
            add("condition_unknown_group", path + "." + str(arg))
        return
    if op == "method_is":
        allowed = {"area_unit", "asset_rate", "tax_base_rate", "direct"}
        if isinstance(arg, str):
            arg = [arg]
        if not isinstance(arg, list) or any(a not in allowed for a in arg):
            add("condition_value_unapproved", path)
        return
    add("condition_tree_shape", path)


def _serial(date_text):
    import datetime as _dt
    try:
        day = _dt.date.fromisoformat(str(date_text).strip())
    except ValueError:
        raise Held("fact_type_invalid")
    return (day - _dt.date(1899, 12, 30)).days


def _label_source_prefix(raw):
    """Retained area label text: strip a trailing number/㎡ tail if present."""
    text = str(raw)
    match = re.match(r"^(.*?)[0-9][0-9,]*\s*㎡?\s*$", text, re.S)
    return match.group(1) if match else text


def _fact_snapshot(project):
    facts = project.get("facts", {})
    selected = {fid: fact for fid, fact in facts.items()
                if isinstance(fact, dict) and str(fact.get("field_id", "")).startswith("hort_env_systems.fin.")}
    rows = []
    for fid, f in sorted(selected.items()):
        row = {"id": fid, "field_id": f.get("field_id"),
               "answer_state": f.get("answer_state"), "revision": f.get("revision"),
               "value": f.get("value"), "unit": f.get("unit"),
               "period": f.get("period")}
        for semantic in ("per_unit", "specification", "conversion_evidence"):
            if semantic in f:
                row[semantic] = f[semantic]
        rows.append(row)
    return selected, digest(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _check_value(key, spec, fact):
    """Type/range/allowed-value validation for a provided or derived value."""
    state = fact.get("answer_state")
    if state not in spec.get("allowed_answer_states", ()) or state in ("unknown", "not_asked"):
        raise Held("answer_state_held")
    value = fact.get("value")
    if value is None:
        raise Held("provided_value_missing")
    kind = spec["data_type"]
    if kind in ("integer", "decimal"):
        if isinstance(value, bool):
            raise Held("fact_type_invalid")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise Held("fact_type_invalid")
        if not number.is_finite() or kind == "integer" and number != number.to_integral_value():
            raise Held("fact_type_invalid")
        bounds = spec.get("allowed_range") or {}
        for field, op in (("min", lambda a, b: a < b), ("max", lambda a, b: a > b)):
            if bounds.get(field) is not None and op(number, Decimal(str(bounds[field]))):
                raise Held("fact_range_invalid")
        if bounds.get("max_exclusive") and bounds.get("max") is not None and number == Decimal(str(bounds["max"])):
            raise Held("fact_range_invalid")
        return int(number) if kind == "integer" else number
    if kind == "string":
        if not isinstance(value, str) or not value.strip():
            raise Held("fact_type_invalid")
        allowed = spec.get("allowed_values")
        if allowed is not None and value not in allowed:
            raise Held("fact_value_invalid")
        return value
    if kind == "date":
        if not isinstance(value, str) or not value.strip():
            raise Held("fact_type_invalid")
        _serial(value)
        return value
    raise Held("fact_type_invalid")


def _check_fact_unit(key, spec, fact):
    """A declared semantic override must agree with the public parameter contract.

    Legacy canonical facts leave these fields null; in that case the namespaced
    field ID supplies the unit and basis defined by the parameter registry.
    A caller cannot attach a conflicting unit or an unapproved conversion.
    """
    unit = fact.get("unit")
    if unit is not None and unit != spec.get("unit_code"):
        raise Held("fact_unit_mismatch", key)
    basis = fact.get("per_unit")
    if basis is not None and basis != spec.get("per_unit"):
        raise Held("fact_per_unit_mismatch", key)
    for field in ("specification", "conversion_evidence"):
        declared = fact.get(field)
        if declared is not None and declared != spec.get(field):
            raise Held("fact_" + field + "_mismatch", key)


def _check_fact_period(key, spec, fact, start_year, values):
    """Bind an explicit fact period to its relative finance axis."""
    period = fact.get("period")
    if period is None:
        return
    axis = spec.get("period_axis")
    if isinstance(period, dict):
        year, month = period.get("year"), period.get("month")
        if set(period) - {"year", "month", "day"}:
            raise Held("fact_period_invalid", key)
    elif isinstance(period, int) and not isinstance(period, bool):
        year, month = period, None
    elif isinstance(period, str):
        match = re.fullmatch(r"([0-9]{4})(?:-([0-9]{2})(?:-[0-9]{2})?)?", period)
        if not match:
            raise Held("fact_period_invalid", key)
        year = int(match.group(1))
        month = int(match.group(2)) if match.group(2) else None
    else:
        raise Held("fact_period_invalid", key)
    if (not isinstance(year, int) or isinstance(year, bool)
            or month is not None and (not isinstance(month, int) or not 1 <= month <= 12)):
        raise Held("fact_period_invalid", key)
    y_match = re.search(r"(?:^|\.)y([1-5])(?:\.|$)", key)
    m_match = re.search(r"(?:^|\.)m(1[0-2]|[1-9])(?:\.|$)", key)
    plan_year = start_year + int(y_match.group(1)) - 1 if y_match else None
    if axis == "base_date_start_year_minus_1_end":
        allowed_year = start_year - 1
    elif axis == "loan_contract_year":
        slot = re.match(r"loan\.slot([1-5])\.", key)
        draw = values.get(f"loan.slot{slot.group(1)}.execution_year") if slot else None
        allowed_year = draw if draw is not None else start_year
    elif axis == "investment_acquisition_or_service_year":
        slot = re.match(r"invest\.slot([1-9])\.", key)
        service = values.get(f"invest.slot{slot.group(1)}.service_start_year") if slot else None
        allowed_year = service if service is not None else plan_year
    elif axis in ("plan_year", "plan_year_month"):
        allowed_year = plan_year
    elif axis in ("observation_year", "observation_year_month"):
        if not 1900 <= year <= start_year:
            raise Held("fact_period_axis_mismatch", key)
        allowed_year = None
    elif axis in ("one_time_or_observation", "all_plan_years"):
        if not 1900 <= year <= start_year + 4:
            raise Held("fact_period_axis_mismatch", key)
        allowed_year = None
    else:
        raise Held("fact_period_axis_unclassified", key)
    if allowed_year is not None and year != allowed_year:
        raise Held("fact_period_axis_mismatch", key)
    if m_match and month is not None and month != int(m_match.group(1)):
        raise Held("fact_period_axis_mismatch", key)
    if axis in ("plan_year", "loan_contract_year", "observation_year") and month is not None:
        raise Held("fact_period_axis_mismatch", key)


def _has_evidence(fact):
    return bool(fact.get("reason") or fact.get("source_refs"))


def _declared_keys(project, params):
    """Param keys carrying a declared fact (independent of resolved value).

    Mirrors the present map _facts builds internally so verify evaluates
    registry condition keys against the same declaration set as the
    required_if/forbidden_if gates, without changing _facts' signature.
    """
    keys = set()
    for fact in project.get("facts", {}).values():
        if not isinstance(fact, dict):
            continue
        field = str(fact.get("field_id", ""))
        if field.startswith("hort_env_systems.fin."):
            key = field.removeprefix("hort_env_systems.fin.")
            if key in params:
                keys.add(key)
    return keys


def _condition_value(row, params, present, resolved, loc):
    """Evaluate a registry condition_key against declared facts.

    any_inactive:g1,g2 licenses the conditional result when at least one
    listed group carries no real declaration.  param:key licenses it while
    that single key is undeclared.  Unclassifiable keys refuse the row.
    """
    if row.get("result_kind") != "conditional_unused_na":
        return None
    ck = row.get("condition_key_if_conditional")
    if not isinstance(ck, str) or not ck:
        raise Held("registry_condition_missing", loc)
    if ck.startswith("any_inactive:"):
        parts = ck.removeprefix("any_inactive:").split(",")
        for part in parts:
            if not _group_members(params, part):
                raise Held("registry_condition_group", loc + ":" + part)
        return any(not _group_active(params, present, resolved, part)
                   for part in parts)
    if ck.startswith("param:"):
        key = ck.removeprefix("param:")
        if key not in params:
            raise Held("registry_condition_param", loc)
        return key not in present
    raise Held("registry_condition", loc)


def _check_sales_units(values):
    if (values.get("sales.quantity_unit") != values.get("sales.price_per_unit")
            or values.get("sales.currency") != "원"
            or not values.get("sales.package_spec")):
        raise Held("sales_unit_mismatch")


def _check_material_basis(values):
    for key, value in values.items():
        if (key.startswith("cost.material.") and key.endswith(".basis_m2")
                and (not isinstance(value, (int, Decimal)) or isinstance(value, bool)
                     or value <= 0)):
            raise Held("material_basis_invalid", key)


def _facts(project, params):
    facts, fact_sha = _fact_snapshot(project)
    by_key = defaultdict(list)
    for fact in facts.values():
        by_key[fact["field_id"].removeprefix("hort_env_systems.fin.")].append(fact)
    if set(by_key) - set(params):
        raise Held("unknown_param_fact")
    present, resolved, derivations = {}, {}, []
    for key, spec in params.items():
        rows = by_key.get(key, [])
        if len(rows) > 1:
            raise Held("duplicate_param_fact")
        if not rows:
            continue
        fact = rows[0]
        _check_fact_unit(key, spec, fact)
        state = fact.get("answer_state")
        if state == "explicit_none":
            if fact.get("value") is not None or not _has_evidence(fact):
                raise Held("explicit_none_invalid")
            if key in _CATEGORY_GATED and _CATEGORY_PREFIX + _CATEGORY_GATED[key] not in by_key:
                raise Held("category_absence_unconfirmed")
            if spec.get("explicit_none_calculation") != 0:
                raise Held("explicit_none_invalid")
            resolved[key] = 0
            present[key] = fact
            derivations.append({"source_fact_id": fact.get("id"),
                                "source_revision": fact.get("revision"),
                                "rule_id": ZERO_RULE, "param_key": key,
                                "target_cell": spec.get("canonical_cell"),
                                "derived_value": 0, "unit_code": spec.get("unit_code"),
                                "source_answer_state": state,
                                "evidence_ref": fact.get("source_refs")})
            continue
        present[key] = fact
        resolved[key] = _check_value(key, spec, fact)
    # Required / forbidden gates from the public condition trees.
    for key, spec in params.items():
        required = spec.get("required_if")
        if required is None:
            required = TRUE_COND if spec["required_when"].startswith("always") else FALSE_COND
        if key not in present and _eval_cond(required, params, present, resolved, spec):
            raise Held("required_fact_missing")
    for key, spec in params.items():
        forbidden = spec.get("forbidden_if")
        if key in present and forbidden is not None and _eval_cond(
                forbidden, params, present, resolved, spec):
            raise Held("param_fact_forbidden")
    if all(k in params for k in ("sales.quantity_unit", "sales.price_per_unit",
                                "sales.currency", "sales.package_spec")):
        _check_sales_units(resolved)
    _check_material_basis(resolved)
    # Category absence declarations and their conflicts.
    categories = {}
    for key, fact in present.items():
        if not key.startswith(_CATEGORY_PREFIX):
            continue
        cat = key.removeprefix(_CATEGORY_PREFIX)
        if cat not in _CATEGORIES:
            raise Held("category_absence_unknown")
        if fact.get("answer_state") != "provided" or resolved.get(key) != _CATEGORY_VALUE \
                or not _has_evidence(fact):
            raise Held("category_absence_invalid")
        categories[cat] = fact
    for cat, (prefixes, targets) in _CATEGORIES.items():
        if cat not in categories:
            if cat in _ALWAYS_ABSENT_CATEGORIES and _CATEGORY_PREFIX + cat in params:
                raise Held("required_fact_missing")
            continue
        for member in present:
            if member.startswith(_CATEGORY_PREFIX):
                continue
            value = resolved.get(member)
            positive = isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value > 0
            if prefixes == ("stock_qty",):
                hit = "stock_qty" in member and positive
            else:
                hit = any(member.startswith(prefix) for prefix in prefixes) \
                    and value not in (None, 0)
            if hit:
                raise Held("category_absence_conflict")
    # Confirmed-absent members never stated derive the approved 0 layer; each
    # registered sheet-17/18 absence target carries one derivation receipt.
    for key, spec in params.items():
        cat = _CATEGORY_GATED.get(key)
        if cat is None or cat not in categories or key in present:
            continue
        derived = 0 if spec.get("explicit_none_calculation") == 0 else None
        resolved[key] = derived
        derivations.append({"source_fact_id": categories[cat].get("id"),
                            "source_revision": categories[cat].get("revision"),
                            "rule_id": CATEGORY_RULE, "param_key": key,
                            "target_cell": spec.get("canonical_cell"),
                            "derived_value": derived, "unit_code": spec.get("unit_code"),
                            "source_answer_state": _CATEGORY_VALUE,
                            "evidence_ref": categories[cat].get("source_refs")})
    for cat, fact in categories.items():
        for target in _CATEGORIES[cat][1]:
            derivations.append({"source_fact_id": fact.get("id"),
                                "source_revision": fact.get("revision"),
                                "rule_id": CATEGORY_RULE, "param_key": None,
                                "target_cell": target, "derived_value": 0,
                                "unit_code": "KRW_1000",
                                "source_answer_state": _CATEGORY_VALUE,
                                "evidence_ref": fact.get("source_refs")})
    # Design §4 support gates — all numeric/string facts are resolved by now.
    values = resolved
    start_year = int(values.get("plan.start_year") or 0)
    if "plan.start_year" in params:
        if not start_year:
            raise Held("required_fact_missing")
        if "plan.base_date" in params \
                and values.get("plan.base_date") != f"{start_year - 1}-12-31":
            raise Held("base_date_unsupported")
    for key, fact in present.items():
        _check_fact_period(key, params[key], fact, start_year, values)
    for key, value in values.items():
        if "stock_qty" in key and isinstance(value, (int, Decimal)) and value > 0:
            raise Held("positive_inventory_unsupported")
    for year in range(1, 6):
        produced = [values.get(f"sales.y{year}.m{m}.production_qty") for m in range(1, 13)]
        marketability = values.get(f"sales.y{year}.marketability_rate")
        if (all(v is not None for v in produced)
                and marketability is not None
                and sum(Decimal(str(v)) for v in produced) * Decimal(str(marketability)) == 0):
            raise Held("zero_sales_year_unsupported")
    for slot in range(1, 6):
        members = {k: v for k, v in values.items()
                   if k.startswith(f"loan.slot{slot}.") and v is not None}
        if not members:
            continue
        if members.get(f"loan.slot{slot}.method") not in _LOAN_METHODS:
            raise Held("loan_method_unsupported")
        execution = members.get(f"loan.slot{slot}.execution_year")
        grace = members.get(f"loan.slot{slot}.grace_years")
        repay = members.get(f"loan.slot{slot}.repayment_years")
        if execution is None or grace is None or repay is None:
            raise Held("required_fact_missing")
        if not (start_year <= execution <= start_year + 4):
            raise Held("loan_draw_year_unsupported")
        if repay > (25 if slot == 1 else 15):
            raise Held("loan_term_unsupported")
        if execution + grace + repay - 1 > start_year + 24:
            raise Held("loan_horizon_unsupported")
    for slot in range(1, 10):
        service = values.get(f"invest.slot{slot}.service_start_year")
        if service is None:
            continue
        if service != start_year + (0 if slot <= 5 else slot - 5):
            raise Held("invest_year_unsupported")
    for key in params:
        if not key.endswith(".labor_kind") or values.get(key) != "self":
            continue
        prefix = key.rsplit(".", 1)[0] + "."
        for member, value in values.items():
            if member.startswith(prefix) and member != key \
                    and isinstance(value, (int, Decimal)) and value > 0:
                raise Held("self_labor_positive_unsupported")
    for key, spec in params.items():
        if spec.get("zero_evidence") and key in present:
            value = values.get(key)
            if isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value == 0 \
                    and not _has_evidence(present[key]):
                raise Held("zero_evidence_required")
    return values, derivations, fact_sha


def _artifact(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": file_digest(path), "size": path.stat().st_size}


def compare_raw_cache(expected, actual, unit_code):
    """Unrounded, absolute comparison; returns the difference on PASS."""
    try:
        want, got = Decimal(str(expected)), Decimal(str(actual))
    except (InvalidOperation, ValueError, TypeError):
        raise Held("cache_numeric_invalid")
    if not want.is_finite() or not got.is_finite():
        raise Held("cache_numeric_invalid")
    if not isinstance(unit_code, str):
        raise Held("cache_unit_unclassified")
    if unit_code == "KRW_1000" or unit_code.startswith("KRW_PER_"):
        tolerance = Decimal("0.001")
    elif unit_code in {"RATIO", "SCALAR"}:
        tolerance = Decimal("0.000000001")
    elif unit_code in {"COUNT", "YEAR", "YEARS", "MONTH", "SALES_UNIT", "ITEM_UNIT",
                       "DATE", "HOUR", "M2", "M", "KM", "M_PER_S", "HOUR_PER_DAY",
                       "CHARGE_UNIT_PER_SALES_UNIT"}:
        tolerance = Decimal(0)
    else:
        raise Held("cache_unit_unclassified")
    difference = abs(want - got)
    if difference > tolerance:
        raise Held("raw_cache_mismatch")
    return difference


def _example_equal(expected, cached):
    """Original H01 value vs native cache; str for text, Decimal for numbers."""
    if expected is None or cached is None:
        return False
    if isinstance(expected, str) or isinstance(cached, str):
        return isinstance(expected, str) and isinstance(cached, str)             and expected == cached
    try:
        return Decimal(str(expected)) == Decimal(str(cached))
    except (InvalidOperation, ValueError, TypeError):
        return False


def check_identity(left, right):
    """Exact accounting equality; no display rounding or relative tolerance."""
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        raise Held("identity_operand_invalid")
    if not a.is_finite() or not b.is_finite() or a != b:
        raise Held("accounting_identity_mismatch")
    return True


def _receipt(stage, project, auth, source, docs, fact_sha, values, previous=None,
             project_root=None):
    raw_values = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str,
                            separators=(",", ":")).encode()
    return {"schema": SCHEMA, "status": "pass", "stage": stage,
            "created_at": datetime.now(timezone.utc).isoformat(), "profile": PROFILE,
            "major_id": "hort_env_systems", "module_version": "0.2.0",
            "contract_version": auth.contract_version, "project_revision": project["revision"],
            "binding_evidence": auth.binding_evidence, "source_id": "H-X1",
            "source_sha256": SOURCE_SHA,
            "transform_map_sha256": file_digest(REF / FILES[0]),
            "param_registry_sha256": file_digest(REF / FILES[1]),
            "params_sha256": digest(raw_values), "canonical_facts_sha256": fact_sha,
            "input_artifacts": [_artifact(source)], "output_artifacts": [],
            "previous_receipt_sha256": file_digest(previous) if previous else None,
            "previous_receipt_path": str(Path(previous).resolve()) if previous else None,
            "project_root": str(Path(project_root).resolve()) if project_root else None,
            "major_authorization": auth.to_dict()}


def _write_receipt(path, obj):
    target = Path(path)
    if target.exists():
        raise Held("receipt_exists")
    target.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
                      encoding="utf-8")


def check_transform_receipt(path):
    """I2 boundary: validate schema, authority, fresh contracts and file SHA chain."""
    if not PIPELINE_READY:
        raise Held("i1_design_disposition_required")
    rec = _json(path)
    if (rec.get("schema") != SCHEMA or rec.get("status") != "pass"
            or rec.get("stage") != "transform" or rec.get("profile") != PROFILE
            or rec.get("major_id") != "hort_env_systems" or rec.get("module_version") != "0.2.0"
            or rec.get("source_sha256") != SOURCE_SHA):
        raise Held("transform_receipt_invalid")
    if rec.get("transform_map_sha256") != file_digest(REF / FILES[0]) or rec.get("param_registry_sha256") != file_digest(REF / FILES[1]):
        raise Held("transform_receipt_stale")
    prior_path = rec.get("previous_receipt_path")
    project_root = rec.get("project_root")
    if not isinstance(prior_path, str) or not isinstance(project_root, str):
        raise Held("transform_receipt_invalid")
    if file_digest(prior_path) != rec.get("previous_receipt_sha256"):
        raise Held("transform_receipt_stale")
    prior = _json(prior_path)
    if (prior.get("schema") != SCHEMA or prior.get("stage") != "plan"
            or prior.get("status") != "pass"):
        raise Held("transform_receipt_invalid")
    for field in ("profile", "major_id", "module_version", "project_revision",
                  "source_sha256", "transform_map_sha256", "param_registry_sha256",
                  "params_sha256", "canonical_facts_sha256", "major_authorization"):
        if prior.get(field) != rec.get(field):
            raise Held("transform_receipt_stale")
    plan_inputs = prior.get("input_artifacts")
    transform_inputs = rec.get("input_artifacts")
    if (not isinstance(plan_inputs, list) or len(plan_inputs) != 1
            or not isinstance(transform_inputs, list) or len(transform_inputs) != 1
            or not isinstance(plan_inputs[0], dict)
            or plan_inputs != transform_inputs
            or plan_inputs[0].get("sha256") != SOURCE_SHA):
        raise Held("transform_receipt_invalid")
    import gg_core
    project = gg_core.load(project_root)
    if project["revision"] != rec.get("project_revision"):
        raise Held("transform_receipt_stale")
    if _fact_snapshot(project)[1] != rec.get("canonical_facts_sha256"):
        raise Held("transform_receipt_stale")
    if _authorize(project_root).to_dict() != rec.get("major_authorization"):
        raise Held("transform_receipt_stale")
    outputs = rec.get("output_artifacts")
    if (not isinstance(outputs, list) or len(outputs) != 1
            or not re.fullmatch(r"[a-f0-9]{64}", rec.get("previous_receipt_sha256") or "")):
        raise Held("transform_receipt_invalid")
    for entry in transform_inputs + outputs:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise Held("transform_receipt_invalid")
        try:
            if (file_digest(entry["path"]) != entry.get("sha256")
                    or Path(entry["path"]).stat().st_size != entry.get("size")):
                raise Held("transform_receipt_stale")
        except OSError as exc:
            raise Held("transform_receipt_stale") from exc
    if Path(outputs[0]["path"]).resolve() == Path(transform_inputs[0]["path"]).resolve():
        raise Held("transform_receipt_invalid")
    return rec


def _native_caches(path):
    """{sheet: {ref: (t_attr, cached_value, has_formula)}} from a recalculated workbook."""
    out = {}
    with zipfile.ZipFile(path) as z:
        shared = load_shared(z)
        for name, spath in workbook_sheets(z):
            root = ET.fromstring(z.read(spath))
            cells = {}
            for c in root.iter(local("c")):
                ref = c.attrib.get("r")
                if not ref:
                    continue
                v = c.find(local("v"))
                value = text_value(c, shared)
                cells[ref] = (c.attrib.get("t"), value,
                              c.find(local("f")) is not None, v is not None)
            out[name] = cells
    return out


def _sheet_picture_count(native_path, sheet_name):
    """(count, ok): picture anchors reachable from sheet_name's drawing rels.

    A <drawing> element whose rels entry or target is missing is a broken
    graft — reported as not-ok so the caller can refuse rather than treat
    an undecodable structure as a pass.
    """
    count = 0
    ok = True
    with zipfile.ZipFile(native_path) as z:
        names = set(z.namelist())
        sheet_map = dict(workbook_sheets(z))
        sheet_path = sheet_map.get(sheet_name)
        if sheet_path is None:
            return 0, False
        root = ET.fromstring(z.read(sheet_path))
        drawing_rids = [d.get("{" + R + "}id")
                        for d in root.iter(local("drawing"))]
        if not drawing_rids:
            return 0, True
        rels_path = _rels_name(sheet_path)
        if rels_path not in names:
            return 0, False
        rels = ET.fromstring(z.read(rels_path))
        rel_map = {r.get("Id"): r.get("Target")
                   for r in rels.iter("{" + P + "}Relationship")}
        for rid in drawing_rids:
            target = rel_map.get(rid)
            if target is None:
                ok = False
                continue
            part = _resolve_rel_target(rels_path, target)
            if part not in names:
                ok = False
                continue
            d_root = ET.fromstring(z.read(part))
            for anchor in d_root:
                if anchor.tag == "{" + XDR + "}twoCellAnchor" \
                        or anchor.tag == "{" + XDR + "}oneCellAnchor" \
                        or anchor.tag == "{" + XDR + "}absoluteAnchor":
                    if anchor.find("{" + XDR + "}pic") is not None:
                        count += 1
    return count, ok


def _verify_cell_kind(row, cache, condition_value=None):
    """Registry result-kind vs native cached value.

    Input cells are covered by the fact-to-cache comparison, not by their
    source-state kind (``intentional_blank_nonformula`` describes the source
    layout, while the native workbook legitimately holds the written fact).

    'conditional_unused_na' rows only license #N/A or a blank cache while
    their registry condition is true (a listed group inactive).  When every
    condition group is active the cell must satisfy 'active_kind' — #N/A
    under active inputs fails closed.

    'active_kind' is a |-joined union of the cached-value shapes the
    registry generator proved the formula can yield: 'number' (finite
    numeric cache), 'string' (non-empty string cache), 'empty' (empty
    string or missing cache).  A cell that can legitimately render a label
    such as 개별 alongside a SUM therefore carries 'empty|number|string'
    instead of being forced into a single-kind straitjacket.
    """
    if row["action"] == "input_param":
        return
    sheet, ref, kind = row["sheet"], row["cell"], row["result_kind"]
    t, value, has_formula, has_v = cache if cache else (None, None, False, False)
    loc = f"{sheet}!{ref}"
    cond = condition_value if kind == "conditional_unused_na" else None
    if kind == "conditional_unused_na" and cond is None:
        raise Held("registry_condition_missing", loc)
    if t == "e" or (isinstance(value, str) and re.fullmatch(
            r"#(N/A|DIV/0!|VALUE!|REF!|NAME\?|NUM!|NULL!|SPILL!|CALC!|GETTING_DATA)", value)):
        if cond is True and value == "#N/A":
            return
        raise Held("verify_error_cache", loc)
    if cond is True:
        if value in (None, ""):
            return
        # A formula referring to an unused blank input can evaluate to
        # numeric zero in Excel, while another branch can still render a
        # literal.  active_kind applies when the condition is false.
        return
    if kind == "conditional_unused_na":
        kind = row.get("active_kind")
        if not (isinstance(kind, str) and _ACTIVE_KIND_RE.fullmatch(kind)):
            raise Held("verify_kind_unclassified", loc)
        tokens = set(kind.split("|"))
        if isinstance(value, bool):
            raise Held("verify_numeric_required", loc)
        if isinstance(value, (int, float, Decimal)):
            try:
                if Decimal(str(value)).is_finite() and "number" in tokens:
                    return
            except InvalidOperation:
                pass
            raise Held("verify_numeric_required", loc)
        if value in (None, ""):
            if "empty" in tokens:
                return
            raise Held("verify_empty_string_required", loc)
        if isinstance(value, str) and "string" in tokens:
            return
        raise Held("verify_display_required", loc)
    if kind == "numeric_required":
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise Held("verify_numeric_required", loc)
        try:
            if not Decimal(str(value)).is_finite():
                raise Held("verify_numeric_required", loc)
        except InvalidOperation:
            raise Held("verify_numeric_required", loc)
    elif kind == "display_string":
        if not isinstance(value, str) or not value:
            raise Held("verify_display_required", loc)
    elif kind == "normal_empty_string":
        if value != "":
            raise Held("verify_empty_string_required", loc)
    elif kind == "intentional_blank_nonformula":
        if has_formula or value not in (None, ""):
            raise Held("verify_blank_required", loc)
    elif kind == "error_forbidden":
        pass
    else:
        raise Held("verify_kind_unclassified", loc)


_PROD_RUN_RE = re.compile(
    r"((?:(?:'[^']+'!)?\$?[A-Z]{1,3}\$?[0-9]+\s*\*\s*)+"
    r"(?:'[^']+'!)?\$?[A-Z]{1,3}\$?[0-9]+)\s*/\s*1000")
_FACTOR_RE = re.compile(r"(?:'([^']+)'!)?\$?([A-Z]{1,3})\$?([0-9]+)")
_SUM_RE = re.compile(r"^=SUM\(([^()]*)\)$")
_REF_RE = re.compile(r"^=\$?([A-Z]{1,3})\$?([0-9]+)$")
_RANGE_RE = re.compile(r"^\$?([A-Z]{1,3})\$?([0-9]+):\$?([A-Z]{1,3})\$?([0-9]+)$")


def _product_eval(formula, caches, sheet):
    # Independent KRW_1000 check for the ref(*ref)+/1000 multiplication
    # family (per-unit cost formulas).  Two-or-more factors, each optionally
    # sheet-qualified.  Returns None when the formula is not in this family.
    m = _PROD_RUN_RE.search(formula or "")
    if not m:
        return None
    total = Decimal(1)
    for fm in _FACTOR_RE.finditer(m.group(1)):
        factor_sheet = fm.group(1) or sheet
        total *= _cache_num(caches, factor_sheet, f"{fm.group(2)}{fm.group(3)}")
    return total / Decimal(1000)


def _cache_num(caches, sheet, ref):
    entry = caches.get(sheet, {}).get(ref.replace("$", ""))
    if entry is None:
        return Decimal(0)
    t, value, _, _ = entry
    if t == "e":
        raise Held("verify_operand_error", f"{sheet}!{ref}")
    if value is None or isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return Decimal(0)


def _target_num(caches, sheet, ref):
    entry = caches.get(sheet, {}).get(ref)
    if entry is None:
        return None
    t, value, _, _ = entry
    if t == "e":
        return "#ERR"
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _loan_slot_rows(slot):
    param_row = (39, 73, 96, 119, 142)[slot - 1]
    count = 25 if slot == 1 else 15
    return param_row, range(param_row + 4, param_row + 4 + count)


def _verify_loan_identities(caches, start_year):
    """Compare cached, independently checked operands without output check rows."""
    sheet = "5. 원리금상환계획"

    def number(sh, ref, detail):
        value = _target_num(caches, sh, ref)
        if value == "#ERR" or value is None:
            raise Held("accounting_identity_mismatch", detail)
        return Decimal(str(value))

    opening = number("1. 자산조사", "E53", f"{sheet}!opening_debt")
    prior = Decimal(str(start_year - 1))
    balances = Decimal(0)
    for slot in range(1, 6):
        _, rows = _loan_slot_rows(slot)
        for r in rows:
            b = caches.get(sheet, {}).get(f"B{r}")
            if b is None or b[0] == "e" or b[1] is None:
                raise Held("accounting_identity_mismatch", f"{sheet}!opening_debt")
            if b[1] == "":
                continue
            if b[0] == "e":
                raise Held("accounting_identity_mismatch", f"{sheet}!opening_debt")
            try:
                year = Decimal(str(b[1]))
            except (ValueError, TypeError, InvalidOperation):
                raise Held("accounting_identity_mismatch", f"{sheet}!opening_debt")
            if year == prior:
                balances += number(sheet, f"C{r}", f"{sheet}!opening_debt")
                balances -= number(sheet, f"E{r}", f"{sheet}!opening_debt")
    if opening != balances:
        raise Held("accounting_identity_mismatch", f"{sheet}!opening_debt")
    for k in range(5):
        detail = f"{sheet}!funding_y{k+1}"
        left = number(sheet, f"C{8+k}", detail)
        right = number("4.투자계획", f"F{8+k}", detail)
        if left != right:
            raise Held("accounting_identity_mismatch", detail)


def _loan_schedule(principal, rate, grace, repay, method):
    """Expected per-year (balance, interest, principal_paid) for a supported loan."""
    principal, rate = Decimal(str(principal)), Decimal(str(rate))
    g, n = int(grace), int(repay)
    if method == "원리금균등" and rate > 0:
        factor = (Decimal(1) + rate) ** n
        payment = principal * rate * factor / (factor - 1)
    elif method == "원리금균등":
        payment = principal / n
    else:
        payment = None
    rows = []
    balance = principal
    for i in range(25):
        year_index = i + 1
        interest = balance * rate if balance > 0 else Decimal(0)
        if balance <= 0:
            principal_paid = Decimal(0)
        elif year_index <= g:
            principal_paid = Decimal(0)
        elif year_index >= g + n:
            principal_paid = balance
        elif method == "원리금균등":
            principal_paid = min(balance, max(Decimal(0), payment - interest))
        elif method == "원금균등":
            principal_paid = min(balance, principal / n)
        else:
            principal_paid = Decimal(0)
        rows.append((balance, interest, principal_paid))
        balance = max(Decimal(0), balance - principal_paid)
    return rows


def _label_eval(formula, caches, sheet):
    """Evaluate a concat-only label formula; None when the grammar is not covered."""
    if not isinstance(formula, str) or not formula.startswith("=") or "IF(" in formula:
        return None
    body = formula[1:].strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    terms, depth, in_str, cur = [], 0, False, ""
    for ch in body:
        if ch == '"':
            in_str = not in_str
        elif ch == "(" and not in_str:
            depth += 1
        elif ch == ")" and not in_str:
            depth -= 1
        if ch == "&" and not in_str and depth == 0:
            terms.append(cur)
            cur = ""
        else:
            cur += ch
    terms.append(cur)
    parts = []
    for term in terms:
        term = term.strip()
        while term.startswith("(") and term.endswith(")"):
            term = term[1:-1].strip()
        if not term:
            continue
        if term.startswith('"') and term.endswith('"'):
            parts.append(term[1:-1])
            continue
        m = re.fullmatch(r"TEXT\(([^,]+),\s*\"([^\"]*)\"\)", term)
        if m:
            ref, fmt = m.group(1), m.group(2)
            value = _label_ref(ref, caches, sheet)
            if value is None:
                return None
            try:
                if fmt in ("#,##0", "#,##0.00"):
                    places = 2 if fmt.endswith(".00") else 0
                    parts.append(f"{value:,.{places}f}")
                elif fmt == "0":
                    parts.append(str(int(value)))
                else:
                    return None
            except (ValueError, TypeError):
                return None
            continue
        m = re.fullmatch(r"(.+?)\s*\+\s*([0-9]+)", term)
        if m:
            base = _label_ref(m.group(1), caches, sheet)
            if base is None:
                return None
            value = base + int(m.group(2))
            parts.append(str(int(value)) if float(value) == int(value) else str(value))
            continue
        value = _label_ref(term, caches, sheet)
        if value is None:
            return None
        parts.append(str(int(value)) if isinstance(value, (int, float, Decimal))
                     and float(value) == int(value) else str(value))
    return "".join(parts)


def _label_ref(term, caches, sheet):
    m = re.fullmatch(r"(?:'([^']+)'!)?\$?([A-Z]{1,3})\$?([0-9]+)", term.strip())
    if not m:
        return None
    target = m.group(1) or sheet
    entry = caches.get(target, {}).get(f"{m.group(2)}{m.group(3)}")
    if entry is None:
        return None
    _, value, _, _ = entry
    return value


def _check_native_receipt(path, trec, trec_path, project, fact_sha, auth):
    """Validate the manifest published by the guarded gg_office Excel path.

    The Office manifest itself is the native receipt.  It binds the actual
    engine invocation, transformed input, native workbook and exported PDF;
    a lane-written substitute is not an accepted production proof.
    """
    rec = _json(path)
    val = rec.get("validation")
    if (rec.get("status") != "converted" or rec.get("engine") != "Microsoft Excel"
            or not isinstance(rec.get("engine_result"), str)
            or not rec["engine_result"].strip() or not isinstance(val, dict)
            or val.get("finance_profile") != PROFILE
            or trec.get("canonical_facts_sha256") != fact_sha
            or trec.get("project_revision") != project["revision"]
            or rec.get("major_authorization") != [auth.to_dict()]):
        raise Held("native_receipt_invalid")
    binding = val.get("template_receipt")
    expected_input = (trec.get("output_artifacts") or [{}])[0]
    trec_sha = file_digest(trec_path)
    if (not isinstance(binding, dict)
            or binding.get("authority") != "hort_transform_receipt"
            or binding.get("sha256") != trec_sha
            or binding.get("input_sha256") != expected_input.get("sha256")
            or not isinstance(binding.get("path"), str)
            or Path(binding["path"]).resolve() != Path(trec_path).resolve()
            or val.get("previous_receipt_sha256") != trec_sha):
        raise Held("native_receipt_stale")
    input_path = rec.get("input_file")
    if (not isinstance(input_path, str)
            or Path(input_path).resolve() != Path(expected_input.get("path", "")).resolve()
            or rec.get("input_sha256") != expected_input.get("sha256")):
        raise Held("native_receipt_stale")
    native_path, pdf_path = rec.get("published_office"), rec.get("published_pdf")
    if (not isinstance(native_path, str) or not isinstance(pdf_path, str)
            or Path(native_path).resolve().parent != Path(path).resolve().parent
            or Path(pdf_path).resolve().parent != Path(path).resolve().parent):
        raise Held("native_receipt_invalid")
    try:
        if (file_digest(input_path) != rec["input_sha256"]
                or file_digest(native_path) != rec.get("published_office_sha256")
                or file_digest(pdf_path) != rec.get("published_pdf_sha256")):
            raise Held("native_receipt_stale")
    except OSError as exc:
        raise Held("native_receipt_stale") from exc
    meta_xlsx, meta_pdf = val.get("excel"), val.get("pdf")
    coverage = val.get("native_pdf_coverage")
    if (not isinstance(meta_xlsx, dict) or not isinstance(meta_pdf, dict)
            or not isinstance(coverage, dict)
            or meta_xlsx.get("valid") is not True or meta_pdf.get("valid") is not True
            or meta_xlsx.get("sha256") != rec["published_office_sha256"]
            or meta_pdf.get("sha256") != rec["published_pdf_sha256"]
            or meta_xlsx.get("size_bytes") != Path(native_path).stat().st_size
            or meta_pdf.get("size_bytes") != Path(pdf_path).stat().st_size
            or not isinstance(meta_pdf.get("pages"), int)
            or meta_pdf["pages"] < 18
            or coverage.get("worksheet_count") != 18
            or coverage.get("pdf_pages") != meta_pdf["pages"]
            or coverage.get("status") != "page_count_floor_met"
            or val.get("structure_issues") != []
            or val.get("school_issues") != []):
        raise Held("native_receipt_invalid")
    # Re-read both published formats, rather than trusting a copied manifest.
    import gg_office
    try:
        checked_xlsx = gg_office.validate_office_file(Path(native_path), "excel")
        checked_pdf = gg_office.validate_pdf_file(Path(pdf_path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise Held("native_artifact_invalid") from exc
    if checked_xlsx != meta_xlsx or checked_pdf != meta_pdf:
        raise Held("native_receipt_stale")
    return rec, native_path, pdf_path


def _independent_model(source, transform, params, values):
    """Build expected formulas from H01 and the public map, never native caches."""
    from gg_hort_formula_eval import Evaluator, Parser, normalize_ref
    formulas, defaults, inputs = {}, {}, {}
    original_cells = {}
    with zipfile.ZipFile(source) as z:
        shared = load_shared(z)
        for sheet, spath in workbook_sheets(z):
            dom, cells, _, _ = _sheet_structure(z.read(spath), spath)
            _expand_shared(dom)
            original_cells[sheet] = cells
            for ref, cell in cells.items():
                coord = (sheet, ref)
                f = _direct(cell, "f")
                if f is not None:
                    formula = "=" + _text(f)
                    if f.getAttribute("t") == "array" and f.getAttribute("ref"):
                        ast = Parser(formula).parse()
                        if (ast[0] == "binary" and ast[1] == ":"
                                and ast[2][0] == "ref" and ast[3][0] == "ref"):
                            s1, a = normalize_ref(ast[2][1], sheet)
                            s2, b = normalize_ref(ast[3][1], s1)
                            targets = expand_ref(f.getAttribute("ref"))
                            source_refs = expand_ref(a + ":" + b)
                            if s1 == s2 and len(targets) == len(source_refs):
                                quoted = s1.replace("'", "''")
                                for target, source_ref in zip(targets, source_refs):
                                    formulas[(sheet, target)] = f"='{quoted}'!{source_ref}"
                                continue
                        # A scalar or unsupported array stays evaluable only
                        # at its anchor; a registered numeric member without
                        # a derivable formula is refused below.
                        formulas[coord] = formula
                    else:
                        formulas[coord] = formula
                    continue
                try:
                    raw = _original_value(cell, shared)
                except Held:
                    continue
                if cell.getAttribute("t") in ("s", "str", "inlineStr"):
                    defaults[coord] = raw
                else:
                    try:
                        defaults[coord] = float(Decimal(raw))
                    except (InvalidOperation, ValueError):
                        defaults[coord] = raw
    for e in transform["entries"]:
        coord = (e["sheet"], e["cell"])
        formulas.pop(coord, None)
        defaults.pop(coord, None)
        if e.get("after_formula"):
            formulas[coord] = e["after_formula"]
        elif e["action"] == "input_param":
            key = e["param_key"]
            value = values.get(key)
            if value is not None:
                if params[key].get("unit_code") == "DATE":
                    inputs[coord] = float(_serial(value))
                elif isinstance(value, (int, Decimal)) and not isinstance(value, bool):
                    inputs[coord] = float(value)
                else:
                    inputs[coord] = value
        elif e["action"] == "label_template":
            kind, payload = _label(e, original_cells, shared, values, params)
            if kind == "formula":
                formulas[coord] = payload
            else:
                inputs[coord] = payload
    actions = [{"sheet": sh, "cell": ref, "after_formula": formula}
               for (sh, ref), formula in formulas.items()]
    return Evaluator(actions, inputs=inputs, defaults=defaults), formulas


def _verify_independent_formulas(source, docs, params, values, registry,
                                 caches, cond_by_coord, counts):
    """Evaluate every native numeric formula from facts and static formulas."""
    from gg_hort_formula_eval import FormulaError, UnsupportedError
    model, formulas = _independent_model(source, docs[FILES[0]], params, values)
    for row in registry:
        sheet, ref = row["sheet"], row["cell"]
        coord = (sheet, ref)
        if (row.get("expected_state") == "formula"
                and row["result_kind"] in ("numeric_required", "conditional_unused_na")
                and coord not in formulas):
            raise Held("verify_independent_unmodeled", f"{sheet}!{ref}")
        if coord not in formulas:
            continue
        kind = row["result_kind"]
        if kind not in ("numeric_required", "conditional_unused_na"):
            continue
        cache = caches.get(sheet, {}).get(ref)
        actual = cache[1] if cache else None
        try:
            expected = model.cell(sheet, ref)
        except FormulaError as exc:
            if (kind == "conditional_unused_na" and exc.args == ("#N/A",)
                    and cache and cache[0] == "e" and actual == "#N/A"):
                counts["non_numeric_conditional"] += 1
                continue
            raise Held("verify_independent_unresolved", f"{sheet}!{ref}") from exc
        except (UnsupportedError, RecursionError, OverflowError,
                ZeroDivisionError) as exc:
            raise Held("verify_independent_unresolved", f"{sheet}!{ref}") from exc
        if not isinstance(expected, (int, float, Decimal)) or isinstance(expected, bool):
            if kind == "conditional_unused_na" and actual == expected:
                counts["non_numeric_conditional"] += 1
                continue
            raise Held("verify_independent_non_numeric", f"{sheet}!{ref}")
        if not isinstance(actual, (int, float, Decimal)) or isinstance(actual, bool):
            raise Held("verify_independent_non_numeric", f"{sheet}!{ref}")
        unit = row.get("unit")
        if unit in (None, "UNKNOWN", "TEXT"):
            raise Held("verify_unit_unclassified", f"{sheet}!{ref}")
        try:
            compare_raw_cache(expected, actual, unit)
        except Held as exc:
            raise Held(exc.code, f"{sheet}!{ref}") from exc
        counts["independent_formula_checked"] += 1


def verify(project_root, source, transform_receipt, native_receipt, receipt_path):
    """plan→transform→native chain validation + registry cache + oracle checks."""
    if not PIPELINE_READY:
        raise Held("i1_design_disposition_required")
    docs, project, auth, values, derivations, fact_sha = _load_context(project_root, source)
    trec = check_transform_receipt(transform_receipt)
    nrec, native_path, pdf_path = _check_native_receipt(
        native_receipt, trec, transform_receipt, project, fact_sha, auth)
    caches = _native_caches(native_path)
    registry = docs[FILES[2]]["registry"]
    entries = {(e["sheet"], e["cell"]): e for e in docs[FILES[0]]["entries"]}
    params = docs[FILES[1]]["parameters"]
    counts = Counter()
    present = _declared_keys(project, params)
    cond_by_coord = {}
    for row in registry:
        loc = f"{row['sheet']}!{row['cell']}"
        cond_by_coord[(row["sheet"], row["cell"])] = _condition_value(
            row, params, present, values, loc)
    for row in registry:
        cache = caches.get(row["sheet"], {}).get(row["cell"])
        _verify_cell_kind(row, cache, cond_by_coord.get((row["sheet"], row["cell"])))
        counts["kind_checked"] += 1
    for row in registry:
        if row["action"] != "input_param":
            continue
        entry = entries.get((row["sheet"], row["cell"]))
        pkey = entry.get("param_key") if entry else None
        if not pkey or pkey not in params:
            raise Held("verify_param_unmapped", f"{row['sheet']}!{row['cell']}")
        spec = params[pkey]
        value = values.get(pkey)
        cache = caches.get(row["sheet"], {}).get(row["cell"])
        t, cached, _, _ = cache if cache else (None, None, False, False)
        if t == "e":
            raise Held("verify_error_cache", f"{row['sheet']}!{row['cell']}")
        if value is None:
            if cached not in (None, ""):
                raise Held("verify_blank_required", f"{row['sheet']}!{row['cell']}")
        elif spec.get("unit_code") == "DATE":
            compare_raw_cache(_serial(value), cached, "DATE")
        elif spec.get("data_type") == "string" or isinstance(value, str):
            if cached != value:
                raise Held("verify_input_mismatch", f"{row['sheet']}!{row['cell']}")
        else:
            expected = _serial(value) if spec.get("unit_code") == "DATE" else value
            unit = spec.get("unit_code")
            if unit in ("TEXT", "EVIDENCE_REF"):
                if str(cached) != str(expected):
                    raise Held("verify_input_mismatch", f"{row['sheet']}!{row['cell']}")
            elif unit not in ("KRW_1000", "RATIO", "COUNT", "YEAR", "YEARS", "MONTH",
                              "SALES_UNIT", "ITEM_UNIT", "HOUR", "M2", "HOUR_PER_DAY",
                              "CHARGE_UNIT_PER_SALES_UNIT", "DATE") \
                    and not str(unit or "").startswith("KRW_PER_"):
                if str(cached) != str(expected):
                    raise Held("verify_input_mismatch", f"{row['sheet']}!{row['cell']}")
            else:
                compare_raw_cache(expected, cached, unit)
        counts["input_checked"] += 1
    # Teacher-example residue: any cell flagged as an H01 teaching example
    # that stayed outside the map must not keep its original value in the
    # printed output.  Mapped targets are already covered by the input
    # comparisons above; this trips when an example cell loses its
    # disposition (or a new one is left unmapped).
    originals = {}
    unmapped_coords = [coord for coord in
                       docs[FILES[2]].get("teacher_example_coords") or []
                       if (coord["sheet"], coord["cell"]) not in entries]
    if unmapped_coords:
        with zipfile.ZipFile(source) as z:
            shared = load_shared(z)
            sheet_paths = dict(workbook_sheets(z))
            for coord in unmapped_coords:
                sn, ref = coord["sheet"], coord["cell"]
                if sn not in originals:
                    spath = sheet_paths.get(sn)
                    if spath is None:
                        raise Held("teacher_example_sheet", f"{sn}!{ref}")
                    root = ET.fromstring(z.read(spath))
                    originals[sn] = {
                        c.get("r"): text_value(c, shared)
                        for c in root.iter(local("c"))}
                expected = originals[sn].get(ref)
                t, cached, _, _ = caches.get(sn, {}).get(
                    ref, (None, None, False, False))
                if expected is not None and _example_equal(expected, cached):
                    raise Held("teacher_example_residue", f"{sn}!{ref}")
                counts["teacher_example_checked"] += 1
    # Teacher images: the map's label entries at the removed anchor origins
    # must be present and the sheet must carry no picture anchors.  Fail
    # closed when the labels or the drawing graft is missing.
    image_label_entries = [e for e in entries.values()
                           if e["sheet"] == _TEACHER_IMAGE_SHEET
                           and e.get("after_value") == _TEACHER_IMAGE_LABEL]
    if len(image_label_entries) != 2:
        raise Held("teacher_image_label_missing", _TEACHER_IMAGE_SHEET)
    for e in image_label_entries:
        t, cached, _, _ = caches.get(e["sheet"], {}).get(
            e["cell"], (None, None, False, False))
        if cached != _TEACHER_IMAGE_LABEL:
            raise Held("teacher_image_label_missing", f"{e['sheet']}!{e['cell']}")
        counts["teacher_image_label_checked"] += 1
    pic_count, drawing_ok = _sheet_picture_count(native_path, _TEACHER_IMAGE_SHEET)
    if not drawing_ok:
        raise Held("teacher_image_verify_error", _TEACHER_IMAGE_SHEET)
    if pic_count != 0:
        raise Held("teacher_image_residue", _TEACHER_IMAGE_SHEET)
    counts["teacher_image_checked"] += 1
    start_year = values.get("plan.start_year")
    if not isinstance(start_year, int) or isinstance(start_year, bool):
        raise Held("start_year_missing", "plan.start_year")
    for slot in range(1, 6):
        prefix = f"loan.slot{slot}."
        if not any(k.startswith(prefix) and v is not None for k, v in values.items()):
            continue
        principal = Decimal(str(values[prefix + "principal_krw1000"]))
        rate = Decimal(str(values[prefix + "annual_rate"]))
        schedule = _loan_schedule(principal, rate, values[prefix + "grace_years"],
                                  values[prefix + "repayment_years"], values[prefix + "method"])
        _, rows = _loan_slot_rows(slot)
        sheet = "5. 원리금상환계획"
        for i, r in enumerate(rows):
            balance, interest, principal_paid = schedule[i]
            for col, expected in (("C", balance), ("D", interest),
                                  ("E", principal_paid), ("F", interest + principal_paid)):
                actual = _target_num(caches, sheet, f"{col}{r}")
                if actual == "#ERR":
                    raise Held("verify_error_cache", f"{sheet}!{col}{r}")
                if actual is None:
                    raise Held("verify_oracle_blank", f"{sheet}!{col}{r}")
                compare_raw_cache(expected, actual, "KRW_1000")
            counts["loan_rows"] += 1
    for row in registry:
        entry = entries.get((row["sheet"], row["cell"]))
        formula = (entry or {}).get("after_formula") or ""
        sheet, ref = row["sheet"], row["cell"]
        expected = None
        try:
            if _PROD_RUN_RE.search(formula):
                expected = _product_eval(formula, caches, sheet)
            else:
                m = _SUM_RE.match(formula)
                if m:
                    total = Decimal(0)
                    for part in m.group(1).split(","):
                        part = part.strip()
                        rm = _RANGE_RE.match(part)
                        if rm:
                            for cell_ref in expand_ref(f"{rm.group(1)}{rm.group(2)}:{rm.group(3)}{rm.group(4)}"):
                                total += _cache_num(caches, sheet, cell_ref)
                        elif re.fullmatch(r"\$?[A-Z]{1,3}\$?[0-9]+", part):
                            total += _cache_num(caches, sheet, part)
                        else:
                            expected = None
                            break
                    else:
                        expected = total
                elif _REF_RE.match(formula):
                    m2 = _REF_RE.match(formula)
                    expected = _cache_num(caches, sheet, f"{m2.group(1)}{m2.group(2)}")
        except Held as exc:
            if exc.code == "verify_operand_error" and cond_by_coord.get(
                    (row["sheet"], row["cell"])) is True:
                continue
            raise
        if expected is None:
            continue
        actual = _target_num(caches, sheet, ref)
        if actual == "#ERR":
            if cond_by_coord.get((row["sheet"], row["cell"])) is True:
                continue
            raise Held("verify_error_cache", f"{sheet}!{ref}")
        if actual is None:
            if cond_by_coord.get((row["sheet"], row["cell"])) is True:
                continue
            raise Held("verify_oracle_blank", f"{sheet}!{ref}")
        unit = row.get("unit")
        if unit not in ("KRW_1000", "RATIO", "COUNT", "YEAR", "YEARS", "MONTH",
                        "SALES_UNIT", "ITEM_UNIT", "HOUR", "M2") \
                and not str(unit or "").startswith("KRW_PER_"):
            unit = "KRW_1000"
        compare_raw_cache(expected, actual, unit)
        counts["oracle_checked"] += 1
    _verify_independent_formulas(source, docs, params, values, registry,
                                 caches, cond_by_coord, counts)
    sheet17 = "17. 추정대차대조표"
    for col in "CDEFGH":
        left = _target_num(caches, sheet17, f"{col}30")
        right = _target_num(caches, sheet17, f"{col}46")
        if left == "#ERR" or right == "#ERR" or left is None or right is None:
            raise Held("accounting_identity_mismatch", f"{sheet17}!{col}30")
        check_identity(left, right)
        counts["identity"] += 1
    _verify_loan_identities(caches, start_year)
    counts["identity"] += 6
    for row in registry:
        if row["result_kind"] != "display_string":
            continue
        entry = entries.get((row["sheet"], row["cell"]))
        formula = (entry or {}).get("after_formula") or ""
        if "$I$2" not in formula:
            continue
        expected = _label_eval(formula, caches, row["sheet"])
        if expected is None:
            continue
        cached = caches.get(row["sheet"], {}).get(row["cell"], (None, None, False, False))[1]
        if cached != expected:
            raise Held("year_label_mismatch", f"{row['sheet']}!{row['cell']}")
        counts["label_checked"] += 1
    # Label placeholders must never reach the student workbook: every cached
    # string in the native output is scanned for unresolved {token} residue.
    for sheet_name, cells in caches.items():
        for ref, entry in cells.items():
            value = entry[1]
            if isinstance(value, str) and _LABEL_TOKEN_RE.search(value):
                raise Held("label_token_residual", f"{sheet_name}!{ref}")
            counts["token_scanned"] += 1
    rec = _receipt("verify", project, auth, source, docs, fact_sha, values,
                   native_receipt, project_root)
    rec["input_artifacts"] = [_artifact(transform_receipt), _artifact(native_receipt),
                              _artifact(native_path), _artifact(pdf_path)]
    rec["verify_counts"] = dict(counts)
    _write_receipt(receipt_path, rec)
    return rec


def _authorize(project_root):
    import gg_major_contract as mc
    context = mc.OutputContext(project_root, "hort_env_systems")
    auth = mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
    mc.require_finance_profile(auth, PROFILE)
    if auth.module_version != "0.2.0":
        raise Held("module_version_mismatch")
    return auth


def _load_context(project_root, source):
    import gg_core
    docs = contracts()
    project = gg_core.load(project_root)
    auth = _authorize(project_root)
    if auth.project_revision != project["revision"]:
        raise Held("project_revision_race")
    values, derivations, fact_sha = _facts(project, docs[FILES[1]]["parameters"])
    if file_digest(source) != SOURCE_SHA:
        raise Held("source_hash_mismatch")
    return docs, project, auth, values, derivations, fact_sha


def plan(project_root, source, receipt_path):
    if not PIPELINE_READY:
        raise Held("i1_design_disposition_required")
    docs, project, auth, values, derivations, fact_sha = _load_context(project_root, source)
    rec = _receipt("plan", project, auth, source, docs, fact_sha, values,
                   project_root=project_root)
    rec["derivations"] = derivations
    _write_receipt(receipt_path, rec)
    return rec


def transform(project_root, source, plan_receipt, out, receipt_path):
    if not PIPELINE_READY:
        raise Held("i1_design_disposition_required")
    docs, project, auth, values, derivations, fact_sha = _load_context(project_root, source)
    prior = _json(plan_receipt)
    expected = _receipt("plan", project, auth, source, docs, fact_sha, values,
                        project_root=project_root)
    compare = ("schema", "status", "stage", "profile", "major_id", "module_version",
               "project_revision", "source_sha256", "transform_map_sha256",
               "param_registry_sha256", "params_sha256", "canonical_facts_sha256",
               "input_artifacts", "major_authorization", "project_root")
    if any(prior.get(k) != expected.get(k) for k in compare) or prior.get("derivations") != derivations:
        raise Held("plan_receipt_stale")
    if Path(out).exists() or Path(receipt_path).exists() or Path(out).resolve() == Path(source).resolve():
        raise Held("output_exists_or_source")
    payload, counts = _zip_transform(source, docs[FILES[0]], values,
                                     docs[FILES[1]]["parameters"])
    Path(out).write_bytes(payload)
    rec = _receipt("transform", project, auth, source, docs, fact_sha, values,
                   plan_receipt, project_root)
    rec["output_artifacts"] = [_artifact(out)]
    rec["transition_counts"] = counts
    rec["derivations"] = derivations
    _write_receipt(receipt_path, rec)
    return rec


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "transform", "verify", "audit-public"):
        p = sub.add_parser(name)
        if name in ("plan", "transform"):
            p.add_argument("--project-root", required=True)
            p.add_argument("--source", required=True)
            p.add_argument("--receipt", required=True)
        if name == "transform":
            p.add_argument("--plan-receipt", required=True)
            p.add_argument("--out", required=True)
        if name == "verify":
            p.add_argument("--project-root", required=True)
            p.add_argument("--source", required=True)
            p.add_argument("--transform-receipt", required=True)
            p.add_argument("--native-receipt", required=True)
            p.add_argument("--receipt", required=True)
        if name == "audit-public":
            p.add_argument("--source")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan(args.project_root, args.source, args.receipt)
        elif args.command == "transform":
            transform(args.project_root, args.source, args.plan_receipt, args.out, args.receipt)
        elif args.command == "verify":
            verify(args.project_root, args.source, args.transform_receipt,
                   args.native_receipt, args.receipt)
        elif args.command == "audit-public":
            if not args.source:
                raise Held("source_audit_held_no_H01")
            errors = audit_public_documents({name: _json(REF / name) for name in FILES}, args.source)
            if errors:
                for item in errors[:30]:
                    print(item["code"] + ":" + item["path"], file=sys.stderr)
                return 1
    except (Held, OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        print("HELD:", exc.code if isinstance(exc, Held) else "invalid_or_unreadable_input", file=sys.stderr)
        return 2
    print("PASS:", args.command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
