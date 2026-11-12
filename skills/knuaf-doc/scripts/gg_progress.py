#!/usr/bin/env python3
"""진행 파일(`.knuaf-progress.json`) — 정본에서 7단계 진행을 계산한다.

앱(`progress-file.ts`)이 읽는 `knuaf.progress/1` 문서를 만든다. 판정 입력은
정본(p)·`gate` 행·`finance_gate`·`completion` 뿐이고, 학생에게 보이는 문구는
설계 §2-1 고정 표가 유일 출처다(런타임 필터 없음).

쓰기는 `write_progress(root)` 하나이며 정본 잠금 **안**에서만 불린다 — 이
모듈은 스스로 잠금을 잡지 않는다(재진입 교착 방지). CLI는 읽기 전용 `show`
하나이고 `write` 서브커맨드는 두지 않는다(쓰기 자리는 커밋 뒤 곁파일 한 곳).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import gg_core as core
import gg_major_contract as mc
import gg_update

NAME = ".knuaf-progress.json"
SCHEMA = "knuaf.progress/1"
STEP_COUNT = 7

#: 절 결함 행 — 판정은 `status`가 아니라 `check_id` 존재로 한다
#: (`checks.add()` 기본값이 fail/error라 상태로 거르면 놓친다).
DEFECT_IDS = frozenset({
    "draft_marker_extra",
    "draft_changed",
    "content_incomplete",
    "claim_location",
    "draft_claim",
    "dependency_revision",
    "section",
})

#: 손상 정본 — `checks`가 여기서 조기 반환해 검토·결함 행이 안 생긴다.
DAMAGED_IDS = frozenset({"source_claims_invalid", "view_invalid"})

REVIEW_KINDS = ("content", "logic", "calculation", "docx", "xlsx", "render")

#: 6단계가 "막는 행 없음"으로 보는 행 집합.
STEP6_IDS = frozenset(
    {"coverage", "review_observation", "fact_unreviewed"}
    | {"review_" + kind for kind in REVIEW_KINDS}
)

#: 학생 화면 고정 문구(설계 §2-1). `{major}`·`{m}`·`{n}`만 치환한다.
NOTES = {
    1: {
        "done": "{major}으로 등록됨",
        "now": "전공을 골라 주세요",
        "todo": "전공 확인 전",
    },
    2: {
        "done": "재무 계획까지 정해짐",
        "now": "재무 계획을 마저 정해요",
        "todo": "전공 확인 뒤 이어서",
        "unknown": "재무 계산을 할 수 없어요",
    },
    3: {
        "done": "본문 착수함",
        "now": "본문에 쓸 근거를 모아요",
        "todo": "전공 확인 뒤 이어서",
    },
    4: {
        "done": "본문 {m}개 절 초안 등록됨",
        "now": "{n}/{m}개 절 초안 씀",
        "todo": "본문 초안 전",
        "unknown": "본문 기록을 다시 봐야 해요",
    },
    5: {
        "done": "재무 엑셀 검사 통과",
        "now": "재무 엑셀을 만들고 검사해요",
        "todo": "본문·엑셀 뒤에 만들어요",
        "unknown": "이 전공은 재무 엑셀을 지원하지 않아요",
    },
    6: {
        "done": "검토 네 갈래 이상 통과",
        "now": "남은 검토를 마저 해요",
        "todo": "본문·엑셀 뒤에 해요",
        "unknown": "검토 기록을 다시 봐야 해요",
    },
    7: {
        "done": "워드 검토본 준비됨",
        "now": "워드 검토본을 만들어요",
        "todo": "검토 뒤에 만들어요",
    },
}

#: 7단계 뒤에 남는 일 — 7/7에서도 유지한다(설계 §2-3, I5).
AFTER = {"text": "한글 마무리(글꼴·여백·쪽번호)·교수 승인", "owner": "student"}

HWP_FLAG = {"text": "한글 파일로 마지막 저장", "needed_for": 7}

DRAFT_STATUSES = frozenset({"drafting", "review_ready"})

ALIASES_REL = Path("references") / "benchmark-packs" / "aliases" / "major-aliases.json"


def skill_version():
    """배포된 `version.json`의 버전 문자열. 실패는 `"unknown"`(예외 없음)."""
    try:
        version_file = Path(__file__).resolve().parent.parent / "version.json"
        return gg_update.format_version(gg_update.read_version_file(version_file))
    except Exception:
        return "unknown"


def major_label(major_id):
    """`major-aliases.json`의 표시명. 파일을 읽는다(사전 복제 금지)."""
    if not isinstance(major_id, str) or not major_id:
        return None
    try:
        path = Path(__file__).resolve().parent.parent / ALIASES_REL
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = (data.get("majors") or {}).get(major_id) or {}
        label = entry.get("label")
        return label if isinstance(label, str) and label.strip() else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _binding(p):
    """등록 전공 → `binding`, 아니면 None (1단계 근거)."""
    try:
        return mc.binding_from_project(mc.default_registry(), p)
    except mc.MajorContractError:
        return None


def _rows(report):
    return [row for row in (report or []) if isinstance(row, dict)]


def _evidence(p, rows, fg, binding):
    """단계별 근거 → `{n: "done"|"unknown"|None}`.

    None은 "긍정 근거 없음"이고, now/todo는 승격 규칙(§1-3)이 정한다.
    """
    ids = [row.get("check_id") for row in rows]
    damaged = any(cid in DAMAGED_IDS for cid in ids)
    passed = {cid for cid, row in zip(ids, rows) if row.get("status") == "pass"}
    sections = p.get("sections") or {}

    out = {n: None for n in range(1, STEP_COUNT + 1)}
    if binding is not None:
        out[1] = "done"

    gate_state = fg.get("state")
    if gate_state in {"surplus", "deficit_accepted"}:
        out[2] = "done"
    elif gate_state == "not_computable":
        out[2] = "unknown"

    if sections:
        out[3] = "done"

    if damaged:
        out[4] = "unknown"
    elif sections and all(
        s.get("status") in DRAFT_STATUSES for s in sections.values()
    ) and not any(cid in DEFECT_IDS for cid in ids):
        out[4] = "done"

    if "output_xlsx" in passed:
        out[5] = "done"
    elif fg.get("supported") is False:
        out[5] = "unknown"

    if damaged:
        out[6] = "unknown"
    elif sections and not any(cid in STEP6_IDS for cid in ids):
        out[6] = "done"

    if damaged:
        out[7] = "unknown"
    elif "output_docx" in passed:
        out[7] = "done"
    return out


def _states(evidence):
    """근거 + 승격 → 7개 상태. `unknown`도 '끝난 칸'으로 센다."""
    states = []
    has_now = False
    for n in range(1, STEP_COUNT + 1):
        settled = all(s in {"done", "unknown"} for s in states)
        if evidence[n] in {"done", "unknown"}:
            state = evidence[n]
        elif settled and not has_now:
            state = "now"
        else:
            state = "todo"
        if state == "now":
            has_now = True
        states.append(state)
    return states


def _note(n, state, *, bound, label, section_total, section_drafted):
    """1단계만 예외 — 미등록·잘못된 등록은 표 문구, 표시명 실패만 대체 문구."""
    table = NOTES[n]
    template = table.get(state)
    if template is None:
        return None
    if n == 1:
        if state != "done":
            return template
        if label is None:
            # 등록은 됐는데 별칭 파일에서 표시명을 못 읽은 경우에만.
            return "전공 등록됨" if bound else None
        return template.format(major=label)
    if n == 4:
        return template.format(m=section_total, n=section_drafted)
    return template


def assemble(p, report, fg, st):
    """정본·검사 행·재무 게이트·완료 판정 → `knuaf.progress/1` 문서."""
    rows = _rows(report)
    binding = _binding(p)
    label = major_label(binding.major_id) if binding is not None else None
    states = _states(_evidence(p, rows, fg, binding))

    sections = p.get("sections") or {}
    section_total = len(sections)
    section_drafted = sum(
        1 for s in sections.values() if s.get("status") in DRAFT_STATUSES
    )

    steps = []
    for index, state in enumerate(states, start=1):
        step = {"n": index, "state": state}
        note = _note(
            index,
            state,
            bound=binding is not None,
            label=label,
            section_total=section_total,
            section_drafted=section_drafted,
        )
        if note is not None:
            step["note"] = note
        steps.append(step)

    hwp_blocked = any(
        row.get("check_id") == "output_hwp" and row.get("status") != "pass"
        for row in rows
    )
    return {
        "schema": SCHEMA,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "revision": p.get("revision", 0),
        "skill_version": skill_version(),
        "review_pending": not bool(st.get("skill_ready")),
        "steps": steps,
        "flags": [dict(HWP_FLAG)] if hwp_blocked else [],
        "after": dict(AFTER),
        "next": None,
        "ask": None,
    }


class ProgressLinkError(RuntimeError):
    """진행 파일 자리가 심볼릭 링크 — 정본을 지키려고 거절한다."""


def _slot(root):
    """진행 파일 자리 — **링크를 풀기 전** 경로.

    `core.local`은 최종 링크까지 풀어 버려 폴더 안 링크를 통과시킨다
    (`gg_core.py:54-59`). 그래서 링크 판정은 이 경로로 하고, 폴더 밖
    거부는 `core.local`에 그대로 맡긴다.
    """
    return Path(root).resolve() / NAME


def _warn_unremoved(path):
    try:
        print("진행 파일을 지우지 못함: " + str(path), file=sys.stderr)
    except Exception:
        pass


def write_progress(root):
    """정본 잠금 안에서 불린다. 진행 파일을 원자적으로 다시 쓴다.

    성공하면 `{"status": "written"}`을 돌려준다. 실패는 삼키지 않고
    그대로 던진다 — 공통 곁파일 호출기는 예외/`failed`를 같은 remove
    경로로 보낸다(W-N C-01).

    자리가 심볼릭 링크면 **거절**한다(`ProgressLinkError`). 링크를 따라
    쓰면 폴더 안 다른 파일(`project.json` 등)을 진행 문서로 덮어쓴다.
    하드 링크는 `os.replace`가 디렉터리 항목만 갈아 끼우므로 원본이
    보존되어 따로 막지 않는다. 링크는 `remove_progress`가 링크 자체만
    지운다.
    """
    unresolved = _slot(root)
    if unresolved.is_symlink():
        raise ProgressLinkError(
            "진행 파일 자리가 심볼릭 링크임: " + str(unresolved))
    p = core.load(root)
    report = core.gate(root, p)
    fg = core.finance_gate(p)
    st = core.completion(root, p, report=report)
    data = assemble(p, report, fg, st)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    core.atomic(core.local(root, NAME), payload)
    return {"status": "written"}


def remove_progress(root):
    """진행 파일 제거. 실패는 감지 가능하게 처리한다(I3).

    지우기 실패 → 같은 자리를 `{}`로 원자적 덮어쓰기(앱은 무효 파일을
    "파일 없음"과 같게 다룬다). 그것도 실패하면 stderr 한 줄, 예외 없음.

    자리가 심볼릭 링크면 **링크 자체만** 지운다(`unlink`, 링크를 따라
    들어가지 않는다). 폴더 안 다른 파일은 건드리지 않고, `{}` 덮어쓰기
    대체 경로도 타지 않는다 — 링크를 지웠으면 목표 파일은 그대로다.
    """
    path = _slot(root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        pass
    if path.is_symlink():
        # 링크 삭제가 실패한 경우 — 덮어쓰기(`os.replace`)는 링크를 푼
        # 목적지에 쓰이므로 정본을 건드린다. 여기서 멈춘다.
        _warn_unremoved(path)
        return False
    try:
        core.atomic(core.local(root, NAME), b"{}\n")
        return True
    except (OSError, ValueError):
        _warn_unremoved(path)
        return False


def _valid_document(data):
    """`knuaf.progress/1` 최소 구조 — 앱 검증(`progress-file.ts`)과 같은 뼈대."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return False
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) != STEP_COUNT:
        return False
    seen = set()
    for step in steps:
        if not isinstance(step, dict):
            return False
        n = step.get("n")
        if not isinstance(n, int) or isinstance(n, bool):
            return False
        if not 1 <= n <= STEP_COUNT or n in seen:
            return False
        seen.add(n)
        if step.get("state") not in {"done", "now", "todo", "unknown"}:
            return False
    return True


def show(root):
    """파일 요약만 stdout에 낸다. 어느 경우에도 쓰지 않는다."""
    path = core.local(root, NAME)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        print("진행 파일 없음: " + str(path), file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
    except ValueError:
        print("진행 파일을 읽을 수 없음: " + str(path), file=sys.stderr)
        return 1
    if not _valid_document(data):
        print("진행 파일 형식 오류: " + str(path), file=sys.stderr)
        return 1
    steps = data["steps"]
    now = None
    for step in steps:
        if step.get("state") == "now":
            now = step.get("n")
    print(json.dumps(
        {
            "path": str(path),
            "revision": data.get("revision"),
            "written_at": data.get("written_at"),
            "steps": len(steps) if isinstance(steps, list) else 0,
            "now": now,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gg_progress.py",
        description="진행 파일(.knuaf-progress.json) 요약 — 읽기 전용",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    show_parser = sub.add_parser("show", help="진행 파일 요약 출력")
    show_parser.add_argument("target")
    args = ap.parse_args(argv)
    if args.command == "show":
        try:
            return show(Path(args.target))
        except Exception as error:
            print("진행 파일을 읽지 못함: " + str(error), file=sys.stderr)
            return 2
    return 2


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
