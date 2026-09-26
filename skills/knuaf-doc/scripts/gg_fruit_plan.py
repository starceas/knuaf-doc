#!/usr/bin/env python3
"""Fruit-tree writing plan — read-only (first bundle).

``fruit_plan(root)`` shows what a fruit_trees project can write next and
what is missing: the module's S01 document plan, the registered research
handoff checked against the registered originals, school requirement
locators, conflict holds, the reference-workbook decision, orchard block
and planting-cohort answers per instance, the current plan review state,
and why each deliverable output is still held.

It returns plan and source information only.  It never returns a paper
body or a calculated number, never writes a file and never writes the
canonical record, so it is not a deliverable output path (the common
policy-B guard stays on every output writer).  Orchard production,
finance and the S01 paper are the next bundle.

CLI: ``python3 gg_fruit_plan.py <project folder>`` prints JSON; exit 0 when
a plan is presented, 2 when the project is not bound to fruit_trees.
"""

import json
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import gg_core
import gg_major_contract as mc
import gg_research_handoff as handoff_mod
import gg_workbook_registry as wr

FRUIT = "fruit_trees"
PREFIX = FRUIT + "."

EXPECTED_REQUIREMENTS = tuple("FRT-S%02d" % i for i in range(1, 18))
EXPECTED_ROLES = tuple(range(1, 16))
EXPECTED_CONFLICTS = tuple("C%02d" % i for i in range(1, 16))
EXPECTED_TEMPLATE_REFS = ("S01", "X01", "X02")

# Default sections held by each known S01/XLSX conflict when the handoff
# lacks (or cannot prove a decision for) that conflict record.
FRUIT_CONFLICT_SECTIONS = {
    "C01": ("ch2_location",),
    "C02": ("appendix_05",),
    "C03": ("appendix_03", "ch3_investment"),
    "C04": ("appendix_04",),
    "C05": ("appendix_09",),
    "C06": ("ch3_pest",),
    "C07": ("appendix_05",),
    "C08": ("ch3_pest",),
    "C09": ("appendix_12", "appendix_14", "appendix_15"),
    "C10": ("front", "ch3_cultivar", "back_parent_consent"),
    "C11": ("appendix_15",),
    "C12": ("appendix_13",),
    "C13": ("appendix_14",),
    "C14": ("ch3_investment",),
    "C15": ("ch2_location",),
}

DELIVERABLES = (mc.OUTPUT_SCHOOL_PAPER, mc.OUTPUT_SCHOOL_WORKBOOK,
                mc.OUTPUT_FINANCE_CALCULATION)

_INSTANCE = re.compile(r"^fruit_trees\.(block|cohort|batch|move)\."
                       r"([a-z0-9_]{1,32})\.([a-z0-9_]+)$")
_GROUPS = ("block", "cohort", "batch", "move")
_YEAR = re.compile(r"^\d{4}$")
_STABLE_ID = re.compile(r"^[a-z0-9_]{1,32}$")
_XML_REF = re.compile(r"^section(\d+):(p|t)(\d+)$")
_ORIGINS = ("신규식재", "승계", "갱신·보식")
_BEARING = ("미성목", "성목", "갱신중")

GROUP_FIELDS = {
    "block": tuple(name for name, _, _ in mc._FRUIT_BLOCK_FIELDS),
    "cohort": tuple(name for name, _, _ in mc._FRUIT_COHORT_FIELDS),
    "batch": tuple(name for name, _, _ in mc._FRUIT_BATCH_FIELDS),
    "move": tuple(name for name, _, _ in mc._FRUIT_MOVE_FIELDS),
}
# Per-year cohort inputs (fact.period = "YYYY"); every other field keeps
# one answer per field ID.
YEARLY_FIELDS = mc._FRUIT_YEARLY_FIELDS


def is_yearly(field_id):
    m = _INSTANCE.match(field_id)
    return bool(m and m.group(1) == "cohort" and m.group(3) in YEARLY_FIELDS)


def valid_year(value):
    return (isinstance(value, str) and bool(_YEAR.match(value))
            and 1900 <= int(value) <= 2200)
# Effective-value owners: crop/cultivar/rootstock on the cohort, the
# cultivation form and heating on its block; both fall back to the farm.
_COHORT_OWN = ("crop_species", "cultivar", "rootstock")
_BLOCK_OWN = ("cultivation_form", "heating")


def _norm(text):
    text = unicodedata.normalize("NFC", text or "").lower()
    return "".join(ch for ch in text if ch.isalnum())


