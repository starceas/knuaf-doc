"""W-E2 — cross-section amount conflicts for one (item, year, scope).

Pure module: no file I/O, no model calls, no import of ``gg_core``.
``gg_school_excel`` is imported lazily (the same way ``gg_finance_body``
does) so the existing table/period/unit primitives are reused rather than
forked.

Public entry point::

    detect_amount_gaps(sections, items, *, legacy_issues=None) -> list[dict]

``sections`` = ``[{section_id, path, title, text}]`` in document order,
``items``    = ``[{fact_id, field_id, value, unit, period, scope}]``,
``legacy_issues`` = the rows ``gg_school_excel.crosscheck_body`` already
emitted for the same body (optional; used only for §6 suppression).

Rows use the ``gg_finance_body._issue`` dict shape and the existing
``body_finance_crosscheck`` check id.  Design: ``DESIGN-W-E2.md`` §3
(predicate), §4 (extraction), §6 (suppression), §7 (message), §8 (shape).

Two deliberate cuts keep this from ever blocking a student wrongly; both
lose coverage rather than guess:

* a clause counts only when it carries **its own** year **and** its own
  scope — a clause that inherits either from its sentence is dropped;
* a table is dropped when its caption names another entity, joins two
  names, or aggregates (``합계``/``전체``) — regardless of how many scopes
  ``items`` binds.
"""

from decimal import Decimal, InvalidOperation
import re


def _gg_school_excel():
    import gg_school_excel
    return gg_school_excel


# ---------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------

#: Account names taken verbatim from references/hort-env-systems/
#: finance-fact-map.json, references/fruit-trees/fruit-finance-fact-map*.json,
#: gg_school_excel.py's 17-sheet P&L rows and its cost-sheet rows
#: (DESIGN-W-E2 §9).  Registered ``field_id`` values are unioned in at run
#: time so a map addition needs no edit here.
_ACCOUNT_LABELS = (
    "총투자비 합계",
    "1차년도 투자 소요액",
    "원금상환액",
    "이자상환액",
    "시설 총 시공비",
    "판매금액",
    "영농자재비",
    "인건비 계",
    "경비 계",
    "시설 감가상각비",
    "경비 합계",
    "총생산원가",
    "매출액",
    "소득",
    "순수익",
    "자산총계",
    "당기순이익",
    "생산원가",
    "영업이익",
    "수지균형",
    "누적수지균형",
    "매출원가",
    "매출총이익",
    "판매비와 관리비",
    "영업외 수익",
    "영업외 비용",
    "세금차감전 순익",
    "세금",
    "감가상각비",
    "당기총생산원가",
    "현금생산원가",
)

#: One rejected word anywhere in the clause drops that clause's candidates
#: (§4).  Applied to the whole ``ctx`` for tables.
_REJECT = re.compile(
    r"목표|전국|평균|타\s*농가|다른\s*농가|이웃|시장|단가|전년|작년|지난해"
    r"|예상|추정|전망|실적|약\s*\d|만원|억원"
)

_SENT_SEP = re.compile(
    r"(?:\r?\n)+"
    r"|(?<=[^\d])\.(?:\s+|$)"
    r"|(?<=\d)\.(?=[^\d\s]|\s+[^\d])"
    r"|[;•·]+"
)

_CLAUSE_SEP = re.compile(
    r"이며|이고|이지만|하며|하고|그리고|또한|한편|반면"
    r"|(?<![0-9]),(?![0-9])|;|·"
)

_YEAR_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:년도|년)?(?!\d)")

_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

_AFTER_LABEL = re.compile(
    r"^\s*(?:은|는|이|가)?\s*[:=]?\s*(" + _NUMBER + r")\s*(천원|원)"
    r"(?![A-Za-z0-9/㎡])"
)

_PARALLEL_AFTER_UNIT = re.compile(r"(?:에서|부터|~|-|및|과|와)")

