"""PR④ B단계 잠금 시험 — econ 2025·mafra 2024 source 부여 + 광주·인천 + E9 1행.

설계 정본: ``<work>/2026-11-11-official-data/w4src/DESIGN-PR4-SOURCE.md`` §4
(시험 1~6).  기대값은 코드가 아니라 **동결 대응표**(A단계 산출, 웹 검토 통과)와
main ``d663bf8`` 팩에서만 온다.  구현(``tools/apply_source_map_pr4.py``)은 보지
않고 "적용된 팩이 이래야 한다"만 잠근다(발주 분리 원칙).

동결 입력
  * ``fixtures/source-map-pr4.tsv``  — 5,695행(econ 2,707 + mafra 2,988), 열 순서 §1.2
  * ``fixtures/region-fix-pr4.tsv``  — 광주·인천 2행(souce-map 행 수에 미포함, N-02)

바탕(main ``d663bf8``)은 git 역사에서 읽지 않는다. ``base_blob``이 현재 팩
바이트에서 이 PR이 한 일(source 삽입·E9 두 칸·광주·인천 level 정정)을 거꾸로
되돌려 바탕 바이트를 만들고, 그 sha256이 동결 상수와 같을 때만 바탕으로 인정한다.
되돌림은 동결 대응표(class=sourced)·region-fix 표·E9 상수에서만 유도하고 적용기는
보지 않는다. 어긋나면 skip이 아니라 FAIL이다 — 대응표가 엉뚱한 줄을 가리키지
않는다는 증거(시험 3)와 E9 before/after 지문(시험 5), 유령 상태 불변(시험 6 ①)이
모두 바탕 바이트 전체를 전제로 하기 때문이다. git 역사가 없는 얕은 클론에서도
같은 경로를 탄다.
"""

import csv
import hashlib
import json
import sys
import unittest
from functools import lru_cache
from pathlib import Path

KNUAF_DOC = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = KNUAF_DOC / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_candidates as candidates  # noqa: E402
import gg_rda_provenance as provenance  # noqa: E402

ECON_PACK_ID = "rda.econ.2025"
MAFRA_PACK_ID = "mafra.specialty.production.2024"
REGIONAL_PACK_ID = "rda.income.regional.2025"
REGIONAL_PACK_ID_2024 = "rda.income.regional.2024"

PACK_RELPATH = {
    ECON_PACK_ID: "skills/knuaf-doc/references/benchmark-packs/"
                  "common/rda-econ-2025/records.jsonl",
    MAFRA_PACK_ID: "skills/knuaf-doc/references/benchmark-packs/"
                   "majors/specialty_crops/"
                   "mafra-specialty-production-2024/records.jsonl",
    REGIONAL_PACK_ID: "skills/knuaf-doc/references/benchmark-packs/"
                      "common/rda-income-regional-2025/records.jsonl",
    REGIONAL_PACK_ID_2024: "skills/knuaf-doc/references/benchmark-packs/"
                           "common/rda-income-regional-2024/records.jsonl",
}
PACK_DIR = {
    ECON_PACK_ID: KNUAF_DOC / "references" / "benchmark-packs" / "common"
                  / "rda-econ-2025",
    MAFRA_PACK_ID: KNUAF_DOC / "references" / "benchmark-packs" / "majors"
                   / "specialty_crops" / "mafra-specialty-production-2024",
}
PACK_LINES = {ECON_PACK_ID: 2707, MAFRA_PACK_ID: 2988}
REGIONAL_LINES = {REGIONAL_PACK_ID: 49, REGIONAL_PACK_ID_2024: 365}

# E6: pack_id -> 수록 문서 PDF 지문(via.pdf_sha256 기대값, manifest
# source_pdf_sha256과 같은 값).
PACK_PDF_SHA256 = {
    ECON_PACK_ID:
        "38e38ffb8b478584b9f76a8b9d98d56c7cdeb8eb18a2200aef26639a72935a83",
    MAFRA_PACK_ID:
        "0974c3d30aa5c1abd795be0af453f4de3bd4e569285c250537897b820d2aa1d5",
}
ECON_VIA_TITLE = "2025 농업과학기술 경제성 분석 기준자료집"
MAFRA_VIA_TITLE = "2024 특용작물 생산실적"

# E1 표의 동결 survey id 20개. 대응표가 쓰지 않은 id도 여기 들어 있다 —
# 목록 동결과 "쓰인 id"는 별개다(시험 2는 쓰인 id가 이 집합에 있음만 본다).
FROZEN_SURVEYS = frozenset({
    "rda.income",
    "kostat.crop_production",
    "kostat.production_cost",
    "kostat.farm_price",
    "nsc.seed_supply_price",
    "nh.seed_center_price",
    "nh.fertilizer_sale_price",
    "nh.pesticide_price",
    "opinet.fuel_price",
    "kepco.farm_tariff",
    "kamis.wholesale",
    "at.flower_auction",
    "rda.fertilizer_spec",
    "nas.fertilizer_prescription",
    "nias.livestock_econ",
    "market.garak_wholesale",
    "market.gyeongdong_herbal",
    "market.daegu_herbal",
    "market.yeongcheon_herbal",
    "mafra.specialty_admin",
})

CLASSES = ("sourced", "unsourced_no_note", "unsourced_insufficient",
           "unsourced_mixed")
CLASS_COUNTS = {"sourced": 5147, "unsourced_no_note": 196,
                "unsourced_insufficient": 280, "unsourced_mixed": 72}
TOTAL_MAP_ROWS = 5695

# source 키는 대응표 §1.2 열과 E11의 survey_label(null 필수)에서 유도한다.
# section·table·source_url은 대응표에 열이 없으므로 쓰면 지어낸 출처다.
SOURCE_KEYS = frozenset({
    "publisher", "survey", "survey_label", "aggregation_group", "data_year", "published",
    "document", "via",
})
VIA_KEYS = frozenset({"title", "pdf_page", "pdf_sha256"})

GHOST_LINES = frozenset(list(range(105, 122)) + list(range(2581, 2588)))
GHOST_MARK = "유령(원문 없음)"
E9_LINE = 195
E9_MARK = "정정 전 검산"
E9_REGION_NAME = "전남"
E9_FARMS = "1477"
E9_AREA_HA = "1117"
E9_PDF_PAGE = 33          # 원문 물리 쪽
E9_PRINTED_PAGE = "29"    # 원문 인쇄 쪽

METROPOLITAN_SIDO = ("광주", "인천")
REGION_FIX_LINES = {REGIONAL_PACK_ID: (40, 41)}

# 바탕(main ``d663bf8``) records 파일 sha256 — PR④가 손댄 네 팩. 시험이 현재
# 바이트에서 PR④ 편집을 거꾸로 되돌린 결과가 이 값과 같을 때만 그 바이트를
# 바탕으로 인정한다. git 역사가 없는 얕은 클론에서도 같은 경로를 타도록
# 값 자체를 동결한다(전체 바이트 지문이라 git show보다 약하지 않다).
BASE_RECORDS_SHA256 = {
    ECON_PACK_ID:
        "a5dbf6db2aed41254d2a05460d1950a3cdc2a8db51c0d54e82d035d91bd2253a",
    MAFRA_PACK_ID:
        "fc1c0b35d63a67dc5a794128f873272d72f0850e6d6470e7d9501270eafdd4be",
    REGIONAL_PACK_ID:
        "406ac11feb62641a756de171c2c70af2ed2a89026be8c533568232bf98c41ab3",
    REGIONAL_PACK_ID_2024:
        "de3e3d0b1334a23bb3a1760313c108040f8887c60778bfb688587d9d109430ea",
}

