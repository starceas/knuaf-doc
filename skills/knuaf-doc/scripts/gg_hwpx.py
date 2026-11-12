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


def rewrite_margins(section_xml: str, margins_mm=SCHOOL_MARGINS_MM) -> str:
    """section0.xml의 <hp:margin/> 여백을 학교 규격(mm→HWPUNIT)으로 덮어쓴다."""

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


def inject_page_numbers(section_xml: str,
                        control: str = PAGE_NUMBER_CONTROL) -> str:
    """section의 첫 단 정의 뒤에 쪽번호 컨트롤을 넣는다(이미 있으면 그대로)."""
    if "<hp:pageNum" in section_xml:
        return section_xml
    match = _COLPR_CTRL_RE.search(section_xml)
    if match is None:
        return section_xml
    return section_xml[:match.end()] + control + section_xml[match.end():]


def normalize_markdown(data: bytes) -> str:
    """UTF-8(BOM 허용) 바이트 → BOM 제거·개행 LF 정규화 문자열."""
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def transform_hwpx(data: bytes, *, font: str = SCHOOL_FONT,
                   margins_mm=SCHOOL_MARGINS_MM, page_numbers: bool = True):
    """kordoc HWPX 바이트를 학교 규격으로 다시 쓴다.

    mimetype을 첫 항목·무압축으로 보존하고 나머지 항목은 순서대로 다시 쓴다.
    반환: (새 바이트, 적용 내역 dict).
    """
    applied = {"margins": [], "font": [], "page_numbers": []}
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
        mimetype = next(info for info in infos if info.filename == MIMETYPE)
        stored = zipfile.ZipInfo(MIMETYPE, date_time=mimetype.date_time)
        stored.compress_type = zipfile.ZIP_STORED
        stored.external_attr = mimetype.external_attr
        zout.writestr(stored, zin.read(MIMETYPE) or MIMETYPE_BYTES)
        for info in infos:
            if info.filename == MIMETYPE:
                continue
            member = zin.read(info.filename)
            if SECTION_RE.fullmatch(info.filename):
                text = member.decode("utf-8")
                updated = (rewrite_margins(text, margins_mm)
                           if margins_mm is not None else text)
                if updated != text:
                    applied["margins"].append(info.filename)
                if page_numbers:
                    numbered = inject_page_numbers(updated)
                    if numbered != updated:
                        applied["page_numbers"].append(info.filename)
                    updated = numbered
                member = updated.encode("utf-8")
            elif info.filename == HEADER_NAME:
                text = member.decode("utf-8")
                updated = rewrite_fonts(text, font) if font is not None else text
                if updated != text:
                    applied["font"].append(info.filename)
                member = updated.encode("utf-8")
            zout.writestr(info, member)
    return target.getvalue(), applied


def missing_format_steps(applied, *, font, margins_mm, page_numbers):
    """요청한 서식 변환 중 하나도 적용되지 않은 항목 목록.

    kordoc 산출물의 네임스페이스 접두사·비자기닫힘 요소·colPr 부재 등으로
    정규식이 아무것도 못 바꿨는데도 그대로 발행해 매니페스트가 적용된 것처럼
    적는 일을 막는다. None/False인 항목은 요청하지 않은 것으로 본다.
    """
    requested = []
    if margins_mm is not None:
        requested.append("margins")
    if font is not None:
        requested.append("font")
    if page_numbers:
        requested.append("page_numbers")
    return [name for name in requested if not applied.get(name)]


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


def _resolve_image(reference, roots, root):
    """목적지를 기준 폴더들 순서로 푼다. 경로 또는 ``_NotFound``를 낸다.

    뿌리 밖으로 풀린 후보는 없는 것으로 친다(DOCX 빌더와 같은 경계이며, 뿌리
    기준 상대 경로로 다시 쓸 수 있는 그림만 생성기에 넘길 수 있다). 둘 이상의
    서로 다른 파일이 걸리면 고르지 않고 후보 목록과 함께 ``_NotFound``다.
    """
    root = Path(root).expanduser().resolve()
    decoded = Path(unquote(reference)).expanduser()
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
            action="원고 옆과 이미지 폴더 모두에 같은 이름의 다른 그림이 있어 "
                   "고르지 않았습니다. 그림 경로를 폴더까지 적어 구분하세요.")
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

        transformed, applied = transform_hwpx(
            raw_path.read_bytes(), font=font, margins_mm=margins_mm,
            page_numbers=page_numbers,
        )
        pictures = embedded_picture_count(transformed)
        if pictures < len(references):
            raise HwpxBlocked(
                "images_not_embedded", referenced=len(references), embedded=pictures,
                action="원문의 그림 표기 일부가 HWPX에 그림으로 들어가지 않았습니다. "
                       "발행하지 않았습니다. 그림 경로 표기를 단순하게 고쳐 보세요.")
        missing = missing_format_steps(
            applied, font=font, margins_mm=margins_mm,
            page_numbers=page_numbers)
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
