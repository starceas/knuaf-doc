"""물가·임금·판매가 상승률 가정 — 검증·연평균 변화율(CAGR)·적용 배율.

순수 함수 모듈: 출력 파일 쓰기·전역 상태 변경 없음(레지스트리 JSON 읽기만).
계약(P2/P5, R1 §3, V1-01~04 처분):
  - 상승률 r은 **확인 관측 수열의 끝점 CAGR로만** 정해진다
    (r=(V_end/V_start)^(1/n)-1, 음수 허용). 명시 `rate` 입력은 검산용이며
    CAGR과 허용오차(RATE_ABS/REL_TOLERANCE) 내 일치할 때만 받는다.
    관측이 없거나 2점 미만인 수열의 r, 관측과 무관한 임의 r은 거부한다.
  - 계획연도 Y의 배율은 (1+r)^(Y - application_base_year). 적용 횟수는
    연도 차이와 같다(2022→2025 = 3회, 2회가 아님). 같은 가격에 같은 률을
    두 번 곱하지 않는다.
  - 역할은 세 개: "general"(자재·수도광열 등 경비), "wage"(노무비),
    "sales"(판매가→매출). 판매가에 총지수·다른 작목 수열 자동 대입 금지 —
    판매가 상승 미반영은 {"status": "not_applied", "reason": ...} 명시
    선택일 때만.
  - 모든 수열 항목은 공식 출처 계약(`check_source_entry`)을 통과해야
    한다: 기관 종류(agency_kind)·공식 도메인 url·evidence_locator·
    allowed_roles 목록·dimensions·유한 양수 관측값, 관측/미확인 연도
    교집합 금지.
  - sales 역할은 수열 차원(crop·cultivation·region·product·unit·
    channel·grade)이 계획 대상 차원(target_dimensions)과 일치해야 한다.

spec 계약:
  spec["price_assumptions"] = {"general": A, "wage": A, "sales": A}
  A = {"status": "not_applied", "reason": str}
    | {"source_id": str,                  # 레지스트리 또는 price_sources id
       "base_year": int|null,             # 지수 100 기준연도, 키 자체는 필수
       "application_base_year": int,      # 입력 가격·비용이 기준하는 연도
       "rate": float,                     # 선택 — 검산용(수열 CAGR과 일치해야 함)
       "observation": {"start_year": int, "end_year": int},  # 선택
       "target_dimensions": dict}         # 선택 — 계획 대상 차원 명시
  spec["price_sources"] = [수열 항목, ...]  # 학생이 직접 조회한 공식 출처 선언
                                           # (레지스트리와 같은 스키마)
  spec["price_dimensions"] = dict          # 선택 — 계획 대상 차원(crops·region·
                                           # cultivation 키에 더해 병합)
"""

import json
import math
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

D = Decimal

ROLES = ("general", "wage", "sales")
ROLE_LABELS = {"general": "일반물가", "wage": "임금", "sales": "판매가"}

# 공식 출처 계약(V1-02): 기관 종류와 URL 도메인 규칙.
OFFICIAL_AGENCY_KINDS = (
    "national_statistics",
    "central_government",
    "local_government",
    "public_corporation",
    "public_research_institute",
)
OFFICIAL_URL_SUFFIXES = ("go.kr", "kosis.kr", "kamis.or.kr", "re.kr")

# 판매 수열 차원의 정본 키 → 허용 별칭(수열 dimensions/target 양쪽).
DIMENSION_ALIASES = {
    "crop": ("crop", "작목"),
    "cultivation": ("cultivation", "form", "재배형태"),
    "region": ("region", "지역"),
    "product": ("product", "item", "상품"),
    "unit": ("unit", "단위"),
    "channel": ("channel", "stage", "trade_stage", "거래단계"),
    "grade": ("grade", "등급"),
}

# 명시 r 검산 허용오차: |declared - cagr| <= max(ABS, REL*|cagr|).
RATE_ABS_TOLERANCE = 5e-4
RATE_REL_TOLERANCE = 0.05


