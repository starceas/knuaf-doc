#!/usr/bin/env python3
"""식량작물 HWPX 쪽별 서식 기계 검사기 (설계 §6.2).

측정만 하고 파일은 절대 고치지 않는다. 구역 3개(겉표지/앞부분/본문)의 여백·
제어 요소·쪽번호·글자 모양·manifest 참조 무결성을 ZIP 안의 XML에서 읽어
`food_crops.format.*` 위반으로 낸다.

기대값은 여기에 **직접 적는다**. 빌더(`gg_hwpx`)의 표를 import 하면 표가
틀렸을 때 검사기도 같이 틀려 아무것도 못 잡는다. 그래서 `gg_hwpx`·
`gg_kordoc`을 import 하지 않고, 외부 프로세스(kordoc)도 이 파일이 직접
띄운다.

파일이 없거나 XML이 깨졌으면 예외를 올리지 않고 `blocked` 결과를 낸다.

사용:
    python3 -B gg_school_format_hwpx.py out.hwpx --out result.json \\
        [--kordoc /path/to/kordoc]

`--kordoc`이 없으면 `structure`(kordoc validate)·`reparse`(재파싱 대조)는
`skipped`로 기록하고 나머지만 본다. 반대로 `--kordoc`을 지정했는데 그 명령을
실행할 수 없으면(파일 없음·타임아웃·OSError) 두 검사 모두 `failed`와 위반으로
닫는다 — 검사를 요청한 산출물을 판정 없이 통과시키지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

SECTION_NAMES = ("Contents/section0.xml", "Contents/section1.xml",
                 "Contents/section2.xml")
HEADER_NAME = "Contents/header.xml"
MANIFEST_NAME = "Contents/content.hpf"
# A4 210x297 mm를 HWPUNIT으로 옮긴 값(양식 원본 hp:pagePr과 같다).
# kordoc은 만드는 과정에서 폭을 1 HWPUNIT 깎아 59527로 낸다. 1판 산출물도
# 59527이고 양식 원본만 59528이므로, 두 값을 같은 A4로 본다(높이는 같다).
A4_PAGE = {"width": "59528", "height": "84188"}
A4_PAGE_WIDTHS = ("59527", "59528")

# 설계 §2.1의 구역별 여백. 양식 원본은 2834/8503을 쓰지만 빌더는 mm를
# HWPUNIT으로 반올림해 2835/8504를 낸다(`mm_to_hwpunit`). 검사기는 빌더
# 산출물을 재는 도구이므로 빌더 값으로 잡고, 1단위 차이는 주석으로 남긴다.
MARGIN_KEYS = ("header", "footer", "gutter", "left", "right", "top", "bottom")
EXPECTED_MARGINS = (
    {"header": "0", "footer": "2835", "gutter": "0", "left": "5669",
     "right": "5669", "top": "5669", "bottom": "2835"},
    {"header": "0", "footer": "2835", "gutter": "0", "left": "8504",
     "right": "8504", "top": "8504", "bottom": "8504"},
    {"header": "0", "footer": "2835", "gutter": "0", "left": "8504",
     "right": "8504", "top": "8504", "bottom": "8504"},
)
ROLES = ("cover", "front", "body")

# 설계 §2.2: 겉표지는 번호를 넣지 않고(0개), 앞부분은 로마 대문자, 본문은
# 아라비아. `startNum`이 아니라 첫 문단의 `newNum`으로 재시작한다.
EXPECTED_PAGE_NUM = (
    None,
    {"pos": "BOTTOM_CENTER", "formatType": "ROMAN_CAPITAL"},
    {"pos": "BOTTOM_CENTER", "formatType": "DIGIT"},
)

# 설계 §4.3이 header.xml 끝에 덧붙이는 (포인트, 굵기) 14개. id는 기존
# itemCnt부터 이 순서로 이어 붙으므로, 꼬리 14개가 이 서명과 일치하면 그
# 구간이 '앞부분 전용' charPr 블록이다. 이 id가 본문 문단에 나타나면
# 앞부분 서식이 본문으로 샌 것이다(본문 불변 검사).
FRONT_CHARPR_SIGNATURE = (
    (10, False), (12, False), (14, True), (14, False), (16, True), (16, False),
    (18, True), (18, False), (20, True), (20, False), (22, True), (22, False),
    (32, True), (32, False),
)

MARGIN_RELATED = {"header": "OG-008", "footer": "OG-009", "left": "OG-010",
                  "right": "OG-011", "top": "OG-006", "bottom": "OG-007",
                  "gutter": "OG-012"}
PAGE_NUMBER_RELATED = ("OG-125",)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node, local):
    return [child for child in list(node) if _local(child.tag) == local]


def _first(node, local):
    found = _children(node, local)
    return found[0] if found else None


def _text(node) -> str:
    return " ".join("".join(t.text or "" for t in node.iter("{%s}t" % HP)).split())


def _int_attr(node, name, default=None):
    try:
        return int(node.attrib.get(name, default))
    except (TypeError, ValueError):
        return default


class Measurement:
    """한 HWPX 파일에서 읽은 값 묶음(JSON 직렬화 가능한 것만)."""

    def __init__(self):
        self.sections = []          # [{role, name, paragraphs, texts, ...}]
        self.sec_cnt = None
        self.page_size = None
        self.charprs = {}           # id(str) -> {"height": pt, "bold": bool}
        self.front_charpr_ids = set()
        self.manifest = None
        self.names = []
        self.notes = []


def _read_section(archive, name, role):
    """구역 하나를 재서 dict로 낸다. XML이 깨지면 None."""
    try:
        root = ET.fromstring(archive.read(name))
    except (ET.ParseError, KeyError):
        return None
    paragraphs = _children(root, "p")
    first = _first(paragraphs[0], "run") if paragraphs else None
    page_pr = next((node for node in root.iter() if _local(node.tag) == "pagePr"), None)
    margin = next((node for node in root.iter() if _local(node.tag) == "margin"), None)
    # hp:ctrl은 hp:p 바로 밑이 아니라 그 안의 hp:run 밑에 온다(HPML 2011).
    controls = {}
    for ctrl in root.iter():
        if _local(ctrl.tag) != "ctrl":
            continue
        for child in list(ctrl):
            controls[_local(child.tag)] = controls.get(_local(child.tag), 0) + 1
    first_run_children = [] if first is None else [_local(child.tag) for child in list(first)]
    page_num = [child for child in root.iter() if _local(child.tag) == "pageNum"]
    new_num = [child for child in root.iter() if _local(child.tag) == "newNum"]
    return {
        "role": role,
        "name": name,
        "_paragraph_nodes": paragraphs,
        "paragraphs": len(paragraphs),
        "texts": [_text(paragraph) for paragraph in paragraphs],
        "first_run_children": first_run_children,
        "has_first_run": first is not None,
        "sec_pr": len([n for n in root.iter() if _local(n.tag) == "secPr"]),
        "col_pr": len([n for n in root.iter() if _local(n.tag) == "colPr"]),
        "page_num": [dict(n.attrib) for n in page_num],
        "new_num": [dict(n.attrib) for n in new_num],
        "controls": controls,
        "margin": None if margin is None else dict(margin.attrib),
        "page_size": None if page_pr is None else {
            "width": page_pr.attrib.get("width"), "height": page_pr.attrib.get("height")},
    }


def _read_charprs(archive):
    try:
        root = ET.fromstring(archive.read(HEADER_NAME))
    except (ET.ParseError, KeyError):
        return None, None
    properties = next((node for node in root.iter()
                       if _local(node.tag) == "charProperties"), None)
    if properties is None:
        return None, root.attrib.get("secCnt")
    charprs = {}
    for node in _children(properties, "charPr"):
        bold_tag = any(_local(child.tag) in ("bold", "charShape") for child in list(node))
        charprs[str(node.attrib.get("id"))] = {
            "height": _int_attr(node, "height"),
            "bold": node.attrib.get("bold") == "1" or bold_tag,
        }
    return charprs, root.attrib.get("secCnt")


def _read_manifest(archive, names):
    try:
        root = ET.fromstring(archive.read(MANIFEST_NAME))
    except (ET.ParseError, KeyError):
        return None
    items = [dict(node.attrib) for node in root.iter() if _local(node.tag) == "item"]
    itemrefs = [dict(node.attrib) for node in root.iter() if _local(node.tag) == "itemref"]
    ids = [item.get("id") for item in items]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    return {
        "item_count": len(items),
        "itemref_count": len(itemrefs),
        "duplicate_ids": duplicates,
        "unresolved_idrefs": sorted({ref.get("idref") for ref in itemrefs}
                                    - set(ids)),
        "missing_hrefs": sorted({item.get("href") for item in items
                                 if item.get("href") not in names}),
        "spine_sections": [ref.get("idref") for ref in itemrefs
                           if str(ref.get("idref", "")).startswith("section")],
        "manifested": {item.get("href") for item in items},
    }


def _appended_charpr_ids(charprs):
    """header.xml 꼬리 14개가 앞부분 전용 블록인지 확인하고 그 id 집합을 낸다."""
    ordered = sorted(charprs, key=lambda value: int(value) if value.isdigit() else 1 << 30)
    tail = ordered[-len(FRONT_CHARPR_SIGNATURE):]
    # header.xml의 height는 1/100 pt 단위(1000 = 10 pt)이고 기대표는 pt다.
    signature = [((charprs[cid]["height"] or 0) // 100, charprs[cid]["bold"])
                 for cid in tail]
    if signature != list(FRONT_CHARPR_SIGNATURE):
        return set()
    return set(tail)


def measure(path: Path):
    """파일 하나를 재서 (Measurement, None) 또는 (None, 이유)를 낸다."""
    measured = Measurement()
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return None, f"ZIP을 열 수 없음: {exc}"
    with archive:
        # ZIP 항목 자체가 손상되면(CRC 불일치 등) archive.read()가 BadZipFile을
        # 낸다. 일부만 읽고 통과시키지 않고 측정 불가로 닫아 package 차단으로 보낸다.
        try:
            names = set(archive.namelist())
            measured.names = sorted(names)
            missing = [name for name in SECTION_NAMES if name not in names]
            if missing:
                measured.notes.append({"missing_sections": missing})
            for name, role in zip(SECTION_NAMES, ROLES):
                if name not in names:
                    continue
                record = _read_section(archive, name, role)
                if record is None:
                    measured.notes.append({"invalid_xml": name})
                    continue
                measured.sections.append(record)
            charprs, sec_cnt = _read_charprs(archive)
            if charprs is None:
                measured.notes.append({"invalid_xml": HEADER_NAME})
            else:
                measured.charprs = charprs
                measured.front_charpr_ids = _appended_charpr_ids(charprs)
            measured.sec_cnt = sec_cnt
            measured.manifest = _read_manifest(archive, names)
            if measured.manifest is None:
                measured.notes.append({"invalid_xml": MANIFEST_NAME})
            measured.version_xml_present = "version.xml" in names
        except zipfile.BadZipFile as exc:
            return None, f"ZIP 항목을 읽을 수 없음: {exc}"
    return measured, None


class _Violations:
    def __init__(self):
        self.items = []

    def bad(self, code, related, location, expected, actual, message):
        self.items.append({
            "requirement_id": code, "code": code, "location": location,
            "expected": expected, "actual": actual, "message": message,
            "related": list(related), "probe_local": True,
        })


def _check_sections(measured, violations):
    found = [section["name"] for section in measured.sections]
    if found != list(SECTION_NAMES):
        violations.bad(
            "food_crops.format.section.count", (), "Contents/section*.xml",
            list(SECTION_NAMES), found,
            "HWPX 구역 파일이 겉표지·앞부분·본문 3개가 아님")
    if measured.sec_cnt != "3":
        violations.bad(
            "food_crops.format.section.count", (), HEADER_NAME + ":hh:head@secCnt",
            "3", measured.sec_cnt, "header.xml의 구역 수(secCnt)가 3이 아님")

    for section, expected_margin, expected_number in zip(
            measured.sections, EXPECTED_MARGINS, EXPECTED_PAGE_NUM):
        role = section["role"]
        place = "Contents/section%d.xml" % ROLES.index(role)
        if section["sec_pr"] != 1 or section["col_pr"] != 1:
            violations.bad(
                "food_crops.format.section.controls", (), place,
                {"secPr": 1, "colPr": 1},
                {"secPr": section["sec_pr"], "colPr": section["col_pr"]},
                "구역마다 secPr·colPr 제어 요소가 정확히 1개여야 함")
        prefix = section["first_run_children"][:3]
        if not section["has_first_run"] or prefix[:2] != ["secPr", "ctrl"]:
            violations.bad(
                "food_crops.format.section.controls", (), place + ":첫 문단 첫 run",
                ["secPr", "ctrl"], prefix,
                "secPr·colPr가 구역 첫 최상위 문단 첫 run 안에 그 순서로 있어야 함")
        elif len(prefix) == 3 and expected_number is not None and prefix[2] != "ctrl":
            violations.bad(
                "food_crops.format.section.controls", (), place + ":첫 문단 첫 run",
                ["secPr", "ctrl", "ctrl"], prefix,
                "pageNum 제어 요소가 첫 run 안 secPr·colPr 뒤에 있어야 함")

        if section["margin"] is None:
            violations.bad(
                "food_crops.format.section.margin." + role, (), place + ":hp:margin",
                expected_margin, None, "구역에 hp:margin 요소가 없음")
        else:
            for key in MARGIN_KEYS:
                got = section["margin"].get(key)
                if got != expected_margin[key]:
                    violations.bad(
                        "food_crops.format.section.margin.%s_%s" % (role, key),
                        [MARGIN_RELATED[key]], place + ":hp:margin@" + key,
                        expected_margin[key], got,
                        "%s 구역 %s 여백이 양식 값과 다름" % (role, key))
        page_size = section["page_size"]
        if page_size is None or page_size.get("width") not in A4_PAGE_WIDTHS \
                or page_size.get("height") != A4_PAGE["height"]:
            violations.bad(
                "food_crops.format.section.margin." + role, (), place + ":hp:pagePr",
                A4_PAGE, page_size,
                "용지 크기가 A4(59527~59528 x 84188 HWPUNIT)가 아님")


def _check_page_numbers(measured, violations):
    counts = [len(section["page_num"]) for section in measured.sections]
    if counts != [0, 1, 1]:
        violations.bad(
            "food_crops.format.page_number.count", PAGE_NUMBER_RELATED,
            "Contents/section*.xml", [0, 1, 1], counts,
            "구역별 pageNum 제어 요소 개수가 cover 0 / front 1 / body 1이 아님")
    if measured.sections and len(measured.sections[0]["page_num"]) != 0:
        violations.bad(
            "food_crops.format.page_number.cover", PAGE_NUMBER_RELATED,
            "Contents/section0.xml", 0, len(measured.sections[0]["page_num"]),
            "겉표지 구역에는 pageNum 제어 요소가 없어야 함")
    for index, role in ((1, "front"), (2, "body")):
        if index >= len(measured.sections):
            continue
        section = measured.sections[index]
        found = section["page_num"]
        wanted = EXPECTED_PAGE_NUM[index]
        place = section["name"]
        if len(found) != 1:
            violations.bad(
                "food_crops.format.page_number." + role, PAGE_NUMBER_RELATED, place,
                dict(wanted, count=1),
                {"count": len(found), "page_numbers": found},
                "%s 구역 pageNum이 정확히 1개여야 함" % role)
            continue
        got = {"pos": found[0].get("pos"), "formatType": found[0].get("formatType")}
        if got != wanted:
            violations.bad(
                "food_crops.format.page_number." + role, PAGE_NUMBER_RELATED, place,
                dict(wanted, count=1), dict(got, count=1),
                "%s 구역 pageNum의 pos·formatType이 양식 값과 다름" % role)
        if found[0].get("sideChar") != "-":
            violations.bad(
                "food_crops.format.page_number." + role, PAGE_NUMBER_RELATED, place,
                {"sideChar": "-"}, {"sideChar": found[0].get("sideChar")},
                "pageNum의 sideChar가 '-'가 아님")

    # 재시작: 구역 첫 문단 첫 run 안, hp:ctrl 하위, hp:t 뒤.
    for index, role in ((1, "front"), (2, "body")):
        if index >= len(measured.sections):
            continue
        section = measured.sections[index]
        code = "food_crops.format.page_number.%s_restart" % role
        found = section["new_num"]
        if len(found) != 1:
            violations.bad(
                code, PAGE_NUMBER_RELATED, section["name"], {"count": 1},
                {"count": len(found), "new_num": found},
                "%s 구역 newNum 제어 요소가 정확히 1개여야 함" % role)
            continue
        if found[0].get("num") != "1" or found[0].get("numType") != "PAGE":
            violations.bad(
                code, PAGE_NUMBER_RELATED, section["name"],
                {"num": "1", "numType": "PAGE"}, dict(found[0]),
                "newNum의 num·numType이 1·PAGE가 아님")
        children = section["first_run_children"]
        if "ctrl" not in children or "secPr" not in children:
            violations.bad(
                code, PAGE_NUMBER_RELATED, section["name"] + ":첫 run",
                {"first_run_children": ["secPr", "ctrl", "ctrl", "t", "ctrl"]},
                {"first_run_children": children},
                "newNum이 구역 첫 문단 첫 run 안에 없음")
        else:
            # 첫 run의 마지막 ctrl이 newNum이고, 그 앞에 hp:t가 있어야 한다.
            tail = children[children.index("t") + 1:] if "t" in children else []
            if "t" not in children or tail[-1:] != ["ctrl"]:
                violations.bad(
                    code, PAGE_NUMBER_RELATED, section["name"] + ":첫 run",
                    {"order": "… <hp:t>…</hp:t> <hp:ctrl>newNum</hp:ctrl>"},
                    {"first_run_children": children},
                    "newNum이 hp:t 뒤에 <hp:ctrl>로 감싸여 오지 않음")

    # 중복: newNum·pageNum은 front·body 첫 문단 두 곳뿐이어야 한다.
    new_num_places, page_num_places = [], []
    for index, section in enumerate(measured.sections):
        if section["new_num"]:
            new_num_places.append(index)
        if section["page_num"]:
            page_num_places.append(index)
    if new_num_places != [1, 2] or page_num_places != [1, 2]:
        violations.bad(
            "food_crops.format.page_number.duplicate", PAGE_NUMBER_RELATED,
            "Contents/section*.xml",
            {"newNum_sections": [1, 2], "pageNum_sections": [1, 2]},
            {"newNum_sections": new_num_places, "pageNum_sections": page_num_places},
            "newNum·pageNum이 앞부분·본문 첫 문단 두 곳 밖에도 있음")
    for index, section in ((1, measured.sections[1] if len(measured.sections) > 1 else None),
                           (2, measured.sections[2] if len(measured.sections) > 2 else None)):
        if section is None:
            continue
        controls = section["controls"]
        if controls.get("newNum", 0) != 1 or controls.get("pageNum", 0) != 1:
            violations.bad(
                "food_crops.format.page_number.duplicate", PAGE_NUMBER_RELATED,
                section["name"],
                {"newNum": 1, "pageNum": 1},
                {"newNum": controls.get("newNum", 0),
                 "pageNum": controls.get("pageNum", 0)},
                "구역 안 newNum·pageNum 제어 요소 개수가 1개씩이 아님")


def _check_charpr(measured, violations):
    sizes = {cid: (value["height"] // 100, value["bold"])
             for cid, value in measured.charprs.items()
             if value["height"] is not None}
    if not sizes:
        violations.bad(
            "food_crops.format.charpr.front", (), HEADER_NAME,
            {"charPr": "height 속성이 있는 charPr"}, None,
            "header.xml에서 글자 크기를 읽지 못해 글자 모양을 판정할 수 없음")
        return

    def run_refs(section):
        if "_references" in section:
            return section["_references"]
        refs = {}
        for index, paragraph in enumerate(section["_paragraph_nodes"]):
            values = set()
            for run in _children(paragraph, "run"):
                cid = run.attrib.get("charPrIDRef")
                if cid is not None:
                    values.add(cid)
            refs[index] = values
        section["_references"] = refs
        return refs

    def report(code, section, index, text, wanted, related=()):
        refs = run_refs(section).get(index, set())
        found = {sizes.get(cid) for cid in refs}
        if wanted in found:
            return
        actual = sorted(({"size": size, "bold": bold, "charPrIDRef": cid}
                         for cid in refs
                         for size, bold in [sizes.get(cid, (None, False))]),
                        key=lambda value: (value["size"] or 0, value["bold"]))
        violations.bad(code, related,
                       "%s:%d" % (section["name"], index),
                       {"size": wanted[0], "bold": wanted[1]}, actual,
                       "`%s` 문단 글자 모양이 %d pt %s이어야 함" % (
                           text[:20], wanted[0], "굵게" if wanted[1] else "보통"))

    def index_of(section, predicate):
        for index, text in enumerate(section["texts"]):
            if text and predicate(text):
                return index
        return None

    if len(measured.sections) != 3:
        return
    cover, front, body = measured.sections

    # 겉표지: 학위논문 18 굵게, 그다음 계획 제목 32 굵게, 연도 20 보통.
    degree = index_of(cover, lambda text: text.endswith("학위논문"))
    if degree is None:
        violations.bad("food_crops.format.charpr.front", (), cover["name"],
                       "`…학위논문` 문단", None,
                       "겉표지에서 학위논문 표기 문단을 찾지 못함")
    else:
        report("food_crops.format.charpr.front", cover, degree,
               cover["texts"][degree], (18, True))
        plan_at = next((index for index in range(degree + 1, len(cover["texts"]))
                        if cover["texts"][index]), None)
        if plan_at is None:
            violations.bad("food_crops.format.charpr.front", (), cover["name"],
                           "계획 제목 문단", None,
                           "겉표지에서 계획 제목 문단을 찾지 못함")
        else:
            report("food_crops.format.charpr.front", cover, plan_at,
                   cover["texts"][plan_at], (32, True))
    # 연도 문단은 앞부분 날짜가 채워졌을 때만 생긴다. 날짜가 비어 있으면
    # 겉표지에 `[확인 필요]` 자리표시자가 오므로 연도 문단 부재는 위반이 아니다.
    year = index_of(cover, lambda text: text[:4].isdigit() and "년" in text[:12])
    if year is not None:
        report("food_crops.format.charpr.front", cover, year,
               cover["texts"][year], (20, False))

    # 앞부분: 요약 22 굵게 / 소제목 16 굵게 / 요약 본문 12 보통 /
    # 감사의 글 20 굵게 / 목차 22 굵게 / 표·그림 목차 20 보통.
    summary = index_of(front, lambda text: text == "요약")
    thanks = index_of(front, lambda text: text.startswith("감사의 글"))
    contents = index_of(front, lambda text: text.replace(" ", "") == "목차")
    for index, wanted, label in (
            (summary, (22, True), "요약"),
            (thanks, (20, True), "감사의 글"),
            (contents, (22, True), "목차")):
        if index is None:
            violations.bad("food_crops.format.charpr.front", (), front["name"],
                           "`%s` 문단" % label, None,
                           "앞부분에서 `%s` 문단을 찾지 못함" % label)
        else:
            report("food_crops.format.charpr.front", front, index,
                   front["texts"][index], wanted)
    # 번호형 소제목(`1. 농장 개요`)은 요약 구간에만 16 pt 굵게다. 목차 항목도
    # `1. 서론 …… 1`처럼 숫자와 마침표로 시작하지만 12 pt 보통이므로, 요약
    # 경계(요약 다음 ~ 감사의 글/목차 앞) 안에서만 번호형 규칙을 적용한다.
    # 생성기 `_front_charpr_targets`가 bounds["요약"]으로 자르는 것과 같은 경계다.
    summary_end = min([value for value in (thanks, contents) if value is not None],
                      default=len(front["texts"]))
    for index, text in enumerate(front["texts"]):
        if not text or index in (summary, thanks, contents):
            continue
        in_summary = summary is not None and summary < index < summary_end
        if text.replace(" ", "") in ("표목차", "그림목차"):
            report("food_crops.format.charpr.front", front, index, text, (20, False))
        elif in_summary and text[:1].isdigit() and "." in text[:4]:
            report("food_crops.format.charpr.front", front, index, text, (16, True))
        else:
            report("food_crops.format.charpr.front", front, index, text, (12, False))

    # 본문 불변: 앞부분 전용 charPr가 본문(동의서 제외) 문단에 새어들면 안 된다.
    consent = index_of(body, lambda text: text == "학부모 동의서")
    if not measured.front_charpr_ids:
        violations.bad(
            "food_crops.format.charpr.body_invariant", (), HEADER_NAME,
            {"trailing_charpr_signature": [list(spec) for spec in FRONT_CHARPR_SIGNATURE]},
            {"charpr_ids": sorted(sizes)},
            "header.xml 꼬리에서 앞부분 전용 charPr 블록을 찾지 못해 본문 누출을 판정할 수 없음")
    else:
        for index, refs in run_refs(body).items():
            if consent is not None and index >= consent:
                continue
            leaked = sorted(refs & measured.front_charpr_ids)
            if leaked:
                violations.bad(
                    "food_crops.format.charpr.body_invariant", (),
                    "%s:%d" % (body["name"], index),
                    {"charPrIDRef": "본문 기존 id"}, leaked,
                    "앞부분 전용 charPr가 본문 문단으로 샘")
        for section in measured.sections:
            for index, refs in run_refs(section).items():
                unknown = sorted(cid for cid in refs if cid not in sizes)
                if unknown:
                    violations.bad(
                        "food_crops.format.charpr.body_invariant", (),
                        "%s:%d" % (section["name"], index),
                        {"charPrIDRef": "header.xml에 있는 id"}, unknown,
                        "문단이 header.xml에 없는 charPr를 참조함")

    # 동의서: 제목 20 굵게 / 본문 14 굵게 / `※` 10 보통.
    if consent is None:
        violations.bad("food_crops.format.charpr.consent", (), body["name"],
                       "`학부모 동의서` 문단", None,
                       "본문에서 학부모 동의서 제목을 찾지 못함")
        return
    report("food_crops.format.charpr.consent", body, consent,
           body["texts"][consent], (20, True))
    for index in range(consent + 1, len(body["texts"])):
        text = body["texts"][index]
        if not text:
            continue
        wanted = (10, False) if text.startswith("※") else (14, True)
        report("food_crops.format.charpr.consent", body, index, text, wanted)


def _check_manifest(measured, violations):
    manifest = measured.manifest
    if manifest is None:
        violations.bad(
            "food_crops.format.manifest.spine", (), MANIFEST_NAME,
            {"manifest": "opf:manifest + opf:spine"}, None,
            "content.hpf를 읽지 못해 spine 참조 무결성을 판정할 수 없음")
        return
    if manifest["duplicate_ids"]:
        violations.bad(
            "food_crops.format.manifest.spine", (), MANIFEST_NAME + ":item@id",
            {"duplicate_ids": []}, {"duplicate_ids": manifest["duplicate_ids"]},
            "content.hpf의 item id가 중복됨")
    if manifest["unresolved_idrefs"]:
        violations.bad(
            "food_crops.format.manifest.spine", (), MANIFEST_NAME + ":itemref@idref",
            {"unresolved_idrefs": []},
            {"unresolved_idrefs": manifest["unresolved_idrefs"]},
            "spine의 idref가 item으로 해석되지 않음")
    if manifest["missing_hrefs"]:
        violations.bad(
            "food_crops.format.manifest.spine", (), MANIFEST_NAME + ":item@href",
            {"missing_hrefs": []}, {"missing_hrefs": manifest["missing_hrefs"]},
            "manifest item의 href가 ZIP에 없는 항목을 가리킴")
    if manifest["spine_sections"] != ["section0", "section1", "section2"]:
        violations.bad(
            "food_crops.format.manifest.spine", (), MANIFEST_NAME + ":spine",
            {"section_order": ["section0", "section1", "section2"]},
            {"section_order": manifest["spine_sections"]},
            "spine에 section0·1·2가 그 순서로 있지 않음")

    # ZIP 디렉터리 항목(`BinData/`처럼 `/`로 끝나는 이름)은 파일이 아니므로
    # manifest item 대조에서 뺀다. 1판 산출물에도 같은 항목이 있다.
    required = sorted(name for name in measured.names
                      if not name.endswith("/")
                      and ((name.startswith("Contents/") and name.endswith(".xml"))
                           or name.startswith("BinData/")))
    unlisted = [name for name in required if name not in manifest["manifested"]]
    if unlisted:
        violations.bad(
            "food_crops.format.manifest.items", (), MANIFEST_NAME,
            {"unlisted_items": []}, {"unlisted_items": unlisted},
            "ZIP의 Contents/*.xml·BinData/* 항목 중 manifest에 없는 것이 있음")


# --- 외부 프로세스 (kordoc) ----------------------------------------------

def _run(command, *, timeout=180):
    try:
        return subprocess.run(
            [str(part) for part in command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        return exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        return exc


def _check_structure(cli_command, path, violations):
    if not cli_command:
        return {"status": "skipped", "reason": "no kordoc"}
    process = _run(list(cli_command) + ["validate", str(path), "--json"])
    if not isinstance(process, subprocess.CompletedProcess):
        # `--kordoc`로 지정한 명령이 없거나 시간 안에 끝나지 않았다. 검사를
        # 요청해 놓고 판정 없이 통과시키면 구조 검증이 빠진 산출물이
        # pass_with_notes로 나가므로, skip이 아니라 실패로 닫는다.
        violations.bad(
            "food_crops.format.structure", (), "kordoc validate",
            {"status": "ok"},
            {"status": "failed", "error": type(process).__name__,
             "detail": str(process)[-400:]},
            "지정된 kordoc 구조 검증을 실행하지 못함(파일 없음·타임아웃·OSError)")
        return {"status": "failed", "reason": "kordoc not runnable",
                "error": type(process).__name__}
    text = process.stdout or ""
    start, end = text.find("{"), text.rfind("}")
    report = {"status": "failed", "returncode": process.returncode,
              "issues": []}
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            issues = parsed.get("issues", [])
            # 1판·양식 원본에도 version.xml이 없다(설계 §8-2: 우리 변경 탓이
            # 아닌 사전 결함). 그 항목만 빼고 0건이어야 한다.
            remaining = [issue for issue in issues
                         if "version.xml" not in json.dumps(issue, ensure_ascii=False)]
            report["issues"] = remaining
            report["pre_existing"] = len(issues) - len(remaining)
            report["kordoc_ok"] = parsed.get("ok")
            # kordoc은 version.xml 부재만으로도 `ok: false`를 낸다. 남은 문제가
            # version.xml뿐이면 1판·양식 원본에도 있는 사전 결함이므로 통과다.
            # 다만 `ok: false`인데 문제를 하나도 보고하지 않은 경우는 그 false의
            # 근거를 이 검사기가 확인할 수 없으므로 통과시키지 않는다.
            report["status"] = "ok" if not remaining and (
                parsed.get("ok") is True or issues) else "failed"
    if report["status"] != "ok":
        violations.bad(
            "food_crops.format.structure", (), "kordoc validate",
            {"issues": [], "pre_existing_version_xml_allowed": True},
            {"status": report["status"], "issues": report["issues"][:5]},
            "kordoc 구조 검증이 사전 결함(version.xml) 밖의 문제를 보고함")
    return report


_TABLE_RULE = re.compile(r"^\s*\|?[\s|:\-]+\|[\s|:\-]*$")
_MARKUP = ("|", "*", "`", "#")


def _normalize_text(text: str) -> str:
    """재파싱 대조용으로 공백·마크다운 장식을 걷어낸다.

    kordoc의 markdown 재파싱은 표를 파이프 표로 펴고(`항목 | 값`), 문단 안의
    연속 공백을 하나로 줄이며(`위  원` → `위 원`), `*` 같은 문자를 `\\*`로
    이스케이프한다. 그래서 원본 XML 문단 텍스트와 문자 그대로 비교하면 표
    행과 이스케이프된 문단이 전부 '사라진 문단'으로 잡힌다. 표 구분선을 빼고
    마크다운 장식을 지운 뒤 공백을 모두 제거해 비교한다.
    """
    text = re.sub(r"\\(.)", r"\1", text)
    lines = [line for line in text.splitlines() if not _TABLE_RULE.match(line)]
    blob = "".join(lines)
    for token in _MARKUP:
        blob = blob.replace(token, "")
    return re.sub(r"\s+", "", blob)


def _check_reparse(cli_command, path, measured, violations):
    if not cli_command:
        return {"status": "skipped", "reason": "no kordoc"}
    work = Path(tempfile.mkdtemp(prefix="hwpx-probe-"))
    out_md = work / "reparsed.md"
    process = _run(list(cli_command) + [
        str(path), "-o", str(out_md), "--format", "markdown", "--silent",
        "--keep-empty-cols", "--keep-empty-paragraphs"])
    if not isinstance(process, subprocess.CompletedProcess):
        # 구조 검사와 같은 계약: `--kordoc`을 지정했는데 실행할 수 없으면
        # 재파싱 판정을 못 한 것이므로 skip이 아니라 실패다.
        violations.bad(
            "food_crops.format.reparse", (), "kordoc reparse",
            {"status": "ok"},
            {"status": "failed", "error": type(process).__name__,
             "detail": str(process)[-400:]},
            "지정된 kordoc 재파싱을 실행하지 못함(파일 없음·타임아웃·OSError)")
        return {"status": "failed", "reason": "kordoc not runnable",
                "error": type(process).__name__}
    if process.returncode != 0 or not out_md.is_file():
        # 실행 파일은 있는데 재파싱이 실패한 경우는 검사 불가가 아니라 실패다.
        # skip으로 넘기면 재파싱이 통째로 망가진 산출물이 통과한다.
        result = {"status": "failed", "reason": "kordoc reparse failed",
                  "returncode": process.returncode,
                  "stderr": (process.stderr or "")[-400:]}
        violations.bad(
            "food_crops.format.reparse", (), "kordoc reparse",
            {"status": "ok"}, {"status": "failed",
                               "returncode": process.returncode},
            "kordoc 재파싱이 실패해 문단 보존을 판정할 수 없음")
        return result
    text = out_md.read_text(encoding="utf-8", errors="replace")
    haystack = _normalize_text(text)
    missing, wanted_texts = [], []
    for section in measured.sections:
        for paragraph in section["texts"]:
            # 정규화 후 빈 문자열이 되는 문단(장식 문자만 있는 줄)은 대조할
            # 글자가 없으므로 판정에서 뺀다. 빈 문자열은 항상 포함으로 잡힌다.
            wanted = _normalize_text(paragraph) if paragraph else ""
            if not wanted:
                continue
            wanted_texts.append((section["name"], paragraph, wanted))
            if wanted not in haystack:
                missing.append({"section": section["name"], "text": paragraph})

    # 순서·중복 대조. 포함 여부만 보면 문단이 뒤섞이거나 하나가 둘로
    # 복제된 재파싱도 통과한다. 원본 문단을 문서 순서대로 훑으며 그 문단이
    # 나올 수 있는 가장 이른 자리를 찾는다(탐욕). 원본 순서가 정상이면 이
    # 자리는 언제나 존재하므로 오탐이 없고, 실제로 뒤집힌 경우만 걸린다.
    out_of_order = []
    cursor = 0
    for name, paragraph, wanted in wanted_texts:
        found = haystack.find(wanted, cursor)
        if found < 0:
            if haystack.find(wanted) >= 0:
                out_of_order.append({"section": name, "text": paragraph})
            continue
        cursor = found
    shortfall = []
    expected_counts = {}
    for _, _, wanted in wanted_texts:
        expected_counts[wanted] = expected_counts.get(wanted, 0) + 1
    for wanted, expected in expected_counts.items():
        actual = haystack.count(wanted)
        if actual < expected:
            shortfall.append({"text": wanted[:40], "expected": expected,
                              "actual": actual})

    result = {"status": "ok" if not (missing or out_of_order or shortfall)
              else "failed",
              "paragraphs": len(wanted_texts),
              "missing": missing[:5], "missing_count": len(missing),
              "out_of_order": out_of_order[:5],
              "out_of_order_count": len(out_of_order),
              "count_shortfalls": shortfall[:5],
              "count_shortfall_count": len(shortfall)}
    if missing:
        violations.bad(
            "food_crops.format.reparse", (), "kordoc reparse",
            {"missing_paragraphs": 0}, {"missing_count": len(missing)},
            "재파싱 텍스트에 사라진 문단이 있음")
    if out_of_order:
        violations.bad(
            "food_crops.format.reparse.order", (), "kordoc reparse",
            {"out_of_order_paragraphs": 0},
            {"out_of_order_count": len(out_of_order),
             "first": out_of_order[0]["text"][:40]},
            "재파싱 텍스트에서 문단 순서가 원본과 다름")
    if shortfall:
        violations.bad(
            "food_crops.format.reparse.count", (), "kordoc reparse",
            {"paragraph_count_shortfalls": 0},
            {"count_shortfall_count": len(shortfall), "first": shortfall[0]},
            "재파싱 텍스트에서 문단이 원본보다 적게 남음(중복 문단 소실)")
    return result


def check(path: Path, *, output_json: Path | None = None, cli_command=None):
    """HWPX 하나를 재서 `food_crops.format.*` 위반 목록을 낸다(고치지 않음)."""
    path = Path(path)
    if output_json is not None and output_json.resolve() == path.resolve():
        raise ValueError("--out must differ from the input HWPX (refusing overwrite)")
    violations = _Violations()
    result = {
        "schema": "gg-school-format-hwpx-probe/v1",
        "status": "blocked",
        "input": {"path": str(path), "sha256": None},
        "checks": {},
        "violations": violations.items,
    }
    if not path.is_file():
        violations.bad("food_crops.format.package", (), str(path),
                       {"file": "readable"}, None, "HWPX 파일이 없음")
        if output_json:
            _write(result, output_json)
        return result
    result["input"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    measured, reason = measure(path)
    if measured is None:
        violations.bad("food_crops.format.package", (), str(path),
                       {"zip": "readable"}, reason, "HWPX ZIP을 열지 못함")
        if output_json:
            _write(result, output_json)
        return result

    if measured.notes:
        result["notes"] = measured.notes
    result["parts"] = {
        "sections": [{key: value for key, value in section.items()
                      if not key.startswith("_")}
                     for section in measured.sections],
        "sec_cnt": measured.sec_cnt,
        "charpr_count": len(measured.charprs),
        "manifest_items": None if measured.manifest is None
        else measured.manifest["item_count"],
    }
    if len(measured.sections) != len(SECTION_NAMES) or not measured.charprs:
        # 구역이나 글자 모양을 읽지 못하면 나머지 대조는 의미가 없다.
        _check_sections(measured, violations)
        result["checks"] = {
            "section": {"status": "blocked", "measured": len(measured.sections)},
            "structure": {"status": "skipped", "reason": "incomplete package"},
            "reparse": {"status": "skipped", "reason": "incomplete package"},
        }
        result["status"] = "blocked"
        if output_json:
            _write(result, output_json)
        return result

    _check_sections(measured, violations)
    _check_page_numbers(measured, violations)
    _check_charpr(measured, violations)
    _check_manifest(measured, violations)
    result["checks"]["structure"] = _check_structure(cli_command, path, violations)
    result["checks"]["reparse"] = _check_reparse(
        cli_command, path, measured, violations)
    result["checks"]["section"] = {
        "status": "pass" if not any(
            value["code"].startswith("food_crops.format.section") for value in violations.items)
        else "blocked",
        "count": len(measured.sections),
        "sec_cnt": measured.sec_cnt,
        "margins": [section["margin"] for section in measured.sections],
    }
    result["checks"]["page_number"] = {
        "status": "pass" if not any(
            value["code"].startswith("food_crops.format.page_number") for value in violations.items)
        else "blocked",
        "page_num_counts": [len(section["page_num"]) for section in measured.sections],
        "new_num_counts": [len(section["new_num"]) for section in measured.sections],
    }
    result["checks"]["charpr"] = {
        "status": "pass" if not any(
            value["code"].startswith("food_crops.format.charpr") for value in violations.items)
        else "blocked",
        "charpr_count": len(measured.charprs),
        "front_charpr_ids": sorted(measured.front_charpr_ids, key=int),
    }
    result["checks"]["manifest"] = {
        "status": "pass" if not any(
            value["code"].startswith("food_crops.format.manifest") for value in violations.items)
        else "blocked",
        "version_xml_present": getattr(measured, "version_xml_present", None),
        "version_xml_note": "1판·양식 원본에도 없다(사전 결함, 우리 변경 탓 아님)"
        if not getattr(measured, "version_xml_present", True) else None,
    }
    # 페이지 크기 검사는 margin 코드로 내지만 section 상태에 넣는다.
    result["status"] = "blocked" if violations.items else "pass_with_notes"
    if output_json:
        _write(result, output_json)
    return result


def _write(result, output_json: Path):
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--kordoc", nargs="+", default=None,
                        help="kordoc 실행 명령(예: kordoc). 주면 구조 검증·재파싱도 한다")
    options = parser.parse_args(argv)
    try:
        result = check(options.hwpx, output_json=options.out,
                       cli_command=options.kordoc)
    except ValueError as exc:
        print("BLOCK: %s" % exc, file=sys.stderr)
        return 2
    codes = sorted({value["code"] for value in result["violations"]})
    print(json.dumps({
        "status": result["status"],
        "sha256": result["input"]["sha256"],
        "violations": len(result["violations"]),
        "codes": codes,
        "out": str(options.out) if options.out else None,
    }, ensure_ascii=False))
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    sys.exit(main())