_TAIL_ENDERS = re.compile(r"(?:이다|입니다|이었다|였다|임)")
_TAIL_PARTICLES = re.compile(
    r"(?:으로|로|에서|부터|까지|정도|가량|수준|씩|당|의|은|는|이|가|을|를"
    r"|과|와|도|만|및)"
)
_TAIL_UNIT_PAREN = re.compile(
    r"\(\s*(?:단위\s*[:：]\s*)?[0-9,.\s]*(?:천원|원)\s*\)"
)

#: Words that may precede an account name inside the same clause without
#: turning that occurrence into a different thing (item/period/scenario).
_PREFIX_OK = frozenset(
    "의 은 는 이 가 에서 에는 에서는 도 만 과 와 및 중 기준 대비".split()
)

#: Caption words that name the table itself rather than a sub-entity (§4).
_CAPTION_WORDS = frozenset(
    "표 그림 부록 자료 출처 단위 계획 사업 추정 전망 목표 기준 합계 소계 전체"
    " 당기 차기 본 해당".split()
)

#: Plan phrasing that may trail a caption title without narrowing the value.
_PLAN_WORDS = frozenset(
    "개년 추진 향후 중장기 장기 단기 연차 종합 기본 수립 예정 개략 세부".split()
)

#: A table header cell must hold exactly one year and nothing else (§4).
HEAD_OK = re.compile(
    r"^\s*\(?\s*((?:19|20)\d{2})\s*(?:년도|년)?\s*\)?\s*"
    r"(?:\(\s*(?:단위\s*[:：]\s*)?(?:천원|원)\s*\))?\s*$"
)

_JOIN = re.compile(r"(?<=[가-힣])\s*(?:·|및|와|과)\s*(?=[가-힣])")

_LEGACY_REASON = "기간·단위·범위·출력 반올림 값 불일치"


# ---------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------

def _dec(text):
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _places(number_text):
    """Display decimals written for a number (``1,000.4`` → 1)."""
    _, _, frac = str(number_text).partition(".")
    return len(frac)


def _issue(location, reason, amounts, pairs):
    """Same dict shape as ``gg_finance_body._issue`` (§8)."""
    return {
        "code": "BODY_SECTION_AMOUNT_CONFLICT",
        "check_id": "body_finance_crosscheck",
        "status": "fail",
        "severity": "error",
        "fact_id": None,
        "consumer_id": None,
        "location": location,
        "amounts": amounts,
        "pairs": pairs,
        "reason": reason,
        "remedy": "같은 항목·연도·범위의 금액을 모든 절에서 "
                  "실제로 계획한 한 값으로 통일하세요.",
        "required_for": ["submission_candidate"],
    }


def _public(occ):
    return {
        "section_id": occ["section_id"],
        "title": occ["title"],
        "amount": occ["amount"],
        "unit": occ["unit"],
        "year": occ["year"],
        "scope": occ["scope"],
        "where": occ["where"],
        "line": occ["line"],
        "table": occ["table"],
        "row": occ["row"],
        "cell": occ["cell"],
    }


def _spans(text, pattern):
    out = []
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            out.append((pos, match.start()))
        pos = match.end()
    if pos < len(text):
        out.append((pos, len(text)))
    return out


def _amount_tail_ok(tail):
    """Guards the text right after ``<number><unit>`` (§4).

    Sentence enders, particles and numeric-only unit parentheses
    (``(1,000,000원)``, ``(천원)``, ``(단위: 천원)``) are stripped
    repeatedly; anything else — ``(상반기)``, ``상반기다`` — rejects the
    occurrence so a half-year or item breakdown never reads as the annual
    figure of the whole scope.
    """
    s = tail
    for _ in range(8):
        s = s.strip()
        for pattern in (_TAIL_ENDERS, _TAIL_PARTICLES, _TAIL_UNIT_PAREN):
            match = pattern.match(s)
            if match is not None:
                s = s[match.end():]
                break
        else:
            break
    s = s.strip().rstrip(".!?。…,)]}〉》")
    return s.strip() == ""


