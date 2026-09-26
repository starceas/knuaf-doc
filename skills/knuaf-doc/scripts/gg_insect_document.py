"""Read-only industrial-insects document plan built on the common major contract.

Precedents are examples, never mandatory headings or student defaults.  This
module returns a review plan; canonical writes and rendered-paper approval stay
with the common project transaction and output paths.
"""

import json
import re
from pathlib import Path

import gg_core
import gg_major_contract as contract


DEFAULT_PRECEDENTS = (
    Path(__file__).resolve().parents[1]
    / "references" / "industrial-insects" / "precedents.json"
)

MAJOR_ID = "industrial_insects"
LINE_PREFIX = "industrial_insects.line."
FIELD_INVENTORY = "industrial_insects.line_inventory"
FIELD_RETIRED = "industrial_insects.line_retired"
FIELD_PLAN_YEARS = "industrial_insects.plan_years"
_LINE_TEMPLATE = "industrial_insects.line.{id}."
_YEAR_TEMPLATE = "industrial_insects.line.{id}.year.{yyyy}."

# Line instance grammar (design §2.3): stable ids are ``l`` + 2-3 digits,
# plan years are four digits.  Concrete facts carry the id in the field id.
# Whole-token grammar uses fullmatch so a trailing newline never validates.
_LINE_ID = re.compile(r"l[0-9]{2,3}")
_YEAR = re.compile(r"[0-9]{4}")
_LINE_FIELD = re.compile(r"industrial_insects\.line\.(l[0-9]{2,3})\."
                         r"([a-z_]+)")
_LINE_YEAR_FIELD = re.compile(r"industrial_insects\.line\.(l[0-9]{2,3})"
                              r"\.year\.([0-9]{4})\.([a-z_]+)")
_LINE_ANY_ID = re.compile(r"^industrial_insects\.line\.(l[0-9]{2,3})\.")
_LINE_ANY_YEAR = re.compile(r"^industrial_insects\.line\.(l[0-9]{2,3})"
                            r"\.year\.([0-9]{4})\.")

_PRODUCT_ROLES = ("main", "byproduct", "processed", "service")
_REF_RULES = {
    "source_line": {"byproduct", "service"},
    "input_line": {"processed"},
}
_COMPANIONS = (
    ("survival_rate", "survival_basis"),
    ("stocking_input", "stocking_unit"),
)
# Answer states that count as "answered" for the related-fact pointer.
_ANSWERED_STATES = {"provided", "explicit_none", "not_applicable",
                    "withheld"}

# Same-topic project summary field behind each line field (design §2.5):
# the plan emits a fact pointer (id/revision/field_id, never a value) so a
# helper can ask whether the earlier project answer applies to this line.
# Deliberately renamed line fields point at the project field whose topic
# they refine; fields with no project counterpart are absent here.
_RELATED_PROJECT_FIELDS = {
    "species": "species",
    "purpose": "purpose",
    "product_form": "product_form",
    "cycle_days": "cycle_days",
    "cycles_per_year": "cohort_or_batch",
    "first_year_sold_cycles": "cohort_or_batch",
    "stocking_input": "stocking_input",
    "stocking_unit": "stocking_input",
    "stock_source": "stocking_input",
    "survival_rate": "survival_or_loss",
    "survival_basis": "survival_or_loss",
    "saleable_per_cycle": "saleable_yield",
    "sale_unit": "unit",
    "feed_or_substrate": "feed_or_substrate",
    "feed_supply": "feed_or_substrate",
    "channel": "channel",
    "price_basis": "price_date",
    "unit_price": "price_date",
}

ROSTER = {"F0", "E1", "E2", "E3", "E4"}
SCHEMA_ID = "insect-precedents/v2"

# Closed verification-code table for the precedent map.  The map's own
# verification_codes declaration must reproduce these codes and usable
# flags exactly; meaning text lives in the map only.
VERIFICATION_CODES = {
    "text_layer_page_checked": True,
    "text_layer_contents_checked": True,
    "caption_text_checked": True,
    "caption_and_table_index_text_checked": True,
    "page_text_checked_and_w21_continuation_map": True,
    "under_20_extracted_chars; render_or_ocr_pending": False,
}

