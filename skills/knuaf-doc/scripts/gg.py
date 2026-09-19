#!/usr/bin/env python3
"""Local command interface; never calls model providers or web services."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import gg_core as core


def main(argv=None):
    if sys.version_info < (3, 10):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "Python 3.10 이상 필요: 현재 " + sys.version.split()[0],
                },
                ensure_ascii=False,
            )
        )
        return 2
    ap = argparse.ArgumentParser(description="논문 정본·검사·검토본 관리")
    ap.add_argument(
        "command",
        choices=[
            "init",
            "doctor",
            "unlock",
            "import",
            "status",
            "next",
            "apply",
            "check",
            "export",
            "question",
            "bundle",
            "paper",
            "observe",
            "rda-lookup",
            "catalog-sources",
        ],
    )
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--change")
    ap.add_argument("--expected-revision", type=int)
    ap.add_argument("--scope", default="all")
    ap.add_argument(
        "--kind", choices=["draft", "review", "submission_candidate"], default="review"
    )
    ap.add_argument("--field")
    ap.add_argument("--input")
    ap.add_argument("--observer")
    ap.add_argument("--crop")
    ap.add_argument("--region")
    ap.add_argument("--major")
    ap.add_argument("--rda-kind")
    ap.add_argument("--year", type=int)
    ap.add_argument("--form")
    ap.add_argument("--allow-web", action="store_true")
    ap.add_argument("--from", dest="from_folder")
    ap.add_argument("--current", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        if a.command != "rda-lookup" and a.folder is None:
            raise ValueError("folder 필요")
        if a.command == "init":
            value = core.init(a.folder)
        elif a.command == "import":
            if not a.out:
                raise ValueError("--out 새 폴더 필요")
            value = core.migrate(a.folder, a.out)
        elif a.command == "doctor":
            lock_dir = Path(a.folder) / ".gg-lock"
            owner_path = lock_dir / "owner.json"
            lock_owner = None
            lock_owner_created = None
            lock_holder_alive = None
            if owner_path.exists():
                try:
                    lock_owner = json.loads(owner_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    lock_owner = "판독 불가"
                try:
                    lock_owner_created = owner_path.stat().st_mtime
                except OSError:
                    lock_owner_created = None
                if os.name == "nt":
                    import msvcrt

                    fd = None
                    try:
                        fd = os.open(str(owner_path), os.O_RDWR)
                        try:
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        except OSError:
                            lock_holder_alive = True
                        else:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                            lock_holder_alive = False
                    except OSError:
                        lock_holder_alive = "판정 불가"
                    finally:
                        if fd is not None:
                            os.close(fd)
            value = {
                "python": sys.version,
                "dependencies": {
                    m: bool(importlib.util.find_spec(m)) for m in ("docx", "openpyxl")
                },
                "recalculation": "not_run",
                "render": "not_run",
                "hwp": "unsupported",
                "web": "not_run",
                "model_calls": "disabled",
                "lock_present": (Path(a.folder) / ".gg-lock").exists(),
                "lock_owner": lock_owner,
                "lock_owner_created": lock_owner_created,
                "orphan_files": [
                    str(p)
                    for p in list(Path(a.folder).rglob(".gg-tmp-*"))
                    + list(Path(a.folder).rglob(".gg-export-*"))
                ],
                "notice": "설치 탐지는 실행 검증이 아님. 채팅만 가능하면 초안과 원답변 인계, 제출 후보 불가.",
            }
            if os.name == "nt" and owner_path.exists():
                value["lock_holder_alive"] = lock_holder_alive
        elif a.command == "unlock":
            lock_dir = Path(a.folder) / ".gg-lock"
            if not lock_dir.is_dir():
                raise ValueError("쓰기 잠금 없음")
            owner_path = lock_dir / "owner.json"
            owner_record = None
            if owner_path.exists():
                try:
                    owner_record = json.loads(owner_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    owner_record = "판독 불가"
                if os.name == "nt":
                    try:
                        owner_path.unlink()
                    except PermissionError:
                        raise ValueError(
                            "쓰기 잠금 사용 중: 다른 작성자가 이 폴더를 열고 있어 해제할 수 없음.\n"
                            "해당 작성자를 먼저 종료한 뒤 다시 시도. (doctor의 lock_holder_alive 확인)"
                        )
                else:
                    owner_path.unlink()
            lock_dir.rmdir()
            value = {"status": "released", "path": str(lock_dir), "owner": owner_record}
        elif a.command == "observe":
            if not a.input or not a.observer:
                raise ValueError("--input 관측 JSON과 --observer 필요")
            payload = json.loads(core.local(a.folder, a.input).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("관측 기록은 객체여야 함")
            value = core.ingest_review_observation(a.folder, payload, a.observer)
        elif a.command == "paper":
            if not a.input:
                raise ValueError("--input 논문 입력 JSON 필요")
            from gg_school_paper import paper as school_paper

            src = core.local(a.folder, a.input)
            dest = core.local(a.folder, a.out or "build/검토전_본문.md")
            if dest.exists():
                raise ValueError("기존 산출물을 덮어쓰지 않음")
            dest.parent.mkdir(parents=True, exist_ok=True)
            spec = json.loads(src.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                raise ValueError("논문 입력은 객체여야 함")
            spec.setdefault("school_profile", {"mode": "school", "layout": "forms_1_to_4"})
            dest.write_text(school_paper(spec), encoding="utf-8")
            value = {"path": str(dest), "status": "generated"}
        elif a.command == "rda-lookup":
            if not a.crop or not a.region:
                raise ValueError("--crop과 --region 필요")
            import gg_rda_lookup

            value = gg_rda_lookup.lookup_rda_data(
                a.crop,
                a.region,
                major=a.major,
                kind=a.rda_kind,
                year=a.year,
                form=a.form,
                allow_web=a.allow_web,
            )
        elif a.command == "catalog-sources":
            if not a.from_folder:
                raise ValueError("--from 참고폴더 필요")
            if not Path(a.from_folder).is_dir():
                raise ValueError("참고폴더를 찾을 수 없음: " + a.from_folder)
            import gg_source_intake

            catalog = gg_source_intake.catalog_reference_folder(
                a.from_folder, current_draft_paths=a.current
            )
            paths = gg_source_intake.write_intake_index(a.folder, catalog)
            value = {
                "folder_fingerprint": catalog["folder_fingerprint"],
                "item_count": len(catalog["items"]),
                "index_path": paths["index_path"],
                "markdown_path": paths["markdown_path"],
                "project_json": "project.json 없음 - 인덱스만 기록"
                if not (Path(a.folder) / "project.json").exists()
                else "project.json 존재 - apply 미수행, 인덱스만 기록",
            }
        else:
            p = core.load(a.folder)
            if a.command == "apply":
                if not a.change or a.expected_revision is None:
                    raise ValueError("--change와 --expected-revision 필요")
                value = core.apply(
                    a.folder,
                    json.loads(Path(a.change).read_text(encoding="utf-8")),
                    a.expected_revision,
                )
            elif a.command == "check":
                value = (
                    core.gate(a.folder, p)
                    if a.scope == "submission"
                    else core.checks(a.folder, p)
                )
            elif a.command == "status":
                report = core.gate(a.folder, p)
                state = core.completion(a.folder, p, report)
                value = {
                    "revision": p["revision"],
                    "checks": core.checks(a.folder, p),
                    "tasks": core.next_tasks(p),
                    "completion": state,
                    "skill_ready": state["skill_ready"],
                    "guideline_ready": state["guideline_ready"],
                    "user_finish_pending": state["user_finish_pending"],
                    "professor_approval_pending": state["professor_approval_pending"],
                    "professor_approval": "별도 기록 확인 필요",
                }
            elif a.command == "next":
                value = core.next_tasks(p)
            elif a.command == "question":
                if not a.field:
                    raise ValueError("--field 필요")
                action = core.question(p, a.field)
                if action in {"ask", "help"}:
                    q = p["questions"].get(
                        a.field,
                        {
                            "id": a.field,
                            "field_id": a.field,
                            "decision_revision": 1,
                            "attempts": 0,
                        },
                    )
                    q = dict(q, attempts=q["attempts"] + 1)
                    core.apply(
                        a.folder,
                        {
                            "request_id": "question:%s:%s:%s"
                            % (a.field, q["decision_revision"], q["attempts"]),
                            "ops": [{"collection": "questions", "value": q}],
                        },
                        p["revision"],
                    )
                value = {
                    "action": action,
                    "field": a.field,
                    "notice": "질문 1회·도움 1회 후 해당 작업만 보류",
                }
            elif a.command == "bundle":
                if a.scope not in p["sections"]:
                    raise ValueError("--scope 절 ID 필요")
                s = p["sections"][a.scope]
                refs = [{"collection": "sections", "id": a.scope}]
                value = {
                    "section": s,
                    "draft": core.draft(core.local(a.folder, s["path"]).read_text(encoding="utf-8")),
                    "original_sources": p["sources"],
                    "facts": p["facts"],
                    "rules": p["rules"],
                    "input_fingerprint": core.fingerprint(a.folder, p, refs),
                    "review_axes": [
                        "사실성",
                        "적용 타당성",
                        "논증",
                        "실행성",
                        "일관성",
                        "균형성",
                        "표현",
                    ],
                    "required_findings": [
                        "본문 위치",
                        "원문 위치",
                        "문제",
                        "영향",
                        "수정 조건",
                    ],
                    "notice": "요약 대신 실제 원답변·원문 파일을 읽어 대조. 같은 작성자의 자가검토는 독립검토가 아님.",
                }
            else:
                value = {"path": core.export(a.folder, a.kind)}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        if a.command == "check" and any(
            core.blocks_skill_candidate(r)
            if a.scope == "submission"
            else r["severity"] == "error" and r["status"] != "pass"
            for r in value
        ):
            return 1
        return 0
    except (ValueError, KeyError, OSError, TypeError, AttributeError, NotImplementedError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
