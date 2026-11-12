#!/usr/bin/env python3
"""Fail-closed OOXML format probe for school manuscript outputs.

This checker supports DOCX files generated with the school body bookmark. It measures the
DOCX package as XML (including style inheritance) and never rewrites it.
"""
from __future__ import annotations
import argparse, hashlib, json, posixpath, re, sys
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
QN = lambda local: "{%s}%s" % (W, local)
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
A4 = (11906, 16838)  # twips, 210 x 297 mm
PAGE_MAR_KEYS = ("top", "right", "bottom", "left", "header", "footer", "gutter")
# The school instruction's margin rows (OG-006~OG-012) and the page-number form
# row (OG-125) stay shared requirements: the food-crops checks cite them in
# ``related`` and never reuse their ids as their own check key.
MARGIN_RELATED = {"top": "OG-006", "bottom": "OG-007", "header": "OG-008",
                  "footer": "OG-009", "left": "OG-010", "right": "OG-011",
                  "gutter": "OG-012"}
PAGE_NUMBER_RELATED = ("OG-125",)

class ProbeError(ValueError):
    pass

def attr(node, name, default=None):
    if node is None:
        return default
    if isinstance(node, dict):
        return node.get(QN(name), node.get(name, default))
    return node.attrib.get(QN(name), default)

def text_of(p):
    # Preserve text order, but ignore field instructions and drawing metadata.
    return "".join(x.text or "" for x in p.iter(QN("t"))).replace("\u00a0", " ")

def props(node, children):
    out = {}
    if node is None:
        return out
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1]
        if local in children:
            out[local] = dict(child.attrib)
    return out

def merge_props(base, overlay):
    out = {k: dict(v) for k, v in base.items()}
    for k, v in overlay.items():
        old = out.get(k, {})
        old.update(v)
        out[k] = old
    return out

class Styles:
    def __init__(self, root):
        self.styles = {}
        self.default_para = None
        self.defaults_p = {}
        self.defaults_r = {}
        dd = root.find(QN("docDefaults"))
        if dd is not None:
            self.defaults_p = props(dd.find("./" + QN("pPrDefault") + "/" + QN("pPr")), {"spacing", "ind", "jc"})
            self.defaults_r = props(dd.find("./" + QN("rPrDefault") + "/" + QN("rPr")), {"sz", "rFonts", "b", "i", "bCs", "szCs"})
        for st in root.findall(QN("style")):
            sid = attr(st, "styleId")
            if not sid:
                continue
            self.styles[sid] = {
                "based": attr(st.find(QN("basedOn")), "val"),
                "p": props(st.find(QN("pPr")), {"spacing", "ind", "jc"}),
                "outline": attr(st.find("./" + QN("pPr") + "/" + QN("outlineLvl")), "val"),
                "r": props(st.find(QN("rPr")), {"sz", "rFonts", "b", "i", "bCs", "szCs"}),
                "name": attr(st.find(QN("name")), "val"),
                "type": attr(st, "type"),
            }
            if attr(st, "default") == "1" and attr(st, "type") == "paragraph":
                self.default_para = sid

    def chain(self, sid):
        seen = set(); result = []
        while sid and sid not in seen:
            seen.add(sid)
            st = self.styles.get(sid)
            if st is None: break
            result.append((sid, st)); sid = st["based"]
        return list(reversed(result))

    def paragraph(self, p):
        direct = p.find(QN("pPr"))
        sid = attr(direct.find(QN("pStyle")), "val") if direct is not None else None
        if sid is None:
            sid = self.default_para
        pprops = dict(self.defaults_p)
        rprops = dict(self.defaults_r)
        for _, st in self.chain(sid):
            pprops = merge_props(pprops, st["p"])
            rprops = merge_props(rprops, st["r"])
        pprops = merge_props(pprops, props(direct, {"spacing", "ind", "jc"}))
        st = self.styles.get(sid, {})
        outline = None
        # outlineLvl is style metadata, not a paragraph property in our merge set.
        for csid, cst in self.chain(sid):
            node = cst.get("outline")
            if node is not None: outline = node
        return sid, pprops, rprops, {"name": st.get("name"), "outline_level": outline}

    def run(self, p, base_r):
        values = []
        for r in p.iter(QN("r")):
            if not r.findall(".//" + QN("t")):
                continue
            rp = r.find(QN("rPr"))
            merged = dict(base_r)
            rstyle = attr(rp.find(QN("rStyle")), "val") if rp is not None and rp.find(QN("rStyle")) is not None else None
            for _, st in self.chain(rstyle):
                if st.get("type") == "character": merged = merge_props(merged, st["r"])
            values.append(merge_props(merged, props(rp, {"sz", "rFonts", "b", "i", "bCs", "szCs"})))
        return values