# -- sources -----------------------------------------------------------------

def verify_source(root, p, source_id):
    """Registered source vs current bytes (read-only)."""
    rec = p["sources"].get(source_id) if isinstance(source_id, str) else None
    if rec is None:
        return {"source_id": source_id, "state": "missing_source"}
    out = {"source_id": source_id, "revision": rec.get("revision"),
           "recorded_hash": rec.get("hash"), "path": rec.get("path")}
    try:
        data = gg_core.local(root, rec["path"]).read_bytes()
    except (OSError, ValueError, KeyError):
        out["state"] = "unreadable"
        return out
    out["current_hash"] = gg_core.digest(data)
    out["state"] = ("ok" if out["current_hash"] == rec.get("hash")
                    else "stale_source")
    return out


def _provided(p, field_id):
    return [f for f in p["facts"].values()
            if f["field_id"] == field_id and f["answer_state"] == "provided"]


def _single_value(p, field_id):
    """One provided answer or a conflict: two provided facts are a
    conflict even when their values agree (a duplicate is not a choice)."""
    facts = _provided(p, field_id)
    if not facts:
        return "absent", None, None
    if len(facts) > 1:
        return "conflict", None, sorted(f["id"] for f in facts)
    return "one", facts[0]["value"], [facts[0]["id"]]


# -- handoff -----------------------------------------------------------------

def _load_handoff(root, p, members):
    state, value, fact_ids = _single_value(p, "fruit_trees.research_handoff")
    result = {"state": None, "fact_ids": fact_ids, "source": None,
              "gaps": [], "sha256": None}
    if state == "absent":
        result["state"] = "not_registered"
        return result, None
    if state == "conflict":
        result["state"] = "handoff_fact_conflict"
        return result, None
    src = verify_source(root, p, value)
    result["source"] = src
    if src["state"] != "ok":
        result["state"] = src["state"]
        return result, None
    data = gg_core.local(root, p["sources"][value]["path"]).read_bytes()
    sheets = {k: v["sheets"] for k, v in members.items()}
    try:
        checked = handoff_mod.load_and_validate(
            data, major_id=FRUIT, reference_sheets=sheets)
    except handoff_mod.HandoffError as error:
        result["state"] = "invalid"
        result["error"] = {"code": error.code, "where": error.where}
        return result, None
    except (TypeError, AttributeError, KeyError) as error:
        # A structure the validator did not anticipate is still an invalid
        # handoff, never a crash of the whole plan.
        result["state"] = "invalid"
        result["error"] = {"code": "unexpected_structure",
                           "where": type(error).__name__}
        return result, None
    result["state"] = "valid"
    result["gaps"] = checked["gaps"]
    result["sha256"] = checked["handoff"]["handoff_sha256"]
    return result, checked["handoff"]


def _template_states(root, p, h, members):
    states = {}
    for ref in h["template_source_refs"]:
        src = verify_source(root, p, ref["source_id"])
        state = src["state"]
        if state == "ok" and (src["revision"] != ref["source_revision"]
                              or src["recorded_hash"] != ref["sha256"]):
            state = "identity_mismatch"
        if state == "ok" and ref["ref_id"] in members \
                and ref["sha256"] != members[ref["ref_id"]]["sha256"]:
            state = "identity_mismatch"
        states[ref["ref_id"]] = dict(src, ref_id=ref["ref_id"], state=state,
                                     role=ref["role"])
    return states


def _template_sheets(root, template_states):
    """Actual sheet names of each verified template workbook (read-only)."""
    out = {}
    for ref_id, state in template_states.items():
        if state["state"] != "ok" or not str(state.get("path", "")).lower()\
                .endswith(wr.WORKBOOK_SUFFIXES):
            continue
        ident = wr.read_workbook_identity(gg_core.local(root, state["path"]))
        out[ref_id] = ident["sheets"] if ident["readable"] else None
    return out


# -- locators -----------------------------------------------------------------

def _xml_element_text(path, section, kind, index):
    with zipfile.ZipFile(path) as z:
        xml = z.read("Contents/section%d.xml" % section)
    tree = ET.fromstring(xml)
    if kind == "p":
        items = [el for el in tree if el.tag.rsplit("}", 1)[-1] == "p"]
    else:
        items = [el for el in tree.iter() if el.tag.rsplit("}", 1)[-1] == "tbl"]
    if index < 1 or index > len(items):
        return None
    # ``hp:t`` may hold ``hp:tab``/``hp:lineBreak`` children; text after
    # them is the child's tail, so read every text node inside each run.
    return "".join("".join(t.itertext()) for t in items[index - 1].iter()
                   if t.tag.rsplit("}", 1)[-1] == "t")


