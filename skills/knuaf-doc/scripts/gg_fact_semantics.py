"""P3 S-lane: registry-driven fact semantics/classification (design-r2 §2-5).

Pure module: no file I/O, no writes, no model calls, no import of gg_core.
All inputs are read-only in-memory dicts/lists; core builds the Context
snapshot and passes it in. Every function returns JSON-compatible
dict/list; Decimal values are always serialized as strings and input
objects are never mutated in place.

Implements design-r2/DESIGN.md sections 2 (registry schema), 3
(classification/consumption/price-history), 4 (source/approval lookup),
5 (this module's exact API), 6.2 (validate_result_shape contract).
"""

from decimal import Decimal, InvalidOperation
import copy
import re

MEASURE_KINDS = {
    "monetary_total", "unit_price", "quantity", "area", "ratio",
    "duration", "text",
}
FINANCE_ROLES = {"plan", "context"}
ANSWER_STATES = {
    "not_provided", "explicit_none", "unknown", "provided",
    "not_applicable", "withheld",
}
FACT_KINDS = {"reported_fact", "observation", "assumption", "target", "derived"}


def _issue(code, *, status="fail", severity="error", fact_id=None,
           consumer_id=None, location=None, reason="", required_for=None,
           remedy=""):
    return {
        "code": code,
        "check_id": "fact_semantics",
        "status": status,
        "severity": severity,
        "fact_id": fact_id,
        "consumer_id": consumer_id,
        "location": location or {
            "path": None, "section_id": None, "line": None,
            "column": None, "table": None, "row": None, "cell": None,
        },
        "reason": reason,
        "required_for": required_for or ["submission_candidate"],
        "remedy": remedy,
    }


