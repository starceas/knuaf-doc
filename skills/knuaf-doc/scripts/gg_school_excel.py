"""School 17-sheet workbook. Sheet names follow the exemplar formula refs."""

import json
import hashlib
import re
import zipfile
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from gg_core import digest, local
from gg_finance import num
from gg_price_assumptions import footnote as price_footnote
from gg_price_assumptions import multiplier_d as price_multiplier_d
from gg_price_assumptions import normalize_price_assumptions
from gg_price_assumptions import canonical_dimensions, check_dimensions

# Irregular names are formula targets. Do not "fix" spaces or periods.
SCHOOL_SHEETS = (
    "목록",
    "1. 기초재무상태조사",
    "2. 중장기영농목표",
    "3.투자계획",
    "4. 원리금상환계획",
    " 5. 판매계획",
    "6. 생산계획",
    "7. 영농자재소요계획",
    "8. 노무비계획",
    "9 .경비계획",
    "10. 감가상각비계획 ",
    "11. 생산원가계획",
    "12 손익계획",
    "13. 추정대차대조표",
    "14. 현금흐름계획",
    "15.추정소득분석",
    "참고1. 모델농장분석",
)
YEAR_LABELS = "가나다라마"
EVIDENCE_KEYS = ("area", "seedlings", "yield", "growth", "commodity")
# 지원 통합문서 판. v1 = 기존 양식(투자 2줄·총 생산량 입력), v2 = 새 양식
# (투자 3줄·연도별 10a당 목표). 판 식별은 영수증이 묶은 원본 자산 sha가 한다.
PROFILES = ("school_17_sheet_v1", "school_17_sheet_v2")
# 14. 현금흐름계획 좌표는 양식마다 한 행 어긋난다. X01 정본(투자 2줄)은
# 소계(B) 21 · 수지(C) 22 · 누적(D) 23 · 주석 24, X02·레거시 생성물·새
# 양식(투자 3줄)은 22·23·24·25다. X01과 X02는 같은 프로필 이름
# (school_17_sheet_v1)을 쓰므로 프로필로는 못 가른다 — 시트 라벨로 읽는다
# (``cashflow_layout``).
CF_LABEL_INFLOW = ("소계", "A")
CF_LABEL_OUTFLOW = ("소계", "B")
CF_LABEL_NET = "수지균형"
CF_LABEL_CUM = "누적수지균형"
CF_LABEL_NOTE = ("당기", "감가상각")
# v2만 연도별 10a당 목표(kg) 5개를 받는다. v1은 총 생산량 단일 입력이다.
PRODUCTION_YIELD_FIELD = "production_yield_kg_per_10a"

