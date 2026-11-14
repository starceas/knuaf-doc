#!/usr/bin/env python3
"""kordoc `generate` → 학교 논문 규격 HWPX 어댑터 (라이브러리).

마크다운 한 파일을 kordoc(`generate`)으로 HWPX로 변환한 뒤, 표준 라이브러리
zipfile과 문자열 치환만으로 학교 논문 규격에 맞춘다: (a) 여백을 위20/아래15/
머리15/꼬리15/좌30/우30/제본0 mm로, (b) 글꼴 이름을 신명조로 바꾸고, (c) 쪽번호를
넣는다. 생성 모드는 `--plain`(공문서 장식 없음)을 써서 `Ⅰ.` 장 번호·표·학명
이탤릭을 그대로 보존한다 — 보고서/기안문/개조식 프리셋은 로마자 장 번호를 떼어내고
문서정보표·날짜를 덧붙인다.

발행 함수 `build()`는 전공 권한 맥락(`context`)과 권한 객체(`authorization`)를 **필수**로
받고, 발행 직전에 `gg_major_contract.reconfirm_output(authorization, context)`를 직접
호출한다 — 권한 없는 독립 발행 진입점을 두지 않기 위해서다(이 모듈에는 CLI가 없다).
순수 바이트 변환·검증 함수는 권한을 요구하지 않는다.

입력은 수정하지 않고, 기존 출력은 덮어쓰지 않으며(배타 발행), 외부 프로세스는
shell=False로 실행한다. 입력은 UTF-8(BOM 없음)·LF로 정규화해 임시 폴더의 경로로
넘기고 stdout에 의존하지 않는다(-o 파일). macOS와 Windows 모두에서 돈다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from typing import NamedTuple
from urllib.parse import quote, unquote
import zipfile

import gg_fs
import gg_kordoc


# 1 mm = 1/25.4 inch, 1 inch = 7200 HWPUNIT.
HWPUNIT_PER_MM = 7200 / 25.4

SCHOOL_FONT = "신명조"
SCHOOL_MARGINS_MM = {
    "header": 15,
    "footer": 15,
    "gutter": 0,
    "left": 30,
    "right": 30,
    "top": 20,
    "bottom": 15,
}
PAGE_NUMBER_CONTROL = (
    '<hp:ctrl><hp:pageNum pos="BOTTOM_CENTER" formatType="DIGIT" '
    'sideChar="-"/></hp:ctrl>'
)

# 식량작물 양식 원본 실측(sec0/겉표지, sec2/앞부분, sec3/본문 시작)을 구역
# 순서대로 적은 값. 이 표를 쓰는 빌더는 food_crops 하나뿐이고, 다른 전공은
# None 경로를 그대로 타서 바이트가 변하지 않는다.
FOOD_CROPS_SECTION_FORMATS = (
    {
        "role": "cover",
        "margin_mm": {
            "header": 0, "footer": 10, "gutter": 0, "left": 20,
            "right": 20, "top": 20, "bottom": 10,
        },
        "page_number": {"pos": "NONE", "format": "DIGIT", "restart": None},
    },
    {
        "role": "front",
        "margin_mm": {
            "header": 0, "footer": 10, "gutter": 0, "left": 30,
            "right": 30, "top": 30, "bottom": 30,
        },
        "page_number": {
            "pos": "BOTTOM_CENTER", "format": "ROMAN_CAPITAL", "restart": 1,
        },
    },
    {
        "role": "body",
        "margin_mm": {
            "header": 0, "footer": 10, "gutter": 0, "left": 30,
            "right": 30, "top": 30, "bottom": 30,
        },
        "page_number": {
            "pos": "BOTTOM_CENTER", "format": "DIGIT", "restart": 1,
        },
    },
)

GENERATE_DEFAULT_ARGS = ("--plain", "--paper", "A4")
DEFAULT_TIMEOUT = 180
MIMETYPE = "mimetype"
MIMETYPE_BYTES = b"application/hwp+zip"
HEADER_NAME = "Contents/header.xml"
SECTION_RE = re.compile(r"Contents/section\d+\.xml")


class HwpxBlocked(RuntimeError):
    """A user-actionable, expected failure represented by status=blocked."""

    def __init__(self, reason: str, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


def _result(status: str, **fields):
    return {"status": status, **fields}


# --- 순수 변환 함수 (문자열 in/out, kordoc·플랫폼 불필요) ----------------

def mm_to_hwpunit(mm) -> int:
    return int(round(mm * HWPUNIT_PER_MM))


_MARGIN_RE = re.compile(r"<hp:margin\b[^>]*/>")
_MARGIN_ATTR_RE = re.compile(r'([A-Za-z][A-Za-z0-9]*)="([^"]*)"')
_MARGIN_ORDER = ("header", "footer", "gutter", "left", "right", "top", "bottom")


def rewrite_margins(section_xml: str, margins_mm=SCHOOL_MARGINS_MM, *,
                    override=None) -> str:
    """section0.xml의 <hp:margin/> 여백을 학교 규격(mm→HWPUNIT)으로 덮어쓴다.

    ``override``(구역 서식 dict)가 있으면 그 구역 값이 ``margins_mm``보다
    우선한다. 둘 다 None이면 아무것도 바꾸지 않는다.
    """
    if override is not None:
        margins_mm = override.get("margin_mm", margins_mm)
    if margins_mm is None:
        return section_xml

    def rebuild(match):
        attrs = dict(_MARGIN_ATTR_RE.findall(match.group(0)))
        for key, mm in margins_mm.items():
            attrs[key] = str(mm_to_hwpunit(mm))
        ordered = [(key, attrs[key]) for key in _MARGIN_ORDER if key in attrs]
        ordered += [(key, value) for key, value in attrs.items()
                    if key not in _MARGIN_ORDER]
        body = " ".join('%s="%s"' % (key, value) for key, value in ordered)
        return "<hp:margin %s/>" % body

    return _MARGIN_RE.sub(rebuild, section_xml)


_FONT_FACE_RE = re.compile(r'(<hh:font\b[^>]*?\bface=")([^"]*)(")')


def rewrite_fonts(header_xml: str, font: str = SCHOOL_FONT) -> str:
    """header.xml의 hh:font face 이름을 학교 규격 글꼴로 바꾼다."""
    return _FONT_FACE_RE.sub(
        lambda m: m.group(1) + font + m.group(3), header_xml
    )


_COLPR_CTRL_RE = re.compile(r"<hp:ctrl>\s*<hp:colPr\b[^>]*/>\s*</hp:ctrl>")


def _page_number_pos(override) -> str:
    """구역 서식 dict에서 page_number.pos를 꺼낸다(중첩·평면 둘 다 허용).

    구역 서식 행은 `gg_major_format`에서 `MappingProxyType`으로 얼려 오므로
    `dict`만 보면 겉표지 `pos="NONE"`을 놓쳐 기본값(BOTTOM_CENTER)으로 되돌아간다.
    """
    spec = override.get("page_number", override) if isinstance(override, Mapping) else {}
    return str(spec.get("pos", "BOTTOM_CENTER"))


def inject_page_numbers(section_xml: str, control: str = PAGE_NUMBER_CONTROL,
                        *, override=None) -> str:
    """section의 첫 단 정의 뒤에 쪽번호 컨트롤을 넣는다(이미 있으면 그대로).

    ``override``(식량작물 구역 서식)가 주어지면 **아무것도 넣지 않는다** —
    구역 분할 단계(§4.1 3단계)가 이미 넣었으므로 구역마다 하나만 있어야 한다.
    이때는 개수만 확인한다: ``pos == "NONE"``(겉표지)은 0개, 나머지는 1개.
    """
    if override is not None:
        expected = 0 if _page_number_pos(override) == "NONE" else 1
        found = section_xml.count("<hp:pageNum")
        if found != expected:
            raise HwpxBlocked(
                "page_number_control_count",
                expected=expected, found=found,
                action="구역 분할이 쪽번호 컨트롤을 정확히 넣지 못했습니다.",
            )
        return section_xml
    if "<hp:pageNum" in section_xml:
        return section_xml
    match = _COLPR_CTRL_RE.search(section_xml)
    if match is None:
        return section_xml
    return section_xml[:match.end()] + control + section_xml[match.end():]


NEW_NUM_CONTROL = '<hp:ctrl><hp:newNum num="1" numType="PAGE"/></hp:ctrl>'

_PAGE_NUM_CTRL_RE = re.compile(
    r"<hp:ctrl\b[^>]*>\s*<hp:pageNum\b[^>]*/>\s*</hp:ctrl>")
_NEW_NUM_CTRL_RE = re.compile(
    r"<hp:ctrl\b[^>]*>\s*<hp:newNum\b[^>]*/>\s*</hp:ctrl>")
_RUN_OPEN_RE = re.compile(r"<hp:run\b[^>]*>")
_T_ELEMENT_RE = re.compile(r"<hp:t\b[^>]*>.*?</hp:t>", re.S)
_SEC_TAG = "{http://www.hancom.co.kr/hwpml/2011/section}sec"
_PARA_TAG = "{http://www.hancom.co.kr/hwpml/2011/paragraph}p"
_TEXT_TAG = "{http://www.hancom.co.kr/hwpml/2011/paragraph}t"
_SPACE_RE = re.compile(r"\s+")


def _local_name(name: str) -> str:
    """`hp:secPr` → `secPr` (접두사 무관)."""
    return name.rsplit(":", 1)[-1]


def _iter_xml_tags(xml: str):
    """`<...>` 토큰을 (시작, 끝, 지역이름, 종류)로 낸다.

    종류: start / end / empty / comment / cdata / pi / decl. 속성값 안의 `>`
    (드물지만 XML에서 합법)를 태그 끝으로 오인하지 않도록 따옴표를 추적한다.
    """
    i, n = 0, len(xml)
    while True:
        i = xml.find("<", i)
        if i < 0:
            return
        if xml.startswith("<!--", i):
            end = xml.find("-->", i + 4)
            if end < 0:
                return
            yield (i, end + 3, None, "comment")
            i = end + 3
            continue
        if xml.startswith("<![CDATA[", i):
            end = xml.find("]]>", i + 9)
            if end < 0:
                return
            yield (i, end + 3, None, "cdata")
            i = end + 3
            continue
        if xml.startswith("<?", i):
            end = xml.find("?>", i + 2)
            if end < 0:
                return
            yield (i, end + 2, None, "pi")
            i = end + 2
            continue
        j, quote_ch = i + 1, None
        while j < n:
            ch = xml[j]
            if quote_ch is not None:
                if ch == quote_ch:
                    quote_ch = None
            elif ch in "\"'":
                quote_ch = ch
            elif ch == ">":
                break
            j += 1
        if j >= n:
            return
        token = xml[i:j + 1]
        if token.startswith("<!"):
            kind, name = "decl", None
        elif token.startswith("</"):
            kind = "end"
            name = _local_name(token[2:-1].strip())
        elif token.endswith("/>"):
            kind = "empty"
            name = _local_name(token[1:-2].split(None, 1)[0])
        else:
            kind = "start"
            name = _local_name(token[1:-1].split(None, 1)[0])
        yield (i, j + 1, name, kind)
        i = j + 1


def _element_spans(xml: str, local: str):
    """지역이름이 ``local``인 요소의 (시작, 끝) 바이트 구간 목록."""
    stack, spans = [], []
    for start, end, name, kind in _iter_xml_tags(xml):
        if kind in ("comment", "cdata", "pi", "decl"):
            continue
        if kind == "empty":
            if name == local:
                spans.append((start, end))
            continue
        if kind == "start":
            stack.append((name, start))
            continue
        if not stack:
            continue
        open_name, open_start = stack.pop()
        if open_name == local and name == local:
            spans.append((open_start, end))
    return spans


def _cut_spans(xml: str, spans) -> str:
    """구간들을 뒤에서부터 잘라낸다(앞 구간 오프셋이 밀리지 않게)."""
    for start, end in sorted(spans, reverse=True):
        xml = xml[:start] + xml[end:]
    return xml


def top_level_paragraph_spans(section_xml: str):
    """최상위 `<hp:p>` 요소의 (시작, 끝) 구간. 표 안 문단은 세지 않는다."""
    spans, depth, open_at = [], 0, None
    for start, end, name, kind in _iter_xml_tags(section_xml):
        if kind in ("comment", "cdata", "pi", "decl"):
            continue
        if kind == "start":
            if depth == 1 and name == "p":
                open_at = start
            depth += 1
        elif kind == "end":
            depth -= 1
            if depth == 1 and name == "p" and open_at is not None:
                spans.append((open_at, end))
                open_at = None
        elif depth == 1 and name == "p":
            spans.append((start, end))
    return spans


def _normalize_space(text: str) -> str:
    return _SPACE_RE.sub(" ", text or "").strip()


def _paragraph_texts(section_xml: str):
    """최상위 문단 텍스트 목록(ElementTree, 문서 순서)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(section_xml)
    return [_normalize_space("".join(t.text or "" for t in para.iter(_TEXT_TAG)))
            for para in root if para.tag == _PARA_TAG]