def _dec(value):
    """Decimal from a JSON-safe value; returns None on failure (never raises)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _decstr(d):
    return None if d is None else format(d, "f")


# ---------------------------------------------------------------------
# §2 registry / metadata validation
# ---------------------------------------------------------------------

def validate_metadata(fact, *, registry, mode="legacy"):
    """Validate a fact's registry-facing metadata (measure/finance_role/
    meaning_id) per design-r2 §2. mode='new' rejects unknown keys/enums
    strictly; mode='legacy' reads without inventing metadata and defers
    ambiguity to SEMANTIC_UNRESOLVED rather than a hard reject."""
    issues = []
    if not isinstance(fact, dict):
        return [_issue("METADATA_INVALID", reason="fact가 객체 아님")]
    fact_id = fact.get("id")
    meaning_id = fact.get("meaning_id")
    measure = fact.get("measure")
    finance_role = fact.get("finance_role")

    if meaning_id is None and measure is None and finance_role is None:
        # No explicit P3 metadata present at all — legacy fact, not an error.
        return issues

    meanings = (registry or {}).get("meanings", {}) if registry else {}

    if meaning_id is not None:
        if not isinstance(meaning_id, str) or not meaning_id.strip():
            issues.append(_issue(
                "METADATA_INVALID", fact_id=fact_id,
                reason="meaning_id는 비어있지 않은 문자열이어야 함"))
        elif mode == "new" and meanings and meaning_id not in meanings:
            issues.append(_issue(
                "METADATA_INVALID", fact_id=fact_id,
                reason="registry에 없는 meaning_id: " + meaning_id))

    if measure is not None:
        if not isinstance(measure, dict) or "kind" not in measure:
            issues.append(_issue(
                "METADATA_INVALID", fact_id=fact_id,
                reason="measure는 kind를 포함한 객체여야 함"))
        else:
            kind = measure.get("kind")
            if kind not in MEASURE_KINDS:
                issues.append(_issue(
                    "METADATA_INVALID", fact_id=fact_id,
                    reason="알 수 없는 measure.kind: " + str(kind)))
            else:
                if kind == "monetary_total" and measure.get("currency") not in (
                        None, "KRW"):
                    issues.append(_issue(
                        "METADATA_INVALID", fact_id=fact_id,
                        reason="monetary_total의 currency는 KRW만 허용"))
                if kind == "unit_price":
                    denom = measure.get("denominator")
                    if denom not in (None, "kg", "g", "a", "㎡"):
                        issues.append(_issue(
                            "METADATA_INVALID", fact_id=fact_id,
                            reason="unit_price denominator 미지원: "
                                   + str(denom)))
                    basis = measure.get("basis")
                    if basis is not None:
                        bd = _dec(basis)
                        if bd is None or bd <= 0:
                            issues.append(_issue(
                                "METADATA_INVALID", fact_id=fact_id,
                                reason="basis는 양수 유한 Decimal 문자열이어야 함"))
                unknown_keys = set(measure) - {
                    "kind", "unit", "currency", "denominator", "basis"}
                if mode == "new" and unknown_keys:
                    issues.append(_issue(
                        "METADATA_INVALID", fact_id=fact_id,
                        reason="measure의 알 수 없는 키: "
                               + ",".join(sorted(unknown_keys))))

    if finance_role is not None and finance_role not in FINANCE_ROLES:
        issues.append(_issue(
            "METADATA_INVALID", fact_id=fact_id,
            reason="finance_role은 plan|context만 허용"))

    return issues


# ---------------------------------------------------------------------
# §3 classification
# ---------------------------------------------------------------------

_NONFINANCIAL_UNIT_RE = re.compile(
    r"^(kg|g|㎡|m2|년|개월|%|ratio)$")


def classify_fact(fact, *, registry, bindings):
    """Classify one fact into a FactResult per design-r2 §3.

    ``bindings`` is the list of Binding dicts already resolved for this
    fact_id by resolve_consumers (may be empty)."""
    issues = list(validate_metadata(fact, registry=registry, mode="legacy"))
    fact_id = fact.get("id") if isinstance(fact, dict) else None
    meaning_id = fact.get("meaning_id") if isinstance(fact, dict) else None
    measure = fact.get("measure") if isinstance(fact, dict) else None
    finance_role = fact.get("finance_role") if isinstance(fact, dict) else None
    own_bindings = [b for b in (bindings or [])
                    if b.get("fact_ids") and fact_id in b["fact_ids"]]

    if any(i["status"] == "fail" for i in issues):
        return {
            "fact_id": fact_id, "status": "invalid", "meaning_id": meaning_id,
            "measure": measure, "source_kind": (fact or {}).get("kind"),
            "finance_role": finance_role, "normalized_value": None,
            "bindings": own_bindings, "dependencies": [], "issues": issues,
        }

    unit = (fact or {}).get("unit")
    value_type = (fact or {}).get("value_type")

    # legacy unmetadated fact — try a conservative non-financial read.
    if measure is None and meaning_id is None:
        if isinstance(unit, str) and (
                _NONFINANCIAL_UNIT_RE.match(unit)
                or value_type == "text"):
            return {
                "fact_id": fact_id, "status": "resolved",
                "meaning_id": None, "measure": None,
                "source_kind": (fact or {}).get("kind"),
                "finance_role": None, "normalized_value": None,
                "bindings": own_bindings, "dependencies": [], "issues": [],
            }
        issues.append(_issue(
            "SEMANTIC_UNRESOLVED", status="blocked", fact_id=fact_id,
            reason="비재무/재무 차원이 불명확한 legacy fact"))
        return {
            "fact_id": fact_id, "status": "unresolved", "meaning_id": None,
            "measure": None, "source_kind": (fact or {}).get("kind"),
            "finance_role": None, "normalized_value": None,
            "bindings": own_bindings, "dependencies": [], "issues": issues,
        }

    # metadata present: measure.kind vs declared value_type must agree —
    # caller cannot exempt a known money fact by writing value_type=text.
    if measure and measure.get("kind") in ("monetary_total", "unit_price"):
        if value_type == "text":
            issues.append(_issue(
                "ROLE_CONFLICT", fact_id=fact_id,
                reason="금액 fact가 value_type=text로 선언됨"))
        if isinstance(unit, str) and measure.get("unit") and unit != measure.get(
                "unit"):
            issues.append(_issue(
                "MEASURE_CONFLICT", fact_id=fact_id,
                reason="fact.unit과 measure.unit 불일치"))

    normalized = None
    if measure and measure.get("kind") in (
            "monetary_total", "unit_price", "quantity", "area"):
        d = _dec((fact or {}).get("value"))
        normalized = _decstr(d)

    if any(i["status"] == "fail" for i in issues):
        return {
            "fact_id": fact_id, "status": "invalid", "meaning_id": meaning_id,
            "measure": measure, "source_kind": (fact or {}).get("kind"),
            "finance_role": finance_role, "normalized_value": normalized,
            "bindings": own_bindings, "dependencies": [], "issues": issues,
        }

    return {
        "fact_id": fact_id, "status": "resolved", "meaning_id": meaning_id,
        "measure": measure, "source_kind": (fact or {}).get("kind"),
        "finance_role": finance_role, "normalized_value": normalized,
        "bindings": own_bindings, "dependencies": [], "issues": issues,
    }


# ---------------------------------------------------------------------
# §3 price-history channel state (3-row table)
# ---------------------------------------------------------------------

def _valid_history_years(years):
    if not isinstance(years, list) or len(years) != 5:
        return False
    ints = []
    for y in years:
        if isinstance(y, bool) or not isinstance(y, int):
            return False
        ints.append(y)
    return all(ints[i] < ints[i + 1] for i in range(4))


def _valid_history_prices(prices):
    if not isinstance(prices, list) or len(prices) != 5:
        return False
    for p in prices:
        if isinstance(p, bool):
            return False
        d = _dec(p)
        if d is None or d < 0 or not d.is_finite():
            return False
    return True


def price_history_channel_state(prices, years):
    """One channel's price-history state per design-r2 §3's 3-row table.

    Returns one of: 'absent' (no projection, declared price used),
    'invalid' (HISTORY_INVALID, no fallback to declared price), or
    'valid' (average usable as effective-price projection)."""
    if prices is None and years is None:
        return "absent"
    if not _valid_history_prices(prices) or not _valid_history_years(years):
        return "invalid"
    return "valid"


def price_history_projection(prices, years, *, source_id, source_revision,
                              channel, declared_scope, spec_source_id=None):
    """Build the projection dict + history_pairs for a valid channel, or
    None with an issue list for absent/invalid channels."""
    state = price_history_channel_state(prices, years)
    if state == "absent":
        return None, []
    if state == "invalid":
        return None, [_issue(
            "HISTORY_INVALID", consumer_id=channel,
            reason="가격 이력 길이/가격/연도가 무효함 — 선언가 fallback 금지")]
    decs = [_dec(p) for p in prices]
    avg = sum(decs) / Decimal(5)
    pairs = []
    for i in range(5):
        pairs.append({
            "index": i,
            "price_pointer": "/%s/%d" % (
                "purchase_price_history" if channel == "수매"
                else "direct_price_history", i),
            "price_fact_id": "%s:%s:price:%d" % (source_id, channel, i),
            "year_pointer": "/price_history_years/%d" % i,
            "year_fact_id": "%s:%s:year:%d" % (source_id, channel, i),
            "year": years[i],
            "price": _decstr(decs[i]),
        })
    projection_id = "projection:%s:%s:%s" % (
        spec_source_id or source_id, channel, "plan")
    return {
        "projection_id": projection_id,
        "channel": channel,
        "average_price": _decstr(avg),
        "history_pairs": pairs,
        "source_id": source_id,
        "source_revision": source_revision,
        "scope": declared_scope,
    }, []


# ---------------------------------------------------------------------
# §4 source/approval lookup
# ---------------------------------------------------------------------

def _review_covers(review, source_id, source_revision, claim_id, locator):
    if not isinstance(review, dict):
        return False
    if review.get("review_kind") != "content":
        return False
    if review.get("status") != "pass" or review.get("disposition") != "resolved":
        return False
    if review.get("findings"):
        # unresolved findings block reuse as an approval basis
        if any(f.get("status") not in ("resolved", "closed")
               for f in review["findings"] if isinstance(f, dict)):
            return False
    coverage = review.get("coverage") or []
    for c in coverage:
        if not isinstance(c, dict):
            continue
        if (c.get("source_id") == source_id
                and c.get("source_revision") == source_revision
                and c.get("claim_id") == claim_id
                and c.get("locator") == locator):
            return True
    return False


def lookup_input_state(fact, *, project, evidence, consumer_ids):
    """One InputState per design-r2 §4's outcome table."""
    fact = fact or {}
    answer_state = fact.get("answer_state")
    kind = fact.get("kind")
    source_refs = fact.get("source_refs") or []
    evidence = evidence or {}
    sources = evidence.get("sources", {})
    reviews = evidence.get("reviews", {})
    approvals = evidence.get("approvals", {})

    dependencies = []
    approval_refs = []
    issues = []

    def _source_approved():
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            sid = ref.get("id") or ref.get("source_id")
            rev = ref.get("revision") or ref.get("source_revision")
            claim_id = ref.get("claim_id")
            locator = ref.get("locator")
            src = sources.get(sid)
            if not src or not src.get("actual_sha256"):
                continue
            claim_review_id = src.get("claim_review")
            if not claim_review_id:
                continue
            review = reviews.get(claim_review_id)
            if _review_covers(review, sid, rev, claim_id, locator):
                dependencies.append({"collection": "sources", "id": sid})
                dependencies.append(
                    {"collection": "reviews", "id": claim_review_id})
                return True
        return False

    if kind == "assumption":
        approval_id = fact.get("assumption_use_approval")
        approval = approvals.get(approval_id) if approval_id else None
        if approval and _valid_assumption_use(
                approval, fact=fact, project=project):
            approval_refs.append(approval_id)
            dependencies.append({"collection": "approvals", "id": approval_id})
            return {
                "state": "explicit_assumption", "usable": True,
                "original_answer_state": answer_state,
                "value": fact.get("value"), "source_refs": source_refs,
                "approval_refs": approval_refs, "dependencies": dependencies,
                "issues": issues,
            }
        issues.append(_issue(
            "ASSUMPTION_APPROVAL_REQUIRED", status="blocked",
            fact_id=fact.get("id"),
            reason="assumption_use 승인이 없거나 유효하지 않음"))
        return {
            "state": "unresolved", "usable": False,
            "original_answer_state": answer_state, "value": None,
            "source_refs": source_refs, "approval_refs": [],
            "dependencies": dependencies, "issues": issues,
        }

    if answer_state == "provided":
        provenance_kind = fact.get("provenance_kind")
        if _source_approved():
            if provenance_kind == "user_answer":
                return {
                    "state": "user_answer", "usable": True,
                    "original_answer_state": answer_state,
                    "value": fact.get("value"), "source_refs": source_refs,
                    "approval_refs": approval_refs,
                    "dependencies": dependencies, "issues": issues,
                }
            if provenance_kind == "research":
                return {
                    "state": "research", "usable": True,
                    "original_answer_state": answer_state,
                    "value": fact.get("value"), "source_refs": source_refs,
                    "approval_refs": approval_refs,
                    "dependencies": dependencies, "issues": issues,
                }
        issues.append(_issue(
            "SOURCE_APPROVAL_UNRESOLVED", status="blocked",
            fact_id=fact.get("id"), reason="source 승인 근거 불충분"))
        return {
            "state": "unresolved", "usable": False,
            "original_answer_state": answer_state, "value": None,
            "source_refs": source_refs, "approval_refs": [],
            "dependencies": dependencies, "issues": issues,
        }

    if answer_state == "not_applicable":
        return {
            "state": "not_applicable", "usable": False,
            "original_answer_state": answer_state, "value": None,
            "source_refs": source_refs, "approval_refs": [],
            "dependencies": [], "issues": [],
        }

    if kind == "derived":
        return {
            "state": "derived", "usable": False,
            "original_answer_state": answer_state, "value": None,
            "source_refs": source_refs, "approval_refs": [],
            "dependencies": [], "issues": [],
        }

    return {
        "state": "unresolved", "usable": False,
        "original_answer_state": answer_state, "value": None,
        "source_refs": source_refs, "approval_refs": [],
        "dependencies": [], "issues": [],
    }


