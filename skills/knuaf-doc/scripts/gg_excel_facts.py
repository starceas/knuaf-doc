#!/usr/bin/env python3
"""Build source/fact ops from native Excel caches; never calculate finances.

The caller stages the workbook and native receipt inside its project, reviews
an exact hash-bound cell map, then applies the returned change with gg_core.
No files or canonical state are written by this adapter.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

import gg_core as core
from gg_excel_template import NS_MAIN, workbook_sheets

MAP_SCHEMA = "gg-xlsx-fact-map/v1"
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")


class RegistrationHeld(ValueError):
    """A cache/proof/mapping failure leaves the project unchanged."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _object(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RegistrationHeld("duplicate_json_key")
            result[key] = value
        return result

    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise RegistrationHeld("json_object_required")
    return value


def _text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistrationHeld("mapping_" + key + "_required")
    return value


def _hort_legacy_fact(fact):
    """A fact written by a v0.4.7 horticulture registration.

    That adapter wrote the machine identifier (``finance.…``) and the literal
    ``student_farm`` placeholder instead of the body label and the student's
    farm name, so such a fact can never equal what this map claims.
    """
    return (str(fact.get("field_id", "")).startswith("finance.")
            or fact.get("scope") == "student_farm")


def _legacy_hort_conflict(project, authorization, rows):
    """Whether the map would re-register a fact a v0.4.7 run already wrote.

    Checked before the transform-receipt chain and before the farm-name
    demand.  A real second registration in the same folder changed the
    canonical revision, so the old receipt is stale first, and a v0.4.7
    project predates the farm-name requirement; either refusal would hide
    the reason the student needs (design §4-3).

    Only a difference the row already proves is reported.  v0.4.7 wrote the
    machine identifier (``finance.…``) and the literal placeholder, while this
    map claims the body label and the farm name, so the field label alone
    already differs.  The placeholder also never survives a claim: it becomes
    the farm name, or registration stops for want of one.  A row whose claim
    could still equal the previous fact stays with the row loop, which
    compares the cached value, so this never refuses a match.
    """
    if authorization.major_id != "hort_env_systems":
        return False
    facts = project.get("facts", {})
    farm = facts.get("farm_name")
    farm_value = farm.get("value") if isinstance(farm, dict) else None
    if not isinstance(farm_value, str) or not farm_value.strip():
        farm_value = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        previous = facts.get(row.get("fact_id"))
        if not isinstance(previous, dict) or not _hort_legacy_fact(previous):
            continue
        for key in ("field_id", "period", "unit"):
            declared = row.get(key)
            if isinstance(declared, str) and declared != previous.get(key):
                return True
        scope = row.get("scope")
        if isinstance(scope, str):
            if scope != "student_farm":
                if scope != previous.get("scope"):
                    return True
            elif previous.get("scope") == "student_farm":
                # The claim never keeps the placeholder: a name replaces it,
                # or registration is refused for want of one.
                return True
            elif farm_value is not None and farm_value.strip() != previous.get("scope"):
                return True
    return False


def _student_farm_scope(project, authorization):
    """The student's own farm name, which the map's scope placeholder means.

    Rows of the horticulture finance map carry the literal placeholder
    ``student_farm``; the prose they are checked against names the farm.  The
    project fact ``farm_name`` is the single source, so a missing, withheld or
    blank name stops registration instead of writing a placeholder the body
    can never contain.  Another major's map keeps its literal scope, so the
    placeholder means nothing there and no farm name is demanded.
    """
    if authorization.major_id != "hort_env_systems":
        return None
    fact = project.get("facts", {}).get("farm_name")
    value = fact.get("value") if isinstance(fact, dict) else None
    if (not isinstance(fact, dict) or fact.get("answer_state") != "provided"
            or not isinstance(value, str) or not value.strip()):
        raise RegistrationHeld("farm_name_required")
    return value.strip()