def _check_locators(root, p, req, template_states):
    source = template_states.get(req["source_ref"])
    views, headings = [], set()
    for loc in req["locators"]:
        headings.add(_norm(loc["heading"]))
        verification = "unverified"
        match = _XML_REF.match(loc["ref"]) if loc["kind"] == "xml" else None
        if match and source and source["state"] == "ok":
            try:
                text = _xml_element_text(
                    gg_core.local(root, source["path"]),
                    int(match.group(1)), match.group(2),
                    int(match.group(3)))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile,
                    ET.ParseError):
                text = None
            if text is not None:
                verification = ("verified" if _norm(loc["heading"])
                                in _norm(text) else "mismatch")
        views.append({"kind": loc["kind"], "ref": loc["ref"],
                      "verification": verification})
    kinds = {v["kind"] for v in views}
    if len(headings) > 1 or any(v["verification"] == "mismatch"
                                for v in views):
        status = "locator_mismatch"
    elif not views:
        status = "missing_locator"
    elif kinds & {"xml", "render"} and "parsed" not in kinds:
        status = "parser_omission_detected"
    else:
        status = "planned"
    return status, views


# -- workbook references -----------------------------------------------------

def _survey(root, p, survey):
    ev = survey["evidence"]
    basis, verified, reason = None, False, "no_evidence"
    if ev["kind"] == "source_scan_receipt":
        verified, reason = _receipt_ok(root, p, survey, ev)
        basis = "source_scan_receipt"
    elif ev["kind"] == "user_attestation":
        src = verify_source(root, p, ev["attestation_source_id"])
        if src["state"] != "ok":
            reason = "attestation_" + src["state"]
        else:
            text = gg_core.local(root, src["path"]).read_bytes().decode(
                "utf-8", "replace")
            verified = _scope_key(survey["scope_id"]) in _scope_key(text)
            reason = "ok" if verified else "attestation_scope_missing"
        basis = "user_attested"
    status = "complete" if (survey["status"] == "complete" and verified) \
        else ("partial" if survey["status"] == "complete"
              else survey["status"])
    return {"scope_label": survey["scope_label"],
            "scope_id": survey["scope_id"], "status": status,
            "evidence": {"verified": verified, "basis": basis,
                         "reason": reason}}


def _scope_key(path_text):
    return unicodedata.normalize("NFC", os.path.normpath(path_text)) \
        if path_text else ""


def _receipt_ok(root, p, survey, ev):
    rec = verify_source(root, p, ev["receipt_source_id"])
    diag = verify_source(root, p, ev["diagnostics_source_id"])
    if rec["state"] != "ok" or diag["state"] != "ok":
        return False, "evidence_source_" + (rec["state"] if rec["state"]
                                            != "ok" else diag["state"])
    rec_path, diag_path = Path(rec["path"]), Path(diag["path"])
    if rec_path.parent != diag_path.parent or rec_path.parts[-3:-1] != (
            "sources", "intake"):
        return False, "receipt_location"
    try:
        sealed = json.loads(gg_core.local(root, rec["path"]).read_bytes())
        diagnostics = json.loads(gg_core.local(root, diag["path"])
                                 .read_bytes())
    except (ValueError, OSError):
        return False, "receipt_unparsable"
    import gg_source_intake
    if not isinstance(sealed, dict) or not isinstance(diagnostics, dict) \
            or not isinstance(sealed.get("files"), list) \
            or not isinstance(diagnostics.get("absolute_root"), str):
        return False, "receipt_unparsable"
    scan_id = sealed.get("scan_id")
    if not isinstance(scan_id, str) or not all(
            _receipt_entry_ok(f) for f in sealed["files"]):
        # One malformed entry makes the whole listing unusable as proof of
        # absence; entries are never silently dropped.
        return False, "receipt_unparsable"
    pre = {k: v for k, v in sealed.items() if k != "scan_id"}
    if gg_source_intake._sha256_text(gg_source_intake._canonical_json(pre)) \
            != scan_id:
        return False, "receipt_self_hash"
    if rec_path.name != scan_id + ".json" \
            or diag_path.name != scan_id + ".diagnostics.json":
        return False, "receipt_pairing"
    if _scope_key(diagnostics.get("absolute_root")) != \
            _scope_key(survey["scope_id"]):
        return False, "receipt_scope"
    return True, "ok"


