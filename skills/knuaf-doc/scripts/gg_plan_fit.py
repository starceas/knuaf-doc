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

X01(학과 17시트 양식 정본)이 아닌 통합문서는 ``not_applicable``(검사 안 함, 종료 0)이다.
X01이면 표마다 "인식한 영역" 밖, 시트 사용 범위 안에 값이 하나라도 남거나 캐시 없는
수식·찾지 못한 표가 있으면 그 표는 ``blocked``(확인 불가, 종료 2)다. 확인 불가는 계획
오류가 아니라 검사하지 못했다는 뜻이고, 실제로 읽은 값의 모순만 ``fail``(종료 1)이다.
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

# 학과 17시트 양식(X01) 배치 기준 기대 개수와 표 열 한도(1-기준 열 번호). 공통 표 확인(_TableCheck)이 쓴다.
LAYOUT = {
    "investment": {"year_rows": 5, "cols": (2, 9)},       # 3.투자계획 연차별 세부투자계획 B~I(B17:I18)
    "assets": {"blocks": 8, "cols": (2, 8)},              # 1. 기초재무상태조사 (1)~(8), B~H(B8:H8)
    "depreciation": {"blocks": 5, "cols": (2, 11)},       # 10. 감가상각비계획 가.~마., B~K(J6:K6 생물 자산)
    "labor": {"tables": 2, "cols": (2, 21)},              # 8. 노무비계획 산출근거 2표, B~U(R24:U25)
    "sales_history": {"years": 5, "cols": (2, 15)},       # 5. 판매계획 가격 이력, B~O(O6 평균가격)
    "sales_shares": {"years": 5, "cols": (2, 14)},        # 5. 판매계획 판매비율, B~N(N19 비고)
    "sga": {"years": 5, "cols": (2, 7)},                  # 12 손익계획 연도 열, B~G(C4:G4)
}

# X01 판별: 검사가 읽지 않는 10개 시트의 이름·B2 제목·핵심 머리글 한 칸(공백·점 무시).
# 강동현 정본(X01)과 같은 양식 파일에서 공통인 칸만 골랐다. 검사가 읽는 6개 시트는
# 판별에 쓰지 않는다 — X01 안에서 그 시트·머리글이 빠지거나 바뀌면 확인 불가다.
X01_SHEETS = (
    ("목록", "작성양식", ("E8", "1.기초재무상태조사")),
    ("2. 중장기영농목표", "2.중장기영농목표", ("B9", "구분")),
    ("4. 원리금상환계획", "4.원리금상환계획", ("B6", "연도")),
    ("6. 생산계획", "6.생산계획", ("B6", "구분")),
    ("7. 영농자재소요계획", "7.영농자재소요계획", ("B4", "적용기준")),
    ("9 .경비계획", "9.경비계획", ("B4", "항목")),
    ("11. 생산원가계획", "11.생산원가계획", ("B4", "과목")),
    ("13. 추정대차대조표", "13.추정대차대조표", ("B5", "과목")),
    ("14. 현금흐름계획", "14.현금흐름계획", ("B5", "구분")),
    ("15.추정소득분석", "15.추정소득분석", ("B4", "비목별")),
)

# 검사가 읽지 않는 X01 부속 영역. 강동현 정본(X01)의 좌표·머리글로 등록했다.
# rows=(시작 B열 글자, 끝 B열 글자 또는 None=시트 끝): 시작 행부터 끝 글자 행 바로 위까지.
# anchor=(글자, (행 오프셋), (열 오프셋)): 그 글자로 시작하는 칸 기준 사각형.
X01_AREAS = (
    {"sheet": "1. 기초재무상태조사", "what": "나. 부채 표", "rows": ("나.", None), "cols": (2, 8),
     "evidence": "B55 '나. 부채', B57:H57 머리글"},
    {"sheet": "1. 기초재무상태조사", "what": "양식 잔여 글자", "cell": (21, 10, "w"),
     "evidence": "J21 ' w'"},
    {"sheet": "3.투자계획", "what": "가. 총괄 투자계획 표", "rows": ("가.", "나."), "cols": (2, 9),
     "evidence": "B4, B6:I7 머리글(G6:I7 병합), B13 합계"},
    {"sheet": " 5. 판매계획", "what": "다. 연도별 매출액 추정 표와 아래 각주", "rows": ("다.", None),
     "cols": (2, 15), "evidence": "B34, C36:M37 머리글(M36:O37 병합), B45·B46 각주"},
    {"sheet": "8. 노무비계획", "what": "가. 노동력 소요 및 노무비 계획 표", "rows": ("가.", "나."),
     "cols": (2, 21), "evidence": "B4, B6:U7 머리글(U6 '비 고'), B17:B20 각주"},
    {"sheet": "8. 노무비계획", "what": "임금 상승률 관측표", "anchor": ("인건비평균상승률", (0, 14), (-1, 8)),
     "evidence": "X24 제목, Y25:AB25 머리글, W26 '자료집', W33 '예측치', AC38·AD38 평균, "
                 "AF32 출처 메모 → W24:AF38"},
    {"sheet": "12 손익계획", "what": "손익 본표와 아래 판매관리비 산출근거", "rows": ("과목", None),
     "cols": (2, 7), "evidence": "B4:G4 머리글, B26 'X. 당기순이익', B29~ 포장비·택배 산출근거(B~G)"},
)

