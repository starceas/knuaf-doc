"""재배방식·등급·판로에 맞는 재무 통합문서인지 검사한다.

학과 17시트 통합문서(재계산된 사본)를 읽기만 한다. 규칙은
``references/plan-fit-rules.md``, 재배방식별 장비·작업 목록은
``references/cultivation-fit.json``이다.

검사:
- 미보유 행: 장비·자산 이름이 있는데 금액이 0이거나 비어 있는 행
  (``UNOWNED_ROW``, error). 0원 행으로 남기지 말고 지운다.
- 재배방식에 맞지 않는 장비·작업(``EQUIPMENT_METHOD_MISMATCH``·
  ``TASK_METHOD_MISMATCH``). 학생이 실제로 쓰는 예외는 ``allow``에
  이유와 함께 적으면 경고로 남는다.
- 판매하지 않는 등급·판로의 단가(``SALES_UNUSED_PRICE``, error).
- 판매하는 등급끼리 같은 단가·역전(``GRADE_PRICE_EQUAL``·
  ``GRADE_PRICE_ORDER``), 두 판로의 같은 등급 같은 단가
  (``CHANNEL_PRICE_EQUAL``), 모두 error.
- 판로 비용(택배·포장·수수료·운반) 반영처가 판매관리비에도 각주에도
  없음(``CHANNEL_COST_UNREFLECTED``, error).
- 등급 근거·판로별 거래단계 각주 없음(warning).

캐시 없는 수식은 값을 추정하지 않고 ``blocked``로 남긴다.
"""

import argparse
import json
import math
from pathlib import Path
import re
import sys

import openpyxl

CHECK_ID = "finance_plan_fit"
PROFILE = "school_17_sheet_v1"

_FIT_PATH = (Path(__file__).resolve().parent.parent
             / "references" / "cultivation-fit.json")

GRADES = ("상품", "중품", "하품")
CHANNELS = {"purchase": "수매", "direct": "직거래"}
PRICE_LABELS = {"purchase": "평균가격(수매)", "direct": "평균가격(직거래)"}
RATIO_LABELS = {"purchase": "수매시장", "direct": "직거래"}
PRICE_TOLERANCE = 0.05  # 원/kg

NOTE_PREFIXES = ("*", "※", "주", "출처")
NOTE_MIN_LENGTH = 20
GRADE_NOTE_WORDS = ("등급", "상품만", "단일", "상품화율", "품위")
SOURCE_NOTE_WORDS = {
    "purchase": ("수매", "도매", "경락", "공판", "농가수취", "수취가", "산지"),
    "direct": ("소매", "직거래", "직판", "온라인", "로컬", "택배"),
}
COST_NOTE_WORDS = {
    "purchase": ("수수료", "운반", "농가수취", "수취가", "상장"),
    "direct": ("택배", "포장", "배송", "운반"),
}

_SPACE = re.compile(r"\s+")
_SHEET_KEY = re.compile(r"[\s.]+")
_BLOCK_LABEL = re.compile(r"^\((\d)\)")
_YEAR = re.compile(r"^(19|20)\d{2}(년)?$")

# 1. 기초재무상태조사 자산 블록 → 금액 열(1-기준)
_ASSET_BLOCKS = {
    "1": ("land", 5), "2": ("building", 5), "3": ("plants", 5),
    "4": ("animals", 8), "5": ("machines", 5), "6": ("cash", 5),
    "7": ("materials", 6), "8": ("produce", 6),
}
_DEPRECIATION_GROUPS = {"계", "영농시설", "대농기구", "생물자산"}


def _norm(value):
    if value is None:
        return ""
    return _SPACE.sub("", str(value))


def _key(value):
    return _norm(value).replace("·", "").replace("-", "").lower()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _issue(code, *, severity="error", status=None, reason="", remedy="",
           evidence=None):
    if status is None:
        status = "fail" if severity == "error" else "pending"
    return {"code": code, "check_id": CHECK_ID, "status": status,
            "severity": severity, "reason": reason, "remedy": remedy,
            "evidence": evidence or {}}