def _receipt_entry_ok(entry):
    if not isinstance(entry, dict):
        return False
    sha = entry.get("content_sha256")
    return (isinstance(entry.get("relative_id"), str)
            and entry["relative_id"] != ""
            and isinstance(entry.get("type"), str)
            and (sha is None or (isinstance(sha, str) and len(sha) == 64)))


def _receipt_workbooks(root, p, ev):
    rec = p["sources"][ev["receipt_source_id"]]
    sealed = json.loads(gg_core.local(root, rec["path"]).read_bytes())
    found = {}
    for f in sealed["files"]:  # entries validated by _receipt_ok
        rel = f.get("relative_id")
        if f.get("type") == "file" and isinstance(rel, str) \
                and rel.lower().endswith(wr.WORKBOOK_SUFFIXES):
            found[rel] = f.get("content_sha256")
    return found


def _workbook_references(root, p, h, template_states, members):
    inv = h["workbook_reference_set"]
    survey = _survey(root, p, inv["survey"])
    files, link_sources = [], []
    for f in inv["files"]:
        entry = {k: f[k] for k in ("file_id", "label", "origin",
                                   "relative_id", "declared_role",
                                   "role_authority")}
        link = f["link"]
        src = None
        if link["kind"] == "template_ref":
            src = template_states.get(link["ref_id"])
            entry["ref_id"] = link["ref_id"]
            state = src["state"] if src else "dangling_source_ref"
        elif link["kind"] == "project_source":
            src = verify_source(root, p, link["source_id"])
            link_sources.append(link["source_id"])
            state = src["state"]
        else:
            state = "unlinked"
        if src:
            entry["source_id"] = src.get("source_id")
            entry["source_revision"] = src.get("revision")
        if state == "ok":
            ident = wr.read_workbook_identity(gg_core.local(root, src["path"]))
            entry.update(ident)
            if not ident["readable"]:
                state = "unreadable"
        else:
            entry.update({"sha256": None, "readable": False, "sheets": None})
        entry["link_state"] = state
        files.append(entry)
    # Survey file correspondence: receipt workbook paths == inventory
    # major-folder paths, each linked file's hash == the receipt hash.
    if survey["status"] == "complete" and \
            inv["survey"]["evidence"]["kind"] == "source_scan_receipt":
        found = _receipt_workbooks(root, p, inv["survey"]["evidence"])
        majors = {f["relative_id"]: f for f in files
                  if f["origin"] == "major_folder"}
        ok = set(found) == set(majors) and all(
            found[rel] is not None and majors[rel]["sha256"] is not None
            and majors[rel]["sha256"] == found[rel]
            for rel in found)
        if not ok:
            survey["status"] = "partial"
            survey["evidence"].update(verified=False,
                                      reason="receipt_inventory_mismatch")
    sel_state, sel_value, sel_facts = _single_value(
        p, "fruit_trees.workbook.selection")
    if sel_state == "absent":
        selection = {"state": "absent", "file_id": None, "fact_id": None}
    elif sel_state == "conflict":
        selection = {"state": "conflict", "file_id": None,
                     "fact_id": sel_facts}
    elif sel_value == "none":
        selection = {"state": "none", "file_id": None,
                     "fact_id": sel_facts[0]}
    elif not isinstance(sel_value, str) or not sel_value:
        selection = {"state": "invalid", "file_id": None,
                     "fact_id": sel_facts[0]}
    else:
        selection = {"state": "selected", "file_id": sel_value,
                     "fact_id": sel_facts[0]}
    decision = wr.resolve_workbook_references(
        FRUIT, {"survey": survey, "files": files}, selection,
        reference_set=members)
    research = inv["research_selection"]
    decision["selection_drift"] = (
        research is not None and selection["state"] == "selected"
        and research != selection["file_id"]) or (
        research is not None and selection["state"] in ("absent", "none"))
    decision["survey"] = survey
    return decision, link_sources


def _evidence_sources(inv):
    ev = inv["survey"]["evidence"]
    return [ev[k] for k in ("receipt_source_id", "diagnostics_source_id",
                            "attestation_source_id") if k in ev]


# -- instances ---------------------------------------------------------------