def is_heading(style_id, txt, rec=None):
    if rec is not None and (rec.get("outline_level") is not None or (rec.get("style_name") or "").lower().startswith("heading")):
        return True
    if style_id in {"1", "21", "31"}:
        return True
    return bool(re.match(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|\d+[.)]|[가-힣]\.)\s*", txt)) and len(txt) < 100

def heading_group(rec):
    lvl = rec.get("outline_level")
    name = (rec.get("style_name") or "").lower()
    if lvl == "0" or "heading 1" in name or rec.get("style_id") == "1":
        return "large"
    if lvl == "1" or "heading 2" in name or rec.get("style_id") == "21":
        return "middle"
    if lvl is not None or "heading" in name or rec.get("style_id") == "31":
        return "sub"
    txt = rec.get("text", "").strip()
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.", txt): return "large"
    if re.match(r"^\d+\.", txt): return "middle"
    return "sub"

def classify(txt, table, front_done, toc_done):
    if table:
        return "table"
    if not front_done:
        return "front"
    if not toc_done:
        if txt.strip() in {"목    차", "목차", "표 목차", "그림 목차"} or txt.strip().endswith(("목차", "목 차")):
            return "toc"
        # TOC rows have trailing page number and precede summary/body.
        if re.search(r"(?:[ⅠⅡⅢⅣⅤⅥ]+\.|\d+\.|[가-힣]\.)[^\n]{0,100}\d+$", txt.strip()):
            return "toc"
    return "body"

def walk_paragraphs(parent, context, out):
    """Walk paragraphs while retaining table/row/cell path."""
    for child in list(parent):
        local = child.tag.rsplit("}", 1)[-1]
        if local == "p":
            out.append((child, context))
        elif local == "tbl":
            ti = context.get("table_index", 0) + 1
            rows = child.findall("./" + QN("tr"))
            for ri, row in enumerate(rows, 1):
                cells = row.findall("./" + QN("tc"))
                for ci, cell in enumerate(cells, 1):
                    walk_paragraphs(cell, {"table": True, "table_index": ti, "row": ri, "cell": ci, "front": context.get("front", False)}, out)
        elif local == "tr":
            ti = context.get("table_index", 1)
            ri = context.get("row", 0) + 1
            for ci, cell in enumerate(child.findall("./" + QN("tc")), 1):
                walk_paragraphs(cell, {"table": True, "table_index": ti, "row": ri, "cell": ci, "front": context.get("front", False)}, out)
        elif local in {"sdtContent", "txbxContent", "customXml", "smartTag"}:
            walk_paragraphs(child, context, out)

def location(i, ctx):
    if ctx.get("table"):
        return f"document.xml:p{i} table#{ctx['table_index']} row#{ctx['row']} cell#{ctx['cell']}"
    return f"document.xml:p{i} body"

def paragraph_record(i, p, ctx, styles):
    txt = text_of(p)
    sid, pp, base_r, meta = styles.paragraph(p)
    runs = styles.run(p, base_r)
    size_values = sorted({attr(r.get("sz"), "val") for r in runs if r.get("sz") and attr(r.get("sz"), "val") is not None})
    fonts = sorted({f for f in (attr(r.get("rFonts"), "eastAsia") or attr(r.get("rFonts"), "ascii") or attr(r.get("rFonts"), "hAnsi") for r in runs if r.get("rFonts")) if f})
    bold_values = sorted({"on" if ("b" in r and attr(r.get("b"), "val", "true") not in {"0", "false", "off", "none"}) else "off" for r in runs})
    spacing = pp.get("spacing", {}); ind = pp.get("ind", {}); jc = pp.get("jc", {})
    return {
        "index": i, "location": location(i, ctx), "text": txt[:160],
        "bookmark_names": [attr(b, "name") for b in p.findall(".//" + QN("bookmarkStart")) if attr(b, "name")],
        "style_id": sid, "style_name": meta.get("name"), "outline_level": meta.get("outline_level"), "table": bool(ctx.get("table")),
        "size_half_points": size_values, "fonts_declared": fonts,
        "bold": bold_values,
        "line": attr(spacing, "line"), "line_rule": attr(spacing, "lineRule"),
        "first_line_twips": attr(ind, "firstLine"), "left_twips": attr(ind, "left"), "hanging_twips": attr(ind, "hanging"),
        "alignment": attr(jc, "val", "left"),
    }