_TOP_KEYS = {"schema", "sources", "verification_codes", "observations"}
_SOURCE_KEYS = {
    "id", "sha256", "physical_pages", "authority",
    "title", "author", "title_basis",
}
_TITLE_BASES = {"first_text_page", "page_1_render", "not_present_in_source"}
_OBSERVATION_KEYS = {
    "source_id", "physical_page", "object_label", "role", "status",
    "verification",
}
_CODE_KEYS = {"usable", "meaning"}
_SELECTION_KEYS = {
    "section_id", "action", "reason", "role_fit_reason",
    "observation_refs", "source_refs",
}
_REF_KEYS = {"source_id", "physical_page", "object_label"}
_SOURCE_REF_KEYS = {"id", "locator"}

# These are relevance hints for example locators, not required headings.
# A different placement can be proposed only as an adaptation for review.
SECTION_ROLES = {
    "preface": {"introduction", "front_matter_and_summary"},
    "farm_status": {"farm_and_production_context"},
    "environment_analysis": {
        "environment_and_strategy", "human_resources", "noncurrent_assets",
        "profitability_summary", "climate_context",
        "regional_agriculture_context", "industry_survey_context",
        "swot_analysis", "swot_strategy",
    },
    "vision_goals": {"vision_and_goals"},
    "detailed_plan": {
        "assets_investment_production_and_marketing", "financial_plan",
        "cash_flow", "operating_expenses", "production_cost",
        "production_plan", "investment_plan", "depreciation",
        "input_purchases", "profit_and_loss", "profitability_analysis",
        "financial_position", "land_and_asset_basis", "annual_investment",
        "itemized_investment_and_funding", "loan_repayment",
        "repayment_summary", "production_and_sales", "labor_cost",
        "monthly_labor",
    },
    "closing": {"closing_plan_and_conclusion"},
    "sources_appendix": {"references_and_back_matter"},
}


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _strict_loads(text):
    return json.loads(
        text,
        object_pairs_hook=_no_duplicates,
        parse_constant=_reject_constant,
    )


def parse_selection(text):
    """Parse a selections JSON text; duplicate keys and NaN/Infinity fail.

    Raises a fixed ValueError code only, no raw parser diagnostics, so
    callers can print the reason without leaking input content or paths.
    """
    if not isinstance(text, (str, bytes)):
        raise ValueError("insect_selection_invalid:json")
    try:
        return _strict_loads(text)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("insect_selection_invalid:json") from None


def _anchor(item):
    return (item["source_id"], item["physical_page"], item["object_label"])