def _valid_assumption_use(approval, *, fact, project):
    if not isinstance(approval, dict):
        return False
    if approval.get("kind") != "assumption_use":
        return False
    if approval.get("status") != "confirmed":
        return False
    if approval.get("scope") != "fact_economic_use":
        return False
    hc = approval.get("human_confirmation")
    if not isinstance(hc, dict) or hc.get("explicit") is not True:
        return False
    if not isinstance(hc.get("confirmed_by"), str) or not hc["confirmed_by"].strip():
        return False
    targets = approval.get("target_refs") or []
    if not isinstance(targets, list) or not targets:
        return False
    fact_id = (fact or {}).get("id")
    # self-approval rejected: this fact's own approval cannot target itself
    # as if it were a different dependency (approval id != fact id is the
    # narrow, checkable form of "자기 approval을 target에 넣지 않는다").
    if approval.get("id") and approval["id"] == fact_id:
        return False
    if not any(
            isinstance(t, dict) and t.get("collection") == "facts"
            and t.get("id") == fact_id for t in targets):
        return False
    if not isinstance(approval.get("evidence_path"), str) or not approval[
            "evidence_path"].strip():
        return False
    if not isinstance(approval.get("evidence_hash"), str):
        return False
    if not isinstance(approval.get("input_fingerprint"), str):
        return False
    if not isinstance(approval.get("allowed_consumers"), list):
        return False
    return True