WORKBOOK_REFERENCE_SET = (
    Path(__file__).resolve().parents[1] / "references"
    / "workbook-reference-set.json"
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

def profile_structure(profile):
    """Registered sheet structure for a workbook profile, or None.

    Only profiles registered in the shipped reference set are
    structure-bound. Horticulture keeps its dedicated transform receipt and
    specialty crops keep the historical render route; neither is opened by
    this common gate.
    """
    try:
        data = json.loads(WORKBOOK_REFERENCE_SET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("workbook reference set unreadable") from error
    for entry in data.get("workbook_profiles") or []:
        if not isinstance(entry, dict) or entry.get("profile") != profile:
            continue
        sheets = entry.get("sheets")
        digest = entry.get("sha256")
        formulas = entry.get("formula_fingerprints")
        if (not isinstance(sheets, list) or not sheets
                or any(not isinstance(name, str) or not name for name in sheets)
                or not isinstance(digest, str) or not _SHA256.match(digest)
                or not isinstance(formulas, dict) or set(formulas) != set(sheets)
                or any(not isinstance(value, str) or not _SHA256.fullmatch(value)
                       for value in formulas.values())):
            raise ValueError("workbook profile structure invalid: " + str(profile))
        return {"sha256": digest, "sheets": list(sheets),
                "formula_fingerprints": dict(formulas), "label": entry.get("label")}
    return None

def formula_fingerprints(book):
    """Hash sorted (address, expanded formula) pairs per sheet; ignore values."""
    result = {}
    for sheet in book:
        formulas = sorted((cell.coordinate, cell.value)
                          for row in sheet for cell in row if cell.data_type == "f")
        raw = json.dumps(formulas, ensure_ascii=False, separators=(",", ":"))
        result[sheet.title] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return result


# 두 17시트 판의 골격 차이 — 수식 **주소** 집합이다. 값 채우기·수식 패치는
# 수식 문면을 바꾸지만 주소는 옮기지 않으므로, 감사 계보가 끊긴 사본도 자기
# 판을 유지한다. 값은 실측이다(감사 인벤토리 x01.json·
# school_17_sheet_v2.json의 주소 차집합). 시험이 감사와 다시 대조해 잠근다.
EDITION_FORMULA_ADDRESSES = {
    "school_17_sheet_v1": frozenset({
        ("6. 생산계획", "H7"), ("6. 생산계획", "K7"), ("6. 생산계획", "N7"),
        ("6. 생산계획", "Q7"), ("6. 생산계획", "T7"),
    }),
    "school_17_sheet_v2": frozenset({
        ("6. 생산계획", "H9"),
        ("14. 현금흐름계획", "D24"), ("14. 현금흐름계획", "E24"),
        ("14. 현금흐름계획", "F24"), ("14. 현금흐름계획", "G24"),
        ("14. 현금흐름계획", "H24"),
    }),
}


def formula_cells(book):
    """``(sheet, cell)`` of every stored formula in an open openpyxl book."""
    return {(sheet.title, cell.coordinate)
            for sheet in book for row in sheet.iter_rows() for cell in row
            if cell.data_type == "f"}


def edition_marker(cells):
    """The edition a formula address set is, or None.

    Only the cells the two editions do not share are read, so a copy whose
    formulas were filled, cleared or patched still says which edition it is.
    Both editions' markers are counted and the larger count wins: a student
    who overwrites one of the first edition's five marker cells leaves the
    other four, and the file is still that edition.  A set that carries the
    same count from each edition — including neither — names no edition, and
    the caller must refuse rather than guess: there is no fallback to the
    first edition, because a second-edition workbook that lost its own
    markers would be handed the older form's rows.
    """
    cells = set(cells)
    scores = {name: len(addresses & cells)
              for name, addresses in EDITION_FORMULA_ADDRESSES.items()}
    best = max(scores.values())
    if best == 0:
        return None
    winners = [name for name, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def edition_of_workbook(path):
    """Read a workbook's formula addresses and name its edition, or None."""
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception:
        return None
    try:
        return edition_marker(formula_cells(book))
    finally:
        book.close()


def is_school_form(path):
    """True when the file carries the 17-sheet school form's sheet order.

    The two editions are distinguished by formula addresses; a file with a
    different sheet order is not this form at all and its edition question
    does not apply.  An unreadable file is not this form either — reading it
    is the caller's own later step, and its failure belongs there.
    """
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception:
        return False
    try:
        return list(book.sheetnames) == list(SCHOOL_SHEETS)
    finally:
        book.close()


def is_readable_workbook(path):
    """True when the file opens as a workbook, whatever form it carries."""
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception:
        return False
    book.close()
    return True


def registered_profiles():
    """Every structure-bound profile in the shipped reference set."""
    try:
        data = json.loads(WORKBOOK_REFERENCE_SET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("workbook reference set unreadable") from error
    return {entry["profile"] for entry in data.get("workbook_profiles") or []
            if isinstance(entry, dict) and isinstance(entry.get("profile"), str)}


def _refuse_foreign_registered_form(path, profile):
    """Fail an unregistered-profile workbook that is another profile's form.

    Values are filled in by then, so only sheet order and formula
    fingerprints are compared. A 17-sheet workbook whose formula addresses
    name no single edition is refused here too: the declared profile is one
    without a registered form of its own, so nothing later would catch a
    second-edition paper carrying that label — a declaration never answers
    for a form that cannot be read. Any other shape is left to its own
    verifier, as before.
    """
    others = sorted(registered_profiles() - {profile})
    if not others:
        return
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception:
        return
    try:
        observed = list(book.sheetnames)
        fingerprints = None
        for other in others:
            expected = profile_structure(other)
            if expected is None or observed != expected["sheets"]:
                continue
            if fingerprints is None:
                fingerprints = formula_fingerprints(book)
            if all(fingerprints[name] == expected["formula_fingerprints"][name]
                   for name in observed):
                raise ValueError(
                    "workbook structure is the registered %s form, not %s; "
                    "declare the profile whose edition the file is"
                    % (other, profile))
        # A formula that was patched no longer matches any registered
        # fingerprint, so the loop above cannot see it. The two editions of
        # the 17-sheet form move different rows: their formula *addresses*
        # still say which edition the file is after a patch. Only a file that
        # is that form is asked — another shape has no edition to declare.
        if observed == list(SCHOOL_SHEETS):
            marker = edition_marker(formula_cells(book))
            if marker != profile:
                raise ValueError(
                    "workbook structure is the %s edition, not %s; "
                    "declare the profile whose edition the file is"
                    % (marker or "unrecognised", profile))
    finally:
        book.close()


def require_profile_structure(path, profile):
    """Open the real workbook and require its registered school form.

    A caller-written profile label never substitutes for this check. Sheet
    order and formula fingerprints must match; student values may differ.
    A profile with no registered structure is not left unchecked: a workbook
    that *is* another registered profile's form is refused, so one edition's
    bytes cannot be declared under a label that has no structure of its own.
    """
    drift = _structure_violation(path, profile)
    if drift is None:
        return
    if isinstance(drift, FormulaDrift):
        # The form is the declared one; only formulas differ. A route that
        # rewrites formulas by design (``patch_copy``) re-asks the weaker
        # question below; every other route keeps this answer.
        raise drift
    raise ValueError(drift)


class FormulaDrift(ValueError):
    """The form is right, its formulas are not the registered ones."""


def _structure_violation(path, profile):
    """None when ``path`` is ``profile``'s form, else the refusal."""
    expected = profile_structure(profile)
    if expected is None:
        _refuse_foreign_registered_form(path, profile)
        return None
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception as error:
        return "workbook structure unreadable: %s" % error
    try:
        observed = list(book.sheetnames)
        if observed != expected["sheets"]:
            return (
                "workbook structure mismatch for %s: %d sheets do not match "
                "the registered %d-sheet school form"
                % (profile, len(observed), len(expected["sheets"])))
        fingerprints = formula_fingerprints(book)
        changed = [name for name in observed
                   if fingerprints[name] != expected["formula_fingerprints"][name]]
        if changed:
            return FormulaDrift(
                "workbook formula fingerprint mismatch for %s: %s"
                % (profile, ", ".join(changed)))
        return None
    finally:
        book.close()


def require_profile_edition(path, profile):
    """Require the declared edition's forms *and* its formula skeleton.

    Used where formulas are expected to differ from the registered set: a
    patched copy still moves the cells its edition moves. Sheet order must
    match and the edition skeleton must name ``profile``, so a copy of one
    edition can never be declared as the other.
    """
    expected = profile_structure(profile)
    if expected is None:
        _refuse_foreign_registered_form(path, profile)
        return
    from openpyxl import load_workbook

    try:
        book = load_workbook(path, read_only=True)
    except Exception as error:
        raise ValueError("workbook structure unreadable: %s" % error) from error
    try:
        observed = list(book.sheetnames)
        if observed != expected["sheets"]:
            raise ValueError(
                "workbook structure mismatch for %s: %d sheets do not match "
                "the registered %d-sheet school form"
                % (profile, len(observed), len(expected["sheets"])))
        marker = edition_marker(formula_cells(book))
        if marker != profile:
            raise ValueError(
                "workbook structure is the %s edition, not %s; declare the "
                "profile whose edition the file is"
                % (marker or "unrecognised", profile))
    finally:
        book.close()


def _label_text(value):
    return re.sub(r"\s+", "", str(value or ""))


def _cashflow_row(ws, markers):
    """First row whose B/C label contains all ``markers``."""
    if isinstance(markers, str):
        markers = (markers,)
    for row in ws.iter_rows(min_col=2, max_col=3):
        for cell in row:
            text = _label_text(cell.value)
            if text and all(m in text for m in markers):
                return cell.row
    return None


def cashflow_layout(ws):
    """``14. 현금흐름계획`` row numbers read from the sheet's own labels.

    두 배치가 유통된다: X01 정본(투자 2줄)은 유입 14 · 지출소계 21 ·
    수지 22 · 누적 23 · 주석 24, X02·레거시 생성물·새 양식(투자 3줄)은
    22·23·24·25. X01과 X02는 같은 프로필 이름을 쓰므로 프로필로는 가를
    수 없다 — 라벨이 가른다.
    """
    rows = {
        "in": _cashflow_row(ws, CF_LABEL_INFLOW),
        "out": _cashflow_row(ws, CF_LABEL_OUTFLOW),
        "net": _cashflow_row(ws, CF_LABEL_NET),
        "cum": _cashflow_row(ws, CF_LABEL_CUM),
        "note": _cashflow_row(ws, CF_LABEL_NOTE),
    }
    names = {"in": "유입소계", "out": "지출소계", "net": "수지", "cum": "누적",
             "note": "주석"}
    missing = [names[k] for k, v in rows.items() if v is None]
    if missing:
        raise ValueError("현금흐름계획 라벨 누락: " + ", ".join(missing))
    if not (rows["in"] < rows["out"] < rows["net"] < rows["cum"] < rows["note"]):
        raise ValueError("현금흐름계획 라벨 순서 이상: %s" % rows)
    return rows


def profile_supports(profile):
    """Whether this module's generator and verifier handle a workbook profile.

    Callers outside this module use it instead of comparing the profile
    literal, so a new form is added in one place.
    """
    return profile in PROFILES


def production_yield_rows(spec):
    """A v2 spec's yearly 10a targets in kg, as Decimals (5 entries).

    v1 specs store one total in ``production_kg`` and have no such field.
    """
    raw = spec.get(PRODUCTION_YIELD_FIELD)
    if (not isinstance(raw, (list, tuple)) or len(raw) != 5
            or any(isinstance(v, bool) or v is None for v in raw)):
        raise ValueError("10a당 생산량 목표 5개 필요")
    rows = []
    for value in raw:
        try:
            number = value if isinstance(value, Decimal) else num(str(value))
        except ValueError as error:
            raise ValueError("10a당 생산량 목표는 십진 수치 필요") from error
        if number < 0:
            raise ValueError("10a당 생산량 목표는 0 이상 필요")
        rows.append(number)
    return tuple(rows)


def q(name):
    return "'" + name.replace("'", "''") + "'"


def plan_years(start_year):
    if type(start_year) is not int or isinstance(start_year, bool) or not 1900 <= start_year <= 9995:
        raise ValueError("영농 시작연도 정수 필요")
    return [start_year + i for i in range(5)]


def check_unsupported(spec):
    profile = spec.get("profile")
    if not profile_supports(profile):
        return None
    if any(spec.get(k) for k in ("processing", "perennial", "shared_facilities")):
        return {
            "status": "unsupported",
            "profile": profile,
            "reason": "가공·다년생·공유시설 자동 산식 미검증. 원계획을 보존합니다.",
        }
    grants = spec.get("grants")
    if grants is not None and grants != 0 and grants != "0" and grants != 0.0:
        return {
            "status": "unsupported",
            "profile": profile,
            "reason": "보조금 자동 산식 미검증. 원계획을 보존합니다.",
        }
    if "crops" in spec and len(spec["crops"]) > 1:
        return {
            "status": "unsupported",
            "profile": profile,
            "reason": "다작목 자동 산식 미검증. 원계획을 보존합니다.",
        }
    if "period_data" in spec or "yearly_data" in spec:
        return {
            "status": "unsupported",
            "profile": profile,
            "reason": "연차별 변동 계획 자동 산식 미검증. 원계획을 보존합니다.",
        }
    return None


def validate(spec, *, resolver=None):
    profile = spec.get("profile")
    if not profile_supports(profile):
        raise ValueError("school_17_sheet_v1·school_17_sheet_v2 만 지원")
    if not spec.get("source_refs"):
        raise ValueError("근거 참조 필요")
    if not isinstance(spec.get("source_refs"), list) or not all(
        isinstance(r, str) and r.strip() for r in spec["source_refs"]
    ):
        raise ValueError("근거 참조 목록 필요")
    # P4 reference-fill hook (stage-g005 D09): refs claiming audited
    # statistical provenance (pack@rev#record / audit_key) must resolve
    # through the provenance chain — fabricated refs fail closed.
    stat_result = resolve_statistical_source_refs(
        spec["source_refs"], resolver=resolver)
    if stat_result["conflicts"]:
        raise ValueError(
            "통계 근거 참조 해석 실패: "
            + "; ".join(
                "%s (%s)" % (c["ref"], c["reason"])
                for c in stat_result["conflicts"]
            )
        )
    if spec.get("unit") != "천원" or spec.get("quantity_unit") != "kg":
        raise ValueError("학교 제출 엑셀 단위는 천원, 수량은 kg")

    if "inflation" in spec:
        raise ValueError(
            "'inflation' 단일 배율 입력은 폐기되었습니다. "
            "price_assumptions.general(일반물가)·wage(임금)·sales(판매가)에 "
            "상승률 r과 출처 id·기준연도·관측기간·적용 기초연도를 입력하세요"
        )

    # F14: strict nonblank string validation for production evidence
    evidence = spec.get("production_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("생산량 산출근거 딕셔너리 필요")
    for k in EVIDENCE_KEYS:
        val = evidence.get(k)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"생산량 산출근거 문자열 필요: {k}")

    fields = (
        "area_m2",
        "purchase_price",
        "direct_price",
        "purchase_share",
        "direct_share",
        "land",
        "facility",
        "equipment",
        "equity",
        "loan",
        "loan_rate",
        "salvage",
        "materials",
        "labor",
        "packing",
        "transport",
        "household",
        "repair_facility_rate",
        "repair_equipment_rate",
        "utility_per_10a",
    )
    values = {k: num(spec.get(k, "0") if k in ("purchase_price", "direct_price") else spec[k]) for k in fields}

    # 생산량 입력은 판마다 다르다: v1은 총량 한 개, v2는 연도별 10a당
    # 목표이고 총량은 9행이 계산한다. 둘을 한 필드로 뭉개면 v2가 총량을
    # 입력받은 것처럼 보인다.
    if profile == "school_17_sheet_v2":
        values["production_yield"] = production_yield_rows(spec)
        if "production_kg" in spec:
            raise ValueError("새 양식은 총 생산량 대신 연도별 10a당 목표를 받습니다")
    else:
        values["production_kg"] = num(spec["production_kg"])
        if values["production_kg"] < 0:
            raise ValueError("생산량은 0 이상 필요")

    # F6: Domain constraints
    if values["area_m2"] <= 0:
        raise ValueError("재배면적은 양수 필요")
    if values["purchase_price"] < 0 or values["direct_price"] < 0:
        raise ValueError("단가는 0 이상 필요")
    if values["purchase_share"] < 0 or values["purchase_share"] > 1:
        raise ValueError("수매 판매비율은 0과 1 사이 필요")
    if values["direct_share"] < 0 or values["direct_share"] > 1:
        raise ValueError("직거래 판매비율은 0과 1 사이 필요")
    if values["purchase_share"] + values["direct_share"] > 1:
        raise ValueError("판매비율 합계 100% 초과")

    for k in (
        "land",
        "facility",
        "equipment",
        "equity",
        "loan",
        "salvage",
        "materials",
        "labor",
        "packing",
        "transport",
        "household",
        "loan_rate",
        "repair_facility_rate",
        "repair_equipment_rate",
        "utility_per_10a",
    ):
        if values[k] < 0:
            raise ValueError(f"{k} 항목은 0 이상 필요")

    # F4: salvage bound
    if values["salvage"] > values["facility"]:
        raise ValueError("잔존가액은 시설가액 이하 필요")

    # F5: Loan funding allocation bound
    total_capex = values["facility"] + values["equipment"] + values["land"]
    if values["loan"] > total_capex:
        raise ValueError("융자금은 총 투자액(시설+대농기구+토지) 이하 필요")
    if values["equity"] + values["loan"] < total_capex:
        raise ValueError("자부담+융자금은 총 투자액 이상 필요")

    # Price history validation if supplied
    has_pur_hist = (
        "purchase_price_history" in spec and spec["purchase_price_history"] is not None
    )
    has_dir_hist = (
        "direct_price_history" in spec and spec["direct_price_history"] is not None
    )
    if has_pur_hist or has_dir_hist:
        hist_years = spec.get("price_history_years")
        if not isinstance(hist_years, (list, tuple)) or len(hist_years) != 5:
            raise ValueError(
                "가격 이력이 있는 경우 price_history_years는 5개 연도 목록 필요"
            )
        for y in hist_years:
            if type(y) is not int or isinstance(y, bool):
                raise ValueError("price_history_years는 정수 연도 필요")
        for i in range(4):
            if hist_years[i] >= hist_years[i + 1]:
                raise ValueError(
                    "price_history_years는 엄격히 오름차순 정렬된 5개 연도 필요"
                )

    for hist_key in ("purchase_price_history", "direct_price_history"):
        hist = spec.get(hist_key)
        if hist is not None:
            if not isinstance(hist, (list, tuple)) or len(hist) != 5:
                raise ValueError(f"{hist_key}는 5개 연도 관측치 목록 필요")
            for p in hist:
                if num(str(p)) < 0:
                    raise ValueError(f"{hist_key} 가격은 0 이상 필요")
    years = plan_years(spec.get("start_year"))
    for key in ("grace", "term", "life"):
        if (
            type(spec.get(key)) is not int
            or isinstance(spec.get(key), bool)
            or spec[key] < 0
        ):
            raise ValueError("거치·상환·내용연수 0 이상의 정수 필요")
    if spec["life"] <= 0:
        raise ValueError("내용연수는 1 이상의 정수 필요")
    if values["loan"] > 0 and spec["term"] <= 0:
        raise ValueError("융자금이 있는 경우 상환기간은 1 이상의 정수 필요")

    active = [k for k in ("purchase", "direct") if values[k + "_share"] > 0]
    # Only these school rows sell 상품. Bind an omitted target grade to
    # that output condition, never fill an unknown source grade. The
    # shared normalizer then requires the source to prove the condition.
    block = spec.get("price_assumptions")
    raw_sales = block.get("sales") if isinstance(block, dict) else None
    price_spec = spec
    if isinstance(raw_sales, dict):
        def row_target(raw):
            if not isinstance(raw, dict):
                return raw
            target = canonical_dimensions(raw.get("target_dimensions"))
            target.setdefault("grade", "상품")
            return {**raw, "target_dimensions": target}

        if isinstance(raw_sales.get("anchors"), dict):
            bound = {**raw_sales, "anchors": {
                key: row_target(raw) if key in active else raw
                for key, raw in raw_sales["anchors"].items()}}
        elif "anchors" not in raw_sales and len(active) == 1:
            bound = row_target(raw_sales)
        else:
            bound = raw_sales
        price_spec = {**spec, "price_assumptions": {**block, "sales": bound}}
    price_assumptions = normalize_price_assumptions(price_spec, years)
    sale = price_assumptions["sales"]
    anchors = sale.get("anchors", {})
    if not anchors and len(active) == 1:
        anchors = {active[0]: dict(sale)}
    if set(anchors) - {"purchase", "direct"}:
        raise ValueError("school sales anchors는 purchase/direct만 허용")
    for key in ("purchase", "direct"):
        if key not in active:
            values[key + "_price"] = Decimal(0)
            continue
        if key not in anchors:
            raise ValueError(f"활성 {key} 채널의 가격 출처/앵커 필요")
        a = anchors[key]
        # The school row fixes its channel. Input target/source agreement
        # cannot turn O7 into direct sales or O10 into purchase sales.
        channel = {"purchase": "수매", "direct": "직거래"}[key]
        try:
            check_dimensions(a, {"channel": channel}, required=("channel",),
                             origin=f"school {key}")
        except ValueError as exc:
            raise ValueError(f"school {key} 채널은 {channel}로 확정 — 출처/target 채널 불일치: {exc}") from exc
        check_dimensions(a, {"grade": "상품"}, required=("grade",),
                         origin=f"school {key} 상품 행")
        if a["unit"] != "원/kg":
            raise ValueError("school 판매가는 원/kg 필요")
        mean = Decimal(str(a["base_price"]))
        if key + "_price" in spec and abs(values[key + "_price"] - mean) > Decimal("1e-12"):
            raise ValueError(f"{key}_price와 검증된 5년 평균 불일치")
        hist = spec.get(key + "_price_history")
        if hist is not None and (spec.get("price_history_years") != list(range(spec["writing_year"]-5, spec["writing_year"]))
                or [float(p) for p in hist] != [p["value"] for p in a["price_observations"]]):
            raise ValueError(f"{key}_price_history가 공식 5년 관측과 불일치")
        values[key + "_price"] = mean
    # Only effective channels reach either consumer. Build a fresh parent
    # so a supported flat single-channel input never refers to itself.
    effective = {key: anchors[key] for key in active}
    if effective:
        price_assumptions["sales"] = {**next(iter(effective.values())), "anchors": effective}

    return years, values, price_assumptions


def school_workbook(spec, path, *, context=None):
    """Guarded school workbook build: authorize before any output,
    reconfirm just before the file is saved (policy B).

    특용작물전공은 교수님 양식 복제 경로만 쓰므로 여기서 막는다
    (``require_template_route``). 생성 본체는 그대로 남아 있다."""
    import gg_major_contract as mc

    if not isinstance(spec, dict):
        raise mc.OutputHeldError("finance_profile_evidence_required")
    authorization = mc.authorize_output(
        mc.OUTPUT_SCHOOL_WORKBOOK, context, spec=spec)
    profile = spec.get("profile")
    mc.require_finance_profile(authorization, profile)
    if not profile_supports(profile):
        raise mc.OutputHeldError("unsupported_finance_profile")
    mc.require_template_route(authorization, profile)
    return _generate_school_workbook(spec, path, authorization, context,
                                     output_profile=profile)


def _generate_school_workbook(spec, path, authorization, context, *,
                              output_profile="school_17_sheet_v1"):
    """Legacy 17-sheet generator body; callers pass a policy-B
    authorization.  Kept unchanged for a later removal decision.

    ``output_profile`` picks what the body writes: v1 keeps the historical
    layout (총 생산량 입력, 7행 역산) and v2 writes the new form (연도별
    10a당 목표, 9행 계산).  The two differ only in those places."""
    import gg_major_contract as mc
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    unsupported = check_unsupported(spec)
    if unsupported:
        return unsupported
    profile = output_profile
    if not profile_supports(profile):
        raise ValueError("지원하지 않는 재무 판: %s" % (profile,))
    years, v, price = validate(spec)
    start = years[0]
    mult_g = [price_multiplier_d(price["general"], y) for y in years]
    mult_w = [price_multiplier_d(price["wage"], y) for y in years]
    mult_s = [price_multiplier_d(price["sales"], y) for y in years]

    def _step(a):
        return 1.0 + a["rate"] if a["status"] == "applied" else None

    step_g = _step(price["general"])
    step_w = _step(price["wage"])
    wb = Workbook()
    wb.active.title = SCHOOL_SHEETS[0]
    for name in SCHOOL_SHEETS[1:]:
        wb.create_sheet(name)
    (
        index,
        base,
        goal,
        invest,
        repay,
        sales,
        prod,
        mat,
        labor,
        exp,
        dep,
        cost,
        pl,
        bs,
        cf,
        inc,
        model,
    ) = (wb[name] for name in SCHOOL_SHEETS)
    model.sheet_state = "hidden"

    index["B2"] = "목록"
    index["B4"] = "동반제출 재무계획 시트 목록. 숨김 모델농장분석은 수식 연결하지 않음."
    for i, name in enumerate(SCHOOL_SHEETS, 1):
        index.cell(5 + i, 2, i)
        index.cell(5 + i, 3, name)

    # F1: Pre-investment opening basis (assets/loans are zero before Year 1 capex)
    base["B2"] = "1. 기초재무상태조사"
    base["I2"] = "(단위:천원)"
    base["B4"] = "토지"
    base["C4"] = 0
    base["B5"] = "시설"
    base["C5"] = 0
    base["B6"] = "대농기구"
    base["C6"] = 0
    base["B7"] = "자기자본"
    base["C7"] = float(v["equity"])
    base["B8"] = "융자"
    base["C8"] = 0
    base["B9"] = "기초현금"
    base["C9"] = "=C7+C8-C4-C5-C6"
    base["D4"] = "창업 전 기초재무상태. 신규 투자는 투자계획에 기록."

    prod["B2"] = "6. 생산계획"
    prod["B4"] = "가. 생산량계획"
    prod["B7"] = "10a당 생산량(kg)"
    prod["B8"] = "재배면적(㎡)"
    prod["B9"] = "생산량(kg)"
    if profile == "school_17_sheet_v2":
        # 교수님 양식: 학생이 해마다 10a당 목표를 적고 총량은 9행이 계산한다.
        # 면적은 해마다 같은 값을 쓰므로 네 열 모두 적는다(빈 칸이면 #VALUE!).
        for i, target in enumerate(v["production_yield"]):
            col = get_column_letter(8 + 3 * i)
            prod[f"{col}7"] = float(target)
            prod[f"{col}8"] = float(v["area_m2"])
            prod[f"{col}9"] = f"={col}7*{col}8/1000"
    else:
        prod["H8"] = float(v["area_m2"])
        prod["H9"] = float(v["production_kg"])
        prod["H7"] = "=H9/H8*1000"
    prod["B11"] = "나. 생산량 산출근거"
    evidence = spec["production_evidence"]
    prod["B15"] = "재배면적"
    prod["H15"] = evidence["area"]
    prod["B17"] = "묘목수"
    prod["H17"] = evidence["seedlings"]
    prod["B19"] = "생산량"
    prod["H19"] = evidence["yield"]
    prod["B23"] = "생산증가율"
    prod["H23"] = evidence["growth"]
    prod["B24"] = "상품화율"
    prod["H24"] = evidence["commodity"]
    prod["B25"] = "합계"
    for i, year in enumerate(years):
        prod.cell(25, 5 + i, "=H9")
        prod.cell(4, 5 + i, year if i == 0 else f"={get_column_letter(4 + i)}4+1")
    prod["E4"] = start

    sales_name = q(" 5. 판매계획")
    prod_name = q("6. 생산계획")
    dep_name = q("10. 감가상각비계획 ")
    invest_name = q("3.투자계획")
    repay_name = q("4. 원리금상환계획")
    cost_name = q("11. 생산원가계획")
    pl_name = q("12 손익계획")
    mat_name = q("7. 영농자재소요계획")
    labor_name = q("8. 노무비계획")
    exp_name = q("9 .경비계획")
    cf_name = q("14. 현금흐름계획")
    base_name = q("1. 기초재무상태조사")

    # F8: 5. 판매계획 - 가. 연도별 가격 추이 (5 dated observations & visible citations)
    sales["B2"] = "5. 판매계획"
    sales["B4"] = "가. 연도별 가격 추이"
    sales["I4"] = "(단위 : 원/kg)"
    sales["B6"] = "연도별"
    sales["C6"] = "등급"
    sales["O6"] = "평균가격"

    hist_cols = ["E", "G", "I", "K", "M"]
    hist_years = list(range(spec["writing_year"]-5, spec["writing_year"]))
    if hist_years and len(hist_years) == 5:
        for i, yr in enumerate(hist_years):
            sales[f"{hist_cols[i]}6"] = yr
    sales["B7"] = "평균가격(수매)"
    sales["C7"] = "상 품"
    pur_hist = [p["value"] for p in price["sales"]["anchors"]["purchase"]["price_observations"]] if "purchase" in price["sales"]["anchors"] and v["purchase_share"] > 0 else None
    if pur_hist and len(pur_hist) == 5:
        for i, val in enumerate(pur_hist):
            c_letter = hist_cols[i]
            sales[f"{c_letter}7"] = float(num(str(val)))
        sales["O7"] = "=AVERAGE(E7,G7,I7,K7,M7)"
    else:
        sales["O7"] = float(v["purchase_price"])
    sales["C8"] = "중 품"
    sales["O8"] = "[해당 없음: 상품만 판매]"
    sales["C9"] = "하 품"
    sales["O9"] = "[해당 없음: 상품만 판매]"

    sales["B10"] = "평균가격(직거래)"
    sales["C10"] = "상 품"
    dir_hist = [p["value"] for p in price["sales"]["anchors"]["direct"]["price_observations"]] if "direct" in price["sales"]["anchors"] and v["direct_share"] > 0 else None
    if dir_hist and len(dir_hist) == 5:
        for i, val in enumerate(dir_hist):
            c_letter = hist_cols[i]
            sales[f"{c_letter}10"] = float(num(str(val)))
        sales["O10"] = "=AVERAGE(E10,G10,I10,K10,M10)"
    else:
        sales["O10"] = float(v["direct_price"])
    sales["C11"] = "중 품"
    sales["O11"] = "[해당 없음: 상품만 판매]"
    sales["C12"] = "하 품"
    sales["O12"] = "[해당 없음: 상품만 판매]"
    sales["B14"] = f"출처: {', '.join(source_ref_labels(spec['source_refs']))}"

    # F9: 나. 유통채널별 판매계획 (5 year pairs across columns D..M)
    sales["B19"] = "나. 유통채널별 판매계획"
    sales["I19"] = "(단위 : %, kg)"
    sales["B21"] = "유통채널"
    sales["C21"] = "등급"
    sales["B22"] = "수매시장"
    sales["C22"] = "상품"
    sales["C23"] = "중품"
    sales["C24"] = "하품"
    sales["C25"] = "계"
    sales["B26"] = "직거래"
    sales["C26"] = "상품"
    sales["C27"] = "중품"
    sales["C28"] = "하품"
    sales["C29"] = "계"
    sales["B30"] = "미상품과"
    sales["B31"] = "합계"

    for t in range(5):
        col_pct = get_column_letter(4 + 2 * t)
        col_qty = get_column_letter(5 + 2 * t)
        prev_pct = get_column_letter(4 + 2 * (t - 1)) if t > 0 else None

        sales[f"{col_pct}20"] = f"{years[t]}년"
        sales[f"{col_pct}21"] = "판매비율"
        sales[f"{col_qty}21"] = "판매량"

        # 수매
        sales[f"{col_pct}22"] = (
            float(v["purchase_share"]) if t == 0 else f"={prev_pct}22"
        )
        sales[f"{col_qty}22"] = f"={col_pct}22*{prod_name}!$H$9"
        sales[f"{col_pct}23"] = 0
        sales[f"{col_qty}23"] = f"={col_pct}23*{prod_name}!$H$9"
        sales[f"{col_pct}24"] = 0
        sales[f"{col_qty}24"] = f"={col_pct}24*{prod_name}!$H$9"
        sales[f"{col_pct}25"] = f"=SUM({col_pct}22:{col_pct}24)"
        sales[f"{col_qty}25"] = f"=SUM({col_qty}22:{col_qty}24)"

        # 직거래
        sales[f"{col_pct}26"] = float(v["direct_share"]) if t == 0 else f"={prev_pct}26"
        sales[f"{col_qty}26"] = f"={col_pct}26*{prod_name}!$H$9"
        sales[f"{col_pct}27"] = 0
        sales[f"{col_qty}27"] = f"={col_pct}27*{prod_name}!$H$9"
        sales[f"{col_pct}28"] = 0
        sales[f"{col_qty}28"] = f"={col_pct}28*{prod_name}!$H$9"
        sales[f"{col_pct}29"] = f"=SUM({col_pct}26:{col_pct}28)"
        sales[f"{col_qty}29"] = f"=SUM({col_qty}26:{col_qty}28)"

        # 미상품과 & 합계
        sales[f"{col_pct}30"] = f"=1-{col_pct}25-{col_pct}29"
        sales[f"{col_qty}30"] = f"={col_pct}30*{prod_name}!$H$9"
        sales[f"{col_pct}31"] = f"={col_pct}25+{col_pct}29+{col_pct}30"
        sales[f"{col_qty}31"] = f"={col_qty}25+{col_qty}29+{col_qty}30"

        # Apply percent formatting on populated ratio rows 22:31
        for r_idx in range(22, 32):
            sales[f"{col_pct}{r_idx}"].number_format = "0%"

    # F9: 다. 연도별 매출액 추정 (5 years in columns D..H with genuine revenue formulas)
    sales["B38"] = "다. 연도별 매출액 추정"
    sales["C38"] = "판매량(kg)"
    sales["I38"] = "(단위 : 천원, Kg)"
    sales["B39"] = "수매(상품)"
    sales["B40"] = "직거래(상품)"
    sales["B41"] = "미상품과"
    sales["B50"] = "매출합계"
    # Year 1 quantity reference column C
    sales["C39"] = "=E22"
    sales["C40"] = "=E26"
    sales["C41"] = "=E30"
    sales["C50"] = "=C39+C40+C41"

    for t in range(5):
        rev_col = get_column_letter(4 + t)
        qty_col = get_column_letter(5 + 2 * t)
        from gg_excel_formula_patch import _excel_number
        suffix = "" if t == 0 else f"*(1+{_excel_number(price['sales']['rate'])})^{t}"
        sales[f"{rev_col}38"] = f"{years[t]}년"
        sales[f"{rev_col}39"] = f"={qty_col}22*$O$7/1000{suffix}"
        sales[f"{rev_col}40"] = f"={qty_col}26*$O$10/1000{suffix}"
        sales[f"{rev_col}41"] = 0
        sales[f"{rev_col}50"] = f"={rev_col}39+{rev_col}40+{rev_col}41"
    sales["B42"] = price_footnote(price["sales"])

    # F5 & F10: 3.투자계획 - 총괄 및 5개년 연차별 세부투자계획
    tot_loan = v["loan"]
    fac_loan = min(tot_loan, v["facility"])
    rem_loan = tot_loan - fac_loan
    eq_loan = min(rem_loan, v["equipment"])
    rem_loan2 = rem_loan - eq_loan
    land_loan = min(rem_loan2, v["land"])

    invest["B2"] = "3. 투자계획"
    invest["B4"] = "가. 총괄 투자계획"
    invest["I5"] = "(단위:천원)"
    invest["B6"] = "투자연도"
    invest["C6"] = "소요액"
    invest["D7"] = "자부담"
    invest["E7"] = "보조"
    invest["F7"] = "융자"
    invest["B8"] = start
    invest["C8"] = "=SUM(D8:F8)"
    invest["D8"] = "=F24"
    invest["E8"] = 0
    invest["F8"] = "=H24"
    for i in range(1, 5):
        r = 8 + i
        det_r = 24 + 6 * i
        invest.cell(r, 2, f"=B{r-1}+1")
        invest.cell(r, 3, f"=SUM(D{r}:F{r})")
        invest.cell(r, 4, f"=F{det_r}")
        invest.cell(r, 5, f"=G{det_r}")
        invest.cell(r, 6, f"=H{det_r}")
    invest["B13"] = "합    계"
    invest["C13"] = "=SUM(C8:C12)"
    invest["D13"] = "=SUM(D8:D12)"
    invest["E13"] = "=SUM(E8:E12)"
    invest["F13"] = "=SUM(F8:F12)"

    # 나. 연차별 세부투자계획 - 5개년 블록 (행 19~48)
    invest["B15"] = "나. 연차별 세부투자계획"
    invest["B17"] = "연도별"
    invest["C17"] = "투자대상"
    invest["E17"] = "투자금액(천원)"
    invest["C18"] = "투자항목"
    invest["D18"] = "규격"
    invest["E18"] = "계"
    invest["F18"] = "자부담"
    invest["G18"] = "보조"
    invest["H18"] = "융자"

    # Year 1 세부투자 (19..24)
    invest["B19"] = start
    invest["C19"] = "시설"
    invest["D19"] = spec.get("facility_spec") or (
        "[확인 필요]" if v["facility"] > 0 else "-"
    )
    invest["E19"] = "=SUM(F19:H19)"
    invest["F19"] = float(v["facility"] - fac_loan)
    invest["G19"] = 0
    invest["H19"] = float(fac_loan)

    invest["C20"] = "대농기구"
    invest["D20"] = spec.get("equipment_spec") or (
        "[확인 필요]" if v["equipment"] > 0 else "-"
    )
    invest["E20"] = "=SUM(F20:H20)"
    invest["F20"] = float(v["equipment"] - eq_loan)
    invest["G20"] = 0
    invest["H20"] = float(eq_loan)

    invest["C21"] = "토지"
    invest["D21"] = spec.get("land_spec") or ("[확인 필요]" if v["land"] > 0 else "-")
    invest["E21"] = "=SUM(F21:H21)"
    invest["F21"] = float(v["land"] - land_loan)
    invest["G21"] = 0
    invest["H21"] = float(land_loan)

    invest["C22"] = "기타1"
    invest["D22"] = "-"
    invest["E22"] = "=SUM(F22:H22)"
    invest["F22"] = 0
    invest["G22"] = 0
    invest["H22"] = 0

    invest["C23"] = "기타2"
    invest["D23"] = "-"
    invest["E23"] = "=SUM(F23:H23)"
    invest["F23"] = 0
    invest["G23"] = 0
    invest["H23"] = 0

    invest["C24"] = "계"
    invest["E24"] = "=SUM(E19:E23)"
    invest["F24"] = "=SUM(F19:F23)"
    invest["G24"] = "=SUM(G19:G23)"
    invest["H24"] = "=SUM(H19:H23)"

    # Years 2..5 세부투자 블록 (25..48)
    for i in range(1, 5):
        r = 25 + 6 * (i - 1)
        invest.cell(r, 2, f"=B{8 + i}")
        invest.cell(r, 3, "시설")
        invest.cell(r, 4, "-")
        invest.cell(r, 5, f"=SUM(F{r}:H{r})")
        invest.cell(r, 6, 0)
        invest.cell(r, 7, 0)
        invest.cell(r, 8, 0)

        invest.cell(r + 1, 3, "대농기구")
        invest.cell(r + 1, 4, "-")
        invest.cell(r + 1, 5, f"=SUM(F{r+1}:H{r+1})")
        invest.cell(r + 1, 6, 0)
        invest.cell(r + 1, 7, 0)
        invest.cell(r + 1, 8, 0)

        invest.cell(r + 2, 3, "토지")
        invest.cell(r + 2, 4, "-")
        invest.cell(r + 2, 5, f"=SUM(F{r+2}:H{r+2})")
        invest.cell(r + 2, 6, 0)
        invest.cell(r + 2, 7, 0)
        invest.cell(r + 2, 8, 0)

        invest.cell(r + 3, 3, "기타1")
        invest.cell(r + 3, 4, "-")
        invest.cell(r + 3, 5, f"=SUM(F{r+3}:H{r+3})")
        invest.cell(r + 3, 6, 0)
        invest.cell(r + 3, 7, 0)
        invest.cell(r + 3, 8, 0)

        invest.cell(r + 4, 3, "기타2")
        invest.cell(r + 4, 4, "-")
        invest.cell(r + 4, 5, f"=SUM(F{r+4}:H{r+4})")
        invest.cell(r + 4, 6, 0)
        invest.cell(r + 4, 7, 0)
        invest.cell(r + 4, 8, 0)

        invest.cell(r + 5, 3, "계")
        invest.cell(r + 5, 5, f"=SUM(E{r}:E{r+4})")
        invest.cell(r + 5, 6, f"=SUM(F{r}:F{r+4})")
        invest.cell(r + 5, 7, f"=SUM(G{r}:G{r+4})")
        invest.cell(r + 5, 8, f"=SUM(H{r}:H{r+4})")

    # F3 & F5: Bounded principal repayment and authoritative loan linkage
    term_val = spec["term"] if spec.get("term", 0) > 0 else 1
    repay["B2"] = "4. 원리금상환계획"
    repay["B6"] = "연도"
    repay["C6"] = "융자금액"
    repay["D7"] = "원금상환"
    repay["E7"] = "이자"
    repay["F6"] = "연말융자잔액"
    repay["B8"] = start
    repay["C8"] = f"={invest_name}!F8"
    repay["D8"] = (
        f'=IF(B8-$B$8+1<={spec["grace"]},0,IF($C$8=0,0,MIN(C8,$C$8/{term_val})))'
    )
    repay["E8"] = f"=C8*{float(v['loan_rate'])}"
    repay["F8"] = "=C8-D8"
    for i in range(1, 5):
        r = 8 + i
        repay.cell(r, 2, f"=B{r-1}+1")
        repay.cell(r, 3, 0)
        repay.cell(
            r,
            4,
            f'=IF(B{r}-$B$8+1<={spec["grace"]},0,IF($C$8=0,0,MIN(F{r-1},$C$8/{term_val})))',
        )
        repay.cell(r, 5, f"=F{r-1}*{float(v['loan_rate'])}")
        repay.cell(r, 6, f"=F{r-1}-D{r}")

    # F4 & F5 & F10: 10. 감가상각비계획 (annual basis columns and useful-life bounded charges)
    dep["B2"] = "10. 감가상각비계획"
    dep["B4"] = "취득가액"
    dep["D7"] = "영농시설"
    dep["E8"] = f"={invest_name}!E19"
    dep["H7"] = "대농기구"
    dep["I8"] = f"={invest_name}!E20"

    dep["B10"] = "연도별 상각"
    dep["B11"] = "연도"
    dep["C11"] = "영농시설 취득가"
    dep["D11"] = "내용연수"
    dep["E11"] = "영농시설 상각액"
    dep["G11"] = "대농기구 취득가"
    dep["H11"] = "내용연수"
    dep["I11"] = "대농기구 상각액"

    for t in range(5):
        r = 12 + t
        dep.cell(r, 2, start if t == 0 else f"=B{r-1}+1")
        dep.cell(r, 3, "=E8")
        dep.cell(r, 4, spec["life"])
        dep.cell(
            r,
            5,
            f"=IF({t + 1}<={spec['life']},IF({spec['life']}=0,0,(E8-{float(v['salvage'])})/{spec['life']}),0)",
        )
        dep.cell(r, 7, "=I8")
        dep.cell(r, 8, spec["life"])
        dep.cell(
            r,
            9,
            f"=IF({t + 1}<={spec['life']},IF({spec['life']}=0,0,I8/{spec['life']}),0)",
        )

    # 9 .경비계획
    exp["B2"] = "9. 경비계획"
    exp["B5"] = "대농기구 수선비"
    exp["D5"] = float(v["repair_equipment_rate"])
    exp["B6"] = "영농시설 수선비"
    exp["D6"] = float(v["repair_facility_rate"])
    exp["B7"] = "수도광열비 (천원/10a)"
    exp["D7"] = float(v["utility_per_10a"])
    exp["B8"] = "경영면적(m2) > 1년차"
    exp["D8"] = f"={prod_name}!$H$8"
    exp["B9"] = "경영면적(m2) > 2년차 이후"
    exp["D9"] = "=D8"
    exp["B10"] = "임대면적(m2)"
    exp["D10"] = 0
    for t, year in enumerate(years):
        b = 12 + 20 * t
        exp.cell(b, 2, f"{YEAR_LABELS[t]}. {year}년")
        exp.cell(b + 1, 5, "(단위 : 천원)")
        exp.cell(b + 2, 2, "과     목")
        exp.cell(b + 2, 3, "금  액")
        exp.cell(b + 2, 4, "산   출   근   거")
        exp.cell(b + 2, 5, "비고")
        exp.cell(b + 3, 2, "1. 수선비")
        exp.cell(b + 3, 3, f"=SUM(C{b+4}:C{b+5})")
        exp.cell(b + 3, 4, "하위 합계")
        exp.cell(b + 4, 2, "  가. 영농시설")
        if t == 0:
            exp.cell(b + 4, 3, f"={dep_name}!E8*$D$6")
        else:
            exp.cell(b + 4, 3, f"=C{b + 4 - 20}")
        exp.cell(b + 4, 4, "* 구입금액의 시설수선비율 적용")
        exp.cell(b + 5, 2, "  나. 대농기구")
        if t == 0:
            exp.cell(b + 5, 3, f"={dep_name}!I8*$D$5")
        else:
            exp.cell(b + 5, 3, f"=C{b + 5 - 20}")
        exp.cell(b + 5, 4, "* 구입금액의 대농기구수선비율 적용")
        exp.cell(b + 6, 2, "2. 수도 광열비")
        if t == 0:
            mg0 = float(mult_g[0])
            exp.cell(
                b + 6,
                3,
                f"=$D$7*$D$8/1000{'' if mg0 == 1.0 else f'*{mg0}'}",
            )
            exp.cell(b + 6, 4, "수도광열비×면적/1000×일반물가배율")
        else:
            exp.cell(
                b + 6,
                3,
                (
                    f"=C{b + 6 - 20}*{step_g}"
                    if step_g is not None
                    else f"=C{b + 6 - 20}"
                ),
            )
            exp.cell(
                b + 6,
                4,
                "전년 수도광열비×(1+일반물가)"
                if step_g is not None
                else "전년과 동일(일반물가 미적용·명시 선택)",
            )
        exp.cell(b + 8, 2, "3. 감가상각비")
        exp.cell(b + 8, 3, f"=SUM(C{b+9}:C{b+11})")
        exp.cell(b + 8, 4, "* 감가상각비계획 참조")
        exp.cell(b + 9, 2, "  가. 영농시설")
        exp.cell(b + 9, 3, f"={dep_name}!E{12 + t}")
        exp.cell(b + 9, 4, "정액상각")
        exp.cell(b + 10, 2, "  나. 대농기구")
        exp.cell(b + 10, 3, f"={dep_name}!I{12 + t}")
        exp.cell(b + 10, 4, "정액상각")
        exp.cell(b + 11, 2, "  다. 생물자산")
        exp.cell(b + 11, 3, 0)
        exp.cell(b + 11, 4, "해당 없으면 0")
        exp.cell(b + 12, 2, "4. 임차료")
        # F11: Rental parent SUM of subitems
        exp.cell(b + 12, 3, f"=SUM(C{b+13}:C{b+14})")
        exp.cell(b + 12, 4, "하위 합계")
        exp.cell(b + 13, 2, "  가. 대농기구")
        exp.cell(b + 13, 3, 0)
        exp.cell(b + 13, 4, "해당 없으면 0")
        exp.cell(b + 14, 2, "  나. 토지")
        exp.cell(b + 14, 3, 0)
        exp.cell(b + 14, 4, "해당 없으면 0")
        exp.cell(b + 15, 2, "5. 위탁영농비")
        exp.cell(b + 15, 3, 0)
        exp.cell(b + 15, 4, "해당 없으면 0")
        exp.cell(b + 16, 2, "6. 소농구비")
        exp.cell(b + 16, 3, 0)
        exp.cell(b + 16, 4, "해당 없으면 0")
        exp.cell(b + 17, 2, "7. 기타 요금")
        exp.cell(b + 17, 3, 0)
        exp.cell(b + 17, 4, "해당 없으면 0")
        exp.cell(b + 18, 2, "합    계")
        exp.cell(
            b + 18,
            3,
            f"=C{b+3}+C{b+6}+C{b+8}+C{b+12}+C{b+15}+C{b+16}+C{b+17}",
        )
        exp.cell(b + 18, 4, "7과목 합계. 해당 없어도 0으로 남김")
        if t == 0:
            exp.cell(b + 19, 2, price_footnote(price["general"]))

    # F10: 7. 영농자재소요계획 & 8. 노무비계획 (basis text & rate roll-forward)
    # 일반물가(r_g)는 자재, 임금(r_w)은 노무비 — 같은 가격에 두 률을
    # 함께 곱하지 않는다(P5 이중 적용 금지).
    mat["B2"] = "7. 영농자재소요계획"
    mat["I2"] = "(단위:천원)"
    labor["B2"] = "8. 노무비계획"
    labor["I2"] = "(단위:천원)"
    for t, year in enumerate(years):
        r = 4 + t
        if t == 0:
            mat.cell(r, 2, year)
            mat.cell(r, 3, float(v["materials"] * mult_g[0]))
            mat.cell(
                r,
                4,
                spec.get("materials_basis")
                or ("[확인 필요]" if v["materials"] > 0 else "-"),
            )
            labor.cell(r, 2, year)
            labor.cell(r, 3, float(v["labor"] * mult_w[0]))
            labor.cell(
                r,
                4,
                spec.get("labor_basis") or ("[확인 필요]" if v["labor"] > 0 else "-"),
            )
        else:
            mat.cell(r, 2, f"=B{r-1}+1")
            mat.cell(
                r,
                3,
                f"=C{r-1}*{step_g}" if step_g is not None else f"=C{r-1}",
            )
            mat.cell(
                r,
                4,
                "전년 자재비 × (1+일반물가)"
                if step_g is not None
                else "전년과 동일(일반물가 미적용·명시 선택)",
            )
            labor.cell(r, 2, f"=B{r-1}+1")
            labor.cell(
                r,
                3,
                f"=C{r-1}*{step_w}" if step_w is not None else f"=C{r-1}",
            )
            labor.cell(
                r,
                4,
                "전년 노무비 × (1+임금상승률)"
                if step_w is not None
                else "전년과 동일(임금상승률 미적용·명시 선택)",
            )
    mat["B10"] = "합계"
    mat["C10"] = "=SUM(C4:C8)"
    mat["B12"] = price_footnote(price["general"])
    labor["B10"] = "합계"
    labor["C10"] = "=SUM(C4:C8)"
    labor["B12"] = price_footnote(price["wage"])

    cost["B2"] = "11. 생산원가계획"
    cost["I2"] = "(단위:천원)"
    cost["B4"] = "과목"
    for i, year in enumerate(years):
        cost.cell(4, 3 + i, year if i == 0 else f"={get_column_letter(2 + i)}4+1")
    cost["C4"] = start
    cost["B6"] = "자재비"
    cost["B7"] = "노무비"
    cost["B8"] = "경비"
    cost["B9"] = "감가상각비"
    cost["B31"] = "당기총생산원가"
    cost["B32"] = "감가상각비"
    cost["B33"] = "현금생산원가"
    cost["B35"] = "매출원가"
    for i in range(5):
        col = get_column_letter(3 + i)
        b = 12 + 20 * i
        cost.cell(6, 3 + i, f"={mat_name}!C{4 + i}")
        cost.cell(7, 3 + i, f"={labor_name}!C{4 + i}")
        cost.cell(8, 3 + i, f"={exp_name}!C{b + 18}-{exp_name}!C{b + 8}")
        cost.cell(9, 3 + i, f"={exp_name}!C{b + 8}")
        cost.cell(31, 3 + i, f"={col}6+{col}7+{col}8+{col}9")
        cost.cell(32, 3 + i, f"={col}9")
        cost.cell(33, 3 + i, f"={col}31-{col}32")
        cost.cell(35, 3 + i, f"={col}31")

    pl["B2"] = "12 손익계획"
    pl["B4"] = "과목"
    pl["C4"] = start
    for i in range(1, 5):
        pl.cell(4, 3 + i, f"={get_column_letter(2 + i)}4+1")
        pl.cell(5, 3 + i, "1월1일~12월31일")
    pl["C5"] = "1월1일~12월31일"
    rows = {
        6: ("I. 매출액", "=SUM(C7:C8)"),
        7: ("  1. 생산물 매출", f"={sales_name}!D50"),
        8: ("  2. 부산물 매출", 0),
        9: ("II. 매출원가", f"={cost_name}!C35"),
        10: ("III. 매출총이익", "=C6-C9"),
        11: ("IV. 판매비와 관리비", "=SUM(C12:C14)"),
        12: ("  1. 포장비", float(v["packing"])),
        13: ("  2. 운반비", float(v["transport"])),
        14: ("  3. 기타", 0),
        15: ("V. 영업이익", "=C10-C11"),
        16: ("VI. 영업외 수익", "=SUM(C17:C19)"),
        17: ("  1. 토지/농기계임대료", 0),
        18: ("  2. 보조금", 0),
        19: ("  3. 배당금", 0),
        20: ("VII. 영업외 비용", "=SUM(C21:C22)"),
        21: ("  1. 이자비용", f"={repay_name}!E8"),
        22: ("  2. 기타", 0),
        23: ("VIII. 세금차감전 순익", "=C15+C16-C20"),
        25: ("IX. 세금(법인세 등)", 0),
        26: ("X. 당기순이익", "=C23-C25"),
    }
    for r, (label, formula) in rows.items():
        pl.cell(r, 2, label)
        pl.cell(r, 3, formula)
    for i in range(1, 5):
        col = get_column_letter(3 + i)
        prev = get_column_letter(2 + i)
        pl.cell(7, 3 + i, f"={sales_name}!{get_column_letter(4 + i)}50")
        pl.cell(8, 3 + i, 0)
        pl.cell(6, 3 + i, f"=SUM({col}7:{col}8)")
        pl.cell(9, 3 + i, f"={cost_name}!{col}35")
        pl.cell(10, 3 + i, f"={col}6-{col}9")
        pl.cell(12, 3 + i, float(v["packing"]))
        pl.cell(13, 3 + i, float(v["transport"]))
        pl.cell(14, 3 + i, 0)
        pl.cell(11, 3 + i, f"=SUM({col}12:{col}14)")
        pl.cell(15, 3 + i, f"={col}10-{col}11")
        pl.cell(17, 3 + i, 0)
        pl.cell(18, 3 + i, 0)
        pl.cell(19, 3 + i, 0)
        pl.cell(16, 3 + i, f"=SUM({col}17:{col}19)")
        pl.cell(21, 3 + i, f"={repay_name}!E{8 + i}")
        pl.cell(22, 3 + i, 0)
        pl.cell(20, 3 + i, f"=SUM({col}21:{col}22)")
        pl.cell(23, 3 + i, f"={col}15+{col}16-{col}20")
        pl.cell(25, 3 + i, 0)
        pl.cell(26, 3 + i, f"={col}23-{col}25")

    goal["B2"] = "2. 중장기영농목표"
    goal["B4"] = f"가. 작성기간 : 5개년 계획({years[0]}~{years[4]})"
    goal["B7"] = "나. 매출액 및 순이익 목표"
    goal["B9"] = "구 분"
    goal["C9"] = start
    for i in range(1, 5):
        goal.cell(9, 3 + i, f"={get_column_letter(2 + i)}9+1")
    goal["B10"] = "매출액"
    goal["B11"] = "순이익"
    goal["B12"] = "순이익률(%)"
    for i in range(5):
        col = get_column_letter(3 + i)
        goal.cell(10, 3 + i, f"={pl_name}!{col}6")
        goal.cell(11, 3 + i, f"={pl_name}!{col}26")
        goal.cell(12, 3 + i, f"=IF({col}10=0,NA(),{col}11/{col}10)")
        goal.cell(12, 3 + i).number_format = "0.0%"
    # F1 & F5: Coherent cash flow schedule with land cash purchase
    cf["B2"] = "14. 현금흐름계획"
    cf["B6"] = "조달"
    cf["C7"] = "농산물 판매대금"
    cf["D7"] = f"={pl_name}!C6"
    cf["C8"] = "부산물 매출"
    cf["D8"] = 0
    cf["C9"] = "보조금"
    cf["D9"] = 0
    cf["C10"] = "영농착수금"
    cf["D10"] = 0
    cf["C11"] = "자산매각대금"
    cf["D11"] = 0
    cf["C12"] = "융자금"
    cf["D12"] = f"={invest_name}!F8"
    cf["C13"] = "기타 수입"
    cf["D13"] = 0
    cf["C14"] = "소   계(A)"
    cf["D14"] = "=SUM(D7:D13)"
    cf["C15"] = "시설투자"
    cf["D15"] = f"={invest_name}!E19"
    cf["C16"] = "농기계투자"
    cf["D16"] = f"={invest_name}!E20"
    cf["C17"] = "토지구입"
    cf["D17"] = f"={invest_name}!E21"
    cf["C18"] = "당기총생산 원가"
    cf["D18"] = f"={cost_name}!C33"
    cf["C19"] = "판매 및 일반관리비"
    cf["D19"] = f"={pl_name}!C11"
    cf["C20"] = "영업외 비용"
    cf["D20"] = f"={pl_name}!C20"
    cf["C21"] = "융자원금상환"
    cf["D21"] = f"={repay_name}!D8"
    cf["C22"] = "소   계(B)"
    cf["D22"] = "=SUM(D15:D21)"
    cf["C23"] = "수지균형(C)"
    cf["D23"] = "=D14-D22"
    cf["C24"] = "누적수지균형(D)"
    cf["D24"] = "=D23"
    cf["E24"] = "=D24"
    cf["B25"] = "* 당기 총생산원가 중 감가상각비는 제외. 비현금 비용이기 때문."
    for i in range(1, 5):
        col = get_column_letter(4 + i)
        prev = get_column_letter(3 + i)
        cf.cell(7, 4 + i, f"={pl_name}!{get_column_letter(3 + i)}6")
        cf.cell(8, 4 + i, 0)
        cf.cell(9, 4 + i, 0)
        cf.cell(10, 4 + i, 0)
        cf.cell(11, 4 + i, 0)
        cf.cell(12, 4 + i, 0)
        cf.cell(13, 4 + i, 0)
        cf.cell(14, 4 + i, f"=SUM({col}7:{col}13)")
        cf.cell(15, 4 + i, 0)
        cf.cell(16, 4 + i, 0)
        cf.cell(17, 4 + i, 0)
        cf.cell(18, 4 + i, f"={cost_name}!{get_column_letter(3 + i)}33")
        cf.cell(19, 4 + i, f"={pl_name}!{get_column_letter(3 + i)}11")
        cf.cell(20, 4 + i, f"={pl_name}!{get_column_letter(3 + i)}20")
        cf.cell(21, 4 + i, f"={repay_name}!D{8 + i}")
        cf.cell(22, 4 + i, f"=SUM({col}15:{col}21)")
        cf.cell(23, 4 + i, f"={col}14-{col}22")
        cf.cell(24, 4 + i, f"={prev}24+{col}23")

    # F1 & F2 & F5: Balance Sheet with cumulative depreciation and authoritative references
    bs["B2"] = "13. 추정대차대조표"
    bs["I2"] = "(단위:천원)"
    bs["B4"] = "자산"
    bs["C4"] = start
    for i in range(1, 5):
        bs.cell(4, 3 + i, f"={get_column_letter(2 + i)}4+1")
    bs["B6"] = "현금"
    bs["C6"] = f"={base_name}!C9+{cf_name}!D23"
    bs["B7"] = "토지"
    bs["C7"] = f"={invest_name}!E21"
    bs["B8"] = "시설장부가"
    bs["C8"] = f"={dep_name}!E8-{dep_name}!E12"
    bs["B9"] = "대농기구장부가"
    bs["C9"] = f"={dep_name}!I8-{dep_name}!I12"
    bs["B10"] = "자산합계"
    bs["C10"] = "=C6+C7+C8+C9"
    bs["B12"] = "융자잔액"
    bs["C12"] = f"={repay_name}!F8"
    bs["B13"] = "자본"
    bs["C13"] = f"={base_name}!C7+{pl_name}!C26"
    bs["B14"] = "부채와자본"
    bs["C14"] = "=C12+C13"
    bs["B16"] = "자산=부채+자본"
    bs["C16"] = "=C10-C14"
    for i in range(1, 5):
        col = get_column_letter(3 + i)
        prev = get_column_letter(2 + i)
        bs.cell(6, 3 + i, f"={prev}6+{cf_name}!{get_column_letter(4 + i)}23")
        bs.cell(7, 3 + i, f"={prev}7")
        bs.cell(8, 3 + i, f"={prev}8-{dep_name}!E{12 + i}")
        bs.cell(9, 3 + i, f"={prev}9-{dep_name}!I{12 + i}")
        bs.cell(10, 3 + i, f"={col}6+{col}7+{col}8+{col}9")
        bs.cell(12, 3 + i, f"={repay_name}!F{8 + i}")
        bs.cell(13, 3 + i, f"={prev}13+{pl_name}!{col}26")
        bs.cell(14, 3 + i, f"={col}12+{col}13")
        bs.cell(16, 3 + i, f"={col}10-{col}14")

    inc["B2"] = "15.추정소득분석"
    inc["I2"] = "(단위:천원)"
    inc["B4"] = "매출액"
    inc["B5"] = "생산원가"
    inc["B6"] = "당기순이익"
    inc["B7"] = "가계비"
    inc["B8"] = "추정소득"
    inc["C4"] = f"={pl_name}!C6"
    inc["C5"] = f"={cost_name}!C35"
    inc["C6"] = f"={pl_name}!C26"
    inc["C7"] = float(v["household"])
    inc["C8"] = "=C6-C7"
    for i in range(1, 5):
        col = get_column_letter(3 + i)
        inc.cell(4, 3 + i, f"={pl_name}!{col}6")
        inc.cell(5, 3 + i, f"={cost_name}!{col}35")
        inc.cell(6, 3 + i, f"={pl_name}!{col}26")
        inc.cell(7, 3 + i, float(v["household"]))
        inc.cell(8, 3 + i, f"={col}6-{col}7")

    model["B2"] = "참고1. 모델농장분석"
    model["B4"] = "정성 비교 자료. 다른 시트가 이 시트를 수식으로 참조하지 않음."

    for sheet in wb:
        sheet.freeze_panes = "B2"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = 18
        for row in sheet:
            for cell in row:
                cell.font = Font(size=11)
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    # Column widths tailored for content and evidence readability
    exp.column_dimensions["B"].width = 24
    exp.column_dimensions["C"].width = 16
    exp.column_dimensions["D"].width = 38
    exp.column_dimensions["E"].width = 14

    sales.column_dimensions["B"].width = 22
    sales.column_dimensions["C"].width = 12
    for c_letter in ("D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N"):
        sales.column_dimensions[c_letter].width = 13
    sales.column_dimensions["O"].width = 16

    invest.column_dimensions["B"].width = 14
    invest.column_dimensions["C"].width = 16
    invest.column_dimensions["D"].width = 28
    for c_letter in ("E", "F", "G", "H"):
        invest.column_dimensions[c_letter].width = 14

    prod.column_dimensions["B"].width = 22
    prod.column_dimensions["H"].width = 40
    index.column_dimensions["B"].width = 10
    index.column_dimensions["C"].width = 30
    index.column_dimensions["D"].width = 60
    index.merge_cells("B4:D4")

    # Explicit used-range print areas and pagination configurations
    index.print_area = "B2:D22"
    base.print_area = "B2:D10"
    goal.print_area = "B2:G13"

    # 3.투자계획: discontiguous print areas (총괄+1년차, 2~5년차).
    # 연속 인쇄 페이지에서도 열 머리행이 반복되도록 제목 행을 지정한다.
    invest.print_area = "B2:H24,B25:H48"
    invest.print_title_rows = "17:18"

    repay.print_area = "B2:G13"

    # 5. 판매계획: discontiguous print areas (가.가격추이+나.유통채널 [B2:O31] and 다.매출추정 [B38:O50])
    sales.print_area = "B2:O31,B38:O50"

    prod.print_area = "B2:I26"
    mat.print_area = "B2:E12"
    labor.print_area = "B2:E12"

    # 9 .경비계획: discontiguous print areas per summary and annual blocks
    exp.print_area = "B2:E10,B12:E31,B32:E50,B52:E70,B72:E90,B92:E110"

    dep.print_area = "B2:I17"
    cost.print_area = "B2:G36"  # fitToHeight=1 avoids orphan row on page 19
    pl.print_area = "B2:G27"  # fitToHeight=1 avoids orphan row on page 21
    bs.print_area = "B2:G17"
    cf.print_area = "B2:I25" if profile == "school_17_sheet_v2" else "B2:H26"
    inc.print_area = "B2:G9"
    path = Path(path)
    if path.exists():
        raise ValueError("기존 XLSX 덮어쓰기 금지")
    mc.reconfirm_output(authorization, context)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    path.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "profile": profile,
                "writing_year": spec["writing_year"],
                "years": years,
                "file_hash": digest(path.read_bytes()),
                "input_hash": digest(spec),
                "price_assumptions": {role: a for role, a in price.items()
                },
                "recalculation": "not_run",
                "rendering": "not_run",
                "major_authorization": authorization.to_dict(),
                "notice": "학교 17시트 구조·수식 생성. 재계산·인쇄·본문 교차는 별도 검사.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "generated",
        "profile": profile,
        "years": years,
        "sheets": list(SCHOOL_SHEETS),
        "recalculation": "not_run",
    }


def inspect_school_workbook(path):
    from openpyxl import load_workbook

    issues = []
    wb = load_workbook(path, data_only=False)
    if tuple(wb.sheetnames) != SCHOOL_SHEETS:
        issues.append(
            {
                "status": "fail",
                "reason": "17시트 이름·순서 불일치",
                "observed": wb.sheetnames,
            }
        )
        return issues
    if wb["참고1. 모델농장분석"].sheet_state != "hidden":
        issues.append({"status": "fail", "reason": "모델농장분석 숨김 아님"})
    exp = wb["9 .경비계획"]
    if exp["D16"].value in (None, "") or exp["D17"].value in (None, ""):
        issues.append({"status": "fail", "reason": "경비 산출근거 열 비어 있음"})
    labels = [exp.cell(12 + i, 2).value for i in (3, 6, 8, 12, 15, 16, 17)]
    if not all(labels):
        issues.append({"status": "fail", "reason": "경비 7과목 라벨 누락"})
    prod = wb["6. 생산계획"]
    for addr in ("H15", "H17", "H19", "H23", "H24"):
        if not prod[addr].value:
            issues.append(
                {"status": "fail", "reason": "생산량 산출근거 문장 누락: " + addr}
            )
    invest = wb["3.투자계획"]
    if not str(invest["B9"].value or "").startswith("="):
        issues.append({"status": "fail", "reason": "투자연도 수식 증가 아님"})
    if invest["D19"].value in (None, "", "입력 규격", "[확인 필요]") and invest[
        "E19"
    ].value not in (0, "0", None, 0.0):
        issues.append({"status": "fail", "reason": "시설 투자 규격 누락"})
    if invest["D20"].value in (None, "", "입력 규격", "[확인 필요]") and invest[
        "E20"
    ].value not in (0, "0", None, 0.0):
        issues.append({"status": "fail", "reason": "대농기구 투자 규격 누락"})
    if invest["D21"].value in (None, "", "입력 규격", "[확인 필요]") and invest[
        "E21"
    ].value not in (0, "0", None, 0.0):
        issues.append({"status": "fail", "reason": "토지 투자 규격 누락"})
    mat = wb["7. 영농자재소요계획"]
    if mat["D4"].value in (None, "", "[확인 필요]") and mat["C4"].value not in (
        0,
        "0",
        None,
        0.0,
    ):
        issues.append({"status": "fail", "reason": "영농자재 산출근거 누락"})
    labor = wb["8. 노무비계획"]
    if labor["D4"].value in (None, "", "[확인 필요]") and labor["C4"].value not in (
        0,
        "0",
        None,
        0.0,
    ):
        issues.append({"status": "fail", "reason": "노무비 산출근거 누락"})
    repay = wb["4. 원리금상환계획"]
    if str(repay["F9"].value or "").replace(" ", "") != "=F8-D9":
        issues.append(
            {"status": "fail", "reason": "상환잔액이 전년 잔액-당해 원금이 아님"}
        )
    sales = wb[" 5. 판매계획"]
    if not str(sales["B14"].value or "").startswith("출처:"):
        issues.append({"status": "fail", "reason": "판매계획 출처 각주 누락"})
    pl = wb["12 손익계획"]
    if " 5. 판매계획" not in str(pl["C7"].value or ""):
        issues.append(
            {"status": "fail", "reason": "손익 매출이 판매계획을 참조하지 않음"}
        )
    goal = wb["2. 중장기영농목표"]
    if "12 손익계획" not in str(goal["C10"].value or ""):
        issues.append(
            {"status": "fail", "reason": "중장기목표가 손익계획을 참조하지 않음"}
        )
    if "NA()" not in str(goal["C12"].value or ""):
        issues.append({"status": "fail", "reason": "매출 0 순이익률 NA 미처리"})
    cf = wb["14. 현금흐름계획"]
    # 주석 행은 양식에 따라 24행(X01 정본) 또는 25행(3줄 투자 양식)이다.
    try:
        note_row = cashflow_layout(cf)["note"]
    except ValueError as error:
        issues.append({"status": "fail", "reason": str(error)})
        note_row = None
    if note_row is not None and "감가상각" not in str(
            cf.cell(note_row, 2).value or ""):
        issues.append({"status": "fail", "reason": "현금흐름 감가상각 제외 각주 없음"})
    return issues


def _period_forms(period):
    """Regex alternation for every accepted spelling of a fact period.

    W-H C1: `2027년도` was not read at all, so a wrong amount in that
    sentence passed silently.  A registered `2027`/`2027년` accepts `2027`,
    `2027년`, `2027년도` (each optionally with `의`); a registered `1년차`
    accepts `1년차`, `1차년`, `1차년도`.  A calendar year is never crossed
    with an ordinal (`2027` ≠ `1차년도`): the ordinal needs the farm's start
    year, which this check does not receive.
    """
    s = str(period or "").strip()
    forms = []
    m = re.fullmatch(r"(\d{4})\s*(?:년도?)?", s)
    if m:
        year = m.group(1)
        forms = [year + "년도", year + "년", year]
    else:
        m = re.fullmatch(r"(\d+)\s*(?:년차|차년도?)", s)
        if m:
            n = m.group(1)
            forms = [n + "년차", n + "차년도", n + "차년"]
    if not forms:
        return None
    return "(?:" + "|".join(re.escape(f) for f in forms) + r")(?:의)?"


def _period_keys(period):
    """Canonical keys a table header may carry for a fact period."""
    s = str(period or "").strip()
    keys = set()
    m = re.fullmatch(r"(\d{4})\s*(?:년도?)?", s)
    if m:
        keys.add(("year", int(m.group(1))))
    m = re.fullmatch(r"(\d+)\s*(?:년차|차년도?)", s)
    if m:
        keys.add(("nth", int(m.group(1))))
    return keys


def _header_period_keys(cell):
    s = str(cell or "")
    keys = set()
    for m in re.finditer(r"(?<!\d)(\d{4})\s*년", s):
        keys.add(("year", int(m.group(1))))
    for m in re.finditer(r"(?<!\d)(\d+)\s*(?:년차|차년도?)", s):
        keys.add(("nth", int(m.group(1))))
    return keys


def _has_unit(text, unit):
    return (
        re.search(
            r"(?<![가-힣A-Za-z0-9_/])" + re.escape(unit) + r"(?![가-힣A-Za-z0-9_/])",
            text or "",
        )
        is not None
    )


def _has_scope(text, scope):
    return (
        re.search(
            r"(?<![가-힣A-Za-z0-9_])" + re.escape(scope) + r"(?![A-Za-z0-9_])",
            text or "",
        )
        is not None
    )


def _label_cell(cell, label):
    """Match a header cell to the claim label; return (matched, declared unit)."""
    s = str(cell or "").strip()
    if s == label:
        return True, None
    m = re.fullmatch(re.escape(label) + r"\s*[(\[]\s*([^)\]]+?)\s*[)\]]", s)
    if m:
        return True, m.group(1)
    return False, None


_CELL_VALUE = re.compile(
    r"^\s*(?:\(\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*\)"
    r"|([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?))\s*(.*?)\s*$"
)

# A cell that opens like a parenthesized figure but is malformed must not
# be skipped silently — a wrong paren attempt can never hide behind a
# correct occurrence elsewhere.
_CELL_BAD_PAREN = re.compile(r"^\s*[+-]?\s*\(.*\d")


_TABLE_NOTE = re.compile(r"^(?:주\s*\)|(?:주|자료|출처|단위)\s*[:：])")


def _is_table_note(text):
    return _TABLE_NOTE.match(text or "") is not None


def _body_tables(text):
    """Table nodes plus the context each table actually owns.

    Ownership is settled once for every node before any context is
    collected, in priority order: a ``표`` caption belongs to the table it
    announces; an explicit trailing note (``주)``/``주:``/``자료:``/
    ``출처:``/``단위:``) belongs to the table it directly follows — or the
    block it sits inside — never to a table it merely precedes; and the
    whole contiguous run of unclaimed non-note prose leading into a table
    block is that block's introduction, stopping at the heading that opens
    the block's own section.  Each table then collects only its own nodes
    and unclaimed neighbours inside the existing 3-back/2-forward window;
    a node owned by another table, an unattached note, another object
    block (image / ``그림`` caption), or a heading/new ``표`` caption
    after the table is a boundary — never a scope/unit source.
    """
    from gg_document import parse

    nodes = parse(text)
    n = len(nodes)
    is_table = [nd.get("kind") == "table" for nd in nodes]
    # A "주) X: tail" note is split by the parser into a heading plus a
    # tail paragraph — treat the tail as part of the note when the
    # original text still holds them on a single line.
    raw_lines = {ln.strip() for ln in text.splitlines()}
    note_like = [_is_table_note(nd.get("text")) for nd in nodes]
    for k in range(1, n):
        if (
            nodes[k].get("kind") == "paragraph"
            and nodes[k - 1].get("kind") == "heading"
            and note_like[k - 1]
        ):
            head = nodes[k - 1].get("text") or ""
            tail = nodes[k].get("text") or ""
            pat = re.escape(head) + r":\s+" + re.escape(tail)
            if any(re.fullmatch(pat, ln) for ln in raw_lines):
                note_like[k] = True
    # Other object blocks (images and non-표 captions) are boundaries in
    # both directions — they never serve as a table's introduction.
    is_other = [
        nd.get("kind") == "image"
        or (nd.get("kind") == "caption" and nd.get("label") != "표")
        for nd in nodes
    ]
    owner = [None] * n
    for i, t in enumerate(is_table):
        if not t:
            continue
        k = i - 1
        while k >= 0 and not is_table[k] and note_like[k]:
            k -= 1
        if (
            k >= 0
            and nodes[k].get("kind") == "caption"
            and nodes[k].get("label") == "표"
        ):
            owner[k] = i
            for m in range(k + 1, i):
                owner[m] = i
    for k in range(1, n):
        if is_table[k] or owner[k] is not None or not note_like[k]:
            continue
        if is_table[k - 1]:
            owner[k] = k - 1
        elif owner[k - 1] is not None and note_like[k - 1]:
            owner[k] = owner[k - 1]
    for a in range(n):
        t = a if is_table[a] else owner[a]
        if t is None or not (is_table[a] or nodes[a].get("kind") == "caption"):
            continue
        for k in range(a - 1, -1, -1):
            if (
                is_table[k]
                or owner[k] is not None
                or note_like[k]
                or is_other[k]
            ):
                break
            owner[k] = t
            if nodes[k].get("kind") == "heading":
                break

    tables = []
    for i, node in enumerate(nodes):
        if not is_table[i]:
            continue
        context = []
        for j in range(i - 1, max(i - 4, -1), -1):
            kind_j = nodes[j].get("kind")
            if is_table[j] or is_other[j]:
                break
            if owner[j] is not None:
                if owner[j] != i:
                    break
            elif note_like[j]:
                break
            if kind_j in ("caption", "paragraph", "heading"):
                context.append(nodes[j].get("text") or "")
            if kind_j == "heading" and not note_like[j]:
                break
        for j in range(i + 1, min(i + 3, n)):
            kind_j = nodes[j].get("kind")
            if is_table[j] or is_other[j]:
                break
            if owner[j] is not None:
                if owner[j] != i:
                    break
                context.append(nodes[j].get("text") or "")
                continue
            if (
                kind_j == "heading"
                or note_like[j]
                or (kind_j == "caption" and nodes[j].get("label") == "표")
            ):
                break
            if kind_j in ("caption", "paragraph"):
                context.append(nodes[j].get("text") or "")
        tables.append((node["rows"], "\n".join(context)))
    return tables


_DECL_PLACES = re.compile(
    r"소수점?\s*(?:아래|이하)?\s*(\d+)\s*자리\s*(?:까지|로)"
)
_DECL_INT = re.compile(r"정수\s*(?:로|까지)\s*반올림")
_DECL_BREAK = "(,;:)"
_DECL_SUBJECT = re.compile(r"([가-힣A-Za-z0-9/㎡]+?)\s*(?:은|는|의)\s*$")
_MAX_DECL_PLACES = 10


def _decl_unit(text, unit):
    """Unit mention where a digit may precede (``231,594.3천원``)."""
    return (
        re.search(
            r"(?<![가-힣A-Za-z_/])" + re.escape(unit) + r"(?![가-힣A-Za-z0-9_/])",
            text or "",
        )
        is not None
    )


def _display_places(scope_text, unit):
    """Local display-rounding declaration for ``unit`` inside ``scope_text``.

    Returns ``None`` when no declaration applies — the caller keeps the
    exact Decimal comparison; an ``int`` N when exactly one valid
    ``소수 N자리까지 반올림``/``정수로 반올림`` declaration binds to
    ``unit``; or ``"conflict"`` when an applicable declaration lacks places,
    is out of range, or is contradicted by another declaration — the claim
    then stays a mismatch rather than silently passing on exact comparison.
    """
    found = set()
    broken = False
    for line in str(scope_text or "").splitlines():
        if "반올림" not in line:
            continue
        matches = [
            (m.start(), m.group(1)) for m in _DECL_PLACES.finditer(line)
        ]
        matches += [(m.start(), "0") for m in _DECL_INT.finditer(line)]
        if not matches:
            head = line[: line.index("반올림")]
            w0 = max(head.rfind(ch) for ch in _DECL_BREAK)
            window = head[w0 + 1 :].strip()
            subj = _DECL_SUBJECT.search(window)
            if _decl_unit(window, unit):
                broken = True
            elif subj is not None:
                broken = broken or (subj.group(1) == unit)
            elif not window:
                broken = broken or _decl_unit(head, unit)
            continue
        for start, digits in matches:
            w0 = max(line.rfind(ch, 0, start) for ch in _DECL_BREAK)
            window = line[w0 + 1 : start].strip()
            subj = _DECL_SUBJECT.search(window)
            if _decl_unit(window, unit):
                bound = True
            elif subj is not None:
                bound = subj.group(1) == unit
            elif not window or "반올림" in window:
                bound = _decl_unit(line[:start], unit)
            else:
                bound = False
            if not bound:
                continue
            n = int(digits)
            if n > _MAX_DECL_PLACES:
                broken = True
            else:
                found.add(n)
    if broken or len(found) > 1:
        return "conflict"
    if found:
        return found.pop()
    return None


def _round_half_up(value, places):
    """Quantize a canonical Decimal to ``places`` decimals (ROUND_HALF_UP)."""
    prec = max(28, len(value.as_tuple().digits) + places + 2)
    try:
        return value.quantize(
            Decimal(1).scaleb(-places),
            rounding=ROUND_HALF_UP,
            context=Context(prec=prec),
        )
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _table_claim_verdict(rows, context, label, expected_d, unit, period_keys, scope):
    """(matched, conflicted) for one table against one claim.

    A cell position counts only when the table's adjacent context declares
    the scope, the unit is declared by the item cell or the context, and the
    item row/column meets the period row/column.  Wrong period/scope/unit or
    a same-valued cell under another item can never stand in.
    """
    if not rows or not period_keys or not _has_scope(context, scope):
        return False, False
    decl = _display_places(context, unit)
    ncols = max(len(r) for r in rows)

    def cell(r, c):
        row = rows[r]
        return row[c] if c < len(row) else ""

    matched = conflicted = False
    for r in range(1, len(rows)):
        for c in range(1, ncols):
            for label_cell, period_cell in (
                (cell(r, 0), cell(0, c)),
                (cell(0, c), cell(r, 0)),
            ):
                ok, declared = _label_cell(label_cell, label)
                if not ok or not (_header_period_keys(period_cell) & period_keys):
                    continue
                if declared is not None:
                    if declared != unit:
                        conflicted = True
                        continue
                elif not _has_unit(
                    context + "\n" + str(label_cell) + "\n" + str(period_cell),
                    unit,
                ):
                    continue
                m = _CELL_VALUE.match(str(cell(r, c)))
                if m is None:
                    if _CELL_BAD_PAREN.match(str(cell(r, c))):
                        conflicted = True
                    continue
                rest = m.group(3).strip()
                if rest and rest != unit:
                    conflicted = True
                    continue
                claimed = Decimal(
                    (m.group(1) or m.group(2)).replace(",", "")
                )
                if m.group(1) is not None:
                    claimed = -claimed
                if decl == "conflict":
                    conflicted = True
                    continue
                target = (
                    expected_d
                    if decl is None
                    else _round_half_up(expected_d, decl)
                )
                if target is not None and claimed == target:
                    matched = True
                else:
                    conflicted = True
    return matched, conflicted


_MACHINE_FIELD_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+$")

# A copula/ending after a year-shaped figure makes that figure a value
# (`50,000천원 및 2028이다`), not the label of the next period (W-H 코드1회 ①).
_COPULA_TAIL = re.compile(
    r"(?:입(?:니다|니까)|이(?:다|며|고|지|면|므로|었)|임(?![가-힣])|였)"
)

_SECOND_AMOUNT = re.compile(
    r"\s*(?:~|–|—|∼|과|와|및|또는|혹은|내지|,|/|-)\s*"
    r"(?P<sign>[+\-\u2212\u00B1])?"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\d.,])(?!\s*년도?)"
    # W-H (나3): `, 1인당 200만원` — the figure in front of `…당` counts
    # people/units, and the money after it is a per-unit rate, not a second
    # amount of this claim.
    r"(?!\s*[가-힣]{0,2}당)"
    # W-D8: `, 3개 품목 기준` / `, -5℃ 저장` are counts and measures, not a
    # second amount.  A bare number after a connector (`및 60,000`) still
    # blocks; only a following non-money unit clears it.
    r"(?!\s*(?:개|품목|작목|포기|그루|주|명|회|건|톤|kg|Kg|KG|g|㎏|ha|㎡|평|"
    r"℃|°C|%|리터|L|ℓ|시간|분|초|년|월|일|차|배|번|미터|m|cm|mm))"
)


def _has_second_amount(clause, amount_end, unit_match, amount_text=None):
    """A matching `50,000천원` must not head `50,000천원~60,000천원` /
    `50,000천원과 60,000천원` / `50,000천원 및 60,000천원` and still pass
    (W-D2 @Roh 중요①, W-D6 @PR#111 P2).

    The connector set covers ranges, parallels and enumerations (`및`,
    `또는`, `혹은`, `내지`, comma, slash, hyphen).  A thousands separator
    inside one amount (`50,000`) never reaches this check — the first
    amount is consumed whole before ``amount_end`` — and a trailing
    `년` keeps a year (`, 2028년 매출액은 …`) from reading as a second
    amount.

    This gate now only covers what the value scan cannot judge: a figure
    that carries no unit at all (`및 60,000`) and a verbatim duplicate of
    the checked figure (`, 50,000천원` twice, pinned since W-D7).  Every
    other figure goes to :func:`find_rival_amounts`, which compares the
    signed decimal — so `50,000.0천원` (same value, different spelling)
    passes while `50,000.1천원` and `-50,000천원` fail (Roh confirm5)."""
    after = clause[amount_end:].lstrip()
    if unit_match is not None:
        after = after[len(unit_match.group()):]
    match = _SECOND_AMOUNT.match(after)
    if match is None:
        return False
    rest = after[match.end():].lstrip()
    if unit_match is not None:
        has_unit = rest.startswith(unit_match.group())
    else:
        has_unit = bool(rest)
    if not has_unit:
        # W-H (가1): `, 2028 매출액은 …` — a four-digit calendar year in
        # front of a Korean word names the next period, not a second amount
        # of this claim.  A four-digit amount keeps blocking because it
        # carries the checked unit (`2028천원`) or a non-word tail.
        if (
            re.fullmatch(r"(?:19|20)\d{2}", match.group("num"))
            and re.match(r"[가-힣]", rest)
            and _COPULA_TAIL.match(rest) is None
        ):
            return False
        # W-H (나3): a `만원`/`백만원`/`억원` figure is compared by value in
        # :func:`find_rival_amounts` — deciding here would block a same-value
        # restatement (`50,000천원, 5,000만원이다`).
        if _won_scale_head(rest) is not None:
            return False
        return True
    if amount_text is None:
        return True
    plain = lambda value: fold_amount_text(value).replace(" ", "")
    written = plain((match.group("sign") or "") + match.group("num"))
    return written == plain(amount_text)


# W-D8: a second amount may be joined by any glyph the writer likes.  The
# connector list approach kept leaking (range → repeated claim → `및` →
# comma with blanks/sign), so the scan below looks for the *same unit*
# instead of a typographic join, and classifies what stands in front of the
# figure.  `crosscheck_body` blocks only the `conflict` class; a figure that
# a new claim head or a period introduces stays a separate claim.
_AMOUNT_FOLD = str.maketrans(
    {
        "\uFF0C": ",",  # ，full-width comma
        "\u3001": ",",  # 、ideographic comma
        "\uFF1B": ";",  # ；
        "\uFF1A": ":",  # ：
        "\u3000": " ",  # ideographic space
        "\u00A0": " ",  # no-break space
        "\uFF0B": "+",  # ＋
        "\uFF0D": "-",  # －
        "\u2212": "-",  # −
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
    }
)

# `비용은` / `매출액이` / `총수입도` / `값은` — a label carrying a topic/subject
# particle opens its own claim.  A one-syllable label is legal (W-H 가2: `값은
# 30,000천원` used to block); the connector set below decides the words that
# end in a particle shape but introduce no claim (`또는`, `혹은`, `정도`).
_CLAIM_HEAD = re.compile(r"(?P<label>[가-힣A-Za-z][가-힣A-Za-z0-9]*)(?:은|는|이|가|도)\s*$")
# W-H (나1): a correction phrase re-states the same fact with another value.
# `50,000천원인데 실제는 60,000천원이다` is one statement, not two claims, so it
# blocks.  `실제 매출액은 …` names an item and stays a new claim (the label is
# `매출액`, not `실제`).
_CORRECTION_WORDS = frozenset(("실제", "실제로", "사실", "정정", "정확히"))
# Connective and adverbial words that end in a particle shape but introduce
# no claim of their own (`아마도`, `가까이`, `같이`, …).
_NON_CLAIM_WORDS = frozenset(
    (
        # connectors
        "또는", "혹은", "및", "내지", "그리고", "또한", "그러나", "하지만",
        "따라서", "그런데", "즉", "예컨대",
        # demonstrative pronouns: `이는 60,000천원이다` restates the same
        # amount, it does not name a second item (W-H 코드1회 ②).
        "이", "그", "저",
        # degree / approximation
        "정도", "대략", "약간", "거의", "모두", "대개", "대체로", "대강",
        # adverbs and manner words
        "아마도", "가까이", "같이", "많이", "조금", "매우", "아주", "특히",
        "더욱", "훨씬", "계속", "다시", "먼저", "함께", "서로", "스스로",
        "마침", "이미", "아직", "반드시", "결국", "과연", "혹시", "비록",
        "이처럼", "그처럼", "비슷하게", "마찬가지로", "무려", "겨우",
    )
)
# `값:` / `비용 =` — a delimiter opens a claim only behind a label; a
# bare `50,000천원:` is a juxtaposition, not a new claim.
_DELIM_HEAD = re.compile(r"(?P<label>[가-힣A-Za-z][가-힣A-Za-z0-9]*)\s*[:=]\s*$")
# `2028년` / `2년차` / `2027년도` / `1차년도` — a period in front of a figure
# starts a new claim (W-H C1: the `년도` forms were not read at all).
_PERIOD_TAIL = re.compile(
    r"(?:\d\s*년(?:차|도)?|\d+\s*차\s*년도?)\s*(?:의)?\s*$"
)
# `2028년 매출액은 …` / `2028년도 매출액은 …` — the period opens the tail itself.
_PERIOD_HEAD = re.compile(r"^(?:19|20)\d{2}\s*년(?:도)?\s*(?:의)?")
# `2026년도~2027년도` / `2026년부터 2027년까지` — a year range is not the
# single declared period.  The digits are separated by a year word, so the
# plain `\d~\d` range test cannot see it (W-H 코드1회 ④).
_YEAR_RANGE = re.compile(
    r"(?:19|20)\d{2}\s*년(?:도)?\s*(?:[~–—∼～－]|부터)\s*(?:19|20)\d{2}"
)


def _range_covers(clause, claim):
    """True when a year range overlaps this claim's own period.

    `2026년도~2027년도 농장A의 …` puts the first year of the range before the
    claim and the second year inside it, and `농장A의 2026년~2027년 매출액은 …`
    puts it inside the claim, so in both the claim's period is one end of a
    range rather than the single declared period.  A range that merely
    precedes the claim (`2026년~2027년 계획 대비 2027년 매출액은 …`) does not
    overlap and must not blame it (W-H 코드1회 ④).
    """
    for hit in _YEAR_RANGE.finditer(clause):
        if hit.start() < claim.end() and hit.end() > claim.start():
            return True
    return False


def fold_amount_text(text):
    """Fold full-width punctuation, odd spaces and dash variants for scanning.

    Only the scan copies are folded; the caller's text is untouched, so
    ``claim`` strings and offsets reported elsewhere keep the author's
    glyphs.
    """
    return str(text or "").translate(_AMOUNT_FOLD)


# W-H (나3): a rival figure may be written in `만원`/`백만원`/`억원` while the
# checked fact is registered in `원`/`천원`.  Those are money amounts, so they
# are compared by value; a per-unit figure (`1인당 200만원`, `50만원/평`) is a
# rate, not a second amount of this claim, and stays out.
_WON_SCALE = {"억원": Decimal(100000000), "백만원": Decimal(1000000),
              "만원": Decimal(10000)}
_UNIT_KRW = {"원": Decimal(1), "천원": Decimal(1000)}
_WON_SCALE_RE = re.compile(r"(억원|백만원|만원)")
# `평당 약 50만원` / `1인당 대략 200만원` — an approximation word between the
# per-unit head and the figure keeps the figure a rate (W-H 코드1회 ③).
_PER_UNIT_HEAD = re.compile(r"[가-힣A-Za-z0-9㎡]*당\s*(?:약|대략|정도|쯤)?\s*$")
_WON_SCALE_AMOUNT = re.compile(
    r"(?<!\d)(?<![.,]\d)(?P<sign>[+\-\u00B1])?\s*"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?:억원|백만원|만원)(?!\s*(?:/|당|씩))"
)