def load_precedents(path=DEFAULT_PRECEDENTS):
    """Validate a compact public locator map without opening private PDFs.

    Single validator shared by the document plan and the standalone
    precedent checker: closed key sets, non-bool page numbers, a declared
    verification-code table identical to VERIFICATION_CODES, and only
    registered codes on observations.
    """
    doc = _strict_loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or set(doc) != _TOP_KEYS:
        raise ValueError("precedent map keys invalid")
    if doc["schema"] != SCHEMA_ID:
        raise ValueError("precedent map schema invalid")
    declared = doc["verification_codes"]
    if not isinstance(declared, dict):
        raise ValueError("precedent verification codes invalid")
    if set(declared) != set(VERIFICATION_CODES):
        raise ValueError("precedent verification codes differ from contract")
    for code, entry in declared.items():
        if not isinstance(entry, dict) or set(entry) != _CODE_KEYS:
            raise ValueError("precedent verification code entry invalid")
        if entry["usable"] is not VERIFICATION_CODES[code]:
            raise ValueError("precedent code usability differs from contract")
        if not isinstance(entry["meaning"], str) or not entry["meaning"]:
            raise ValueError("precedent code meaning invalid")
    sources = doc["sources"]
    observations = doc["observations"]
    if not isinstance(sources, list) or not isinstance(observations, list):
        raise ValueError("precedent map needs sources and observations")
    ids = set()
    page_counts = {}
    for source in sources:
        if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
            raise ValueError("invalid precedent source")
        sid = source["id"]
        digest = source["sha256"]
        if not isinstance(sid, str) or sid in ids:
            raise ValueError("duplicate or invalid precedent source")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            ch not in "0123456789abcdef" for ch in digest
        ):
            raise ValueError("invalid precedent source hash")
        if source["authority"] != "example_observed":
            raise ValueError("precedent authority must be example_observed")
        count = source["physical_pages"]
        if type(count) is not int or count < 1:
            raise ValueError("invalid precedent page count")
        for key in ("title", "author"):
            if not (isinstance(source[key], str) or source[key] is None):
                raise ValueError("invalid precedent source identifier")
        if source["title_basis"] not in _TITLE_BASES:
            raise ValueError("invalid precedent title basis")
        page_counts[sid] = count
        ids.add(sid)
    if ids != ROSTER:
        raise ValueError("precedent roster differs from contract")
    anchors = set()
    for item in observations:
        if not isinstance(item, dict) or set(item) != _OBSERVATION_KEYS:
            raise ValueError("invalid precedent observation")
        anchor = _anchor(item)
        sid, page, label = anchor
        if (sid not in ids or type(page) is not int or page < 1
                or page > page_counts[sid]):
            raise ValueError("invalid precedent locator")
        if not isinstance(label, str) or not label or anchor in anchors:
            raise ValueError("duplicate or invalid precedent locator")
        if not isinstance(item["role"], str) or not item["role"]:
            raise ValueError("invalid precedent role")
        if item["status"] != "example_observed":
            raise ValueError("observation is not an example")
        if not isinstance(item["verification"], str) or (
            item["verification"] not in VERIFICATION_CODES
        ):
            raise ValueError("observation uses unregistered verification code")
        anchors.add(anchor)
    return doc


def question_view(project):
    """Read-only project view without superseded facts (questions kept)."""
    facts = {
        fid: fact
        for fid, fact in (project.get("facts") or {}).items()
        if not isinstance(fact, dict)
        or fact.get("verification") != "superseded"
    }
    return contract.canonical_view({
        "facts": facts,
        "questions": project.get("questions") or {},
    })


def question_states(module, project):
    """Expose question verdicts and answer states; no private values.

    question_status is the common gg_core.question verdict on the
    superseded-filtered view; reuse only means no re-ask is needed, never
    verification or approval.  answer_states lists normalized states of
    active (non-superseded) facts for the field, values omitted.
    """
    view = question_view(project)
    fields = {q.field_id for q in module.question_schema}
    active_by_field = {}
    for fact in (project.get("facts") or {}).values():
        if not isinstance(fact, dict):
            continue
        field = fact.get("field_id")
        if field not in fields or fact.get("verification") == "superseded":
            continue
        active_by_field.setdefault(field, []).append(fact)
    states = {}
    for q in module.question_schema:
        active = active_by_field.get(q.field_id, [])
        answer_states = sorted(
            {
                contract.normalize_answer_state(fact.get("answer_state"))
                for fact in active
            }
        ) or ["not_provided"]
        flags = []
        if len(active) >= 2:
            flags.append("multiple_active")
        if any(fact.get("verification") == "disputed" for fact in active):
            flags.append("disputed")
        states[q.field_id] = {
            "question_status": gg_core.question(view, q.field_id),
            "answer_states": answer_states,
            "flags": flags,
        }
    return states


# -- line / plan-year instances (design §2.2-2.7, §11, §12) --------------------

def _active_by_field(project):
    """Non-superseded facts grouped by field_id (read-only scan)."""
    by_field = {}
    for fact in (project.get("facts") or {}).values():
        if not isinstance(fact, dict):
            continue
        if fact.get("verification") == "superseded":
            continue
        by_field.setdefault(fact.get("field_id"), []).append(fact)
    return by_field