def relationship_targets(archive, names):
    """Map document.xml relationship ids to package part names.

    Footer measurement needs the referenced footer part, and a dangling
    relationship must be visible as a measurement, not crash the probe.
    """
    part = "word/_rels/document.xml.rels"
    if part not in names:
        return {}
    try:
        root = ET.fromstring(archive.read(part))
    except ET.ParseError as e:
        raise ProbeError(f"invalid OOXML relationships: {e}") from e
    targets = {}
    for rel in root:
        ident, target = rel.attrib.get("Id"), rel.attrib.get("Target")
        if not ident or not target or rel.attrib.get("TargetMode") == "External":
            continue
        name = target.lstrip("/") if target.startswith("/") else posixpath.join("word", target)
        targets[ident] = posixpath.normpath(name)
    return targets


def footer_record(archive, names, targets, sect_pr, cache, inherited):
    """Measure one section's bottom footer without rewriting anything.

    A section without ``w:footerReference`` shows the footer of the section
    before it, so ``linked_to_previous`` reports that inheritance rather than
    claiming the first section inherits something.
    """
    refs = sect_pr.findall(QN("footerReference"))
    if not refs:
        return {"has_reference": False, "linked_to_previous": inherited, "page_fields": 0}
    default = next((r for r in refs if (attr(r, "type") or "default") == "default"), refs[0])
    part = targets.get(default.attrib.get(RID))
    record = {"has_reference": True, "linked_to_previous": False, "part": part,
              "page_fields": None}
    if part is None or part not in names:
        return record
    if part not in cache:
        try:
            root = ET.fromstring(archive.read(part))
        except ET.ParseError as e:
            raise ProbeError(f"invalid footer OOXML {part}: {e}") from e
        cache[part] = sum(1 for field in root.iter(QN("fldSimple"))
                          if (attr(field, "instr") or "").strip().upper().startswith("PAGE"))
    record["page_fields"] = cache[part]
    return record


def food_crops_expected_sections():
    """The food-crops DOCX form, written out independently of the builder.

    A probe that imported the builder's table could not detect a wrong table;
    these twips are the measured 양식 values (20/20/10 cover, 30/30/30 body).
    """
    cover = {"top": "1134", "right": "1134", "bottom": "567", "left": "1134",
             "header": "0", "footer": "567", "gutter": "0"}
    inner = {"top": "1701", "right": "1701", "bottom": "1701", "left": "1701",
             "header": "0", "footer": "567", "gutter": "0"}
    return [
        {"role": "cover", "pg_mar": dict(cover), "pg_num_type": None,
         "footer": {"has_reference": False, "page_fields": 0}},
        {"role": "front", "pg_mar": dict(inner),
         "pg_num_type": {"start": "1", "fmt": "upperRoman"},
         "footer": {"has_reference": True, "page_fields": 1}},
        {"role": "body", "pg_mar": dict(inner),
         "pg_num_type": {"start": "1", "fmt": "decimal"},
         "footer": {"has_reference": True, "page_fields": 1}},
    ]