def _won_scale_head(rest):
    """The won-scale word a figure carries (`만원`, `억원`), or None."""
    match = _WON_SCALE_RE.match(rest or "")
    return match.group(1) if match is not None else None


# One figure may look back this far for the words that classify it.  Every
# rule in :func:`_classify_figure_head` and `_PER_UNIT_HEAD` is anchored to
# the figure, so only the nearby tail matters; the cap keeps a long run of
# figures linear (W-H 코드1회 ⑤).
_HEAD_WINDOW = 64


def _figure_head(text, start):
    """The clause tail in front of a figure, bounded in length.

    :func:`_classify_figure_head` only reads the piece after the last
    ``','``/``';'``, so cutting there changes nothing; the cap then bounds
    the work one figure can demand, because a run of thousands of figures
    with no comma would otherwise re-scan the whole prefix for each of them
    (W-H 코드1회 ⑤).  The cap moves forward to the next word start, so a
    partial word is never handed to the head rules.
    """
    cut = max(text.rfind(",", 0, start), text.rfind(";", 0, start)) + 1
    if start - cut > _HEAD_WINDOW:
        cut = start - _HEAD_WINDOW
        while cut < start and not text[cut].isspace():
            cut += 1
    return text[cut:start].rstrip()


def _won_scale_amounts(text, unit):
    """`만원`/`백만원`/`억원` figures converted into ``unit``.

    Entries carry the same shape as :func:`find_rival_amounts` plus the
    ``sign`` the caller needs to honour `±` (which never equals a plain
    figure).  A non-money ``unit`` yields nothing: `kg`/`㎡` claims have no
    won-scale spelling.
    """
    unit_krw = _UNIT_KRW.get(unit)
    if unit_krw is None:
        return []
    found = []
    for match in _WON_SCALE_AMOUNT.finditer(text):
        head = _figure_head(text, match.start())
        if _PER_UNIT_HEAD.search(head):
            continue
        token = match.group()
        scale = None
        for word in ("백만원", "억원", "만원"):
            if token.endswith(word):
                scale = _WON_SCALE[word]
                break
        if scale is None:
            continue
        value = Decimal(match.group("num").replace(",", "")) * scale / unit_krw
        if match.group("sign") == "-":
            value = -value
        found.append(
            {
                "text": token.strip(),
                "value": str(value),
                "start": match.start(),
                "end": match.end(),
                "sign": match.group("sign"),
                "kind": _classify_figure_head(head, unit),
            }
        )
    return found