# ---------------------------------------------------------------------
# §5 remaining API surface
# ---------------------------------------------------------------------

def resolve_consumers(project, *, inputs, registry):
    """Resolve consumer_bindings against actual input snapshots.

    ``inputs`` is a list of InputSnapshot dicts
    ({source_id,source_revision,path,actual_sha256,profile,document,
    invocation}). Returns a ConsumerResult."""
    registry = registry or {}
    bindings_spec = registry.get("consumer_bindings", []) or []
    by_source = {i.get("source_id"): i for i in (inputs or [])}
    bindings = []
    issues = []
    seen_positions = {}
    for spec in bindings_spec:
        source_id = spec.get("source_id")
        pointer = spec.get("pointer")
        consumer_id = spec.get("consumer_id")
        snap = by_source.get(source_id)
        if not snap:
            issues.append(_issue(
                "PARAMETER_UNRESOLVED", consumer_id=consumer_id,
                reason="바인딩 소비 소스 없음: " + str(source_id)))
            continue
        pos_key = (source_id, snap.get("source_revision"), consumer_id, pointer)
        if pos_key in seen_positions:
            issues.append(_issue(
                "BINDING_CONFLICT", consumer_id=consumer_id,
                reason="같은 위치의 중복 바인딩: " + str(pos_key)))
            continue
        seen_positions[pos_key] = True
        binding = {
            "consumer_id": consumer_id, "source_id": source_id,
            "source_revision": snap.get("source_revision"),
            "pointer": pointer, "fact_ids": spec.get("fact_ids", []),
            "use": spec.get("use", "parameter"),
            "period": spec.get("period"), "scope": spec.get("scope"),
            "native_unit": spec.get("native_unit"),
            "native_value": spec.get("native_value"),
            "approval_refs": spec.get("approval_refs", []),
            "active": spec.get("active", True),
        }
        bindings.append(binding)
    dependencies = [
        {"collection": "sources", "id": sid} for sid in
        sorted({b["source_id"] for b in bindings if b.get("source_id")})
    ]
    return {
        "bindings": bindings, "projections": [], "required_inputs": [],
        "dependencies": dependencies, "issues": issues,
    }