# PR④가 source를 넣은 팩 — 이 팩들의 sourced 줄에서만 ``"source"`` 멤버를
# 되돌린다. 나머지 팩은 원래 source가 있으므로 건드리지 않는다.
SOURCE_ADDED_PACKS = frozenset({ECON_PACK_ID, MAFRA_PACK_ID})

# E9 정정 전/후 값 — 동결 상수(적용기 import 금지). before는 mafra 195행의
# 원문 두 칸이고, after는 이 PR이 넣은 값이다. 되돌림은 after→before.
E9_METRICS_BEFORE = {"farms": None, "area_ha": "14771117"}
E9_METRICS_AFTER = {"farms": E9_FARMS, "area_ha": E9_AREA_HA}

# 유령 24행의 바탕(main ``d663bf8``) manifest 상태 칸 — git 역사 없이 비교하려고
# 값 자체를 동결한다. 값은 ``git show d663bf8:<econ manifest.json>``에서 읽어
# (physical_line, catalog_status, source_observation.observation_status,
# audit_key.raw_line_sha256) 그대로 적었다. 지문(sha256) 대신 값을 그대로
# 적는 이유: 어긋날 때 어느 행 어느 칸이 바뀌었는지 바로 보이고, 투영 JSON의
# 직렬화 옵션(정렬·구분자)이 계산기와 시험 사이에서 어긋날 여지가 없다.
# 이 PR은 이 세 칸을 바꾸지 않았으므로 d663bf8 값 = 현재 값이다.
GHOST_BASE_STATE = {
    105: ("quarantined", "correction_proposed",
          "f4a92a9617ac22923922ff3d89c3af8f6456388d7057c585825a9ed544408818"),
    106: ("quarantined", "correction_proposed",
          "90ad1e9401bdb590d4a40a6564fd3e0d8873fbea71c3ac0216c9b898eb720831"),
    107: ("quarantined", "correction_proposed",
          "97dd2f5c97222ea9a031eaaa42a5f4e4e94b685fbde0be4be1cff1e73e3cc9fc"),
    108: ("quarantined", "unresolved",
          "b519fb2d0cb4d2b0164d547edc2c15fca2f46e2efd7ff45259d474e3db87b94c"),
    109: ("quarantined", "unresolved",
          "37b7d23311f474e53218206ba98bae9f6e4b6d4b9be766e9110c8db30ba8f874"),
    110: ("quarantined", "unresolved",
          "97e92c30fcd5904b17e1bb79797b59db3425e5e898497443bb5319c0547843c8"),
    111: ("quarantined", "unresolved",
          "61f9721fe9c2e8de9f089433d585a9dd0c88d9427b5d0217f2f2c1f802ac2a7e"),
    112: ("quarantined", "correction_proposed",
          "9d6b3d7b92a6843c2dca50df84e4764ce8494d3a93ea5adbcbc029c84df2a53e"),
    113: ("quarantined", "unresolved",
          "66c4a9e6f6975fd9da107101fb98439752251997d65ba10d4ab85745af5e50d4"),
    114: ("quarantined", "correction_proposed",
          "9ac144b8cb1de0e161586cc2b4b846cd36958d79891892390e4878e6cfc39d10"),
    115: ("quarantined", "unresolved",
          "5f76687a43f18de01d115230c2eac2309ab4c7c00fd1f24e9dd2286cc514b4e8"),
    116: ("quarantined", "unresolved",
          "1d945fa1a71e100b2c7a1ec506444d6dd3e2f53855cb55447258faa656d911be"),
    117: ("quarantined", "correction_proposed",
          "077964f816370e61dffdc45663e105fce2e6da2fdc46c6f9860c913d2787cfea"),
    118: ("quarantined", "correction_proposed",
          "c1bc2df830deceb1545802e97fac426e1695c8451a4b17cd70631a568d111f7a"),
    119: ("quarantined", "unresolved",
          "173efdd3a11079c7f51b26d8311b87c23068184993b0c1a51e755272433942c1"),
    120: ("quarantined", "unresolved",
          "37a38084bed8e36073e419c9d2ff3fa7f5322a724a670a48e0a4a681eac68b6d"),
    121: ("quarantined", "unresolved",
          "6fafc5274d4775caf9270c73e048098a4318aba324ea5b01c45737405a0e75d4"),
    2581: ("quarantined", "unresolved",
           "ef3eefc0e69cf534b1b8b7bb81f4f4a28d8147ebbd2b9bdde07c6cb24d7de3cd"),
    2582: ("quarantined", "unresolved",
           "0d179393597a0db433445ee108d3f456004ffdd3ba27661c3003e9998c3b2b80"),
    2583: ("quarantined", "unresolved",
           "1aa5b6f18bada9660a270e1c9dcd70e31b3b3ce963695d55c29a8a1d443ce03d"),
    2584: ("quarantined", "unresolved",
           "68ba0275cf365f5b0515d91ef7a2f9dd43b51130a09d84a74c1b2e1a75047042"),
    2585: ("quarantined", "unresolved",
           "865ec76bd938f16284a3adb65269ef416b16ed03ffbaa481dd6215be7e47b715"),
    2586: ("quarantined", "unresolved",
           "23ba0cdbd962bd8a37e591e57899cf955db63ffccac84efcf19c4258cc74c93f"),
    2587: ("quarantined", "unresolved",
           "37f44fd4806f7c491a1b01148853a400d1267aae62e9ad6a2c12170c47edf3b6"),
}

MAP_HEADER = [
    "pack_id", "physical_line", "raw_line_sha256_before", "record_id",
    "kind", "locator.section", "locator.pdf_page", "row_label", "class",
    "survey", "publisher", "document", "data_year", "published",
    "via_document", "via_pdf_page", "via_pdf_sha256", "note_page",
    "note_text", "image_checked", "reason",
]

# source-map.tsv 열 인덱스(0-based)
(_C_PACK, _C_LINE, _C_SHA, _C_RID, _C_KIND, _C_SECTION, _C_PDF_PAGE,
 _C_LABEL, _C_CLASS, _C_SURVEY, _C_PUBLISHER, _C_DOC, _C_YEAR, _C_PUBLISHED,
 _C_VIA_DOC, _C_VIA_PAGE, _C_VIA_SHA, _C_NOTE_PAGE, _C_NOTE_TEXT,
 _C_IMAGE, _C_REASON) = range(len(MAP_HEADER))


# ---------------------------------------------------------------------------
# 동결 입력 로딩 (파일 읽기만 — git 역사 불필요)


def _read_tsv(path):
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    return rows[0], [r for r in rows[1:] if r]


@lru_cache(maxsize=1)
def source_map_rows():
    """dict로 줄을 합치기 전의 동결 TSV 원시 행."""
    return _read_tsv(FIXTURES / "source-map-pr4.tsv")


@lru_cache(maxsize=1)
def source_map():
    """``{(pack_id, physical_line): row}`` — 중복 소실 전 검사한다."""
    header, rows = source_map_rows()
    if len(rows) != TOTAL_MAP_ROWS:
        raise AssertionError("대응표 원시 행 수: %d != %d"
                             % (len(rows), TOTAL_MAP_ROWS))
    table = {}
    for row in rows:
        if len(row) != len(MAP_HEADER):
            raise AssertionError("대응표 열 수: %r" % row)
        key = (row[_C_PACK], int(row[_C_LINE]))
        if key in table:
            raise AssertionError("대응표 중복 감사 키: %r" % (key,))
        table[key] = row
    return header, table


@lru_cache(maxsize=1)
def region_fix_map():
    """동결 region-fix 2행 — source-map과 별 파일(N-02)."""
    header, rows = _read_tsv(FIXTURES / "region-fix-pr4.tsv")
    return header, rows


