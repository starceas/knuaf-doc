"""Verified paper goal minus canonical plan; read-only, stateless notices."""
from decimal import Decimal, ROUND_HALF_UP

import gg_core as core
import gg_publication as publication
from gg_school_paper import years
from gg_school_table_sources import load_spec

KIND = "finance_hole_target_gap"
# Design ceiling, not a measured student-file bound; synthetic fixtures only.
SPEC_LIMIT = 1 << 20


def _goal_won(spec):
    keys = core._NATIVE_FIELD_ALIASES[-1][0]
    values = []
    for key in keys:
        value = spec.get(key)
        if value is None or not str(value).strip():
            continue
        if not core._is_native_number(value):
            return None, "goal_missing"
        amount = Decimal(str(value).strip().replace(",", "")) * 1000
        if not amount.is_finite():
            return None, "goal_missing"
        values.append(amount)
    if not values:
        return None, "goal_missing"
    if any(value != values[0] for value in values[1:]):
        return None, "goal_ambiguous"
    if values[0] != values[0].to_integral_value():
        return None, "goal_not_integer_won"
    return values[0], None


def _text(year, shown):
    goal, plan, gap = (format(shown[key], ",.0f")
                       for key in ("goal_won", "plan_won", "gap_won"))
    title = "목표까지 %s원이 모자라요" % gap
    explanation = ("목표는 %s원인데, 지금 계획은 %s원이에요. 두 값을 빼면 %s원이 모자라요."
                   % (goal, plan, gap))
    current = "%s년 목표 %s원 · 지금 계획 %s원 · 차이 %s원" % (year, goal, plan, gap)
    return {
        "title": title,
        "choices": [{"value": "fix", "label": "고치기"},
                    {"value": "proceed", "label": "이대로 진행"},
                    {"value": "later", "label": "나중에"}],
        "talk_message": "목표 순이익과 계획의 차이를 조정하고 싶어요",
        "proceed_message": "지금 계획대로 목표 차이를 감수하고 진행할게요",
        "items": [{"what": explanation, "where": "재무 계획", "current": current,
                   "basis": "목표에서 지금 계획을 뺀 금액이에요.",
                   "how": "목표와 계획을 도우미와 함께 살펴봐요.",
                   "where_detail": "", "changed": ""}],
    }


def _read(root, p):
    none = lambda reason: {"status": "none", "reason": reason}
    if core._advice_major_id(p) != "specialty_crops":
        return none("plan_not_surplus")
    status, receipt, ref = publication.latest_paper_receipt(root, max_revision=p["revision"])
    if status == "absent":
        return {"status": "absent", "reason": "no_publication"}
    if status == "invalid":
        return {"status": "invalid", "reason": receipt}
    request = receipt["request"]
    if "body_sha256" in request:
        return none("body_replaced")
    try:
        raw = publication._read_file(root, request["spec_path"], limit=SPEC_LIMIT)
    except (OSError, ValueError, TypeError):
        return none("spec_unreadable")
    if core.digest(raw) != request["spec_sha256"]:
        return none("spec_changed")
    try:
        spec = load_spec(raw)
        if not isinstance(spec, dict):
            return none("spec_rejected")
        target_year = years(spec)[2]
    except (ValueError, TypeError, AttributeError):
        return none("spec_rejected")
    goal, reason = _goal_won(spec)
    if reason:
        return none(reason)
    if core.finance_gate(p)["state"] != "surplus":
        return none("plan_not_surplus")
    fact = core._finance_gate_fact(p, core.FINANCE_GATE_PROFIT)
    year = str(fact["period"]).rstrip("년")
    if year != str(target_year):
        return none("year_mismatch")
    plan = Decimal(fact["value"])
    shown = {"goal_won": goal.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
             "plan_won": plan.quantize(Decimal("1"), rounding=ROUND_HALF_UP)}
    shown["gap_won"] = shown["goal_won"] - shown["plan_won"]
    if shown["gap_won"] <= 0:
        return none("gap_not_positive")
    return {"status": "ok", "reason": "gap", "year": year,
            "goal_exact_won": goal, "plan_exact_won": plan,
            "gap_exact_won": goal - plan, "shown": shown,
            "card_key": core._card_key(_text(year, shown), KIND, "gap:" + year),
            "evidence": {"revision": p["revision"], "receipt_path": ref["path"],
                         "receipt_sha256": ref["sha256"],
                         "publication_id": ref["id"], "spec_path": request["spec_path"],
                         "spec_sha256": request["spec_sha256"],
                         "plan_fact": {"id": fact["id"], "revision": fact["revision"]}}}


def read(root, p):
    """Shared B2/B3 contract; caller owns the canonical lock. Never raises."""
    try:
        result = _read(root, p)
    except Exception:
        result = {"status": "invalid", "reason": "internal_error"}
    if result["status"] == "invalid":
        core._sidecar_report("notice target_gap skipped - " + result["reason"] + "\n")
    return result


def row(root, p):
    try:
        result = read(root, p)
        if result["status"] != "ok":
            return None
        return dict(_text(result["year"], result["shown"]), kind=KIND,
                    blocking=False, choice_policy="none", dismissible=False,
                    fix_mode="talk", record_refs=[],
                    project_id=core._advice_project_id(root),
                    notice_identity=result["card_key"], card_key=result["card_key"])
    except Exception:
        core._sidecar_report("notice target_gap skipped - internal_error\n")
        return None