def _strip_header_controls(para: str, *, keep_colpr: bool):
    """한 문단에서 secPr·pageNum·newNum·colPr 제어 요소를 걷어낸다."""
    para = _cut_spans(para, _element_spans(para, "secPr"))
    para = _PAGE_NUM_CTRL_RE.sub("", para)
    para = _NEW_NUM_CTRL_RE.sub("", para)
    match = _COLPR_CTRL_RE.search(para) if keep_colpr else None
    return _COLPR_CTRL_RE.sub("", para), (match.group(0) if match else None)


def _insert_run_prefix(para: str, fragment: str) -> str:
    match = _RUN_OPEN_RE.search(para)
    if match is None:
        raise HwpxBlocked(
            "section_split_control_count",
            action="구역 첫 문단에 run이 없어 제어 요소를 넣을 수 없습니다.",
        )
    return para[:match.end()] + fragment + para[match.end():]


def _insert_after_run_text(para: str, fragment: str) -> str:
    runs = _element_spans(para, "run")
    if not runs:
        raise HwpxBlocked(
            "section_split_control_count",
            action="앵커 문단에 run이 없어 쪽번호 재시작을 넣을 수 없습니다.",
        )
    start, end = runs[0]
    match = _T_ELEMENT_RE.search(para, start, end)
    at = match.end() if match else para.rfind("</hp:run>", start, end)
    if at < 0:
        raise HwpxBlocked(
            "section_split_control_count",
            action="앵커 문단 첫 run의 끝을 찾지 못했습니다.",
        )
    return para[:at] + fragment + para[at:]


def _page_num_control(fmt):
    pos = str(fmt.get("pos", "BOTTOM_CENTER"))
    if pos == "NONE":
        return ""
    return ('<hp:ctrl><hp:pageNum pos="%s" formatType="%s" sideChar="-"/>'
            '</hp:ctrl>' % (pos, fmt.get("format", "DIGIT")))


def split_sections_by_plan(section_xml: str, *, nodes, plan,
                           section_formats=FOOD_CROPS_SECTION_FORMATS,
                           major_id=None):
    """단일 구역 section0.xml을 겉표지/앞부분/본문 3구역으로 자른다.

    분할 지점은 문서 계획의 노드 색인이다(텍스트 탐색 없음). 절차는
    지우기 → 나누기 → 넣기 → 세기 순서를 지킨다. 계획이 없으면 앞머리
    표지가 하나도 없을 때만 유예하고(`not_applicable`), 하나라도 있으면
    거절한다.

    반환: ``(sections, info)`` — ``sections``는 3개 문자열(유예면 None),
    ``info``는 ``{"status", "names", "controls"}``.

    ``major_id``는 계획이 없을 때 쓸 앞머리 구분 이름의 출처다. 구분 이름은
    전공마다 다르므로 이 경로를 타는 빌더는 넘겨받은 전공 ID를 넘긴다
    (생략하면 식량작물 — 이 경로를 열어 준 1판과 같은 값).
    """
    names = ["Contents/section%d.xml" % i for i in range(3)]
    if plan is None:
        present = _front_matter_markers(nodes, major_id)
        if not present:
            return None, {"status": "not_applicable", "names": []}
        markers = set(_front_markers(major_id))
        raise HwpxBlocked(
            "section_split_front_matter_partial",
            missing=sorted(markers - present), present=sorted(present),
            action="앞머리 표지가 일부만 있어 구역을 나눌 수 없습니다.",
        )

    # 앞부분 앵커 `요약`은 **앞부분 구간에서** 한 번만 있어야 한다. 계획은
    # 첫 번째 것만 잡으므로, 원고·HWPX 양쪽에 `요약`이 둘이면 문단 수가 맞아도
    # 둘째가 조용히 요약 본문(12 pt)으로 들어간다. `감사의 글`은 목차 항목과
    # 본문 끝 제목 두 번 나오는 것이 정상이므로 중복 검사에서 뺀다.
    # 본문·목차의 독립 `요약` 문단은 앵커가 아니므로 세지 않는다(문서 전체를
    # 세면 정상 원고가 section_split_anchor_ambiguous로 막힌다).
    body_start = plan["body"]
    summary_hits = [index for index, node in enumerate(nodes[:body_start])
                    if _normalize_space(node.get("text", "")) == "요약"]
    if len(summary_hits) != 1:
        raise HwpxBlocked(
            "section_split_anchor_ambiguous",
            anchor="요약", occurrences=summary_hits, nodes=len(nodes),
            action="앞부분 앵커 `요약`이 문서에 정확히 한 번 있어야 합니다.",
        )

    anchors = (("cover", 0),
               ("front", plan["indices"]["요약"]),
               ("body", plan["body"]))
    indices = [index for _, index in anchors]
    if (len(set(indices)) != 3 or sorted(indices) != indices
            or not 0 <= indices[0] < indices[1] < indices[2] < len(nodes)):
        raise HwpxBlocked(
            "section_split_anchor_ambiguous",
            anchors={role: index for role, index in anchors},
            nodes=len(nodes),
            action="앞부분 앵커 색인이 서로 다르고 증가하지 않습니다.",
        )

    texts = _paragraph_texts(section_xml)
    if len(texts) != len(nodes):
        raise HwpxBlocked(
            "section_split_node_map_mismatch",
            paragraphs=len(texts), nodes=len(nodes),
            action="HWPX 문단 수와 원고 노드 수가 다릅니다.",
        )
    spans = top_level_paragraph_spans(section_xml)
    if len(spans) != len(nodes):
        raise HwpxBlocked(
            "section_split_node_map_mismatch",
            paragraphs=len(spans), nodes=len(nodes),
            action="최상위 문단 스캔 결과가 원고 노드 수와 다릅니다.",
        )
    for role, index in anchors:
        expected = _normalize_space(nodes[index].get("text", ""))
        if texts[index] != expected:
            raise HwpxBlocked(
                "section_split_node_map_mismatch",
                role=role, index=index,
                node=expected, paragraph=texts[index],
                action="앵커 문단 텍스트가 원고 노드와 다릅니다.",
            )

    # 문단 사이 구분자·머리·꼬리는 문단 밖에 살아 있으므로 미리 떼어 둔다.
    prefix = section_xml[:spans[0][0]]
    suffix = section_xml[spans[-1][1]:]
    separators = [section_xml[spans[i][1]:spans[i + 1][0]]
                  for i in range(len(spans) - 1)]
    raw = [section_xml[start:end] for start, end in spans]

    cleaned, colpr = [], None
    for i, para in enumerate(raw):
        para, kept = _strip_header_controls(para, keep_colpr=(i == 0))
        if kept is not None:
            colpr = kept
        cleaned.append(para)
    if colpr is None:
        raise HwpxBlocked(
            "section_split_control_count",
            action="원본 첫 문단에 colPr 제어 요소가 없습니다.",
        )

    starts = indices + [len(cleaned)]
    sections, controls = [], []
    for order in range(3):
        group = cleaned[starts[order]:starts[order + 1]]
        if not group:
            raise HwpxBlocked(
                "section_split_anchor_ambiguous",
                role=anchors[order][0], empty_group=True,
                action="구역에 문단이 하나도 없습니다.",
            )
        fmt = dict(section_formats[order]) if section_formats else {}
        sec_pr = _element_spans(raw[0], "secPr")
        if not sec_pr:
            raise HwpxBlocked(
                "section_split_control_count",
                action="원본 첫 문단에 secPr가 없습니다.",
            )
        secpr_xml = raw[0][sec_pr[0][0]:sec_pr[0][1]]
        group[0] = _insert_run_prefix(
            group[0], secpr_xml + colpr + _page_num_control(fmt.get("page_number", {})))
        restart = (fmt.get("page_number") or {}).get("restart")
        if restart is not None:
            group[0] = _insert_after_run_text(group[0], NEW_NUM_CONTROL)
        body = group[0]
        for offset, para in enumerate(group[1:]):
            body += separators[starts[order] + offset] + para
        sections.append(prefix + body + suffix)

    info = {"status": "split", "names": names,
            "controls": _count_section_controls(sections, section_formats)}
    return sections, info


def _front_markers(major_id=None):
    """이 전공의 앞머리 구분 이름 순서. 전공 미지정이면 식량작물(옛 값)."""
    import gg_document as gd

    return gd.school_frontmatter_spec(major_id or "food_crops").orders[0]