@lru_cache(maxsize=None)
def sourced_lines(pack_id):
    """대응표에서 class=sourced인 물리 줄 번호 — 되돌림이 source를 지울 줄."""
    _, table = source_map()
    return frozenset(
        line for (pid, line), row in table.items()
        if pid == pack_id and row[_C_CLASS] == "sourced")


def _scan_string_end(text, quote_at):
    """``text[quote_at] == '"'`` — 그 JSON 문자열을 닫는 따옴표 위치."""
    i = quote_at + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i
        i += 1
    raise AssertionError("닫히지 않은 JSON 문자열: %r"
                         % text[quote_at:quote_at + 60])


def _match_brace(text, open_at):
    """``text[open_at] == '{'`` — 짝이 맞는 닫는 중괄호 위치(문자열 무시)."""
    depth = 0
    i = open_at
    while i < len(text):
        char = text[i]
        if char == '"':
            i = _scan_string_end(text, i) + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError("닫히지 않은 JSON 객체: %r"
                         % text[open_at:open_at + 60])


def _top_level_member_span(text, name):
    """최상위 객체의 ``name`` 멤버 ``(키 시작, 값 끝)`` — 없으면 None.

    깊이를 세어 최상위 키만 집고, 값이 객체면 짝 중괄호까지, 문자열이면
    닫는 따옴표까지를 끝으로 본다. 문자열 안의 중괄호는 따옴표 스캔이
    건너뛰므로 오탐이 없다.
    """
    key = json.dumps(name)
    depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == '"':
            end = _scan_string_end(text, i)
            if depth == 1 and text[i:end + 1] == key:
                if text[end + 1:end + 3] != ": ":
                    raise AssertionError(
                        "%r 멤버 구분자가 ': '가 아니다: %r"
                        % (name, text[end + 1:end + 4]))
                value_at = end + 3
                if text[value_at] == "{":
                    return i, _match_brace(text, value_at) + 1
                if text[value_at] == '"':
                    return i, _scan_string_end(text, value_at) + 1
                raise AssertionError(
                    "%r 값이 객체·문자열이 아니다: %r"
                    % (name, text[value_at:value_at + 20]))
            i = end + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        i += 1
    return None


def _strip_source_member(text):
    """PR④가 넣은 최상위 ``"source"`` 멤버 하나를 지운다(구분자까지)."""
    span = _top_level_member_span(text, "source")
    if span is None:
        raise AssertionError("source 멤버가 없다: %r" % text[:120])
    start, end = span
    if text[end:end + 2] == ", ":
        return text[:start] + text[end + 2:]
    if text[start - 2:start] == ", ":
        return text[:start - 2] + text[end:]
    raise AssertionError("source 멤버 앞뒤 구분자를 못 찾았다: %r"
                         % text[max(0, start - 20):end + 20])


def _revert_member(text, parent, field, before, after):
    """``parent`` 객체 안 ``field`` 값을 ``after``→``before``로 되돌린다."""
    span = _top_level_member_span(text, parent)
    if span is None:
        raise AssertionError("%r 멤버가 없다: %r" % (parent, text[:120]))
    start, end = span
    body = text[start:end]
    old = "%s: %s" % (json.dumps(field), json.dumps(after))
    new = "%s: %s" % (json.dumps(field), json.dumps(before))
    if body.count(old) != 1:
        raise AssertionError(
            "%r.%r가 %r로 정확히 한 번 있지 않다(=%d): %r"
            % (parent, field, after, body.count(old), body[:200]))
    return text[:start] + body.replace(old, new) + text[end:]


@lru_cache(maxsize=None)
def base_blob(pack_id):
    """``d663bf8`` 팩 바이트 — 현재 바이트에서 PR④ 편집을 거꾸로 되돌린다.

    되돌림 대상은 셋뿐이고 전부 동결 상수·동결 표에서 온다: sourced 줄의
    ``source`` 멤버, mafra 195행의 두 칸, 지역 2025 40·41행의 level.
    결과 전체 sha256이 ``BASE_RECORDS_SHA256``과 같을 때만 바탕으로 인정한다.
    """
    if pack_id not in BASE_RECORDS_SHA256:
        raise AssertionError("바탕 상수가 없는 팩: %s" % pack_id)
    current = (REPO_ROOT / PACK_RELPATH[pack_id]).read_bytes()
    sourced = sourced_lines(pack_id) if pack_id in SOURCE_ADDED_PACKS else ()
    level_fixes = {int(row[1]): (row[4], row[5])
                   for row in region_fix_map()[1] if row[0] == pack_id}
    out = []
    for number, raw in enumerate(current.splitlines(keepends=True), start=1):
        body = raw.decode("utf-8")
        newline = ""
        if body.endswith("\n"):
            body, newline = body[:-1], "\n"
        record = json.loads(body)  # 기대값 - 되돌린 결과와 같아야 한다
        if number in sourced:
            body = _strip_source_member(body)
            record.pop("source")
        if pack_id == MAFRA_PACK_ID and number == E9_LINE:
            for field, after in E9_METRICS_AFTER.items():
                if (record.get("metrics") or {}).get(field) != after:
                    raise AssertionError(
                        "mafra:%d metrics.%s가 E9 정정 후 값 %r이 아니다"
                        % (number, field, after))
                body = _revert_member(body, "metrics", field,
                                      E9_METRICS_BEFORE[field], after)
                record["metrics"][field] = E9_METRICS_BEFORE[field]
        if number in level_fixes:
            before_level, after_level = level_fixes[number]
            if (record.get("region") or {}).get("level") != after_level:
                raise AssertionError(
                    "%s:%d region.level이 %r이 아니다"
                    % (pack_id, number, after_level))
            body = _revert_member(body, "region", "level",
                                  before_level, after_level)
            record["region"]["level"] = before_level
        if json.loads(body) != record:
            raise AssertionError(
                "%s:%d 되돌림이 기대 레코드와 다르다" % (pack_id, number))
        out.append(body.encode("utf-8") + newline.encode("utf-8"))
    blob = b"".join(out)
    digest = hashlib.sha256(blob).hexdigest()
    if digest != BASE_RECORDS_SHA256[pack_id]:
        raise AssertionError(
            "%s: 되돌린 바탕 바이트 sha256이 동결 상수와 다르다 — 기대 %s, "
            "실제 %s(이 PR 밖에서 팩이 바뀌었거나 되돌림 규칙이 어긋났다)"
            % (pack_id, BASE_RECORDS_SHA256[pack_id], digest))
    return blob


@lru_cache(maxsize=None)
def _raw_lines(pack_id, *, base=False):
    blob = (base_blob(pack_id) if base else
            (REPO_ROOT / PACK_RELPATH[pack_id]).read_bytes())
    return blob.splitlines(keepends=True)


@lru_cache(maxsize=None)
def base_records(pack_id):
    """``d663bf8`` 팩의 물리 줄 순서 레코드 리스트."""
    blob = base_blob(pack_id)
    return [json.loads(line) for line in blob.decode("utf-8").splitlines()]


@lru_cache(maxsize=None)
def new_records(pack_id):
    """현재 워크트리(적용 후) 팩의 레코드 리스트."""
    path = REPO_ROOT / PACK_RELPATH[pack_id]
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()]


def _manifest(pack_id):
    path = PACK_DIR[pack_id] / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_rows(pack_id):
    return {int(row["physical_line"]): row
            for row in _manifest(pack_id)["rows"]}


def _ghost_view(row):
    """manifest 행 1개를 동결 표와 같은 모양(상태 3칸)으로 뽑는다."""
    return (
        row.get("catalog_status"),
        (row.get("source_observation") or {}).get("observation_status"),
        (row.get("audit_key") or {}).get("raw_line_sha256"),
    )