def _unit_amount_re(unit):
    return re.compile(
        # A figure may follow `,` (`50,000천원, 60,000천원`), but must not
        # start inside a longer number: no digit immediately before, and no
        # `,`/`.` followed by a digit (`1,234,567` is consumed whole).
        r"(?<!\d)(?<![.,]\d)"
        r"(?P<sign>[+\-\u00B1])?\s*"
        # The fraction belongs to the value: `50,000.1천원` is not
        # `50,000천원` (Roh confirm5 중요①).
        r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
        + re.escape(unit)
        + r"(?![A-Za-z0-9/\u33A1])"
    )


def _classify_figure_head(head, unit):
    """Why a figure stands where it does: ``conflict``, ``new_claim``, ``year``.

    ``new_claim`` needs a real label in front of the particle — a topic or
    subject particle glued to the previous amount's unit (`50,000천원이
    60,000천원`) is a contradiction, not a new claim, so the label must not
    itself follow a digit, and the unit word itself (`천원:` /
    `천원 =`) is not a label.  The figure may sit right after a comma
    (`50,000천원, 비용은 30,000천원`), so the tail up to the last clause
    break is what gets classified.
    """
    tail = re.split(r"[,;]\s*", head)[-1].strip()
    if _PERIOD_TAIL.search(tail) or _PERIOD_HEAD.match(tail):
        return "year"
    # W-H (나1): `…인데 실제는 60,000천원` / `, 실제로는 …` re-states the same
    # fact with a corrected value, so it is one claim and blocks.  Checked
    # before the generic head rule, which would read `실제` as a new item.
    claim = _CLAIM_HEAD.search(tail)
    if claim is not None and claim.group("label") in _CORRECTION_WORDS:
        return "conflict"
    match = _DELIM_HEAD.search(tail)
    if (
        match is not None
        and match.group("label") != unit
        # `즉:` / `따라서:` introduce a restatement, not a second item, so
        # the connector rule applies to `:`/`=` heads as well (W-H 코드1회 ②).
        and match.group("label") not in _NON_CLAIM_WORDS
    ):
        return "new_claim"
    match = _CLAIM_HEAD.search(tail)
    if match is not None and match.group("label") != unit:
        label = match.group("label")
        before = tail[: match.start("label")]
        # The stem alone decides: `내지` is a connector whatever particle
        # follows it (`내지는`, `내지가`), and `또한`/`정도` likewise.  Only
        # a stem outside the connector set can open a real claim.
        if (not before or before[-1] not in "0123456789.,") and (
            match.group().strip() not in _NON_CLAIM_WORDS
            and label not in _NON_CLAIM_WORDS
        ):
            return "new_claim"
    return "conflict"