_TITLE = re.compile(r"^([가-하]|\d+)\.")
_UNIT_PREFIX = "(단위"

NOTICES = {
    "not_applicable": "학과 17시트 재무 양식(X01)이 아닌 파일이라 자동 검사를 하지 않았습니다. "
                      "장비·등급·판로 규칙은 직접 확인하세요.",
    "blocked": "확인 불가: 표 일부를 읽지 못해 검사하지 못했습니다(계획 오류 아님). "
               "표시된 칸을 확인한 뒤 계속하세요.",
}


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
    if status == "blocked":
        severity = "unverified"  # 확인 불가: 계획 오류(error)로 세지 않는다.
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
        "PLAN_FIT_UNCACHED", status="blocked",
        reason="%s!%s 수식 캐시 없음 — 값을 읽지 못해 검사하지 않음" % (sheet, cell),
        remedy="통합문서를 Excel에서 재계산·저장한 사본으로 다시 검사한다",
        evidence={"sheet": sheet, "cell": cell}))


def _matches(name, keywords):
    key = _key(name)
    return [k for k in keywords if _key(k) and _key(k) in key]


# --- 장비·자산 행 ----------------------------------------------------------

def _unreadable(issues, sheet, what):
    """표를 읽지 못함(확인 불가) — '해당 항목 없음'이 아니므로 통과로 보고하지 않는다."""
    issues.append(_issue(
        "PLAN_FIT_TABLE_UNREADABLE", status="blocked",
        reason="%s: %s — 표를 읽지 못해 검사하지 않음" % (sheet, what),
        remedy="적힌 칸을 사람이 확인한다(plan-fit-rules 5절 확인 불가 처리)",
        evidence={"sheet": sheet, "problem": what}))


_EMPTY_TEXT = {"", "-", "–", "—"}
_ASSET_HEADER_WORDS = ("천원", "금액", "평가액", "가격")
_DEPRECIATION_LABELS = {"구분", "취득가격", "잔존가격율", "내용년수(년)", "사용년수(년)",
                        "감가상각비", "감가상각누계", "장부가격"}
_DEPRECIATION_MONEY = {"취득가격", "감가상각비", "감가상각누계", "장부가격"}


def _read_number(book, pair, row, col):
    """(숫자 또는 None, 캐시없음, 숫자 아닌 값). 빈 칸·'-'·'해당 없음'은 None."""
    value, uncached = book.value(pair, row, col)
    if uncached:
        return None, True, None
    if value is None:
        return None, False, None
    number = _number(value)
    if number is not None:
        return number, False, None
    if isinstance(value, str):
        key = _norm(value)
        if key in _EMPTY_TEXT or "해당없음" in key:
            return None, False, None
    return None, False, value


def _addr(row, col):
    return "%s%d" % (openpyxl.utils.get_column_letter(col), row)


def _not_number(row, col, value):
    return "%s 값이 숫자가 아님: %r" % (_addr(row, col), value)


def _b_row(ws, prefix, start=1):
    want = _norm(prefix)
    return next((r for r in range(start, ws.max_row + 1)
                 if _norm(ws.cell(r, 2).value).startswith(want)), None)


def x01_mismatch(book):
    """X01 판별. X01이면 None, 아니면 처음 어긋난 곳(시트 없음·제목·머리글)."""
    for name, title, (cell, head) in X01_SHEETS:
        pair = book.sheet(name)
        if pair is None:
            return "'%s' 시트 없음" % name.strip()
        ws = pair[0]
        for ref, want in (("B2", title), (cell, head)):
            if _SHEET_KEY.sub("", str(ws[ref].value or "")) != _SHEET_KEY.sub("", want):
                return "%s!%s가 '%s'가 아님" % (name.strip(), ref, want)
    return None


def _x01_areas(ws, sheet):
    """이 시트에 등록된 X01 부속 영역 → [(첫 행, 끝 행, 열 범위)]. 찾지 못한 영역은 등록하지 않는다."""
    key = _SHEET_KEY.sub("", sheet)
    out = []
    for spec in X01_AREAS:
        if _SHEET_KEY.sub("", spec["sheet"]) != key:
            continue
        if "rows" in spec:
            start, stop = spec["rows"]
            first = _b_row(ws, start)
            last = ws.max_row
            if first is not None and stop is not None:
                after = _b_row(ws, stop, first + 1)
                last = None if after is None else after - 1
            if first is not None and last is not None:
                lo, hi = spec["cols"]
                out.append((first, last, range(lo, hi + 1)))
        elif "anchor" in spec:
            text, (r0, r1), (c0, c1) = spec["anchor"]
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and _norm(cell.value).startswith(text):
                        out.append((cell.row + r0, cell.row + r1,
                                    range(max(cell.column + c0, 1), cell.column + c1 + 1)))
        else:
            r, c, text = spec["cell"]
            if _norm(ws.cell(r, c).value) == text:
                out.append((r, r, range(c, c + 1)))
    return out