def _expected_source(row):
    """대응표 1행 -> 기대 source dict (값이 없는 키는 None으로 남긴다)."""
    def _value(idx):
        cell = row[idx]
        return None if cell == "null" else cell

    expected = {
        "survey": _value(_C_SURVEY),
        "survey_label": None,
        "publisher": _value(_C_PUBLISHER),
        "document": _value(_C_DOC),
        "data_year": int(_value(_C_YEAR)) if _value(_C_YEAR) else None,
        "published": _value(_C_PUBLISHED),
    }
    if _value(_C_SURVEY) is not None:
        expected["aggregation_group"] = "%s.%d" % (
            expected["survey"], expected["data_year"])
    via_title = _value(_C_VIA_DOC)
    if via_title is None:
        expected["via"] = None
    else:
        expected["via"] = {
            "title": via_title,
            "pdf_page": int(_value(_C_VIA_PAGE)),
            "pdf_sha256": _value(_C_VIA_SHA),
        }
    return expected


def _non_null(mapping):
    return {k: v for k, v in mapping.items() if v is not None}


class Pr4SourceMapTestCase(unittest.TestCase):
    """공통 로더 + 대응표 자체 정합."""

    maxDiff = None

    def map_row(self, pack_id, line):
        _, table = source_map()
        key = (pack_id, int(line))
        self.assertIn(key, table, "대응표에 %s 물리 %d행이 없다" % key)
        return table[key]

    def new_record(self, pack_id, line):
        return new_records(pack_id)[int(line) - 1]

    def base_record(self, pack_id, line):
        return base_records(pack_id)[int(line) - 1]


# ---------------------------------------------------------------------------
# 시험 1 — 정확 대응


class SourceAppliedExactlyTests(Pr4SourceMapTestCase):
    """§4-1: 새 팩 물리 줄의 source가 대응표 그 줄 기대값과 필드 단위 일치.

    ``unsourced_*`` 줄은 source 없음.  줄 순서·행 수 불변도 함께 잠근다.
    """

    def test_map_covers_every_pack_line_once(self):
        header, table = source_map()
        self.assertEqual(header, MAP_HEADER)
        self.assertEqual(len(table), TOTAL_MAP_ROWS)
        for pack_id, expected_lines in PACK_LINES.items():
            lines = sorted(line for pid, line in table if pid == pack_id)
            self.assertEqual(len(lines), expected_lines, pack_id)
            self.assertEqual(lines, list(range(1, expected_lines + 1)),
                             "%s: 물리 줄이 1..N 연속이 아니다" % pack_id)

    def test_pack_line_count_and_order_unchanged(self):
        for pack_id, expected_lines in PACK_LINES.items():
            records = new_records(pack_id)
            self.assertEqual(len(records), expected_lines, pack_id)
            base = base_records(pack_id)
            self.assertEqual(len(base), expected_lines,
                             "%s: 되돌린 바탕 줄 수" % pack_id)
            self.assertEqual([r.get("record_id") for r in records],
                             [r.get("record_id") for r in base],
                             "%s: 줄 순서/행 신원이 바뀌었다" % pack_id)
            self.assertEqual([r.get("pack_id") for r in records],
                             [pack_id] * expected_lines)

    def test_source_matches_frozen_map_field_by_field(self):
        _, table = source_map()
        for (pack_id, line), row in sorted(table.items()):
            record = self.new_record(pack_id, line)
            expected = _expected_source(row)
            label = "%s:%d %s" % (pack_id, line, record.get("record_id"))
            source = record.get("source")
            if row[_C_CLASS] != "sourced":
                self.assertIsNone(
                    source,
                    "%s: class=%s인데 source가 붙었다(지어낸 출처)"
                    % (label, row[_C_CLASS]))
                continue
            self.assertIsInstance(source, dict, "%s: source 없음" % label)
            self.assertTrue(
                set(source) <= SOURCE_KEYS,
                "%s: 대응표에 없는 source 키 %s"
                % (label, sorted(set(source) - SOURCE_KEYS)))
            self.assertIn("survey_label", source, "%s: E11 키 누락" % label)
            self.assertIsNone(source["survey_label"],
                              "%s: E11 조사 이름을 지어내면 안 된다" % label)
            self.assertEqual(
                _non_null(source), _non_null(expected),
                "%s: 대응표 기대값과 다르다" % label)

    def test_sourced_and_unsourced_counts_match_map(self):
        _, table = source_map()
        counts = {name: 0 for name in CLASSES}
        for row in table.values():
            counts[row[_C_CLASS]] += 1
        self.assertEqual(counts, CLASS_COUNTS)
        for pack_id in PACK_LINES:
            with_source = sum(
                1 for (pid, _), row in table.items()
                if pid == pack_id and row[_C_CLASS] == "sourced")
            actual = sum(1 for r in new_records(pack_id)
                         if r.get("source") is not None)
            self.assertEqual(actual, with_source, pack_id)


# ---------------------------------------------------------------------------
# 시험 2 — sourced 행의 스키마·연도·via


class SourceShapeTests(Pr4SourceMapTestCase):
    """§4-2: 스키마 검사 통과, data_year==basis.year, aggregation_group 규칙,
    survey ∈ 동결 목록, via 형식·팩별 지문(E6 3갈래).
    """

    def _sourced_rows(self):
        _, table = source_map()
        for (pack_id, line), row in sorted(table.items()):
            if row[_C_CLASS] == "sourced":
                yield pack_id, line, row

    def test_sourced_source_is_well_formed(self):
        seen = 0
        for pack_id, line, row in self._sourced_rows():
            record = self.new_record(pack_id, line)
            source = record["source"]
            label = "%s:%d" % (pack_id, line)
            self.assertIn("survey_label", source, "%s: E11 키 누락" % label)
            self.assertIsNone(source["survey_label"],
                              "%s: E11 survey_label은 null만 허용" % label)
            # PR③ 코드의 source 계약(스키마 서술의 기계 검사) — 통과해야 한다.
            self.assertIsNone(
                candidates._source_reason(record),
                "%s: source 계약 위반(%s)"
                % (label, candidates._source_reason(record)))
            self.assertIn(source["survey"], FROZEN_SURVEYS,
                          "%s: 동결 목록 밖 survey" % label)
            self.assertIsInstance(source["data_year"], int)
            self.assertEqual(source["data_year"],
                             record["basis"]["year"],
                             "%s: data_year != basis.year(E2)" % label)
            self.assertEqual(source["aggregation_group"],
                             "%s.%d" % (source["survey"],
                                        source["data_year"]))
            # 대응표가 밝힌 survey·data_year와도 같아야 한다(이중 잠금).
            self.assertEqual(source["survey"], row[_C_SURVEY], label)
            self.assertEqual(source["data_year"], int(row[_C_YEAR]), label)
            seen += 1
        self.assertEqual(seen, CLASS_COUNTS["sourced"])

    def test_via_follows_pack_rule(self):
        for pack_id, line, row in self._sourced_rows():
            record = self.new_record(pack_id, line)
            source = record["source"]
            label = "%s:%d %s" % (pack_id, line, source.get("survey"))
            via = source.get("via")
            if pack_id == ECON_PACK_ID:
                self.assertIsInstance(via, dict, "%s: econ은 via 필수" % label)
            elif row[_C_SURVEY] == "mafra.specialty_admin":
                self.assertIsNone(
                    via, "%s: 생산실적은 via 없음(N-01)" % label)
                continue
            else:
                self.assertIsInstance(
                    via, dict, "%s: mafra 가격은 via 필수(N-01)" % label)
            self.assertEqual(set(via), VIA_KEYS, label)
            self.assertIsInstance(via["pdf_page"], int, label)
            self.assertEqual(via["pdf_page"], int(row[_C_VIA_PAGE]), label)
            self.assertEqual(via["pdf_sha256"], PACK_PDF_SHA256[pack_id],
                             "%s: via 지문이 팩 PDF와 다르다(N-04)" % label)
            self.assertEqual(via["pdf_sha256"], row[_C_VIA_SHA], label)
            self.assertEqual(via["title"],
                             ECON_VIA_TITLE if pack_id == ECON_PACK_ID
                             else MAFRA_VIA_TITLE, label)

    def test_unsourced_rows_carry_no_provenance(self):
        """unsourced 행은 바탕 레코드의 키 집합 그대로 — source 말고는
        어떤 출처 흔적도 덧붙지 않는다(설계 §3 예외 밖 필드 금지)."""
        _, table = source_map()
        for (pack_id, line), row in sorted(table.items()):
            if row[_C_CLASS] == "sourced":
                continue
            record = self.new_record(pack_id, line)
            base = self.base_record(pack_id, line)
            label = "%s:%d" % (pack_id, line)
            self.assertNotIn("source", record, "%s: unsourced 행에 source" % label)
            self.assertEqual(
                set(record) - set(base), set(),
                "%s: 바탕에 없던 키가 붙었다 %s"
                % (label, sorted(set(record) - set(base))))
            self.assertEqual(record, base,
                             "%s: unsourced 행이 바탕과 다르다" % label)
            self.assertEqual(
                _raw_lines(pack_id)[line - 1],
                _raw_lines(pack_id, base=True)[line - 1],
                "%s: 미변경 행 바이트가 바뀌었다" % label)