def load_fit(path=None):
    doc = json.loads(Path(path or _FIT_PATH).read_text(encoding="utf-8"))
    if doc.get("schema") != "knuaf-cultivation-fit/v1":
        raise ValueError("cultivation-fit schema 불일치")
    return doc


def resolve_method(text, fit=None):
    """재배방식 id 또는 별칭 → id. 모르면 ValueError."""
    fit = fit or load_fit()
    want = _key(text)
    for method_id, meta in fit["methods"].items():
        names = [method_id, meta.get("label", "")] + list(meta.get("aliases", []))
        if want in {_key(n) for n in names if n}:
            return method_id
    raise ValueError("알 수 없는 재배방식: %r (가능: %s)"
                     % (text, ", ".join(sorted(fit["methods"]))))


class _Book:
    """같은 파일의 수식 보기·캐시 보기를 함께 연다."""

    def __init__(self, path):
        self.formula = openpyxl.load_workbook(path, data_only=False)
        self.data = openpyxl.load_workbook(path, data_only=True)

    def close(self):
        self.formula.close()
        self.data.close()

    def sheet(self, name):
        want = _SHEET_KEY.sub("", name)
        for title in self.data.sheetnames:
            if _SHEET_KEY.sub("", title) == want:
                return self.formula[title], self.data[title]
        return None

    @staticmethod
    def value(pair, row, col):
        """(값, 캐시 없음 여부). 수식인데 캐시가 없으면 (None, True)."""
        ws_f, ws_d = pair
        raw = ws_f.cell(row, col).value
        cached = ws_d.cell(row, col).value
        uncached = isinstance(raw, str) and raw.startswith("=") and (
            cached is None or (isinstance(cached, str) and cached.startswith("=")))
        return (None if uncached else cached), uncached


def _uncached(issues, sheet, cell):
    issues.append(_issue(
        "PLAN_FIT_UNCACHED", severity="error", status="blocked",
        reason="%s!%s 수식 캐시 없음 — 값을 읽지 못해 검사하지 않음" % (sheet, cell),
        remedy="통합문서를 Excel에서 재계산·저장한 사본으로 다시 검사한다",
        evidence={"sheet": sheet, "cell": cell}))


def _matches(name, keywords):
    key = _key(name)
    return [k for k in keywords if _key(k) and _key(k) in key]


# --- 장비·자산 행 ----------------------------------------------------------

def _investment_rows(book):
    """3.투자계획 연차별 세부투자계획의 (행, 항목명, 금액, 캐시없음)."""
    pair = book.sheet("3.투자계획")
    if pair is None:
        return None
    ws = pair[1]
    header = None
    for r in range(1, ws.max_row + 1):
        if _norm(ws.cell(r, 3).value) == "투자항목":
            header = r
            break
    if header is None:
        return None
    rows = []
    for r in range(header + 1, ws.max_row + 1):
        name = _norm(ws.cell(r, 3).value)
        if not name or name in ("계", "합계"):
            continue
        amount, uncached = book.value(pair, r, 5)
        rows.append((r, ws.cell(r, 3).value, _number(amount), uncached))
    return rows


def _asset_rows(book):
    """1. 기초재무상태조사 자산 블록의 (블록, 행, 항목명, 금액, 캐시없음)."""
    pair = book.sheet("1. 기초재무상태조사")
    if pair is None:
        return None
    ws = pair[1]
    blocks = []
    for r in range(1, ws.max_row + 1):
        match = _BLOCK_LABEL.match(_norm(ws.cell(r, 2).value))
        if match and match.group(1) in _ASSET_BLOCKS:
            blocks.append((r, _ASSET_BLOCKS[match.group(1)]))
    rows = []
    for index, (start, (role, col)) in enumerate(blocks):
        end = blocks[index + 1][0] if index + 1 < len(blocks) else ws.max_row + 1
        header = None
        for r in range(start + 1, min(end, start + 4)):
            if _norm(ws.cell(r, col).value):
                header = r
                break
        if header is None:
            continue
        for r in range(header + 1, end):
            label = _norm(ws.cell(r, 2).value)
            if label.startswith("나."):
                break
            if not label or "합계" in label or label == "계" or label.startswith("*"):
                continue
            amount, uncached = book.value(pair, r, col)
            note = _norm(ws.cell(r, 8).value)
            rows.append((role, r, ws.cell(r, 2).value, _number(amount), uncached, note))
    return rows