def _prefix_ok(prefix, scopes, labels):
    """The part of the clause before the account name must be neutral.

    A leftover Korean token (``토마토``, ``상반기``, ``계획상``) means the
    claus describes a sub-entity, not the scope's annual figure, so the
    occurrence is dropped (§4 name-prefix modifier guard).
    """
    s = prefix
    for scope in sorted(scopes, key=len, reverse=True):
        s = re.sub(
            r"(?<![가-힣A-Za-z0-9_])" + re.escape(scope) + r"(?![A-Za-z0-9_])",
            " ", s)
    for label in labels:
        s = s.replace(label, " ")
    s = re.sub(r"(?:19|20)\d{2}", " ", s)
    s = re.sub(r"년도|년|차년도|차년|연차", " ", s)
    s = re.sub(r"천원|원", " ", s)
    s = re.sub(r"[\d,.\-+~()\[\]<>:;·•/㎡%]+", " ", s)
    return all(token in _PREFIX_OK for token in re.findall(r"[가-힣]+", s))


def _caption_ok(caption, scopes, labels):
    """Caption must name this table only — no other entity, no aggregate."""
    text = caption.strip()
    if not text:
        return True
    if _JOIN.search(text):
        return False
    if re.search(r"합계|전체", text):
        return False
    s = text
    for label in labels:
        s = s.replace(label, " ")
    for scope in sorted(scopes, key=len, reverse=True):
        s = re.sub(re.escape(scope), " ", s)
    s = re.sub(r"(?:표|그림|부록)\s*\d+", " ", s)
    s = re.sub(r"\d+\s*개년", " ", s)
    s = re.sub(r"(?:19|20)\d{2}", " ", s)
    s = re.sub(r"년도|년|차년도|차년|연차", " ", s)
    s = re.sub(r"천원|원", " ", s)
    s = re.sub(r"[\d,.\-+~()\[\]<>:;·•/㎡%]+", " ", s)
    for token in re.findall(r"[가-힣]+", s):
        if token in _PREFIX_OK or token in _CAPTION_WORDS:
            continue
        if token in _PLAN_WORDS:
            continue
        return False
    return True


# ---------------------------------------------------------------------
# occurrence extraction — prose (§4)
# ---------------------------------------------------------------------

def _prose_occurrences(gse, text, scopes, labels, index, section):
    label_re = re.compile(
        r"(?<![가-힣A-Za-z0-9])("
        + "|".join(re.escape(name) for name in labels)
        + r")"
    )
    out = []
    for start, end in _spans(text, _SENT_SEP):
        sentence = text[start:end]
        if not sentence.strip() or sentence.lstrip().startswith("|"):
            continue
        line = text.count("\n", 0, start) + 1
        for clause in _spans(sentence, _CLAUSE_SEP):
            piece = sentence[clause[0]:clause[1]]
            if not piece.strip() or _REJECT.search(piece):
                continue
            years = {int(m.group(1)) for m in _YEAR_TOKEN.finditer(piece)}
            if len(years) != 1:
                continue
            found = [sc for sc in scopes if gse._has_scope(piece, sc)]
            if len(found) != 1:
                continue
            year, scope = years.pop(), found[0]
            for match in label_re.finditer(piece):
                rest = piece[match.end():]
                amount_match = _AFTER_LABEL.match(rest)
                if amount_match is None:
                    continue
                tail = rest[amount_match.end():]
                if _PARALLEL_AFTER_UNIT.match(tail):
                    continue
                if not _amount_tail_ok(tail):
                    continue
                if not _prefix_ok(piece[:match.start()], scopes, labels):
                    continue
                number, unit = amount_match.group(1), amount_match.group(2)
                value = _dec(number)
                if value is None:
                    continue
                out.append({
                    "section_id": section.get("section_id"),
                    "title": (section.get("title") or "").strip(),
                    "path": section.get("path"),
                    "index": index,
                    "label": match.group(1),
                    "year": year,
                    "scope": scope,
                    "unit": unit,
                    "amount": number + unit,
                    "value": value,
                    "places": _places(number) - (3 if unit == "천원" else 0),
                    "where": "문장",
                    "line": line,
                    "table": None,
                    "row": None,
                    "cell": None,
                })
    return out


# ---------------------------------------------------------------------
# occurrence extraction — tables (§4)
# ---------------------------------------------------------------------