# ---------------------------------------------------------------------------
# 시험 3 — 대응표 자체


class SourceMapSelfConsistencyTests(Pr4SourceMapTestCase):
    """§4-3: 5,695행, 식별열·class 누락 0, 빈 문자열 0, region-fix 분리,
    raw_line_sha256_before가 d663bf8 팩 줄과 일치.
    """

    def test_identity_and_class_columns_filled(self):
        _, rows = source_map_rows()
        for row in rows:
            self.assertEqual(len(row), len(MAP_HEADER), row)
            for idx in range(_C_CLASS + 1):
                self.assertNotIn(
                    row[idx], ("", "null"),
                    "%s:%s 식별열/class 누락(열 %s)"
                    % (row[_C_PACK], row[_C_LINE], MAP_HEADER[idx]))

    def test_no_empty_cell_and_class_domain(self):
        _, table = source_map()
        for (pack_id, line), row in table.items():
            for idx, cell in enumerate(row):
                self.assertNotEqual(
                    cell, "",
                    "%s:%d 빈 문자열 칸(열 %s) — 값 없음은 null(N-05)"
                    % (pack_id, line, MAP_HEADER[idx]))
            self.assertIn(row[_C_CLASS], CLASSES, "%s:%d" % (pack_id, line))
            self.assertIn(row[_C_IMAGE], ("y", "n"),
                          "%s:%d image_checked" % (pack_id, line))

    def test_unsourced_rows_are_fully_null(self):
        _, table = source_map()
        for (pack_id, line), row in table.items():
            if row[_C_CLASS] == "sourced":
                continue
            for idx in (_C_SURVEY, _C_PUBLISHER, _C_DOC, _C_YEAR,
                        _C_PUBLISHED, _C_VIA_DOC, _C_VIA_PAGE, _C_VIA_SHA):
                self.assertEqual(
                    row[idx], "null",
                    "%s:%d class=%s인데 %s 값이 있다"
                    % (pack_id, line, row[_C_CLASS], MAP_HEADER[idx]))

    def test_unsourced_no_note_has_no_note_text(self):
        _, table = source_map()
        for (pack_id, line), row in table.items():
            if row[_C_CLASS] == "unsourced_no_note":
                self.assertEqual(
                    row[_C_NOTE_TEXT], "null",
                    "%s:%d: 주석 없음인데 note_text" % (pack_id, line))

    def test_sourced_rows_carry_survey_and_year(self):
        _, table = source_map()
        for (pack_id, line), row in table.items():
            if row[_C_CLASS] != "sourced":
                continue
            self.assertIn(row[_C_SURVEY], FROZEN_SURVEYS,
                          "%s:%d" % (pack_id, line))
            self.assertTrue(row[_C_YEAR].isdigit(),
                            "%s:%d data_year" % (pack_id, line))
            self.assertTrue(row[_C_PDF_PAGE].isdigit(),
                            "%s:%d locator.pdf_page" % (pack_id, line))

    def test_via_sha_matches_pack_fingerprint(self):
        _, table = source_map()
        for (pack_id, line), row in table.items():
            if row[_C_VIA_SHA] == "null":
                continue
            self.assertEqual(
                row[_C_VIA_SHA], PACK_PDF_SHA256[pack_id],
                "%s:%d via_pdf_sha256가 팩 PDF 지문과 다르다" % (pack_id, line))
            self.assertTrue(row[_C_VIA_PAGE].isdigit(),
                            "%s:%d via_pdf_page" % (pack_id, line))

    def test_ghost_rows_are_exactly_the_frozen_twentyfour(self):
        _, table = source_map()
        ghosts = {(pid, line) for (pid, line), row in table.items()
                  if row[_C_REASON].startswith(GHOST_MARK)}
        self.assertEqual(len(ghosts), 24)
        self.assertEqual(
            ghosts,
            {(ECON_PACK_ID, line) for line in GHOST_LINES})
        for key in ghosts:
            self.assertEqual(table[key][_C_CLASS], "unsourced_no_note", key)

    def test_e9_row_is_marked_by_pre_fix_reason(self):
        row = self.map_row(MAFRA_PACK_ID, E9_LINE)
        self.assertTrue(row[_C_REASON].startswith(E9_MARK),
                        "E9 행 reason 앞머리 표지 누락(N3-01)")
        self.assertEqual(row[_C_CLASS], "sourced")

    def test_region_fix_is_separate_and_not_in_source_map(self):
        header, rows = region_fix_map()
        self.assertEqual(
            header, ["pack_id", "physical_line", "raw_line_sha256_before",
                     "record_id", "before_level", "after_level", "reason"])
        self.assertEqual(len(rows), 2)
        seen = set()
        for row in rows:
            self.assertEqual(row[0], REGIONAL_PACK_ID)
            seen.add(int(row[1]))
            self.assertEqual(row[4], "provincial", row)
            self.assertEqual(row[5], "metropolitan", row)
        self.assertEqual(seen, set(REGION_FIX_LINES[REGIONAL_PACK_ID]))
        _, table = source_map()
        for line in seen:
            self.assertNotIn(
                (REGIONAL_PACK_ID, line), table,
                "region-fix 행이 source-map에 섞였다(N-02)")

    def test_raw_line_sha256_before_matches_base_pack(self):
        _, table = source_map()
        base_blobs = {
            pack_id: base_blob(pack_id).splitlines(keepends=True)
            for pack_id in PACK_LINES
        }
        for pack_id, lines in base_blobs.items():
            self.assertEqual(len(lines), PACK_LINES[pack_id], pack_id)
        for (pack_id, line), row in sorted(table.items()):
            self.assertEqual(
                hashlib.sha256(base_blobs[pack_id][line - 1]).hexdigest(),
                row[_C_SHA],
                "%s:%d raw_line_sha256_before가 되돌린 바탕 줄과 다르다"
                % (pack_id, line))

    def test_region_fix_raw_sha_matches_base_regional_pack(self):
        _, rows = region_fix_map()
        blob = base_blob(REGIONAL_PACK_ID)
        lines = blob.splitlines(keepends=True)
        self.assertEqual(len(lines), REGIONAL_LINES[REGIONAL_PACK_ID])
        for row in rows:
            digest = hashlib.sha256(lines[int(row[1]) - 1]).hexdigest()
            self.assertEqual(digest, row[2], row[3])


