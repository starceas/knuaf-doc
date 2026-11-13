#!/usr/bin/env python3
"""학생 알림 파일 쓰기·선택 기록 (v0.4.9).

앱은 ``gg.py check`` 출력을 읽지 못하고 ``<논문 폴더>/.knuaf-notices.json``
만 읽는다. 그래서 알림을 그 파일로도 내보낸다.  ``gg.py`` 는 다른 갈래가
고치는 중이므로 이 모듈은 **자체 실행 진입점**만 둔다.

    python3 gg_notices.py write <논문 폴더> [--json]
    python3 gg_notices.py choose <논문 폴더> --identity <값> --value <direct_tell|footnote> [--json]

``write`` 는 ``gg_core.checks`` 를 돌려 알림 행을 모으고 파일을 원자적으로
교체한다. ``choose`` 는 선택을 정본(questions+facts)에 기록하고 파일을 다시
쓴다.  정본 기록은 기존 공개 ``apply`` 경로를 그대로 쓴다(잠금·개정 대조).
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import sys
import tempfile
from pathlib import Path

NOTICES_NAME = ".knuaf-notices.json"
NOTICES_SCHEMA = "knuaf.notices/1"
CHOICE_FIELD = "finance.workbook.accounting_notice.choice"
CHOICE_VALUES = ("direct_tell", "footnote")
TITLE_MAX = 1          # 한 줄
ITEMS_MAX = 50


def _core():
    import gg_core
    return gg_core


def notice_rows(root):
    """현재 점검에서 나온 알림 행만.

    ``gg_core.checks`` 전체(엑셀 열기·본문 대조 포함)를 돌리지 않고 **알림 행을
    만드는 얇은 경로**만 부른다. 두 경로가 같은 행을 내는 것은
    ``tests/test_hort_workbook_advice.py``의 ``write_matches_full_checks``가
    **바이트까지** 잠근다(실측: 0.089s vs 0.0002s).
    """
    core = _core()
    project = core.load(root)
    rows = [core._workbook_notice_row_for_project(root, project),
            core._unsupported_finance_notice_row_for_project(root, project)]
    return [row for row in rows if row is not None]


def notice_rows_or_error(root):
    """(행 목록, 오류) — '알림 필요 없음'과 '생성 실패'를 나눈다(I4).

    행 생성이 예상 밖 예외로 죽으면 빈 목록을 '성공'으로 보고하면 안 된다.
    호출부가 실패를 표시하고 기존 알림 파일을 보존할 수 있도록 오류를
    함께 돌려준다. 필요 없음은 ``([], None)``이다.
    """
    try:
        return notice_rows(root), None
    except Exception as error:      # 실패 — 빈 목록으로 바꾸지 않는다
        return [], type(error).__name__ + ": " + str(error)[:200]


def _is_relative_inside(path):
    """문자열이 상대 경로이고 ``..`` 로 빠져나가지 않을 때만 True."""
    if not isinstance(path, str) or not path.strip():
        return False
    candidate = Path(path)
    if candidate.is_absolute():
        return False
    return ".." not in candidate.parts


def _sanitize_row(row):
    """파일에 넣기 전 계약 정리. 원본 dict 는 건드리지 않는다."""
    clean = dict(row)
    title = clean.get("title") or clean.get("reason") or ""
    clean["title"] = " ".join(str(title).split())
    items = clean.get("items")
    if not isinstance(items, list):
        items = []
    if len(items) > ITEMS_MAX:
        extra = len(items) - ITEMS_MAX
        items = (items[:ITEMS_MAX]
                 + [{"key": "more", "kind": "more",
                     "what": "외 %d곳" % extra, "cells": [],
                     "source": "notice", "in_school_form": False}])
    clean["items"] = items
    if not _is_relative_inside(clean.get("receipt_path")):
        clean["receipt_path"] = None
    refs = clean.get("record_refs")
    refs = refs if isinstance(refs, list) else []
    clean["record_refs"] = [
        {"collection": r["collection"], "id": r["id"]}
        for r in refs
        if isinstance(r, dict)
        and isinstance(r.get("collection"), str)
        and isinstance(r.get("id"), str)
        and set(r) <= {"collection", "id"}
    ]
    return clean


def notices_document(root, rows):
    core = _core()
    project = core.load(root)
    payload = {
        "schema": NOTICES_SCHEMA,
        "written_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "project_id": digest_project(root),
        "notices": [_sanitize_row(r) for r in rows],
    }
    return payload


def digest_project(root):
    core = _core()
    return core.digest(str(Path(root).resolve()).encode("utf-8"))


def write_notices(root):
    """알림 파일을 원자적으로 교체한다. 반환: 계약 JSON.

    정본 잠금은 호출자가 쥔다 — 이 함수는 잠그지 않는다. CLI ``write``는
    호출 전에 ``core.Lock(root)``를 잡고, 커밋 훅은 이미 잠금 안이다(공통 장
    §4-5). 알림 생성이 실패하면 이 함수는 파일을 건드리지 않는다. 등록부
    호출기가 ``failed`` 반환을 실패로 보고 기존 알림을 지운다(공통 장 §3).
    돌려주는 값은 ``status="failed"`` + ``error``다.
    """
    root = Path(root)
    rows, error = notice_rows_or_error(root)
    if error is not None:
        return {"status": "failed", "path": NOTICES_NAME, "error": error,
                "notices": None}
    document = notices_document(root, rows)
    target = root / NOTICES_NAME
    handle, temp_name = tempfile.mkstemp(dir=str(root), prefix=".knuaf-notices-",
                                        suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, target)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise
    return {
        "status": "written",
        "path": NOTICES_NAME,
        "project_id": document["project_id"],
        "written_at": document["written_at"],
        "notices": len(document["notices"]),
    }


def choose(root, identity, value):
    """선택을 정본에 기록한다. 알림 파일은 정본 커밋 훅이 다시 쓴다.

    선택은 **질문 결정 기록 한 레코드**에만 둔다(C1). 일반 재무 사실로
    저장하면 ``checks()``의 ``claim_missing``과 ``gate()``의
    ``fact_unreviewed``가 붙어 **선택이 곧 제출 차단**이 된다. 질문
    레코드는 두 검사의 순회 대상이 아니다.

    request ID는 **선택 이벤트마다** 다르다(I3). 값과 결정 개정을 함께
    넣으므로, 같은 값으로 되돌아가도(A→B→A) 새 이벤트로 기록되고,
    같은 이벤트를 그대로 다시 보내면 멱등이다.
    """
    core = _core()
    root = Path(root)
    if value not in CHOICE_VALUES:
        return {"status": "rejected", "reason": "choice_value_invalid"}
    rows, error = notice_rows_or_error(root)
    if error is not None:
        return {"status": "failed", "reason": "notice_rows_unavailable",
                "error": error}
    match = next((r for r in rows if r.get("notice_identity") == identity), None)
    if match is None:
        return {"status": "rejected", "reason": "notice_identity_unknown"}
    if not match.get("choices"):
        return {"status": "rejected", "reason": "choice_not_offered"}
    project = core.load(root)
    existing = (project.get("questions") or {}).get(CHOICE_FIELD)
    ops = _choice_ops(project, value, identity)
    if ops is None:
        return {"status": "rejected", "reason": "source_ref_missing"}
    change = {
        "request_id": _choice_request_id(identity, value, existing),
        "ops": ops,
    }
    try:
        core.apply(root, change, project["revision"])
    except ValueError as error:
        return {"status": "rejected", "reason": _reason_of(error)}
    # 정본 커밋 훅(_rewrite_sidecars)이 이미 알림 파일을 다시 썼다. 여기서 한 번
    # 더 쓰면 잠금 밖에서 같은 값을 다시 쓰는 일이고, 그 사이 커밋이 끼면 낡은
    # 스냅숏이 최신 파일을 덮는다(공통 장 §4). 갱신 실패는 훅의 stderr 한 줄이다.
    n = root / NOTICES_NAME
    return {"status": "chosen", "choice": value, "path": NOTICES_NAME,
            "notices_exists": n.exists()}


def _choice_request_id(identity, value, existing):
    """선택 **이벤트**의 request ID(I3).

    결정 개정까지 넣는다. 같은 값으로 돌아오면 개정이 이미 올라가 있어
    새 ID가 되고, 같은 상태에서 같은 값을 다시 보내면 같은 ID라 멱등이다.
    """
    revision = 1
    if isinstance(existing, dict):
        revision = int(existing.get("decision_revision", 1)) + 1
    return "notice:%s:%s:%d" % (identity[:16], value, revision)


def _reason_of(error):
    text = str(error)
    if "개정 불일치" in text or "revision" in text:
        return "revision_conflict"
    return "apply_rejected"


def _choice_ops(project, value, identity):
    """선택 질문 레코드 op.

    사실(facts)은 만들지 않는다(C1). 질문 레코드에 선택 값·알림
    정체성을 함께 적어, 알림이 바뀌면 이전 선택이 따라오지 않게 한다(R2).
    """
    existing = (project.get("questions") or {}).get(CHOICE_FIELD)
    ref_id = _registered_source_id(project)
    if ref_id is None:
        return None
    source = project["sources"][ref_id]
    question = dict(existing) if isinstance(existing, dict) else {
        "id": CHOICE_FIELD,
        "field_id": CHOICE_FIELD,
        "attempts": 0,
        "decision_revision": 1,
    }
    question["attempts"] = int(question.get("attempts", 0)) + 1
    question["choice"] = value
    question["notice_identity"] = identity
    if isinstance(existing, dict):
        # 재선택: decision_revision +1, resume_reason + 등록된 source_ref 필요.
        question["decision_revision"] = int(
            question.get("decision_revision", 1)) + 1
        question["resume_reason"] = "user_change"
        question["source_ref"] = {"id": ref_id, "locator": "notice_choice",
                                  "quote": value}
    return [{"collection": "questions", "value": question}]


def _registered_source_id(project):
    """``p["sources"]`` 에 실제로 있는 id 하나(결정적: 정렬 첫 값)."""
    sources = project.get("sources") or {}
    for sid in sorted(sources):
        return sid
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write", help="알림 파일을 쓴다")
    write.add_argument("folder")
    write.add_argument("--json", action="store_true")
    pick = sub.add_parser("choose", help="선택을 기록하고 파일을 다시 쓴다")
    pick.add_argument("folder")
    pick.add_argument("--identity", required=True)
    pick.add_argument("--value", required=True, choices=CHOICE_VALUES)
    pick.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            # 정본과 같은 잠금(공통 장 §4-5). 잠금 밖에서 조립한 알림이 새
            # revision 커밋 뒤에 남는 창을 막는다. LOCK_NB라 경쟁 시 즉시
            # busy로 거절하고, 재시도는 사람이 한다.
            with _core().Lock(Path(args.folder)):
                value = write_notices(Path(args.folder))
        else:
            value = choose(Path(args.folder), args.identity, args.value)
    except Exception as error:  # 실행 오류는 exit 2
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False))
    # 알림 파일 갱신 실패는 성공이 아니다 — 앱·스크립트가 알 수 있게 0이 아닌 코드.
    return 0 if value.get("status") in {"written", "chosen"} else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