def _front_matter_markers(nodes, major_id=None):
    markers = set(_front_markers(major_id))
    return {text for text in
            (_normalize_space(n.get("text", "")) for n in nodes)
            if text in markers}


def _expected_page_numbers(index, section_formats):
    fmt = (section_formats or FOOD_CROPS_SECTION_FORMATS)[index]
    page = fmt.get("page_number") or {}
    restart = page.get("restart")
    return {
        "secPr": 1,
        "colPr": 1,
        "pageNum": 0 if str(page.get("pos", "NONE")) == "NONE" else 1,
        "newNum": 0 if restart is None else 1,
    }


def _count_section_controls(sections, section_formats):
    """구역별 제어 요소 개수를 세고 기대값과 다르면 거절한다."""
    measured = []
    for index, section in enumerate(sections):
        counts = {
            "secPr": len(_element_spans(section, "secPr")),
            "colPr": len(_element_spans(section, "colPr")),
            "pageNum": len(_element_spans(section, "pageNum")),
            "newNum": len(_element_spans(section, "newNum")),
        }
        expected = _expected_page_numbers(index, section_formats)
        if counts != expected:
            raise HwpxBlocked(
                "section_split_control_count",
                section=index, expected=expected, found=counts,
                action="구역 분할 뒤 제어 요소 개수가 기대와 다릅니다.",
            )
        spans = top_level_paragraph_spans(section)
        first = section[spans[0][0]:spans[0][1]] if spans else ""
        run = _RUN_OPEN_RE.search(first)
        if run is None or not first[run.end():].startswith("<hp:secPr"):
            raise HwpxBlocked(
                "section_split_control_count",
                section=index, secpr_direct_child=False,
                action="secPr가 구역 첫 문단 첫 run의 직속 자식이 아닙니다.",
            )
        measured.append(counts)
    return measured


# --- 앞부분·동의서 전용 글자 모양(§4.3) ----------------------------------

# (포인트, 굵기) — 1판 header.xml에 없는 크기만 만든다. id0(10pt) 형태를
# 그대로 복제하고 id는 기존 itemCnt부터 이어 붙인다. 본문 문단은 건드리지
# 않으므로 본문 charPr 히스토그램이 그대로 남는다.
_FRONT_CHARPR_SPECS = (
    (10, False),
    (12, False),
    (14, True), (14, False),
    (16, True), (16, False),
    (18, True), (18, False),
    (20, True), (20, False),
    (22, True), (22, False),
    (32, True), (32, False),
)

_CHARPR_ITEM_CNT_RE = re.compile(
    r'(<hh:charProperties\b[^>]*\bitemCnt=")(\d+)(")')
_CHARPR_END_TAG = "</hh:charProperties>"
_CHARPR_TEMPLATE = (
    '      <hh:charPr id="%d" height="%d" textColor="#000000" shadeColor="none"'
    ' useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1"%s>\n'
    '        <hh:fontRef hangul="0" latin="0" hanja="0" japanese="0"'
    ' other="0" symbol="0" user="0"/>\n'
    '        <hh:ratio hangul="100" latin="100" hanja="100" japanese="100"'
    ' other="100" symbol="100" user="100"/>\n'
    '        <hh:spacing hangul="0" latin="0" hanja="0" japanese="0"'
    ' other="0" symbol="0" user="0"/>\n'
    '        <hh:relSz hangul="100" latin="100" hanja="100" japanese="100"'
    ' other="100" symbol="100" user="100"/>\n'
    '        <hh:offset hangul="0" latin="0" hanja="0" japanese="0"'
    ' other="0" symbol="0" user="0"/>%s\n'
    '      </hh:charPr>'
)


def _append_front_charprs(header_xml: str):
    """앞부분·동의서 전용 charPr를 header.xml 끝에 덧붙인다.

    반환: ``(새 header.xml, {(포인트, 굵기): charPr id})``. 기존 charPr는
    하나도 고치지 않는다(본문 참조가 그대로 살아남는다).
    """
    match = _CHARPR_ITEM_CNT_RE.search(header_xml)
    if match is None or _CHARPR_END_TAG not in header_xml:
        raise HwpxBlocked(
            "charpr_properties_missing",
            has_item_cnt=match is not None,
            has_end_tag=_CHARPR_END_TAG in header_xml,
            action="header.xml에 charProperties가 없어 글자 모양을 더할 수 없습니다.",
        )
    first = int(match.group(2))
    ids, entries = {}, []
    for offset, (size, bold) in enumerate(_FRONT_CHARPR_SPECS):
        cid = first + offset
        entries.append(_CHARPR_TEMPLATE % (
            cid, size * 100,
            ' bold="1"' if bold else '',
            "<hh:bold/>" if bold else ''))
        ids[(size, bold)] = cid
    end = header_xml.index(_CHARPR_END_TAG)
    line_start = header_xml.rfind("\n", 0, end) + 1
    # 한 줄로 압축된 header.xml(`rfind`가 -1)에서는 줄 시작이 0이라 그대로
    # 넣으면 XML 선언 앞에 charPr가 붙어 파싱 불가 파일이 된다. 끝 태그
    # 앞에 공백만 있는지로 판정하고, 아니면 끝 태그 바로 앞에 넣는다.
    at = line_start if not header_xml[line_start:end].strip() else end
    updated = (header_xml[:at] + "\n" + "\n".join(entries) + "\n"
               + header_xml[at:])
    updated = _CHARPR_ITEM_CNT_RE.sub(
        lambda m: m.group(1) + str(first + len(_FRONT_CHARPR_SPECS)) + m.group(3),
        updated, count=1)
    return updated, ids


def _rewrite_run_charpr(paragraph: str, cid: int) -> str:
    """한 문단의 모든 run charPrIDRef를 ``cid``로 바꾼다."""
    replacement = 'charPrIDRef="%d"' % cid

    def rebuild(match):
        tag = match.group(0)
        if 'charPrIDRef="' in tag:
            return re.sub(r'charPrIDRef="[^"]*"', replacement, tag)
        return tag[:-1] + " " + replacement + ">"

    return _RUN_OPEN_RE.sub(rebuild, paragraph)


def _peer_block_targets(nodes, start, end, spec, *, cover=False):
    """겉표지·표제면·제출서·인준서 블록의 문단별 (크기, 굵기)를 정한다.

    계획 제목 문단은 `spec.plan_label`과 같은 글자를 먼저 찾고, 없으면
    학위논문 표기 바로 다음 문단을 제목으로 본다(원고 제목은 자유 문구라
    양식 라벨과 다를 수 있다).
    """
    items = [(index, _normalize_space(nodes[index].get("text", "")))
             for index in range(start, end)]
    items = [(index, text) for index, text in items if text]
    targets = {}
    plan_at = next((k for k, (_, text) in enumerate(items)
                    if text == spec.plan_label), None)
    degree_at = next((k for k, (_, text) in enumerate(items)
                      if text == spec.degree), None)
    if plan_at is None and degree_at is not None and degree_at + 1 < len(items):
        plan_at = degree_at + 1
    for k, (index, text) in enumerate(items):
        if k == plan_at:
            targets[index] = (32, True)
        elif (text == spec.degree
              or text.startswith(("이 논문을", "지도교수 ", "위원장", "위 원"))
              or text.endswith(("인준함", "인준함."))):
            targets[index] = (18, True)
        elif plan_at is not None and k == plan_at + 1:
            targets[index] = (20, cover)
        else:
            targets[index] = (20, False)
    return targets


def _front_role_bounds(plan):
    """역할별 본문 구간 ``{역할: (시작, 끝)}`` — 역할 이름 문단 **다음**부터."""
    indices = plan["indices"]
    order = list(plan["order"])
    bounds = {}
    for position, role in enumerate(order):
        start = indices[role] + 1
        end = (indices[order[position + 1]] if position + 1 < len(order)
               else plan["body"])
        bounds[role] = (start, end)
    return bounds


def _front_charpr_targets(nodes, plan):
    """계획 색인으로 대상 문단을 찾아 {(문단 색인): (크기, 굵기)}를 낸다.

    본문 구역(`plan["body"]`)은 손대지 않고, 동의서만 따로 잠근다.
    """
    spec = plan["spec"]
    indices = plan["indices"]
    targets = {}
    bounds = _front_role_bounds(plan)

    for role in ("겉표지", "표제면", "제출서", "인준서"):
        if role in bounds:
            start, end = bounds[role]
            targets.update(_peer_block_targets(
                nodes, start, end, spec, cover=role == "겉표지"))

    if "요약" in indices:
        targets[indices["요약"]] = (22, True)
        start, end = bounds["요약"]
        for index in range(start, end):
            text = _normalize_space(nodes[index].get("text", ""))
            if not text:
                continue
            targets[index] = (16, True) if re.match(r"^\d+\s*\.", text) else (12, False)

    if "감사의 글" in indices:
        targets[indices["감사의 글"]] = (20, True)
        start, end = bounds["감사의 글"]
        for index in range(start, end):
            if _normalize_space(nodes[index].get("text", "")):
                targets[index] = (12, False)

    if "목차" in indices:
        targets[indices["목차"]] = (22, True)
        start, end = bounds["목차"]
        for index in range(start, end):
            text = _normalize_space(nodes[index].get("text", ""))
            if not text:
                continue
            targets[index] = (20, False) if text in ("표 목차", "그림 목차") else (12, False)

    consent = next((index for index in range(plan["body"], len(nodes))
                    if _normalize_space(nodes[index].get("text", "")) == "학부모 동의서"),
                   None)
    if consent is not None:
        targets[consent] = (20, True)
        for index in range(consent + 1, len(nodes)):
            text = _normalize_space(nodes[index].get("text", ""))
            if not text:
                continue
            targets[index] = (10, False) if text.startswith("※") else (14, True)
    return targets


def _apply_front_charpr(section_xml: str, *, nodes, plan, ids, start=0, end=None):
    """대상 문단 run의 charPrIDRef만 새 charPr로 바꾼다.

    ``start``/``end``는 이 구역이 맡는 전체 문단 색인 구간이다(구역 분할
    뒤에는 구역마다 다르다). 반환: ``(새 구역 XML, 바꾼 문단 수)``.
    """
    end = len(nodes) if end is None else end
    targets = {index - start: spec
               for index, spec in _front_charpr_targets(nodes, plan).items()
               if start <= index < end}
    if not targets:
        return section_xml, 0
    spans = top_level_paragraph_spans(section_xml)
    if len(spans) != end - start:
        raise HwpxBlocked(
            "section_split_node_map_mismatch",
            paragraphs=len(spans), nodes=end - start,
            action="HWPX 문단 수와 원고 노드 수가 다릅니다.",
        )
    pieces, cursor, changed = [], 0, 0
    for index, (start, end) in enumerate(spans):
        spec = targets.get(index)
        if spec is None:
            continue
        cid = ids.get(spec)
        if cid is None:
            raise HwpxBlocked(
                "charpr_properties_missing", size=spec[0], bold=spec[1],
                action="필요한 글자 크기의 charPr를 만들지 못했습니다.",
            )
        pieces.append(section_xml[cursor:start])
        pieces.append(_rewrite_run_charpr(section_xml[start:end], cid))
        cursor = end
        changed += 1
    pieces.append(section_xml[cursor:])
    return "".join(pieces), changed