# ---------------------------------------------------------------------------
# 시험 4 — 광주·인천


class MetropolitanRegionTests(Pr4SourceMapTestCase):
    """§4-4: 광주·인천 metropolitan, 지역 팩 2개에 provincial 광역시 0."""

    def test_gwangju_incheon_rows_are_metropolitan(self):
        _, rows = region_fix_map()
        records = new_records(REGIONAL_PACK_ID)
        self.assertEqual(len(records), REGIONAL_LINES[REGIONAL_PACK_ID])
        for row in rows:
            line = int(row[1])
            record = records[line - 1]
            label = "%s:%d" % (REGIONAL_PACK_ID, line)
            self.assertEqual(record["record_id"], row[3], label)
            self.assertEqual(record["region"]["level"], row[5], label)
            self.assertIn(record["region"].get("sido"), METROPOLITAN_SIDO)
            self.assertEqual(record["region"].get("name"),
                             record["region"].get("sido"), label)

    def test_region_fix_changes_only_level(self):
        _, rows = region_fix_map()
        base = base_records(REGIONAL_PACK_ID)
        new = new_records(REGIONAL_PACK_ID)
        for row in rows:
            line = int(row[1])
            before = json.loads(json.dumps(base[line - 1]))
            after = json.loads(json.dumps(new[line - 1]))
            self.assertEqual(before["region"]["level"], row[4])
            before["region"]["level"] = after["region"]["level"]
            self.assertEqual(before, after,
                             "%s:%d: level 외 칸이 바뀌었다"
                             % (REGIONAL_PACK_ID, line))

    def test_no_provincial_metropolitan_rows_left(self):
        for pack_id in (REGIONAL_PACK_ID, REGIONAL_PACK_ID_2024):
            records = new_records(pack_id)
            bad = [r["record_id"] for r in records
                   if (r.get("region") or {}).get("level") == "provincial"
                   and (r.get("region") or {}).get("sido")
                   in METROPOLITAN_SIDO]
            self.assertEqual(bad, [], "%s: 광역시 provincial 잔존" % pack_id)

    def test_other_regional_rows_unchanged(self):
        for pack_id, expected_lines in REGIONAL_LINES.items():
            before = _raw_lines(pack_id, base=True)
            after = _raw_lines(pack_id)
            self.assertEqual(len(before), expected_lines, pack_id)
            self.assertEqual(len(after), expected_lines, pack_id)
            fixed = set(REGION_FIX_LINES.get(pack_id, ()))
            for line, (old, new) in enumerate(zip(before, after), start=1):
                if line in fixed:
                    continue
                self.assertEqual(old, new,
                                 "%s:%d: 정정 대상 밖 행 바이트 변경"
                                 % (pack_id, line))


# ---------------------------------------------------------------------------
# 시험 5 — E9 값 정정 1행


class E9CorrectionTests(Pr4SourceMapTestCase):
    """§4-5(N3-01): 물리 195행 두 칸만 정정, 나머지 metrics 불변,
    정정 명세의 before/after 감사 키가 실제 줄 지문과 같음.
    """

    def test_row_195_metrics_corrected(self):
        record = self.new_record(MAFRA_PACK_ID, E9_LINE)
        metrics = record["metrics"]
        self.assertEqual(metrics["farms"], E9_FARMS, record["record_id"])
        self.assertEqual(metrics["area_ha"], E9_AREA_HA, record["record_id"])
        self.assertEqual(record["region"]["name"], E9_REGION_NAME)
        self.assertEqual(record["locator"]["pdf_page"], E9_PDF_PAGE)
        self.assertEqual(record["locator"]["printed_page"], E9_PRINTED_PAGE)
        self.assertEqual(record["crop"]["name"], "기호작물계")
        self.assertEqual(record["region"]["level"], "provincial")

    def test_row_195_other_cells_unchanged(self):
        before = self.base_record(MAFRA_PACK_ID, E9_LINE)
        after = self.new_record(MAFRA_PACK_ID, E9_LINE)
        expected_metrics = json.loads(json.dumps(before["metrics"]))
        self.assertIsNone(expected_metrics["farms"])
        self.assertEqual(expected_metrics["area_ha"], "14771117")
        expected_metrics["farms"] = E9_FARMS
        expected_metrics["area_ha"] = E9_AREA_HA
        after_view = json.loads(json.dumps(after))
        self.assertEqual(after_view.pop("metrics"), expected_metrics,
                         "195행 metrics에서 두 칸 밖이 바뀌었다")
        # 195행도 대응표 class=sourced라 source는 붙는다(설계 §3 예외는
        # metrics 두 칸뿐). 나머지 칸 비교에서 source만 걷어낸다.
        self.assertIn("source", after_view,
                      "195행에 source가 없다 — 대응표 class=sourced")
        after_view.pop("source")
        before_view = json.loads(json.dumps(before))
        before_view.pop("metrics")
        self.assertEqual(after_view, before_view,
                         "195행에서 metrics·source 밖 칸이 바뀌었다")

    def test_every_other_row_metrics_unchanged(self):
        before = base_records(MAFRA_PACK_ID)
        after = new_records(MAFRA_PACK_ID)
        self.assertEqual(len(before), len(after))
        for idx, (old, new) in enumerate(zip(before, after), start=1):
            if idx == E9_LINE:
                continue
            self.assertEqual(old.get("metrics"), new.get("metrics"),
                             "%s:%d: metrics가 바뀌었다"
                             % (MAFRA_PACK_ID, idx))

    def test_only_source_added_on_other_rows(self):
        before = base_records(MAFRA_PACK_ID)
        after = new_records(MAFRA_PACK_ID)
        for idx, (old, new) in enumerate(zip(before, after), start=1):
            if idx == E9_LINE:
                continue
            old_view = json.loads(json.dumps(old))
            new_view = json.loads(json.dumps(new))
            old_view.pop("source", None)
            new_view.pop("source", None)
            self.assertEqual(old_view, new_view,
                             "%s:%d: source 밖 변경" % (MAFRA_PACK_ID, idx))

    def test_econ_pack_rows_unchanged_except_source(self):
        before = base_records(ECON_PACK_ID)
        after = new_records(ECON_PACK_ID)
        self.assertEqual(len(before), len(after))
        for idx, (old, new) in enumerate(zip(before, after), start=1):
            old_view = json.loads(json.dumps(old))
            new_view = json.loads(json.dumps(new))
            old_view.pop("source", None)
            new_view.pop("source", None)
            self.assertEqual(old_view, new_view,
                             "%s:%d: source 밖 변경" % (ECON_PACK_ID, idx))

    def test_correction_spec_pins_before_and_after_line_digests(self):
        path = PACK_DIR[MAFRA_PACK_ID] / "corrections" / (
            "e9-mafra-195-기호작물계-전남.json")
        self.assertTrue(path.is_file(), "E9 정정 명세 없음: %s" % path)
        spec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(spec["schema"], "knuaf-doc.correction-spec/1")
        self.assertEqual(spec["issue"], "E9")
        self.assertEqual(len(spec["records"]), 1)
        node = spec["records"][0]
        self.assertEqual(node["pack_id"], MAFRA_PACK_ID)
        self.assertEqual(node["physical_line"], E9_LINE)
        self.assertEqual(node["record_id"],
                         self.base_record(MAFRA_PACK_ID, E9_LINE)["record_id"])
        before_blob = base_blob(MAFRA_PACK_ID)
        after_blob = (REPO_ROOT / PACK_RELPATH[MAFRA_PACK_ID]).read_bytes()
        for field, blob in (("audit_key_before", before_blob),
                            ("audit_key_after", after_blob)):
            expected = {
                "pack_id": MAFRA_PACK_ID,
                "physical_jsonl_line_1based": E9_LINE,
                "raw_line_sha256": hashlib.sha256(
                    blob.splitlines(keepends=True)[E9_LINE - 1]).hexdigest(),
                "records_file_sha256": hashlib.sha256(blob).hexdigest(),
            }
            self.assertEqual(node[field], expected, field)
        self.assertNotEqual(node["audit_key_before"]["raw_line_sha256"],
                            node["audit_key_after"]["raw_line_sha256"])
        before = self.base_record(MAFRA_PACK_ID, E9_LINE)["metrics"]
        after = self.new_record(MAFRA_PACK_ID, E9_LINE)["metrics"]
        original = {"metrics.farms": None, "metrics.area_ha": "14771117",
                    "metrics.harvest_area_ha": "990",
                    "metrics.production_mt": "1500"}
        corrected = {"metrics.farms": E9_FARMS, "metrics.area_ha": E9_AREA_HA}
        self.assertEqual(node["before"], original)
        self.assertEqual(node["after"], corrected)
        for field, value in original.items():
            self.assertEqual(before[field.split(".")[1]], value, field)
        for field, value in corrected.items():
            self.assertEqual(after[field.split(".")[1]], value, field)
        self.assertEqual(node["changes"], [
            {"path": field, "before": original[field], "after": value}
            for field, value in corrected.items()])
        document = spec["source_document"]
        self.assertEqual(document["title"], MAFRA_VIA_TITLE)
        self.assertEqual(document["pdf_sha256"], PACK_PDF_SHA256[MAFRA_PACK_ID])
        self.assertEqual(document["pdf_page"], E9_PDF_PAGE)
        self.assertEqual(document["printed_page"], E9_PRINTED_PAGE)
        self.assertEqual(document["section"], "3) 기호작물")
        self.assertEqual(document["row_label"], E9_REGION_NAME)
        self.assertIn("1,477 1,117 990 1,500", spec["verbatim"]["line"])
        self.assertTrue(node["evidence"].strip())