def _depreciation_items(book):
    """10. 감가상각비계획의 (행, 열, 항목명, 취득가격, 캐시없음)."""
    pair = book.sheet("10. 감가상각비계획")
    if pair is None:
        return None
    ws = pair[1]
    items = []
    for r in range(2, ws.max_row + 1):
        if _norm(ws.cell(r, 2).value) != "취득가격":
            continue
        for c in range(3, ws.max_column + 1):
            name = _norm(ws.cell(r - 1, c).value)
            if not name or name in _DEPRECIATION_GROUPS:
                continue
            cost, uncached = book.value(pair, r, c)
            items.append((r, c, ws.cell(r - 1, c).value, _number(cost), uncached))
    return items


def _labor_tasks(book):
    """8. 노무비계획 노동비 산출근거의 (행, 작업명, 작업일수, 캐시없음)."""
    pair = book.sheet("8. 노무비계획")
    if pair is None:
        return None
    ws = pair[1]
    tasks = []
    r = 1
    while r <= ws.max_row:
        if _norm(ws.cell(r, 2).value) != "항목":
            r += 1
            continue
        days_col = None
        for c in range(3, ws.max_column + 1):
            if _norm(ws.cell(r, c).value) == "작업일수":
                days_col = c
                break
        r += 1
        if days_col is None:
            continue
        while r <= ws.max_row and _norm(ws.cell(r, 2).value) not in ("합계", "계"):
            name = _norm(ws.cell(r, 2).value)
            if name:
                days, uncached = book.value(pair, r, days_col)
                tasks.append((r, ws.cell(r, 2).value, _number(days), uncached))
            r += 1
    return tasks