class _TableCheck:
    """표 읽기 공통 단계.

    각 표는 (1) 양식이 기대하는 연도 열·블록 수와 실제로 읽은 수를 대조하고
    (2) 읽기 함수가 해석한 영역(area)을 등록한다. result()는 시트 사용 범위 전체를 훑어
    인식한 영역 — 등록한 표 영역, X01 부속 영역(X01_AREAS), 시트 제목·절 제목·단위 표기·
    B열 각주 — 밖에 값이 하나라도 있으면 그 표 전체를 확인 불가(blocked)로 낸다.
    개별 읽기 함수는 이 확인을 통과한 결과만 쓴다.
    """

    def __init__(self, book, pair, sheet):
        self.book, self.pair, self.sheet = book, pair, sheet
        self.ws_f, self.ws = pair
        self.problems = []
        self.areas = []

    def fail(self, what):
        self.problems.append(what)

    def expect(self, what, expected, found):
        if found < expected:
            self.fail("%s %d개 기대, %d개 읽음" % (what, expected, found))

    def _filled(self, row, col):
        raw, data = self.ws_f.cell(row, col).value, self.ws.cell(row, col).value
        if isinstance(raw, str) and raw.startswith("="):
            return True
        if data is None:
            return False
        return _norm(data) not in _EMPTY_TEXT

    def area(self, first, last, cols, limit):
        """읽기 함수가 해석한 영역(행 first~last, 열 cols)을 등록한다.

        limit(양식 열 한도)은 그 표의 외곽이다. 등록된 X01 부속 영역은 이 외곽 안을
        덮지 않는다(표 안의 머리글 없는 열 값이 부속 영역으로 숨지 않는다).
        """
        self.areas.append((first, last, frozenset(cols), range(limit[0], limit[1] + 1)))

    def header_cols(self, rows, limit, base=None):
        """머리글 줄 rows에서 인정한 열.

        base가 없으면 글자가 있는 열과 머리글 줄에 걸친 병합 열, base가 있으면 base와
        base에서 시작하는 병합의 나머지 열. 양식 열 한도 limit 밖은 넣지 않는다.
        """
        lo, hi = limit
        rows = list(rows)
        if base is None:
            cols = {c for r in rows for c in range(lo, hi + 1) if self._filled(r, c)}
        else:
            cols = {c for c in base if lo <= c <= hi}
        for m in self.ws_f.merged_cells.ranges:
            if any(m.min_row <= r <= m.max_row for r in rows) and (
                    base is None or m.min_col in cols):
                cols.update(c for c in range(m.min_col, m.max_col + 1) if lo <= c <= hi)
        return cols

    def segments(self, rows, col):
        """col 열의 칸 구획 수. 병합된 칸은 하나로, 글자가 없어도 병합이면 하나로 센다."""
        merged = [m for m in self.ws_f.merged_cells.ranges
                  if m.min_col <= col <= m.max_col and m.max_row > m.min_row]
        keys = set()
        for r in rows:
            hit = next((m for m in merged if m.min_row <= r <= m.max_row), None)
            if hit is not None:
                keys.add(("m", hit.min_row))
            elif self._filled(r, col):
                keys.add(("c", r))
        return len(keys)

    def _labelled(self, row, col):
        """시트 제목(B2)·절 제목(B열 '가.' 등)·단위 표기('(단위')·B열 각주 글자."""
        raw, value = self.ws_f.cell(row, col).value, self.ws.cell(row, col).value
        if not isinstance(value, str) or (isinstance(raw, str) and raw.startswith("=")):
            return False
        key = _norm(value)
        if key.startswith(_UNIT_PREFIX):
            return True
        return col == 2 and (row == 2 or bool(_TITLE.match(key))
                             or key.startswith(NOTE_PREFIXES) or len(key) >= NOTE_MIN_LENGTH)

    def residual(self):
        """인식한 영역 밖, 시트 사용 범위 안의 값 칸 주소들."""
        extra = _x01_areas(self.ws, self.sheet)
        cells = []
        for r in range(1, self.ws_f.max_row + 1):
            for c in range(1, self.ws_f.max_column + 1):
                if not self._filled(r, c) or self._labelled(r, c):
                    continue
                if any(first <= r <= last and c in cols for first, last, cols, _ in self.areas):
                    continue
                boxed = any(first <= r <= last and c in box for first, last, _, box in self.areas)
                if not boxed and any(first <= r <= last and c in cols
                                     for first, last, cols in extra):
                    continue
                cells.append(_addr(r, c))
        return cells

    def result(self, rows):
        """(행, None) 또는 (None, 문제들). 읽기 함수는 이 결과만 돌려준다."""
        cells = self.residual()
        if cells:
            self.fail("인식한 영역 밖 값 %d칸(%s%s)" % (
                len(cells), ", ".join(cells[:5]), " 등" if len(cells) > 5 else ""))
        if self.problems:
            return None, "; ".join(self.problems[:4])
        return rows, None