def _table_occurrences(gse, text, scopes, labels, index, section):
    out = []
    for table_no, (rows, ctx) in enumerate(gse._body_tables(text), 1):
        if len(rows) < 2:
            continue
        ctx = ctx or ""
        if _REJECT.search(ctx):
            continue
        found = [sc for sc in scopes if gse._has_scope(ctx, sc)]
        if len(found) != 1:
            continue
        scope = found[0]
        caption = (ctx.splitlines() or [""])[0]
        if not _caption_ok(caption, scopes, labels):
            continue
        ncols = max(len(row) for row in rows)

        def cell(r, c):
            row = rows[r]
            return row[c] if c < len(row) else ""

        for r in range(1, len(rows)):
            for c in range(1, ncols):
                for label_cell, period_cell in (
                    (cell(r, 0), cell(0, c)),
                    (cell(0, c), cell(r, 0)),
                ):
                    header = HEAD_OK.match(period_cell)
                    if header is None:
                        continue
                    year = int(header.group(1))
                    value_text = cell(r, c)
                    value_match = gse._CELL_VALUE.match(value_text)
                    if value_match is None:
                        continue
                    number = value_match.group(1) or value_match.group(2)
                    for label in labels:
                        matched, declared = gse._label_cell(
                            label_cell, label)
                        if not matched:
                            continue
                        unit = declared
                        if unit is None:
                            for candidate in ("천원", "원"):
                                if gse._has_unit(
                                    ctx + "\n" + label_cell + "\n"
                                    + period_cell, candidate,
                                ):
                                    unit = candidate
                                    break
                        if unit not in ("천원", "원"):
                            continue
                        if value_match.group(3).strip() not in ("", unit):
                            continue
                        value = _dec(number)
                        if value is None:
                            continue
                        if value_match.group(1) is not None:
                            value = -value
                        if "년" in period_cell:
                            ok, conflicted = gse._table_claim_verdict(
                                rows, ctx, label, value, unit,
                                {("year", year)}, scope)
                            if not ok or conflicted:
                                continue
                        out.append({
                            "section_id": section.get("section_id"),
                            "title": (section.get("title") or "").strip(),
                            "path": section.get("path"),
                            "index": index,
                            "label": label,
                            "year": year,
                            "scope": scope,
                            "unit": unit,
                            "amount": number + unit,
                            "value": value,
                            "places": _places(number)
                            - (3 if unit == "천원" else 0),
                            "where": "표",
                            "line": None,
                            "table": table_no,
                            "row": r,
                            "cell": c,
                        })
    return out


# ---------------------------------------------------------------------
# §6 suppression
# ---------------------------------------------------------------------

def _suppression_keys(gse, legacy_issues, items):
    """``(field_id, year, scope)`` triples a legacy row already reports.

    Restored only when the legacy row failed for a period/unit/scope
    mismatch, joins an ``items`` row through ``fact_id``, and that row's
    ``period`` reads as exactly one year.  Any gap means no suppression —
    a duplicate row is cheaper than a hidden conflict.
    """
    by_fact = {
        item.get("fact_id"): item for item in items if item.get("fact_id")
    }
    keys = set()
    for issue in legacy_issues or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("status") != "fail":
            continue
        if _LEGACY_REASON not in str(issue.get("reason") or ""):
            continue
        item = by_fact.get(issue.get("fact_id"))
        if item is None:
            continue
        years = {
            value for kind, value in gse._period_keys(item.get("period"))
            if kind == "year"
        }
        if len(years) != 1:
            continue
        keys.add((item.get("field_id"), years.pop(), item.get("scope")))
    return keys


# ---------------------------------------------------------------------
# comparison + message (§3, §7)
# ---------------------------------------------------------------------

def _won(occ):
    """The occurrence's amount expressed in 원."""
    value = occ["value"]
    return value * 1000 if occ.get("unit") == "천원" else value


def _disagree(gse, left, right):
    """Compare both amounts in 원 at the coarser of the two resolutions.

    ``places`` is already a decimal count for the value in 원 (``천원``
    numbers are shifted three places), so the coarser of the two is
    ``min``; each side is converted to 원 *before* quantizing.
    """
    places = min(left["places"], right["places"])
    a = gse._round_half_up(_won(left), places)
    b = gse._round_half_up(_won(right), places)
    if a is None or b is None:
        return False
    return a != b


