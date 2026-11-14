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
from gg_major_format import ENABLED_FORMAT_MAJORS, enabled_profile


FONT = gg_hwpx.SCHOOL_FONT
FOOD_CROPS_SECTION_FORMATS = gg_hwpx.FOOD_CROPS_SECTION_FORMATS
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

# 식량작물 전용 안내문. 공용 상수는 다른 전공이 그대로 쓰므로 손대지 않고,
# 이 전공에서만 아래 문구를 고른다(안내문까지 전공 격리를 닫는다).
# 값의 출처는 이제 `gg_major_format.FOOD_CROPS_PROFILE`이다 — 아래 세 상수는
# 그 표의 기준선(옛 하드코딩 원본)으로만 남긴다. 빌더는 이 상수를 읽지 않으므로
# 문구를 고칠 때는 프로필을 고치고, 이 상수는 대조용으로만 맞춰 둔다.
FOOD_CROPS_FINISH_NOTICE = (
    "저수준 미검증 출력. 목차 필드 갱신·전체 페이지·학교 원문·학명·그림·표·"
    "머리행 검토 필요. 한글에서 글꼴 신명조·여백(위30/아래30/머리0/꼬리10/"
    "좌30/우30/제본0 mm)·쪽 번호(요약은 Ⅰ부터, 본문은 1부터)를 확인한다. "
    "이 세 가지는 스킬 작성 완료를 막지 않으며 자동 통과하지 않는다. "
    "file_hash는 빌드 직후 파일 기준이다. 쪽 나눔·여백 등 후처리로 파일을 "
    "다시 저장했다면 --rehash로 매니페스트 해시를 갱신한다."
)
FOOD_CROPS_HUMAN_FINISH = HUMAN_FINISH + (
    "요약이 Ⅰ부터, 본문이 1부터인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
)

# HWPX 빌더가 프로필 표를 읽는 전공. 값은 `gg_major_format.ENABLED_FORMAT_MAJORS`
# 한 곳이 정한다 — 게이트를 켜는 것은 그 상수 한 줄이다. 이 이름은 기존
# 시험이 패치하는 통로라 별칭으로 남긴다(값이 아니라 같은 튜플 객체).
HWPX_PROFILE_MAJORS = ENABLED_FORMAT_MAJORS


def hwpx_profile(major_id):
    """HWPX 빌더가 읽을 전공 서식 프로필(없으면 None = 기본 경로)."""
    return enabled_profile(major_id, HWPX_PROFILE_MAJORS)


def notice(verify, major_id=None):
    """실제로 실행되어 통과한 검사만 열거한다(검증 생략이면 사실대로)."""
    profile = hwpx_profile(major_id)
    return " ".join([
        "저수준 미검증 출력.",
        VERIFIED_NOTICE if verify else SKIPPED_NOTICE,
        profile.finish_notice if profile is not None else FINISH_NOTICE,
    ])