def food_crops_violations(sections):
    """Compare measured sections with the food-crops form; report, never fix."""
    expected = food_crops_expected_sections()
    out = []

    def bad(code, related, location, want, got, message):
        out.append({"requirement_id": code, "code": code, "location": location,
                    "expected": want, "actual": got, "message": message,
                    "related": list(related), "probe_local": True})

    if len(sections) != len(expected):
        bad("food_crops.format.section.count", (),
            "document.xml:sectPr", len(expected), len(sections),
            "식량작물 구역이 겉표지·앞부분·본문 3개가 아님")
        return out
    for section, want in zip(sections, expected):
        role = want["role"]
        place = f"document.xml:sectPr#{section['index']}"
        for key in PAGE_MAR_KEYS:
            if section["pg_mar"][key] != want["pg_mar"][key]:
                bad(f"food_crops.format.margin.{role}_{key}", [MARGIN_RELATED[key]],
                    place, want["pg_mar"][key], section["pg_mar"][key],
                    f"{role} 구역 {key} 여백이 양식 값과 다름")
        if section["pg_num_type"] != want["pg_num_type"]:
            bad(f"food_crops.format.page_number.{role}", PAGE_NUMBER_RELATED, place,
                want["pg_num_type"], section["pg_num_type"],
                f"{role} 구역 pgNumType이 양식 값(start·fmt·개수)과 다름")
        footer = section["footer"]
        if want["footer"]["has_reference"] != footer["has_reference"]:
            bad(f"food_crops.format.footer.{role}", PAGE_NUMBER_RELATED, place,
                {"has_reference": want["footer"]["has_reference"]},
                {"has_reference": footer["has_reference"], "part": footer.get("part")},
                ("겉표지 구역에는 footerReference가 없어야 함(번호 미표시)"
                 if not want["footer"]["has_reference"] else
                 "이 구역에 footerReference가 있어야 함"))
        elif want["footer"]["page_fields"] != footer["page_fields"]:
            bad(f"food_crops.format.footer.{role}", PAGE_NUMBER_RELATED, place,
                {"page_fields": want["footer"]["page_fields"]},
                {"page_fields": footer["page_fields"], "part": footer.get("part")},
                "구역 바닥글에 PAGE 필드가 정확히 1개 있어야 함")
    return out


