#!/usr/bin/env python3
"""Common reference-workbook resolver (D3a) — read-only, major-neutral.

Decides which XLSX files a writing plan may *refer to*; it never creates,
copies, fills or calculates a workbook.  Policy (all majors, 2026-09-25):
when the major folder survey is complete and shows no major XLSX, the
common set X01+X02 (``references/workbook-reference-set.json``) is both
the reference set and the fill template — X01 for the general form, X02
when a perennial-plant asset account is needed; the user picks at fill
time.  A major-specific workbook takes precedence over the common set:
for ``hort_env_systems`` the shipped H01 identity is that major's base
workbook and X01/X02 stay reference-only there; an explicit user
selection always wins.  An incomplete survey, an unreadable or damaged
file, or a missing user selection is never turned into "absent".
Roles come only from the declared role; file names, author names or
formula counts never assign one.

The caller verifies every file against the canonical source record
before calling ``resolve_workbook_references`` and passes the result in
``link_state``/``sha256``/``readable``/``sheets``; research-time values
carried in a handoff have no effect here.

``extract`` is the deterministic, read-only side lane: it measures
structure (labels, tab colors, formula counts, layout anchors) from a
real common workbook and emits the shipped
``references/common-workbooks/kang-finance-workbook.json`` extract — no
example values, so the whole workbook is never shipped.  ``--check``
re-measures and compares, which is how a maintainer re-verifies lineage
without shipping bytes.
"""

import argparse
import hashlib
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

REFERENCE_SET_PATH = (
    Path(__file__).resolve().parents[1] / "references"
    / "workbook-reference-set.json"
)
COMMON_EXTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "references"
    / "common-workbooks" / "kang-finance-workbook.json"
)

REFERENCE_SET_SCHEMA = "knuaf-workbook-reference-set/v2"
COMMON_EXTRACT_SCHEMA = "knuaf-common-workbook-extract/v1"

ORIGINS = ("major_folder", "common_root")
ROLES = ("school_template", "professor_reference", "student_example",
         "current_work", "unclassified")
AUTHORITY_KINDS = ("user_statement", "document", "none")
SURVEY_STATUSES = ("complete", "partial", "unreadable", "unspecified")
SELECTION_STATES = ("selected", "none", "conflict", "absent", "invalid")
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
          "relationships")

# Per-owner confirmation 2026-09-15/2026-09-25: red tabs are input sheets,
# yellow/green are linked calculation; the model-farm reference sheet is
# example-only and preserved.
TAB_COLOR_ROLES = {"FFFF0000": "input",
                   "FFFFFF00": "linked_calculation",
                   "FF92D050": "linked_calculation"}


class InventoryError(ValueError):
    """Malformed inventory/selection input (a caller bug, not a state)."""


def load_reference_set(path=None):
    data = json.loads(Path(path or REFERENCE_SET_PATH).read_text(
        encoding="utf-8"))
    if data.get("schema") != REFERENCE_SET_SCHEMA:
        raise InventoryError("reference set schema mismatch")
    members = {}
    for m in data["members"]:
        members[m["ref_id"]] = {"sha256": m["sha256"],
                                "sheets": list(m["sheets"]),
                                "label": m["label"]}
    if set(members) != {"X01", "X02"}:
        raise InventoryError("reference set must name X01 and X02")
    return members


def load_reference_catalog(path=None):
    """Full v2 catalogue: ``{"members": {...}, "known_workbooks": {...}}``.

    ``known_workbooks`` entries are identification-only (same shipped
    ``sha256``/exact ``sheets`` fields) that also preserve ``major``,
    ``role``, ``authority`` and ``base_for_major`` when declared; a
    missing block loads as empty.  The shipped v2 document is the only
    accepted schema — fail closed."""
    data = json.loads(Path(path or REFERENCE_SET_PATH).read_text(
        encoding="utf-8"))
    if data.get("schema") != REFERENCE_SET_SCHEMA:
        raise InventoryError("reference set schema mismatch")
    members = load_reference_set(path)
    known = {}
    for k in data.get("known_workbooks") or []:
        entry = {"sha256": k["sha256"], "sheets": list(k["sheets"]),
                 "label": k["label"], "major": k.get("major"),
                 "role": k.get("role"), "authority": k.get("authority"),
                 "base_for_major": k.get("base_for_major")}
        known[k["ref_id"]] = entry
    return {"members": members, "known_workbooks": known}