# ---------------------------------------------------------------------------
# 시험 6 — E10 유령 격리 게이트


class GhostIsolationTests(Pr4SourceMapTestCase):
    """§4-6(N3-02): 유령 24행이 ① 상태 불변 ② source 0
    ③ 승인 조회 도달 0 — 셋 다 통과해야 '학생 영향 0'.
    """

    def test_ghost_state_unchanged_versus_base_manifest(self):
        after = _manifest_rows(ECON_PACK_ID)
        self.assertEqual(len(after), PACK_LINES[ECON_PACK_ID])
        # 바탕 manifest는 git 역사 없이 되살릴 수 없으므로, 이 시험이 비교하는
        # 상태 3칸을 d663bf8 값으로 동결해 두고 행마다 값으로 맞춘다.
        self.assertEqual(set(GHOST_BASE_STATE), set(GHOST_LINES))
        for line in sorted(GHOST_LINES):
            self.assertIn(line, after, "econ:%d manifest 행이 없다" % line)
            self.assertEqual(
                _ghost_view(after[line]), GHOST_BASE_STATE[line],
                "econ:%d 유령 행의 상태 칸(catalog_status·"
                "observation_status·raw_line_sha256)이 바탕과 다르다" % line)
            self.assertEqual(
                after[line]["audit_key"]["raw_line_sha256"],
                self._line_digest(line),
                "econ:%d 유령 행의 줄 지문이 실제 줄과 다르다" % line)

    def test_ghost_records_have_no_source(self):
        for line in sorted(GHOST_LINES):
            record = self.new_record(ECON_PACK_ID, line)
            self.assertNotIn(
                "source", record,
                "econ:%d 유령 행에 source가 붙었다" % line)
            self.assertIsNone(record.get("source"))

    @staticmethod
    def _line_digest(line):
        raw = (REPO_ROOT / PACK_RELPATH[ECON_PACK_ID]).read_bytes() \
            .splitlines(keepends=True)[line - 1]
        return hashlib.sha256(raw).hexdigest()

    def _ghost_audit_key(self, line):
        """그 줄의 감사 키 — records.jsonl에는 없다(조회 계층이 붙인다).

        그래서 현재 팩 manifest가 선언한 키를 쓰되, ``raw_line_sha256``이
        실제 현재 줄 바이트 지문과 같은지 먼저 확인한다. 지문이 어긋나면
        그 키는 그 줄을 가리키지 않아 조회가 무조건 ``not_found``가 되고
        "도달 0"이 공허하게 참이 된다 — 그 구멍을 여기서 막는다.
        """
        row = _manifest_rows(ECON_PACK_ID).get(line)
        self.assertIsNotNone(row, "econ:%d manifest 행 없음" % line)
        key = row.get("audit_key")
        self.assertIsInstance(key, dict, "econ:%d manifest audit_key 없음" % line)
        self.assertEqual(key.get("pack_id"), ECON_PACK_ID, line)
        self.assertEqual(key.get("physical_jsonl_line_1based"), line, line)
        self.assertEqual(key.get("raw_line_sha256"), self._line_digest(line),
                         "econ:%d manifest 감사 키 지문이 실제 줄과 다르다"
                         " — 도달 0 판정이 공허해진다" % line)
        return key

    def test_ghost_rows_unreachable_from_approval_lookup(self):
        for line in sorted(GHOST_LINES):
            record = self.new_record(ECON_PACK_ID, line)
            crop = ((record.get("crop") or {}).get("name")
                    or (record.get("basis") or {}).get("item_name") or "")
            audit_key = self._ghost_audit_key(line)
            with self.subTest(line=line, record=record.get("record_id")):
                result = candidates.lookup_approved_candidates(
                    crop, "전국", "specialty_crops",
                    kind=record.get("kind"),
                    year=(record.get("basis") or {}).get("year"),
                    use_scope="plan_input", audit_key=audit_key)
                self.assertNotEqual(
                    result.get("status"), "approved",
                    "econ:%d 유령 행이 승인 조회에서 approved로 도달했다"
                    % line)
                self.assertEqual(
                    (result.get("counts") or {}).get("approved", 0), 0,
                    "econ:%d 승인 후보 수가 0이 아니다" % line)
                self.assertEqual(result.get("approved_candidates"), [],
                                 "econ:%d approved_candidates 비어 있지 않다"
                                 % line)
                returned = result.get("candidates")
                self.assertIsInstance(returned, list)
                if line in range(105, 122):
                    # 이 17행은 승인 평가 이전 원시 조회에서 rejected로 제외된다.
                    self.assertEqual(record["extraction"]["status"], "rejected")
                    self.assertEqual(result["status"], "not_found")
                    self.assertEqual(returned, [])
                    continue
                # 나머지 7행이 없으면 감사 키/조회 필터가 빗나간 공허 통과다.
                self.assertEqual(record["extraction"]["status"], "extracted")
                self.assertEqual(result["status"], "unapproved")
                self.assertEqual(len(returned), 1)
                candidate = returned[0]
                self.assertEqual(candidate["audit_key"], audit_key)
                self.assertEqual(candidate["record"]["record_id"],
                                 record["record_id"])
                self.assertEqual(candidate["observation_status"], "quarantined")
                self.assertIn("observation_not_verified", candidate["rejections"])
                self.assertNotIn("use_scope_unspecified", candidate["rejections"])
                self.assertFalse(candidate["approved"])
                # 현재 자료는 다른 승인 축도 미충족. 격리 하나만 풀었을 때
                # 반드시 승인된다는 증거가 아니라 격리 거부가 실제 평가됨을 잠근다.


# ---------------------------------------------------------------------------
# 웹 r5 — null 조회·출력, 팩별 출처 수, 비료 별표 N-A 전제


