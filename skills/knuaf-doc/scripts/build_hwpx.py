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
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

import gg_core
import gg_document
import gg_fs
import gg_hwpx
import gg_major_contract as mc


FONT = gg_hwpx.SCHOOL_FONT
DEFAULT_IN = "build/본문_통합.md"
DEFAULT_OUT = "build/검토전_논문.hwpx"
PAGE_WIDTH_MM = 210
HUMAN_FINISH = (
    "표지·제출면·인준서의 쪽 나눔과 가운데 정렬",
    "목차의 실제 쪽번호",
    "표·그림 목차(있으면)",
    "표 폭이 본문 폭(150 mm)인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
    "장(Ⅰ~Ⅵ) 시작이 새 쪽인지 확인 — 자동으로 쪽 나눔을 넣었지만 한글에서 확인한다",
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


def body_width_units(margins_mm=None):
    """A4 본문 폭(좌우 여백 제외)을 HWPUNIT으로. 기본 150 mm = 42520."""
    margins = dict(gg_hwpx.SCHOOL_MARGINS_MM if margins_mm is None else margins_mm)
    return gg_hwpx.mm_to_hwpunit(
        PAGE_WIDTH_MM - margins["left"] - margins["right"])

_TBL_RE = re.compile(r"<hp:tbl\b[^>]*>.*?</hp:tbl>", re.S)
_TBL_SZ_RE = re.compile(r'(<hp:tbl\b[^>]*>\s*<hp:sz\b[^>]*?width=")(\d+)(")')
_TR_RE = re.compile(r"<hp:tr\b[^>]*>.*?</hp:tr>", re.S)
_CELL_SZ_RE = re.compile(r'(<hp:cellSz\b[^>]*?width=")(\d+)(")')

def rewrite_table_widths(section_xml, width):
    """표 폭을 본문 폭에 맞춘다(kordoc 기본 155.2 mm → 본문 폭).

    표 전체 폭과 각 행의 셀 폭을 같은 비율로 줄이고, 행 합계가 표 폭과
    정확히 같도록 마지막 셀에서 반올림 오차를 흡수한다.
    """
    changed = []

    def fix_table(match):
        block = match.group(0)
        head = _TBL_SZ_RE.search(block)
        if head is None:
            return block
        current = int(head.group(2))
        if current == width:
            return block
        factor = width / current

        def fix_row(row_match):
            row = row_match.group(0)
            cells = list(_CELL_SZ_RE.finditer(row))
            if not cells:
                return row
            scaled = [max(1, round(int(c.group(2)) * factor)) for c in cells]
            scaled[-1] += width - sum(scaled)
            pieces, last = [], 0
            for cell, size in zip(cells, scaled):
                pieces.append(row[last:cell.start(2)])
                pieces.append(str(size))
                last = cell.end(2)
            pieces.append(row[last:])
            return "".join(pieces)

        block = _TR_RE.sub(fix_row, block)
        block = _TBL_SZ_RE.sub(
            lambda m: m.group(1) + str(width) + m.group(3), block, count=1)
        changed.append(current)
        return block

    return _TBL_RE.sub(fix_table, section_xml), changed

_CHAPTER_RE = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\s*[.．]")
_P_RE = re.compile(r"<hp:p\b[^>]*>.*?</hp:p>", re.S)
_T_RE = re.compile(r"<hp:t\b[^>]*>(.*?)</hp:t>", re.S)

def add_chapter_breaks(section_xml, heading_para_ref="1"):
    """장(Ⅰ~Ⅵ) 제목 문단에 쪽 나눔을 넣는다(이미 있으면 그대로).

    kordoc `--plain` 산출물에는 장 시작 쪽 나눔이 없어 Ⅰ장이 표지 다음
    쪽에서 이어졌다. 제목 스타일(paraPrIDRef)이 장 제목 문단에만 붙는 것을
    확인하고 그 문단에만 pageBreak="1"을 준다(재무제표 주석의 Ⅰ. 항목은
    본문 스타일이라 대상이 아니다).
    """
    chapters = []

    def fix(match):
        para = match.group(0)
        head = re.match(r"<hp:p\b[^>]*>", para).group(0)
        if 'paraPrIDRef="' + heading_para_ref + '"' not in head:
            return para
        text = "".join(_T_RE.findall(para)).strip()
        if not _CHAPTER_RE.match(text):
            return para
        chapters.append(text)
        if 'pageBreak="' in head:
            return re.sub(r'pageBreak="[^"]*"', 'pageBreak="1"', para, count=1)
        return head[:-1] + ' pageBreak="1">' + para[len(head):]

    return _P_RE.sub(fix, section_xml), chapters

def apply_layout(data, *, margins_mm=None):
    """생성된 HWPX 바이트에 표 폭·장 쪽 나눔을 적용한다(순수 바이트 변환)."""
    width = body_width_units(margins_mm)
    report = {"body_width": width, "tables": 0, "previous_widths": [],
              "chapter_breaks": []}
    source = io.BytesIO(data)
    target = io.BytesIO()
    with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(target, "w") as zout:
        for info in zin.infolist():
            member = zin.read(info.filename)
            if info.filename == gg_hwpx.MIMETYPE:
                stored = zipfile.ZipInfo(gg_hwpx.MIMETYPE, date_time=info.date_time)
                stored.compress_type = zipfile.ZIP_STORED
                stored.external_attr = info.external_attr
                zout.writestr(stored, member or gg_hwpx.MIMETYPE_BYTES)
                continue
            if gg_hwpx.SECTION_RE.fullmatch(info.filename):
                text = member.decode("utf-8")
                updated, widths = rewrite_table_widths(text, width)
                updated, chapters = add_chapter_breaks(updated)
                if updated != text:
                    report["tables"] += len(widths)
                    report["previous_widths"] += sorted(set(widths))
                    report["chapter_breaks"] += chapters
                member = updated.encode("utf-8")
            zout.writestr(info, member)
    report["previous_widths"] = sorted(set(report["previous_widths"]))
    return target.getvalue(), report

def verify_layout(data, command, source_text, *, timeout):
    """후처리한 바이트를 임시 파일로 검증한다(구조·재파싱·표 수치)."""
    with tempfile.TemporaryDirectory(prefix=".gg-hwpx-layout-") as work:
        staged = Path(work) / "layout.hwpx"
        staged.write_bytes(data)
        validation = gg_hwpx.validate_hwpx(command, staged, timeout=timeout)
        if not (validation.get("process_ok") is True
                and validation.get("returncode") == 0
                and validation.get("status") == "ok"):
            raise gg_hwpx.HwpxBlocked(
                "layout_validate_failed", status=validation.get("status"),
                process_ok=validation.get("process_ok"),
                returncode=validation.get("returncode"),
                issues=validation.get("issues"),
                action="표 폭·장 쪽 나눔을 적용한 파일이 구조 검증을 통과하지 "
                       "못했습니다. 발행하지 않았습니다.")
        back_md = Path(work) / "back.md"
        reparsed = gg_hwpx.reparse_hwpx(command, staged, back_md, timeout=timeout)
        if not (reparsed.get("ok") and "markdown" in reparsed):
            raise gg_hwpx.HwpxBlocked(
                "layout_reparse_failed",
                action="후처리 뒤 재파싱 대조가 불가해 발행하지 않았습니다.")
        comparison = gg_hwpx.compare_table_numbers(source_text, reparsed["markdown"])
        if not comparison["match"]:
            raise gg_hwpx.HwpxBlocked("layout_table_numbers_mismatch", **comparison,
                                      action="후처리 뒤 표 셀 수치가 원문과 달라 "
                                             "발행하지 않았습니다.")
    return {"validate": validation, "reparse": {"ok": True},
            "table_numbers": comparison}

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
    staging = target.with_name("." + target.name + ".layout-staging")
    if staging.exists():
        raise ValueError("임시 후처리 파일이 남아 있음: " + str(staging))
    try:
        result = gg_hwpx.build(
            source, staging, context=context, authorization=authorization,
            cli_command=cli_command, cache_dir=cache_dir, node=node, pnpm=pnpm,
            kordoc=kordoc, timeout=timeout, font=font,
            page_numbers=page_numbers, image_dir=image_dir, verify=verify,
        )
        generated = staging.read_bytes()
        published_bytes, layout = apply_layout(generated)
        verification = result["verification"]
        if published_bytes != generated:
            if verify:
                verification = verify_layout(
                    published_bytes, result["runtime"]["cli_command"],
                    source_text, timeout=timeout)
            else:
                verification = {"validate": None, "reparse": None,
                                "table_numbers": None}
        mc.reconfirm_output(authorization, context)
        identity = gg_fs.identity(target.parent, kind="directory")
        published = gg_fs.publish_bytes_noreplace(
            target, published_bytes, parent_identity=identity)
        if published.get("state") != "installed":
            raise gg_hwpx.HwpxBlocked(
                "output_publish_failed",
                publish_reason=published.get("reason"), output=str(target),
                action="기존 파일·입력은 보존됐습니다. 새 경로로 다시 시도하세요.")
    finally:
        if staging.exists():
            staging.unlink()
    manifest = {
        "kind": "draft",
        "file_hash": gg_core.digest(published_bytes),
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
            "layout": layout,
            "margins_mm": result["margins_mm"],
            "page_numbers": result["page_numbers"],
            "images": result["images"],
            "validate": verification["validate"],
            "reparse": verification["reparse"],
            "table_numbers": verification["table_numbers"],
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