def _line_field_state(view, field_id, active):
    """Common question verdict + answer states + flags, values omitted.

    ``usable`` is False when the field's active facts are ambiguous
    (multiple_active or disputed): the plan never treats the field as a
    confirmed value.  question_status is the shared gg_core verdict on the
    superseded-filtered view — line fields never inherit project answers.
    """
    answer_states = sorted(
        {
            contract.normalize_answer_state(fact.get("answer_state"))
            for fact in active
        }
    ) or ["not_provided"]
    flags = []
    if len(active) >= 2:
        flags.append("multiple_active")
    if any(fact.get("verification") == "disputed" for fact in active):
        flags.append("disputed")
    return {
        "question_status": gg_core.question(view, field_id),
        "answer_states": answer_states,
        "flags": flags,
        "usable": not flags,
    }


def _declared_tokens(facts, pattern):
    """Union of pattern-matching tokens across provided list facts.

    Declaration facts are comma-joined id lists ("l01,l02").  Tokens that
    fail the grammar are not ids and never become instances; they mark the
    list with ``inventory_mismatch``.  Conflicting provided lists keep the
    union (both declarations are reported) and also mark the mismatch.
    """
    tokens, raw_lists, malformed = [], [], False
    for fact in facts:
        if fact.get("answer_state") != "provided":
            continue
        value = fact.get("value")
        if not isinstance(value, str):
            malformed = True
            continue
        parts = [token.strip() for token in value.split(",")]
        raw_lists.append(tuple(parts))
        for token in parts:
            if pattern.fullmatch(token):
                if token not in tokens:
                    tokens.append(token)
            else:
                malformed = True
    differ = len(set(raw_lists)) > 1
    return tokens, malformed, differ


def _has_provided(facts):
    return any(f.get("answer_state") == "provided" for f in facts)


def _single_provided_value(facts):
    """Raw value of the sole provided fact, else None (never emitted raw
    for ref fields unless it validates against the declared id grammar)."""
    provided = [f for f in facts if f.get("answer_state") == "provided"]
    if len(provided) != 1:
        return None
    return provided[0].get("value")


def _dependency_superseded(facts, all_facts):
    """A line fact whose depends_on target is now superseded (§11.4)."""
    for fact in facts:
        for dep in fact.get("depends_on") or []:
            if not isinstance(dep, dict) or dep.get("collection") != "facts":
                continue
            target = all_facts.get(dep.get("id"))
            if isinstance(target, dict) and (
                    target.get("verification") == "superseded"):
                return True
    return False


def _line_template_names(module):
    """Concrete field names behind the declared line/year templates."""
    line_names, year_names = [], []
    for q in module.question_schema:
        fid = q.field_id
        if fid.startswith(_YEAR_TEMPLATE):
            year_names.append(fid[len(_YEAR_TEMPLATE):])
        elif fid.startswith(_LINE_TEMPLATE):
            line_names.append(fid[len(_LINE_TEMPLATE):])
    return line_names, year_names


def _invalid_field_issue(field_id):
    """Diagnose by status code, never by echoing the raw field_id: only
    grammar-verified line id/year tokens inside it may be reported
    (§2.6 — diagnostics carry status codes, not unvalidated input text)."""
    issue = {"kind": "invalid_field_id"}
    ym = _LINE_ANY_YEAR.match(field_id)
    if ym:
        issue["line_id"] = ym.group(1)
        issue["year"] = ym.group(2)
    else:
        lm = _LINE_ANY_ID.match(field_id)
        if lm:
            issue["line_id"] = lm.group(1)
    return issue


def _reserved_line_ids(facts, questions):
    """Every grammar-valid line id that ever appears in canonical: all
    industrial_insects.line.<id>.* fact field ids (every state, superseded
    included), every line_inventory/line_retired fact's list tokens (every
    state), and every question record key (§11.5 — a used number is never
    reissued)."""
    seen = set()
    for fact in facts.values():
        if not isinstance(fact, dict):
            continue
        field_id = fact.get("field_id")
        if not isinstance(field_id, str):
            continue
        match = _LINE_ANY_ID.match(field_id)
        if match:
            seen.add(match.group(1))
        elif field_id in (FIELD_INVENTORY, FIELD_RETIRED):
            value = fact.get("value")
            if isinstance(value, str):
                for token in value.split(","):
                    token = token.strip()
                    if _LINE_ID.fullmatch(token):
                        seen.add(token)
    for key in questions:
        match = _LINE_ANY_ID.match(key) if isinstance(key, str) else None
        if match:
            seen.add(match.group(1))
    return seen