class SourceNullLookupTests(Pr4SourceMapTestCase):
    """N5-01·E14-02: 실팩의 null 이름을 승인 조회와 JSON 출력에 통과시킨다.

    읽은 경로: gg.py:371-377은 lookup_approved_candidates를 직접 호출하고,
    :853은 결과를 json.dumps한다. 별도 source 표시 함수는 이 경로에 없다.
    candidates._evaluate는 record를 보존한다(:508). lookup의 verification.label은
    검증 상태 설명(:554-564,644)이지 조사 이름이 아니므로 survey ID로 바꾸지 않는다.
    이 시험은 이 조회·출력 경로의 근거이며 다른 화면 전체의 무영향 주장이 아니다.
    """

    # 물리 행으로 고정: record_id 중복이나 빈 조회가 null 검사를 우회하지 못한다.
    # econ140=통계청 생산량, econ201=오피넷 가격(publisher도 null),
    # mafra800=행정 생산실적, mafra2900=시장 가격(publisher/document도 null).
    CASES = (
        (ECON_PACK_ID, 140, "쌀", "전국", "food_crops", "yield_series",
         2024, "kostat.crop_production"),
        (ECON_PACK_ID, 201, "유류(2014년)", "전국", "specialty_crops", "input_price",
         2024, "opinet.fuel_price"),
        (MAFRA_PACK_ID, 800, "식방풍", "울산", "specialty_crops", "production_stat",
         2024, "mafra.specialty_admin"),
        (MAFRA_PACK_ID, 2900, "두충", "경북", "specialty_crops", "official_market_price",
         2021, "market.yeongcheon_herbal"),
    )

    def _lookup_case(self, case):
        pack_id, line, crop, region, major, kind, year, survey = case
        record = self.new_record(pack_id, line)
        self.assertEqual(self.map_row(pack_id, line)[_C_CLASS], "sourced")
        self.assertEqual(record["kind"], kind)
        self.assertEqual(record["basis"]["year"], year)
        self.assertEqual(record["source"]["survey"], survey)
        self.assertIsNone(record["source"]["survey_label"])
        raw = _raw_lines(pack_id)[line - 1]
        key = _manifest_rows(pack_id)[line]["audit_key"]
        self.assertEqual(key["physical_jsonl_line_1based"], line)
        self.assertEqual(key["raw_line_sha256"], hashlib.sha256(raw).hexdigest())
        result = candidates.lookup_approved_candidates(
            crop, region, major, kind=kind, year=year,
            use_scope="plan_input", audit_key=key)
        # 현재 팩의 미충족 승인 축은 그대로다. 조회 성공 ≠ 본문·표 사용 승인.
        self.assertNotEqual(result["status"], "not_found")
        self.assertEqual(len(result["candidates"]), 1)
        returned = result["candidates"][0]
        self.assertEqual(returned["audit_key"], key)
        self.assertEqual(returned["pack_id"], pack_id)
        self.assertEqual(returned["record"]["source"], record["source"])
        self.assertIsNone(returned["record"]["source"]["survey_label"])
        return result

    def _assert_no_null_text(self, node, path="result"):
        if isinstance(node, dict):
            for key, value in node.items():
                field = "%s.%s" % (path, key)
                if key == "survey_label":
                    self.assertIsNone(value, field)
                if (key in ("publisher", "document", "title", "label", "name")
                        or key.endswith(("_label", "_name"))):
                    if isinstance(value, str):
                        self.assertNotIn(value.strip().lower(), ("null", "none"), field)
                self._assert_no_null_text(value, field)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self._assert_no_null_text(value, "%s[%d]" % (path, index))

    def test_sourced_null_labels_survive_approval_lookup_and_cli_serialization(self):
        for case in self.CASES:
            with self.subTest(pack=case[0], line=case[1]):
                result = self._lookup_case(case)
                # gg.py와 같은 함수·직렬화 옵션. JSON null은 허용하되 문자열은 금지.
                output = json.dumps(result, ensure_ascii=False, indent=2)
                decoded = json.loads(output)
                self._assert_no_null_text(decoded)
                source = decoded["candidates"][0]["record"]["source"]
                self.assertIsNone(source["survey_label"])
                self.assertEqual(source["survey"], case[-1])


class SourcePackContractTests(Pr4SourceMapTestCase):
    """N5-02·E14-01와 팩별 수: 총량만 같고 팩 사이 출처가 이동하는 회귀를 막는다."""

    def test_pinned_source_counts_are_preserved_per_pack(self):
        expected = {
            "rda.income.national.2025": 104,
            "rda.income.national.2024": 102,
            REGIONAL_PACK_ID: 49,
            REGIONAL_PACK_ID_2024: 365,
            ECON_PACK_ID: 2231,
            MAFRA_PACK_ID: 2916,
        }
        packs = KNUAF_DOC / "references" / "benchmark-packs"
        actual = {}
        for pack_id, pin in provenance.PINNED_PACKS.items():
            records = (packs / pin["relpath"]).read_text(encoding="utf-8")
            actual[pack_id] = sum(
                json.loads(line).get("source") is not None
                for line in records.splitlines())
        self.assertEqual(actual, expected)

    def test_aggregation_rule_requires_null_safe_source_display(self):
        # 사용자 지정 manifest 계약: publisher 문장 삭제 변이를 직접 잡는다.
        clauses = (
            "source.aggregation_group가 다른 레코드는 합산·평균·순위 비교의 같은 행에 묶지 않는다.",
            "표시 계층은 source.survey_label이 있으면 이를 사용하고, null이면 source.survey ID를 사용한다.",
            "source.publisher가 null이면 기관명을 추정하거나 문자열 null을 표시하지 않는다.",
            "조사·자료 생산체계를 감춘 채 서로 다른 출처를 하나의 집계로 합치지 않는다.",
        )
        for pack_id in (ECON_PACK_ID, MAFRA_PACK_ID):
            rule = _manifest(pack_id).get("aggregation_rule", "")
            for clause in clauses:
                with self.subTest(pack=pack_id, clause=clause):
                    self.assertIn(clause, rule)


class FertilizerAsteriskPremiseTests(Pr4SourceMapTestCase):
    """N5-04: ⑥·⑥' N-A는 동결 Ⅲ-5의 별표 행이 실제 0일 때만 유효하다."""

    def test_frozen_fertilizer_asterisk_rows_are_zero(self):
        _, table = source_map()
        section = "Ⅲ-5 주요 비료성분표"
        mapped = {line: row for (pack_id, line), row in table.items()
                  if pack_id == ECON_PACK_ID and row[_C_SECTION] == section}
        self.assertEqual(set(mapped), set(range(2588, 2601)),
                         "동결 비료 대상이 바뀌었다: 변이 ⑥·⑥'를 걸어라")
        # 별표는 행 라벨/품목에 붙은 '*'이다. 자료 주의 '*국립농업과학원…참고'
        # 자체를 세지 않는다(SUMMARY-ECON.md:54,80; 동결 대응표 2588~2600행).
        marked = []
        for version, records in (("base", base_records(ECON_PACK_ID)),
                                 ("current", new_records(ECON_PACK_ID))):
            fertilizer = {
                line: record for line, record in enumerate(records, 1)
                if (record.get("locator") or {}).get("section") == section}
            self.assertEqual(set(fertilizer), set(mapped),
                             "%s 비료 대상이 바뀌었다: 변이 ⑥·⑥'를 걸어라" % version)
            for line, record in fertilizer.items():
                labels = (mapped[line][_C_LABEL],
                          (record.get("locator") or {}).get("row_label", ""),
                          (record.get("basis") or {}).get("item_name", ""))
                if any("*" in label for label in labels):
                    marked.append((version, line))
        self.assertEqual(len(marked), 0,
                         "비료 별표 행이 생겼다: 변이 ⑥·⑥'를 걸어라 — %r" % marked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