def find_rival_amounts(text, unit, *, expected=None):
    """Same-unit figures after a checked amount, with their standing.

    Returns one entry per figure whose value differs from ``expected`` (pass
    ``expected=None`` to keep every figure)::

        {"text": "60,000천원", "value": "60000", "start": 30, "end": 39,
         "kind": "conflict"}

    The comparison is on the whole signed decimal, so `50,000.0천원` equals
    `50,000천원` while `50,000.1천원` does not, and `-50,000천원` is a
    different figure from `50,000천원` (Roh confirm5 중요①).  A `±` figure
    is never equal to a plain one — it states two values at once.

    ``kind`` is ``conflict`` when nothing separates the figure from the
    claim it follows, ``new_claim`` when a label+particle or ``:``/``=``
    introduces it, and ``year`` when a period introduces it.  Callers
    decide the policy (block vs warn); this helper only reports.
    """
    folded = fold_amount_text(text)
    pattern = _unit_amount_re(unit)
    want = None
    if expected is not None:
        want = Decimal(str(expected).replace(",", ""))
    found = []
    for match in pattern.finditer(folded):
        sign = match.group("sign")
        value = Decimal(match.group("num").replace(",", ""))
        if sign == "-":
            value = -value
        if want is not None and sign != "\u00B1" and value == want:
            continue
        head = _figure_head(folded, match.start())
        found.append(
            {
                "text": match.group().strip(),
                "value": str(value),
                "start": match.start(),
                "end": match.end(),
                "kind": _classify_figure_head(head, unit),
            }
        )
    # W-H (나3): `6,000만원`/`6억원`/`6,000백만원` beside a `50,000천원`
    # claim is the same money in another spelling, so it is compared by
    # value here.  Per-unit rates (`1인당 200만원`, `50만원/평`) and `원`
    # prices stay out (`_won_scale_amounts`), as does a non-money unit.
    for entry in _won_scale_amounts(folded, unit):
        value = Decimal(entry["value"])
        if want is not None and entry["sign"] != "\u00B1" and value == want:
            continue
        entry.pop("sign", None)
        found.append(entry)
    found.sort(key=lambda entry: entry["start"])
    return found


