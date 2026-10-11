#!/usr/bin/env python3
"""기존 원고의 장별 분할 — 분할 계획(plan) → 확정 계획만 쓰기 (초안, N4).

현행 구조에 맞춘 2단계다:
    1) plan:  원고를 읽어 장 제목 후보를 찾고 분할 계획 JSON만 낸다.
              기본 동작이며 파일을 쓰지 않는다(옛 --dry가 기본이 된 형태).
    2) apply: 검토·확정한 계획 파일을 받아 status=proposed 항목만
              sections/ 에 쓰고, 원본 사본을 sources/imported/ 에 남긴 뒤
              `gg.py apply --change`에 넘길 변경 JSON을 출력한다.

옛 extract_draft.py와 다른 점: TOC를 하드코딩하지 않고 --toc 목차 JSON을
받는다(학교 장 제목은 official-toc.md 같은 현행 정본에서 만든 목차 파일).
옛 STATUS/FACTS 6블록 양식은 쓰지 않고 현행 gg:draft 마커 봉투를 쓴다.
정본(project.json)은 직접 쓰지 않는다 — 변경 JSON을 만들고 실제 등록은
section-ledger의 apply 경로가 한다.

표 보존: `|`로 시작하는 연속 줄을 표 덩어리로 보고, 그 안에서 찾은
제목 후보는 held로 보류한다(표 중간 분할 금지). 제목 중복·순서 어긋남·
목차 앞 미배정 본문도 held로 사유를 남겨 사용자에게 묻는다.

사용법:
    python3 gg_draft_split.py <작업폴더> --from <원고> --toc <목차.json>
        → 분할 계획 JSON (쓰기 없음)
    python3 gg_draft_split.py <작업폴더> --apply --plan <계획.json>
        [--change-out <변경.json>]
        → 확정 계획의 proposed 항목만 sections/에 쓰고 변경 JSON 출력

지원 입력: .md / .txt / .docx(python-docx 필요). .hwp/.hwpx는 지원하지
않는다 — parser-setup.md 경로로 먼저 Markdown/텍스트로 변환한다.

목차 JSON 형식:
    [{"id": "III-6", "title": "관련 논문",
      "patterns": ["^\\\\s*6\\\\s*[\\\\.．]\\\\s*관련\\\\s*논문"]}, ...]
    patterns는 파이썬 정규식이며 각 줄에 대해 평가한다.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

SCHEMA = "gg-draft-split-plan/1"
START = "<!-- gg:draft:start -->"
END = "<!-- gg:draft:end -->"
TABLE_LINE = re.compile(r"^\s*\|")


def read_text(path):
    """입력 원고를 줄 목록으로 읽는다. 반환 (lines, fmt, blocked_reason)."""
    ext = path.suffix.lower()
    if ext in (".md", ".txt", ".markdown"):
        try:
            return path.read_text(encoding="utf-8",
                                  errors="replace").splitlines(), "text", None
        except OSError:
            return None, None, "원고 파일을 읽을 수 없음"
    if ext == ".docx":
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            return None, None, (
                "python-docx가 없다. 먼저 Markdown/텍스트로 변환해 넘기거나 "
                "package-install.md 경로로 의존성을 준비한다")
        doc = Document(str(path))
        lines = []
        # 본문 요소를 문서 순서대로 순회해 표를 제자리에 원자적으로 둔다.
        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:p"):
                from docx.text.paragraph import Paragraph
                lines.append(Paragraph(child, doc).text)
            elif child.tag == qn("w:tbl"):
                from docx.table import Table
                for row in Table(child, doc).rows:
                    lines.append("| " + " | ".join(
                        c.text.strip() for c in row.cells) + " |")
        return lines, "docx", None
    if ext in (".hwp", ".hwpx"):
        return None, None, (
            "HWP는 지원하지 않는다. parser-setup.md 경로(kordoc 등)로 먼저 "
            "Markdown/텍스트로 변환한 뒤 --from에 넘긴다")
    return None, None, "지원하지 않는 형식: " + ext


def load_toc(path):
    try:
        toc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError("목차 파일 읽기 실패: %s" % e) from e
    if not isinstance(toc, list) or not toc:
        raise ValueError("목차 JSON은 비어 있지 않은 배열이어야 함")
    out = []
    for i, entry in enumerate(toc):
        if not isinstance(entry, dict) or not entry.get("id") \
                or not isinstance(entry.get("patterns"), list) \
                or not entry["patterns"]:
            raise ValueError("목차 %d번 항목에 id·patterns 필요" % i)
        try:
            pats = [re.compile(p) for p in entry["patterns"]]
        except re.error as e:
            raise ValueError("목차 %s 정규식 오류: %s" % (entry["id"], e))
        out.append({"id": entry["id"], "title": entry.get("title") or
                    entry["id"], "patterns": pats, "order": i})
    return out


def _table_ranges(lines):
    """연속된 표 줄 범위 [(start, end_exclusive)]를 구한다."""
    ranges, start = [], None
    for i, ln in enumerate(lines):
        if TABLE_LINE.match(ln):
            if start is None:
                start = i
        else:
            if start is not None:
                ranges.append((start, i))
                start = None
    if start is not None:
        ranges.append((start, len(lines)))
    return ranges


def build_plan(lines, toc, *, src_path, fmt, workdir):
    """분할 계획을 만든다. 순수 함수 — 파일을 쓰지 않는다."""
    tables = _table_ranges(lines)

    def in_table(i):
        return any(s <= i < e for s, e in tables)

    # 1) 제목 후보 탐색 — 표 안의 일치는 경계로 쓰지 않고 보류한다.
    candidates, held = [], []
    for i, ln in enumerate(lines):
        matches = [entry for entry in toc
                   if any(p.search(ln) for p in entry["patterns"])]
        ambiguous = len({entry["id"] for entry in matches}) > 1
        if ambiguous:
            matches.sort(key=lambda entry: entry["id"])
        for entry in matches:
            cand = {"section_id": entry["id"], "title": entry["title"],
                    "line": i + 1, "toc_order": entry["order"]}
            if ambiguous:
                # An unresolved match has no authoritative TOC position.
                cand.pop("toc_order")
            if in_table(i):
                held.append(dict(
                    cand, status="held", reason="heading_inside_table",
                    detail="표 덩어리 안의 제목 일치 — 표 중간 분할 금지"))
            elif ambiguous:
                held.append(dict(
                    cand, status="held", reason="ambiguous_heading",
                    detail="한 줄이 서로 다른 절에 일치 — 절 확정 보류"))
            else:
                candidates.append(cand)

    # 2) 같은 절 ID가 여러 줄에 걸리면 그 절 전체를 보류한다.
    by_id = {}
    for c in candidates:
        by_id.setdefault(c["section_id"], []).append(c)
    dup_ids = {sid for sid, cs in by_id.items() if len(cs) > 1}
    accepted = []
    for c in candidates:
        if c["section_id"] in dup_ids:
            lines_hit = ", ".join(str(x["line"]) for x in
                                  by_id[c["section_id"]])
            held.append(dict(c, status="held", reason="duplicate_heading",
                             detail="같은 절 제목이 여러 줄(%s)에 걸림 — "
                                    "어느 쪽이 진짜 장 시작인지 확인 필요"
                                    % lines_hit))
        else:
            accepted.append(c)

    # 3) 목차 순서가 뒤집히면 보류한다(앞에서만 전이).
    last = -1
    ordered = []
    for c in accepted:
        if c["toc_order"] <= last:
            held.append(dict(
                c, status="held", reason="out_of_order",
                detail="목차 순서와 다른 위치의 제목 — 목차 구성이 다른지 "
                       "사용자 확인 필요"))
        else:
            last = c["toc_order"]
            ordered.append(c)

    # 4) 모든 목차 후보(표 안 후보 제외)를 경계로 삼는다. 보류 제목 뒤의
    # 본문은 다음 제목까지 독립된 held 범위에 남겨 제안 절에 섞이지 않게 한다.
    held_boundaries = [h for h in held if h.get("reason") in {
        "duplicate_heading", "out_of_order", "ambiguous_heading"}]
    boundaries = sorted(ordered + held_boundaries, key=lambda c: c["line"])
    boundary_lines = sorted({c["line"] for c in boundaries})
    next_line = {}
    for idx, line in enumerate(boundary_lines):
        next_line[line] = (boundary_lines[idx + 1]
                           if idx + 1 < len(boundary_lines) else len(lines) + 1)
    for h in held_boundaries:
        end = next_line[h["line"]] - 1
        h["lines"] = "%d-%d" % (h["line"], end)

    # 5) 경계를 기준으로 확정·보류 구간을 서로 겹치지 않게 나눈다.
    items = []
    for c in ordered:
        end = next_line[c["line"]] - 1
        body = lines[c["line"] - 1:end]
        items.append({
            "section_id": c["section_id"], "title": c["title"],
            "status": "proposed", "line": c["line"],
            "toc_order": c["toc_order"],
            "lines": "%d-%d" % (c["line"], end),
            "target_path": "sections/%s.md" % c["section_id"],
            "conflict": (workdir / "sections" / (c["section_id"] + ".md"))
            .exists(),
            "body_chars": len("".join(body)),
            "table_blocks": sum(
                1 for s, e in tables if c["line"] - 1 <= s < end),
        })
    if not candidates and not held_boundaries:
        held.append({
            "section_id": None, "status": "held", "reason": "no_headings",
            "detail": "공식 목차 제목을 하나도 찾지 못함 — 원고가 다른 체계를 "
                      "쓰거나 추출이 불완전할 수 있으니 사람이 나눈다",
        })
    prefix_end = boundaries[0]["line"] - 1 if boundaries else len(lines)
    prefix = [ln for ln in lines[:prefix_end] if ln.strip()]
    if prefix and boundaries:
        held.append({
            "section_id": None, "status": "held",
            "reason": "unmatched_prefix",
            "detail": "첫 장 제목 이전의 본문 %d줄 — 표지·목차·머리말 앞 "
                      "내용인지 확인 필요" % len(prefix),
            "lines": "1-%d" % prefix_end,
        })
    return {
        "schema": SCHEMA,
        "input": {
            "name": "manuscript" + src_path.suffix.lower(),
            "sha256": hashlib.sha256(src_path.read_bytes()).hexdigest(),
            "format": fmt,
            "line_count": len(lines),
        },
        "items": items,
        "held": sorted(held, key=lambda h: h.get("line") or 0),
        "notice": "items의 proposed만 쓴다. held 목록은 참고 사유이며 "
                  "목차나 원고를 조정한 뒤 계획을 다시 만든다. 기존 절 "
                  "파일은 적용 시점에 다시 확인하고 덮어쓰지 않는다.",
    }


def _inside(root, name):
    root = Path(root).resolve()
    p = (root / name).resolve()
    if root not in p.parents and p != root:
        raise ValueError("작업 폴더 밖 경로는 허용하지 않음: " + str(name))
    return p


def apply_plan(root, plan, lines, *, source_path, change_out=None):
    """확정 계획의 proposed 항목만 쓰고 apply 변경 JSON을 만든다."""
    input_info = plan["input"]
    if not isinstance(input_info, dict) or not isinstance(input_info.get("sha256"), str):
        raise ValueError("계획 입력 정보 오류")
    source_path = Path(source_path).resolve()
    try:
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    except OSError:
        source_hash = None
    if not source_path.is_file() or source_hash != input_info["sha256"]:
        raise ValueError("계획 이후 원본이 바뀌었거나 파일이 없음 — 계획을 다시 만든다")
    written, skipped = [], []
    prepared = []
    project_path = Path(root) / "project.json"
    project = None
    if project_path.is_file():
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("project.json을 읽거나 해석할 수 없음") from None
        if not isinstance(project.get("sections"), dict) or not isinstance(project.get("sources"), dict):
            raise ValueError("project.json의 sections/sources 컬렉션 오류")
    if not isinstance(plan.get("items"), list):
        raise ValueError("계획 items 형식 오류")
    for it in plan["items"]:
        if not isinstance(it, dict) or it.get("status") not in {"proposed", "held"}:
            raise ValueError("계획 항목 형식 오류")
        if it.get("status") != "proposed":
            skipped.append({"section_id": it.get("section_id"),
                            "reason": "not_confirmed"})
            continue
        section_id = it.get("section_id")
        if not isinstance(section_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", section_id):
            raise ValueError("계획 절 ID 형식 오류")
        target_rel = "sections/%s.md" % section_id
        if it.get("target_path") != target_rel:
            raise ValueError("계획 대상 경로가 절 ID와 다름")
        if not isinstance(it.get("title"), str) or not it["title"].strip():
            raise ValueError("계획 절 제목 오류")
        if type(it.get("toc_order")) is not int or it["toc_order"] < 0:
            raise ValueError("계획 목차 순서 오류")
        target = _inside(root, target_rel)
        if project is not None and section_id in project["sections"]:
            skipped.append({"section_id": section_id,
                            "reason": "conflict_existing_record"})
            continue
        if target.exists():
            skipped.append({"section_id": it["section_id"],
                            "reason": "conflict_existing_file"})
            continue
        try:
            lo, hi = (int(x) for x in it["lines"].split("-"))
        except (ValueError, AttributeError):
            raise ValueError("계획 줄 범위 오류") from None
        if lo < 1 or hi < lo or hi > len(lines):
            raise ValueError("계획 줄 범위가 원고를 벗어남")
        body = "\n".join(lines[lo - 1:hi]).rstrip()
        text = "# %s\n\n%s\n%s\n%s\n" % (it["title"], START, body, END)
        prepared.append((it, target, text))
    # 원본 사본 — 같은 바이트면 재사용, 다른 바이트면 충돌로 멈춘다.
    src = source_path
    safe_name = "manuscript-%s%s" % (input_info["sha256"][:12],
                                      src.suffix.lower())
    copy_rel = "sources/imported/" + safe_name
    copy_path = _inside(root, copy_rel)
    src_id = "legacy:" + copy_rel
    existing_source = None
    if project is not None and src_id in project["sources"]:
        existing = project["sources"][src_id]
        if not isinstance(existing, dict):
            raise ValueError("기존 정본 출처 레코드 형식 오류")
        if existing.get("path") != copy_rel or existing.get("hash") != input_info["sha256"]:
            raise ValueError("기존 정본 출처 ID가 다른 원본에 연결됨")
        existing_revision = existing.get("revision")
        if type(existing_revision) is not int or existing_revision < 1:
            raise ValueError("기존 정본 출처 개정 번호가 올바르지 않음")
        existing_source = existing
        source_revision = existing_revision
    else:
        source_revision = 1
    if copy_path.exists():
        if hashlib.sha256(copy_path.read_bytes()).hexdigest() \
                != input_info["sha256"]:
            raise ValueError("원본 사본 경로에 다른 파일이 있음: " + copy_rel)
    # Validate every output path and write target before making any section file.
    change_target = _inside(root, change_out) if change_out else None
    if change_target and change_target.exists():
        raise ValueError("변경 JSON 파일이 이미 있음: 덮어쓰지 않음")
    if not copy_path.exists():
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with copy_path.open("xb") as f:
                f.write(src.read_bytes())
        except FileExistsError:
            if hashlib.sha256(copy_path.read_bytes()).hexdigest() != input_info["sha256"]:
                raise ValueError("원본 사본 경로에 다른 파일이 있음: " + copy_rel)
    for it, target, text in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            skipped.append({"section_id": it["section_id"],
                            "reason": "conflict_existing_file"})
            continue
        written.append(it)
    ops = []
    if existing_source is None:
        ops.append({
            "collection": "sources",
            "value": {
                "id": src_id,
                "path": copy_rel,
                "legacy": True,
                "verification": "unreviewed",
                "original_path": copy_rel,
            },
        })
    for i, it in enumerate(written):
        ops.append({
            "collection": "sections",
            "value": {
                "id": it["section_id"],
                "path": it["target_path"],
                "title": it["title"],
                "order": it["toc_order"],
                "status": "drafting",
                "depends_on": [{"collection": "sources", "id": src_id,
                                "revision": source_revision}],
            },
        })
    ops.append({
        "collection": "tasks",
        "value": {
            "id": "draft-split-review:" + input_info["sha256"][:12],
            "required_fields": [],
            "source_refs": [{"id": src_id, "revision": source_revision,
                             "locator": "sections"}],
            "description": "분할된 절 본문의 수치·주장을 원본 사본과 대조해 "
                           "정본 사실로 등록. 역추출만으로 검증 승격 금지.",
        },
    })
    request = "draft-split:" + hashlib.sha256(
        json.dumps(plan, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()[:16]
    change = {"request_id": request, "ops": ops}
    if change_target:
        change_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with change_target.open("x", encoding="utf-8") as out:
                out.write(json.dumps(change, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            raise ValueError("변경 JSON 파일이 이미 있음: 덮어쓰지 않음") from None
    return {
        "status": "applied",
        "written": [w["target_path"] for w in written],
        "skipped": skipped,
        "original_copy": copy_rel,
        "change": change,
        "notice": "절 등록은 정본 apply 경로로 한다: "
                  "gg.py apply <폴더> --change <변경.json> "
                  "--expected-revision <현재 revision>",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="기존 원고 장별 분할 (2단계)")
    ap.add_argument("folder", help="작업폴더")
    ap.add_argument("--from", dest="src", help="원고 파일 (.md/.txt/.docx)")
    ap.add_argument("--toc", help="장 제목 목차 JSON (인자로만 받는다)")
    ap.add_argument("--apply", action="store_true",
                    help="확정 계획 실행 (기본은 계획만 출력)")
    ap.add_argument("--plan", help="확정한 계획 JSON (apply 단계 입력)")
    ap.add_argument("--change-out", help="apply에 넘길 변경 JSON 경로")
    a = ap.parse_args(argv)
    try:
        root = Path(a.folder).resolve()
        if not root.is_dir():
            raise ValueError("작업폴더를 찾을 수 없음")
        if a.apply:
            if not a.plan:
                raise ValueError("--apply에는 --plan <확정 계획 JSON> 필요")
            plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
            if plan.get("schema") != SCHEMA:
                raise ValueError("계획 스키마 불일치: %s"
                                 % plan.get("schema"))
            if not a.src:
                raise ValueError("--apply에는 --from <같은 원고 파일> 필요")
            src = Path(a.src).resolve()
            lines, _fmt, blocked = read_text(src)
            if lines is None:
                raise ValueError(blocked)
            value = apply_plan(root, plan, lines, source_path=src,
                               change_out=a.change_out)
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        # 기본: plan만 만든다 (쓰기 없음)
        if not a.src or not a.toc:
            raise ValueError("계획 단계에는 --from <원고>와 --toc <목차> 필요")
        src = Path(a.src).resolve()
        if not src.is_file():
            raise ValueError("원고를 찾을 수 없다: " + str(src))
        lines, fmt, blocked = read_text(src)
        if lines is None:
            print(json.dumps({"status": "blocked", "reason": blocked},
                             ensure_ascii=False))
            return 2
        plan = build_plan(lines, load_toc(a.toc),
                          src_path=src, fmt=fmt, workdir=root)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