# 구분 이름(`겉표지`·`표제면`·`제출서`·`인준서`)은 우리 문서 계획이 만든 관리용
# 줄이다. 한글판은 이 줄을 화면에 그대로 내보내므로 문단은 남기고 글자만 비운다
# (문단을 지우면 구역 분할의 문단 수 1:1 계약이 깨진다). `요약`·`감사의 글`·
# `목차`·`표 목차`·`그림 목차`는 학교 서식 검사기가 **글자로 찾으므로** 비우지
# 않는다(`gg_school_format_hwpx._check_charpr`).
_HIDDEN_FRONT_ROLES = ("겉표지", "표제면", "제출서", "인준서")
_TOC_ROLE = "목차"
_TOC_SUB_HEADS = ("표 목차", "그림 목차")
# 쪽을 나누는 역할: 표제면·제출서·인준서와 목차류. `겉표지`는 구역 첫 문단,
# `요약`은 구역 시작 자체가 쪽 나눔, `감사의 글`은 Word판에도 나눔이 없다.
_FRONT_BREAK_ROLES = ("표제면", "제출서", "인준서")
_T_SELF_CLOSING_RE = re.compile(r"<hp:t\b[^>]*/>")


def _root_open_tag(section_xml: str):
    """구역 XML 루트 요소의 (열림 태그 문자열, 접두사 붙은 이름)."""
    for start, end, name, kind in _iter_xml_tags(section_xml):
        if kind == "start":
            token = section_xml[start:end]
            return token, token[1:].split(None, 1)[0].rstrip("/>")
    raise HwpxBlocked(
        "section_split_node_map_mismatch",
        action="구역 XML에서 루트 요소를 찾지 못했습니다.",
    )


def _fragment_text(paragraph: str, root_open: str) -> str:
    """문단 조각 하나의 `<hp:t>` 글자를 모아 공백을 정규화한다.

    조각은 구역 루트에서 잘라 낸 것이라 자기 `xmlns:` 선언이 없다. 루트 열림
    태그를 그대로 재사용해 감싸면 접두사·엔티티가 원문과 같은 뜻으로 읽힌다
    (`_paragraph_texts`와 같은 ElementTree 경로).
    """
    import xml.etree.ElementTree as ET

    wrapped = "%s%s</%s>" % (root_open[0], paragraph, root_open[1])
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as exc:
        raise HwpxBlocked(
            "front_marker_text_mismatch",
            actual=None, parse_error=str(exc),
            action="구분 이름 문단을 XML로 읽지 못해 은닉 여부를 확인할 수 없습니다.",
        ) from exc
    return _normalize_space("".join(node.text or ""
                                    for node in root.iter(_TEXT_TAG)))


def _blank_paragraph_text(paragraph: str) -> str:
    """문단의 모든 `<hp:t>` 글자만 비운다(제어 요소·`linesegarray` 보존)."""
    # 자기닫힘(`<hp:t/>`)을 먼저 바꾼다. 그러지 않으면 `_T_ELEMENT_RE`가
    # 자기닫힘 태그에서 시작해 뒤 문단 글자까지 한 덩이로 삼킬 수 있다.
    paragraph = _T_SELF_CLOSING_RE.sub("<hp:t></hp:t>", paragraph)
    return _T_ELEMENT_RE.sub("<hp:t></hp:t>", paragraph)


def _front_paragraph_spans(section_xml: str, para_start: int, para_end: int):
    """구역 문단 구간과 원고 노드 수가 맞는지 확인하고 spans를 낸다."""
    spans = top_level_paragraph_spans(section_xml)
    if len(spans) != para_end - para_start:
        raise HwpxBlocked(
            "section_split_node_map_mismatch",
            paragraphs=len(spans), nodes=para_end - para_start,
            action="HWPX 문단 수와 원고 노드 수가 다릅니다.",
        )
    return spans


def _hide_front_markers(section_xml: str, *, nodes, plan,
                        para_start: int, para_end: int):
    """이 구역이 맡는 구분 이름 문단의 글자를 비운다.

    반환: ``(새 구역 XML, None)`` 또는
    ``(새 구역 XML, {"roles": […], "paragraphs": [구역 로컬 색인…]})``.
    """
    if plan is None:
        return section_xml, None
    indices = plan["indices"]
    targets = {indices[role]: role for role in _HIDDEN_FRONT_ROLES
               if role in indices and para_start <= indices[role] < para_end}
    if not targets:
        return section_xml, None
    spans = _front_paragraph_spans(section_xml, para_start, para_end)
    # 비우기 전에 그 자리 글자가 정말 기대 구분 이름인지 본다. 노드↔문단
    # 대응이 어긋나도 문단 수만 맞으면 제목 같은 실제 내용을 지워 버리기
    # 때문이다(웹 r1 중요 01). 하나라도 다르면 아무것도 비우지 않고 멈춘다.
    root_open = None
    for index in sorted(targets):
        start, end = spans[index - para_start]
        if root_open is None:
            root_open = _root_open_tag(section_xml)
        text = _fragment_text(section_xml[start:end], root_open)
        if text != targets[index]:
            raise HwpxBlocked(
                "front_marker_text_mismatch",
                role=targets[index], index=index, paragraph=index - para_start,
                expected=targets[index], actual=text,
                action="구분 이름 문단 자리에 다른 글자가 있어 은닉할 수 없습니다.",
            )
    pieces, cursor = [], 0
    for index in sorted(targets):
        start, end = spans[index - para_start]
        pieces.append(section_xml[cursor:start])
        pieces.append(_blank_paragraph_text(section_xml[start:end]))
        cursor = end
    pieces.append(section_xml[cursor:])
    return "".join(pieces), {
        "roles": [targets[index] for index in sorted(targets)],
        "paragraphs": [index - para_start for index in sorted(targets)],
    }


_PARAGRAPH_HEAD_RE = re.compile(r"<hp:p\b[^>]*>")
# 속성값은 큰따옴표·작은따옴표 둘 다 XML에서 허용된다. 작은따옴표를 못 보면
# 같은 이름의 속성을 하나 더 붙여 XML이 깨진다(웹 r1 중요 02).
_PAGE_BREAK_ATTR_RE = re.compile(
    r"""(?<![\w:.\-])pageBreak\s*=\s*(?:"[^"]*"|'[^']*')""")


def _set_page_break(paragraph: str) -> str:
    """문단 헤드의 `pageBreak`를 1로 만든다(속성이 없으면 넣는다)."""
    head = _PARAGRAPH_HEAD_RE.match(paragraph).group(0)
    # 존재 판정은 **매치 개수**로 한다. 치환 결과 비교로 판정하면 이미
    # `pageBreak="1"`인 헤드는 결과가 입력과 같아져 "속성 없음"으로 오판하고
    # 같은 속성을 하나 더 붙인다(웹 r2 중요 02).
    new_head, found = _PAGE_BREAK_ATTR_RE.subn('pageBreak="1"', head, count=1)
    if not found:
        closing = "/>" if head.endswith("/>") else ">"
        new_head = head[:-len(closing)] + ' pageBreak="1"' + closing
    return new_head + paragraph[len(head):]


def _break_front_pages(section_xml: str, *, nodes, plan,
                       para_start: int, para_end: int):
    """쪽을 나누는 앞부분 제목 문단에 `pageBreak="1"`을 준다.

    대상은 노드 색인으로만 고른다: `표제면`·`제출서`·`인준서` + `목차`와 목차
    블록 안의 `표 목차`·`그림 목차`(`_front_charpr_targets`와 같은 구간·비교).
    """
    if plan is None:
        return section_xml, None
    indices = plan["indices"]
    targets = {indices[role] for role in _FRONT_BREAK_ROLES
               if role in indices and para_start <= indices[role] < para_end}
    if _TOC_ROLE in indices and para_start <= indices[_TOC_ROLE] < para_end:
        targets.add(indices[_TOC_ROLE])
        toc_start, toc_end = _front_role_bounds(plan)[_TOC_ROLE]
        for index in range(toc_start, min(toc_end, len(nodes))):
            if not para_start <= index < para_end:
                continue
            if _normalize_space(nodes[index].get("text", "")) in _TOC_SUB_HEADS:
                targets.add(index)
    if not targets:
        return section_xml, None
    spans = _front_paragraph_spans(section_xml, para_start, para_end)
    pieces, cursor = [], 0
    for index in sorted(targets):
        start, end = spans[index - para_start]
        pieces.append(section_xml[cursor:start])
        pieces.append(_set_page_break(section_xml[start:end]))
        cursor = end
    pieces.append(section_xml[cursor:])
    return "".join(pieces), {
        "paragraphs": [index - para_start for index in sorted(targets)]}


def normalize_markdown(data: bytes) -> str:
    """UTF-8(BOM 허용) 바이트 → BOM 제거·개행 LF 정규화 문자열."""
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


_SEC_CNT_RE = re.compile(r'(<hh:head\b[^>]*\bsecCnt=")(\d+)(")')
_HAS_HEAD_RE = re.compile(r"<hh:head\b")


def _new_section_info(source_info, name):
    info = zipfile.ZipInfo(name, date_time=source_info.date_time)
    info.compress_type = source_info.compress_type
    info.external_attr = source_info.external_attr
    return info


def _split_zip_rank(name):
    """분할 산출물의 ZIP 항목 순서(한글·kordoc이 읽는 검증된 순서)."""
    if name == MIMETYPE:
        return (0, 0)
    if name == "META-INF/":
        return (1, 0)
    if name == "META-INF/container.xml":
        return (1, 1)
    if name == "Contents/":
        return (2, 0)
    if name == "Contents/content.hpf":
        return (3, 0)
    if name == "Contents/section1.xml":
        return (4, 0)
    if name == "Contents/section2.xml":
        return (4, 1)
    if name == "BinData/":
        return (5, 0)
    if name.startswith("BinData/"):
        return (5, 1)
    if name == HEADER_NAME:
        return (6, 0)
    if name == "Contents/section0.xml":
        return (7, 0)
    if name == "Preview/":
        return (8, 0)
    if name.startswith("Preview/"):
        return (8, 1)
    return (9, 0)