def _instances(p):
    facts = [f for f in p["facts"].values()
             if f["field_id"].startswith(PREFIX)]
    by_field, issues = {}, []
    for f in facts:
        by_field.setdefault(f["field_id"], []).append(f)
        m = _INSTANCE.match(f["field_id"])
        if f["field_id"].startswith(tuple(
                "fruit_trees.%s." % g for g in _GROUPS)):
            if not m or m.group(3) not in GROUP_FIELDS[m.group(1)]:
                issues.append({"kind": "invalid_field_id",
                               "fact_id": f["id"],
                               "field_id": f["field_id"]})
                continue
            expected = "%s:%s" % (m.group(1), m.group(2))
            if f.get("scope") != expected:
                issues.append({"kind": "scope_mismatch", "fact_id": f["id"],
                               "expected": expected, "scope": f.get("scope")})
    for fid, group in by_field.items():
        provided = [f for f in group if f["answer_state"] == "provided"]
        if is_yearly(fid):
            periods = [f.get("period") for f in provided
                       if valid_year(f.get("period"))]
            for year in sorted({y for y in periods if periods.count(y) > 1}):
                issues.append({"kind": "duplicate_answer", "field_id": fid,
                               "period": year})
        elif len(provided) > 1:
            issues.append({"kind": "duplicate_answer", "field_id": fid})
    declared = {g: [] for g in _GROUPS}
    seen = set()
    for fid in sorted(by_field):
        m = _INSTANCE.match(fid)
        if m and m.group(3) in GROUP_FIELDS[m.group(1)]:
            key = (m.group(1), m.group(2))
            if key in seen:
                continue
            seen.add(key)
            if any(f["answer_state"] == "provided"
                   for f in by_field.get("fruit_trees.%s.%s.label" % key, [])):
                declared[key[0]].append(key[1])
            else:
                issues.append({"kind": "undeclared_instance",
                               "group": key[0], "id": key[1]})
    return by_field, declared, issues


def _answer_yearly(by_field, field_id):
    """``{"by_period": {YYYY: answer}, "invalid_periods": [fact_id]}``."""
    facts = by_field.get(field_id, [])
    if not facts:
        return None
    by_period, invalid = {}, []
    for f in facts:
        if valid_year(f.get("period")):
            by_period.setdefault(f["period"], []).append(f)
        else:
            invalid.append(f["id"])
    out = {}
    for year, group in sorted(by_period.items()):
        out[year] = _one_answer(group)
    return {"by_period": out, "invalid_periods": sorted(invalid)}


def _duplicate(provided):
    """Several provided facts: no value; ``unit`` is the shared unit, or
    None when the duplicates disagree on it or any unit is not a string."""
    units = [f.get("unit") for f in provided]
    shared = units[0] if all(isinstance(u, str) for u in units) \
        and len(set(units)) == 1 else None
    return {"answer_state": "duplicate_answer", "value": None,
            "unit": shared, "fact_ids": [f["id"] for f in provided]}


def _one_answer(facts):
    provided = [f for f in facts if f["answer_state"] == "provided"]
    if len(provided) > 1:
        return _duplicate(provided)
    f = provided[0] if provided else facts[0]
    return {"answer_state": f["answer_state"],
            "value": f["value"] if f["answer_state"] == "provided" else None,
            "unit": f.get("unit"), "fact_ids": [f["id"]]}


def _answer(by_field, field_id):
    facts = by_field.get(field_id, [])
    if not facts:
        return None
    provided = [f for f in facts if f["answer_state"] == "provided"]
    if len(provided) > 1:
        return _duplicate(provided)
    f = provided[0] if provided else facts[0]
    return {"answer_state": f["answer_state"],
            "value": f["value"] if f["answer_state"] == "provided" else None,
            "unit": f.get("unit"), "fact_ids": [f["id"]]}


def _effective(by_field, chain):
    """First level whose answer is anything but not_provided wins; an
    explicit state (none/not applicable/withheld/unknown) stops the chain."""
    for level, field_id in chain:
        a = _answer(by_field, field_id)
        if a is None or a["answer_state"] == "not_provided":
            continue
        return dict(a, inherited_from=level)
    return {"answer_state": "none", "value": None, "inherited_from": "none"}


