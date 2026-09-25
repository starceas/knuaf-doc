"""원예환경시스템전공(hort_env_systems) 읽기 전용 계획 CLI (설계 §7/§13/§14).

프로젝트 정본과 ``references/hort-env-systems/profile.json``을 읽어
(a) 원예 질문 필드별 답변 상태(값 없음), (b) 문서 계획 노드와 선례 위치,
(c) H-X1과 해시가 같은 등록 원천의 교재 주의 목록, (d) 근거 사용 규칙을
JSON으로 반환한다. 어떤 파일도 만들거나 고치지 않으며 lock도 잡지
않는다(``gg_core.load``는 정본 JSON 읽기만 한다).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import gg_core
import gg_major_contract


PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "hort-env-systems"
    / "profile.json"
)

SCHEMA = "knuaf-hort-env-profile/v1"

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ITEM_ID = re.compile(r"[a-z][a-z0-9_]{0,40}\Z")
_CAUTION_ID = re.compile(r"X-[CN][0-9]{1,2}R?\Z")
_SOURCE_ID = re.compile(r"[A-Z]{2,3}-[A-Z0-9]{1,4}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_CELLREF = re.compile(
    r"\$?[A-Z]{1,3}\$?[0-9]{1,7}(:\$?[A-Z]{1,3}\$?[0-9]{1,7})?\Z"
)

_FILE_STAGES = ("draft_1_by_filename", "unknown")
_APPENDIX8_AXES = ("crop_cycle", "fiscal_year")
_CAUTION_STATUSES = ("confirmed_inconsistency", "needs_confirmation")
_ACQUISITIONS = ("bytes_held", "link_only")
_REQUIRED_NOS = set(range(1, 19))


class ProfileInvalidError(gg_major_contract.MajorContractError):
    reason = "profile_invalid"


def _invalid(message, **detail):
    raise ProfileInvalidError(message, **detail)


def _expect(cond, message, **detail):
    if not cond:
        _invalid(message, **detail)


def _keys(obj, expected, where):
    """정확한 키 집합(누락·추가 모두 무효)."""
    if not isinstance(obj, dict):
        _invalid("%s: 객체가 아님" % where)
    got, want = set(obj), set(expected)
    if got != want:
        _invalid(
            "%s: 키 불일치" % where,
            missing=sorted(want - got),
            extra=sorted(got - want),
        )


def _str(value, where):
    if not isinstance(value, str) or not value.strip():
        _invalid("%s: 비어 있지 않은 문자열이 아님" % where)


def _opt_str(value, where):
    if value is not None:
        _str(value, where)


def _int(value, where, low=None, high=None):
    if (
        type(value) is not int
        or (low is not None and value < low)
        or (high is not None and value > high)
    ):
        _invalid("%s: 허용 범위 밖 정수" % where, got=repr(value))


def _page(value, where, upper):
    """쪽 필드: null 또는 1..upper."""
    if value is not None:
        _int(value, where, low=1, high=upper)


def _str_list(value, where, min_len=1):
    if (
        not isinstance(value, list)
        or len(value) < min_len
        or any(not isinstance(x, str) or not x.strip() for x in value)
    ):
        _invalid("%s: 비어 있지 않은 문자열 목록이 아님" % where)


def _validate_section(item, where, physical_pages):
    _keys(item, {"item", "title", "toc_page", "physical_page",
                 "printed_page", "note"}, where)
    _expect(
        isinstance(item["item"], str)
        and _ITEM_ID.fullmatch(item["item"]) is not None,
        "%s.item 형식" % where,
    )
    _str(item["title"], "%s.title" % where)
    _page(item["toc_page"], "%s.toc_page" % where, 10 ** 9)
    _page(item["physical_page"], "%s.physical_page" % where,
          physical_pages)
    _page(item["printed_page"], "%s.printed_page" % where, 10 ** 9)
    _opt_str(item["note"], "%s.note" % where)


def _validate_appendix(item, where, physical_pages):
    _keys(item, {"no", "title", "toc_page", "physical_start",
                 "physical_end", "printed_start", "note"}, where)
    _int(item["no"], "%s.no" % where, low=1, high=18)
    _str(item["title"], "%s.title" % where)
    _page(item["toc_page"], "%s.toc_page" % where, 10 ** 9)
    _page(item["physical_start"], "%s.physical_start" % where,
          physical_pages)
    _page(item["physical_end"], "%s.physical_end" % where, physical_pages)
    _page(item["printed_start"], "%s.printed_start" % where, 10 ** 9)
    if (
        item["physical_start"] is not None
        and item["physical_end"] is not None
    ):
        _expect(
            item["physical_start"] <= item["physical_end"],
            "%s: physical_start>physical_end" % where,
        )
    _opt_str(item["note"], "%s.note" % where)


def _validate_precedent(item, index, roster):
    where = "precedents[%d]" % index
    _keys(item, {"ref", "title", "author", "sha256", "physical_pages",
                 "file_stage", "sections", "appendices",
                 "appendix8_axes"}, where)
    _expect(isinstance(item.get("ref"), str), "%s.ref 형식" % where)
    _expect(item.get("ref") in roster, "%s: roster 밖 ref" % where,
            ref=item.get("ref"))
    _str(item["title"], "%s.title" % where)
    _str(item["author"], "%s.author" % where)
    _expect(
        isinstance(item["sha256"], str)
        and _HEX64.fullmatch(item["sha256"]) is not None,
        "%s.sha256" % where,
    )
    physical_pages = item["physical_pages"]
    _int(physical_pages, "%s.physical_pages" % where, low=1)
    _expect(item["file_stage"] in _FILE_STAGES, "%s.file_stage" % where)
    if not isinstance(item["sections"], list) or not item["sections"]:
        _invalid("%s.sections: 1개 이상 필요" % where)
    seen = set()
    for i, section in enumerate(item["sections"]):
        w = "%s.sections[%d]" % (where, i)
        _validate_section(section, w, physical_pages)
        _expect(section["item"] not in seen, "%s item 중복" % w)
        seen.add(section["item"])
    if not isinstance(item["appendices"], list):
        _invalid("%s.appendices: 목록이 아님" % where)
    for i, appendix in enumerate(item["appendices"]):
        _validate_appendix(appendix, "%s.appendices[%d]" % (where, i),
                           physical_pages)
    nos = [a["no"] for a in item["appendices"]]
    _expect(
        len(nos) == len(_REQUIRED_NOS) and set(nos) == _REQUIRED_NOS,
        "%s.appendices: no 1..18 각각 1개 필요" % where,
    )
    axes = item["appendix8_axes"]
    if (
        not isinstance(axes, list)
        or not axes
        or any(a not in _APPENDIX8_AXES for a in axes)
        or len(set(axes)) != len(axes)
    ):
        _invalid("%s.appendix8_axes" % where)


def _validate_workbook(wb):
    where = "workbook_reference"
    _keys(wb, {"ref", "sha256", "role", "authority", "statement_date",
               "finance_status", "sheets", "appendix_map", "cautions"},
          where)
    _expect(wb["ref"] == "H-X1", "%s.ref" % where)
    _expect(
        isinstance(wb["sha256"], str)
        and _HEX64.fullmatch(wb["sha256"]) is not None,
        "%s.sha256" % where,
    )
    _expect(wb["role"] == "professor_reference", "%s.role" % where)
    _expect(wb["authority"] == "user_statement", "%s.authority" % where)
    _expect(
        isinstance(wb["statement_date"], str)
        and _DATE.fullmatch(wb["statement_date"]) is not None,
        "%s.statement_date" % where,
    )
    _expect(wb["finance_status"] == "unsupported",
            "%s.finance_status" % where)
    sheets = wb["sheets"]
    if (
        not isinstance(sheets, list)
        or len(sheets) != 18
        or any(not isinstance(s, str) or not s for s in sheets)
        or len(set(sheets)) != 18
    ):
        _invalid("%s.sheets: 서로 다른 18개 문자열 필요" % where)
    sheet_set = set(sheets)
    amap = wb["appendix_map"]
    if not isinstance(amap, list):
        _invalid("%s.appendix_map: 목록이 아님" % where)
    for i, entry in enumerate(amap):
        w = "%s.appendix_map[%d]" % (where, i)
        _keys(entry, {"no", "sheet"}, w)
        _int(entry["no"], "%s.no" % w, low=1, high=18)
        _expect(isinstance(entry["sheet"], str), "%s.sheet 형식" % w)
        _expect(entry["sheet"] in sheet_set,
                "%s.sheet: sheets에 없음" % w)
    nos = [e["no"] for e in amap]
    _expect(
        len(nos) == len(_REQUIRED_NOS) and set(nos) == _REQUIRED_NOS,
        "%s.appendix_map: no 1..18 각각 1개 필요" % where,
    )
    if not isinstance(wb["cautions"], list):
        _invalid("%s.cautions: 목록이 아님" % where)
    seen = set()
    for i, caution in enumerate(wb["cautions"]):
        w = "%s.cautions[%d]" % (where, i)
        _keys(caution, {"id", "status", "kind", "locations", "guidance"}, w)
        _expect(
            isinstance(caution["id"], str)
            and _CAUTION_ID.fullmatch(caution["id"]) is not None,
            "%s.id 형식" % w,
        )
        _expect(caution["id"] not in seen, "%s.id 중복" % w)
        seen.add(caution["id"])
        _expect(caution["status"] in _CAUTION_STATUSES, "%s.status" % w)
        _expect(
            isinstance(caution["kind"], str)
            and _ITEM_ID.fullmatch(caution["kind"]) is not None,
            "%s.kind 형식" % w,
        )
        locs = caution["locations"]
        if not isinstance(locs, list) or not locs:
            _invalid("%s.locations: 1개 이상 필요" % w)
        for j, loc in enumerate(locs):
            lw = "%s.locations[%d]" % (w, j)
            _keys(loc, {"sheet", "cells"}, lw)
            # 시트명은 앞뒤 공백 포함 정확 일치만 인정(strip 비교 금지).
            _expect(isinstance(loc["sheet"], str), "%s.sheet 형식" % lw)
            _expect(loc["sheet"] in sheet_set,
                    "%s.sheet: sheets에 없음" % lw)
            cells = loc["cells"]
            if not isinstance(cells, list) or not cells:
                _invalid("%s.cells: 1개 이상 필요" % lw)
            for cell in cells:
                _expect(
                    isinstance(cell, str)
                    and _CELLREF.fullmatch(cell) is not None,
                    "%s.cells 항목 형식" % lw,
                )
        _str(caution["guidance"], "%s.guidance" % w)


def _validate_source(item, index, use_scopes):
    where = "evidence_sources[%d]" % index
    _keys(item, {"id", "title", "publisher", "edition", "effective",
                 "sha256", "acquisition", "use_scope", "caveats"}, where)
    _expect(
        isinstance(item["id"], str)
        and _SOURCE_ID.fullmatch(item["id"]) is not None,
        "%s.id 형식" % where,
    )
    _str(item["title"], "%s.title" % where)
    _str(item["publisher"], "%s.publisher" % where)
    _opt_str(item["edition"], "%s.edition" % where)
    if item["effective"] is not None:
        _expect(
            isinstance(item["effective"], str)
            and _DATE.fullmatch(item["effective"]) is not None,
            "%s.effective" % where,
        )
    if item["sha256"] is not None:
        _expect(
            isinstance(item["sha256"], str)
            and _HEX64.fullmatch(item["sha256"]) is not None,
            "%s.sha256" % where,
        )
    _expect(item["acquisition"] in _ACQUISITIONS, "%s.acquisition" % where)
    _expect(item["use_scope"] in use_scopes,
            "%s.use_scope: 모듈 use_scopes 밖" % where,
            use_scope=item.get("use_scope"))
    _str_list(item["caveats"], "%s.caveats" % where)


def _validate_prohibited(item, index):
    where = "prohibited_rows[%d]" % index
    _keys(item, {"pack_id", "kind", "locator_pdf_pages", "reason"}, where)
    _str(item["pack_id"], "%s.pack_id" % where)
    _expect(
        isinstance(item["kind"], str)
        and _ITEM_ID.fullmatch(item["kind"]) is not None,
        "%s.kind 형식" % where,
    )
    pages = item["locator_pdf_pages"]
    if (
        not isinstance(pages, list)
        or not pages
        or any(type(x) is not int or x < 1 for x in pages)
    ):
        _invalid("%s.locator_pdf_pages" % where)
    _str(item["reason"], "%s.reason" % where)


def _validate_profile(profile, module):
    _keys(profile, {"schema", "major_id", "module_version", "notice",
                    "precedents", "workbook_reference",
                    "evidence_sources", "prohibited_rows"}, "root")
    _expect(profile["schema"] == SCHEMA, "schema")
    _expect(profile["major_id"] == module.major_id,
            "major_id: 모듈과 불일치")
    _expect(profile["module_version"] == module.module_version,
            "module_version: 모듈과 불일치")
    _str(profile["notice"], "notice")

    roster = [ps.ref for ps in module.precedents]
    if (
        not isinstance(profile["precedents"], list)
        or not profile["precedents"]
    ):
        _invalid("precedents: 1개 이상 필요")
    for i, item in enumerate(profile["precedents"]):
        _validate_precedent(item, i, roster)
    _expect(
        {x["ref"] for x in profile["precedents"]} == set(roster)
        and len(profile["precedents"]) == len(set(roster)),
        "precedents ref 집합이 모듈 roster와 불일치",
        roster=list(roster),
    )

    _validate_workbook(profile["workbook_reference"])

    use_scopes = tuple(
        module.evidence_applicability.get("use_scopes") or ()
    )
    sources = profile["evidence_sources"]
    if not isinstance(sources, list) or not sources:
        _invalid("evidence_sources: 1개 이상 필요")
    for i, item in enumerate(sources):
        _validate_source(item, i, use_scopes)
    ids = [s["id"] for s in sources]
    _expect(len(set(ids)) == len(ids), "evidence_sources id 중복")

    if not isinstance(profile["prohibited_rows"], list):
        _invalid("prohibited_rows: 목록이 아님")
    for i, item in enumerate(profile["prohibited_rows"]):
        _validate_prohibited(item, i)


def load_profile(path=None, module=None):
    """profile.json을 스펙 v1 규칙 전부로 검사해 dict로 반환한다.

    모든 객체의 키는 정확 일치(누락·추가·중복 키 = 무효), module_version·
    선례 ref 집합·use_scope는 모듈 선언과 일치해야 한다. 반환 dict에는
    파일 바이트의 ``profile_sha256``이 함께 실린다.
    """
    if module is None:
        module = getattr(
            gg_major_contract, "HORT_ENV_SYSTEMS_MODULE", None
        )
        if module is None:
            _invalid("HORT_ENV_SYSTEMS_MODULE 선언 없음")
    p = Path(path) if path is not None else PROFILE_PATH
    try:
        raw = p.read_bytes()
    except OSError as error:
        _invalid("profile 읽기 실패: %s" % error, path=str(p))
    digest = hashlib.sha256(raw).hexdigest()

    duplicates = []

    def hook(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            duplicates.append(True)
        return dict(pairs)

    try:
        profile = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    except (UnicodeDecodeError, ValueError) as error:
        _invalid("profile JSON 해석 실패: %s" % error, path=str(p))
    if duplicates:
        _invalid("profile 중복 키", path=str(p))
    if not isinstance(profile, dict):
        _invalid("profile 루트가 객체가 아님", path=str(p))
    try:
        _validate_profile(profile, module)
    except ProfileInvalidError as error:
        error.detail.setdefault("path", str(p))
        raise
    except (TypeError, KeyError, AttributeError) as error:
        # Any shape the explicit checks missed is still a schema
        # violation, never an uncaught crash of the read-only plan.
        _invalid("profile 형식 오류: %s" % type(error).__name__, path=str(p))
    profile["profile_sha256"] = digest
    return profile


def _check_source_file(root, src):
    """§13-5 판정 순서 그대로: 해시 일치 확인 후
    path→경계(읽기 전)→존재→형식→읽기→실제 해시."""
    path = src.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        return "path_invalid", "source path missing, empty or malformed"
    try:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            return "outside_project", "absolute path or '..' not allowed"
        root_resolved = Path(root).resolve()
        resolved = (root_resolved / candidate).resolve()
        if not resolved.is_relative_to(root_resolved):
            return "outside_project", "resolved path escapes project root"
        if not resolved.exists():
            return "missing", "file does not exist"
        if not resolved.is_file():
            return "path_invalid", "not a regular file"
    except (OSError, ValueError, RuntimeError) as error:
        return "path_invalid", "path could not be resolved: %s" % type(error).__name__
    try:
        data = resolved.read_bytes()
    except OSError as error:
        return "read_error", "read failed: %s" % error
    if hashlib.sha256(data).hexdigest() != src.get("hash"):
        return "changed", "recorded hash does not match file bytes"
    return "match", None


def _workbook_references(project, workbook, root):
    sources = project.get("sources") or {}
    items = []
    for source_id in sorted(sources):
        src = sources[source_id]
        if not isinstance(src, dict):
            continue
        if src.get("hash") != workbook["sha256"]:
            continue  # 기록 해시가 H-X1과 다르면 대상 아님(목록 제외)
        check, held_reason = _check_source_file(root, src)
        items.append({
            "source_id": source_id,
            "file_check": check,
            "role": workbook["role"],
            "authority": workbook["authority"],
            "cautions": workbook["cautions"] if check == "match" else [],
            "held_reason": None if check == "match" else held_reason,
        })
    if not items:
        return {"status": "no_hort_reference_registered", "items": []}
    return {"status": "checked", "items": items}


def _questions(module, project):
    facts = project.get("facts") or {}
    by_field = {}
    for fact_id in sorted(facts):
        fact = facts[fact_id]
        if isinstance(fact, dict) and "field_id" in fact:
            by_field.setdefault(fact["field_id"], []).append(fact_id)
    out = []
    for spec in module.question_schema:
        entries = []
        for fact_id in by_field.get(spec.field_id, []):
            fact = facts[fact_id]
            entries.append({
                "fact_id": fact_id,
                # 여섯 상태 그대로(normalize 통과값), 충돌 판정 없음,
                # value·unit 값·source_refs 내용은 출력하지 않는다.
                "answer_state": gg_major_contract.normalize_answer_state(
                    fact.get("answer_state")
                ),
                "period": fact.get("period"),
                "scope": fact.get("scope"),
            })
        out.append({
            "field_id": spec.field_id,
            "meaning": spec.meaning,
            "unit": spec.unit,
            "period": spec.period,
            "target": spec.target,
            "recorded": bool(entries),
            "fact_count": len(entries),
            "facts": entries,
        })
    return out


def build_plan(project_root, *, registry=None, profile_path=None):
    """원예 계획 조립(읽기 전용). ``(plan_dict, exit_code)`` 반환."""
    if registry is None:
        registry = gg_major_contract.default_registry()
    try:
        project = gg_core.load(project_root)
    except (OSError, ValueError) as error:
        return {
            "status": "held",
            "reason": "project_invalid",
            "detail": str(error),
        }, 2
    try:
        binding = gg_major_contract.binding_from_project(registry, project)
    except gg_major_contract.MajorContractError as error:
        return gg_major_contract.common_only_plan(error.reason), 2
    if binding.major_id != "hort_env_systems":
        return {
            "status": "held",
            "reason": "wrong_major",
            "major_id": binding.major_id,
        }, 2
    try:
        module = registry.resolve(binding.major_id)
    except gg_major_contract.MajorContractError as error:
        return gg_major_contract.common_only_plan(error.reason), 2
    try:
        profile = load_profile(profile_path, module)
    except ProfileInvalidError as error:
        detail = dict(error.detail)
        detail.setdefault("path", str(profile_path or PROFILE_PATH))
        return {
            "status": "held",
            "reason": "profile_invalid",
            "detail": detail,
        }, 2

    applicability = dict(module.evidence_applicability)
    return {
        "status": "ok",
        "binding": vars(binding),
        "project_revision": project["revision"],
        "module_version": module.module_version,
        "profile_sha256": profile["profile_sha256"],
        "questions": _questions(module, project),
        "document_plan": [
            dict(vars(node)) for node in module.document_plan
        ],
        "precedents": profile["precedents"],
        "workbook_references": _workbook_references(
            project, profile["workbook_reference"], project_root
        ),
        "evidence_rules": {
            "use_scopes": list(applicability.get("use_scopes") or ()),
            "common_pack_policy": applicability.get("common_pack_policy"),
            "sources": profile["evidence_sources"],
            "prohibited_rows": profile["prohibited_rows"],
            "notice": profile["notice"],
        },
        "finance": {
            "status": "unsupported",
            "profiles": [
                {
                    "profile": cap.profile,
                    "status": cap.status,
                    "reason": cap.reason,
                }
                for cap in module.finance_capabilities
            ],
        },
    }, 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gg_hort_plan.py",
        description="원예환경시스템전공 읽기 전용 계획 조회",
    )
    parser.add_argument("--project", required=True, metavar="DIR")
    args = parser.parse_args(argv)
    plan, code = build_plan(args.project)
    json.dump(plan, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

