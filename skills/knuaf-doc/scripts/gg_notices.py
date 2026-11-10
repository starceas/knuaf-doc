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
    """현재 점검에서 나온 알림 행만."""
    core = _core()
    project = core.load(root)
    rows = core.checks(root, project)
    return [r for r in rows if isinstance(r, dict)
            and r.get("check_id") == "finance_workbook_notice"]


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
    """알림 파일을 원자적으로 교체한다. 반환: 계약 JSON."""
    root = Path(root)
    rows = notice_rows(root)
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
    """선택을 정본에 기록하고 알림 파일을 다시 쓴다."""
    core = _core()
    root = Path(root)
    if value not in CHOICE_VALUES:
        return {"status": "rejected", "reason": "choice_value_invalid"}
    rows = notice_rows(root)
    match = next((r for r in rows if r.get("notice_identity") == identity), None)
    if match is None:
        return {"status": "rejected", "reason": "notice_identity_unknown"}
    if not match.get("choices"):
        return {"status": "rejected", "reason": "choice_not_offered"}
    project = core.load(root)
    ops = _choice_ops(project, value)
    if ops is None:
        return {"status": "rejected", "reason": "source_ref_missing"}
    change = {
        "request_id": "notice:%s:%s" % (identity[:16], value),
        "ops": ops,
    }
    try:
        core.apply(root, change, project["revision"])
    except ValueError as error:
        return {"status": "rejected", "reason": _reason_of(error)}
    written = write_notices(root)
    written.update(status="chosen", choice=value)
    return written


def _reason_of(error):
    text = str(error)
    if "개정 불일치" in text or "revision" in text:
        return "revision_conflict"
    return "apply_rejected"


def _choice_ops(project, value):
    """questions+facts 기록 op. 재선택이면 source_ref 계약을 채운다."""
    existing = project.get("questions", {}).get(CHOICE_FIELD)
    ref_id = _registered_source_id(project)
    if ref_id is None:
        return None
    source = project["sources"][ref_id]
    fact = {
        "id": CHOICE_FIELD,
        "revision": 0,
        "field_id": CHOICE_FIELD,
        "kind": "reported_fact",
        "value": value,
        "unit": "TEXT",
        "value_type": "text",
        "period": None,
        "scope": "project",
        "answer_state": "provided",
        "verification": "source_located",
        "source_refs": [{"id": ref_id, "locator": "notice_choice",
                         "revision": int(source.get("revision", 0))}],
    }
    question = dict(existing) if isinstance(existing, dict) else {
        "id": CHOICE_FIELD,
        "field_id": CHOICE_FIELD,
        "attempts": 0,
        "decision_revision": 1,
    }
    question["attempts"] = int(question.get("attempts", 0)) + 1
    if isinstance(existing, dict):
        # 재선택: decision_revision +1, resume_reason + 등록된 source_ref 필요.
        question["decision_revision"] = int(
            question.get("decision_revision", 1)) + 1
        question["resume_reason"] = "user_change"
        question["source_ref"] = {"id": ref_id, "locator": "notice_choice",
                                  "quote": value}
    return [
        {"collection": "questions", "value": question},
        {"collection": "facts", "value": fact},
    ]


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
            value = write_notices(Path(args.folder))
        else:
            value = choose(Path(args.folder), args.identity, args.value)
    except Exception as error:  # 실행 오류는 exit 2
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value.get("status") in {"written", "chosen"} else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