def crosscheck_body(text, values):
    """Check explicit scope/period/metric/amount/unit claims, not arbitrary prose.

    ``values`` accepts the legacy ``{label: meta}`` mapping or a list of
    claim records (``fact_id`` identity, ``field_id`` display label, value,
    unit, period, scope) so repeated labels across periods survive.  Table
    claims count only when the connected axes and declared scope/unit admit
    the position; a conflicting sentence or table cell is never covered by
    one correct occurrence.
    """
    issues = []
    if not isinstance(text, str):
        return [{"location": "body", "status": "fail", "reason": "본문 텍스트 없음"}]

    num_pattern = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])")

    if isinstance(values, dict):
        items = [(None, label, meta) for label, meta in values.items()]
    elif isinstance(values, (list, tuple)):
        items = [
            (
                (v.get("fact_id"), v.get("field_id"), v)
                if isinstance(v, dict)
                else (None, None, v)
            )
            for v in values
        ]
    else:
        items = [(None, None, values)]
    tables = _body_tables(text) if items else []

    for fact_id, label, meta in items:
        issue = {"location": str(label), "status": "fail"}
        if fact_id is not None:
            issue["fact_id"] = fact_id
        if not isinstance(label, str) or not label.strip():
            issue["reason"] = "유효하지 않은 메타데이터/기준값"
            issues.append(issue)
            continue
        issue["location"] = label
        if label not in text:
            if _MACHINE_FIELD_ID.match(label):
                # A fact identifier is not prose: it cannot appear in the
                # body, so a miss says nothing about the body being wrong.
                issue["status"] = "blocked"
                issue["reason"] = (
                    "사실 이름이 기계용 식별자라 본문에서 찾을 수 없음: "
                    "field_id 를 본문에 쓰는 항목 이름(예: 매출액)으로 두어야 대조한다"
                )
            else:
                issue["reason"] = "본문에 재무 항목 없음"
            issues.append(issue)
            continue

        # Fail closed on missing metadata (non-dict, or missing/blank value/unit/period/scope)
        val_raw = meta.get("value") if isinstance(meta, dict) else None
        unit_req = meta.get("unit") if isinstance(meta, dict) else None
        period_req = meta.get("period") if isinstance(meta, dict) else None
        scope_req = meta.get("scope") if isinstance(meta, dict) else None

        if (
            val_raw is None
            or unit_req is None
            or period_req is None
            or scope_req is None
            or not str(unit_req).strip()
            or not str(period_req).strip()
            or not str(scope_req).strip()
        ):
            issue["reason"] = "유효하지 않은 메타데이터/기준값"
            issues.append(issue)
            continue

        try:
            expected_d = Decimal(str(val_raw).replace(",", ""))
        except InvalidOperation:
            issue["reason"] = f"유효하지 않은 기준값: {val_raw}"
            issues.append(issue)
            continue

        expected_unit = str(unit_req).strip()
        expected_period = str(period_req).strip()
        expected_scope = str(scope_req).strip()

        # Split text into lines/sentences.
        # W-H (나2): a line break does not end a statement when the next
        # line opens with a figure (`…50,000천원\n60,000천원이다`), so that
        # rival stays in the same run and the value scan judges it.  A
        # blank line, or a line that opens with a word, is a new statement
        # — and a full stop before the break already ended the sentence.
        line_break = r"\r?\n[ \t]*(?![+\-\u2212\u00B1]?[ \t]*\d)"
        _sentence_split = re.compile(
            line_break
            + r"|(?<=[^\d])\.(?:\s+|$)|(?<=\d)\.(?=[^\d\s]|\s+[^\d])|[;•·]+"
        )
        # W-D8: `;`/`·` break a sentence for claim matching but do not end
        # the run a rival amount can hide in — `50,000천원;60,000천원` is one
        # statement.  The rival scan below therefore uses the enclosing run,
        # which stops only at a full stop or a line break.
        _run_split = re.compile(
            line_break + r"|(?<=[^\d])\.(?:\s+|$)|(?<=\d)\.(?=[^\d\s]|\s+[^\d])"
        )
        sentences = []
        scans = []
        for run in _run_split.split(text):
            for sent in _sentence_split.split(run):
                sentences.append(sent)
                scans.append(run)

        scope = re.escape(expected_scope)
        period = re.escape(expected_period)
        # v0.4.8 ⓑ: a natural Korean sentence writes the owner with 의
        # (합성학생농장의 2027년 …) and a bare year as 2027년.  The unit of
        # comparison (scope/period/label/value/unit) is untouched — only
        # these two boundaries widen.  A `년` tail is added ONLY to a
        # numeric-only period: appending it to `2026년`/`1년차` would accept
        # the non-word `2026년년`.  `(?<!\w)` still guards both orderings, so
        # `농장A`/`12027` cannot masquerade as `농장`/`2027`.
        # W-D2 @Roh 중요①: the widened regex must not swallow a period or
        # amount RANGE as a single occurrence, and must not let a first
        # matching amount silence a second, different one in the same
        # clause. Guard with the leading/trailing boundaries the old
        # exact-match semantics guaranteed: reject the sentence when the
        # matched period is embedded in a range (`2026~2027` on either
        # side) or the matched amount is followed by a range/parallel
        # second amount (`50,000~60,000`, `50,000과 60,000`).
        scope_part = scope + r"(?:의)?"
        # W-H C1: every accepted spelling of the period (`2027`, `2027년`,
        # `2027년도`, `1년차`, `1차년도`) is one period.  A period this check
        # does not recognise keeps the exact spelling, so behaviour for any
        # other format is unchanged.
        period_part = _period_forms(expected_period) or period
        claim_pattern = re.compile(
            r"(?<!\w)(?:"
            + scope_part
            + r"\s+"
            + period_part
            + "|"
            + period_part
            + r"\s+"
            + scope_part
            + r")\s+"
            + r"(?P<label>"
            + re.escape(label)
            + r")(?:은|는|이|가)?\s*[:=]?\s*"
        )
        unit_pattern = re.compile(re.escape(expected_unit) + r"(?![A-Za-z0-9/㎡])")
        found_match = False
        conflicting_claim = False
        rival_amount = False
        for sent, scan in zip(sentences, scans):
            if label not in sent:
                continue

            # Split sentence into clauses. A comma may open a new claim
            # (`, 비용은 …`) but never a second amount of the amount in
            # front of it: `,  60,000` / `, +60,000` must stay inside the
            # clause so `_has_second_amount` sees the juxtaposition
            # (W-D7 @Roh confirm4). Any run of blanks is consumed here,
            # so the lookahead sees the amount's sign or digit, not a
            # leftover space.
            clauses = re.split(r",\s+(?=[^\d\s+\-\u2212])", sent)
            for clause in clauses:
                # W-D3 @Roh 중요①: every claim in the clause must be
                # checked, not just the first — a first correct amount
                # must not silence a second, different one.
                for claim in claim_pattern.finditer(clause):
                    label_start = claim.start("label")
                    valid_occurrence = any(
                        m.start() == label_start for m in re.finditer(re.escape(label), clause)
                    )
                    if not valid_occurrence:
                        continue
                    # Period is part of a range ('2026~2027') — a range
                    # occurrence is not the single declared period.
                    # Check the period window only, so an unrelated later
                    # range in the same clause does not false-positive.
                    window_start = max(0, claim.start() - 12)
                    window_end = min(len(clause), claim.end() + 12)
                    window = clause[window_start:window_end]
                    if re.search(r"\d\s*[~–—∼]\s*\d", window) or _range_covers(
                        clause, claim
                    ):
                        conflicting_claim = True
                        continue
                    amount = num_pattern.match(clause, claim.end())
                    if amount is None:
                        conflicting_claim = True
                        continue
                    unit_tail = clause[amount.end() :].lstrip()
                    unit = unit_pattern.match(unit_tail)
                    if unit is None:
                        # A same-unit figure may hide behind a slash the
                        # unit lookahead rejects (`50,000천원/60,000천원`);
                        # read the unit as matched so the ordinary rival
                        # rules below judge it (책임자 확인5-2).  Only a
                        # figure that carries the same unit counts: a slash
                        # opening a compound unit (`천원/㎡`, `천원/10a`)
                        # leaves `unit` unmatched and stays a plain unit
                        # mismatch, including `/10a` whose digits alone
                        # would otherwise look like an amount.
                        if re.match(
                            re.escape(expected_unit)
                            + r"\s*/\s*[+\-\u2212\u00B1]?"
                            r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*"
                            + re.escape(expected_unit),
                            fold_amount_text(unit_tail),
                        ):
                            unit = re.match(re.escape(expected_unit), unit_tail)
                    actual = Decimal(amount.group().replace(",", ""))
                    decl = _display_places(clause, expected_unit)
                    if decl is None:
                        target = expected_d
                    elif decl == "conflict":
                        target = None
                    else:
                        target = _round_half_up(expected_d, decl)
                    if unit is None or target is None or actual != target:
                        # A rival figure can be glued to the unit with no
                        # blank (`50,000천원50,000.1천원`); the unit lookahead
                        # rejects the digit that follows, so that shape would
                        # otherwise fail without the student hint (Roh
                        # confirm5 중요①③).
                        glued = re.match(
                            re.escape(expected_unit)
                            + r"(?=\s*[+\-\u2212\u00B1]?\d)",
                            fold_amount_text(clause[amount.end() :]).lstrip(),
                        )
                        if (
                            glued is not None
                            and target is not None
                            and actual == target
                            and any(
                                f["kind"] == "conflict"
                                for f in find_rival_amounts(
                                    scan, expected_unit, expected=target
                                )
                            )
                        ):
                            rival_amount = True
                        conflicting_claim = True
                    elif _has_second_amount(
                        clause, amount.end(), unit, amount.group()
                    ):
                        # The connector gate still catches a figure with no
                        # unit (`및 60,000`) and a verbatim repeat; every
                        # other rival is judged by value below.
                        conflicting_claim = True
                        rival_amount = True
                    else:
                        # W-D8: one correct amount must not excuse a rival
                        # same-unit figure elsewhere in the same sentence.
                        # The check is sentence-wide because a comma may
                        # split the rival into another clause, and the join
                        # may be any glyph (`，`, `->`, ` = `, ` 약 `…).
                        # `;`/`·` also separate claims without ending one,
                        # so the scan covers the run those glyphs join and
                        # stops only at a full stop or a line break.  A
                        # figure a new claim head (`비용은`) or a period
                        # (`2028년`) introduces is a separate claim.
                        rival = [
                            f for f in find_rival_amounts(
                                scan, expected_unit, expected=target)
                            if f["kind"] == "conflict"
                        ]
                        if rival:
                            conflicting_claim = True
                            rival_amount = True
                        else:
                            found_match = True

        for rows, context in tables:
            matched, conflicted = _table_claim_verdict(
                rows,
                context,
                label,
                expected_d,
                expected_unit,
                _period_keys(expected_period),
                expected_scope,
            )
            found_match = found_match or matched
            conflicting_claim = conflicting_claim or conflicted

        if not found_match or conflicting_claim:
            # Keep the original reason verbatim — existing readers, tests and
            # the app parser match it — and append the student's fix hint
            # only when a rival same-unit amount is what blocked the claim.
            reason = "기간·단위·범위·출력 반올림 값 불일치"
            if rival_amount:
                reason += (
                    " · 같은 문장에 다른 금액이 함께 있습니다. "
                    "그 금액이 전년·목표·일부 금액이면 문장을 나눠 쓰세요"
                )
            issue["reason"] = reason
            issues.append(issue)

    return issues