def _section_name(index, title):
    title = (title or "").strip()
    return title + " 절" if title else f"본문 {index}번째 절"


def _message(year, label, ordered):
    """§7 wording; ``ordered`` = [(index, title, amount)] document order."""
    named = [
        (index, title.strip(), _section_name(index, title), amount)
        for index, title, amount in ordered
    ]
    if len(named) < 5:
        head = f"{named[0][2]}의 {year}년 {label} {named[0][3]}"
        if len(named) == 2:
            body = f"{head}과 {named[1][2]}의 {named[1][3]}"
        else:
            body = head + "".join(
                f", {name}의 {amount}"
                for _, _, name, amount in named[1:])
        count = {2: "두", 3: "세", 4: "네"}[len(named)]
        return (
            f"{body}이 서로 다릅니다. {count} 절에 적은 금액을 실제로 "
            "계획한 한 값으로 통일하세요."
        )
    groups = {}
    order = []
    for index, title, fallback, amount in named:
        if amount not in groups:
            groups[amount] = []
            order.append(amount)
        groups[amount].append(title or fallback)
    parts = []
    for amount in order:
        joined = "·".join(groups[amount])
        if not joined.endswith("절"):
            joined += " 절"
        parts.append(f"{joined}: {amount}")
    return (
        f"{len(named)}개 절의 {year}년 {label} 금액이 서로 다릅니다 — "
        + ", ".join(parts)
        + ". 모든 절에 적은 금액을 실제로 계획한 한 값으로 통일하세요."
    )


def _row(gse, key, members, bad):
    label, year, scope = key
    ordered = []
    seen = {}
    for occ in members:
        sid = occ["section_id"]
        if sid not in seen:
            seen[sid] = occ
            ordered.append((occ["index"], occ["title"], occ["amount"]))
    ordered.sort(key=lambda row: row[0])
    first = members[0]
    location = {
        "path": first["path"],
        "section_id": first["section_id"],
        "line": first["line"],
        "column": None,
        "table": first["table"],
        "row": first["row"],
        "cell": first["cell"],
    }
    return _issue(
        location,
        _message(year, label, ordered),
        [_public(occ) for occ in members],
        [[_public(left), _public(right)] for left, right in bad],
    )


# ---------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------

def detect_amount_gaps(sections, items, *, legacy_issues=None):
    """One row per ``(label, year, scope)`` whose amounts disagree across
    two or more different TOC sections of ``sections``."""
    gse = _gg_school_excel()
    if isinstance(sections, dict):
        sections = list(sections.values())
    sections = [s for s in (sections or []) if isinstance(s, dict)]
    items = [i for i in (items or []) if isinstance(i, dict)]
    scopes = sorted({i.get("scope") for i in items if i.get("scope")})
    if not sections or not scopes:
        return []
    labels = sorted(
        set(_ACCOUNT_LABELS)
        | {i.get("field_id") for i in items if i.get("field_id")},
        key=len, reverse=True)

    occurrences = []
    for index, section in enumerate(sections, 1):
        text = section.get("text") or ""
        if not text.strip():
            continue
        occurrences.extend(
            _prose_occurrences(gse, text, scopes, labels, index, section))
        occurrences.extend(
            _table_occurrences(gse, text, scopes, labels, index, section))

    groups = {}
    for occ in occurrences:
        groups.setdefault(
            (occ["label"], occ["year"], occ["scope"]), []).append(occ)

    suppressed = _suppression_keys(gse, legacy_issues, items)
    rows = []
    for key, members in groups.items():
        if key in suppressed:
            continue
        section_ids = []
        for occ in members:
            if occ["section_id"] not in section_ids:
                section_ids.append(occ["section_id"])
        if len(section_ids) < 2:
            continue
        bad = []
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                if left["section_id"] == right["section_id"]:
                    continue
                if _disagree(gse, left, right):
                    bad.append((left, right))
        if not bad:
            continue
        rows.append(_row(gse, key, members, bad))
    return rows