def _next_line_id(seen):
    """First number above every reserved id, or None when the next number
    would exceed the ``l[0-9]{2,3}`` grammar ceiling (l999)."""
    top = max((int(lid[1:]) for lid in seen), default=0)
    if top >= 999:
        return None
    return "l%02d" % (top + 1)


def _ref_state(state, facts, lid, field_name, role, role_uncertain,
               declared, retired, retired_list_uncertain, role_of):
    """source_line/input_line: echo only a declared-id value (§2.6).

    Reference-rule verdicts only use *confirmed* roles: a target whose
    role facts are ambiguous or absent makes the reference
    ``referenced_role_uncertain`` + unusable instead of a definitive
    violation; an untrusted retired list yields ``retired_uncertain``
    rather than ``retired_ref``.
    """
    flags = set(state["flags"])
    candidate = _single_provided_value(facts)
    allowed = _REF_RULES[field_name]
    if candidate is None:
        state["value"] = None
    elif not isinstance(candidate, str) or not _LINE_ID.fullmatch(candidate):
        state["value"] = {"state": "invalid_ref"}
        flags.add("invalid_ref")
    elif candidate == lid:
        state["value"] = candidate
        flags.add("role_ref_rule")
    elif candidate in retired:
        state["value"] = candidate
        if retired_list_uncertain:
            flags.add("retired_uncertain")
            state["usable"] = False
        else:
            flags.add("retired_ref")
    elif candidate not in declared:
        state["value"] = {"state": "invalid_ref"}
        flags.add("invalid_ref")
    else:
        state["value"] = candidate
        # A byproduct/service line hangs off a *main* line (§2.7); a
        # confirmed non-main role violates the rule, while an
        # unconfirmed role makes the reference uncertain, not a verdict.
        target_role = role_of.get(candidate)
        if field_name == "source_line" and target_role == "main":
            pass
        elif target_role is None:
            flags.add("referenced_role_uncertain")
            state["usable"] = False
        elif field_name == "source_line":
            flags.add("role_ref_rule")
    if lid not in role_uncertain and (
            (role in allowed) != _has_provided(facts)):
        flags.add("role_ref_rule")
    state["flags"] = sorted(flags)
    return state