def _investment_rows(book):
    """3.투자계획 연차별 세부투자계획 → ([(행, 항목명, 금액, 캐시없음)], 문제)."""
    pair = book.sheet("3.투자계획")
    if pair is None:
        return None, "시트 없음"
    ws = pair[1]
    check = _TableCheck(book, pair, "3.투자계획")
    header = None
    for r in range(1, ws.max_row + 1):
        if _norm(ws.cell(r, 3).value) == "투자항목":
            header = r
            break
    if header is None:
        return None, "C열 '투자항목' 머리글 없음"
    if _norm(ws.cell(header, 5).value) != "계":
        return None, "E%d 금액 머리글이 '계'가 아님" % header
    # 표 범위: 머리글 아래 B~I에 값이 있거나 B열 연도 병합에 걸친 행이 이어지는 곳까지.
    # 첫 빈 행에서 끝난다(표 아래 메모는 밖).
    merged_b = set()
    for m in pair[0].merged_cells.ranges:
        if m.min_col <= 2 <= m.max_col and m.min_row > header:
            merged_b.update(range(m.min_row, m.max_row + 1))
    last = header
    for r in range(header + 1, ws.max_row + 1):
        if r in merged_b or any(check._filled(r, c) for c in range(2, 10)):
            last = r
        elif last > header:
            break
    # 연도 수: B열 연도 칸 구획(병합된 첫해 블록은 글자가 비어도 한 해로 센다).
    check.expect("연도 칸(B열)", LAYOUT["investment"]["year_rows"],
                 check.segments(range(header + 1, last + 1), 2))
    data_rows = range(header + 1, last + 1)
    # 표 영역: 머리글 두 줄(B~I)의 글자·병합 열과 자료 행. 머리글 없는 열의 값은 영역 밖이다.
    check.area(header - 1, last,
               check.header_cols([header - 1, header], LAYOUT["investment"]["cols"]),
               LAYOUT["investment"]["cols"])
    rows = []
    for r in data_rows:
        name = _norm(ws.cell(r, 3).value)
        if name in ("계", "합계"):
            continue
        amount, uncached, bad = _read_number(book, pair, r, 5)
        if bad is not None:
            check.fail(_not_number(r, 5, bad))
            continue
        if not name:
            # 항목명 없이 금액만 있는 행은 '빈 행'이 아니다. B열 연도가 있는 행은 연도 소계라 제외.
            if (amount or uncached) and not _norm(ws.cell(r, 2).value):
                check.fail("E%d에 금액이 있는데 C%d 투자항목이 비어 있음" % (r, r))
            continue
        rows.append((r, ws.cell(r, 3).value, amount, uncached))
    return check.result(rows)


def _asset_rows(book):
    """1. 기초재무상태조사 자산 블록 → ([(블록, 행, 항목명, 금액, 캐시없음, 비고)], 문제)."""
    pair = book.sheet("1. 기초재무상태조사")
    if pair is None:
        return None, "시트 없음"
    ws = pair[1]
    check = _TableCheck(book, pair, "1. 기초재무상태조사")
    blocks = []
    section_end = ws.max_row + 1
    for r in range(1, ws.max_row + 1):
        label = _norm(ws.cell(r, 2).value)
        if blocks and label.startswith("나."):
            section_end = r  # 자산(가.) 다음 절(부채 등)은 이 검사 밖이다.
            break
        match = _BLOCK_LABEL.match(label)
        if match and match.group(1) in _ASSET_BLOCKS:
            blocks.append((r, _ASSET_BLOCKS[match.group(1)]))
    if not blocks:
        return None, "B열 '(1) 토지' 같은 자산 블록 머리글 없음"
    check.expect("자산 블록", LAYOUT["assets"]["blocks"], len(blocks))
    rows = []
    for index, (start, (role, col)) in enumerate(blocks):
        end = blocks[index + 1][0] if index + 1 < len(blocks) else section_end
        title = str(ws.cell(start, 2).value).strip()
        header = None
        for r in range(start + 1, min(end, start + 4)):
            # 머리글은 글자로만 인정한다. 금액 0·숫자를 머리글로 잡으면 그 행이 사라진다.
            text = ws.cell(r, col).value
            if isinstance(text, str) and any(w in _norm(text) for w in _ASSET_HEADER_WORDS):
                header = r
                break
        if header is None:
            # 빈 블록은 '해당 없음'이다. 무엇이든 적혀 있는데 금액 머리글이 없으면 못 읽음.
            if any(check._filled(r, c) for r in range(start + 1, end) for c in range(2, 9)):
                check.fail("%s 블록(%d행)의 금액 머리글 없음" % (title, start))
            check.area(start, start, {2}, LAYOUT["assets"]["cols"])
            continue
        data_rows = range(header + 1, end)
        # 블록 영역: 블록 제목부터 다음 블록 앞까지, 머리글 줄(B~H)의 글자·병합 열.
        check.area(start, end - 1, check.header_cols([header], LAYOUT["assets"]["cols"]),
                   LAYOUT["assets"]["cols"])
        for r in data_rows:
            label = _norm(ws.cell(r, 2).value)
            if "합계" in label or label == "계" or label.startswith("*"):
                continue
            amount, uncached, bad = _read_number(book, pair, r, col)
            if bad is not None:
                check.fail(_not_number(r, col, bad))
                continue
            name = ws.cell(r, 2).value if label else ws.cell(r, 3).value
            if not _norm(name):
                # 종류(B)·명칭(C)이 모두 비었는데 금액이 있으면 '빈 행'이 아니다.
                if uncached or amount:
                    check.fail("%s에 금액이 있는데 B%d·C%d 종류·명칭이 비어 있음" % (
                        _addr(r, col), r, r))
                continue
            note = _norm(ws.cell(r, 8).value)
            rows.append((role, r, name, amount, uncached, note))
    return check.result(rows)