# A26 body↔workbook locators: cached cells carrying the authoritative
# first-year value, year axis, and unit banner for each checked claim.
A26_CLAIM_LOCATORS = (
    {
        "label": "매출액",
        "unit": "천원",
        "value_ref": (" 5. 판매계획", "D50"),
        "period_ref": (" 5. 판매계획", "D38"),
        "unit_ref": (" 5. 판매계획", "I38"),
    },
    {
        "label": "당기순이익",
        "unit": "천원",
        "value_ref": ("12 손익계획", "C26"),
        "period_ref": ("12 손익계획", "C4"),
        "unit_ref": ("15.추정소득분석", "I2"),
    },
    {
        "label": "수매 판매량",
        "channel": "수매",
        "unit": "kg",
        "value_ref": (" 5. 판매계획", "E22"),
        "unit_ref": (" 5. 판매계획", "I19"),
    },
    {
        "label": "직거래 판매량",
        "channel": "직거래",
        "unit": "kg",
        "value_ref": (" 5. 판매계획", "E26"),
        "unit_ref": (" 5. 판매계획", "I19"),
    },
)


def _a26_year(raw):
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    m = re.search(r"(\d{4})", str(raw or ""))
    return int(m.group(1)) if m else None


def crosscheck_body_workbook(text, path, scope, *, workbook_profile=None):
    """Crosscheck canonical body claims against recalculated workbook cells.

    Each issue names the body claim location and the workbook sheet!cell that
    contradicts it.  A missing or non-numeric cached value is reported as
    ``blocked`` so an unrecalculated workbook cannot masquerade as a mismatch.
    """
    if not profile_supports(workbook_profile):
        return [{"location": "body", "status": "not_checked",
                 "reason": "17시트 좌표 대조 미지원: school_17_sheet_v1 전용"}]
    if not isinstance(text, str):
        return [{"location": "body", "status": "fail", "reason": "본문 텍스트 없음"}]
    scope = str(scope or "").strip()
    if not scope:
        return [
            {
                "location": "body",
                "status": "fail",
                "reason": "유효하지 않은 메타데이터/기준값",
            }
        ]
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return _crosscheck_with_workbook(wb, text, scope)
    finally:
        wb.close()


