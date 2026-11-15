"""One adapter for existing command entrypoints; no secondary state authority."""

import argparse
import json
from pathlib import Path
import sys
import gg_core as c
from gg_document import (
    INFO_CHECKS,
    POLICY_ADVISORY_CHECKS,
    WARNING_CHECKS,
    check,
    project_major,
)


def main(mode):
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-doi", action="store_true")
    ap.add_argument("--major")
    a = ap.parse_args()
    path = Path(a.target)
    if mode == "docx-probe":
        # 학교 규격 DOCX 측정은 자체 진입점(gg_school_format.main)이 이미
        # argv를 다루므로 여기서는 인자만 넘긴다. 원장·project.json과 무관하게
        # 단일 DOCX를 재는 검사라 앞단 root 해석을 거치지 않는다.
        from gg_school_format import main as docx_probe
        argv = [str(path)]
        if a.out:
            argv += ["--out", str(a.out)]
        if a.major:
            argv += ["--major", a.major]
        return docx_probe(argv)
    try:
        if not path.exists():
            raise ValueError("대상 경로가 없음: " + str(path))
        root = path if path.is_dir() else path.parent
        if not path.is_dir() and not (root / "project.json").exists():
            root = root.parent if (root.parent / "project.json").exists() else root
        p = c.load(root) if (root / "project.json").exists() else None
        project_root = root if p is not None else None
        if mode == "status":
            if not p:
                raise ValueError(
                    "기존 작업: gg.py import 원본 --out 새폴더로 변환 대조 필요"
                )
            rows = c.checks(root, p)
            print(
                json.dumps(
                    {
                        "revision": p["revision"],
                        "checks": rows,
                        "tasks": c.next_tasks(p),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if mode == "merge":
            if not p:
                raise ValueError("기존 작업을 먼저 gg.py import로 검증하여 가져오세요")
            print(
                json.dumps(
                    c.export(root, "review", major_id=a.major),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        rows = []
        if mode == "evidence":
            if not p:
                raise ValueError(
                    "구형 원장 문자열을 검증 완료로 인정하지 않음. import 후 항목별 원문 대조 필요"
                )
            rows = c.checks(root, p)
        else:
            if p and path.is_dir():
                text = c.merged(root, p)
            elif path.is_file():
                text = c.draft(path.read_text(encoding="utf-8"))
                root = path.parent
            else:
                files = sorted(
                    (path / "sections").glob("*.md"),
                    key=lambda f: c.section_order(f.name),
                )
                text = "\n\n".join(c.draft(f.read_text(encoding="utf-8")) for f in files)
            if not text.strip():
                raise ValueError("검사할 본문이 없음: " + str(path))
            rows = [
                c.result(
                    cid,
                    str(path),
                    "pass" if cid in INFO_CHECKS else "fail",
                    reason,
                    p["revision"] if p else 0,
                    "info" if cid in INFO_CHECKS else (
                        "warning" if cid in WARNING_CHECKS
                        or cid in POLICY_ADVISORY_CHECKS else "error"
                    ),
                )
                for cid, reason in check(
                    text, root, major_id=(project_major(p) if p else None) or a.major,
                    project_root=project_root,
                )
            ]
            if not rows:
                rows = [
                    c.result(
                        "format",
                        str(path),
                        "pass",
                        "지원 텍스트 서식만 확인. 내용·렌더 별도",
                        p["revision"] if p else 0,
                    )
                ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return (
            1
            if any(r["status"] != "pass" and r["severity"] == "error" for r in rows)
            else 0
        )
    except c.OperationError as e:
        print(json.dumps(e.result, ensure_ascii=False, indent=2))
        return c.COMMIT_EXIT.get(e.result.get("commit_state"), 4)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(
            json.dumps(
                [c.result("execution", str(path), "blocked", str(e), 0)],
                ensure_ascii=False,
            )
        )
        return 2