def _check_equipment(book, method, fit, allow, issues):
    unneeded = (fit["methods"][method]["unneeded_equipment"]
                if method else [])
    label = fit["methods"][method]["label"] if method else None
    seen_unowned, seen_method = set(), set()

    def mismatch(sheet, cell, name):
        hits = _matches(name, unneeded)
        if not hits or (sheet, _key(name)) in seen_method:
            return
        seen_method.add((sheet, _key(name)))
        reason_allowed = next((why for allowed, why in allow.items()
                               if _key(allowed) == _key(name)), None)
        evidence = {"sheet": sheet, "cell": cell, "name": str(name).strip(),
                    "method": method, "keywords": hits}
        if reason_allowed:
            evidence["allowed_reason"] = reason_allowed
            issues.append(_issue(
                "EQUIPMENT_METHOD_EXCEPTION", severity="warning",
                reason="%s 계획에 보통 쓰지 않는 장비 '%s' — 예외 사유: %s"
                       % (label, str(name).strip(), reason_allowed),
                remedy="본문·각주에 이 장비를 쓰는 작업을 밝힌다",
                evidence=evidence))
            return
        issues.append(_issue(
            "EQUIPMENT_METHOD_MISMATCH",
            reason="%s 계획에 맞지 않는 장비 '%s'(%s!%s)"
                   % (label, str(name).strip(), sheet, cell),
            remedy="행을 지운다. 실제로 쓰면 학생 확인 후 allow에 이유를 적는다",
            evidence=evidence))

    def unowned(sheet, cell, name, amount, kind, note=None):
        if amount not in (None, 0.0) or (kind, _key(name)) in seen_unowned:
            return
        seen_unowned.add((kind, _key(name)))
        evidence = {"sheet": sheet, "cell": cell, "name": str(name).strip(),
                    "kind": kind, "amount": amount}
        shown = "0" if amount == 0 else "비어 있음"
        if kind.startswith("opening_"):
            # 기초일 미보유 자산. 투자계획에 같은 자산이 있으면 기초 행은
            # 지우는 것이 원칙이지만 '매입 예정' 같은 비고가 있는 행은
            # 사람이 판단하도록 경고로 남긴다.
            evidence["note"] = note
            issues.append(_issue(
                "OPENING_ZERO_ROW", severity="warning",
                reason="%s!%s '%s' 기초 금액이 %s — 기초일에 없는 자산 행"
                       % (sheet, cell, str(name).strip(), shown),
                remedy="기초일에 보유하지 않으면 행을 지우고, 매입 계획은 3.투자계획에만 둔다",
                evidence=evidence))
            return
        issues.append(_issue(
            "UNOWNED_ROW",
            reason="%s!%s '%s' 금액이 %s — 미보유·미투자 행이 남아 있음"
                   % (sheet, cell, str(name).strip(), shown),
            remedy="0원·빈 행으로 두지 말고 행 내용을 지운다(수식 행은 남기고 항목명·규격·비고만 비운다)",
            evidence=evidence))

    invest = _investment_rows(book)
    if invest is not None:
        for r, name, amount, uncached in invest:
            if uncached:
                _uncached(issues, "3.투자계획", "E%d" % r)
                continue
            unowned("3.투자계획", "C%d" % r, name, amount, "investment")
            mismatch("3.투자계획", "C%d" % r, name)
    assets = _asset_rows(book)
    if assets is not None:
        for role, r, name, amount, uncached, note in assets:
            if uncached:
                _uncached(issues, "1. 기초재무상태조사", "row %d" % r)
                continue
            unowned("1. 기초재무상태조사", "B%d" % r, name, amount,
                    "opening_" + role, note)
            if role == "machines":
                mismatch("1. 기초재무상태조사", "B%d" % r, name)
    items = _depreciation_items(book)
    if items is not None:
        for r, c, name, cost, uncached in items:
            cell = "%s%d" % (openpyxl.utils.get_column_letter(c), r - 1)
            if uncached:
                _uncached(issues, "10. 감가상각비계획", cell)
                continue
            unowned("10. 감가상각비계획", cell, name, cost, "depreciation")
            mismatch("10. 감가상각비계획", cell, name)


def _check_tasks(book, method, fit, issues):
    if not method:
        return
    unneeded = fit["methods"][method]["unneeded_tasks"]
    label = fit["methods"][method]["label"]
    tasks = _labor_tasks(book)
    for r, name, days, uncached in tasks or ():
        hits = _matches(name, unneeded)
        if not hits:
            continue
        if uncached:
            _uncached(issues, "8. 노무비계획", "row %d" % r)
            continue
        evidence = {"sheet": "8. 노무비계획", "cell": "B%d" % r,
                    "name": str(name).strip(), "days": days,
                    "method": method, "keywords": hits}
        if days:
            issues.append(_issue(
                "TASK_METHOD_MISMATCH",
                reason="%s 계획에 없는 작업 '%s'에 %s일 배정" % (label, str(name).strip(), days),
                remedy="재배방식에 맞는 작업(예: 정식·베드관리)으로 이름과 일수를 바꾼다",
                evidence=evidence))
        else:
            issues.append(_issue(
                "TASK_METHOD_LABEL", severity="warning",
                reason="%s 계획에 없는 작업 이름 '%s'(0일)이 남아 있음" % (label, str(name).strip()),
                remedy="재배방식에 맞는 작업 이름으로 바꾸거나 행을 비운다",
                evidence=evidence))


# --- 판매계획: 등급·판로 단가 -------------------------------------------

def _find_row(ws, label, col=2):
    want = _norm(label)
    for r in range(1, ws.max_row + 1):
        if _norm(ws.cell(r, col).value) == want:
            return r
    return None


def _sales_notes(ws):
    notes = []
    for r in range(1, ws.max_row + 1):
        text = ws.cell(r, 2).value
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if stripped.startswith(NOTE_PREFIXES) or len(_norm(stripped)) >= NOTE_MIN_LENGTH:
            notes.append(stripped)
    return notes