def _line_state(module, project):
    """Line/plan-year instance plan keys — structure ids only, no values."""
    view = question_view(project)
    facts = project.get("facts") or {}
    questions = project.get("questions") or {}
    line_names, year_names = _line_template_names(module)
    line_name_set, year_name_set = set(line_names), set(year_names)

    by_field = _active_by_field(project)
    line_facts, year_facts, ids_with_facts, issues = {}, {}, set(), []
    for field_id, flist in by_field.items():
        if not (isinstance(field_id, str)
                and field_id.startswith(LINE_PREFIX)):
            continue
        ym = _LINE_YEAR_FIELD.fullmatch(field_id)
        if ym and ym.group(3) in year_name_set:
            year_facts.setdefault(ym.groups(), []).extend(flist)
            ids_with_facts.add(ym.group(1))
            continue
        lm = _LINE_FIELD.fullmatch(field_id)
        if lm and lm.group(2) in line_name_set:
            line_facts.setdefault(lm.groups(), []).extend(flist)
            ids_with_facts.add(lm.group(1))
            continue
        for _ in flist:
            issues.append(_invalid_field_issue(field_id))

    declared, inv_bad, inv_diff = _declared_tokens(
        by_field.get(FIELD_INVENTORY, []), _LINE_ID)
    retired, ret_bad, ret_diff = _declared_tokens(
        by_field.get(FIELD_RETIRED, []), _LINE_ID)
    plan_years, yr_bad, yr_diff = _declared_tokens(
        by_field.get(FIELD_PLAN_YEARS, []), _YEAR)
    declared, retired, year_set = (
        set(declared), set(retired), set(plan_years))

    # The retired list gets the same state shape as the inventory.  When
    # it carries any integrity state (disputed, multiple_active, malformed
    # tokens, conflicting lists) it cannot ground a definitive verdict:
    # retired_instance/retired_ref become retired_uncertain.
    line_retired = _line_field_state(
        view, FIELD_RETIRED, by_field.get(FIELD_RETIRED, []))
    if ret_bad or ret_diff:
        line_retired["flags"] = sorted(
            set(line_retired["flags"]) | {"inventory_mismatch"})
    retired_list_uncertain = bool(line_retired["flags"])

    label_ok = {
        lid for (lid, name), fl in line_facts.items()
        if name == "label" and _has_provided(fl)
    }
    live = sorted(
        lid for lid in declared
        if (lid not in retired or retired_list_uncertain)
        and lid in label_ok)
    # A line's role is confirmed only when its product_role active facts
    # are a single clean provided value; anything else (disputed,
    # multiple_active, mixed/unknown/absent) is unconfirmed and must not
    # ground reference-rule verdicts.
    role_of, role_uncertain = {}, set()
    for lid in live:
        role_facts = line_facts.get((lid, "product_role"), [])
        role_state = _line_field_state(
            view, LINE_PREFIX + lid + ".product_role", role_facts)
        if role_state["usable"] and role_state["answer_states"] == [
                "provided"]:
            role_of[lid] = _single_provided_value(role_facts)
        else:
            role_of[lid] = None
            if role_facts:
                role_uncertain.add(lid)
    instances = []
    for lid in sorted(declared | retired | ids_with_facts | label_ok):
        flags = set()
        if lid in retired:
            flags.add("retired_uncertain" if retired_list_uncertain
                      else "retired_instance")
        if lid in declared and lid in retired:
            flags.add("inventory_mismatch")
        if lid not in declared and lid not in retired:
            flags.add("undeclared_instance")
        if lid in declared and lid not in retired and lid not in label_ok:
            flags.add("inventory_mismatch")
        instances.append({"id": lid, "flags": sorted(flags)})

    line_inventory = _line_field_state(
        view, FIELD_INVENTORY, by_field.get(FIELD_INVENTORY, []))
    inv_flags = set(line_inventory["flags"])
    if (inv_bad or inv_diff or declared & retired
            or any(lid not in label_ok for lid in declared - retired)):
        inv_flags.add("inventory_mismatch")
    line_inventory["flags"] = sorted(inv_flags)

    plan_years_state = _line_field_state(
        view, FIELD_PLAN_YEARS, by_field.get(FIELD_PLAN_YEARS, []))
    if yr_bad or yr_diff:
        plan_years_state["flags"] = sorted(
            set(plan_years_state["flags"]) | {"inventory_mismatch"})

    line_questions, related = {}, {}
    for lid in live:
        expected_scope = "line:" + lid
        entry = {}
        for name in line_names:
            fid = LINE_PREFIX + lid + "." + name
            fl = line_facts.get((lid, name), [])
            st = _line_field_state(view, fid, fl)
            flags = set(st["flags"])
            if any(f.get("scope") != expected_scope for f in fl):
                flags.add("scope_mismatch")
            if _dependency_superseded(fl, facts):
                flags.add("dependency_superseded")
            st["flags"] = sorted(flags)
            entry[name] = st
        role = role_of[lid]
        if role is not None and role not in _PRODUCT_ROLES:
            entry["product_role"]["flags"] = sorted(
                set(entry["product_role"]["flags"]) | {"role_ref_rule"})
        for name in _REF_RULES:
            entry[name] = _ref_state(
                entry[name], line_facts.get((lid, name), []), lid, name,
                role, role_uncertain, declared, retired,
                retired_list_uncertain, role_of)
        for name, comp in _COMPANIONS:
            if _has_provided(line_facts.get((lid, name), [])) and not (
                    _has_provided(line_facts.get((lid, comp), []))):
                entry[name]["flags"] = sorted(
                    set(entry[name]["flags"]) | {"missing_companion"})
        years = {}
        year_keys = year_set | {
            year for (other, year, _name) in year_facts if other == lid}
        for year in sorted(year_keys):
            yentry = {}
            for name in year_names:
                fid = "%s%s.year.%s.%s" % (LINE_PREFIX, lid, year, name)
                fl = year_facts.get((lid, year, name), [])
                st = _line_field_state(view, fid, fl)
                flags = set(st["flags"])
                if year not in year_set:
                    flags.add("undeclared_instance")
                    st["usable"] = False
                if any(f.get("scope") != expected_scope for f in fl):
                    flags.add("scope_mismatch")
                if _dependency_superseded(fl, facts):
                    flags.add("dependency_superseded")
                st["flags"] = sorted(flags)
                yentry[name] = st
            years[year] = yentry
        entry["years"] = years
        line_questions[lid] = entry

        pointers = {}
        for name in line_names:
            proj_name = _RELATED_PROJECT_FIELDS.get(name)
            if proj_name is None:
                continue
            if entry[name]["question_status"] == "reuse":
                continue
            proj_field = "industrial_insects." + proj_name
            ptrs = [
                {"fact_id": f.get("id"), "revision": f.get("revision"),
                 "field_id": proj_field}
                for f in by_field.get(proj_field, [])
                if f.get("answer_state") in _ANSWERED_STATES
            ]
            if ptrs:
                pointers[name] = ptrs[0] if len(ptrs) == 1 else ptrs
        if pointers:
            related[lid] = pointers

    next_id = _next_line_id(_reserved_line_ids(facts, questions))
    if next_id is None:
        line_inventory["flags"] = sorted(
            set(line_inventory["flags"]) | {"line_ids_exhausted"})
    return {
        "line_inventory": line_inventory,
        "line_retired": line_retired,
        "plan_years": plan_years_state,
        "instances": instances,
        "line_questions": line_questions,
        "related_project_facts": related,
        "next_line_id": next_id,
        "instance_issues": issues,
    }


