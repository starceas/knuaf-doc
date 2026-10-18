#!/usr/bin/env python3
"""학교 논문 HWPX 산출 CLI — gg_hwpx(kordoc generate) + 정책 B 전공 권한 + 매니페스트.

build_docx.py와 같은 규율로 돈다: 전공 권한 확인 → 입력 읽기 → 기존 출력·매니페스트
거부 → HWPX 생성 → 정본 재확인 → 본체 발행 → `<출력파일 전체이름>.manifest.json`.
동반 매니페스트 이름에 본체 확장자를 남겨 같은 basename의 DOCX 영수증(out.manifest.json)
과 겹치지 않게 하고, 매니페스트도 배타 발행으로 기존 파일을 덮어쓰지 않는다.
앞부분(표지·제출면·인준서·목차)의 학교 서식은 kordoc `--plain`으로 재현되지 않으므로
매니페스트에 '한글에서 사람이 마무리'로 남긴다. macOS와 Windows 모두에서 돈다.
"""

from __future__ import annotations

import argparse
import json

import gg_core
import gg_document
import gg_fs
import gg_hwpx
import gg_major_contract as mc


FONT = gg_hwpx.SCHOOL_FONT
DEFAULT_IN = "build/본문_통합.md"
DEFAULT_OUT = "build/검토전_논문.hwpx"
HUMAN_FINISH = (
    "표지·제출면·인준서의 쪽 나눔과 가운데 정렬",
    "목차의 실제 쪽번호",
    "표·그림 목차(있으면)",
)
VERIFIED_NOTICE = "kordoc validate(구조)와 HWPX 재파싱 표 수치 대조를 통과했다."
SKIPPED_NOTICE = ("검증을 생략했다(--no-verify): 발행 전 구조 검증·재파싱·표 수치 "
                  "대조를 실행하지 않았다.")
FINISH_NOTICE = (
    "한글 프로그램에서 열어 (1) 글꼴 신명조 (2) 여백 위20/아래15/머리15/꼬리15/"
    "좌30/우30/제본0 mm (3) 쪽번호를 확인한다. 표지·제출면·인준서의 쪽 나눔·가운데 "
    "정렬과 목차의 실제 쪽번호는 사람이 마무리한다. 이 항목들은 자동 통과하지 않는다."
)


def notice(verify):
    """실제로 실행되어 통과한 검사만 열거한다(검증 생략이면 사실대로)."""
    return " ".join([
        "저수준 미검증 출력.",
        VERIFIED_NOTICE if verify else SKIPPED_NOTICE,
        FINISH_NOTICE,
    ])


def school_frontmatter_summary(md_text):
    """학교 앞부분(표지·제출면·인준서·목차) 감지 요약. 없으면 None."""
    plan = gg_document.school_frontmatter_plan(gg_document.parse(md_text))
    if plan is None:
        return None
    return {
        "detected": True,
        "layout": "plain_markdown",
        "sections": list(plan.get("order", ())),
        "human_finish_required": list(HUMAN_FINISH),
    }


def export(base, inp, out, *, font=FONT, context=None, cli_command=None,
           cache_dir=None, node=None, pnpm=None, kordoc=None,
           timeout=gg_hwpx.DEFAULT_TIMEOUT, page_numbers=True, image_dir=None,
           verify=True):
    """권한 확인 후 마크다운 본문을 학교 규격 HWPX로 발행하고 매니페스트를 쓴다."""
    authorization = mc.authorize_output(mc.OUTPUT_SCHOOL_PAPER, context)
    source = gg_core.local(base, inp)
    target = gg_core.local(base, out)
    manifest_path = target.with_name(target.name + ".manifest.json")
    if not source.is_file():
        raise ValueError("입력 본문 없음: " + str(inp))
    if source == target:
        raise ValueError("입력과 출력 경로가 같음: 새 출력 경로 지정")
    if manifest_path in (source, target):
        raise ValueError("매니페스트 경로가 입력이나 출력과 겹침: 새 출력 이름 지정")
    if target.exists():
        raise ValueError("기존 산출물을 덮어쓰지 않음: 새 경로 지정")
    if manifest_path.exists():
        raise ValueError("기존 매니페스트를 덮어쓰지 않음: 새 경로 지정")
    source_text = source.read_text(encoding="utf-8")
    result = gg_hwpx.build(
        source, target, context=context, authorization=authorization,
        cli_command=cli_command, cache_dir=cache_dir, node=node, pnpm=pnpm,
        kordoc=kordoc, timeout=timeout, font=font,
        page_numbers=page_numbers, image_dir=image_dir, verify=verify,
    )
    manifest = {
        "kind": "draft",
        "file_hash": result["output_sha256"],
        "input_hash": result["input_sha256"],
        "font": font,
        "font_available": "not_run",
        "render": "not_run",
        "hwp": "not_run",
        "major_authorization": authorization.to_dict(),
        "hwpx": {
            "kordoc": result["runtime"],
            "verify": bool(verify),
            "post_processing": result["applied"],
            "margins_mm": result["margins_mm"],
            "page_numbers": result["page_numbers"],
            "images": result["images"],
            "validate": result["verification"]["validate"],
            "reparse": result["verification"]["reparse"],
            "table_numbers": result["verification"]["table_numbers"],
        },
        "school_frontmatter": school_frontmatter_summary(source_text),
        "notice": notice(verify),
    }
    data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    identity = gg_fs.identity(target.parent, kind="directory")
    published = gg_fs.publish_bytes_noreplace(manifest_path, data,
                                             parent_identity=identity)
    if published.get("state") != "installed":
        raise gg_hwpx.HwpxBlocked(
            "manifest_publish_failed", manifest=str(manifest_path),
            publish_reason=published.get("reason"), output=str(target),
            body_published=True,
            action="본체는 발행됐고 매니페스트만 발행하지 못했습니다(본체는 보존).")
    return {"output": str(target), "manifest_path": str(manifest_path),
            "manifest": manifest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("--in", dest="inp", default=DEFAULT_IN)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--font", default=FONT)
    parser.add_argument("--major", default=None, help="명시 전공 ID")
    parser.add_argument("--kordoc", help="기존 kordoc CLI 경로(호환되면 재사용)")
    parser.add_argument("--cache-dir")
    parser.add_argument("--node")
    parser.add_argument("--pnpm")
    parser.add_argument("--timeout", type=int, default=gg_hwpx.DEFAULT_TIMEOUT)
    parser.add_argument("--image-dir")
    parser.add_argument("--no-page-numbers", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        context = mc.output_context(args.base, args.major)
        value = export(
            args.base, args.inp, args.out, font=args.font, context=context,
            kordoc=args.kordoc, cache_dir=args.cache_dir, node=args.node,
            pnpm=args.pnpm, timeout=args.timeout, image_dir=args.image_dir,
            page_numbers=not args.no_page_numbers, verify=not args.no_verify,
        )
    except mc.OutputHeldError as error:
        print(json.dumps({"status": "held", "reason": error.reason,
                          "detail": str(error),
                          "guidance": error.detail.get("guidance")},
                         ensure_ascii=False))
        return 2
    except gg_hwpx.HwpxBlocked as error:
        print(json.dumps({"status": "blocked", "reason": error.reason,
                          **error.details}, ensure_ascii=False))
        return 2
    except (ValueError, OSError) as error:
        print(str(error))
        return 2
    print(value["output"])
    return 0


if __name__ == "__main__":
    import sys

    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