def school_frontmatter_summary(md_text, major_id=None):
    """학교 앞부분(표지·제출면·인준서·목차) 감지 요약. 없으면 None."""
    plan = gg_document.school_frontmatter_plan(
        gg_document.parse(md_text, major_id=major_id), major_id)
    if plan is None:
        return None
    profile = hwpx_profile(major_id)
    return {
        "detected": True,
        "layout": "plain_markdown",
        "sections": list(plan.get("order", ())),
        "human_finish_required": list(
            profile.human_finish if profile is not None else HUMAN_FINISH),
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
# 식량작물 본문은 아라비아 장 번호를 쓰고, 맺음말·참고문헌·학부모 동의서는
# 번호 없는 제목으로 새 쪽에서 시작한다.
_FOOD_CROPS_CHAPTER_RE = re.compile(
    r"^\s*(?:[1-9]\s*[.．]\s*\S|맺음말\s*및\s*발전방향|참고문헌|학부모\s*동의서)")
_P_RE = re.compile(r"<hp:p\b[^>]*>.*?</hp:p>", re.S)
_T_RE = re.compile(r"<hp:t\b[^>]*>(.*?)</hp:t>", re.S)

def chapter_break_texts(md_text, major_id=None):
    """HWPX 장 새 쪽 판정에 쓸 본문 1단계 제목 글자 목록(첫째는 뺀다).

    워드의 `build_docx.chapter_break_node_ids`와 같은 규칙이다: 앞부분 계획이
    있으면 `["body"]` 이후, 없으면 전체 노드에서 1단계 제목을 모으고 첫째를
    뺀다(첫 장은 표지 다음 새 쪽/목차 쪽 나눔으로 이미 새 쪽에서 시작한다).
    글자로 돌려주는 이유는 HWPX가 원고 노드가 아니라 문단 XML이기 때문이다.
    """
    nodes = gg_document.parse(md_text, major_id=major_id)
    plan = gg_document.school_frontmatter_plan(nodes, major_id)
    body = nodes[plan["body"]:] if plan is not None else nodes
    headings = [node.get("text", "").strip() for node in body
                if node.get("kind") == "heading" and node.get("level") == 1]
    return headings[1:]


def add_chapter_breaks(section_xml, heading_para_ref="1", major_id=None,
                       chapter_texts=None):
    """장(Ⅰ~Ⅵ) 제목 문단에 쪽 나눔을 넣는다(이미 있으면 그대로).

    kordoc `--plain` 산출물에는 장 시작 쪽 나눔이 없어 Ⅰ장이 표지 다음
    쪽에서 이어졌다. 제목 스타일(paraPrIDRef)이 장 제목 문단에만 붙는 것을
    확인하고 그 문단에만 pageBreak="1"을 준다(재무제표 주석의 Ⅰ. 항목은
    본문 스타일이라 대상이 아니다). 식량작물은 아라비아 장 번호를 쓰므로
    ``major_id``가 그 전공일 때만 번호 체계를 바꾼다.

    ``chapter_texts``가 None이 아니면 정규식 대신 **글자가 그 목록에 있는지**로
    판정한다. 낙농 원고의 장 번호는 Ⅰ.일 수도 1.일 수도 있어 정규식으로는
    가를 수 없고, 마크다운 1단계 제목(노드)으로 이미 정해진 목록을 그대로 쓴다.

    목록 방식에서는 문단 모양(`paraPrIDRef`) 게이트를 쓰지 않는다. kordoc이
    장 제목에 붙이는 모양 번호는 원고에 따라 다르다(실측: `#` 제목이 있는
    원고는 `1`, 제목 표시 없는 낙농 원고는 `7` 또는 로마 번호 장이면 `0`).
    모양 번호를 고정하면 낙농 HWPX에 쪽 나눔이 하나도 안 들어간다. 대신 목록
    자체가 이미 그 원고의 1단계 제목에서 나왔으므로 글자 일치만으로 충분하다.

    이 갈래는 게이트가 낙농을 막고 있는 동안 **닿지 않는다**(`chapter_texts`는
    `hwpx_chapter == "nodes"`인 프로필, 곧 게이트 안 전공에만 계산된다).
    그래서 이 묶음의 출력은 안 바뀌고, 다음 판에서 게이트 한 줄만 켜도
    장 쪽 나눔이 살아 있다(떼어 내면 그 함정이 그대로 되살아난다).
    """
    chapter_re = _CHAPTER_RE
    profile = hwpx_profile(major_id)
    if profile is not None and profile.hwpx_chapter_re is not None:
        chapter_re = profile.hwpx_chapter_re
    chapter_set = None if chapter_texts is None else set(chapter_texts)
    chapters = []

    def fix(match):
        para = match.group(0)
        head = re.match(r"<hp:p\b[^>]*>", para).group(0)
        text = "".join(_T_RE.findall(para)).strip()
        if chapter_set is None:
            if 'paraPrIDRef="' + heading_para_ref + '"' not in head:
                return para
            if not chapter_re.match(text):
                return para
        elif text not in chapter_set:
            return para
        chapters.append(text)
        if 'pageBreak="' in head:
            return re.sub(r'pageBreak="[^"]*"', 'pageBreak="1"', para, count=1)
        return head[:-1] + ' pageBreak="1">' + para[len(head):]

    return _P_RE.sub(fix, section_xml), chapters

# header.xml의 문단 모양 하나를 통째로 잡는 정규식. 프로필의 제목 여백은
# `type="OUTLINE"`(개요) 모양에만 걸어야 한다 — 본문(`type="NONE"`) 여백이
# 따라 바뀌면 학교 규격 본문이 무너진다.
_HEADER_PARA_PR_RE = re.compile(
    r"<hh:paraPr\b(?![^>]*/>)[^>]*>.*?</hh:paraPr>", re.S)
_HEADER_PARA_PR_ID_RE = re.compile(r'<hh:paraPr\b[^>]*?\sid="(\d+)"')
_OUTLINE_HEADING_RE = re.compile(r'<hh:heading\b[^>]*type="OUTLINE"')
_HC_PREV_RE = re.compile(r'(<hc:prev\b[^>]*?value=")(\d+)(")')
_HC_NEXT_RE = re.compile(r'(<hc:next\b[^>]*?value=")(\d+)(")')

def set_heading_margins(header_xml, prev, next_):
    """제목(개요) 문단 모양의 위·아래 여백을 바꾼다(순수 문자열 변환).

    `<hh:heading type="OUTLINE" …/>`을 가진 문단 모양의 `hc:prev`·`hc:next`
    값만 그 값으로 바꾼다. 0 이하인 값은 그대로 둔다(프로필 기본값 0 =
    미적용). 두 값이 모두 0 이하이거나 바뀔 곳이 없으면 입력 문자열을
    그대로 돌려준다(바이트 불변).
    반환: (새 header.xml, 바꾼 문단 모양 id 목록).
    """
    prev = int(prev or 0)
    next_ = int(next_ or 0)
    if prev <= 0 and next_ <= 0:
        return header_xml, []
    changed = []

    def fix(match):
        para = match.group(0)
        if not _OUTLINE_HEADING_RE.search(para):
            return para
        updated = para
        if prev > 0:
            updated = _HC_PREV_RE.sub(
                lambda m: m.group(1) + str(prev) + m.group(3),
                updated, count=1)
        if next_ > 0:
            updated = _HC_NEXT_RE.sub(
                lambda m: m.group(1) + str(next_) + m.group(3),
                updated, count=1)
        if updated == para:
            return para
        found = _HEADER_PARA_PR_ID_RE.match(para)
        if found:
            changed.append(found.group(1))
        return updated

    return _HEADER_PARA_PR_RE.sub(fix, header_xml), changed

def apply_layout(data, *, margins_mm=None, major_id=None, chapter_texts=None):
    """생성된 HWPX 바이트에 표 폭·장 쪽 나눔·제목 여백을 적용한다.

    순수 바이트 변환이다 — 입력 바이트는 건드리지 않고 새 ZIP 바이트를 낸다.
    ``chapter_texts``가 None이 아니면 장 새 쪽을 정규식 대신 그 글자 목록으로
    판정한다(노드 방식 프로필).
    """
    width = body_width_units(margins_mm)
    report = {"body_width": width, "tables": 0, "previous_widths": [],
              "chapter_breaks": []}
    # 프로필이 없거나 여백 값이 0이면 header.xml은 손대지 않는다(바이트 불변).
    profile = hwpx_profile(major_id)
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
                updated, chapters = add_chapter_breaks(
                    updated, major_id=major_id, chapter_texts=chapter_texts)
                if updated != text:
                    report["tables"] += len(widths)
                    report["previous_widths"] += sorted(set(widths))
                    report["chapter_breaks"] += chapters
                member = updated.encode("utf-8")
            elif (info.filename == gg_hwpx.HEADER_NAME and profile is not None
                  and (profile.heading_prev_hwpunit > 0
                       or profile.heading_next_hwpunit > 0)):
                text = member.decode("utf-8")
                updated, headings = set_heading_margins(
                    text, profile.heading_prev_hwpunit,
                    profile.heading_next_hwpunit)
                if updated != text:
                    report["heading_margins"] = headings
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

def recount_page_numbers(data, section_formats, applied):
    """후처리까지 끝난 최종 ZIP에서 구역별 `pageNum`·`newNum` 개수를 다시 센다.

    분할 시점 검사만으로는 뒤 단계가 쪽번호 컨트롤을 다시 넣은 경우를 못
    잡는다. 개수가 기대(cover 0/0, front 1/1, body 1/1)와 다르면 발행하지
    않고, 맞으면 `applied["page_numbers"]`에 구역별 실측값을 남긴다.
    """
    measured = []
    if applied.get("sections") == "not_applicable":
        return dict(applied)
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        names = set(archive.namelist())
        for index in range(len(section_formats)):
            name = "Contents/section%d.xml" % index
            if name not in names:
                raise gg_hwpx.HwpxBlocked(
                    "page_number_count_after_postprocess", missing=name,
                    action="후처리 뒤 구역 파일이 사라졌습니다. 발행하지 않았습니다.")
            text = archive.read(name).decode("utf-8")
            counts = {"pageNum": text.count("<hp:pageNum"),
                      "newNum": text.count("<hp:newNum")}
            expected = gg_hwpx._expected_page_numbers(index, section_formats)
            if (counts["pageNum"] != expected["pageNum"]
                    or counts["newNum"] != expected["newNum"]):
                raise gg_hwpx.HwpxBlocked(
                    "page_number_count_after_postprocess", section=name,
                    expected={"pageNum": expected["pageNum"],
                              "newNum": expected["newNum"]},
                    found=counts,
                    action="후처리 뒤 구역별 쪽번호 개수가 기대와 다릅니다. "
                           "발행하지 않았습니다.")
            measured.append({"section": name, **counts})
    updated = dict(applied)
    updated["page_numbers"] = measured
    return updated


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
    profile = hwpx_profile(authorization.major_id)
    section_formats = profile.section_formats if profile is not None else None
    margins_mm = section_formats[2]["margin_mm"] if profile is not None else None
    # 장 새 쪽 판정: 프로필이 노드 방식을 요구할 때만 마크다운 1단계 제목으로
    # 목록을 만든다. 그 밖(정규식 프로필·프로필 없음)은 None = 옛 정규식.
    chapter_texts = None
    if profile is not None and profile.hwpx_chapter == "nodes":
        chapter_texts = chapter_break_texts(
            source_text, authorization.major_id)
    staging = target.with_name("." + target.name + ".layout-staging")
    if staging.exists():
        raise ValueError("임시 후처리 파일이 남아 있음: " + str(staging))
    try:
        result = gg_hwpx.build(
            source, staging, context=context, authorization=authorization,
            cli_command=cli_command, cache_dir=cache_dir, node=node, pnpm=pnpm,
            kordoc=kordoc, timeout=timeout, font=font,
            page_numbers=page_numbers, image_dir=image_dir, verify=verify,
            **(dict(margins_mm=margins_mm, section_formats=section_formats)
               if profile is not None else {}),
        )
        generated = staging.read_bytes()
        published_bytes, layout = apply_layout(
            generated, margins_mm=margins_mm,
            major_id=authorization.major_id, chapter_texts=chapter_texts)
        if profile is not None:
            result["applied"] = recount_page_numbers(
                published_bytes, section_formats, result["applied"])
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
        "school_frontmatter": school_frontmatter_summary(
            source_text, authorization.major_id),
        "notice": notice(verify, authorization.major_id),
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