def _bump_section_count(header_xml):
    """`header.xml`의 `secCnt`를 3으로 올린다."""
    match = _SEC_CNT_RE.search(header_xml)
    if match is None:
        raise HwpxBlocked(
            "section_split_header_missing",
            has_head=bool(_HAS_HEAD_RE.search(header_xml)),
            action="header.xml에서 secCnt 속성을 찾지 못해 구역 수를 고칠 수 없습니다.",
        )
    return _SEC_CNT_RE.sub(lambda m: m.group(1) + "3" + m.group(3),
                           header_xml, count=1)


def _extend_manifest(hpf_xml):
    """`content.hpf`에 section1·section2 항목과 spine 참조를 넣는다."""
    item0 = re.search(r'<opf:item\b[^>]*\bid="section0"[^>]*/>', hpf_xml)
    ref0 = re.search(r'<opf:itemref\b[^>]*\bidref="section0"[^>]*/>', hpf_xml)
    if item0 is None or ref0 is None:
        raise HwpxBlocked(
            "section_split_manifest_missing",
            has_item=item0 is not None, has_itemref=ref0 is not None,
            action="content.hpf에 section0 항목이 없어 구역을 더할 수 없습니다.",
        )
    indent = re.match(r"[ \t]*", hpf_xml[hpf_xml.rfind("\n", 0, item0.start()) + 1:]).group(0)
    items = "".join(
        '\n%s<opf:item id="section%d" href="Contents/section%d.xml"'
        ' media-type="application/xml"/>' % (indent, i, i) for i in (1, 2))
    refs = "".join(
        '\n%s<opf:itemref idref="section%d" linear="yes"/>' % (indent, i)
        for i in (1, 2))
    hpf_xml = hpf_xml[:item0.end()] + items + hpf_xml[item0.end():]
    ref0 = re.search(r'<opf:itemref\b[^>]*\bidref="section0"[^>]*/>', hpf_xml)
    return hpf_xml[:ref0.end()] + refs + hpf_xml[ref0.end():]


def _section_format_for(name, section_formats):
    match = re.fullmatch(r"Contents/section(\d)\.xml", name)
    if match is None or section_formats is None:
        return None
    index = int(match.group(1))
    return section_formats[index] if index < len(section_formats) else None


def _section_node_range(name, plan):
    """분할된 구역 파일이 맡는 전체 문단 색인 구간 ``(시작, 끝)``."""
    match = re.fullmatch(r"Contents/section(\d)\.xml", name)
    if match is None or plan is None:
        return None
    starts = (0, plan["indices"]["요약"], plan["body"])
    index = int(match.group(1))
    if index >= len(starts):
        return None
    end = starts[index + 1] if index + 1 < len(starts) else None
    return starts[index], end


def transform_hwpx(data: bytes, *, font: str = SCHOOL_FONT,
                   margins_mm=SCHOOL_MARGINS_MM, page_numbers: bool = True,
                   section_formats=None, nodes=None, plan=None,
                   major_id=None):
    """kordoc HWPX 바이트를 학교 규격으로 다시 쓴다.

    mimetype을 첫 항목·무압축으로 보존하고 나머지 항목은 순서대로 다시 쓴다.
    ``section_formats``가 있으면(식량작물) 맨 앞에서 구역을 3개로 나누고
    (`split_sections_by_plan`) 그 뒤에 구역별 여백·쪽번호를 적용한다.
    반환: (새 바이트, 적용 내역 dict).
    """
    applied = {"margins": [], "font": [], "page_numbers": []}
    if section_formats is not None:
        # 앞부분 글자 모양 기록은 구역 서식(식량작물) 전용이다. 다른 전공
        # 매니페스트에 키가 생기면 post_processing 직렬화가 바뀐다.
        applied["charpr"] = []
        applied["front_hidden"] = []
        applied["front_breaks"] = []
    source = io.BytesIO(data)
    target = io.BytesIO()
    with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(target, "w") as zout:
        infos = zin.infolist()
        names = {info.filename for info in infos}
        if MIMETYPE not in names:
            raise HwpxBlocked(
                "hwpx_missing_mimetype",
                action="kordoc 산출물이 HWPX 구조가 아닙니다. generate 출력을 확인하세요.",
            )
        members = {info.filename: zin.read(info.filename) for info in infos}
        mimetype = next(info for info in infos if info.filename == MIMETYPE)
        stored = zipfile.ZipInfo(MIMETYPE, date_time=mimetype.date_time)
        stored.compress_type = zipfile.ZIP_STORED
        stored.external_attr = mimetype.external_attr
        zout.writestr(stored, members[MIMETYPE] or MIMETYPE_BYTES)

        formats = None
        if section_formats is not None:
            built = members.get("Contents/section0.xml")
            sections, info = split_sections_by_plan(
                built.decode("utf-8") if built is not None else "",
                nodes=nodes or [], plan=plan, section_formats=section_formats,
                major_id=major_id,
            )
            if sections is None:
                applied["sections"] = info["status"]
                applied["section_controls"] = {}
            else:
                formats = section_formats
                applied["sections"] = info["names"]
                applied["section_controls"] = info["controls"]
                for index, section in enumerate(sections):
                    members["Contents/section%d.xml" % index] = section.encode("utf-8")
                if HEADER_NAME in members:
                    members[HEADER_NAME] = _bump_section_count(
                        members[HEADER_NAME].decode("utf-8")).encode("utf-8")
                if "Contents/content.hpf" in members:
                    members["Contents/content.hpf"] = _extend_manifest(
                        members["Contents/content.hpf"].decode("utf-8")).encode("utf-8")
                for index in (1, 2):
                    infos.append(_new_section_info(
                        next(i for i in infos if i.filename == "Contents/section0.xml"),
                        "Contents/section%d.xml" % index))
            if formats is not None:
                infos.sort(key=lambda info: _split_zip_rank(info.filename))

        charpr_ids = None
        if formats is not None and plan is not None and HEADER_NAME in members:
            header_text = members[HEADER_NAME].decode("utf-8")
            if _CHARPR_ITEM_CNT_RE.search(header_text) and _CHARPR_END_TAG in header_text:
                header_text, charpr_ids = _append_front_charprs(header_text)
                members[HEADER_NAME] = header_text.encode("utf-8")

        for info in infos:
            if info.filename == MIMETYPE:
                continue
            member = members[info.filename]
            override = _section_format_for(info.filename, formats)
            if SECTION_RE.fullmatch(info.filename):
                text = member.decode("utf-8")
                updated = (rewrite_margins(text, margins_mm, override=override)
                           if margins_mm is not None or override is not None else text)
                if updated != text:
                    applied["margins"].append(info.filename)
                if page_numbers:
                    numbered = inject_page_numbers(updated, override=override)
                    if numbered != updated:
                        applied["page_numbers"].append(info.filename)
                    updated = numbered
                if charpr_ids is not None and formats is not None:
                    bounds = _section_node_range(info.filename, plan)
                    if bounds is not None:
                        updated, changed = _apply_front_charpr(
                            updated, nodes=nodes or [], plan=plan,
                            ids=charpr_ids, start=bounds[0], end=bounds[1])
                        if changed:
                            applied["charpr"].append(info.filename)
                        updated, hidden = _hide_front_markers(
                            updated, nodes=nodes or [], plan=plan,
                            para_start=bounds[0], para_end=bounds[1])
                        if hidden:
                            applied["front_hidden"].append(
                                {"section": info.filename, **hidden})
                        updated, breaks = _break_front_pages(
                            updated, nodes=nodes or [], plan=plan,
                            para_start=bounds[0], para_end=bounds[1])
                        if breaks:
                            applied["front_breaks"].append(
                                {"section": info.filename, **breaks})
                member = updated.encode("utf-8")
            elif info.filename == HEADER_NAME:
                text = member.decode("utf-8")
                updated = rewrite_fonts(text, font) if font is not None else text
                if updated != text:
                    applied["font"].append(info.filename)
                member = updated.encode("utf-8")
            zout.writestr(info, member)
    return target.getvalue(), applied


def missing_format_steps(applied, *, font, margins_mm, page_numbers,
                         section_formats=None):
    """요청한 서식 변환 중 하나도 적용되지 않은 항목 목록.

    kordoc 산출물의 네임스페이스 접두사·비자기닫힘 요소·colPr 부재 등으로
    정규식이 아무것도 못 바꿨는데도 그대로 발행해 매니페스트가 적용된 것처럼
    적는 일을 막는다. None/False인 항목은 요청하지 않은 것으로 본다.
    ``section_formats``가 있으면 구역 분할·구역별 제어 요소·앞부분 글자
    모양까지 실제로 붙었는지 함께 본다.
    """
    requested = []
    if margins_mm is not None:
        requested.append("margins")
    if font is not None:
        requested.append("font")
    # 구역 분할 경로에서는 쪽번호를 구역 첫 문단의 pageNum/newNum이 맡는다
    # (inject_page_numbers는 확인만 한다). 그래서 아래 section_controls로 본다.
    if page_numbers and section_formats is None:
        requested.append("page_numbers")
    missing = [name for name in requested if not applied.get(name)]
    if section_formats is None:
        return missing
    # 앞머리 표지가 하나도 없는 본문-only 입력은 설계가 유일하게 허용한
    # no-op이다. 이때는 구역 분할 검사를 요구하지 않는다.
    if applied.get("sections") == "not_applicable":
        return missing
    names = applied.get("sections")
    expected = ["Contents/section%d.xml" % i for i in range(len(section_formats))]
    if names != expected:
        missing.append("sections")
    controls = applied.get("section_controls")
    wanted = [{key: value[key] for key in ("pageNum", "newNum")}
              for value in (_expected_page_numbers(index, section_formats)
                            for index in range(len(section_formats)))]
    if isinstance(controls, list):
        measured = [{key: (item.get(key) if isinstance(item, Mapping) else item)
                     for key in ("pageNum", "newNum")} for item in controls]
    else:
        measured = []
    if measured != wanted:
        missing.append("section_controls")
    if not applied.get("charpr"):
        missing.append("charpr")
    return missing


# --- 외부 프로세스 -------------------------------------------------------

# 외부 런타임에 넘길 수 있는 환경 변수의 허용목록. 프록시 자격증명·API 키·
# 세션 토큰은 넘기지 않는다.
_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "COLORTERM",
    "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "NO_COLOR", "CI", "KORDOC_OFFLINE",
})


def _executable_dir(value: str):
    candidate = Path(value)
    if candidate.is_file():
        return str(candidate.resolve().parent)
    found = shutil.which(value)
    return str(Path(found).resolve().parent) if found else None


def _env_for(first: str) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key in _ENV_ALLOWLIST}
    directory = _executable_dir(first)
    if directory:
        env["PATH"] = directory + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    env["KORDOC_OFFLINE"] = "1"
    return env