def _sga_total(book, issues):
    """12 손익계획 판매비와 관리비(포장비·운반비·기타) 연도 합. 못 읽으면 None."""
    pair = book.sheet("12 손익계획")
    if pair is None:
        return None
    ws = pair[1]
    start = end = None
    for r in range(1, ws.max_row + 1):
        label = _norm(ws.cell(r, 2).value)
        if "판매비와관리비" in label and start is None:
            start = r
        elif start is not None and "영업이익" in label:
            end = r
            break
    if start is None or end is None:
        return None
    total = 0.0
    for r in range(start + 1, end):
        for c in range(3, 8):
            value, uncached = book.value(pair, r, c)
            if uncached:
                _uncached(issues, "12 손익계획", "%s%d" % (openpyxl.utils.get_column_letter(c), r))
                return None
            number = _number(value)
            if number:
                total += abs(number)
    return total


def _read_sales(book, issues):
    pair = book.sheet(" 5. 판매계획")
    if pair is None:
        return None
    ws = pair[1]
    sheet = ws.title.strip()
    out = {"sheet": sheet, "prices": {}, "history": {}, "shares": {},
           "notes": _sales_notes(ws)}
    for channel, label in PRICE_LABELS.items():
        row = _find_row(ws, label)
        if row is None:
            return None
        header = row - 1
        price_col = None
        hist_cols = []
        for c in range(3, ws.max_column + 1):
            head = _norm(ws.cell(header, c).value)
            if head == "평균가격":
                price_col = c
                break
            raw = pair[0].cell(header, c).value
            if _YEAR.match(head) or isinstance(_number(ws.cell(header, c).value), float) \
                    or (isinstance(raw, str) and raw.startswith("=")):
                hist_cols.append(c)
        if price_col is None:
            return None
        for offset, grade in enumerate(GRADES):
            r = row + offset
            value, uncached = book.value(pair, r, price_col)
            cell = "%s%d" % (openpyxl.utils.get_column_letter(price_col), r)
            if uncached:
                _uncached(issues, sheet, cell)
                return None
            number = _number(value)
            if value not in (None, "") and number is None and not (
                    isinstance(value, str) and "해당없음" in _norm(value)):
                issues.append(_issue(
                    "SALES_PRICE_UNREADABLE", status="blocked",
                    reason="%s!%s 단가가 숫자가 아님: %r" % (sheet, cell, value),
                    remedy="단가 칸에는 숫자 또는 빈 칸만 둔다",
                    evidence={"cell": cell}))
                return None
            out["prices"][(channel, grade)] = (number, cell)
            series = []
            for c in hist_cols:
                hv, huncached = book.value(pair, r, c)
                series.append(None if huncached else _number(hv))
            out["history"][(channel, grade)] = series
    ratio_rows = {}
    for channel, label in RATIO_LABELS.items():
        row = _find_row(ws, label)
        if row is None:
            return None
        ratio_rows[channel] = row
    first = min(ratio_rows.values())
    share_cols = []
    for header in range(first - 1, max(first - 5, 0), -1):
        share_cols = [c for c in range(3, ws.max_column + 1)
                      if _norm(ws.cell(header, c).value) == "판매비율"]
        if share_cols:
            break
    if not share_cols:
        return None
    for channel, row in ratio_rows.items():
        for offset, grade in enumerate(GRADES):
            r = row + offset
            values = []
            for c in share_cols:
                value, uncached = book.value(pair, r, c)
                if uncached:
                    _uncached(issues, sheet, "%s%d" % (openpyxl.utils.get_column_letter(c), r))
                    return None
                values.append(_number(value) or 0.0)
            out["shares"][(channel, grade)] = values
    return out


def _has_note(notes, words):
    return any(any(w in _norm(n) for w in words) for n in notes)