def _catalog_entries(catalog):
    """Yield (ref_id, entry) identity records from either the catalog
    shape produced by ``load_reference_catalog`` or a bare members map
    (an injected ``reference_set`` argument)."""
    if not isinstance(catalog, dict):
        return []
    if "members" in catalog or "known_workbooks" in catalog:
        entries = list(catalog.get("members", {}).items())
        entries.extend(catalog.get("known_workbooks", {}).items())
        return entries
    return list(catalog.items())


def _is_hex64(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _check_inventory(inventory, selection):
    if not isinstance(inventory, dict):
        raise InventoryError("inventory must be an object")
    survey = inventory.get("survey")
    if not isinstance(survey, dict) \
            or survey.get("status") not in SURVEY_STATUSES:
        raise InventoryError("survey.status invalid")
    files = inventory.get("files")
    if not isinstance(files, list):
        raise InventoryError("files must be a list")
    ids, rels = set(), set()
    for f in files:
        if not isinstance(f, dict):
            raise InventoryError("file entry must be an object")
        fid = f.get("file_id")
        if not isinstance(fid, str) or not fid or fid in ids:
            raise InventoryError("file_id missing or duplicated")
        ids.add(fid)
        if f.get("origin") not in ORIGINS:
            raise InventoryError("origin invalid: %s" % fid)
        if f.get("declared_role") not in ROLES:
            raise InventoryError("declared_role invalid: %s" % fid)
        auth = f.get("role_authority")
        if not isinstance(auth, dict) or auth.get("kind") not in \
                AUTHORITY_KINDS:
            raise InventoryError("role_authority invalid: %s" % fid)
        if f["origin"] == "major_folder":
            rel = f.get("relative_id")
            if not isinstance(rel, str) or not rel or rel in rels:
                raise InventoryError("relative_id missing or duplicated")
            rels.add(rel)
    if not isinstance(selection, dict) \
            or selection.get("state") not in SELECTION_STATES:
        raise InventoryError("selection.state invalid")
    if selection["state"] == "selected" and not isinstance(
            selection.get("file_id"), str):
        raise InventoryError("selected state needs file_id")


def _identity(f, members):
    sha = f.get("sha256")
    if f["origin"] != "common_root":
        return "other" if _is_hex64(sha) else "unknown"
    if not _is_hex64(sha):
        return "unknown"
    for ref_id, m in members.items():
        if sha == m["sha256"]:
            if f.get("sheets") == m["sheets"]:
                return "common_" + ref_id.lower()
            return "common_mismatch"
    return "common_mismatch"


def _known_ref(f, known):
    """Shipped ``known_workbooks`` ref_id (e.g. ``"H01"``) when a
    major_folder file's sha256 AND exact sheet list equal the entry,
    else ``None``.  Hint + identity only — never grants ``usable``."""
    if f["origin"] != "major_folder":
        return None
    sha = f.get("sha256")
    if not _is_hex64(sha):
        return None
    for ref_id, entry in (known or {}).items():
        if sha == entry.get("sha256") and f.get("sheets") == entry.get(
                "sheets"):
            return ref_id
    return None


def _file_view(f, members, known=None):
    readable = f.get("readable") is True
    link_state = f.get("link_state", "unlinked")
    identity = _identity(f, members)
    usable = (readable and _is_hex64(f.get("sha256"))
              and link_state == "ok" and identity not in (
                  "common_mismatch", "unknown"))
    return {
        "file_id": f["file_id"],
        "label": f.get("label"),
        "origin": f["origin"],
        "relative_id": f.get("relative_id"),
        "declared_role": f["declared_role"],
        "authority": ("declared" if f["role_authority"]["kind"] != "none"
                      else "unverified"),
        "read_state": "readable" if readable else "unreadable",
        "identity_state": identity,
        "link_state": link_state,
        "usable": usable,
        "known_ref": _known_ref(f, known),
        # Hint only: a sheet list equal to the X01 layout means the file
        # may carry the same map coordinates.  It never makes a file
        # usable, never grants identity, never enables extract reuse
        # (student copies share the same 17-sheet list).
        "layout_family": (kang_layout_family(f.get("sheets"), members)),
    }


KANG_LAYOUT_FAMILY = "kang_finance_17"


def kang_layout_family(sheets, members):
    """``kang_finance_17`` when the exact 17-name X01 sheet list matches,
    else ``None``.  Callers must treat it as a hint, never as identity."""
    if not isinstance(sheets, list):
        return None
    x01 = (members or {}).get("X01")
    if x01 and sheets == x01["sheets"]:
        return KANG_LAYOUT_FAMILY
    return None


def _common_set_status(views):
    commons = [v for v in views if v["origin"] == "common_root"]
    if any(v["identity_state"] == "common_mismatch" for v in commons):
        return "mismatch"
    present = {v["identity_state"] for v in commons if v["usable"]}
    if {"common_x01", "common_x02"} <= present:
        return "complete"
    return "incomplete"


def resolve_workbook_references(major_id, inventory, selection,
                                reference_set=None):
    """Return the reference decision; every branch returns the same keys.

    ``selection`` is built from the canonical record by the caller:
    ``{"state": "selected|none|conflict|absent|invalid", "file_id",
    "fact_id"}``.
    """
    if not isinstance(major_id, str) or not major_id:
        raise InventoryError("explicit major_id required")
    _check_inventory(inventory, selection)
    # No injection -> the full shipped catalog (members + known_workbooks).
    # An injected bare members map means no known_workbooks; an injected
    # catalog dict is normalized by _catalog_entries.
    catalog = reference_set if reference_set is not None else \
        load_reference_catalog()
    if isinstance(catalog, dict) and ("members" in catalog
                                      or "known_workbooks" in catalog):
        members = catalog.get("members")
        known = catalog.get("known_workbooks") or {}
    else:
        members, known = catalog, {}
    members = members or load_reference_set()
    views = [_file_view(f, members, known) for f in inventory["files"]]
    by_id = {v["file_id"]: v for v in views}
    common_status = _common_set_status(views)
    comparison = [v["file_id"] for v in views
                  if v["origin"] == "common_root" and v["usable"]]
    survey = inventory["survey"]
    evidence = survey.get("evidence") or {}
    survey_complete = (survey["status"] == "complete"
                       and evidence.get("verified") is True)
    major_files = [v for v in views if v["origin"] == "major_folder"]
    reference, completeness, authority = [], "unknown", "not_applicable"
    template_candidates, template_status = [], "not_applicable"
    if not survey_complete:
        inventory_status, selection_status = "not_surveyed", "survey_required"
        reason = "전공 폴더 조사가 완료·검증되지 않아 부재로 판단하지 않음"
    elif not major_files:
        inventory_status = "confirmed_absent"
        selection_status = "common_reference_policy"
        reference = list(comparison)
        completeness = common_status
        authority = "policy"
        template_candidates = list(comparison)
        # Status counts DISTINCT identified variants, not files: two
        # verified X01 copies still leave the user a single variant.
        variants = {v["identity_state"] for v in views
                    if v["file_id"] in template_candidates}
        template_status = ("variant_selection_required"
                           if len(variants) >= 2 else
                           "single_candidate" if variants
                           else "unavailable")
        reason = "전공 XLSX 부재 확인 — 공용 양식집합 X01·X02 사용"
    else:
        inventory_status = "present"
        state = selection["state"]
        chosen = by_id.get(selection.get("file_id"))
        if state in ("none", "absent"):
            # 전공 전용 통합문서 우선 (D-F): 정본 선택이 없을 때 usable
            # 전공 폴더 파일이 자기 전공의 base_for_major 등록과 정확히
            # 하나 맞으면 그 파일이 기본이다. 복사본이 둘 이상이면 사람이
            # 고른다. 명시 선택은 언제나 이 정책보다 앞선다.
            bases = [v for v in major_files
                     if v["usable"] and v["known_ref"]
                     and (known.get(v["known_ref"]) or {}).get(
                         "base_for_major") == major_id]
            if len(bases) == 1:
                selection_status = "major_base_policy"
                reference = [bases[0]["file_id"]]
                completeness = "complete"
                authority = "policy"
                reason = ("전공 전용 통합문서 우선 — 등록 전공 통합문서가 "
                          "기본 선택됨")
            elif len(bases) > 1:
                selection_status = "selection_required"
                reason = ("전공 기본 통합문서 복사본이 둘 이상임 — 정본 선택 "
                          "필요: " + ", ".join(v["file_id"]
                                              for v in bases))
            else:
                selection_status = "selection_required"
                reason = "전공 XLSX 후보가 있으나 정본 선택이 없음"
        elif state == "conflict":
            selection_status = "selection_conflict"
            reason = "정본 선택 사실이 둘 이상임"
        elif state == "invalid":
            selection_status = "selection_invalid"
            reason = "정본 선택 값이 파일 ID 문자열이 아님"
        elif chosen is None or chosen["origin"] != "major_folder":
            selection_status = "selection_invalid"
            reason = "선택한 파일이 전공 폴더 후보 목록에 없음"
        elif not chosen["usable"]:
            selection_status = "selected_unusable"
            reason = "선택 파일을 읽거나 등록 원본과 대조할 수 없음"
        else:
            selection_status = "user_selected"
            reference = [chosen["file_id"]]
            completeness = "complete"
            authority = chosen["authority"]
            reason = "사용자가 정본에서 선택한 전공 XLSX"
    revisions = [
        {k: f.get(k) for k in ("file_id", "ref_id", "source_id",
                               "source_revision", "sha256", "link_state")}
        for f in inventory["files"]
    ]
    return {
        "major_id": major_id,
        "inventory_status": inventory_status,
        "reference_set": reference,
        "comparison_refs": comparison,
        "common_set_status": common_status,
        "selection_status": selection_status,
        "selection_reason": reason,
        "template_candidates": template_candidates,
        "template_status": template_status,
        "authority_status": authority,
        "candidate_roles": views,
        "source_revisions": revisions,
        "completeness": completeness,
        "survey_basis": evidence.get("basis"),
    }


def read_workbook_identity(path, *, reference_set=None):
    """SHA-256, readability and exact sheet names (spaces kept).

    Read-only: nothing is written.  When the SHA-256 equals a shipped
    reference-set member (X01/X02) or a known_workbooks entry (H01), the
    shipped exact sheet list is returned with
    ``identity_source: "registry_extract"`` and the workbook is never
    opened.  Any other hash takes the openpyxl ``read_only`` parse with
    ``identity_source: "parsed"``.  Identity/usable/role decisions are
    unchanged — they still run in ``_identity``/``_file_view``."""
    path = Path(path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if reference_set is None:
        reference_set = load_reference_catalog()
    for _ref_id, entry in _catalog_entries(reference_set):
        if sha == entry.get("sha256") and entry.get("sheets") is not None:
            return {"sha256": sha, "readable": True,
                    "sheets": list(entry["sheets"]),
                    "identity_source": "registry_extract"}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        try:
            sheets = list(wb.sheetnames)
        finally:
            wb.close()
        return {"sha256": sha, "readable": True, "sheets": sheets,
                "identity_source": "parsed"}
    except Exception:  # damaged/unsupported bytes are a state, not a crash
        return {"sha256": sha, "readable": False, "sheets": None,
                "identity_source": "parsed"}


def scan(paths, *, scope_root, origin="major_folder"):
    """Inventory file entries for explicit paths.  Roles stay
    ``unclassified`` and the survey is never marked complete here — a
    list of files is not proof that a folder was fully surveyed."""
    root = Path(scope_root).resolve()
    files = []
    reference_set = load_reference_catalog()
    for p in paths:
        p = Path(p).resolve()
        ident = read_workbook_identity(p, reference_set=reference_set)
        rel = p.relative_to(root).as_posix()
        files.append({
            "file_id": rel, "label": p.name, "origin": origin,
            "relative_id": rel, "declared_role": "unclassified",
            "role_authority": {"kind": "none", "ref": None},
            "link": {"kind": "none"}, **ident,
        })
    return {"survey": {"scope_label": root.name, "scope_id": str(root),
                       "status": "partial",
                       "evidence": {"kind": "none", "ref": None}},
            "files": files}


# --- extract (C1/C5): deterministic structure-only measurement --------

_L = "{%s}" % NS_MAIN
_R = "{%s}" % NS_REL

# Identification labels (C1): fixed worksheet labels only.  The survey
# title carries the source document's own base-year text; it is
# structure, not a plan value.
_ID_LABELS = (("목록", "B2"), ("1. 기초재무상태조사", "B2"))
_EXAMPLE_ONLY_SHEET = "참고1. 모델농장분석"
_INVESTMENT_SHEET = "3.투자계획"
_DEPRECIATION_SHEET = "10. 감가상각비계획 "


def _zip_sheets(z):
    """Ordered (name, xml part, state) for every sheet in a zip."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
    out = []
    for s in wb.findall(f"{_L}sheets/{_L}sheet"):
        target = rid_to_target.get(s.attrib.get(f"{_R}id"), "")
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        out.append((s.attrib["name"], target,
                    s.attrib.get("state", "visible")))
    return out


def _zip_shared(z):
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(si.itertext()) for si in root.findall(f"{_L}si")]


def _cell_value(cell, shared):
    """Literal value or formula text ("=" prefix).  Cached values are
    never emitted for formula cells."""
    t = cell.attrib.get("t")
    if t == "inlineStr":
        return "".join(cell.itertext())
    f = cell.find(f"{_L}f")
    v = cell.find(f"{_L}v")
    if v is None:
        return "=" + (f.text or "") if f is not None else None
    raw = v.text or ""
    if t == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if t == "str":
        return raw
    if f is not None:
        return "=" + (f.text or "")
    return raw


def _sheet_cells(z, target, shared):
    root = ET.fromstring(z.read(target))
    cells = {}
    formulas = 0
    for c in root.findall(f".//{_L}c"):
        ref = c.attrib.get("r")
        if not ref:
            continue
        if c.find(f"{_L}f") is not None:
            formulas += 1
        cells[ref] = _cell_value(c, shared)
    return cells, formulas


def _tab_color(z, target):
    root = ET.fromstring(z.read(target))
    pr = root.find(f"{_L}sheetPr")
    if pr is None:
        return None
    tc = pr.find(f"{_L}tabColor")
    return tc.attrib.get("rgb") if tc is not None else None


def _sheet_role(name, tab_color):
    if name == _EXAMPLE_ONLY_SHEET:
        return "example_only"
    if name == "목록":
        return "index"
    return TAB_COLOR_ROLES.get(tab_color, "linked_calculation")


def extract_member(path, ref_id):
    """Measure one common workbook into its extract member record.

    Read-only: the zip is opened for reading; nothing is written, and no
    example cell values leave this function — only fixed labels, counts,
    colors and anchor references."""
    path = Path(path)
    data = path.read_bytes()
    members_cell_maps = {}
    with zipfile.ZipFile(path) as z:
        shared = _zip_shared(z)
        sheet_rows = _zip_sheets(z)
        sheets, sheet_meta = [], []
        for name, target, state in sheet_rows:
            cells, formulas = _sheet_cells(z, target, shared)
            members_cell_maps[name] = cells
            color = _tab_color(z, target)
            sheets.append(name)
            sheet_meta.append({
                "name": name,
                "state": state,
                "tab_color": color,
                "role": _sheet_role(name, color),
                "cell_count": len(cells),
                "formula_count": formulas,
            })
        labels = {f"{sheet}!{cell}": members_cell_maps.get(sheet, {}).get(
                  cell) for sheet, cell in _ID_LABELS}
    member = {
        "ref_id": ref_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "sheets": sheets,
        "labels": labels,
        "formula_counts": {s["name"]: s["formula_count"]
                           for s in sheet_meta},
        "sheet_meta": sheet_meta,
        "model_farm_sheet": {"name": _EXAMPLE_ONLY_SHEET,
                             "role": "example_only",
                             "preserved": True,
                             "present": _EXAMPLE_ONLY_SHEET in sheets},
    }
    return member, members_cell_maps


def _anchor_row(cells, column, value):
    """Row of the first cell in ``column`` equal to ``value`` — locates a
    moved label row (e.g. the total row that shifts when X02 inserts an
    investment line)."""
    refs = [r for r in cells
            if r.startswith(column) and r[len(column):].isdigit()]
    for ref in sorted(refs, key=lambda r: int(r[len(column):])):
        if cells[ref] == value:
            return int(ref[len(column):])
    return None


def _layout_anchors(cell_maps):
    """Variant-keyed cell predicates separating the two layouts.

    Each variant lists ``{sheet, cell, expect}`` triples: ``equals``
    requires the exact fixed text, ``empty`` requires no value,
    ``present`` requires some value.  All anchors of a variant must hold
    for ``detect_layout`` to name it; the column-J predicate is
    exclusive so both variants can never hold at once."""
    anchors = {}
    for ref_id, maps in cell_maps.items():
        inv = maps.get(_INVESTMENT_SHEET) or {}
        dep = maps.get(_DEPRECIATION_SHEET) or {}
        total_row = _anchor_row(inv, "C", "계")
        variant = []
        if total_row is not None:
            variant.append({"sheet": _INVESTMENT_SHEET,
                            "cell": "C%d" % total_row,
                            "expect": {"equals": "계"}})
        variant.append({"sheet": _DEPRECIATION_SHEET, "cell": "J7",
                        "expect": ({"empty": True}
                                   if dep.get("J7") in (None, "")
                                   else {"present": True})})
        anchors[ref_id.lower()] = variant
    return anchors


def _variant_delta(cell_maps):
    """Structural X02-vs-X01 differences (C1): counts plus the measured
    shifts; example values stay in the files, only labels/locations ship."""
    x01, x02 = cell_maps["X01"], cell_maps["X02"]
    diffs, per_sheet = 0, {}
    for sheet in x01:
        other = x02.get(sheet, {})
        keys = set(x01[sheet]) | set(other)
        d = sum(1 for k in keys
                if x01[sheet].get(k) != other.get(k))
        if d:
            per_sheet[sheet] = d
            diffs += d
    inv20 = (x02.get(_INVESTMENT_SHEET) or {}).get("C20")
    return {
        "base": "X01", "variant": "X02",
        "cell_diffs_total": diffs,
        "cell_diffs_by_sheet": per_sheet,
        "notes": [
            {"kind": "inserted_row", "sheet": _INVESTMENT_SHEET, "row": 20,
             "inserted_label": inv20,
            "detail": ("X02 inserts one investment row; the input detail"
                       " block shifts down one row (19–23 to 20–24) and"
                       " the total row moves C24 to C25")},
            {"kind": "added_column_block", "sheet": _DEPRECIATION_SHEET,
             "column": "J",
             "detail": ("X02 adds the perennial-asset depreciation"
                        " column block populated from 3.투자계획!E20;"
                        " X01 leaves the J cells empty")},
        ],
    }


def build_common_extract(sources):
    """Deterministic extract for ``{ref_id: path}``.

    The result is the committed ``kang-finance-workbook.json`` — labels,
    structure, tab roles, formula counts, variant delta and layout
    anchors only; never example numbers or cell contents."""
    members, cell_maps = {}, {}
    for ref_id, path in sources.items():
        members[ref_id], cell_maps[ref_id] = extract_member(path, ref_id)
    extract = {
        "schema": COMMON_EXTRACT_SCHEMA,
        "members": members,
        "layout_anchors": _layout_anchors(cell_maps),
    }
    if set(members) == {"X01", "X02"}:
        extract["variant_delta"] = _variant_delta(cell_maps)
    return extract


def check_common_extract(sources, extract_path=None):
    """Re-measure and compare against the shipped member extract.

    Only ``members`` is compared — ``variant_delta`` and
    ``layout_anchors`` are re-derivable cross-member views, so a
    single-member ``--check`` still verifies lineage."""
    shipped = json.loads(Path(
        extract_path or COMMON_EXTRACT_PATH).read_text(encoding="utf-8"))
    if shipped.get("schema") != COMMON_EXTRACT_SCHEMA:
        raise InventoryError("common extract schema mismatch")
    fresh = build_common_extract(sources)["members"]
    wanted = set(sources)
    shipped_members = {k: v for k, v in
                       (shipped.get("members") or {}).items()
                       if k in wanted}
    result = {"status": ("match" if shipped_members == fresh
                         else "mismatch"),
              "members": sorted(wanted),
              "extract": str(Path(extract_path or COMMON_EXTRACT_PATH))}
    if result["status"] != "match":
        for ref_id in sorted(wanted):
            if shipped_members.get(ref_id) != fresh.get(ref_id):
                result.setdefault("diverged", []).append(ref_id)
    return result


def cli(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--scope-root", required=True)
    s.add_argument("--origin", default="major_folder", choices=ORIGINS)
    s.add_argument("paths", nargs="+")
    r = sub.add_parser("resolve")
    r.add_argument("--major", required=True)
    r.add_argument("--inventory", required=True)
    r.add_argument("--selection", required=True, help="selection JSON")
    x = sub.add_parser(
        "extract",
        description="read-only structural extract of a common workbook")
    x.add_argument("--ref", action="append", required=True,
                   choices=("X01", "X02"),
                   help="member ref; repeat with one --source each")
    x.add_argument("--source", action="append", required=True,
                   type=Path,
                   help="workbook path; repeat with one --ref each")
    x.add_argument("--out", type=Path,
                   help="write the extract JSON here instead of stdout")
    x.add_argument("--check", type=Path,
                   help="compare the fresh extract with this shipped file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "scan":
            value = scan(a.paths, scope_root=a.scope_root, origin=a.origin)
        elif a.cmd == "extract":
            if len(a.ref) != len(a.source):
                raise InventoryError(
                    "each --ref needs exactly one --source")
            sources = dict(zip(a.ref, a.source))
            if a.check is not None:
                value = check_common_extract(sources, a.check)
            else:
                value = build_common_extract(sources)
                if a.out is not None:
                    if a.out.exists():
                        raise FileExistsError(
                            "refusing to overwrite: %s" % a.out)
                    a.out.parent.mkdir(parents=True, exist_ok=True)
                    a.out.write_text(
                        json.dumps(value, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
                    value = {"status": "written", "out": str(a.out),
                             "members": sorted(sources)}
        else:
            inventory = json.loads(Path(a.inventory).read_text(
                encoding="utf-8"))
            value = resolve_workbook_references(
                a.major, inventory, json.loads(a.selection))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