def _run(cmd, *, cwd=None, env=None, timeout=DEFAULT_TIMEOUT):
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "kind": "missing_executable", "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "kind": "timeout", "error": "timed out after %ss" % timeout}
    except OSError as exc:
        return {"ok": False, "kind": "os_error", "error": str(exc)}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[-16000:],
        "stderr": (completed.stderr or "")[-16000:],
    }


def validate_hwpx(cli_command, path, *, timeout=DEFAULT_TIMEOUT) -> dict:
    """kordoc validate 결과. validate 부명령이 없으면 status=unsupported."""
    command = list(cli_command)
    process = _run(command + ["validate", str(path), "--json"],
                   env=_env_for(command[0]), timeout=timeout)
    text = process.get("stdout") or ""
    report = {"status": "unsupported", "process_ok": process.get("ok"),
              "returncode": process.get("returncode")}
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and "ok" in parsed:
            report["status"] = "ok" if parsed.get("ok") else "failed"
            report["issues"] = parsed.get("issues", [])
            report["entry_count"] = parsed.get("entryCount")
    return report


def reparse_hwpx(cli_command, path, out_md, *, timeout=DEFAULT_TIMEOUT) -> dict:
    """HWPX를 다시 마크다운으로 읽어 표 셀 수치 대조에 쓴다."""
    command = list(cli_command)
    process = _run(
        command + [str(path), "-o", str(out_md), "--format", "markdown",
                   "--silent", "--keep-empty-cols", "--keep-empty-paragraphs"],
        env=_env_for(command[0]), timeout=timeout,
    )
    result = {"ok": bool(process.get("ok")), "process": process}
    if process.get("ok") and Path(out_md).is_file():
        result["markdown"] = Path(out_md).read_text(encoding="utf-8")
    return result


_NUMBER_RE = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


def _split_table_row(row: str):
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return body.split("|")


def table_numbers(text: str):
    """파이프 표에서 뽑은 (표, 행, 칸, 토큰) 목록(문서 순서).

    칸 좌표까지 남겨야 ``| x | 10 | y |`` → ``| x | y | 10 |``처럼 토큰 순서는
    같고 칸만 옮겨진 경우를 잡는다. 부호·소수점을 보존하고 천단위 쉼표만
    정규화한다(-100·100, 1.25·25.1을 서로 다르게 본다).
    """
    entries = []
    table = -1
    row = 0
    in_table = False
    for line in text.splitlines():
        if not _TABLE_ROW_RE.match(line):
            in_table = False
            continue
        if in_table:
            row += 1
        else:
            in_table = True
            table += 1
            row = 0
        for column, cell in enumerate(_split_table_row(line)):
            for match in _NUMBER_RE.finditer(cell):
                entries.append(
                    (table, row, column, match.group(0).replace(",", "")))
    return entries


def compare_table_numbers(source_text: str, parsed_text: str) -> dict:
    """원문 표 수치와 재파싱 표 수치를 (표, 행, 칸) 좌표까지 비교(부호·소수점 보존)."""
    expected = table_numbers(source_text)
    observed = table_numbers(parsed_text)
    if expected == observed:
        return {"match": True, "count": len(expected)}
    index = next((i for i, (a, b) in enumerate(zip(expected, observed)) if a != b),
                 min(len(expected), len(observed)))
    return {"match": False, "index": index,
            "expected_count": len(expected), "observed_count": len(observed),
            "expected": [list(item) for item in expected[index:index + 3]],
            "observed": [list(item) for item in observed[index:index + 3]]}


# CommonMark: 백슬래시는 뒤가 ASCII 문장부호일 때만 이스케이프다. 그 밖(글자 등)에서는
# 글자 그대로라서 Windows 경로(``figs\\a.png``)의 역슬래시를 지우면 안 된다.
_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

# 앱이 받은 그림의 손잡이(gg_figure._block_for). 파일 경로가 아니라 sha256이고,
# 나중에 gg_figure_docx.token_sha가 증명과 대조해 푼다 — 경로로 풀거나 존재를
# 검사하면 안 된다(토큰이라 늘 '없는 파일'이 되어 그 절이 통째로 빠진다).
_APP_FIGURE_TOKEN = re.compile(r"app-figure:[0-9a-f]{64}")

# HWPX는 앱 그림을 풀 수단이 없다 — gg_figure_docx가 DOCX 전용이기 때문이다.
# 조용히 빼면 그림이 빠진 채 발행되므로(A-01) 발행 경로는 이 문장으로 거절한다.
APP_FIGURE_HWPX_ACTION = (
    "앱에서 받은 그림은 아직 한글(HWPX) 파일에 넣지 못합니다 — "
    "Word(DOCX)로 만들어 주세요")


def is_app_figure_token(destination) -> bool:
    """목적지가 앱 그림 토큰(``app-figure:<sha256>``)인가.

    경로 표기와 똑같이 퍼센트 인코딩을 한 번 풀어서 본다(``%3A``도 ``:``).
    생산자(``gg_figure._block_for``)가 내는 정확한 꼴만 통과시킨다 — 앞뒤
    공백을 벗겨 인정하지 않으므로 ``< app-figure:… >`` 같은 비정규 표기는
    일반 경로로 남아 미해결이 된다(R-04). ``app-figure:``에 길이·글자가
    어긋나거나 다른 ``scheme:`` 꼴도 통과시키지 않는다.
    """
    if not isinstance(destination, str):
        return False
    return _APP_FIGURE_TOKEN.fullmatch(unquote(destination)) is not None


def _image_alt_end(text: str, start: int) -> int:
    """``![`` 뒤 대체텍스트의 닫는 ``]`` 위치(이스케이프 무시). 없으면 -1."""
    index = start
    length = len(text)
    while index < length:
        if text[index] == "\\" and index + 1 < length:
            index += 2
            continue
        if text[index] == "]":
            return index
        index += 1
    return -1


class _Destination(NamedTuple):
    """파싱한 그림 목적지 — 해석한 값과 원문에서 차지한 자리.

    ``start``·``end``는 원문 목적지 글자의 반열린 구간이다(``<``·``>``와
    앞뒤 공백은 뺀다). 해석값 ``raw``와 달리 원문 표기(이스케이프·퍼센트
    인코딩)를 그대로 담으므로, 목적지만 갈아 끼울 때 쓴다.
    """

    raw: str
    next_index: int
    start: int
    end: int


def _parse_image_destination(text: str, open_index: int):
    """``(`` 다음부터 목적지를 파싱한다(실패 시 None).

    ``<...>``로 감싼 목적지(공백 허용)·괄호 균형·뒤따르는 "제목" 문자열을
    이해한다. 백슬래시 이스케이프는 여기서 푼다. 성공하면 해석한 값과
    원문 구간(``start``·``end``)을 함께 내서 목적지만 다시 쓸 수 있게 한다.
    """
    length = len(text)
    index = open_index + 1
    while index < length and text[index] in " \t\n":
        index += 1
    angled = index < length and text[index] == "<"
    if angled:
        index += 1
    start = index
    if angled:
        chars = []
        while index < length and text[index] not in ">\n":
            if text[index] == "\\" and index + 1 < length and text[index + 1] in _ASCII_PUNCT:
                chars.append(text[index + 1])
                index += 2
                continue
            chars.append(text[index])
            index += 1
        if index >= length or text[index] != ">":
            return None
        end = index
        index += 1
        destination = "".join(chars)
    else:
        depth = 0
        chars = []
        while index < length:
            char = text[index]
            if char in " \t\n" and depth == 0:
                break
            if char == "\\" and index + 1 < length and text[index + 1] in _ASCII_PUNCT:
                chars.append(text[index + 1])
                index += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            chars.append(char)
            index += 1
        end = index
        destination = "".join(chars)
    while index < length and text[index] in " \t\n":
        index += 1
    if index < length and text[index] in "\"'(":
        closer = ")" if text[index] == "(" else text[index]
        index += 1
        while index < length and text[index] != closer:
            if text[index] == "\\" and index + 1 < length:
                index += 2
                continue
            index += 1
        if index >= length:
            return None
        index += 1
        while index < length and text[index] in " \t\n":
            index += 1
    if index >= length or text[index] != ")":
        return None
    return _Destination(destination, index + 1, start, end)


def _scan_images(text: str):
    """그림 참조마다 (``![`` 위치, 대체텍스트 끝 ``]`` 위치, 닫는 ``)`` 뒤 위치,
    해석된 경로)를 낸다."""
    for row in _scan_raw(text):
        if row[4]:
            continue
        yield row[0], row[1], row[2], unquote(row[3])


def _scan_raw(text: str):
    """그림 참조마다 원문 목적지와 그 자리까지 낸다(퍼센트 인코딩 풀기 전).

    ``(``![`` 위치, 대체텍스트 끝 ``]`` 위치, 닫는 ``)`` 뒤 위치, 해석된
    목적지, 깨짐 여부, 목적지 시작 위치, 목적지 끝 위치)``다. 목적지 자리는
    원문에서 ``<``·``>``와 앞뒤 공백을 뺀 반열린 구간이라, 목적지만 갈아
    끼워도 대체텍스트·제목·이스케이프가 그대로 남는다. 깨진 참조는
    ``![대체텍스트](``까지는 있으나 목적지를 해석할 수 없는 조각이다(학생
    원고 오류) — 두 위치는 None이다.
    """
    search = 0
    length = len(text)
    while True:
        open_at = text.find("![", search)
        if open_at == -1:
            return
        alt_end = _image_alt_end(text, open_at + 2)
        if (alt_end == -1 or alt_end + 1 >= length
                or text[alt_end + 1] != "("):
            search = open_at + 2
            continue
        parsed = _parse_image_destination(text, alt_end + 1)
        if parsed is None:
            yield open_at, alt_end, None, None, True, None, None
            search = open_at + 2
            continue
        yield (open_at, alt_end, parsed.next_index, parsed.raw, False,
               parsed.start, parsed.end)
        search = parsed.next_index


def find_images(text: str):
    """(그림 참조, 깨진 그림 참조). 마크다운 전문을 한 번만 스캔한다.

    그림 참조는 ``(``![`` 위치, 대체텍스트 끝 ``]`` 위치, 닫는 ``)`` 뒤 위치,
    목적지)``이고 목적지는 퍼센트 인코딩을 풀기 전 원문이다. 깨진 참조는
    ``![대체텍스트](``까지는 있으나 목적지를 해석할 수 없는 조각
    ``(``![`` 위치, 조각 끝 위치)``의 목록이다.
    """
    hits, broken = [], []
    for row in _scan_raw(text):
        if row[4]:
            end = text.find("\n", row[0])
            broken.append((row[0], len(text) if end == -1 else end))
            continue
        hits.append((row[0], row[1], row[2], row[3]))
    return hits, broken