def _check_sales(book, issues):
    sales = _read_sales(book, issues)
    if sales is None:
        if not any(i["code"] == "PLAN_FIT_UNCACHED" for i in issues):
            issues.append(_issue(
                "SALES_LAYOUT_UNREADABLE", status="blocked",
                reason="5. 판매계획의 단가표·유통채널 표를 찾지 못함",
                remedy="학과 17시트 판매계획 양식(평균가격(수매)·평균가격(직거래)·수매시장·직거래 행)을 확인한다"))
        return
    sheet = sales["sheet"]
    sold = {key: any(v > 0 for v in values) for key, values in sales["shares"].items()}
    channel_sold = {ch: any(sold[(ch, g)] for g in GRADES) for ch in CHANNELS}

    for (channel, grade), (price, cell) in sales["prices"].items():
        if sold[(channel, grade)]:
            if not price:
                issues.append(_issue(
                    "SALES_PRICE_MISSING",
                    reason="%s %s을 판매하는데 단가(%s!%s)가 없음" % (CHANNELS[channel], grade, sheet, cell),
                    remedy="근거 있는 단가를 넣거나 판매 비율을 0으로 바꾼다",
                    evidence={"cell": cell, "channel": channel, "grade": grade}))
            continue
        history = [v for v in sales["history"][(channel, grade)] if v]
        if price or history:
            issues.append(_issue(
                "SALES_UNUSED_PRICE",
                reason="%s %s 판매 비율이 0인데 단가가 있음(%s!%s=%s)"
                       % (CHANNELS[channel], grade, sheet, cell, price),
                remedy="판매하지 않는 등급·판로의 단가와 가격 이력은 비우고(또는 0) '판매 없음'·'상품만 출하' 각주를 단다",
                evidence={"cell": cell, "channel": channel, "grade": grade,
                          "price": price, "history": history}))

    for channel in CHANNELS:
        sold_grades = [(g, sales["prices"][(channel, g)]) for g in GRADES
                       if sold[(channel, g)] and sales["prices"][(channel, g)][0]]
        for (g_hi, (p_hi, c_hi)), (g_lo, (p_lo, c_lo)) in zip(sold_grades, sold_grades[1:]):
            evidence = {"channel": channel, "higher": [g_hi, c_hi, p_hi],
                        "lower": [g_lo, c_lo, p_lo]}
            if abs(p_hi - p_lo) <= PRICE_TOLERANCE:
                issues.append(_issue(
                    "GRADE_PRICE_EQUAL",
                    reason="%s %s·%s 단가가 같음(%s원/kg)" % (CHANNELS[channel], g_hi, g_lo, p_hi),
                    remedy="등급별 근거 있는 단가(상>중>하)를 넣거나, 등급 구분 근거가 없으면 상품 단일 등급·중하품 비율 0으로 둔다",
                    evidence=evidence))
            elif p_lo > p_hi:
                issues.append(_issue(
                    "GRADE_PRICE_ORDER",
                    reason="%s %s 단가(%s)가 %s 단가(%s)보다 높음" % (CHANNELS[channel], g_lo, p_lo, g_hi, p_hi),
                    remedy="등급 단가 순서(상>중>하)와 근거를 다시 확인한다",
                    evidence=evidence))

    if all(channel_sold.values()):
        for grade in GRADES:
            if not (sold[("purchase", grade)] and sold[("direct", grade)]):
                continue
            (p_pur, c_pur), (p_dir, c_dir) = (sales["prices"][("purchase", grade)],
                                              sales["prices"][("direct", grade)])
            if p_pur and p_dir and abs(p_pur - p_dir) <= PRICE_TOLERANCE:
                issues.append(_issue(
                    "CHANNEL_PRICE_EQUAL",
                    reason="수매·직거래 %s 단가가 같음(%s원/kg, %s·%s)" % (grade, p_pur, c_pur, c_dir),
                    remedy="판로별 거래단계 출처(수매=농가수취가·경락가, 직거래=소매가)로 단가를 따로 잡는다",
                    evidence={"grade": grade, "cells": [c_pur, c_dir], "price": p_pur,
                              "history_purchase": sales["history"][("purchase", grade)],
                              "history_direct": sales["history"][("direct", grade)]}))

    notes = sales["notes"]
    if any(channel_sold.values()) and not _has_note(notes, GRADE_NOTE_WORDS):
        issues.append(_issue(
            "GRADE_BASIS_NOTE_MISSING", severity="warning",
            reason="등급 구성(상품만 출하 또는 등급 비율) 근거 각주가 없음",
            remedy="공식 자료에 등급 구분이 없으면 '상품 단일 등급, 중·하품 0은 계획 가정' 각주를 단다",
            evidence={"notes": notes}))
    sga = _sga_total(book, issues)
    for channel, is_sold in channel_sold.items():
        if not is_sold:
            continue
        if not _has_note(notes, SOURCE_NOTE_WORDS[channel]):
            issues.append(_issue(
                "CHANNEL_SOURCE_NOTE_MISSING", severity="warning",
                reason="%s 단가의 거래단계 출처 각주가 없음" % CHANNELS[channel],
                remedy="수매는 농가수취가·경락가, 직거래는 소매가 등 거래단계를 출처와 함께 밝힌다",
                evidence={"channel": channel}))
        if not sga and not _has_note(notes, COST_NOTE_WORDS[channel]):
            issues.append(_issue(
                "CHANNEL_COST_UNREFLECTED",
                reason="%s 판매 비용(%s) 반영처가 없음 — 판매관리비 0, 각주 없음"
                       % (CHANNELS[channel], "·".join(COST_NOTE_WORDS[channel][:2])),
                remedy="비용을 단가에서 뺐는지 12 손익계획 판매관리비(포장비·운반비)에 넣었는지 각주로 밝히고 금액을 반영한다",
                evidence={"channel": channel, "sga_total": sga}))