def build_registration(root, workbook, receipt, mapping, *, source_id, context):
    """Return a complete gg_core.apply change, after validating every row.

    ``mapping`` is a gg-xlsx-fact-map/v1 object containing workbook.sha256,
    receipt.sha256, workbook_profile and nonempty cells. Each row declares
    fact_id/field_id/sheet/cell/period/unit/scope. Monetary units are 원/천원,
    so every fact carries ``measure`` = monetary_total/KRW in that unit;
    ``field_id`` stays the body label the crosscheck reads.
    Only native successful saved/recalculated Excel proof is accepted; an
    operation receipt, Python-generated caches or absent caches cannot stand
    in for a native run. This proves provenance, not formula correctness.
    """
    import gg_major_contract as mc

    root = Path(root).resolve()
    workbook = core.local(root, str(workbook))
    receipt = core.local(root, str(receipt))
    authorization = mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
    project = core.load(root)
    core._check_record_id(source_id)
    if not isinstance(mapping, dict) or mapping.get("schema") != MAP_SCHEMA:
        raise RegistrationHeld("fact_map_schema_invalid")
    actual = core.digest(workbook.read_bytes())
    receipt_hash = core.digest(receipt.read_bytes())
    if (not isinstance(mapping.get("workbook"), dict)
            or not isinstance(mapping.get("receipt"), dict)
            or mapping["workbook"].get("sha256") != actual
            or mapping["receipt"].get("sha256") != receipt_hash):
        raise RegistrationHeld("fact_map_hash_mismatch")
    native = _object(receipt)
    relative_receipt = str(receipt.relative_to(root))
    pointer = {"path": str(workbook.relative_to(root)),
               "native_manifest": {"path": relative_receipt, "sha256": receipt_hash}}
    rows = mapping.get("cells")
    if authorization.major_id == "hort_env_systems":
        # The 18-sheet workbook declares no generic profile: it opens only
        # through its own transform/native receipt chain.  That chain checks
        # structure and hashes but not the engine failure flags, so apply the
        # same three refusals as _output_workbook_profile before accepting it.
        if (native.get("error") or native.get("returncode") not in (None, 0)
                or str(native.get("engine_stderr", "")).strip()):
            raise mc.OutputHeldError(
                "hort_transform_receipt_invalid",
                "native Excel receipt records a failed run")
        # The cache read below cannot tell a money row from a ratio row, so the
        # reviewed map itself is checked here: excluded ranges and registry
        # units.  This and the legacy scan come before the receipt chain: a
        # folder whose earlier run already registered a fact has a changed
        # revision, and the stale receipt would otherwise be the only thing
        # reported (design §4-3).  ``check_fact_map`` also owns the
        # missing/empty ``cells`` refusal, so ``rows`` is a non-empty list of
        # dicts from here on.
        import gg_hort_workbook as hort
        try:
            hort.check_fact_map(mapping, hort.contracts()[hort.FILES[1]]["parameters"])
        except hort.Held as error:
            raise RegistrationHeld(error.code) from error
        if _legacy_hort_conflict(project, authorization, rows):
            raise RegistrationHeld("legacy_hort_fact_conflict")
        profile = core._output_hort_profile(root, pointer, workbook, authorization)
    else:
        profile = core._output_workbook_profile(root, pointer)
        mc.require_workbook_profile(authorization, profile)
    if mapping.get("workbook_profile") != profile:
        raise RegistrationHeld("fact_map_profile_mismatch")
    recorded = native.get("major_authorization")
    if not isinstance(recorded, list) or not any(
            isinstance(item, dict) and item == authorization.to_dict() for item in recorded):
        raise RegistrationHeld("native_authorization_mismatch")
    result = native.get("engine_result", "")
    session = native.get("office_session")
    mac_saved = (isinstance(session, dict)
                 and session.get("app") == "excel"
                 and session.get("reason") == "closed_confirmed"
                 and session.get("cleanup") == "closed"
                 and session.get("save_state") == "saved"
                 and isinstance(result, str)
                 and result.startswith("GG_OFFICE_SESSION|closed|saved|"))
    windows_saved = (session is None and isinstance(result, str)
                     and result.startswith("Excel process completed; Excel ")
                     and bool(result.removeprefix("Excel process completed; Excel ").strip()))
    if not (mac_saved or windows_saved):
        raise RegistrationHeld("native_recalculation_proof_required")
    if not isinstance(rows, list) or not rows:
        raise RegistrationHeld("cell_mapping_required")
    facts = []
    claims = {}
    seen_cells = set()
    farm_scope = None
    if any(isinstance(row, dict) and row.get("scope") == "student_farm"
           for row in rows):
        farm_scope = _student_farm_scope(project, authorization)
    source_revision = project.get("sources", {}).get(source_id, {}).get("revision", 0) + 1
    with zipfile.ZipFile(workbook) as archive:
        sheets = dict(workbook_sheets(archive))
        selected = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RegistrationHeld("mapping_object_required")
            fid = _text(row, "fact_id")
            core._check_record_id(fid)
            field = _text(row, "field_id")
            sheet, cell = _text(row, "sheet"), _text(row, "cell")
            period, scope, unit = (_text(row, key) for key in ("period", "scope", "unit"))
            if farm_scope is not None and scope == "student_farm":
                scope = farm_scope
            if unit not in {"원", "천원"}:
                raise RegistrationHeld("monetary_unit_required")
            if (not CELL.fullmatch(cell) or sheet not in sheets
                    or fid in claims or (sheet, cell) in seen_cells):
                raise RegistrationHeld("cell_or_fact_mapping_conflict")
            seen_cells.add((sheet, cell))
            if sheet not in selected:
                xml = ET.fromstring(archive.read(sheets[sheet]))
                nodes = list(xml.iter(f"{{{NS_MAIN}}}c"))
                if len({node.get("r") for node in nodes}) != len(nodes):
                    raise RegistrationHeld("duplicate_workbook_cell")
                selected[sheet] = {node.get("r"): node for node in nodes}
            node = selected[sheet].get(cell)
            if node is None or node.get("t", "n") != "n":
                raise RegistrationHeld("numeric_cache_required")
            raw = node.findtext(f"{{{NS_MAIN}}}v")
            try:
                value = Decimal(raw) if raw is not None and raw.strip() else None
            except InvalidOperation as error:
                raise RegistrationHeld("numeric_cache_required") from error
            if value is None or not value.is_finite():
                raise RegistrationHeld("finite_cache_required")
            value = format(value, "f")
            locator = f"{sheet}!{cell}; period={period}; unit={unit}; sha256={actual}"
            claim = {"field_id": field, "kind": "observation", "value": value,
                     "unit": unit, "period": period, "scope": scope, "answer_state": "provided"}
            previous = project.get("facts", {}).get(fid)
            if previous is not None and any(previous.get(key) != value for key, value in claim.items()):
                # A v0.4.7 horticulture registration wrote the machine
                # identifier and the literal placeholder, so its value can
                # never match the new claim.  Say so, instead of reporting an
                # ordinary edit the student could simply redo.
                legacy = (authorization.major_id == "hort_env_systems"
                          and _hort_legacy_fact(previous))
                raise RegistrationHeld(
                    "legacy_hort_fact_conflict" if legacy
                    else "existing_fact_conflict")
            claims[fid] = claim
            facts.append({"collection": "facts", "value": {
                "id": fid, **claim, "value_type": "decimal", "verification": "source_located",
                # Every row this adapter admits is a 원/천원 cell, so the
                # fact is a KRW monetary total.  Declaring the measure is
                # what lets fact_semantics classify it; without it the fact
                # is an unmetadated legacy fact and submission is refused
                # even when the body crosscheck matches.  field_id stays the
                # body label that crosscheck looks up.
                "measure": {"kind": "monetary_total", "currency": "KRW",
                            "unit": unit},
                "source_refs": [{"id": source_id, "revision": source_revision,
                                 "claim_id": fid, "locator": locator}],
                "excel_provenance": {"workbook_sha256": actual, "workbook_profile": profile,
                                     "receipt_sha256": receipt_hash, "sheet": sheet, "cell": cell,
                                     "period": period, "unit": unit}}})
    # Do not return observations after a workbook, receipt or binding races.
    if (core.digest(workbook.read_bytes()) != actual
            or core.digest(receipt.read_bytes()) != receipt_hash):
        raise RegistrationHeld("recalculation_artifact_changed")
    mc.reconfirm_output(authorization, context)
    source = {"id": source_id, "path": str(workbook.relative_to(root)),
              "kind": "observation", "source_type": "excel_recalculation",
              "hash": actual, "claims": claims,
              "claim_review": "native Excel numeric caches; reviewed cell mapping; not formula approval",
              "native_manifest": {"path": relative_receipt, "sha256": receipt_hash},
              "workbook_profile": profile}
    request = core.digest(json.dumps({"source_id": source_id, "mapping": mapping,
                                     "revision": project["revision"]},
                                    ensure_ascii=False, sort_keys=True).encode())
    return {"request_id": "excel-facts:" + request,
            "ops": [{"collection": "sources", "value": source}, *facts]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--workbook", required=True, help="project-relative recalculated XLSX")
    parser.add_argument("--receipt", required=True, help="project-relative native Excel manifest")
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--major", required=True)
    args = parser.parse_args(argv)
    import gg_major_contract as mc
    try:
        change = build_registration(args.project, args.workbook, args.receipt, _object(args.map),
                                    source_id=args.source_id,
                                    context=mc.output_context(args.project, args.major))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, mc.MajorContractError) as error:
        print(json.dumps({"status": "held", "reason": getattr(error, "reason", str(error))},
                         ensure_ascii=False))
        return 2
    print(json.dumps(change, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