def build_line_view(registry, project):
    """Line/year instance view for the common interview, no selection JSON.

    ``insect-plan`` without ``--input`` returns this: ``needs_selection``
    plus the declared lines, their per-field question states and the plan
    years.  Only validated structure identifiers are emitted — line ids,
    plan years, and ``source_line``/``input_line`` values when they exactly
    match a declared id; student answer values never leave the record.
    """
    binding = contract.binding_from_project(registry, project)
    if binding.major_id != MAJOR_ID:
        raise ValueError("insect plan requires industrial_insects binding")
    module = registry.resolve(binding.major_id)
    state = {
        "major_id": binding.major_id,
        "module_version": binding.module_version,
        "contract_version": binding.contract_version,
        "project_revision": project.get("revision"),
        "status": "needs_selection",
        "question_status_meaning": (
            "reuse = 재질문 불필요 판정일 뿐 검증·승인 아님"
        ),
        "answer_scope": "line_instances",
        "source_ref_check": "registered_id_only",
        "finance_status": "unsupported",
        "rendered_paper": False,
        "canonical_write": False,
    }
    state.update(_line_state(module, project))
    return state


def _valid_ref(ref):
    return (
        isinstance(ref, dict)
        and set(ref) == _REF_KEYS
        and isinstance(ref["source_id"], str)
        and type(ref["physical_page"]) is int
        and isinstance(ref["object_label"], str)
    )