def _depreciation_items(book):
    """10. 감가상각비계획 → ([(행, 열, 항목명, 취득가격, 캐시없음)], 문제).

    연도 표('구분')마다 판정한다. 한 표를 읽었다고 다른 표의 읽기 실패를 덮지 않는다.
    """
    pair = book.sheet("10. 감가상각비계획")
    if pair is None:
        return None, "시트 없음"
    ws_f, ws = pair
    check = _TableCheck(book, pair, "10. 감가상각비계획")
    sections = [r for r in range(1, ws.max_row + 1) if _norm(ws.cell(r, 2).value) == "구분"]
    if not sections:
        return None, "B열 '구분' 표 없음"
    check.expect("연도 표('구분')", LAYOUT["depreciation"]["blocks"], len(sections))
    items = []
    for index, start in enumerate(sections):
        end = sections[index + 1] if index + 1 < len(sections) else ws.max_row + 1
        cost = next((r for r in range(start + 1, end)
                     if _norm(ws.cell(r, 2).value) == "취득가격"), None)
        if cost is None:
            check.fail("%d행 '구분' 표에 '취득가격' 행 없음" % start)
            continue
        last = cost
        for r in range(cost, end):
            label = _norm(ws.cell(r, 2).value)
            if label in _DEPRECIATION_LABELS:
                last = r
            elif label:
                break  # 다음 연도 제목(나. … 등)
        # 표 영역: '구분'부터 마지막 항목 행까지, B열부터 머리글 두 줄(구분 / 장비 이름)의
        # 마지막 글자·병합 열까지(양식 한도 K). 그 안의 이름 없는 열은 장비 이름을 지운
        # 양식 빈 열 후보라 아래 규칙이 금액 행 전부를 읽어 판정한다. 머리글 끝 오른쪽 값은
        # 영역 밖이라 결과 단계에서 막힌다.
        header_end = max(check.header_cols(range(start, cost), LAYOUT["depreciation"]["cols"]) | {2})
        cols = set(range(2, header_end + 1))
        check.area(start, last, cols, LAYOUT["depreciation"]["cols"])
        value_cols = sorted(c for c in cols if c >= 3)
        for r in range(start + 1, last + 1):
            if r == cost - 1 or _norm(ws.cell(r, 2).value) in _DEPRECIATION_LABELS:
                continue
            if any(_number(ws.cell(r, c).value) is not None
                   or (isinstance(ws_f.cell(r, c).value, str)
                       and ws_f.cell(r, c).value.startswith("="))
                   for c in value_cols):
                check.fail("%d행 '%s'를 해석하지 못함" % (r, ws.cell(r, 2).value))
        money_rows = [r for r in range(cost, last + 1)
                      if _norm(ws.cell(r, 2).value) in _DEPRECIATION_MONEY]
        for c in value_cols:
            name = _norm(ws.cell(cost - 1, c).value)
            if name in _DEPRECIATION_GROUPS:
                continue
            if not name:
                # 장비 이름 없는 열: 금액 행(취득가격·감가상각비·누계·장부가격)이 모두
                # 0·빈 칸일 때만 양식 빈 열이다. 한 칸이라도 값이 있으면 못 읽음.
                for r in money_rows:
                    amount, uncached, bad = _read_number(book, pair, r, c)
                    if bad is not None:
                        check.fail(_not_number(r, c, bad))
                    elif amount or uncached:
                        check.fail("%s에 값이 있는데 %s 장비 이름이 비어 있음" % (
                            _addr(r, c), _addr(cost - 1, c)))
                continue
            amount, uncached, bad = _read_number(book, pair, cost, c)
            if bad is not None:
                check.fail(_not_number(cost, c, bad))
                continue
            items.append((cost, c, ws.cell(cost - 1, c).value, amount, uncached))
    return check.result(items)