def _instance_plan(p, by_field, declared, issues):
    out = {g: {} for g in _GROUPS}
    questions = []
    for group in _GROUPS:
        for iid in declared[group]:
            fields = {}
            for name in GROUP_FIELDS[group]:
                fid = "fruit_trees.%s.%s.%s" % (group, iid, name)
                if is_yearly(fid):
                    fields[name] = _answer_yearly(by_field, fid) or {
                        "by_period": {}, "invalid_periods": []}
                else:
                    a = _answer(by_field, fid)
                    fields[name] = a or {"answer_state": "no_fact",
                                         "value": None, "fact_ids": []}
                questions.append({"field_id": fid,
                                  "action": gg_core.question(p, fid)})
            out[group][iid] = {"fields": fields}
    for iid, cohort in out["cohort"].items():
        f = cohort["fields"]
        ref = f["block_ref"]["value"]
        if f["block_ref"]["answer_state"] == "provided" and not (
                isinstance(ref, str) and _STABLE_ID.match(ref)):
            issues.append({"kind": "invalid_ref_value", "cohort": iid,
                           "block_ref": ref})
            ref = None
        elif f["block_ref"]["answer_state"] == "provided" \
                and ref not in out["block"]:
            issues.append({"kind": "dangling_ref", "cohort": iid,
                           "block_ref": ref})
        for name, allowed in (("origin", _ORIGINS),
                              ("bearing_status", _BEARING)):
            if f[name]["answer_state"] == "provided" \
                    and (not isinstance(f[name]["value"], str)
                         or f[name]["value"] not in allowed):
                issues.append({"kind": "value_not_allowed", "cohort": iid,
                               "field": name, "allowed": list(allowed)})
        block = ref if ref in out["block"] else None
        combo = {}
        for name in _COHORT_OWN:
            combo[name] = _effective(by_field, (
                ("cohort", "fruit_trees.cohort.%s.%s" % (iid, name)),
                ("farm", "fruit_trees." + name)))
        for name in _BLOCK_OWN:
            chain = []
            if block:
                chain.append(("block", "fruit_trees.block.%s.%s"
                              % (block, name)))
            chain.append(("farm", "fruit_trees." + name))
            combo[name] = _effective(by_field, tuple(chain))
        cohort["effective"] = combo
        cohort["combination"] = [combo[k]["value"] for k in (
            "crop_species", "cultivar", "cultivation_form", "heating")]
    return out, questions


# -- reviews -----------------------------------------------------------------

def _plan_reviews(root, p, target_refs):
    target_key = sorted((r["collection"], r["id"]) for r in target_refs)
    reviews = []
    for r in p["reviews"].values():
        if r.get("review_kind") != "plan":
            continue
        refs = r.get("target_refs") or []
        try:
            fp = gg_core.fingerprint(root, p, refs)
            input_state = ("fresh" if fp == r.get("input_fingerprint")
                           else "stale")
        except (KeyError, OSError, ValueError, TypeError):
            input_state = "unresolvable"
        if input_state == "fresh" and sorted(
                (x.get("collection"), x.get("id")) for x in refs) \
                != target_key:
            input_state = "scope_changed"
        try:
            data = gg_core.local(root, r["path"]).read_bytes()
            evidence_state = ("valid" if gg_core.digest(data)
                              == r.get("report_hash")
                              else "report_hash_mismatch")
        except (OSError, ValueError, KeyError):
            evidence_state = "report_missing"
        if evidence_state == "valid":
            obs, why = gg_core.review_observation_state(root, r, p=p)
            if obs in ("invalid", "stale"):
                evidence_state = "observation_invalid"
            elif obs == "legacy_unobserved":
                evidence_state = "valid_legacy_unobserved"
        outcome_ok = (r.get("status") == "pass"
                      and r.get("disposition") == "resolved"
                      and r.get("author_id") != r.get("reviewer_id"))
        reviews.append({
            "id": r["id"], "input_state": input_state,
            "evidence_state": evidence_state,
            "outcome": "pass" if outcome_ok else "not_pass",
            "stored_stale": r.get("stale"),
            "counts_as_current": input_state == "fresh"
            and evidence_state in ("valid", "valid_legacy_unobserved")
            and outcome_ok,
        })
    return reviews


# -- plan --------------------------------------------------------------------

def _held(reason, **detail):
    return dict(mc.common_only_plan(reason), major_id=FRUIT, **detail)