def registry_path():
    return (
        Path(__file__).resolve().parents[1]
        / "references"
        / "official-price-series.json"
    )


def _is_int_year(v):
    return type(v) is int and not isinstance(v, bool)


def _finite_positive(v):
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and v > 0
    )


def _official_url(url):
    """모호한 입력을 먼저 거부하고 한 번 파싱한 HTTPS 호스트만 판정한다."""
    if not isinstance(url, str) or not url:
        return False
    # 공백·제어·유니코드와 브라우저가 자동으로 escape하는 문자는 받지
    # 않는다. 유니코드 경로·질의는 명시 percent encoding으로 표현한다.
    if not re.fullmatch(r"[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+", url):
        return False
    if re.search(r"%(?![A-Fa-f0-9]{2})", url):
        return False
    # urlsplit/WHATWG의 자동 보정(공백 제거·역슬래시·IDNA·userinfo·
    # host percent decoding·빈 port 등)이 끼어들 수 없는 authority만 허용.
    if not url.lower().startswith("https://"):
        return False
    try:
        parsed = urlsplit(url)
        authority = parsed.netloc
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", authority):
            return False
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    # 빈 ?/#를 Python은 직렬화 때 없애고 WHATWG는 보존한다.
    if ("?" in url.split("#", 1)[0] and not parsed.query
            or "#" in url and not parsed.fragment):
        return False
    # WHATWG는 (percent encoded 형태 포함) 점 경로를 제거한다.
    # Python이 그대로 보존하는 이런 locator는 정규화로 추측하지 않는다.
    if any(re.fullmatch(r"(?:\.|%2e){1,2}", part, re.IGNORECASE)
           for part in parsed.path.split("/")):
        return False
    host = host.lower()
    labels = host.split(".")
    if len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        or label.startswith("xn--") for label in labels
    ):
        return False
    return any(host == s or host.endswith("." + s) for s in OFFICIAL_URL_SUFFIXES)


