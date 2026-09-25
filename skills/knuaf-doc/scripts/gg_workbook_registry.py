#!/usr/bin/env python3
"""Common reference-workbook resolver (D3a) — read-only, major-neutral.

Decides which XLSX files a writing plan may *refer to*; it never creates,
copies, fills or calculates a workbook.  Policy (all majors): when the
major folder survey is complete and shows no major XLSX, the common set
X01+X02 (``references/workbook-reference-set.json``) is the reference
set.  An incomplete survey, an unreadable or damaged file, or a missing
user selection is never turned into "absent".  Roles come only from the
declared role; file names, author names or formula counts never assign
one.

The caller verifies every file against the canonical source record
before calling ``resolve_workbook_references`` and passes the result in
``link_state``/``sha256``/``readable``/``sheets``; research-time values
carried in a handoff have no effect here.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REFERENCE_SET_PATH = (
    Path(__file__).resolve().parents[1] / "references"
    / "workbook-reference-set.json"
)

ORIGINS = ("major_folder", "common_root")
ROLES = ("school_template", "professor_reference", "student_example",
         "current_work", "unclassified")
AUTHORITY_KINDS = ("user_statement", "document", "none")
SURVEY_STATUSES = ("complete", "partial", "unreadable", "unspecified")
SELECTION_STATES = ("selected", "none", "conflict", "absent", "invalid")
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")


class InventoryError(ValueError):
    """Malformed inventory/selection input (a caller bug, not a state)."""


def load_reference_set(path=None):
    data = json.loads(Path(path or REFERENCE_SET_PATH).read_text(
        encoding="utf-8"))
    if data.get("schema") != "knuaf-workbook-reference-set/v1":
        raise InventoryError("reference set schema mismatch")
    members = {}
    for m in data["members"]:
        members[m["ref_id"]] = {"sha256": m["sha256"],
                                "sheets": list(m["sheets"]),
                                "label": m["label"]}
    if set(members) != {"X01", "X02"}:
        raise InventoryError("reference set must name X01 and X02")
    return members


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


def _file_view(f, members):
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
    }


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
    members = reference_set or load_reference_set()
    views = [_file_view(f, members) for f in inventory["files"]]
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
    if not survey_complete:
        inventory_status, selection_status = "not_surveyed", "survey_required"
        reason = "전공 폴더 조사가 완료·검증되지 않아 부재로 판단하지 않음"
    elif not major_files:
        inventory_status = "confirmed_absent"
        selection_status = "common_reference_policy"
        reference = list(comparison)
        completeness = common_status
        authority = "policy"
        reason = "전공 XLSX 부재 확인 — 공용 참조집합 X01·X02 사용"
    else:
        inventory_status = "present"
        state = selection["state"]
        chosen = by_id.get(selection.get("file_id"))
        if state in ("none", "absent"):
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
        "authority_status": authority,
        "candidate_roles": views,
        "source_revisions": revisions,
        "completeness": completeness,
        "survey_basis": evidence.get("basis"),
    }


def read_workbook_identity(path):
    """SHA-256, readability and exact sheet names (spaces kept).

    Read-only: openpyxl ``read_only`` mode, nothing is written."""
    path = Path(path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        try:
            sheets = list(wb.sheetnames)
        finally:
            wb.close()
        return {"sha256": sha, "readable": True, "sheets": sheets}
    except Exception:  # damaged/unsupported bytes are a state, not a crash
        return {"sha256": sha, "readable": False, "sheets": None}


def scan(paths, *, scope_root, origin="major_folder"):
    """Inventory file entries for explicit paths.  Roles stay
    ``unclassified`` and the survey is never marked complete here — a
    list of files is not proof that a folder was fully surveyed."""
    root = Path(scope_root).resolve()
    files = []
    for p in paths:
        p = Path(p).resolve()
        ident = read_workbook_identity(p)
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
    a = ap.parse_args(argv)
    try:
        if a.cmd == "scan":
            value = scan(a.paths, scope_root=a.scope_root, origin=a.origin)
        else:
            inventory = json.loads(Path(a.inventory).read_text(
                encoding="utf-8"))
            value = resolve_workbook_references(
                a.major, inventory, json.loads(a.selection))
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