def review_fit(workbook_path, *, method=None, allow=None, fit_path=None):
    """재배방식·등급·판로 적합성 검사 결과 dict. 통합문서를 고치지 않는다."""
    fit = load_fit(fit_path)
    method_id = resolve_method(method, fit) if method else None
    allow = dict(allow or {})
    book = _Book(Path(workbook_path))
    try:
        issues = []
        if book.sheet(" 5. 판매계획") is None and book.sheet("3.투자계획") is None:
            issues.append(_issue(
                "PLAN_FIT_LAYOUT_UNSUPPORTED", status="blocked",
                reason="학과 17시트 판매계획·투자계획 시트가 없음",
                remedy="이 검사는 %s 통합문서만 지원한다. 다른 양식은 같은 규칙을 사람이 확인한다" % PROFILE))
            return {"status": "blocked", "method": method_id, "issues": issues}
        if method_id is None:
            issues.append(_issue(
                "CULTIVATION_METHOD_UNDECLARED", severity="warning",
                reason="재배방식을 선언하지 않아 장비·작업 대조를 하지 않음",
                remedy="--method로 재배방식(%s)을 지정한다" % ", ".join(sorted(fit["methods"]))))
        _check_equipment(book, method_id, fit, allow, issues)
        _check_tasks(book, method_id, fit, issues)
        _check_sales(book, issues)
    finally:
        book.close()
    if any(i["status"] == "blocked" for i in issues):
        status = "blocked"
    elif any(i["severity"] == "error" for i in issues):
        status = "fail"
    else:
        status = "pass"
    return {"status": status, "method": method_id, "issues": issues}


def _parse_allow(pairs):
    out = {}
    for pair in pairs or ():
        name, sep, reason = pair.partition("=")
        if not sep or not name.strip() or not reason.strip():
            raise ValueError("--allow는 장비명=이유 형식: %r" % pair)
        out[name.strip()] = reason.strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xlsx", help="재계산된 학과 17시트 통합문서 사본")
    ap.add_argument("--method", help="재배방식 id 또는 이름(예: protected_soilless, 고설수경)")
    ap.add_argument("--allow", action="append", metavar="장비명=이유",
                    help="재배방식 목록 밖이지만 실제로 쓰는 장비(학생 확인 이유 필수)")
    a = ap.parse_args(argv)
    try:
        result = review_fit(a.xlsx, method=a.method, allow=_parse_allow(a.allow))
    except (ValueError, OSError, KeyError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"pass": 0, "fail": 1}.get(result["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