def _labor_tasks(book):
    """8. 노무비계획 노동비 산출근거 → ([(행, 작업명, 작업일수, 캐시없음)], 문제)."""
    pair = book.sheet("8. 노무비계획")
    if pair is None:
        return None, "시트 없음"
    ws = pair[1]
    check = _TableCheck(book, pair, "8. 노무비계획")
    titles = sum(1 for r in range(1, ws.max_row + 1)
                 if "노동비산출근거" in _norm(ws.cell(r, 2).value))
    headers = [r for r in range(1, ws.max_row + 1) if _norm(ws.cell(r, 2).value) == "항목"]
    if not headers:
        return None, "B열 '항목' 머리글(노동비 산출근거) 없음"
    check.expect("산출근거 표 제목", LAYOUT["labor"]["tables"], titles)
    check.expect("'항목' 머리글", max(titles, LAYOUT["labor"]["tables"]), len(headers))
    tasks = []
    for index, header in enumerate(headers):
        days_col = next((c for c in range(3, ws.max_column + 1)
                         if _norm(ws.cell(header, c).value) == "작업일수"), None)
        if days_col is None:
            check.fail("%d행 '항목' 표에 '작업일수' 머리글 없음" % header)
            continue
        limit = headers[index + 1] if index + 1 < len(headers) else ws.max_row + 1
        total = None
        for r in range(header + 1, limit):
            label = _norm(ws.cell(r, 2).value)
            if label in ("합계", "계"):
                total = r
                break
            if "노동비산출근거" in label:
                break
        if total is None:
            check.fail("%d행 '항목' 표의 '합계' 행 없음" % header)
            continue
        # 표 영역: '항목' 머리글부터 '합계' 행까지, 머리글 두 줄의 글자·병합 열(B~U).
        # 머리글 없는 열의 값은 영역 밖이다. 오른쪽 임금 상승률 관측표는 X01_AREAS에 등록돼 있다.
        check.area(header, total, check.header_cols([header, header + 1], LAYOUT["labor"]["cols"]),
                   LAYOUT["labor"]["cols"])
        for r in range(header + 1, total):
            name = _norm(ws.cell(r, 2).value)
            if r == header + 1 and not name:
                continue  # 머리글 둘째 줄
            days, uncached, bad = _read_number(book, pair, r, days_col)
            if bad is not None:
                check.fail(_not_number(r, days_col, bad))
                continue
            if name:
                tasks.append((r, ws.cell(r, 2).value, days, uncached))
            elif days or uncached:
                check.fail("%s에 일수가 있는데 B%d 작업명이 비어 있음" % (_addr(r, days_col), r))
    return check.result(tasks)


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

    invest, problem = _investment_rows(book)
    if problem:
        _unreadable(issues, "3.투자계획", problem)
    for r, name, amount, uncached in invest or ():
        if uncached:
            _uncached(issues, "3.투자계획", "E%d" % r)
            continue
        unowned("3.투자계획", "C%d" % r, name, amount, "investment")
        mismatch("3.투자계획", "C%d" % r, name)
    assets, problem = _asset_rows(book)
    if problem:
        _unreadable(issues, "1. 기초재무상태조사", problem)
    for role, r, name, amount, uncached, note in assets or ():
        if uncached:
            _uncached(issues, "1. 기초재무상태조사", "row %d" % r)
            continue
        unowned("1. 기초재무상태조사", "B%d" % r, name, amount,
                "opening_" + role, note)
        if role == "machines":
            mismatch("1. 기초재무상태조사", "B%d" % r, name)
    items, problem = _depreciation_items(book)
    if problem:
        _unreadable(issues, "10. 감가상각비계획", problem)
    for r, c, name, cost, uncached in items or ():
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
    tasks, problem = _labor_tasks(book)
    if problem:
        _unreadable(issues, "8. 노무비계획", problem)
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
        _unreadable(issues, "12 손익계획", "시트 없음")
        return None
    ws = pair[1]
    check = _TableCheck(book, pair, "12 손익계획")
    start = end = None
    for r in range(1, ws.max_row + 1):
        label = _norm(ws.cell(r, 2).value)
        if "판매비와관리비" in label and start is None:
            start = r
        elif start is not None and "영업이익" in label:
            end = r
            break
    header = next((r for r in range(1, (start or 1)) if _norm(ws.cell(r, 2).value) == "과목"), None)
    if start is None or end is None or header is None:
        _unreadable(issues, "12 손익계획", "'과목' 머리글·'판매비와 관리비'~'영업이익' 행 없음")
        return None
    year_cols = [c for c in range(3, ws.max_column + 1) if _YEAR.match(_norm(ws.cell(header, c).value))]
    check.expect("연도 열", LAYOUT["sga"]["years"], len(year_cols))
    data_rows = range(start, end)
    # 표 영역: '과목' 머리글 줄과 판매관리비 행, 연도 열만. 연도 머리글 없는 열 값은 영역 밖이다.
    cols = {2} | set(year_cols)
    check.area(header, header, cols, LAYOUT["sga"]["cols"])
    check.area(start, end - 1, cols, LAYOUT["sga"]["cols"])
    total = 0.0
    for r in data_rows:
        for c in year_cols:
            number, uncached, bad = _read_number(book, pair, r, c)
            if uncached:
                _uncached(issues, "12 손익계획", _addr(r, c))
                return None
            if bad is not None:
                check.fail(_not_number(r, c, bad))
            elif number and r > start:
                total += abs(number)
    rows, problem = check.result(total)
    if problem:
        _unreadable(issues, "12 손익계획", problem)
        return None
    return rows