def check(path: Path, *, output_json: Path | None = None, major_id: str | None = None):
    if not path.is_file(): raise ProbeError(f"missing DOCX: {path}")
    if output_json is not None and output_json.resolve() == path.resolve():
        raise ProbeError("--out must differ from input DOCX (refusing overwrite)")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with ZipFile(path) as z:
            names = set(z.namelist())
            for required in ("word/document.xml", "word/styles.xml"):
                if required not in names: raise ProbeError(f"missing OOXML part: {required}")
            doc = ET.fromstring(z.read("word/document.xml"))
            styles = Styles(ET.fromstring(z.read("word/styles.xml")))
            # Section measurement needs footer parts, so it happens while the
            # archive is open; every value stays a string or bool for JSON.
            targets = relationship_targets(z, names)
            footer_cache = {}
            measured_sections = []
            seen_footer = False
            for si, sp in enumerate(doc.findall(".//" + QN("sectPr")), 1):
                mar = sp.find(QN("pgMar"))
                num = sp.find(QN("pgNumType"))
                footer = footer_record(z, names, targets, sp, footer_cache, seen_footer)
                seen_footer = seen_footer or footer["has_reference"]
                measured_sections.append({
                    "index": si,
                    "pg_mar": {key: attr(mar, key) for key in PAGE_MAR_KEYS},
                    "pg_num_type": (None if num is None else
                                    {"start": attr(num, "start"), "fmt": attr(num, "fmt")}),
                    "footer": footer,
                })
    except (BadZipFile, ET.ParseError) as e:
        raise ProbeError(f"invalid DOCX OOXML: {e}") from e
    body = doc.find(".//" + QN("body"))
    if body is None: raise ProbeError("missing w:body")
    # Frontmatter ends after the first three top-level tables; this mirrors the
    # accepted school sequence while leaving all measurement evidence visible.
    top_tables = [x for x in list(body) if x.tag == QN("tbl")]
    front_table_limit = min(3, len(top_tables))
    toc_done = False; front_done = False; records=[]; top_table_seen=0
    raw=[]
    for child in list(body):
        if child.tag == QN("tbl"):
            top_table_seen += 1
            walk_paragraphs(child, {"table": True, "table_index": top_table_seen, "front": top_table_seen <= front_table_limit}, raw)
            continue
        if child.tag == QN("p"):
            txt = text_of(child)
            raw.append((child, {"table": False, "front": False}))
            if txt.strip() and top_table_seen >= front_table_limit: front_done=True
    # Reclassify by the renderer's body bookmarks.  The pre-body run contains
    # cover/signature tables, TOC fields, and summary; ``gg_body_1`` is the
    # first actual chapter and is safer than guessing from page-number text.
    body_start = next((j for j,(p,_) in enumerate(raw,1) if any((attr(b, "name") or "") == "gg_body_1" for b in p.findall(".//" + QN("bookmarkStart")))), None)
    summary_start = next((j for j,(p,ctx) in enumerate(raw,1) if not ctx.get("table") and text_of(p).strip() == "요약"), None)
    structural_violations = []
    if body_start is None:
        structural_violations.append({"requirement_id":"OG-004","code":"body_bookmark_missing","location":"word/document.xml","expected":"bookmark gg_body_1","actual":None,"message":"본문 시작 bookmark가 없어 본문 서식 검사를 수행할 수 없음"})
    for i,(p,ctx) in enumerate(raw,1):
        stripped=text_of(p).strip()
        if ctx.get("table") and ctx.get("front"):
            kind="front"
        elif ctx.get("table"):
            kind="table"
        elif body_start is not None and i >= body_start:
            kind="body"
        elif summary_start is not None and i >= summary_start:
            kind="summary"
        else:
            kind="toc"
        rec=paragraph_record(i,p,ctx,styles); rec["kind"]=kind
        records.append(rec)
    # 식량작물 동의서는 본문 12 pt가 아니라 14 pt 굵게가 양식 값이다. 마지막
    # `학부모 동의서` 1단계 제목 뒤 문단을 별도 종류로 떼어 본문 판정에서
    # 빼지 않으면, 올바로 만든 산출물이 본문 크기 위반으로 잘못 걸린다.
    if major_id == "food_crops":
        consent_start = next(
            (r["index"] for r in reversed(records)
             if r["text"].strip() == "학부모 동의서"
             and is_heading(r["style_id"], r["text"].strip(), r)), None)
        if consent_start is not None:
            for rec in records:
                if rec["index"] > consent_start:
                    rec["kind"] = "consent"
    body_records=[r for r in records if body_start is not None and r["kind"]=="body" and r["text"].strip() and not is_heading(r["style_id"],r["text"].strip(),r) and not r["text"].strip().startswith(("표 ","그림 "))]
    if body_start is not None and not body_records:
        structural_violations.append({"requirement_id":"OG-005","code":"body_prose_missing","location":"word/document.xml","expected":"at least one body prose paragraph","actual":0,"message":"본문 시작 bookmark 뒤 산문 문단이 없어 본문 서식을 검증할 수 없음"})
        violations = structural_violations
    table_records=[r for r in records if r["kind"]=="table" and r["text"].strip()]
    summary_records=[r for r in records if r["kind"]=="summary" and r["text"].strip()]
    front_records=[r for r in records if r["kind"]=="front" and r["text"].strip()]
    toc_records=[r for r in records if r["kind"]=="toc" and r["text"].strip()]
    summary_prose=[r for r in summary_records if r["text"].strip() not in {"요약","목차","표 목차","그림 목차"} and not is_heading(r["style_id"], r["text"].strip(), r) and not r["text"].strip().startswith(("표 ","그림 "))]
    violations=list(structural_violations)
    def add(req, code, rec, expected, actual, message):
        violations.append({"requirement_id":req,"code":code,"location":rec["location"],"text":rec["text"],"expected":expected,"actual":actual,"message":message})
    # A4 check every section property, including paragraph-level section breaks.
    sections=[]
    for si,sp in enumerate(doc.findall(".//"+QN("sectPr")),1):
        pg=sp.find(QN("pgSz")); got=(attr(pg,"w"),attr(pg,"h"))
        record={"index":si,"size_twips":got,"a4":got==(str(A4[0]),str(A4[1]))}
        record.update(measured_sections[si-1])
        sections.append(record)
        if got != (str(A4[0]),str(A4[1])):
            violations.append({"requirement_id":"OG-002","code":"not_a4","location":f"document.xml:sectPr#{si}","expected":{"w":str(A4[0]),"h":str(A4[1])},"actual":{"w":got[0],"h":got[1]}})
    if not sections:
        violations.append({"requirement_id": "OG-002", "code": "page_size_missing", "location": "word/document.xml", "message": "용지 크기를 검증할 section 정보가 없음"})
    # The food-crops form is measured against its own namespace; ``None`` keeps
    # the probe generic so other majors never inherit these expectations.
    food_crops_format = None
    if major_id == "food_crops":
        food_crops_format = food_crops_violations(sections)
        violations.extend(food_crops_format)
    for rec in body_records:
        if rec["size_half_points"] != ["24"]:
            add("OG-005","body_size",rec,{"size_half_points":["24"]},rec["size_half_points"],"본문 run의 상속 계산 글자 크기가 12 pt가 아님")
        if rec["line"] != "384" or rec["line_rule"] != "auto":
            add("OG-004","line_spacing",rec,{"line":"384","line_rule":"auto"},{"line":rec["line"],"line_rule":rec["line_rule"]},"본문 줄간격이 160% (384 twips auto)가 아님")
        # Lists/references intentionally carry hanging indents; record them as
        # measured exceptions rather than misreporting them as prose failures.
        list_exception = rec["hanging_twips"] not in (None, "0") or rec["text"].lstrip().startswith(("•","- "))
        if not list_exception and rec["first_line_twips"] != "480":
            add("OG-013","first_line_indent",rec,{"first_line_twips":"480"},rec["first_line_twips"],"본문 문단 첫 줄이 2칸(480 twips)이 아님")
    align_counts=Counter(r["alignment"] for r in body_records)
    # The instruction says mixed alignment; preserve the actual distribution and
    # require both alignment for prose; rendered appearance is reviewed separately.
    alignment_bad=[r for r in body_records if r["alignment"] != "both"]
    for rec in alignment_bad:
        add("OG-014","alignment_not_both",rec,"both",rec["alignment"],"본문 산문 정렬이 양쪽 정렬(both)이 아님")
    # Heading declarations, with font installation deliberately not inferred.
    heading_records=[r for r in records if r["kind"]=="body" and not r["table"] and is_heading(r["style_id"],r["text"].strip(),r) and r["text"].strip()]
    heading_groups={"large":[],"middle":[],"sub":[]}
    for r in heading_records:
        g=heading_group(r)
        heading_groups[g].append(r)
    heading_summary={}
    for g,items in heading_groups.items():
        sizes=Counter(tuple(x["size_half_points"]) for x in items); fonts=Counter(tuple(x["fonts_declared"]) for x in items); bold=Counter(tuple(x["bold"]) for x in items)
        heading_summary[g]={"count":len(items),"size_half_points":dict((str(k),v) for k,v in sizes.items()),"fonts_declared":dict((str(k),v) for k,v in fonts.items()),"bold_declarations":dict((str(k),v) for k,v in bold.items()),"font_installation":"not_checked"}
    # A heading requirement is visual: report measured failures but do not claim
    # installed-font or rendered-bold status from OOXML alone.
    for g,req,allowed in (("large","OG-015",{"40"}), ("middle","OG-016",{"26","28","30","32","34","36"}), ("sub","OG-016",{"26","28","30","32","34","36"})):
        for rec in heading_groups[g]:
            if not rec["size_half_points"] or not set(rec["size_half_points"]).issubset(allowed): add(req,"heading_size",rec,sorted(allowed),rec["size_half_points"],"제목 크기가 지침 범위를 벗어남")
            if rec["fonts_declared"] != ["신명조"]: add(req,"heading_font",rec,"신명조",rec["fonts_declared"],"제목 선언 글꼴이 신명조가 아님")
            if rec["bold"] != ["on"]: add(req,"heading_bold",rec,"bold declaration",rec["bold"],"제목에 w:b 선언이 없음")
    # Body font (OG-017) includes inherited Normal style font.
    body_font_violations=[]
    for rec in body_records:
        if rec["fonts_declared"] != ["신명조"]: body_font_violations.append(rec)
    for rec in body_font_violations: add("OG-017","body_font",rec,"신명조",rec["fonts_declared"],"본문 선언 글꼴이 신명조가 아님")
    if not body_records:
        for req in ("OG-004", "OG-005", "OG-013", "OG-014", "OG-017"):
            if not any(v["requirement_id"] == req for v in violations):
                violations.append({"requirement_id": req, "code": "body_unavailable", "location": "word/document.xml", "message": "검사 가능한 본문 산문 없음"})
    machine={
      "OG-002":{"status":"pass" if sections and all(s["a4"] for s in sections) else "blocked","measured_sections":sections},
      "OG-004":{"status":"pass" if not any(v["requirement_id"]=="OG-004" for v in violations) else "blocked","body_paragraph_count":len(body_records)},
      "OG-005":{"status":"pass" if not any(v["requirement_id"]=="OG-005" for v in violations) else "blocked","body_paragraph_count":len(body_records)},
      "OG-013":{"status":"pass" if not any(v["requirement_id"]=="OG-013" for v in violations) else "blocked","body_paragraph_count":len(body_records),"list_or_reference_exceptions":sum(1 for r in body_records if r["left_twips"] is not None or r["hanging_twips"] is not None)},
      "OG-014":{"status":"pass" if not any(v["requirement_id"]=="OG-014" for v in violations) else "blocked","alignment_counts":dict(align_counts),"prose_alignment_expected":"both","mixed_alignment_observed":len(align_counts)>1,"interpretation":"본문 산문은 both(양쪽 정렬)를 요구; 분포와 혼합 여부를 보존하고 명시 없는 문단만 차단"},
    }
    visual={
      "OG-015":{"status":"blocked" if any(v["requirement_id"]=="OG-015" for v in violations) else "pass_with_notes","heading_group":"large","requirement":"신명조 20 pt 굵게","measurement":heading_summary["large"]},
      "OG-016":{"status":"blocked" if any(v["requirement_id"]=="OG-016" for v in violations) else "pass_with_notes","heading_groups":["middle","sub"],"measurement":{"middle":heading_summary["middle"],"sub":heading_summary["sub"]}},
      "OG-017":{"status":"blocked" if any(v["requirement_id"]=="OG-017" for v in violations) else "pass_with_notes","body_font":"신명조 declared; installation not_checked","body_paragraph_count":len(body_records)},
    }
    result={"schema":"gg-school-format-probe/v1","status":"blocked" if violations else "pass_with_notes","input":{"path":str(path),"sha256":sha},"parts":{"body_paragraphs":len(body_records),"table_paragraphs":len(table_records),"front_paragraphs":len(front_records),"summary_paragraphs":len(summary_records),"summary_prose_paragraphs":len(summary_prose),"toc_paragraphs":len(toc_records)},"checks":{"machine":machine,"visual":visual},"heading_styles":heading_summary,"summary_prose_measurement":{"count":len(summary_prose),"size_half_points":{str(k):v for k,v in Counter(tuple(r["size_half_points"]) for r in summary_prose).items()},"line":{str(k):v for k,v in Counter((r["line"],r["line_rule"]) for r in summary_prose).items()},"first_line_twips":dict(Counter(r["first_line_twips"] for r in summary_prose)),"alignment":dict(Counter(r["alignment"] for r in summary_prose))},"violations":violations,"sections":sections,"food_crops_format":food_crops_format,"notes":["OOXML 선언과 style inheritance를 측정했으며 installed font와 실제 렌더링은 이 helper가 주장하지 않음.","표지·목차·표 셀은 본문 12 pt/160%/2칸 판정에서 제외하고 별도 계수로 보존함."]}
    if output_json:
        output_json.parent.mkdir(parents=True,exist_ok=True); output_json.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return result

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("docx",type=Path); ap.add_argument("--out",type=Path)
    ap.add_argument("--major",default=None,help="전공 id (예: food_crops) — 주어지면 그 전공 양식도 대조")
    ns=ap.parse_args(argv)
    try: result=check(ns.docx,output_json=ns.out,major_id=ns.major)
    except (ProbeError,OSError) as e: print(f"BLOCK: {e}",file=sys.stderr); return 2
    print(json.dumps({"status":result["status"],"sha256":result["input"]["sha256"],"violations":len(result["violations"]),"out":str(ns.out) if ns.out else None},ensure_ascii=False))
    return 1 if result["status"]=="blocked" else 0
if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