def _crosscheck_with_workbook(wb, text, scope):
    issues = []
    num_pattern = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])"
    scope_p = re.escape(scope)
    sentences = re.split(
        r"(?:\r?\n)+|(?<=[^\d])\.(?:\s+|$)|(?<=\d)\.(?=[^\d\s]|\s+[^\d])|[;•·]+",
        text,
    )
    clauses = []
    for sent in sentences:
        clauses.extend(re.split(r",\s+(?=[^\d])", sent))

    for loc in A26_CLAIM_LOCATORS:
        label = loc["label"]
        v_sheet, v_cell = loc["value_ref"]
        value_ref = v_sheet + "!" + v_cell
        if v_sheet not in wb.sheetnames:
            issues.append(
                {
                    "location": label,
                    "cell": value_ref,
                    "status": "fail",
                    "reason": "시트 없음: " + v_sheet,
                }
            )
            continue
        cached = wb[v_sheet][v_cell].value
        if isinstance(cached, bool) or not isinstance(cached, (int, float)):
            issues.append(
                {
                    "location": label,
                    "cell": value_ref,
                    "status": "blocked",
                    "reason": "재계산 캐시 없음/미실행",
                }
            )
            continue
        expected_d = Decimal(str(cached))
        expected_unit = loc["unit"]
        u_sheet, u_cell = loc["unit_ref"]
        unit_ref = u_sheet + "!" + u_cell

        expected_year = None
        period_ref = None
        if "period_ref" in loc:
            p_sheet, p_cell = loc["period_ref"]
            expected_year = _a26_year(wb[p_sheet][p_cell].value)
            period_ref = p_sheet + "!" + p_cell

        channel = loc.get("channel")
        if channel:
            claim_re = re.compile(
                r"(?<!\w)"
                + re.escape(channel)
                + r"\s+(?P<amount>"
                + num_pattern
                + r")\s*(?P<unit>[^\s,.;:()×]*)"
            )
        else:
            claim_re = re.compile(
                r"(?<!\w)(?:"
                + scope_p
                + r"\s+(?P<period>\d{4}년|\d+년차)|(?P<period2>\d{4}년|\d+년차)\s+"
                + scope_p
                + r")\s+"
                + re.escape(label)
                + r"(?:은|는|이|가)?\s*[:=]?\s*(?P<amount>"
                + num_pattern
                + r")\s*(?P<unit>[^\s,.;:()×]*)"
            )

        found_match = False
        for clause in clauses:
            decl = _display_places(clause, expected_unit)
            if decl is None:
                effective_d = expected_d
                decl_note = ""
            elif decl == "conflict":
                effective_d = None
                decl_note = " (표시 반올림 선언 모호·상충)"
            else:
                effective_d = _round_half_up(expected_d, decl)
                if effective_d is None:
                    decl_note = " (선언 소수 %d자리 반올림 계산 불가)" % decl
                else:
                    decl_note = (
                        " (소수 %d자리 반올림 표시 선언 기준 %s)"
                        % (decl, effective_d)
                    )
            for m in claim_re.finditer(clause):
                raw_amount = m.group("amount")
                try:
                    claimed_d = Decimal(raw_amount.replace(",", ""))
                except InvalidOperation:
                    claimed_d = None
                raw_unit = m.group("unit") or ""
                claimed_unit = re.sub(
                    r"(?:이다|입니다|이며|이고|에서|으로|였다|된다|임)\.?$",
                    "",
                    raw_unit,
                )
                unit_ok = claimed_unit == expected_unit

                claimed_period = m.groupdict().get("period") or m.groupdict().get(
                    "period2"
                )
                if claimed_period and expected_year is not None:
                    claimed_year = _a26_year(claimed_period)
                    if claimed_period.endswith("년차"):
                        claimed_year = expected_year + int(claimed_period[:-2]) - 1
                    if claimed_year is not None and claimed_year != expected_year:
                        if claimed_d is not None and claimed_d == effective_d:
                            issues.append(
                                {
                                    "location": label,
                                    "cell": period_ref,
                                    "field": "period",
                                    "status": "fail",
                                    "reason": "기간 불일치: 본문 "
                                    + claimed_period
                                    + " ≠ 워크북 "
                                    + str(expected_year)
                                    + "년 ("
                                    + value_ref
                                    + "="
                                    + str(expected_d)
                                    + ")",
                                    "claim": clause.strip(),
                                    "expected": str(expected_year) + "년",
                                    "claimed": claimed_period,
                                }
                            )
                        continue
                bad = False
                if not unit_ok:
                    bad = True
                    issues.append(
                        {
                            "location": label,
                            "cell": unit_ref,
                            "field": "unit",
                            "status": "fail",
                            "reason": "단위 불일치: 본문 "
                            + (claimed_unit or "(없음)")
                            + " ≠ 워크북 "
                            + expected_unit
                            + " ("
                            + unit_ref
                            + ")",
                            "claim": clause.strip(),
                            "expected": expected_unit,
                            "claimed": claimed_unit,
                        }
                    )
                if (
                    claimed_d is None
                    or effective_d is None
                    or claimed_d != effective_d
                ):
                    bad = True
                    issues.append(
                        {
                            "location": label,
                            "cell": value_ref,
                            "field": "value",
                            "status": "fail",
                            "reason": "수치 불일치: 본문 "
                            + raw_amount
                            + " ≠ 워크북 "
                            + str(expected_d)
                            + " ("
                            + value_ref
                            + ")"
                            + decl_note,
                            "claim": clause.strip(),
                            "expected": str(expected_d),
                            "claimed": raw_amount,
                        }
                    )
                if not bad:
                    found_match = True
        if not found_match and not any(
            i["location"] == label and i.get("status") == "fail" for i in issues
        ):
            issues.append(
                {
                    "location": label,
                    "cell": value_ref,
                    "status": "fail",
                    "reason": "본문에 일치/충돌하는 재무 수치 없음",
                }
            )
    return issues


def inspect_outputs(root, p, lineage=None):
    """Inspect current XLSX outputs.

    ``lineage`` is the core-validated ``superseded_by`` verdict map
    (``gg_core.output_lineage``); when omitted it is computed here so
    standalone callers get the same selection.  Only valid chain terminals
    are history-skipped — broken relations stay under inspection.
    """
    if lineage is None:
        from gg_core import output_lineage

        lineage = output_lineage(p)
    issues = []
    for oid, output in p.get("outputs", {}).items():
        if output.get("format") != "xlsx":
            continue
        if (lineage.get(oid) or {}).get("state") == "history":
            continue
        profile = output.get("workbook_profile")
        if not profile_supports(profile):
            issues.append(("school_excel_17_scope",
                           "17시트 검사 미지원/미실행: " + str(profile or "프로필 근거 없음")))
            if output.get("layout_kind") != "provided_template":
                try:
                    from gg_excel_template import read_template
                    read_template(local(root, output["path"]))
                except (OSError, ValueError, KeyError) as error:
                    issues.append(("xlsx_integrity", str(error)))
                continue
        try:
            from openpyxl import load_workbook
        except ImportError:
            issues.append(("xlsx_dependency", "XLSX 검사에 필요한 openpyxl 미설치"))
            continue
        layout_kind = output.get("layout_kind")
        if layout_kind is not None and layout_kind != "provided_template":
            issues.append(("school_excel_17", "알 수 없는 XLSX layout_kind"))
            continue
        # A provided template may share the exemplar's 17 sheet names while
        # having a different cell contract.  It is inspected through its
        # native Excel manifest and generic workbook integrity only.
        if output.get("layout_kind") == "provided_template":
            if output.get("stale") is True:
                issues.append(
                    (
                        "school_excel_17",
                        "provided_template 출력이 입력 변경으로 stale: 재검사 필요: "
                        + str(output.get("id")),
                    )
                )
                continue
            try:
                path = local(root, output["path"])
                if not path.is_file():
                    raise ValueError("제공 템플릿 출력 파일 없음")
                output_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if output.get("file_hash") and output["file_hash"] != output_hash:
                    raise ValueError("제공 템플릿 출력 file_hash 불일치")

                native = output.get("native_manifest")
                if not isinstance(native, dict):
                    raise ValueError("provided_template native_manifest 필요")
                manifest_path = native.get("path")
                manifest_sha = native.get("sha256")
                if (
                    not isinstance(manifest_path, str)
                    or not manifest_path.strip()
                    or Path(manifest_path).is_absolute()
                    or not isinstance(manifest_sha, str)
                    or not re.fullmatch(r"[0-9a-fA-F]{64}", manifest_sha)
                ):
                    raise ValueError("native_manifest 경로·sha256 형식 오류")
                manifest_file = local(root, manifest_path)
                if not manifest_file.is_file():
                    raise ValueError("native manifest 파일 없음")
                actual_manifest_sha = hashlib.sha256(
                    manifest_file.read_bytes()
                ).hexdigest()
                if actual_manifest_sha.lower() != manifest_sha.lower():
                    raise ValueError("native manifest sha256 불일치")
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("native manifest 객체 필요")
                if manifest.get("status") != "converted":
                    raise ValueError("native manifest status converted 필요")
                if manifest.get("engine") != "Microsoft Excel":
                    raise ValueError("native manifest engine Excel 필요")
                if str(manifest.get("engine_stderr", "")).strip():
                    raise ValueError("native Excel stderr 기록됨")
                if manifest.get("error") or (
                    manifest.get("returncode") not in (None, 0)
                ):
                    raise ValueError("native Excel 실행 오류 기록됨")
                if manifest.get("published_office_sha256") != output_hash:
                    raise ValueError("native manifest published_office_sha256 불일치")
                validation = manifest.get("validation")
                if not isinstance(validation, dict):
                    raise ValueError("native manifest validation 필요")
                if validation.get("school_validation") != "source_template_preservation_only":
                    raise ValueError("provided 템플릿 school_validation 상태 오류")
                if validation.get("structure_issues"):
                    raise ValueError("provided 템플릿 structure_issues 존재")
                if validation.get("school_issues"):
                    raise ValueError("provided 템플릿 school_issues 존재")
                excel = validation.get("excel")
                if (
                    not isinstance(excel, dict)
                    or excel.get("valid") is not True
                    or excel.get("sha256") != output_hash
                ):
                    raise ValueError("native manifest validation.excel valid/sha256 오류")
                # Generic integrity gate: do not apply legacy coordinates or
                # school-specific formulas to a supplied template.
                wb_formula = load_workbook(path, read_only=True, data_only=False)
                try:
                    names = wb_formula.sheetnames
                    if profile_supports(profile) and len(names) != 17:
                        raise ValueError("school_17_sheet_v1 17시트 무결성 불일치")
                    error_tokens = re.compile(
                        r"#(?:REF!|VALUE!|DIV/0!|N/A|NAME\?|NUM!|NULL!|SPILL!|CALC!|BLOCKED!|CONNECT!|UNKNOWN!)"
                    )
                    for ws in wb_formula.worksheets:
                        for row in ws.iter_rows():
                            for cell in row:
                                value = cell.value
                                if cell.data_type == "e" or (
                                    isinstance(value, str)
                                    and cell.data_type == "f"
                                    and error_tokens.search(value)
                                ):
                                    raise ValueError(
                                        f"native workbook error token: {ws.title}!{cell.coordinate}"
                                    )
                    wb_values = load_workbook(path, read_only=True, data_only=True)
                    try:
                        for ws in wb_values.worksheets:
                            for row in ws.iter_rows():
                                for cell in row:
                                    value = cell.value
                                    if cell.data_type == "e" or (
                                        isinstance(value, str) and error_tokens.fullmatch(value.strip())
                                    ):
                                        raise ValueError(
                                            f"native workbook cached error: {ws.title}!{cell.coordinate}"
                                        )
                    finally:
                        wb_values.close()
                finally:
                    wb_formula.close()
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                UnicodeError,
                zipfile.BadZipFile,
                json.JSONDecodeError,
            ) as e:
                issues.append(("school_excel_17", f"provided_template: {e}"))
            continue
        if output.get("stale") is True:
            issues.append(
                (
                    "school_excel_17",
                    "입력 변경으로 stale한 출력: 재검사 필요: "
                    + str(output.get("id") or oid),
                )
            )
            continue
        try:
            path = local(root, output["path"])
            names_book = load_workbook(path, read_only=True)
            try:
                names = names_book.sheetnames
            finally:
                names_book.close()
            if len(names) != 17 and not (set(names) & set(SCHOOL_SHEETS)):
                continue
            for item in inspect_school_workbook(path):
                issues.append(("school_excel_17", item["reason"]))
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            UnicodeError,
            zipfile.BadZipFile,
            json.JSONDecodeError,
        ) as e:
            issues.append(("school_excel_17", str(e)))
    return issues


# ---------------------------------------------------------------------------
# P4 reference-fill section (stage-g005 D07/D09): audited statistical
# source_refs resolve through the provenance chain in gg_rda_research
# before any workbook cell is filled.  No P3 workbook formula or
# calculation path is touched by this section.


def resolve_statistical_source_refs(source_refs, *, resolver=None):
    """P4 adapter: resolve audited statistical refs for reference fill.

    ``resolver`` is an optional ``gg_rda_research.resolver_context()`` dict;
    omitted -> the deployed packs context is built lazily.  Returns
    ``{"resolved_source_refs": [...], "conflicts": [...]}`` — conflicts are
    reported, never silently filled."""
    import gg_rda_research

    return gg_rda_research.resolve_statistical_refs(
        source_refs, context=resolver)


def source_ref_labels(source_refs, *, resolver=None):
    """Display labels for the ``출처:`` fill: statistical refs that
    resolved through the provenance chain carry their physical-line
    binding ``(line N)``; plain refs render verbatim.  Callers must run
    ``validate`` (which fails closed on conflicts) before filling."""
    import gg_rda_research

    labels = []
    for ref in source_refs:
        if gg_rda_research.is_statistical_ref(ref):
            verdict = gg_rda_research.resolve_pack_ref(
                ref, context=resolver) if isinstance(ref, str) else (
                gg_rda_research.audit_source_ref(None, ref,
                                                 context=resolver))
            if verdict["status"] == "resolved":
                line = verdict["audit_key"]["physical_jsonl_line_1based"]
                labels.append("%s (물리행 %d)" % (verdict["source_ref"], line))
                continue
        labels.append(ref if isinstance(ref, str) else str(ref))
    return labels