def image_references(text: str):
    """마크다운 그림 참조(![대체텍스트](목적지))의 목적지 목록(문서 순서).

    ``<...>``로 감싼 목적지(공백 허용)와 뒤따르는 "제목" 문자열을 이해하고,
    백슬래시 이스케이프·퍼센트 인코딩을 경로 해석 전에 푼다(``farm%20photo.png``
    → ``farm photo.png``). 경계 판정은 ``resolve_images``가 그대로 한다.
    """
    return [path for _, _, _, path in _scan_images(text)]


def normalize_image_destinations(text: str) -> str:
    """kordoc에 넘기는 마크다운의 그림 목적지를 퍼센트 인코딩 한 가지 표기로 통일한다.

    우리 파서가 읽는 표기(``<...>``·이스케이프·제목)를 kordoc 생성기도 그림으로
    읽는다는 보장이 없어서(``<farm photo.png>``는 그림이 빠진 채 발행됐다), 경로
    해석에 쓴 값 그대로를 생성기가 읽는 단순 표기로 다시 쓴다.

    앱 그림 토큰(``app-figure:<sha256>``)은 경로가 아니므로 그대로 둔다 —
    ``:``를 퍼센트 인코딩하면 토큰이 깨져 나중에 풀 수 없다
    (``is_app_figure_token``).
    """
    out = []
    cursor = 0
    for open_at, alt_end, next_index, path in _scan_images(text):
        if is_app_figure_token(path):
            continue
        out.append(text[cursor:alt_end + 1])
        out.append("(" + quote(path, safe="/") + ")")
        cursor = next_index
    out.append(text[cursor:])
    return "".join(out)


def embedded_picture_count(data: bytes) -> int:
    """HWPX 본문 XML의 그림(``<hp:pic``) 개수."""
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        for info in zin.infolist():
            if SECTION_RE.fullmatch(info.filename):
                count += zin.read(info.filename).decode("utf-8").count("<hp:pic")
    return count


class _NotFound(NamedTuple):
    """그림 목적지를 풀지 못했다 — 없거나, 기준 폴더 밖이거나, 후보가 둘 이상."""

    reference: str
    candidates: list | None  # 뿌리 기준 상대 경로들, 원고 옆이 먼저. 모호할 때만 찬다.


def reference_search_roots(base, root):
    """그림 목적지를 풀 기준 폴더 순서. 원고 옆(base)이 먼저, 뿌리(root)가 다음.

    ``root``가 없으면 base 하나만 쓴다(명시한 ``--image-dir`` 하나로 푸는 기존
    동작). 파일이 실제로 있는지를 넘어 어디에 쓸 수 있는지까지 맞추려면 뿌리가
    필요하므로, 호출자는 뿌리 밖 후보를 거절한다.
    """
    roots = [Path(base).expanduser().resolve()]
    if root is not None:
        extra = Path(root).expanduser().resolve()
        if extra not in roots:
            roots.append(extra)
    return roots


def _resolve_image(reference, roots, root, *, decode=True):
    """목적지를 기준 폴더들 순서로 푼다. 경로 또는 ``_NotFound``를 낸다.

    ``decode``가 참이면 목적지의 퍼센트 인코딩을 먼저 푼다(원문 목적지를 그대로
    받는 HWPX 쪽). 원고 파서를 이미 지난 경로(``parse``가 ``unquote``한 값)는
    ``decode=False``로 넘긴다 — 두 번 풀면 ``%2541`` 같은 이름이 다른 파일이
    된다.

    뿌리 밖으로 풀린 후보는 없는 것으로 친다(DOCX 빌더와 같은 경계이며, 뿌리
    기준 상대 경로로 다시 쓸 수 있는 그림만 생성기에 넘길 수 있다). 둘 이상의
    서로 다른 파일이 걸리면 고르지 않고 후보 목록과 함께 ``_NotFound``다.
    """
    root = Path(root).expanduser().resolve()
    decoded = Path(unquote(reference) if decode else reference).expanduser()
    found = []
    for base in roots:
        candidate = decoded
        try:
            resolved = (candidate if candidate.is_absolute() else base / candidate)
            resolved = resolved.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        if resolved not in found:
            found.append(resolved)
    if not found:
        return _NotFound(reference, None)
    if len(found) > 1:
        return _NotFound(reference,
                         [path.relative_to(root).as_posix() for path in found])
    return found[0]


# 모호할 때(원고 옆·뿌리 모두에 같은 이름의 다른 그림) 학생에게 그대로 보여
# 주는 문장. HWPX 발행·DOCX 빌드·원고 검사가 **같은 말**을 쓴다 — 세 입구의
# 문구가 갈리면 같은 그림 문제에 학생이 다른 답을 받는다.
AMBIGUOUS_IMAGE_SENTENCE = (
    "같은 이름의 그림이 두 곳에 있어요: {paths} — 넣을 그림 하나만 남기거나 "
    "원고에 폴더까지 적어 주세요.")


def ambiguous_image_message(candidates):
    """모호 문장 하나 — 후보 경로(뿌리 기준, 원고 옆이 먼저)를 그대로 나열한다."""
    return AMBIGUOUS_IMAGE_SENTENCE.format(paths=", ".join(candidates))


def resolve_figure(reference, base, root=None, *, decode=False):
    """그림 목적지를 원고 옆(base) → 뿌리(root) 순서로 푼다.

    반환은 ``(경로 또는 None, 후보 목록 또는 None)`` — 후보가 둘 이상의 서로
    다른 파일이면 고르지 않고 ``(None, [뿌리 기준 상대 경로…])``이다. ``root``가
    없으면 base 하나만 보고 base가 경계다(지정한 폴더 하나로 푸는 기존 동작).

    ``reference``는 **이미 파싱된 경로**를 받는다(원고 파서가 ``unquote``한 값) —
    그래서 기본 ``decode=False``다. 원문 목적지를 그대로 넘기는 호출자는
    ``decode=True``로 퍼센트 인코딩을 먼저 푼다.

    ``decode=False``(원고 경로)에서는 절대 경로를 받지 않는다 — 원고에는 뿌리
    기준 상대 경로만 적을 수 있고(`gg_document.check`의 규칙), 다른 컴퓨터에서
    열리지 않는 경로를 조용히 DOCX에 박지 않는다.

    HWPX 발행(``rewrite_image_destinations``)·DOCX 빌드·원고 검사가 이 함수
    하나를 쓴다 — 세 입구가 같은 규칙으로 같은 그림을 찾는다(M-02).
    """
    if not decode and Path(reference).is_absolute():
        return None, None
    boundary = base if root is None else root
    found = _resolve_image(reference, reference_search_roots(base, root),
                           boundary, decode=decode)
    if isinstance(found, _NotFound):
        return None, found.candidates
    return found, None


def rewrite_image_destinations(text: str, base, root):
    """그림 목적지를 뿌리 기준 상대 경로(퍼센트 인코딩)로 다시 쓴다.

    목적지 기준 폴더는 두 단계다 — 원고 옆(base) 먼저, 없으면 뿌리(root).
    kordoc 변환본은 변환 파일 옆에 gg-assets 폴더를 만들고 그림 넣기
    (gg_figure)는 뿌리 기준으로 쓰므로, 둘 다 받아야 원고 위치에 따라 그림이
    사라지지 않는다. 두 기준 모두 유효하지만 다른 파일이면 고르지 않고 모호로
    남긴다.

    바꾸는 것은 목적지 글자뿐이다 — 대체텍스트·``<...>``·뒤따르는 제목·
    백슬래시 이스케이프는 원문 그대로 둔다(I-01). 반환은 (고친 본문,
    [(원래 목적지, 후보들)]). 후보들이 있으면 그 목적지는 그대로 두고 호출자가
    모호로 멈춘다. 후보가 없으면 없는 파일이다.

    앱 그림 토큰(``app-figure:<sha256>``)은 경로가 아니므로 손대지 않고 그대로
    지나간다 — 다시 쓰지도, 있는지 묻지도 않는다(``is_app_figure_token``).
    """
    root = Path(root).expanduser().resolve()
    roots = reference_search_roots(base, root)
    out, problems, cursor = [], [], 0
    for row in _scan_raw(text):
        open_at, alt_end, next_index, destination, is_broken, start, end = row
        if is_broken:
            problems.append((text[open_at:alt_end + 1], None))
            continue
        if is_app_figure_token(destination):
            continue
        resolved = _resolve_image(destination, roots, root)
        if isinstance(resolved, _NotFound):
            problems.append((resolved.reference, resolved.candidates))
            continue
        out.append(text[cursor:start])
        out.append(quote(resolved.relative_to(root).as_posix(), safe="/"))
        cursor = end
    out.append(text[cursor:])
    return "".join(out), problems


def resolve_images(text: str, image_dir, project_root=None):
    """(참조 목록, 미해결 목록). 미해결은 image_dir 밖을 가리키거나 없는 파일이다.

    DOCX 빌더가 작업 폴더 밖 그림 경로를 거부하는 것과 같은 경계를 쓴다.
    ``project_root``를 주면 원고 옆(image_dir)에서 먼저 찾고 그 뿌리에서 한 번
    더 찾는다 — 분할본이 쓰는 두 단계 규칙과 같다. 그래도 없거나 image_dir 밖,
    또는 같은 이름의 서로 다른 파일이면 미해결로 낸다.

    앱 그림 토큰(``app-figure:<sha256>``)은 HWPX가 풀 수 없다 — 앱 그림 해석은
    ``gg_figure_docx``(DOCX 전용)에만 있다. 그래서 토큰이 하나라도 있으면
    조용히 빼지 않고 ``HwpxBlocked('app_figure_unsupported')``로 거절한다.
    빼 버리면 ``build()``의 그림 개수 대조가 토큰 수만큼 비어도 통과해, 앱
    그림이 빠진 HWPX가 발행된다(A-01). 거절 문장과 토큰 줄을 함께 낸다.
    """
    base = Path(image_dir).expanduser().resolve()
    roots = reference_search_roots(base, project_root)
    boundary = base if project_root is None else roots[-1]
    references, missing, tokens = [], [], []
    for row in _scan_raw(text):
        if row[4]:
            continue
        if is_app_figure_token(row[3]):
            tokens.append((row[3], text.count("\n", 0, row[0]) + 1))
            continue
        reference = unquote(row[3])
        references.append(reference)
        if isinstance(_resolve_image(row[3], roots, boundary), _NotFound):
            missing.append(reference)
    if tokens:
        raise HwpxBlocked(
            "app_figure_unsupported",
            images=[token for token, _ in tokens],
            lines=[line for _, line in tokens],
            action=APP_FIGURE_HWPX_ACTION)
    return references, missing


def _ensure_kordoc(*, cache_dir, node, pnpm, kordoc, timeout):
    try:
        return gg_kordoc.ensure(cache_dir=cache_dir, node=node, pnpm=pnpm,
                                kordoc=kordoc, timeout=timeout,
                                need_generate=True)
    except gg_kordoc.KordocBlocked as exc:
        raise HwpxBlocked(exc.reason, **exc.details) from exc


