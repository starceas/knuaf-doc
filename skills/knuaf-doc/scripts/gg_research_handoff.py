"""Research handoff ``knuaf-research-handoff/v1`` — strict, major-neutral.

A handoff carries the research decisions (school requirement locators,
appendix roles, row mappings, known conflicts, period model and the
reference-workbook inventory) from a research folder into a student
project, where it is registered as an ordinary source through
``gg.py apply``.  This module only parses and checks it; it never writes.

Order of judgement:
1. Syntax/structure — duplicate JSON keys, NaN/Infinity, unknown or
   missing keys, wrong types, bad ID formats, duplicate IDs, an
   unresolved row mapping without a hold scope, a decided conflict
   without a decision reference, a wrong ``major_id`` or a self-hash
   mismatch make the whole handoff invalid (``HandoffError``).
2. References — a ``source_ref`` that names no template source, a
   ``conflict_ids`` entry with no ``known_conflicts`` record, or a sheet
   name outside the common reference set is recorded in ``gaps`` (the
   affected item is held by the caller); the handoff stays usable.

The self-hash only shows the file is internally consistent.  It proves
nothing about where the data came from — the caller compares every
``template_source_refs`` entry with the registered source bytes.
"""

import hashlib
import json
import re

SCHEMA = "knuaf-research-handoff/v1"

TOP_KEYS = (
    "schema", "major_id", "template_source_refs", "school_requirement_refs",
    "appendix_roles", "row_mappings", "known_conflicts", "period_model",
    "workbook_reference_set", "unresolved_items", "handoff_sha256",
)