def build_check_set(project, *, facts, consumers, registry):
    """Aggregate FactResults + ConsumerResult into a CheckSet."""
    facts = facts or []
    items = []
    required_ids = []
    claimed_ids = []
    issues = []
    dependencies = []
    for fr in facts:
        items.append(fr)
        if fr.get("status") == "resolved" and fr.get("finance_role") == "plan":
            required_ids.append(fr.get("fact_id"))
        issues.extend(fr.get("issues", []))
        if fr.get("fact_id"):
            dependencies.append({"collection": "facts", "id": fr["fact_id"]})
    mode = "complete" if not issues else "fallback"
    return {
        "items": items, "required_ids": required_ids,
        "mode": mode, "claimed_ids": claimed_ids,
        "dependencies": sorted(
            dependencies, key=lambda r: (r["collection"], r["id"])),
        "issues": issues,
    }


def make_semantics_report(*, registry, target_refs, input_revision,
                           input_fingerprint, facts, consumers, body_result):
    registry = registry or {}
    all_issues = []
    for f in (facts or []):
        all_issues.extend(f.get("issues", []))
    for c in (consumers or {}).get("issues", []) if consumers else []:
        all_issues.append(c)
    if body_result:
        all_issues.extend(body_result.get("issues", []))
    result = "blocked" if any(
        i.get("status") in ("fail", "blocked") for i in all_issues) else "pass"
    return {
        "schema": "gg-finance-semantics-report/1",
        "human_notes": "",
        "semantics": {
            "registry": {
                "version": registry.get("version"),
                "sha256": registry.get("sha256"),
            },
            "input_revision": input_revision,
            "target_refs": target_refs or [],
            "input_fingerprint": input_fingerprint,
            "bindings": (consumers or {}).get("bindings", []),
            "projections": (consumers or {}).get("projections", []),
            "checked_item_ids": [
                f.get("fact_id") for f in (facts or []) if f.get("fact_id")],
            "source_decisions": [],
            "assumption_decisions": [],
            "issues": all_issues,
            "result": result,
            "output_file_hash": None,
        },
        "review": {
            "author_id": None, "reviewer_id": None, "review_kinds": [],
            "findings": [], "disposition": None,
        },
    }


