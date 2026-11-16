#!/usr/bin/env python3
"""On-request school-form what-if text; never returns an application payload.

Release remains gated by Excel-app comparison and application integration.
The caller owns the canonical lock while need_text reads the project.
"""
import importlib
import math
from decimal import Decimal
from pathlib import Path
import sys

import gg_core as core
import gg_workbook_audit as audit
from gg_school_excel import edition_marker

UNAVAILABLE = "지금은 계산해 드릴 수 없어요."
DIFFICULT = "계산으로 답을 내기 어려워요."
LARGE = "변화가 너무 커서 숫자는 보여 드리지 않아요."
AREA = ("재배면적을 넓히면 비용도 달라질 수 있는데, 지금 엑셀은 면적과 비용을 자동으로 이어 주지 않아요.\n"
        "그래서 면적만 바꾼 계산은 보여 드리지 않아요. 넓힐 계획이면 비용도 같이 정해서 다시 계산해 봐요.")
ENDING = "가정해서 계산해 본 값이에요. 계획에 넣으려면 왜 가능한지 알려 주세요."
LEVERS = ({"sheet": "6. 생산계획", "cell": "T7", "unit": "kg/10a",
           "positive": True, "year_column": "G", "total_cell": "T9",
           "total_formula": "T7*T8/1000"},)
PROFIT = ("12 손익계획", "G26")
SALES = ("12 손익계획", "G7")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("non-numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite")
    return result


def _solve(read, lever, goal):
    """Return an upward-rounded root only after four-point and ceil checks."""
    x0 = _number(lever)
    goal = _number(goal)
    if x0 < 1:
        return None
    p0 = _number(read(x0))
    if goal <= p0:
        return None
    d = abs(x0) * .01
    p1, p2 = _number(read(x0 + d)), _number(read(x0 + 2 * d))
    slope = (p1 - p0) / d
    if slope <= 0:
        return None
    if not audit.equal_value(slope, (p2 - p1) / d):
        raise ValueError("nonlinear")
    root = _number(x0 + (goal - p0) / slope)
    if root <= x0:
        return None
    if not audit.equal_value(_number(read(root)), goal):
        raise ValueError("root mismatch")
    shown = math.ceil(root)
    if _number(read(shown)) < goal:
        raise ValueError("rounded root undershoots")
    return shown


def _self_check(book, target):
    """Audit every upstream formula, not only the final cached result."""
    pending, seen, memo = [target], set(), {}
    if target not in book.formulas:
        raise ValueError("missing target formula")
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        if key not in book.formulas:
            continue
        cached = _number(book.formulas[key].cached)
        actual = _number(book.evaluate(*key, memo=memo))
        if not audit.equal_value(cached, actual):
            raise ValueError("cache mismatch")
        pending.extend(audit.ast_refs(book.parse(key), key[0]))
    return _number(book.evaluate(*target, memo=memo))


def _single_crop(p):
    # The existing claim-backed common.crop record is authoritative. Do not
    # infer a single crop from labels in a workbook or an unbound spec.
    fact = core._finance_gate_fact(p, "common.crop")
    if fact is None:
        return None
    value = fact.get("value")
    crops = value if isinstance(value, list) else [value]
    if len(crops) != 1 or not isinstance(crops[0], str) or not crops[0].strip():
        return None
    return crops[0].strip()


def _quantity(value):
    # Round-trip enough significant digits that displayed increases are not
    # silently lost for fractional areas/quantities; no area value is output.
    return format(_number(value), ",.15g")


def need_text(root):
    """Read the current B2 contract and evaluate only the reviewed lever."""
    try:
        root = Path(root).resolve()
        core.local(root, "project.json")
        p = core.load(root)
        major = core._finance_gate_fact(p, "common.major_id")
        if (major is None or major.get("value") != "specialty_crops"
                or not core._finance_plan_supported(p)):
            return UNAVAILABLE
        gap = importlib.import_module("gg_target_gap").read(root, p)
        if gap.get("status") != "ok":
            return UNAVAILABLE
        # Unit conversion lives here, once, and uses exact won rather than
        # the independently rounded amounts intended for student prose.
        goal = gap["goal_exact_won"] / Decimal(1000)
        plan = gap["plan_exact_won"] / Decimal(1000)
        _number(goal)
        _number(plan)
        candidates = core._finance_plan_candidates(root, p)
        if len(candidates) != 1 or _single_crop(p) is None:
            return UNAVAILABLE
        book = audit.Workbook.load(core.local(root, candidates[0]))
        if edition_marker(book.formulas) != "school_17_sheet_v2":
            return UNAVAILABLE
        if str(int(_number(book.evaluate(PROFIT[0], "G4")))) != str(gap["year"]).rstrip("년"):
            return UNAVAILABLE
        base = _self_check(book, PROFIT)
        if not audit.equal_value(base, _number(plan)):
            return UNAVAILABLE
        lever = LEVERS[0]
        key = (lever["sheet"], lever["cell"])
        total_key = (lever["sheet"], lever["total_cell"])
        formula = book.formulas.get(total_key)
        if formula is None or formula.text != lever["total_formula"]:
            return UNAVAILABLE
        # Structural reachability is required even when a coincidental
        # numeric match would pass the cache checks.
        pending, seen = [PROFIT], set()
        while pending:
            item = pending.pop()
            if item in seen:
                continue
            seen.add(item)
            if item in book.formulas:
                pending.extend(audit.ast_refs(book.parse(item), item[0]))
        if key not in seen:
            return UNAVAILABLE
        if _number(book.evaluate(*SALES, overrides={key: 0})) != 0:
            return UNAVAILABLE
        x0 = _number(book.evaluate(*key))
        try:
            value = _solve(lambda x: book.evaluate(*PROFIT, overrides={key: x}), x0, goal)
        except (ValueError, ArithmeticError, audit.Unsupported):
            return DIFFICULT
        if value is None:
            return UNAVAILABLE
        if value > 2 * x0:
            return LARGE
        before = _number(book.evaluate(*total_key))
        after = _number(book.evaluate(*total_key, overrides={key: value}))
        if after <= before:
            return UNAVAILABLE
        # No trustworthy physical/sales-cap registration contract exists in
        # the current project schema. Treat it as unregistered, never invent
        # a cap or interpret arbitrary source text as an approved threshold.
        return ("다른 건 모두 지금 계획 그대로라고 가정하고, 하나만 바꿔서 계산해 봤어요. "
                f"({gap['year']}년 목표 {_quantity(gap['shown']['goal_won'])}원 기준)\n\n"
                f"· 10a당 생산량만 바꾸면: 지금 {_quantity(x0)}kg → {_quantity(value)}kg으로 계산돼요.\n"
                f"  이러면 한 해 생산량이 {_quantity(before)}kg → {_quantity(after)}kg으로 늘어요.\n"
                f"  늘어난 {_quantity(after - before)}kg을 팔 수 있는지, 이 수량이 실제로 나오는지는 확인되지 않은 계산이에요.\n\n"
                f"· {AREA}\n\n{ENDING}")
    except Exception:
        # No internal paths, cell addresses or diagnostic amounts escape.
        return UNAVAILABLE


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(UNAVAILABLE)
        return 2
    try:
        with core.Lock(Path(args[0]).resolve()):
            text = need_text(args[0])
    except Exception:
        text = UNAVAILABLE
    print(text)
    return 0 if text not in (UNAVAILABLE, DIFFICULT) else 2


if __name__ == "__main__":
    raise SystemExit(main())