def build_plan(registry, project, selections, *,
               precedent_path=DEFAULT_PRECEDENTS):
    """Select optional section roles and attach checked example locators.

    selections is a list of {section_id, action, reason, role_fit_reason,
    observation_refs, source_refs}; unknown keys are refused.  Action is
    use or adapt.  A transformation requires a reason; omission is simply
    absence from this list.  The result is a proposal for review, never a
    rendered paper or canonical mutation.
    """
    binding = contract.binding_from_project(registry, project)
    if binding.major_id != MAJOR_ID:
        raise ValueError("insect plan requires industrial_insects binding")
    if not isinstance(selections, list):
        raise ValueError("insect_selection_invalid:selections")
    module = registry.resolve(binding.major_id)
    try:
        proposal = contract.route(
            registry, binding.major_id, "document",
            output="document_plan", canonical=project,
        )
    except contract.UnsupportedOutputError:
        raise ValueError("insect_output_unsupported:document_plan") from None
    if proposal.status != "supported":
        raise ValueError("insect_capability_unsupported:document")
    available = {
        node["section_id"]: node
        for node in proposal.to_dict()["proposal"]["sections"]
    }
    precedent = load_precedents(precedent_path)
    anchors = {_anchor(o): o for o in precedent["observations"]}
    selected = []
    seen = set()
    for choice in selections:
        if not isinstance(choice, dict) or not (set(choice) <= _SELECTION_KEYS):
            raise ValueError("insect_selection_invalid:selection")
        sid = choice.get("section_id")
        if not isinstance(sid, str) or sid not in available or sid in seen:
            raise ValueError("insect_selection_invalid:section_id")
        seen.add(sid)
        action = choice.get("action")
        if action not in ("use", "adapt"):
            raise ValueError("insect_selection_invalid:action")
        reason = choice.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("insect_selection_invalid:reason")
        if action == "adapt" and (not isinstance(reason, str)
                                  or not reason.strip()):
            raise ValueError("insect_selection_invalid:reason")
        fit_reason = choice.get("role_fit_reason")
        if fit_reason is not None and not isinstance(fit_reason, str):
            raise ValueError("insect_selection_invalid:role_fit_reason")
        refs = choice.get("observation_refs", [])
        if not isinstance(refs, list):
            raise ValueError("insect_selection_invalid:observation_refs")
        checked = []
        fit_needs_review = False
        for ref in refs:
            if not _valid_ref(ref):
                raise ValueError("insect_selection_invalid:observation_ref")
            anchor = _anchor(ref)
            if anchor not in anchors:
                raise ValueError("insect_example_unregistered")
            observed = anchors[anchor]
            if (observed["role"] == "content_unresolved"
                    or not VERIFICATION_CODES[observed["verification"]]):
                raise ValueError("insect_example_unresolved")
            role = observed["role"]
            fit = "role_aligned"
            if role not in SECTION_ROLES.get(sid, set()):
                if (action != "adapt" or not isinstance(fit_reason, str)
                        or not fit_reason.strip()):
                    raise ValueError("insect_role_mismatch")
                fit = "fit_needs_review"
                fit_needs_review = True
            checked.append({
                "source_id": anchor[0],
                "physical_page": anchor[1],
                "object_label": anchor[2],
                "status": "example_observed",
                "observed_role": role,
                "role_fit": fit,
            })
        source_refs = choice.get("source_refs", [])
        if not isinstance(source_refs, list):
            raise ValueError("insect_selection_invalid:source_refs")
        verified_sources = []
        for ref in source_refs:
            if not isinstance(ref, dict) or set(ref) != _SOURCE_REF_KEYS:
                raise ValueError("insect_selection_invalid:source_ref")
            source_id, locator = ref.get("id"), ref.get("locator")
            if (not isinstance(source_id, str)
                    or source_id not in (project.get("sources") or {})
                    or not isinstance(locator, str) or not locator.strip()):
                raise ValueError("insect_source_unregistered")
            verified_sources.append({"id": source_id, "locator": locator})
        selected.append({
            "section_id": sid,
            "role": available[sid]["role"],
            "action": action,
            "reason": reason if isinstance(reason, str) else None,
            "observation_refs": checked,
            "role_fit_reason": fit_reason if fit_needs_review else None,
            "source_refs": verified_sources,
            "evidence_status": (
                "fit_needs_review" if fit_needs_review else
                "locator_linked" if checked or verified_sources else
                "locator_pending"
            ),
        })
    plan = {
        "major_id": binding.major_id,
        "module_version": binding.module_version,
        "contract_version": binding.contract_version,
        "project_revision": project.get("revision"),
        "status": "review_plan" if selected else "needs_selection",
        "selected_sections": selected,
        "available_sections": list(available),
        "question_states": question_states(module, project),
        "question_status_meaning": (
            "reuse = 재질문 불필요 판정일 뿐 검증·승인 아님"
        ),
        "answer_scope": "project_level_summary",
        "source_ref_check": "registered_id_only",
        "finance_status": "unsupported",
        "rendered_paper": False,
        "canonical_write": False,
    }
    plan.update(_line_state(module, project))
    return plan