def _sales_unreadable(issues, sheet, what):
    issues.append(_issue(
        "SALES_LAYOUT_UNREADABLE", status="blocked",
        reason="%s: %s — 판매 단가·비율을 읽지 못해 검사하지 않음" % (sheet, what),
        remedy="학과 17시트 판매계획 양식(평균가격(수매)·평균가격(직거래)·수매시장·직거래 행)을 확인한다",
        evidence={"sheet": sheet, "problem": what}))


def _grade_rows_ok(ws, row):
    """row부터 세 행의 C열이 상품·중품·하품인지(비어 있으면 양식 그대로로 본다)."""
    for offset, grade in enumerate(GRADES):
        text = _norm(ws.cell(row + offset, 3).value)
        if text and text != grade:
            return "C%d 등급이 '%s'가 아님(%r)" % (row + offset, grade, ws.cell(row + offset, 3).value)
    if _norm(ws.cell(row, 3).value) != GRADES[0]:
        return "C%d 첫 등급 '상품' 없음" % row
    return None


def _history_cols(ws_f, ws, header, price_col):
    """가격 이력 연도 머리글 열: 연도 글자·숫자 또는 연도 수식."""
    cols = []
    for c in range(4, price_col):
        head = _norm(ws.cell(header, c).value)
        raw = ws_f.cell(header, c).value
        if _YEAR.match(head) or _number(ws.cell(header, c).value) is not None \
                or (isinstance(raw, str) and raw.startswith("=")):
            cols.append(c)
    return cols