def check_source_entry(entry, *, origin="source"):
    """수열 항목 스키마·공식 출처 계약 검증 → id. 실패 시 ValueError."""
    if not isinstance(entry, dict):
        raise ValueError(f"{origin}: 출처 항목은 객체 필요")
    sid = entry.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError(f"{origin}: 출처 id 문자열 필요")
    if not isinstance(entry.get("agency"), str) or not entry["agency"].strip():
        raise ValueError(f"{origin}:{sid}: 기관(agency) 필요")
    if entry.get("agency_kind") not in OFFICIAL_AGENCY_KINDS:
        raise ValueError(
            f"{origin}:{sid}: 기관 종류(agency_kind)는 "
            f"{'|'.join(OFFICIAL_AGENCY_KINDS)} 중 하나 필요 "
            "— 공식 기관 수열만 허용"
        )
    if not isinstance(entry.get("statistic_name"), str) or not entry[
        "statistic_name"
    ].strip():
        raise ValueError(f"{origin}:{sid}: 통계명(statistic_name) 필요")
    if not _official_url(entry.get("url")):
        raise ValueError(
            f"{origin}:{sid}: url은 공식 기관 도메인의 https 주소 필요 "
            f"({'|'.join(OFFICIAL_URL_SUFFIXES)} 계열)"
        )
    if "evidence_url" in entry and not _official_url(entry["evidence_url"]):
        raise ValueError(f"{origin}:{sid}: evidence_url 도메인이 공식 계열이 아님")
    if not isinstance(entry.get("evidence_locator"), str) or not entry[
        "evidence_locator"
    ].strip():
        raise ValueError(
            f"{origin}:{sid}: 근거 위치(evidence_locator) 필요"
        )
    if "base_year" not in entry:
        raise ValueError(f"{origin}:{sid}: 기준연도(base_year) 필드 필요")
    by = entry["base_year"]
    if by is not None and not _is_int_year(by):
        raise ValueError(f"{origin}:{sid}: 기준연도는 정수 또는 null 필요")
    if not isinstance(entry.get("unit"), str) or not entry["unit"].strip():
        raise ValueError(f"{origin}:{sid}: 단위(unit) 필요")
    dims = entry.get("dimensions")
    if not isinstance(dims, dict) or not dims or not all(
        isinstance(k, str) and isinstance(v, str) and v.strip()
        for k, v in dims.items()
    ):
        raise ValueError(
            f"{origin}:{sid}: 차원(dimensions) 비어 있지 않은 문자열 사전 필요"
        )
    allowed = entry.get("allowed_roles")
    if not isinstance(allowed, list) or any(
        r not in ROLES for r in allowed
    ):
        raise ValueError(
            f"{origin}:{sid}: allowed_roles는 {'|'.join(ROLES)}의 "
            "목록 필요(빈 목록 = 참고 전용)"
        )
    obs = entry.get("observations")
    if obs is None:
        obs = []
    if not isinstance(obs, list):
        raise ValueError(f"{origin}:{sid}: observations는 목록 필요")
    seen = set()
    for o in obs:
        if not isinstance(o, dict):
            raise ValueError(f"{origin}:{sid}: 관측 항목은 객체 필요")
        y = o.get("year")
        v = o.get("value")
        if not _is_int_year(y):
            raise ValueError(f"{origin}:{sid}: 관측 연도는 정수 필요")
        if y in seen:
            raise ValueError(f"{origin}:{sid}: 관측 연도 중복 {y}")
        seen.add(y)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"{origin}:{sid}: 관측값은 수치 필요 ({y}년)")
        if not math.isfinite(v) or not (v > 0):
            raise ValueError(
                f"{origin}:{sid}: 관측값은 유한한 양수 필요 ({y}년={v})"
            )
    unconfirmed = entry.get("unconfirmed_years")
    if unconfirmed is not None:
        if not isinstance(unconfirmed, list) or not all(
            _is_int_year(y) for y in unconfirmed
        ):
            raise ValueError(
                f"{origin}:{sid}: unconfirmed_years는 정수 연도 목록 필요"
            )
        overlap = seen & set(unconfirmed)
        if overlap:
            raise ValueError(
                f"{origin}:{sid}: 관측 연도와 미확인 연도가 겹침 "
                f"{sorted(overlap)} — 확인된 값만 observations에 둘 것"
            )
    return sid


def load_registry(path=None):
    """번들 레지스트리 → {id: entry}. 스키마 위반·중복 id는 ValueError."""
    p = Path(path) if path else registry_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema") != "knuaf-official-price-series/v1":
        raise ValueError("official-price-series 스키마 불일치")
    out = {}
    for entry in data.get("series") or []:
        sid = check_source_entry(entry, origin="registry")
        if sid in out:
            raise ValueError(f"레지스트리 출처 id 중복: {sid}")
        out[sid] = entry
    return out