def build(markdown, out, *, context, authorization, cli_command=None,
          cache_dir=None, node=None, pnpm=None, kordoc=None,
          timeout=DEFAULT_TIMEOUT, font=SCHOOL_FONT,
          margins_mm=SCHOOL_MARGINS_MM, page_numbers=True,
          section_formats=None, nodes=None, plan=None,
          generate_args=GENERATE_DEFAULT_ARGS, image_dir=None,
          verify=True, before_publish=None) -> dict:
    """마크다운 한 파일을 학교 규격 HWPX 한 파일로 만든다.

    ``context``와 ``authorization``은 필수다 — ``authorization``은 ``school_paper``
    출력 종류에 결속되며(다른 출력 종류의 유효한 권한으로는 발행하지 않는다), 발행
    직전에 ``gg_major_contract.reconfirm_output(authorization, context)``를 직접
    호출하므로 아무것도 하지 않는 콜백으로 우회할 수 없고, 위조된 권한은 정본과
    대조되어 거절된다. 필수로 둔 이유는 권한 없는 독립 발행 진입점을 없애기 위해서다.
    ``before_publish``는 권한 재확인 **뒤**에 도는 선택적 추가 훅일 뿐이다.

    cli_command(list[str])를 주면 그대로 쓰고, 없으면 gg_kordoc.ensure()로 kordoc
    실행 명령을 얻는다(need_generate=True로 generate 능력을 함께 확인). 출력은 배타
    발행으로 덮어쓰지 않는다. ``image_dir``을 주지 않으면 작업 폴더
    (``context.project_root``)를 쓴다. 그림 목적지는 원고 옆에서 먼저 찾고
    없으면 그 폴더에서 찾은 뒤(분할본과 같은 두 단계 규칙) 작업 폴더 기준
    상대 경로로 다시 써서 kordoc에 넘긴다 — 생성기에 ``--image-dir``은 하나만
    준다. 앱 그림 토큰(``app-figure:<sha256>``)은 HWPX가 풀 수 없으므로
    (``gg_figure_docx``는 DOCX 전용) 생성기를 부르기 전에
    ``app_figure_unsupported``로 거절한다 — 그림이 빠진 HWPX를 발행하지 않는다.
    ``verify=True``면 구조 검증 프로세스가 성공하고 결과가 'ok'이고,
    재파싱이 성공하고, 표 대조가 일치할 때만 발행한다.
    """
    import gg_major_contract as mc

    if not isinstance(authorization, mc.OutputAuthorization):
        raise HwpxBlocked(
            "authorization_required",
            action="학교 논문 HWPX 발행에는 gg_major_contract 권한 객체가 필요합니다.")
    if authorization.output != mc.OUTPUT_SCHOOL_PAPER:
        raise HwpxBlocked(
            "authorization_required", output=authorization.output,
            expected=mc.OUTPUT_SCHOOL_PAPER,
            action="학교 논문 HWPX 발행에는 school_paper 출력 권한이 필요합니다.")
    source = Path(markdown).expanduser()
    if not source.is_file():
        raise HwpxBlocked("input_missing", input=str(source),
                          action="존재하는 마크다운 파일 경로를 주세요.")
    source = source.resolve()
    target = Path(out).expanduser().resolve()
    if source == target:
        raise HwpxBlocked("input_output_same", input=str(source), output=str(target),
                          action="입력과 다른 새 출력 경로를 지정하세요.")
    if target.exists():
        raise HwpxBlocked("output_exists", output=str(target),
                          action="기존 파일을 덮어쓰지 않습니다. 새 경로를 지정하세요.")
    project_root = (context.get("project_root")
                    if isinstance(context, dict)
                    else getattr(context, "project_root", None))
    if image_dir is None and project_root is None:
        raise HwpxBlocked(
            "output_context_required",
            action="그림 기준 폴더를 정하려면 context.project_root 또는 "
                   "--image-dir가 필요합니다.")
    image_dir = (Path(image_dir).expanduser().resolve()
                 if image_dir is not None
                 else Path(project_root).expanduser().resolve())

    source_bytes = source.read_bytes()
    input_sha = hashlib.sha256(source_bytes).hexdigest()
    text = normalize_markdown(source_bytes)
    text, problems = rewrite_image_destinations(text, source.parent, image_dir)
    ambiguous = [row for row in problems if row[1]]
    if ambiguous:
        raise HwpxBlocked(
            "image_ambiguous", images=[path for path, _ in ambiguous],
            candidates=ambiguous[0][1], image_dir=str(image_dir),
            action=ambiguous_image_message(ambiguous[0][1]))
    if problems:
        raise HwpxBlocked(
            "image_unresolved", images=[path for path, _ in problems],
            image_dir=str(image_dir),
            action="입력이 가리키는 그림을 원고 옆이나 이미지 폴더 안에서 찾지 "
                   "못했습니다. 그림 경로를 고치거나 --image-dir를 지정하세요.")
    references, unresolved = resolve_images(text, image_dir)
    if unresolved:
        raise HwpxBlocked(
            "image_unresolved", images=unresolved, image_dir=str(image_dir),
            action="다시 쓴 그림 경로를 이미지 폴더 안에서 찾지 못했습니다.")

    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".gg-hwpx-", dir=str(target.parent)))
    try:
        md_path = work / "input.md"
        md_path.write_text(normalize_image_destinations(text), encoding="utf-8",
                          newline="\n")
        raw_path = work / "raw.hwpx"

        command = None
        runtime_info = None
        if cli_command is not None:
            command = [str(part) for part in cli_command]
        if command:
            runtime_info = {"cli_command": command, "version": None,
                            "source": "injected"}
        else:
            ready = _ensure_kordoc(cache_dir=cache_dir, node=node, pnpm=pnpm,
                                   kordoc=kordoc, timeout=timeout)
            command = [str(part) for part in ready["cli_command"]]
            runtime_info = {
                "cli_command": command,
                "version": ready.get("actual_version") or ready.get("version"),
                "source": ready.get("source") or ready.get("action"),
            }

        options = list(generate_args)
        if "--silent" not in options:
            options.append("--silent")
        if "--image-dir" not in options:
            options += ["--image-dir", str(image_dir)]
        generate_cmd = command + ["generate", *options, "-o", str(raw_path), str(md_path)]
        process = _run(generate_cmd, cwd=work, env=_env_for(command[0]), timeout=timeout)
        if not process.get("ok"):
            raise HwpxBlocked("generate_failed", command=generate_cmd, process=process,
                              action="입력은 보존됐고 아무것도 발행하지 않았습니다.")
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            raise HwpxBlocked("empty_output",
                              action="kordoc가 HWPX를 만들지 않았습니다. 발행하지 않았습니다.")

        if section_formats is not None and (nodes is None or plan is None):
            import gg_document as gd

            major_id = authorization.major_id
            nodes = gd.parse(text, major_id=major_id)
            plan = gd.school_frontmatter_plan(nodes, major_id)

        transformed, applied = transform_hwpx(
            raw_path.read_bytes(), font=font, margins_mm=margins_mm,
            page_numbers=page_numbers, section_formats=section_formats,
            nodes=nodes, plan=plan, major_id=authorization.major_id,
        )
        pictures = embedded_picture_count(transformed)
        if pictures < len(references):
            raise HwpxBlocked(
                "images_not_embedded", referenced=len(references), embedded=pictures,
                action="원문의 그림 표기 일부가 HWPX에 그림으로 들어가지 않았습니다. "
                       "발행하지 않았습니다. 그림 경로 표기를 단순하게 고쳐 보세요.")
        missing = missing_format_steps(
            applied, font=font, margins_mm=margins_mm,
            page_numbers=page_numbers, section_formats=section_formats)
        if missing:
            raise HwpxBlocked(
                "format_not_applied", missing=missing, applied=applied,
                action="kordoc 산출물에 요청한 서식(여백·글꼴·쪽번호)을 적용하지 "
                       "못했습니다. HWPX 구조를 확인하세요. 발행하지 않았습니다.")

        verification = {"validate": None, "reparse": None, "table_numbers": None}
        if verify:
            staged = work / "staged.hwpx"
            staged.write_bytes(transformed)
            validation = validate_hwpx(command, staged, timeout=timeout)
            verification["validate"] = validation
            if not (validation.get("process_ok") is True
                    and validation.get("returncode") == 0
                    and validation.get("status") == "ok"):
                raise HwpxBlocked(
                    "validate_failed", status=validation.get("status"),
                    process_ok=validation.get("process_ok"),
                    returncode=validation.get("returncode"),
                    issues=validation.get("issues"),
                    action="구조 검증(프로세스 성공 + ok)이 아니어서 발행하지 않았습니다.")
            back_md = work / "back.md"
            reparsed = reparse_hwpx(command, staged, back_md, timeout=timeout)
            reparsed_ok = bool(reparsed.get("ok") and "markdown" in reparsed)
            verification["reparse"] = {"ok": reparsed_ok}
            if not reparsed_ok:
                raise HwpxBlocked("reparse_failed",
                                  action="재파싱 대조가 불가해 발행하지 않았습니다.")
            comparison = compare_table_numbers(text, reparsed["markdown"])
            verification["table_numbers"] = comparison
            if not comparison["match"]:
                raise HwpxBlocked("table_numbers_mismatch", **comparison,
                                  action="표 셀 수치가 원문과 달라 발행하지 않았습니다.")

        if hashlib.sha256(source.read_bytes()).hexdigest() != input_sha:
            raise HwpxBlocked("input_changed",
                              action="발행 직전에 입력이 바뀌었습니다. 다시 시도하세요.")
        mc.reconfirm_output(authorization, context)
        if before_publish is not None:
            before_publish()

        parent_identity = gg_fs.identity(target.parent, kind="directory")
        published = gg_fs.publish_bytes_noreplace(
            target, transformed, parent_identity=parent_identity
        )
        if published.get("state") != "installed":
            raise HwpxBlocked("output_publish_failed",
                              publish_reason=published.get("reason"),
                              output=str(target),
                              action="기존 파일·입력은 보존됐습니다. 새 경로로 다시 시도하세요.")
        output_bytes = target.read_bytes()
        return _result(
            "ready",
            action="generated",
            input=str(source),
            output=str(target),
            bytes=len(output_bytes),
            input_sha256=input_sha,
            output_sha256=hashlib.sha256(output_bytes).hexdigest(),
            applied=applied,
            font=font,
            margins_mm=dict(margins_mm) if margins_mm is not None else None,
            page_numbers=bool(page_numbers),
            images={"dir": str(image_dir), "referenced": references},
            verification=verification,
            runtime=runtime_info,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