def _read_sales(book, issues):
    """판매계획 단가·이력·비율. 공통 표 확인을 거치며, 못 읽으면 이슈를 남기고 None."""
    pair = book.sheet(" 5. 판매계획")
    if pair is None:
        _sales_unreadable(issues, "5. 판매계획", "시트 없음")
        return None
    ws_f, ws = pair
    sheet = ws.title.strip()
    check = _TableCheck(book, pair, sheet)
    out = {"sheet": sheet, "prices": {}, "history": {}, "shares": {},
           "notes": _sales_notes(ws)}
    blocked = False

    # 가. 연도별 가격 추이 — 판로마다 이력 연도 열 5개 + 평균가격 열.
    for channel, label in PRICE_LABELS.items():
        row = _find_row(ws, label)
        if row is None:
            check.fail("B열 '%s' 행 없음" % label)
            continue
        problem = _grade_rows_ok(ws, row)
        if problem:
            check.fail(problem)
            continue
        header = row - 1
        price_col = next((c for c in range(3, ws.max_column + 1)
                          if _norm(ws.cell(header, c).value) == "평균가격"), None)
        if price_col is None:
            check.fail("%d행 '평균가격' 머리글 없음" % header)
            continue
        hist_cols = _history_cols(ws_f, ws, header, price_col)
        check.expect("%s 가격 이력 연도 열" % CHANNELS[channel],
                     LAYOUT["sales_history"]["years"], len(hist_cols))
        data_rows = range(row, row + len(GRADES))
        # 표 영역: 머리글 줄과 등급 행, 인정한 열(판로·등급·이력 연도·평균가격)만.
        check.area(header, data_rows[-1], check.header_cols(
            [header], LAYOUT["sales_history"]["cols"], base={2, 3} | set(hist_cols) | {price_col}),
            LAYOUT["sales_history"]["cols"])
        for offset, grade in enumerate(GRADES):
            r = row + offset
            cell = _addr(r, price_col)
            number, uncached, bad = _read_number(book, pair, r, price_col)
            if uncached:
                _uncached(issues, sheet, cell)
                blocked = True
            elif bad is not None:
                issues.append(_issue(
                    "SALES_PRICE_UNREADABLE", status="blocked",
                    reason="%s!%s 단가가 숫자가 아님: %r" % (sheet, cell, bad),
                    remedy="단가 칸에는 숫자 또는 빈 칸만 둔다",
                    evidence={"cell": cell}))
                blocked = True
            out["prices"][(channel, grade)] = (number, cell)
            series = []
            for c in hist_cols:
                hv, huncached, hbad = _read_number(book, pair, r, c)
                if huncached:
                    # 이력 수식 캐시 없음은 '빈 이력'이 아니다(미판매 등급 복사 이력을 놓친다).
                    _uncached(issues, sheet, _addr(r, c))
                    blocked = True
                elif hbad is not None:
                    check.fail(_not_number(r, c, hbad))
                series.append(hv)
            out["history"][(channel, grade)] = series

    # 나. 유통채널별 판매계획 — 판매비율 연도 열 5개, 각 옆 판매량 열.
    ratio_rows = {}
    for channel, label in RATIO_LABELS.items():
        row = _find_row(ws, label)
        if row is None:
            check.fail("B열 '%s' 행 없음" % label)
            continue
        problem = _grade_rows_ok(ws, row)
        if problem:
            check.fail(problem)
            continue
        ratio_rows[channel] = row
    if len(ratio_rows) == len(RATIO_LABELS):
        first = min(ratio_rows.values())
        share_header = next((h for h in range(first - 1, max(first - 5, 0), -1)
                             if any(_norm(ws.cell(h, c).value) in ("판매비율", "판매량")
                                    for c in range(3, ws.max_column + 1))), None)
        if share_header is None:
            check.fail("'%s' 위 '판매비율'·'판매량' 머리글 없음" % RATIO_LABELS["purchase"])
        else:
            share_cols = [c for c in range(3, ws.max_column + 1)
                          if _norm(ws.cell(share_header, c).value) == "판매비율"]
            volume_cols = [c for c in range(3, ws.max_column + 1)
                           if _norm(ws.cell(share_header, c).value) == "판매량"]
            year_row = share_header - 1
            note_cols = {c for c in range(3, ws.max_column + 1)
                         if _norm(ws.cell(year_row, c).value) == "비고"}
            check.expect("판매비율 연도 열", LAYOUT["sales_shares"]["years"], len(share_cols))
            check.expect("판매량 연도 열", LAYOUT["sales_shares"]["years"], len(volume_cols))
            last = max(ratio_rows.values()) + len(GRADES)
            data_rows = range(first, last + 1)
            # 표 영역: 연도·판매비율 머리글 두 줄부터 판로 행과 그 아래 '합계' 행까지(양식의
            # 미상품과·합계 행), 인정한 열(판로·등급·판매비율·판매량·비고)만.
            total = _b_row(ws, "합계", last + 1)
            if total is not None and total <= last + 3:
                last = total
            check.area(year_row, last, check.header_cols(
                [year_row, share_header], LAYOUT["sales_shares"]["cols"],
                base={2, 3} | set(share_cols) | set(volume_cols) | note_cols),
                LAYOUT["sales_shares"]["cols"])
            for channel, row in ratio_rows.items():
                for offset, grade in enumerate(GRADES):
                    r = row + offset
                    values = []
                    for c in share_cols:
                        value, uncached, bad = _read_number(book, pair, r, c)
                        if uncached:
                            _uncached(issues, sheet, _addr(r, c))
                            blocked = True
                        elif bad is not None:
                            check.fail(_not_number(r, c, bad))
                        values.append(value or 0.0)
                    out["shares"][(channel, grade)] = values

    _, problem = check.result(out)
    if problem:
        _sales_unreadable(issues, sheet, problem)
        return None
    return None if blocked else out


def _has_note(notes, words):
    return any(any(w in _norm(n) for w in words) for n in notes)


def _check_sales(book, issues):
    sales = _read_sales(book, issues)
    if sales is None:
        return  # _read_sales가 blocked 이유를 이미 남겼다.
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
    """재배방식·등급·판로 적합성 검사 결과 dict. 통합문서를 고치지 않는다.

    status: ``not_applicable``(X01 아님, 검사 안 함), ``fail``(읽은 값의 모순),
    ``blocked``(확인 불가 — 읽지 못한 표가 있음, 계획 오류 아님), ``pass``.
    읽은 값에 모순이 있으면 다른 표가 확인 불가여도 ``fail``이다.
    """
    fit = load_fit(fit_path)
    method_id = resolve_method(method, fit) if method else None
    allow = dict(allow or {})
    book = _Book(Path(workbook_path))
    try:
        issues = []
        mismatch = x01_mismatch(book)
        if mismatch:
            issues.append(_issue(
                "PLAN_FIT_NOT_APPLICABLE", severity="info", status="not_applicable",
                reason="학과 17시트 재무 양식(X01)이 아님: %s" % mismatch,
                remedy="이 검사는 %s(X01) 통합문서만 읽는다. 1~4절 규칙은 사람이 확인한다" % PROFILE,
                evidence={"mismatch": mismatch}))
            return {"status": "not_applicable", "method": method_id,
                    "notice": NOTICES["not_applicable"], "issues": issues}
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
    result = {"status": "pass", "method": method_id, "issues": issues}
    if any(i["severity"] == "error" for i in issues):
        result["status"] = "fail"
    elif any(i["status"] == "blocked" for i in issues):
        result["status"] = "blocked"
        result["notice"] = NOTICES["blocked"]
    return result


EXIT_CODES = {"pass": 0, "not_applicable": 0, "fail": 1, "blocked": 2}
EXIT_USAGE = 3


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
        print(json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False))
        return EXIT_USAGE
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "notice" in result:
        print(result["notice"], file=sys.stderr)
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