def resolve_series(source_id, registry=None, student_sources=None):
    """출처 id → 수열 항목. 레지스트리·학생 선언 양쪽에 있으면 동일성 확인.

    id는 정규화하지 않는다 — 대소문자·공백 변형은 미등록 id로 거부.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("출처 id 문자열 필요")
    if registry is None:
        registry = load_registry()
    student_sources = student_sources or {}
    if source_id in registry and source_id in student_sources:
        reg, stu = registry[source_id], student_sources[source_id]
        for key in ("agency", "unit", "base_year"):
            if reg.get(key) != stu.get(key):
                raise ValueError(
                    f"출처 {source_id}: 기관·단위·기준연이 다른 수열을 "
                    f"같은 id로 혼합 비교할 수 없음 ({key}: "
                    f"{reg.get(key)!r} vs {stu.get(key)!r})"
                )
        return reg
    if source_id in student_sources:
        return student_sources[source_id]
    if source_id in registry:
        return registry[source_id]
    raise ValueError(
        f"알 수 없는 출처 id: {source_id} "
        "(번들 official-price-series 또는 spec.price_sources에 선언 필요)"
    )


def confirmed_series(entry):
    """{year: value} — 확인된 관측만(unconfirmed 연도는 포함하지 않음)."""
    return {o["year"]: float(o["value"]) for o in entry.get("observations") or []}


def check_role(entry, role, *, origin=""):
    """수열이 role 용도로 허용됐는지 검증. sales에 총합 지수는 거부."""
    if role not in ROLES:
        raise ValueError(f"{origin}: 역할은 {'|'.join(ROLES)} 중 하나 필요")
    allowed = entry.get("allowed_roles")
    if not isinstance(allowed, list) or role not in allowed:
        raise ValueError(
            f"{origin}: 출처 {entry.get('id')}는 {ROLE_LABELS[role]} "
            "역할에 사용 불가(총지수·용도 불일치·참고 전용 자동 대입 금지)"
        )
    if role == "sales" and entry.get("aggregate"):
        raise ValueError(
            f"{origin}: 출처 {entry.get('id')}는 총합 지수라 "
            "특정 작목의 판매가 상승률로 사용 불가"
        )


def check_unit(entry, expected, *, origin=""):
    """수열의 물리 단위가 기대 단위와 일치하는지 검증."""
    choices = (
        {expected}
        if isinstance(expected, str)
        else set(expected or ())
    )
    if entry.get("unit") not in choices:
        raise ValueError(
            f"{origin}: 출처 {entry.get('id')}의 단위 "
            f"{entry.get('unit')!r}는 {'/'.join(sorted(choices))}가 아님"
        )


def check_base_year(declared, entry, *, origin=""):
    """선언 기준연도 ↔ 수열 기준연도 대조 → 실효 base_year.

    지수 수열(기준연도 있는 수열)은 선언이 수열과 일치해야 하고,
    명목 수열(base_year: null)에는 null 선언만 허용한다.
    """
    src_by = entry.get("base_year")
    if src_by is not None:
        if not _is_int_year(declared) or declared != src_by:
            raise ValueError(
                f"{origin}: 기준연도 혼합 비교 금지 — "
                f"출처 기준연도 {src_by}, 입력 {declared!r}"
            )
        return src_by
    if declared is not None:
        raise ValueError(
            f"{origin}: 명목 수열(지수 기준연도 없음)에는 "
            "base_year: null만 허용"
        )
    return None


def canonical_dimensions(mapping):
    """차원 사전 → 정본 키 사전. 별칭·한글 키를 DIMENSION_ALIASES로 통일."""
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError("차원(dimensions/target_dimensions)은 사전 필요")
    canon = {}
    for raw_key, value in mapping.items():
        if not isinstance(raw_key, str):
            raise ValueError("차원 키는 문자열 필요")
        key = raw_key.strip()
        hit = None
        for canon_key, aliases in DIMENSION_ALIASES.items():
            if key == canon_key or key in aliases:
                hit = canon_key
                break
        if hit is None:
            hit = key  # 알 수 없는 차원도 보존해 누락·충돌을 대조한다.
        if isinstance(value, str):
            if not value.strip():
                raise ValueError(f"차원 {raw_key}: 빈 문자열 불가")
            normalized = value.strip()
        elif isinstance(value, list) and value and all(
            isinstance(v, str) and v.strip() for v in value
        ):
            normalized = [v.strip() for v in value]
        else:
            raise ValueError(
                f"차원 {raw_key}: 비어 있지 않은 문자열 또는 문자열 목록 필요"
            )
        _merge_dimensions(canon, {hit: normalized}, origin="차원 별칭")
    return canon


def _dimension_values(value):
    """단일 문자열과 같은 값 하나의 목록은 동일한 차원 선언이다."""
    return frozenset(value if isinstance(value, list) else [value])


def _merge_dimensions(target, additions, *, origin="계획 차원"):
    """빠진 차원만 보완한다. 이미 확정한 값의 변경·목록 확장은 거부한다."""
    for key, value in additions.items():
        if key in target and _dimension_values(target[key]) != _dimension_values(value):
            raise ValueError(
                f"{origin}: {key} 차원 불일치·충돌 — "
                f"확정 계획 {target[key]!r}, 보조 선언 {value!r}"
            )
        if key not in target:
            target[key] = value


def _series_dimensions(entry):
    """수열의 정본 차원. dimensions를 정본화하고 최상위 unit을 병합."""
    canon = canonical_dimensions(entry.get("dimensions") or {})
    if isinstance(entry.get("unit"), str):
        _merge_dimensions(canon, {"unit": entry["unit"]}, origin="수열 단위")
    return canon


def check_dimensions(entry, target_dimensions=None, *, required=(), origin=""):
    """수열 차원이 계획 대상 차원과 일치하는지 구조화 검증.

    - target이 선언한 각 차원은 수열도 같은 값을 명시해야 한다
      (수열 측 부재·값 불일치는 거부).
    - 문자열 또는 문자열 목록의 값 집합이 정확히 같아야 한다.
      region "전국"도 확정 지역값이며 와일드카드가 아니다.
    - ``required``: target·수열 양쪽에 반드시 있어야 하는 정본 키.
    - crop을 필수로 요구하는 판매 경로는 수열의 모든 차원(단위 포함)이
      계획에 확정돼 있어야 한다. 일반·임금의 부분 차원 대조는 유지한다.
    """
    series_dims = _series_dimensions(entry)
    target = canonical_dimensions(target_dimensions or {})
    sid = entry.get("id")
    required_keys = set(required)
    if "crop" in required_keys:
        required_keys.update(series_dims)
    for key, want in target.items():
        got = series_dims.get(key)
        if got is None:
            raise ValueError(
                f"{origin}: 계획이 {key}={want!r}을 요구하지만 "
                f"출처 {sid}가 해당 차원을 명시하지 않음"
            )
        if _dimension_values(got) != _dimension_values(want):
            raise ValueError(
                f"{origin}: 출처 {sid}의 {key}={got!r}가 계획 대상 "
                f"{want!r}과 불일치 — 조건이 다른 수열 전용 금지"
            )
    for key in sorted(required_keys):
        if key not in target:
            raise ValueError(
                f"{origin}: 계획 대상 차원에 {key} 선언이 없어 "
                f"출처 {sid}의 조건 일치를 확인할 수 없음"
            )
        if key not in series_dims:
            raise ValueError(
                f"{origin}: 출처 {sid}가 {key} 차원을 명시하지 않아 "
                "계획 조건과 대조할 수 없음"
            )


def check_observations(entry, observation_years, observed_values, *, origin=""):
    """선언 (연도, 값) 쌍이 수열의 확정 관측과 일치하는지 대조 → {year: float}."""
    if not isinstance(observation_years, (list, tuple)) or not isinstance(
        observed_values, (list, tuple)
    ):
        raise ValueError(f"{origin}: 관측 연도·값은 목록 필요")
    if len(observation_years) != len(observed_values) or len(observation_years) < 2:
        raise ValueError(
            f"{origin}: 관측 연도·값은 같은 길이의 2점 이상 목록 필요"
        )
    series = confirmed_series(entry)
    out = {}
    for y, v in zip(observation_years, observed_values):
        if not _is_int_year(y):
            raise ValueError(f"{origin}: 관측 연도는 정수 필요")
        if not _finite_positive(v):
            raise ValueError(f"{origin}: 관측값은 유한한 양수 필요 ({y}년)")
        if y not in series or series[y] != float(v):
            raise ValueError(
                f"{origin}: 관측 {y}년={v}이(가) 출처 "
                f"{entry.get('id')}의 확정 관측과 불일치"
            )
        out[y] = float(v)
    return out


def cagr(start_value, end_value, interval_years):
    """연평균 변화율 r = (end/start)^(1/n) - 1 (음수 허용)."""
    sv, ev, n = float(start_value), float(end_value), int(interval_years)
    if not math.isfinite(sv) or not math.isfinite(ev) or sv <= 0 or ev <= 0:
        raise ValueError("CAGR 시작·끝값은 유한한 양수 필요")
    if n <= 0:
        raise ValueError("CAGR 연간격은 양수 필요")
    r = (ev / sv) ** (1.0 / n) - 1.0
    if not math.isfinite(r):
        raise ValueError("CAGR 결과가 유한하지 않음")
    return r


def resolve_series_rate(entry, observation=None, declared_rate=None, *, origin=""):
    """확정 관측 수열의 끝점 CAGR을 적용률로 확정한다.

    - ``observation``: {"start_year", "end_year"} — 생략 시 확정 수열 전체 기간.
      끝점 연도는 확정 관측에 있어야 한다.
    - ``declared_rate``: 검산용 명시 r. CAGR과 허용오차 내 일치만 허용하며
      적용값은 항상 수열 CAGR이다.
    - 반환: {"rate", "declared_rate", "observation": {start_year, end_year,
      start_value, end_value}, "interval_years"}
    """
    series = confirmed_series(entry)
    if observation is None:
        if len(series) < 2:
            raise ValueError(
                f"{origin}: 출처 {entry.get('id')}의 확인 관측이 "
                "2점 미만 — 관측 없는 상승률은 산출·입력 불가"
            )
        ys = sorted(series)
        sy, ey = ys[0], ys[-1]
    else:
        if not isinstance(observation, dict):
            raise ValueError(f"{origin}: 관측기간은 객체 필요")
        sy, ey = observation.get("start_year"), observation.get("end_year")
        for name, y in (("start_year", sy), ("end_year", ey)):
            if not _is_int_year(y):
                raise ValueError(f"{origin}: 관측 {name}은 정수 연도 필요")
        if ey - sy <= 0:
            raise ValueError(
                f"{origin}: 관측 연간격은 양수 필요 ({sy}~{ey})"
            )
        missing = [y for y in (sy, ey) if y not in series]
        if missing:
            raise ValueError(
                f"{origin}: 관측 연도 값 확인 불가 {missing} — "
                "미확인·0·보간값으로 대체하지 않음"
            )
    if len(series) < 2:
        raise ValueError(
            f"{origin}: 출처 {entry.get('id')}의 확인 관측이 "
            "2점 미만 — 관측 없는 상승률은 산출·입력 불가"
        )
    ref = cagr(series[sy], series[ey], ey - sy)

    declared = None
    if declared_rate is not None:
        if isinstance(declared_rate, bool) or not isinstance(
            declared_rate, (int, float, str)
        ):
            raise ValueError(f"{origin}: 상승률 r은 수치 필요")
        try:
            declared = float(declared_rate)
        except (TypeError, ValueError):
            raise ValueError(f"{origin}: 상승률 r은 수치 필요") from None
        if not math.isfinite(declared):
            raise ValueError(f"{origin}: 상승률 r은 유한값 필요")
        if declared <= -1.0:
            raise ValueError(
                f"{origin}: 상승률 r은 -1 초과 필요(배율 양수)"
            )
        tol = max(RATE_ABS_TOLERANCE, RATE_REL_TOLERANCE * abs(ref))
        if abs(declared - ref) > tol:
            raise ValueError(
                f"{origin}: 명시 상승률 {declared:.6f}가 출처 "
                f"{entry.get('id')} 관측 CAGR {ref:.6f}와 불일치 "
                f"(허용오차 {tol:.6f}) — 관측 수열로 산출한 r만 허용"
            )
    return {
        "rate": ref,
        "declared_rate": declared,
        "observation": {
            "start_year": sy,
            "end_year": ey,
            "start_value": series[sy],
            "end_value": series[ey],
        },
        "interval_years": ey - sy,
    }


def plan_target_dimensions(spec, assumption=None):
    """계획 대상 차원 → 정본 키 사전.

    spec.crops와 최상위 작목·지역·재배·상품·거래·등급이 권위다.
    price_dimensions·target_dimensions·역할별 target은 미확정 차원만
    보완하며 같은 키의 다른 값은 거부한다. spec.unit은 재무표 표시 단위로
    판매 단위와 별개다(판매 단위는 보조 차원에서 명시한다).
    """
    target = {}
    crops = []
    declared_crops = spec.get("crops", [])
    if not isinstance(declared_crops, list):
        raise ValueError("계획 crops 차원은 작목 목록 필요")
    for c in declared_crops:
        name = c.get("name") if isinstance(c, dict) else c
        if not isinstance(name, str) or not name.strip():
            raise ValueError("계획 crops 차원은 비어 있지 않은 작목 이름 필요")
        crops.append(name.strip())
    if crops:
        target["crop"] = crops
    for key, aliases in DIMENSION_ALIASES.items():
        if key == "unit":
            continue
        for alias in aliases:
            if alias in spec:
                _merge_dimensions(target, canonical_dimensions({alias: spec[alias]}))
    _merge_dimensions(target, canonical_dimensions(spec.get("price_dimensions")))
    _merge_dimensions(target, canonical_dimensions(spec.get("target_dimensions")))
    block = spec.get("price_assumptions")
    if isinstance(block, dict):
        for role in ROLES:
            raw = block.get(role)
            if isinstance(raw, dict):
                _merge_dimensions(
                    target, canonical_dimensions(raw.get("target_dimensions")),
                    origin=f"price_assumptions.{role}",
                )
    if isinstance(assumption, dict):
        _merge_dimensions(target, canonical_dimensions(assumption.get("target_dimensions")))
    return target


def _normalize_role(role, raw, *, registry, student_sources, plan_years,
                    target_dims):
    label = ROLE_LABELS[role]
    origin = f"price_assumptions.{role}"
    if raw is None:
        raise ValueError(
            f"{origin} 필요 — {label} 상승률을 입력하거나 "
            "{'status': 'not_applied', 'reason': ...}로 명시 선택"
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{origin}: 객체 필요")
    target = dict(target_dims)
    _merge_dimensions(target, canonical_dimensions(raw.get("target_dimensions")), origin=origin)
    status = raw.get("status", "applied")
    if status == "not_applied":
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"{origin}: 미적용 선택은 사유(reason) 필요"
            )
        return {"role": role, "status": "not_applied", "reason": reason.strip()}
    if status != "applied":
        raise ValueError(f"{origin}: status는 applied|not_applied")

    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(f"{origin}: 출처 id(source_id) 필요")
    source = resolve_series(source_id, registry, student_sources)

    if "base_year" not in raw:
        raise ValueError(
            f"{origin}: 기준연도(base_year) 필드 필요 "
            "(지수 100 기준연도, 해당 없으면 null)"
        )
    base_year = check_base_year(raw["base_year"], source, origin=origin)
    check_role(source, role, origin=origin)

    series_dims = _series_dimensions(source)
    if role == "sales":
        check_dimensions(
            source, target, required=("crop",), origin=origin)
    else:
        crop_dim = series_dims.get("crop")
        target_crop = target.get("crop")
        if crop_dim and target_crop:
            wants = (
                target_crop if isinstance(target_crop, list)
                else [target_crop]
            )
            if crop_dim not in wants:
                raise ValueError(
                    f"{origin}: 출처 {source['id']}는 {crop_dim} 수열 — "
                    "다른 작목에 전용 금지"
                )

    aby = raw.get("application_base_year")
    if not _is_int_year(aby):
        raise ValueError(
            f"{origin}: 계획 적용 기초연도"
            "(application_base_year) 정수 필요 — 입력 가격·비용이 기준하는 연도"
        )
    if aby > plan_years[0]:
        raise ValueError(
            f"{origin}: 적용 기초연도 {aby}는 첫 계획연도 "
            f"{plan_years[0]} 이하여야 함"
        )

    resolved = resolve_series_rate(
        source,
        observation=raw.get("observation"),
        declared_rate=raw.get("rate"),
        origin=origin,
    )

    return {
        "role": role,
        "status": "applied",
        "rate": resolved["rate"],
        "rate_source": "series_cagr",
        "declared_rate": resolved["declared_rate"],
        "reference_cagr": resolved["rate"],
        "source_id": source["id"],
        "statistic_name": source.get("statistic_name"),
        "item_name": source.get("item_name"),
        "agency": source.get("agency"),
        "unit": source.get("unit"),
        "base_year": base_year,
        "observation": resolved["observation"],
        "interval_years": resolved["interval_years"],
        "application_base_year": aby,
        "dimensions": series_dims,
        "target_dimensions": target,
    }


def normalize_price_assumptions(spec, plan_years, *, registry=None):
    """spec → {"general": A, "wage": A, "sales": A} 정규화. 실패 시 ValueError.

    ``plan_years``: 5개 계획연도 목록(validate의 plan_years 결과).
    ``registry``는 테스트용 주입 지점; 생략 시 번들 JSON을 읽는다.
    """
    if not isinstance(plan_years, (list, tuple)) or not plan_years:
        raise ValueError("계획연도 목록 필요")
    block = spec.get("price_assumptions")
    if not isinstance(block, dict):
        raise ValueError(
            "price_assumptions 입력 필요 — 일반물가·임금·판매가 상승률을 "
            "출처 id·기준연도·관측기간·적용 기초연도와 함께 입력 "
            "(상승률은 출처 수열의 관측 CAGR로 산출; "
            "기존 'inflation' 단일 배율 입력은 폐기)"
        )
    if registry is None:
        registry = load_registry()
    student_sources = {}
    raw_sources = spec.get("price_sources") or []
    if not isinstance(raw_sources, list):
        raise ValueError("price_sources는 출처 항목 목록 필요")
    for entry in raw_sources:
        sid = check_source_entry(entry, origin="price_sources")
        if sid in student_sources:
            raise ValueError(f"price_sources 출처 id 중복: {sid}")
        student_sources[sid] = entry

    target_dims = plan_target_dimensions(spec)

    return {
        role: _normalize_role(
            role,
            block.get(role),
            registry=registry,
            student_sources=student_sources,
            plan_years=plan_years,
            target_dims=target_dims,
        )
        for role in ROLES
    }


def applications(assumption, plan_year):
    """계획연도의 적용 횟수 = plan_year - application_base_year."""
    if assumption["status"] != "applied":
        return 0
    return plan_year - assumption["application_base_year"]


def multiplier(assumption, plan_year):
    """해당 계획연도의 누적 배율 (1+r)^n (float). 비유한 결과는 ValueError."""
    if assumption["status"] != "applied":
        return 1.0
    try:
        m = (1.0 + assumption["rate"]) ** applications(assumption, plan_year)
    except OverflowError:
        m = float("inf")
    if not math.isfinite(m):
        raise ValueError(
            f"price_assumptions.{assumption['role']}: 배율이 유한하지 않음"
        )
    return m


def multiplier_d(assumption, plan_year):
    """Decimal 버전 — 독립 기대값 계산용."""
    return D(str(multiplier(assumption, plan_year)))


def footnote(assumption):
    """표 아래 `출처:` 근거 문구 — 통계명·기준연도·관측기간·산식 표기."""
    label = ROLE_LABELS[assumption["role"]]
    if assumption["status"] != "applied":
        return f"* {label} 상승률 미적용(명시 선택): {assumption['reason']}"
    obs = assumption["observation"]
    by = assumption["base_year"]
    basis = f"기준연도 {by}" if by is not None else "지수 기준연도 해당 없음"
    ref = (
        f"{assumption['reference_cagr'] * 100:.4f}%"
        if assumption["reference_cagr"] is not None
        else "확인 불가"
    )
    return (
        f"* {label} 상승률 {assumption['rate'] * 100:.4f}% 적용 "
        f"(출처: {assumption['statistic_name']} {assumption['item_name']} "
        f"[{assumption['source_id']}], {basis}, "
        f"관측기간 {obs['start_year']}~{obs['end_year']}년, "
        f"관측 연평균 변화율 {ref}, "
        f"산식 r=(끝값/시작값)^(1/{assumption['interval_years']})-1, "
        f"적용 기초연도 {assumption['application_base_year']}년)"
    )