def validate_report_binding(report, *, registry, current):
    """Freshness check per design-r2 §7's AND-list. Returns issues (empty
    == fresh). ``current`` carries the currently-recomputed equivalents:
    {registry, target_refs, input_fingerprint, report_hash,
    observation_report_path, observation_input_revision, review_path,
    review_input_revision, review_status, review_author_id,
    review_reviewer_id, review_kinds}.

    Design ambiguity resolved (was flagged in S-LANE-RECEIPT.json for
    reconciliation before C-lane wiring): §7's AND-list literally lists
    'author/reviewer/종류/정렬 target_refs/input_fingerprint/report_hash
    exact equality' as one of the freshness conjuncts, distinct from the
    top-level registry/target_refs/input_fingerprint check against the
    semantics block. This is the *review* record's author_id/reviewer_id/
    review_kinds compared against the currently-recomputed review identity
    — report.review's own author_id/reviewer_id/review_kinds must match
    ``current``'s freshly-looked-up equivalents, mirroring the same
    tamper-evident pattern as the input_revision triple check. A stale
    cached report whose embedded review identity no longer matches the
    live review record (author reassigned, reviewer changed, review kind
    changed) is REGISTRY_STALE, not silently accepted."""
    issues = []
    if not isinstance(report, dict):
        return [_issue("REGISTRY_STALE", reason="report가 객체 아님")]
    sem = report.get("semantics") or {}
    review_block = report.get("review") or {}
    current = current or {}

    reg = sem.get("registry") or {}
    cur_reg = current.get("registry") or {}
    if reg.get("version") != cur_reg.get("version") or reg.get(
            "sha256") != cur_reg.get("sha256"):
        issues.append(_issue("REGISTRY_STALE", reason="registry bytes/version 변경"))

    if sem.get("target_refs") != current.get("target_refs"):
        issues.append(_issue("REGISTRY_STALE", reason="target_refs 변경"))

    if sem.get("input_fingerprint") != current.get("input_fingerprint"):
        issues.append(_issue("REGISTRY_STALE", reason="input_fingerprint 변경"))

    if current.get("report_hash") is not None and current.get(
            "report_hash") != current.get("actual_report_hash"):
        issues.append(_issue("REGISTRY_STALE", reason="report hash 변경"))

    if "review_author_id" in current and review_block.get(
            "author_id") != current.get("review_author_id"):
        issues.append(_issue("REGISTRY_STALE", reason="review author_id 불일치"))

    if "review_reviewer_id" in current and review_block.get(
            "reviewer_id") != current.get("review_reviewer_id"):
        issues.append(_issue("REGISTRY_STALE", reason="review reviewer_id 불일치"))

    if "review_kinds" in current and sorted(
            review_block.get("review_kinds") or []) != sorted(
            current.get("review_kinds") or []):
        issues.append(_issue("REGISTRY_STALE", reason="review 종류(review_kinds) 불일치"))

    obs_path = current.get("observation_report_path")
    review_path = current.get("review_path")
    if obs_path is not None and review_path is not None and obs_path != review_path:
        issues.append(_issue(
            "OBSERVATION_BINDING_INVALID",
            reason="observation.report_path != review.path"))

    obs_rev = current.get("observation_input_revision")
    review_rev = current.get("review_input_revision")
    report_rev = sem.get("input_revision")
    if not (obs_rev == review_rev == report_rev):
        issues.append(_issue(
            "OBSERVATION_BINDING_INVALID",
            reason="observation/report/review input_revision 불일치"))

    if current.get("review_status") not in (None, "pass"):
        issues.append(_issue("REGISTRY_STALE", reason="review status가 pass 아님"))

    return issues


