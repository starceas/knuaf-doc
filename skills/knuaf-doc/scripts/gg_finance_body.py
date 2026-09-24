"""P3 B-lane: claims/parser contract for financial body crosschecking
(design-r2 DESIGN.md section 6, 6.1, 6.2).

Pure module: no file I/O, no writes, no model calls, no import of gg_core.
Reuses gg_school_excel's existing table-context/period/unit primitives
(``_body_tables``, ``_period_keys``, ``_has_scope``, ``_has_unit``,
``_label_cell``, ``_display_places``, ``_round_half_up``) rather than
duplicating that boundary logic, per DESIGN.md's requirement to preserve
existing table-context ownership.
"""

from decimal import Decimal, InvalidOperation
import re


def _gg_school_excel():
    import gg_school_excel
    return gg_school_excel


def _issue(code, *, status="fail", severity="error", fact_id=None,
           consumer_id=None, location=None, reason="", required_for=None,
           remedy=""):
    return {
        "code": code,
        "check_id": "body_finance_crosscheck",
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
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


# ---------------------------------------------------------------------
# number/unit syntax primitives (§6 supported-syntax table)
# ---------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"(?<![\d,.\-+eE])"
    r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?![\d.,eE%])"
)
_BAD_NUMBER_MARKERS = re.compile(
    r"NaN|Infinity|[eE][+-]?\d|억원?|만원?|약\s*\d|추정\s*\d|\d+\s*[~\-]\s*\d+"
)

_UNIT_SCALES = {"원": Decimal(1), "천원": Decimal(1000)}

# The full monetary-unit domain normalize_monetary supports. C-lane's
# gg_core._finance_check_items reads this directly so both currency units
# stay eligible for the body crosscheck in one place — fixes H9/R6-undercheck
# (main previously matched only '천원' and silently dropped '원' facts).
_MONETARY_UNITS = frozenset(_UNIT_SCALES)


