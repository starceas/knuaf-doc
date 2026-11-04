#!/usr/bin/env python3
"""Check DOCX table-of-contents page numbers for fixed or unrefreshed values.

Only the DOCX package is read (``word/document.xml``); the PDF is never
opened, so a hand-typed number cannot be "verified" against the render.

Two definite defects are reported as ``TOC-PAGE-NUMBERS: fail``:

* a TOC line ends in ``<tab><page number>`` where the number is ordinary
  text outside every field -- a number typed by hand instead of produced by
  ``PAGEREF``/``TOC``;
* a ``PAGEREF``/``TOC`` field result is empty or ``?`` -- the document was
  saved before Word refreshed the fields.

Anything else (a number written without a tab, a field that was refreshed
and then edited, the real page numbers) is left to the human render review;
this tool does not claim the TOC numbers are correct.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

TOC_TITLES = {"목차", "표목차", "그림목차"}
TOC_BOOKMARKS = {"gg_tables", "gg_figures"}

_ARABIC = re.compile(r"\d{1,4}")
_ROMAN = re.compile(r"[ivxlcdm]{1,7}", re.I)
_PAGE_FIELD = re.compile(r"^\s*(PAGEREF|TOC)\b", re.I)
_TAB_TAIL = re.compile(r"\t\s*([^\t\s]+)\s*$")


def _w(name):
    return _W + name


def _text_of(element):
    """Concatenated text of an element, counting ``w:tab`` as a tab."""
    parts = []
    for child in element.iter():
        if child.tag == _w("br") and child.get(_w("type")) == "page":
            break
        if child.tag == _w("t"):
            parts.append(child.text or "")
        elif child.tag == _w("tab"):
            parts.append("\t")
    return "".join(parts)


def _scan_text(text, state):
    """Keep visible text and page-number provenance at the time it is read."""
    stack = state["stack"]
    if stack:
        if stack[-1]["phase"] != "result":
            return
        # A nested field's displayed text is also part of every enclosing
        # result, but its PAGEREF provenance stays separate below.
        for frame in stack:
            if frame["phase"] == "result":
                frame["result"] += text
    else:
        state["outside"] += text
    protected = False
    for frame in stack:
        page = _PAGE_FIELD.match(frame["instr"])
        if (frame["phase"] == "result" and page
                and (page[1].upper() == "PAGEREF" or frame.get("simple"))):
            protected = True
            break
    state["visible"] += text
    state["unprotected"] += " " * len(text) if protected else text


def _scan(element, state, parent_tag=None):
    """Collect outside-field text and every field (instr, result) in order."""
    for child in element:
        tag = child.tag
        if tag == _w("br") and child.get(_w("type")) == "page":
            return True
        if tag == _w("fldSimple"):
            frame = {"instr": child.get(_w("instr"), ""), "result": "",
                     "phase": "result", "simple": True}
            state["stack"].append(frame)
            stopped = _scan(child, state, tag)
            state["stack"].pop()
            state["fields"].append({"instr": frame["instr"], "result": frame["result"]})
            if stopped:
                return True
            continue
        if tag == _w("fldChar"):
            kind = child.get(_w("fldCharType"))
            if kind == "begin":
                state["stack"].append({"instr": "", "result": "",
                                       "phase": "instr"})
            elif kind == "separate" and state["stack"]:
                state["stack"][-1]["phase"] = "result"
            elif kind == "end" and state["stack"]:
                frame = state["stack"].pop()
                state["fields"].append(
                    {"instr": frame["instr"], "result": frame["result"]})
            continue
        if tag == _w("instrText"):
            if state["stack"]:
                state["stack"][-1]["instr"] += child.text or ""
            continue
        if tag == _w("t"):
            _scan_text(child.text or "", state)
            continue
        if tag == _w("tab") and parent_tag == _w("r"):
            _scan_text("\t", state)
            continue
        if _scan(child, state, tag):
            return True


def paragraph_scan(paragraph, state=None):
    """Return field-relative text and fields, preserving state across paragraphs."""
    if state is None:
        state = {"outside": "", "fields": [], "stack": []}
    state["outside"] = ""
    state["fields"] = []
    state["visible"] = ""
    state["unprotected"] = ""
    _scan(paragraph, state)
    return state["outside"], state["fields"]


def _is_toc_title(paragraph):
    """True for a TOC heading, never for a TOC entry line.

    ``contents()`` also lists "표 목차"/"그림 목차" as entries inside the main
    TOC; those lines carry a page field and must not split the area.
    """
    outside, fields = paragraph_scan(paragraph)
    if any(_PAGE_FIELD.match(field["instr"]) for field in fields):
        return False
    if _TAB_TAIL.search(outside):
        return False
    text = re.sub(r"\s+", "", _text_of(paragraph))
    if text in TOC_TITLES:
        return True
    for bookmark in paragraph.iter(_w("bookmarkStart")):
        if bookmark.get(_w("name")) in TOC_BOOKMARKS:
            return True
    return False


def _ends_area(paragraph):
    """True when the paragraph carries a section break or a page break."""
    ppr = paragraph.find(_w("pPr"))
    if ppr is not None and ppr.find(_w("sectPr")) is not None:
        return True
    for br in paragraph.iter(_w("br")):
        if br.get(_w("type")) == "page":
            return True
    return False


def _is_page_number(token):
    return bool(_ARABIC.fullmatch(token) or _ROMAN.fullmatch(token))


def toc_areas(body):
    """Yield the paragraph lists of every table-of-contents block."""
    area = None
    for element in body:
        if element.tag != _w("p"):
            continue
        if _is_toc_title(element):
            if area is not None:
                yield area
            area = []
            if _ends_area(element):
                yield area
                area = None
            continue
        if area is None:
            continue
        area.append(element)
        if _ends_area(element):
            yield area
            area = None
            continue
    if area is not None:
        yield area


def check(path):
    """Return the TOC-PAGE-NUMBERS evidence for a DOCX file."""
    path = Path(path)
    data = path.read_bytes()
    with ZipFile(path) as package:
        if "word/document.xml" not in package.namelist():
            raise ValueError("DOCX에 word/document.xml 없음")
        root = ET.fromstring(package.read("word/document.xml"))
    body = root.find(_w("body"))
    if body is None:
        raise ValueError("DOCX 본문(w:body) 없음")

    findings = []
    entries = 0
    found = False
    for area in toc_areas(body):
        found = True
        field_state = {"outside": "", "fields": [], "stack": []}
        for paragraph in area:
            _, fields = paragraph_scan(paragraph, field_state)
            visible = field_state["visible"]
            label = re.sub(r"\s+", " ", visible).strip()[:80]
            match = _TAB_TAIL.search(visible)
            if (match and _is_page_number(match.group(1))
                    and field_state["unprotected"][match.start(1):match.end(1)].strip()):
                findings.append({
                    "para": label,
                    "reason": "hand_typed_page_number",
                    "value": match.group(1),
                })
            if fields or match:
                entries += 1
            for field in fields:
                if not _PAGE_FIELD.match(field["instr"]):
                    continue
                if field["result"].strip() in {"", "?"}:
                    findings.append({
                        "para": label,
                        "reason": "field_not_refreshed",
                        "field": field["instr"].strip(),
                    })

    result = {
        "docx_sha256": hashlib.sha256(data).hexdigest(),
        "findings": [],
    }
    if found:
        if not entries and not findings:
            findings = [{"para": "", "reason": "toc_empty"}]
        result["findings"].append({
            "id": "TOC-PAGE-NUMBERS",
            "status": "fail" if findings else "pass",
            "items": findings,
        })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx")
    parser.add_argument("--json", dest="evidence",
                        help="증거 JSON을 쓸 경로")
    args = parser.parse_args(argv)
    try:
        result = check(args.docx)
    except (OSError, ValueError, BadZipFile, ET.ParseError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.evidence:
        Path(args.evidence).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    status = next((item["status"] for item in result["findings"]), "pass")
    return 2 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