_RESULT_SHAPE_KEYS = ("npv", "bc", "bc_status", "irr", "irr_status", "payback")


def validate_result_shape(result, *, profile, registry):
    """§6.2 result-shape contract for /investment/{npv,bc,irr,payback}."""
    issues = []
    inv = (result or {}).get("investment")
    if not isinstance(inv, dict):
        return [_issue("OUTPUT_CONTRACT_INVALID", reason="investment 블록 없음")]

    if "npv" not in inv:
        issues.append(_issue("OUTPUT_CONTRACT_INVALID", reason="npv 키 누락"))
    else:
        npv = inv["npv"]
        d = _dec(npv) if not isinstance(npv, bool) else None
        if npv is None or isinstance(npv, bool) or d is None or not d.is_finite():
            issues.append(_issue(
                "OUTPUT_CONTRACT_INVALID",
                reason="npv는 항상 존재하는 non-null 유한 Decimal 문자열이어야 함"))

    if "bc" not in inv or "bc_status" not in inv:
        issues.append(_issue("OUTPUT_CONTRACT_INVALID", reason="bc/bc_status 키 누락"))
    else:
        bc, bc_status = inv["bc"], inv["bc_status"]
        if bc is None:
            if bc_status != "undefined_zero_cost":
                issues.append(_issue(
                    "OUTPUT_CONTRACT_INVALID",
                    reason="bc=null은 undefined_zero_cost일 때만 허용"))
        else:
            d = _dec(bc) if not isinstance(bc, bool) else None
            if isinstance(bc, bool) or d is None or not d.is_finite() or bc_status != "calculated":
                issues.append(_issue(
                    "OUTPUT_CONTRACT_INVALID",
                    reason="bc non-null은 유한 Decimal+status=calculated 필요"))

    if "irr" not in inv or "irr_status" not in inv:
        issues.append(_issue("OUTPUT_CONTRACT_INVALID", reason="irr/irr_status 키 누락"))
    else:
        irr, irr_status = inv["irr"], inv["irr_status"]
        if irr is None:
            if irr_status not in ("undefined", "nonconventional_multiple_or_undefined"):
                issues.append(_issue(
                    "OUTPUT_CONTRACT_INVALID",
                    reason="irr=null status는 undefined|nonconventional_multiple_or_undefined만"))
        else:
            d = _dec(irr) if not isinstance(irr, bool) else None
            if isinstance(irr, bool) or d is None or not d.is_finite() or irr_status != "calculated":
                issues.append(_issue(
                    "OUTPUT_CONTRACT_INVALID",
                    reason="irr non-null은 유한 Decimal+status=calculated 필요"))

    if "payback" not in inv:
        issues.append(_issue("OUTPUT_CONTRACT_INVALID", reason="payback 키 누락"))
    else:
        pb = inv["payback"]
        if pb is not None:
            d = _dec(pb) if not isinstance(pb, bool) else None
            if isinstance(pb, bool) or d is None or not d.is_finite():
                issues.append(_issue(
                    "OUTPUT_CONTRACT_INVALID",
                    reason="payback은 null 또는 유한 Decimal 문자열이어야 함"))

    rc = (result or {}).get("repayment_comparison")
    if rc is not None and rc not in (
            "not_recovered", "longer_than_repayment", "within_repayment"):
        issues.append(_issue(
            "OUTPUT_CONTRACT_INVALID", reason="repayment_comparison 값 무효"))

    return issues
