"""Read-only industrial-insects document plan built on the common major contract.

Precedents are examples, never mandatory headings or student defaults.  This
module returns a review plan; canonical writes and rendered-paper approval stay
with the common project transaction and output paths.
"""

import json
from pathlib import Path

import gg_core
import gg_major_contract as contract


DEFAULT_PRECEDENTS = (
    Path(__file__).resolve().parents[1]
    / "references" / "industrial-insects" / "precedents.json"
)

MAJOR_ID = "industrial_insects"
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
    return {
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