_REF_ID = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_REQ_ID = re.compile(r"^[A-Z]{2,6}-S\d{2}$")
_CONFLICT_ID = re.compile(r"^C\d{2}$")
_XR_ID = re.compile(r"^XR-\d{2}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

SOURCE_ROLES = ("school_template", "professor_reference", "school_example",
                "research_note")
LOCATOR_KINDS = ("xml", "render", "parsed")
AUTHORITIES = ("school_form", "school_example", "derived")
AGGREGATIONS = ("additive", "informational", "unresolved")
MAPPING_STATES = ("mapped", "unresolved")
CONFLICT_STATES = ("unresolved", "decided", "not_applicable")
EVIDENCE_KINDS = ("source_scan_receipt", "user_attestation", "none")
LINK_KINDS = ("template_ref", "project_source", "none")


class HandoffError(ValueError):
    """The handoff is structurally invalid; ``code`` is machine-checkable."""

    def __init__(self, code, where=""):
        self.code = code
        self.where = where
        super().__init__("%s%s" % (code, (" @ " + where) if where else ""))


def canonical_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def compute_hash(handoff):
    """SHA-256 of the handoff without its ``handoff_sha256`` key."""
    body = {k: v for k, v in handoff.items() if k != "handoff_sha256"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def with_hash(handoff):
    """Return a copy carrying its own ``handoff_sha256`` (authoring aid)."""
    out = {k: v for k, v in handoff.items() if k != "handoff_sha256"}
    out["handoff_sha256"] = compute_hash(out)
    return out


def _pairs(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise HandoffError("duplicate_key", key)
        seen[key] = value
    return seen


def _no_constant(name):
    raise HandoffError("non_finite_number", name)


def loads(data):
    """Parse bytes/str strictly (duplicate keys and NaN/Infinity refused)."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HandoffError("not_utf8") from error
    try:
        return json.loads(data, object_pairs_hook=_pairs,
                          parse_constant=_no_constant)
    except HandoffError:
        raise
    except json.JSONDecodeError as error:
        raise HandoffError("json_invalid", str(error)) from error


# -- small structural helpers ---------------------------------------------

def _obj(value, where, required, optional=()):
    if not isinstance(value, dict):
        raise HandoffError("type_error", where)
    keys = set(value)
    missing = set(required) - keys
    if missing:
        raise HandoffError("missing_key", "%s.%s" % (where, sorted(missing)[0]))
    extra = keys - set(required) - set(optional)
    if extra:
        raise HandoffError("unknown_key", "%s.%s" % (where, sorted(extra)[0]))
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise HandoffError("type_error", where)
    return value


def _int(value, where, low=None, high=None):
    if type(value) is not int or (low is not None and value < low) \
            or (high is not None and value > high):
        raise HandoffError("type_error", where)
    return value


def _list(value, where, min_len=0):
    if not isinstance(value, list) or len(value) < min_len:
        raise HandoffError("type_error", where)
    return value


def _ids(values, where, pattern=None):
    _list(values, where)
    seen = set()
    for i, v in enumerate(values):
        _text(v, "%s[%d]" % (where, i))
        if pattern is not None and not pattern.match(v):
            raise HandoffError("id_format", "%s[%d]" % (where, i))
        if v in seen:
            raise HandoffError("duplicate_id", "%s[%d]" % (where, i))
        seen.add(v)
    return values


def _unique(items, key, where, pattern=None):
    seen = set()
    for i, item in enumerate(items):
        value = item[key]
        if key != "role_no" and (not isinstance(value, str)
                                 or not value.strip()):
            raise HandoffError("type_error", "%s[%d].%s" % (where, i, key))
        if pattern is not None and not pattern.match(value):
            raise HandoffError("id_format", "%s[%d].%s" % (where, i, key))
        if value in seen:
            raise HandoffError("duplicate_id", "%s[%d].%s" % (where, i, key))
        seen.add(value)


def _years(values, where, count=None):
    _list(values, where, 1)
    for i, v in enumerate(values):
        _int(v, "%s[%d]" % (where, i), 1900, 2200)
    if any(b != a + 1 for a, b in zip(values, values[1:])):
        raise HandoffError("years_not_consecutive", where)
    if count is not None and len(values) != count:
        raise HandoffError("years_count", where)


# -- structure -----------------------------------------------------------

def _check_structure(h):
    _obj(h, "handoff", TOP_KEYS)
    if h["schema"] != SCHEMA:
        raise HandoffError("schema_mismatch", "schema")
    _text(h["major_id"], "major_id")
    if not isinstance(h["handoff_sha256"], str) \
            or not _HEX64.match(h["handoff_sha256"]):
        raise HandoffError("type_error", "handoff_sha256")

    refs = _list(h["template_source_refs"], "template_source_refs", 1)
    for i, r in enumerate(refs):
        w = "template_source_refs[%d]" % i
        _obj(r, w, ("ref_id", "source_id", "source_revision", "sha256",
                    "role", "rights"))
        _text(r["source_id"], w + ".source_id")
        _int(r["source_revision"], w + ".source_revision", 1)
        if not isinstance(r["sha256"], str) or not _HEX64.match(r["sha256"]):
            raise HandoffError("type_error", w + ".sha256")
        if r["role"] not in SOURCE_ROLES:
            raise HandoffError("type_error", w + ".role")
        _text(r["rights"], w + ".rights")
    _unique(refs, "ref_id", "template_source_refs", _REF_ID)

    reqs = _list(h["school_requirement_refs"], "school_requirement_refs")
    for i, r in enumerate(reqs):
        w = "school_requirement_refs[%d]" % i
        _obj(r, w, ("id", "title", "source_ref", "locators", "authority",
                    "conflict_ids"))
        _text(r["title"], w + ".title")
        _text(r["source_ref"], w + ".source_ref")
        for j, loc in enumerate(_list(r["locators"], w + ".locators")):
            lw = "%s.locators[%d]" % (w, j)
            _obj(loc, lw, ("kind", "ref", "heading"))
            if loc["kind"] not in LOCATOR_KINDS:
                raise HandoffError("type_error", lw + ".kind")
            _text(loc["ref"], lw + ".ref")
            _text(loc["heading"], lw + ".heading")
        if r["authority"] not in AUTHORITIES:
            raise HandoffError("type_error", w + ".authority")
        _ids(r["conflict_ids"], w + ".conflict_ids", _CONFLICT_ID)
    _unique(reqs, "id", "school_requirement_refs", _REQ_ID)

    roles = _list(h["appendix_roles"], "appendix_roles")
    for i, r in enumerate(roles):
        w = "appendix_roles[%d]" % i
        _obj(r, w, ("role_no", "name", "school_tables", "workbook_sheets"))
        _int(r["role_no"], w + ".role_no", 1, 99)
        _text(r["name"], w + ".name")
        _ids(_list(r["school_tables"], w + ".school_tables", 1),
             w + ".school_tables")
        sheets = r["workbook_sheets"]
        if not isinstance(sheets, dict):
            raise HandoffError("type_error", w + ".workbook_sheets")
        for ref_id, names in sheets.items():
            if not _REF_ID.match(ref_id):
                raise HandoffError("id_format", w + ".workbook_sheets")
            for k, name in enumerate(_list(names, w + ".workbook_sheets")):
                if not isinstance(name, str) or not name:
                    raise HandoffError("type_error", "%s.workbook_sheets.%s[%d]"
                                       % (w, ref_id, k))
    _unique(roles, "role_no", "appendix_roles")

    maps = _list(h["row_mappings"], "row_mappings")
    for i, m in enumerate(maps):
        w = "row_mappings[%d]" % i
        _obj(m, w, ("mapping_id", "school_table", "school_row", "source_ref",
                    "source_locator", "indicator", "unit", "period",
                    "aggregation", "status", "conflict_ids",
                    "blocked_outputs", "xr_refs"))
        for key in ("school_table", "school_row", "source_ref",
                    "source_locator", "indicator", "unit", "period"):
            _text(m[key], "%s.%s" % (w, key))
        if m["aggregation"] not in AGGREGATIONS \
                or m["status"] not in MAPPING_STATES:
            raise HandoffError("type_error", w)
        _ids(m["conflict_ids"], w + ".conflict_ids", _CONFLICT_ID)
        _ids(m["blocked_outputs"], w + ".blocked_outputs")
        _ids(m["xr_refs"], w + ".xr_refs", _XR_ID)
        if m["status"] == "unresolved" and not (
                m["conflict_ids"] or m["blocked_outputs"]):
            raise HandoffError("unresolved_without_hold", w)
    _unique(maps, "mapping_id", "row_mappings")

    conflicts = _list(h["known_conflicts"], "known_conflicts")
    for i, c in enumerate(conflicts):
        w = "known_conflicts[%d]" % i
        _obj(c, w, ("id", "summary", "status", "affected_sections",
                    "blocked_outputs", "decision_owner", "decision_ref",
                    "xr_refs"))
        _text(c["summary"], w + ".summary")
        if c["status"] not in CONFLICT_STATES:
            raise HandoffError("type_error", w + ".status")
        _ids(_list(c["affected_sections"], w + ".affected_sections", 1),
             w + ".affected_sections")
        _ids(c["blocked_outputs"], w + ".blocked_outputs")
        _text(c["decision_owner"], w + ".decision_owner")
        _ids(c["xr_refs"], w + ".xr_refs", _XR_ID)
        ref = c["decision_ref"]
        if c["status"] in ("decided", "not_applicable"):
            if ref is None:
                raise HandoffError("decision_ref_required", w)
        if ref is not None:
            _obj(ref, w + ".decision_ref", ("source_ref", "locator"))
            _text(ref["source_ref"], w + ".decision_ref.source_ref")
            _text(ref["locator"], w + ".decision_ref.locator")
    _unique(conflicts, "id", "known_conflicts", _CONFLICT_ID)

    period = _obj(h["period_model"], "period_model",
                  ("school_display_years", "workbook_base_years",
                   "unresolved"))
    _years(period["school_display_years"], "period_model.school_display_years")
    if not isinstance(period["workbook_base_years"], dict):
        raise HandoffError("type_error", "period_model.workbook_base_years")
    for ref_id, years in period["workbook_base_years"].items():
        if not _REF_ID.match(ref_id):
            raise HandoffError("id_format", "period_model.workbook_base_years")
        _years(years, "period_model.workbook_base_years." + ref_id)
    _list(period["unresolved"], "period_model.unresolved")
    for i, v in enumerate(period["unresolved"]):
        _text(v, "period_model.unresolved[%d]" % i)

    _check_inventory(h["workbook_reference_set"])

    items = _list(h["unresolved_items"], "unresolved_items")
    for i, u in enumerate(items):
        w = "unresolved_items[%d]" % i
        _obj(u, w, ("id", "summary", "blocks"))
        _text(u["summary"], w + ".summary")
        _ids(_list(u["blocks"], w + ".blocks", 1), w + ".blocks")
    _unique(items, "id", "unresolved_items")


def _check_inventory(inv):
    w = "workbook_reference_set"
    _obj(inv, w, ("survey", "files", "research_selection"))
    survey = _obj(inv["survey"], w + ".survey",
                  ("scope_label", "scope_id", "status", "evidence"))
    _text(survey["scope_label"], w + ".survey.scope_label")
    _text(survey["scope_id"], w + ".survey.scope_id")
    if survey["status"] not in ("complete", "partial", "unreadable",
                                "unspecified"):
        raise HandoffError("type_error", w + ".survey.status")
    ev = survey["evidence"]
    kind = ev.get("kind") if isinstance(ev, dict) else None
    if kind not in EVIDENCE_KINDS:
        raise HandoffError("type_error", w + ".survey.evidence.kind")
    need = {"source_scan_receipt": ("receipt_source_id",
                                    "diagnostics_source_id"),
            "user_attestation": ("attestation_source_id",),
            "none": ()}[kind]
    _obj(ev, w + ".survey.evidence", ("kind",) + need)
    for key in need:
        _text(ev[key], "%s.survey.evidence.%s" % (w, key))
    files = _list(inv["files"], w + ".files")
    for i, f in enumerate(files):
        fw = "%s.files[%d]" % (w, i)
        _obj(f, fw, ("file_id", "label", "origin", "relative_id",
                     "declared_role", "role_authority", "link"))
        _text(f["file_id"], fw + ".file_id")
        _text(f["label"], fw + ".label")
        if f["origin"] not in ("major_folder", "common_root"):
            raise HandoffError("type_error", fw + ".origin")
        if f["origin"] == "major_folder":
            _text(f["relative_id"], fw + ".relative_id")
        elif f["relative_id"] is not None:
            _text(f["relative_id"], fw + ".relative_id")
        if f["declared_role"] not in ("school_template",
                                      "professor_reference",
                                      "student_example", "current_work",
                                      "unclassified"):
            raise HandoffError("type_error", fw + ".declared_role")
        auth = _obj(f["role_authority"], fw + ".role_authority",
                    ("kind", "ref"))
        if auth["kind"] not in ("user_statement", "document", "none"):
            raise HandoffError("type_error", fw + ".role_authority.kind")
        if auth["kind"] != "none":
            _text(auth["ref"], fw + ".role_authority.ref")
        elif auth["ref"] is not None:
            raise HandoffError("type_error", fw + ".role_authority.ref")
        link = f["link"]
        lkind = link.get("kind") if isinstance(link, dict) else None
        if lkind not in LINK_KINDS:
            raise HandoffError("type_error", fw + ".link.kind")
        lneed = {"template_ref": ("ref_id",),
                 "project_source": ("source_id",), "none": ()}[lkind]
        _obj(link, fw + ".link", ("kind",) + lneed)
        for key in lneed:
            _text(link[key], "%s.link.%s" % (fw, key))
    _unique(files, "file_id", w + ".files")
    rels = [f["relative_id"] for f in files
            if f["origin"] == "major_folder"]
    if len(set(rels)) != len(rels):
        raise HandoffError("duplicate_id", w + ".files.relative_id")
    sel = inv["research_selection"]
    if sel is not None:
        _text(sel, w + ".research_selection")


# -- public ----------------------------------------------------------------

def validate(handoff, *, major_id, reference_sheets=None):
    """Check a parsed handoff.  Raises ``HandoffError`` for structural
    faults; returns ``{"handoff", "gaps"}`` where ``gaps`` lists the
    non-fatal reference gaps the caller must hold on.

    ``reference_sheets`` maps a common ref_id (X01/X02) to its exact
    sheet names; appendix sheet names outside it are reported."""
    _check_structure(handoff)
    if handoff["major_id"] != major_id:
        raise HandoffError("major_mismatch", "major_id")
    if compute_hash(handoff) != handoff["handoff_sha256"]:
        raise HandoffError("self_hash_mismatch", "handoff_sha256")
    ref_ids = {r["ref_id"] for r in handoff["template_source_refs"]}
    conflict_ids = {c["id"] for c in handoff["known_conflicts"]}
    gaps = []

    def source_gap(value, where):
        if value not in ref_ids:
            gaps.append({"kind": "dangling_source_ref", "where": where,
                         "ref": value})

    for r in handoff["school_requirement_refs"]:
        source_gap(r["source_ref"], r["id"])
        for cid in r["conflict_ids"]:
            if cid not in conflict_ids:
                gaps.append({"kind": "dangling_conflict_ref",
                             "where": r["id"], "ref": cid})
    for m in handoff["row_mappings"]:
        source_gap(m["source_ref"], m["mapping_id"])
        for cid in m["conflict_ids"]:
            if cid not in conflict_ids:
                gaps.append({"kind": "dangling_conflict_ref",
                             "where": m["mapping_id"], "ref": cid})
    for c in handoff["known_conflicts"]:
        if c["decision_ref"] is not None:
            source_gap(c["decision_ref"]["source_ref"], c["id"])
    for f in handoff["workbook_reference_set"]["files"]:
        if f["link"]["kind"] == "template_ref":
            source_gap(f["link"]["ref_id"], f["file_id"])
    for role in handoff["appendix_roles"]:
        for ref_id, names in role["workbook_sheets"].items():
            source_gap(ref_id, "appendix_role_%d" % role["role_no"])
            known = (reference_sheets or {}).get(ref_id)
            if known is not None:
                for name in names:
                    if name not in known:
                        gaps.append({"kind": "unknown_sheet",
                                     "where": "appendix_role_%d"
                                     % role["role_no"],
                                     "ref": "%s:%s" % (ref_id, name)})
    return {"handoff": handoff, "gaps": gaps}


def load_and_validate(data, *, major_id, reference_sheets=None):
    return validate(loads(data), major_id=major_id,
                    reference_sheets=reference_sheets)