def fruit_plan(root, *, registry=None):
    root = Path(root)
    p = gg_core.load(root)
    registry = registry or mc.default_registry()
    try:
        binding = mc.binding_from_project(registry, p)
    except mc.MajorContractError as error:
        return _held(error.reason)
    if binding.major_id != FRUIT:
        return _held("major_not_fruit_trees", bound=binding.major_id)
    module = registry.resolve(FRUIT)
    members = wr.load_reference_set()
    handoff_view, h = _load_handoff(root, p, members)

    sections = {n.section_id: {"section_id": n.section_id, "role": n.role,
                               "evidence_ids": list(n.evidence_ids),
                               "holds": []}
                for n in module.document_plan}
    missing = {"requirements": [], "appendix_roles": [], "conflicts": [],
               "template_refs": [], "period_model": [], "references": []}
    requirements, conflicts, mappings, workbook = [], [], [], None
    template_states, link_sources, evidence_sources = {}, [], []

    def hold(section_ids, kind, ref, **extra):
        for sid in section_ids:
            if sid in sections:
                sections[sid]["holds"].append(dict(kind=kind, ref=ref,
                                                   **extra))

    if h is None:
        hold(sections, "handoff_unavailable", handoff_view["state"])
    else:
        template_states = _template_states(root, p, h, members)
        missing["template_refs"] = [r for r in EXPECTED_TEMPLATE_REFS
                                    if r not in template_states]
        reqs = {r["id"]: r for r in h["school_requirement_refs"]}
        defined = {c["id"]: c for c in h["known_conflicts"]}
        for rid in sorted(set(EXPECTED_REQUIREMENTS) | set(reqs)):
            r = reqs.get(rid)
            nodes = [s for s, v in sections.items()
                     if rid in v["evidence_ids"]]
            if r is None:
                missing["requirements"].append(rid)
                requirements.append({"id": rid, "status": "missing_requirement",
                                     "sections": nodes})
                hold(nodes, "missing_requirement", rid)
                continue
            status, locs = _check_locators(root, p, r, template_states)
            src_state = (template_states.get(r["source_ref"]) or
                         {"state": "dangling_source_ref"})["state"]
            if src_state != "ok":
                status = "held"
                hold(nodes, "requirement_source", rid, state=src_state)
            elif status in ("locator_mismatch", "missing_locator"):
                hold(nodes, status, rid)
            for cid in r["conflict_ids"]:
                if cid not in defined and cid not in EXPECTED_CONFLICTS:
                    hold(nodes, "unknown_conflict_ref", cid)
                    missing["references"].append(
                        {"kind": "unknown_conflict_ref", "where": rid,
                         "ref": cid})
                    status = "held"
            requirements.append({"id": rid, "title": r["title"],
                                 "status": status, "locators": locs,
                                 "source_state": src_state,
                                 "conflict_ids": r["conflict_ids"],
                                 "sections": nodes})
        for cid in sorted(set(EXPECTED_CONFLICTS) | set(defined)):
            c = defined.get(cid)
            if c is None:
                missing["conflicts"].append(cid)
                effective, affected, xr = ("missing",
                                           FRUIT_CONFLICT_SECTIONS[cid], [])
            else:
                affected, xr = c["affected_sections"], c["xr_refs"]
                effective = c["status"]
                if effective in ("decided", "not_applicable"):
                    ref = template_states.get(
                        c["decision_ref"]["source_ref"])
                    if not ref or ref["state"] != "ok":
                        effective = "decision_unverified"
                unknown = [s for s in affected if s not in sections]
                if unknown:
                    handoff_view["gaps"].append(
                        {"kind": "unknown_section", "where": cid,
                         "ref": unknown})
                    missing["references"].append(
                        {"kind": "unknown_section", "where": cid,
                         "ref": unknown})
                    # An invalid target never cancels a hold: the known
                    # default sections still apply.
                    affected = [x for x in affected if x in sections] + [
                        x for x in FRUIT_CONFLICT_SECTIONS.get(cid, ())
                        if x not in affected]
            resolved = effective in ("decided", "not_applicable")
            if not resolved:
                hold(affected, "conflict", cid, status=effective,
                     xr_refs=xr)
            conflicts.append({"id": cid, "effective_status": effective,
                              "affected_sections": list(affected),
                              "blocked_outputs": (c or {}).get(
                                  "blocked_outputs", []),
                              "xr_refs": xr, "held": not resolved})
        roles = {r["role_no"] for r in h["appendix_roles"]}
        missing["appendix_roles"] = [n for n in EXPECTED_ROLES
                                     if n not in roles]
        for n in missing["appendix_roles"]:
            hold(["appendix_%02d" % n], "missing_appendix_role", n)
        sheets_of = _template_sheets(root, template_states)
        for role in h["appendix_roles"]:
            node = ["appendix_%02d" % role["role_no"]]
            for ref_id, names in role["workbook_sheets"].items():
                state = (template_states.get(ref_id)
                         or {"state": "dangling_source_ref"})["state"]
                if state != "ok":
                    hold(node, "role_source", ref_id, state=state)
                    continue
                absent = [n for n in names
                          if n not in (sheets_of.get(ref_id) or ())]
                if absent:
                    hold(node, "unknown_sheet", ref_id, sheets=absent)
                    missing["references"].append(
                        {"kind": "unknown_sheet", "where": node[0],
                         "ref": ref_id, "sheets": absent})
        if len(h["period_model"]["school_display_years"]) != 10:
            missing["period_model"].append("school_display_years_10")
        for m in h["row_mappings"]:
            src = template_states.get(m["source_ref"])
            state = src["state"] if src else "dangling_source_ref"
            mappings.append({"mapping_id": m["mapping_id"],
                             "status": ("held" if state != "ok"
                                        else m["status"]),
                             "aggregation": m["aggregation"],
                             "source_state": state,
                             "blocked_outputs": m["blocked_outputs"],
                             "xr_refs": m["xr_refs"]})
        for item in h["unresolved_items"]:
            hold(item["blocks"], "unresolved_item", item["id"])
        for gap in handoff_view["gaps"]:
            if gap["kind"] in ("dangling_source_ref", "unknown_sheet"):
                missing["references"].append(gap)
        workbook, link_sources = _workbook_references(
            root, p, h, template_states, members)
        evidence_sources = _evidence_sources(h["workbook_reference_set"])

    by_field, declared, issues = _instances(p)
    instances, questions = _instance_plan(p, by_field, declared, issues)

    availability = []
    for output in DELIVERABLES:
        try:
            mc.authorize_output(output, mc.output_context(root, FRUIT),
                                registry=registry, project=p)
            availability.append({"output": output, "status": "allowed"})
        except mc.OutputHeldError as error:
            availability.append({"output": output, "status": "held",
                                 "reason": error.reason})

    source_ids = []
    if handoff_view.get("source"):
        source_ids.append(handoff_view["source"]["source_id"])
    source_ids += [s["source_id"] for s in template_states.values()]
    source_ids += link_sources + evidence_sources
    unresolvable = sorted({s for s in source_ids if s not in p["sources"]})
    target_refs = [{"collection": "sources", "id": s}
                   for s in sorted(set(source_ids) - set(unresolvable))]
    target_refs += [{"collection": "facts", "id": fid} for fid in sorted(
        f["id"] for f in p["facts"].values()
        if f["field_id"].startswith(PREFIX))]
    try:
        fingerprint = gg_core.fingerprint(root, p, target_refs)
    except (KeyError, OSError, ValueError, TypeError):
        fingerprint = None
    reviews = _plan_reviews(root, p, target_refs)
    current = (not unresolvable and fingerprint is not None
               and any(r["counts_as_current"] for r in reviews))

    complete = h is not None and not any(missing.values())
    return {
        "status": "presented",
        "major_id": FRUIT,
        "module_version": module.module_version,
        "project_revision": p["revision"],
        "binding": {"basis": binding.basis,
                    "evidence": binding.binding_evidence},
        "proposals": {k: v.to_dict() for k, v in
                      mc.capability_proposals(registry, FRUIT).items()},
        "handoff": handoff_view,
        "template_sources": list(template_states.values()),
        "completeness": "complete" if complete else "incomplete",
        "missing": missing,
        "requirements": requirements,
        "sections": [dict(v, status="held" if v["holds"] else "planned")
                     for v in sections.values()],
        "conflicts": conflicts,
        "row_mappings": mappings,
        "workbook_references": workbook,
        "instances": instances,
        "instance_issues": issues,
        "questions": questions,
        "output_availability": availability,
        "plan_target_refs": target_refs,
        "unresolvable_links": unresolvable,
        "plan_input_fingerprint": fingerprint,
        "plan_reviews": reviews,
        "current_plan_review": "fresh" if current else "none",
    }


def main(argv):
    if len(argv) != 1:
        print(json.dumps({"status": "error",
                          "reason": "usage: gg_fruit_plan.py <folder>"},
                         ensure_ascii=False))
        return 2
    try:
        value = fruit_plan(argv[0])
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2, default=list))
    return 0 if value.get("status") == "presented" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