def parse_number(token):
    """Parse a body-syntax number token, or None if unsupported/invalid."""
    if not isinstance(token, str):
        return None
    token = token.strip()
    if not token:
        return None
    if _BAD_NUMBER_MARKERS.search(token):
        return None
    # single lone sign / no digits
    if re.fullmatch(r"[+-]", token):
        return None
    m = re.fullmatch(
        r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", token)
    if not m:
        # table parenthesized negative form (1,234.5)
        m2 = re.fullmatch(
            r"\(\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*\)", token)
        if m2:
            try:
                return -Decimal(token.strip("()").replace(",", ""))
            except InvalidOperation:
                return None
        return None
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def normalize_monetary(value, unit):
    """Decimal * scale for 원/천원; None for unsupported units."""
    d = _dec(value)
    if d is None:
        return None
    scale = _UNIT_SCALES.get(unit)
    if scale is None:
        return None
    return d * scale


def unit_price_matches(measure_a, measure_b):
    """True only if both unit_price measures share KRW + identical
    denominator + basis — 원/600g and 원/kg are never equal."""
    if not isinstance(measure_a, dict) or not isinstance(measure_b, dict):
        return False
    if measure_a.get("kind") != "unit_price" or measure_b.get("kind") != "unit_price":
        return False
    return (
        measure_a.get("denominator") == measure_b.get("denominator")
        and measure_a.get("basis") == measure_b.get("basis")
    )


# ---------------------------------------------------------------------
# claim shape validation (§6)
# ---------------------------------------------------------------------

_FORBIDDEN_CLAIM_KEYS = {"output_id", "output_pointer"}


def validate_claim_shape(claim, *, location=None):
    """Issues for one claim dict per §6's exact shape rules. Empty list
    means the claim shape itself is acceptable (value/location correctness
    is checked separately by crosscheck_sections)."""
    issues = []
    if not isinstance(claim, dict):
        return [_issue("CLAIM_INVALID", location=location,
                       reason="claim이 객체가 아님")]

    present_forbidden = _FORBIDDEN_CLAIM_KEYS & set(claim)
    if present_forbidden:
        issues.append(_issue(
            "CLAIM_INVALID", location=location,
            reason="금지된 claim key: " + ",".join(sorted(present_forbidden))))

    has_fact = "fact_id" in claim and claim.get("fact_id") is not None
    has_proj = "projection_id" in claim and claim.get("projection_id") is not None
    if has_fact == has_proj:
        # both or neither
        issues.append(_issue(
            "CLAIM_INVALID", location=location,
            reason="claim은 fact_id 또는 projection_id 정확히 하나만 가져야 함"))

    if has_fact:
        allowed = {"fact_id", "quote", "value", "unit", "period", "scope",
                   "kind", "field_id"}
    else:
        allowed = {"projection_id", "quote", "value", "unit", "period",
                   "scope", "field_id"}
    unknown = set(claim) - allowed - _FORBIDDEN_CLAIM_KEYS
    if unknown:
        issues.append(_issue(
            "CLAIM_INVALID", location=location,
            reason="지원하지 않는 claim key: " + ",".join(sorted(unknown))))

    for req in ("quote", "value", "unit", "period", "scope"):
        if not claim.get(req):
            issues.append(_issue(
                "CLAIM_INVALID", location=location,
                reason="claim 필수 필드 누락: " + req))

    return issues


def _quote_is_substring(section_text, quote):
    return isinstance(section_text, str) and isinstance(quote, str) and (
        quote in section_text)


# ---------------------------------------------------------------------
# crosscheck_sections (§5/§6 main entry point)
# ---------------------------------------------------------------------

def crosscheck_sections(sections, items, *, registry=None):
    """BodyResult for the given sections against the required item set.

    sections=[{section_id,path,text}] (text already through core.draft()).
    items = list of BodyItem-shaped dicts (item_id, fact_id/projection
    identity via meaning_id/field_id, measure, value, period, scope,
    required, labels).
    """
    sections = sections or []
    items = items or []
    matches = []
    issues = []
    checked_item_ids = []

    complete_mode = bool(sections) and all(
        isinstance(s.get("claims"), list) for s in sections)

    if complete_mode:
        result = _crosscheck_complete(sections, items, registry=registry)
    else:
        result = _crosscheck_fallback(sections, items, registry=registry)

    return result


def _claim_field_value(claim, field_id):
    return claim.get("field_id") == field_id


def _iter_claims(sections):
    for s in sections:
        for claim in (s.get("claims") or []):
            yield s, claim


def _crosscheck_complete(sections, items, *, registry):
    matches, issues, checked = [], [], []
    if not sections:
        issues.append(_issue("BODY_MISSING", reason="sections가 비어 있음"))

    for s, claim in _iter_claims(sections):
        loc = {"path": s.get("path"), "section_id": s.get("section_id"),
               "line": None, "column": None, "table": None, "row": None,
               "cell": None}
        shape_issues = validate_claim_shape(claim, location=loc)
        issues.extend(shape_issues)

    required = [it for it in items if it.get("required")]
    for item in required:
        item_id = item.get("item_id")
        checked.append(item_id)
        field_id = item.get("field_id")
        found = False
        for s, claim in _iter_claims(sections):
            if claim.get("field_id") != field_id:
                continue
            if item.get("fact_id") is not None:
                if claim.get("fact_id") != item.get("fact_id"):
                    continue
            elif item.get("projection_id") is not None:
                if claim.get("projection_id") != item.get("projection_id"):
                    continue
            section_text = s.get("text", "")
            if not _quote_is_substring(section_text, claim.get("quote")):
                issues.append(_issue(
                    "CLAIM_INVALID",
                    location={"path": s.get("path"),
                             "section_id": s.get("section_id")},
                    fact_id=item.get("fact_id"), reason="claim quote가 본문 부분문자열 아님"))
                continue
            expected = _dec(item.get("value"))
            actual = _dec(claim.get("value"))
            if expected is None or actual is None or expected != actual:
                issues.append(_issue(
                    "BODY_MISMATCH", fact_id=item.get("fact_id"),
                    location={"path": s.get("path"),
                             "section_id": s.get("section_id")},
                    reason="claim 값이 필수항목 값과 불일치"))
                continue
            if claim.get("unit") != item.get("measure", {}).get("unit"):
                issues.append(_issue(
                    "BODY_MISMATCH", fact_id=item.get("fact_id"),
                    location={"path": s.get("path"),
                             "section_id": s.get("section_id")},
                    reason="claim 단위가 필수항목과 불일치"))
                continue
            found = True
            matches.append({
                "item_id": item_id, "section_id": s.get("section_id"),
                "value": claim.get("value"), "quote": claim.get("quote"),
            })
        if not found:
            issues.append(_issue(
                "CLAIM_PLAN_MISSING", fact_id=item.get("fact_id"),
                consumer_id=item.get("item_id"),
                reason="필수 fact/projection의 유효 claim 없음"))

    return {"matches": matches, "issues": issues, "checked_item_ids": checked}


def _crosscheck_fallback(sections, items, *, registry):
    matches, issues, checked = [], [], []
    gse = _gg_school_excel()

    # still validate + check location of any well-formed explicit claims
    for s, claim in _iter_claims(sections):
        if not isinstance(claim, dict):
            continue
        loc = {"path": s.get("path"), "section_id": s.get("section_id")}
        shape_issues = validate_claim_shape(claim, location=loc)
        if shape_issues:
            issues.extend(shape_issues)
            continue
        section_text = s.get("text", "")
        if not _quote_is_substring(section_text, claim.get("quote")):
            issues.append(_issue(
                "CLAIM_INVALID", location=loc,
                reason="claim quote가 본문 부분문자열 아님"))

    required = [it for it in items if it.get("required")]
    full_text = "\n".join(s.get("text", "") for s in sections)
    for item in required:
        item_id = item.get("item_id")
        checked.append(item_id)
        label = item.get("field_id") or item_id
        expected_val = item.get("value")
        measure = item.get("measure") or {}
        unit = measure.get("unit")
        period = item.get("period")
        scope = item.get("scope")
        if not (label and expected_val is not None and unit and period and scope):
            issues.append(_issue(
                "BODY_MISSING", fact_id=item.get("fact_id"),
                consumer_id=item_id, reason="필수 항목 메타데이터 불완전"))
            continue
        legacy_values = {
            label: {"value": expected_val, "unit": unit,
                    "period": period, "scope": scope},
        }
        body_issues = gse.crosscheck_body(full_text, legacy_values)
        if body_issues:
            issues.append(_issue(
                "BODY_MISSING", fact_id=item.get("fact_id"),
                consumer_id=item_id,
                reason="전체 본문에서 필수항목 대조 실패: "
                       + str(body_issues[0].get("reason"))))
        else:
            matches.append({"item_id": item_id, "section_id": None,
                            "value": expected_val, "quote": None})

    return {"matches": matches, "issues": issues, "checked_item_ids": checked}


# ---------------------------------------------------------------------
# BODY_CONTRADICTION across all occurrences of the same metric/period/scope
# ---------------------------------------------------------------------

def detect_contradictions(occurrences):
    """occurrences = [{metric,period,scope,value,unit}]. Returns issues for
    any (metric,period,scope) group whose members disagree on
    value/unit/denominator — even when one occurrence is correct."""
    issues = []
    groups = {}
    for occ in occurrences or []:
        key = (occ.get("metric"), occ.get("period"), occ.get("scope"))
        groups.setdefault(key, []).append(occ)
    for key, members in groups.items():
        values = {(_dec(m.get("value")), m.get("unit")) for m in members}
        if len(values) > 1:
            metric, period, scope = key
            issues.append(_issue(
                "BODY_CONTRADICTION",
                reason="같은 metric/period/scope에 서로 다른 값/단위 주장: "
                       f"{metric}/{period}/{scope}"))
    return issues


# ---------------------------------------------------------------------
# crosscheck_body compat adapter (thin wrapper; the real legacy
# implementation stays owned by gg_school_excel.py per DESIGN.md section 5)
# ---------------------------------------------------------------------

def crosscheck_body(text, values):
    """Compat re-export of gg_school_excel.crosscheck_body (unchanged
    legacy behavior — this module does not modify that implementation,
    it only exposes it under the shared gg_finance_body import surface
    for C-lane convenience)."""
    return _gg_school_excel().crosscheck_body(text, values)
