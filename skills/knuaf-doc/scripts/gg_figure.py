"""그림 받기·넣기 (P1) — 앱 통로(fetch/place) + 앱 밖 직접 받기.

앱(논문GUI) 안에서는 받지 않는다. 요청 파일만 쓰고 앱이 만든 결과(res)를 읽는다.
앱 밖(데스크톱)에서만 이 파일이 같은 규칙으로 직접 받는다(https·허용목록·사설 IP
거절·상한·공공누리 1·2유형만).

    python gg_figure.py fetch <폴더> --slot <절ID> --page <이용조건 URL> \
        --image <그림 URL> [--image …]
    python gg_figure.py fetch <폴더> --slot <절ID> --page … --image … --direct
    python gg_figure.py place <폴더> --slot <절ID> --heading "<절 제목>" [--choose A]
    python gg_figure.py place <폴더> --slot <절ID> --heading … --image <폴더 기준 경로> \
        --org <기관명> [--title <그림 제목>]

출력은 JSON 한 줄. 종료코드 0=처리됨, 2=막힘(blocked/rejected).

KNUAF_GUI=1이면 --direct를 줘도 직접 받지 않는다(앱 통로만).
"""

import argparse
import codecs
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import gg_update

# 판 문자열은 배포된 ``version.json`` 하나에서 온다. 예전에는 이 자리에
# ``"0.4.11"`` 고정 문자열이 있어 v0.5.0 판까지 낡은 채로 user-agent 에
# 실렸다(사람이 판마다 손으로 고쳐야 했다). 판독 규칙은 ``gg_update`` 정본
# 하나만 쓴다 — 여기서 다시 적으면 두 규칙이 갈린다. 읽기는 import 한 번뿐이고
# 실패는 ``"unknown"``으로 닫는다(그림 기능 자체를 막지 않는다).
VERSION_FILE = Path(__file__).resolve().parent.parent / "version.json"


def skill_version():
    """배포된 ``version.json``의 판 문자열. 실패는 ``"unknown"``(예외 없음)."""
    try:
        return gg_update.format_version(
            gg_update.read_version_file(VERSION_FILE))
    except (OSError, gg_update.VersionUnreadable):
        return "unknown"


VERSION = skill_version()

HEARTBEAT_STALE_MS = 15_000
POLL_SECONDS = 60.0
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 20 * 1024 * 1024
TIMEOUT_FILE_SECONDS = 30.0
TIMEOUT_REQUEST_SECONDS = 50.0
MAX_REDIRECTS = 3
MAX_PIXELS = 50_000_000
MAX_IMAGES = 3
MAX_STORE_BYTES = 200 * 1024 * 1024
MAX_STORE_FILES_PER_DAY = 200
TYPE2_NOTICE = "2유형: 상업 출판 시 재확인"

# 요청 경로 퍼센트 인코딩(http.client는 ASCII만 보낸다)과 실재 대조용 정규화가
# 같은 safe 집합을 써야 한글 파일 이름이 양쪽에서 같은 키가 된다.
PATH_SAFE = "/%:@!$&'()*+,;=-._~"
QUERY_SAFE = "=&%:@!$'()*+,;/?-._~"

SLOT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

CAPTION_NUMBER = re.compile(r"^\s*그림\s*(\d+)\s*\.", re.M)
HEADING = re.compile(r"^(#{1,6})\s+")
TITLE_PREFIX = re.compile(r"^(?:[가-힣]\.|\d+[.)]|\(\d+\))\s*")

# 공공누리 마크 판독: <script> 본문은 표(유형 안내)라서 지우고, 주석은 마크를
# 담고 있어서 남긴다.
SCRIPT_BODY = re.compile(r"(?is)<script\b[^>]*>.*?</script\s*>")
MARK_PATTERNS = (
    re.compile(r"(?i)open_mark([1-4])\b"),
    re.compile(r"(?i)img_opentype0([1-4])\b"),
    re.compile(r"제\s*([1-4])\s*유형"),
)
IMG_TAG = re.compile(r"(?is)<img\b[^>]*>")
IMG_SRC = re.compile(r"""(?is)\bsrc\s*=\s*["']([^"']+)["']""")
ANCHOR_HREF = re.compile(r"""(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>""")
THIRD_PARTY = re.compile(r"사진\s*제공|ⓒ|©|copyright|출처\s*[:：](?!\s*표시)")
THIRD_PARTY_WINDOW = 250

APPROVED_KOGL = (1, 2)


class FigureError(Exception):
    """받기·넣기 계약 위반. reason에 기계가 읽는 짧은 사유가 들어간다."""

    def __init__(self, reason, detail=""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# 공용
# --------------------------------------------------------------------------
def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def folder_hash(folder):
    real = str(Path(folder).resolve())
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def _now_ms(now):
    return int(time.time() * 1000) if now is None else int(now() * 1000)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _is_app_mode(environ=None):
    env = os.environ if environ is None else environ
    return env.get("KNUAF_GUI") == "1"


def _figure_home(environ=None):
    env = os.environ if environ is None else environ
    value = env.get("KNUAF_FIGURE_HOME")
    return Path(value) if value else None


def _blocked(reason, detail=""):
    out = {"status": "blocked", "reason": reason}
    if reason == "slot_record_failed":
        out["notice"] = SLOT_LOCK_RETRY_NOTICE
    if detail:
        out["detail"] = detail
    return out


# --------------------------------------------------------------------------
# 앱 통로: 하트비트·턴·요청·결과
# --------------------------------------------------------------------------
def channel_state(home, now_ms=None):
    """하트비트 신선도. (ok, reason) — 낡았거나 없으면 즉시 실패."""
    home = Path(home)
    if not home.is_dir():
        return False, "no_channel"
    beat = _read_json(home / "figure-channel.json")
    if not isinstance(beat, dict) or beat.get("v") != 1:
        return False, "no_heartbeat"
    updated = beat.get("updatedAt")
    if not isinstance(updated, int):
        return False, "no_heartbeat"
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if now_ms - updated > HEARTBEAT_STALE_MS:
        return False, "stale_heartbeat"
    if now_ms - updated < -HEARTBEAT_STALE_MS:
        return False, "stale_heartbeat"
    return True, ""


def turn_state(home, folder, now_ms=None):
    """진행 중 메인 턴. (turnId, reason)."""
    turn = _read_json(Path(home) / "turn.json")
    if not isinstance(turn, dict) or turn.get("v") != 1:
        return None, "no_turn"
    turn_id = turn.get("turnId")
    if not isinstance(turn_id, str) or not turn_id:
        return None, "no_turn"
    expected = str(Path(folder).resolve())
    declared = turn.get("folder")
    if not isinstance(declared, str) or str(Path(declared).resolve()) != expected:
        return None, "folder_mismatch"
    started = turn.get("startedAt")
    if isinstance(started, int) and started > _now_ms(now_ms):
        return None, "invalid_turn"
    return turn_id, ""


def request_payload(req_id, turn_id, slot, page, images, mode="fetch"):
    """요청 JSON. 이 밖의 필드가 없어야 앱이 받는다(CONTRACT §3)."""
    if not REQUEST_ID.match(req_id or ""):
        raise FigureError("invalid_id", str(req_id))
    if not SLOT.match(slot or ""):
        raise FigureError("invalid_slot", str(slot))
    if not isinstance(page, str) or not page.startswith("https://"):
        raise FigureError("invalid_page", str(page))
    if (mode not in ("list", "fetch") or not isinstance(images, list)
            or len(images) > MAX_IMAGES or (mode == "fetch" and not images)
            or (mode == "list" and images)):
        raise FigureError("invalid_images", str(images))
    for url in images:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise FigureError("invalid_image_url", str(url))
    return {
        "v": 1,
        "id": req_id,
        "turnId": turn_id,
        "slot": slot,
        "page": page,
        "images": list(images),
    }


def read_result(home, req_id, mode="fetch", *, rule=None):
    """res/<id>.res.json 검사. 없으면 None."""
    data = _read_json(Path(home) / "res" / (req_id + ".res.json"))
    if data is None:
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise FigureError("invalid_result", "v")
    if data.get("id") != req_id:
        raise FigureError("invalid_result", "id")
    if data.get("status") not in ("done", "rejected"):
        raise FigureError("invalid_result", "status")
    if not isinstance(data.get("slot"), str):
        raise FigureError("invalid_result", "slot")
    if mode == "list":
        if data["status"] == "rejected":
            return _blocked(data.get("reason", "rejected"))
        return _validate_list_result(data, rule)
    if mode != "fetch" or "candidates" in data:
        raise FigureError("invalid_result", "mode")
    images = data.get("images")
    if images is None:
        images = []
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise FigureError("invalid_result", "images")
    for image in images:
        if not isinstance(image, dict):
            raise FigureError("invalid_result", "image")
        if image.get("verdict") not in ("approved", "ambiguous", "rejected"):
            raise FigureError("invalid_result", "verdict")
        if not isinstance(image.get("url"), str):
            raise FigureError("invalid_result", "url")
        if image.get("verdict") == "approved":
            sha = image.get("sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise FigureError("invalid_result", "sha256")
    return data


def fetch_app(folder, page, images, slot, environ=None, now=None,
              sleep=time.sleep, poll_seconds=POLL_SECONDS, *, list_mode=False,
              rules_path=None):
    """앱 요청. 일반 받기는 모든 채널 검사 앞에서 슬롯을 새 id로 선기록한다."""
    env = os.environ if environ is None else environ
    mode = "list" if list_mode else "fetch"
    try:
        folder, req_id = _begin_fetch(folder, slot, images, list_mode, "app", now)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    home = _figure_home(env)
    if home is None:
        return _blocked("no_channel", "KNUAF_FIGURE_HOME 없음")
    ok, reason = channel_state(home, _now_ms(now))
    if not ok:
        return _blocked(reason, "앱 하트비트 없음/낡음 — 기다리지 않는다")
    turn_id, reason = turn_state(home, folder, now)
    if not turn_id:
        return _blocked(reason)
    try:
        payload = request_payload(req_id, turn_id, slot, page, images, mode)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    chosen = select_page_rule(page, load_rules(rules_path)) if list_mode else None
    selected_rule = chosen[0] if chosen is not None else None
    directory = folder / ".knuaf-gui" / "figure-requests"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # 이름만 센다. 링크·디렉터리도 포함하고 어떤 기존 항목도 열거나 지우지 않는다.
        with os.scandir(directory) as entries:
            count = sum(1 for entry in entries if entry.name.endswith(".req.json"))
        if count >= 200:
            return _request_backlog()
    except OSError:
        return _request_backlog()
    try:
        _write_json_atomic(directory / (req_id + ".req.json"), payload)
    except OSError as exc:
        return _blocked("request_write_failed", str(exc))
    deadline = time.monotonic() + poll_seconds
    while True:
        turn = _read_json(Path(home) / "turn.json")
        if not isinstance(turn, dict) or turn.get("turnId") != turn_id:
            return dict(_blocked("cancelled"), reqId=req_id)
        try:
            result = read_result(home, req_id, mode, rule=selected_rule)
        except FigureError as exc:
            return dict(_blocked(exc.reason, exc.detail), reqId=req_id)
        if result is not None:
            result.setdefault("reqId", req_id)
            return result
        if time.monotonic() >= deadline:
            return dict(_blocked("timeout", "결과 없음 (%.0f초)" % poll_seconds),
                        reqId=req_id)
        sleep(0.2)


def _request_backlog():
    return dict(_blocked("request_backlog"), notice=(
        "그림 요청 기록이 200건에 닿아 새 그림을 받을 수 없습니다. "
        "논문 폴더의 .knuaf-gui/figure-requests 폴더 안 파일을 지운 뒤 다시 시도해 주세요."))


# --------------------------------------------------------------------------
# 앱 밖 직접 받기
# --------------------------------------------------------------------------
RULE_PAGE = "page_kogl_applies_to_body_images"
RULE_PAGE_QUERY = "page_query_kogl_applies_to_body_images"
RULE_PAGE_QUERY_ORG = "page_query_kogl_org_applies_to_body_images"
QUERY_KINDS = (RULE_PAGE_QUERY, RULE_PAGE_QUERY_ORG)
RULE_KINDS = (RULE_PAGE,) + QUERY_KINDS
RULE_FIELDS = ("id", "host", "org", "pagePath", "imagePath", "rule")
# 꼴 검사는 `fullmatch`와 같은 뜻이어야 한다 — `$`는 끝 줄바꿈 앞에서도 맞아
# 앱(JS `$`)과 답이 갈린다.
QUERY_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}\Z")
LITERAL_VALUE = re.compile(r"^[A-Za-z0-9._~-]{1,64}\Z")
HOST_PART = re.compile(r"^[a-z0-9-]{1,63}\Z")
HOST_LAST = re.compile(r"^[a-z]{2,}\Z")
STRICT_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:/?=&._~-")
STRICT_MAX_CHARS = 2048


def _host_shape_ok(host):
    """I4: 소문자 호스트 꼴. 다듬지 않고 그대로 본다."""
    if not isinstance(host, str) or not 0 < len(host) <= 253:
        return False
    parts = host.split(".")
    if len(parts) < 2:
        return False
    for part in parts:
        if HOST_PART.match(part) is None:
            return False
        if part.startswith("-") or part.endswith("-") or part.startswith("xn--"):
            return False
    return HOST_LAST.match(parts[-1]) is not None


def _query_spec(value):
    """값 형식 하나 → ('literal', 글자) | ('digits',) | ('enum', 집합) | None."""
    if not isinstance(value, dict) or len(value) != 1:
        return None
    key = next(iter(value))
    if key == "literal":
        text = value[key]
        if not isinstance(text, str) or LITERAL_VALUE.match(text) is None:
            return None
        return ("literal", text)
    if key == "digits":
        return ("digits",) if value[key] is True else None
    if key == "enum":
        items = value[key]
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            return None
        for item in items:
            if not isinstance(item, str) or LITERAL_VALUE.match(item) is None:
                return None
        if len(set(items)) != len(items):
            return None
        return ("enum", frozenset(items))
    return None


def _hangul_syllable(char):
    return 0xAC00 <= ord(char) <= 0xD7A3


def _org_shape(value, shape):
    """RDA-I01: 코드 포인트 범위만 검사한다. 다듬기·유니코드 분류 없음."""
    if not isinstance(value, str):
        return False
    # RDA-X1: 한 글자 법인/단위 기호만 예외. 일반 낱말은 2~40자 그대로.
    if shape == "term" and len(value) == 1:
        return 0x3200 <= ord(value) <= 0x33FF
    low, high = {"hangul": (2, 20), "term": (2, 40),
                 "latin": (2, 20), "marker": (8, 200)}[shape]
    if not low <= len(value) <= high:
        return False
    for char in value:
        code = ord(char)
        if shape == "hangul":
            ok = 0xAC00 <= code <= 0xD7A3
        elif shape == "term":
            ok = (0x21 <= code <= 0x7E or 0x3001 <= code <= 0x33FF
                  or 0xAC00 <= code <= 0xD7A3 or 0xFF01 <= code <= 0xFF5E)
        elif shape == "latin":
            ok = 0x41 <= code <= 0x5A
        else:
            ok = 0x20 <= code <= 0x7E
        if not ok:
            return False
    return True


def _org_list(value, shape, minimum, maximum):
    return (isinstance(value, list) and minimum <= len(value) <= maximum
            and all(_org_shape(word, shape) for word in value)
            and len(set(value)) == len(value))


def _org_lists(data):
    """T1~T3: 하나라도 틀리면 셋째 rule 값만 전부 버린다."""
    if not (_org_list(data.get("orgTerms"), "term", 1, 300)
            and _org_list(data.get("creditWords"), "hangul", 1, 40)
            and _org_list(data.get("latinOk"), "latin", 0, 300)):
        return None
    return {"orgTerms": tuple(data["orgTerms"]),
            "creditWords": tuple(data["creditWords"]),
            "latinOk": frozenset(data["latinOk"])}


def _sorted_aliases(aliases):
    # sorted는 안정 정렬 — 길이가 같으면 파일에 적힌 순서다.
    return sorted(aliases, key=len, reverse=True)


def _load_rule(item, org_lists=None):
    """I1~I9. 버리는 항목은 None."""
    if not isinstance(item, dict):
        return None
    for key in RULE_FIELDS:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            return None
    kind = item["rule"]
    if kind not in RULE_KINDS:
        return None
    org_fields = ("orgAliases", "bodyStart", "bodyEnd")
    if kind != RULE_PAGE_QUERY_ORG and any(key in item for key in org_fields):
        return None
    if kind == RULE_PAGE_QUERY_ORG:
        if org_lists is None:
            return None
        aliases = item.get("orgAliases")
        if (not _org_list(aliases, "hangul", 1, 4)
                or aliases[0] != item["org"]
                or any(word in alias for alias in aliases
                       for key in ("orgTerms", "creditWords")
                       for word in org_lists[key])):
            return None
        start, end = item.get("bodyStart"), item.get("bodyEnd")
        if (not _org_shape(start, "marker") or not _org_shape(end, "marker")
                or start in end or end in start):
            return None
    if not _host_shape_ok(item["host"]):
        return None
    try:
        page_re = re.compile(item["pagePath"])
        image_re = re.compile(item["imagePath"])
    except re.error:
        return None
    if kind == RULE_PAGE:
        if "pageQuery" in item:
            return None
        query = None
    else:
        raw = item.get("pageQuery")
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 8:
            return None
        query = {}
        for name, spec in raw.items():
            if QUERY_NAME.match(name) is None:
                return None
            parsed = _query_spec(spec)
            if parsed is None:
                return None
            query[name] = parsed
    parsed = {
        "id": item["id"],
        "host": item["host"],
        "org": item["org"],
        "pagePath": page_re,
        "imagePath": image_re,
        "rule": kind,
        "pageQuery": query,
    }
    if kind == RULE_PAGE_QUERY_ORG:
        parsed.update(org_lists)
        parsed.update(orgAliases=_sorted_aliases(aliases), bodyStart=start, bodyEnd=end)
    return parsed


def _one_kind_per_host(items):
    """G5: 한 호스트에 옛·새 rule 값 항목이 섞이면 파일째 거절(항목 검사 전)."""
    kinds = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        host = item.get("host")
        kind = item.get("rule")
        if not isinstance(host, str) or kind not in RULE_KINDS:
            continue
        kinds.setdefault(host, set()).add(kind)
    return all(len(found) == 1 for found in kinds.values())


def _reject_json_constant(name):
    # 파이썬 json은 NaN·Infinity·-Infinity를 받지만 표준 JSON(앱 JSON.parse)은 거절한다 — G1을 같게.
    raise ValueError("non-standard JSON constant: " + name)


def _read_rules_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"),
                          parse_constant=_reject_json_constant)
    except (OSError, ValueError):
        return None


def load_rules(path=None):
    """허용목록. 없거나 깨지면 빈 목록 = 전부 거절."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "references" / "figure-sources.json"
    data = _read_rules_json(path)
    if not isinstance(data, dict):
        return []
    version = data.get("v")
    # JSON true는 파이썬에서 1과 같다 — 형까지 본다(G2).
    if type(version) not in (int, float) or version != 1:
        return []
    rules = data.get("rules")
    if not isinstance(rules, list):
        return []
    if not _one_kind_per_host(rules):
        return []
    out = []
    org_lists = _org_lists(data)
    for item in rules:
        parsed = _load_rule(item, org_lists)
        if parsed is not None:
            out.append(parsed)
    ids = [rule["id"] for rule in out]
    if len(set(ids)) != len(ids):
        return []
    return out


def _strict_page(raw):
    """PQ-0: 새 rule 값 항목의 페이지 주소를 적힌 글자 그대로 본다 → (host, path, query)."""
    if not isinstance(raw, str) or len(raw) > STRICT_MAX_CHARS:
        return None
    if not set(raw) <= STRICT_CHARS:
        return None
    if not raw.startswith("https://"):
        return None
    rest = raw[8:]
    if ":" in rest or rest.count("?") != 1:
        return None
    front, query = rest.split("?", 1)
    slash = front.find("/")
    if slash <= 0:
        return None
    host, path = front[:slash], front[slash:]
    # ':'는 위에서 이미 걸러졌으니 경로에 남는 것은 영숫자와 ._~/- 뿐이다.
    if "=" in path or "&" in path:
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    return host, path, query


def _query_matches(rule, query):
    """PQ-1~5: 이름 집합이 같고, 이름마다 한 번씩, 값이 형식에 맞아야 한다."""
    specs = rule.get("pageQuery")
    if not specs:
        return False
    seen = set()
    for piece in query.split("&"):
        if piece.count("=") != 1:
            return False
        name, value = piece.split("=", 1)
        if not name or name in seen:
            return False
        spec = specs.get(name)
        if spec is None:
            return False
        if spec[0] == "literal":
            if value != spec[1]:
                return False
        elif spec[0] == "digits":
            # isdigit·\d는 유니코드 숫자를 받는다 — '0'~'9'만 본다.
            if not 1 <= len(value) <= 20 or not all("0" <= ch <= "9" for ch in value):
                return False
        elif value not in spec[1]:
            return False
        seen.add(name)
    return len(seen) == len(specs)


def select_page_rule(raw, rules):
    """PQ-8: 규칙집 순서로 처음 맞는 항목 하나 → (항목, 보낼 주소) 또는 None."""
    strict = _strict_page(raw)
    for rule in rules:
        if rule.get("rule") in QUERY_KINDS:
            if strict is None or strict[0] != rule["host"]:
                continue
            if rule["pagePath"].fullmatch(strict[1]) is None:
                continue
            if not _query_matches(rule, strict[2]):
                continue
            return rule, raw
        try:
            target = normalize_url(raw, host=rule["host"],
                                   path_pattern=rule["pagePath"])
        except FigureError:
            continue
        return rule, target
    return None


def _bad_address(text):
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return True
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 4:
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _default_resolver(host, port):
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def normalize_url(url, *, host=None, path_pattern=None):
    """https·포트 없음·사용자정보 없음·IP 리터럴 아님·호스트 정확 일치."""
    if not isinstance(url, str) or not url:
        raise FigureError("invalid_url", "빈 URL")
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        raise FigureError("invalid_url", url)
    if parts.scheme.lower() != "https":
        raise FigureError("not_https", parts.scheme)
    if parts.username or parts.password or "@" in parts.netloc:
        raise FigureError("userinfo", parts.netloc)
    try:
        port = parts.port
    except ValueError:
        raise FigureError("bad_port", parts.netloc)
    if port not in (None, 443):
        raise FigureError("bad_port", str(port))
    try:
        ascii_host = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
    except UnicodeError:
        raise FigureError("bad_host", parts.netloc)
    ascii_host = ascii_host.rstrip(".").lower()
    if not ascii_host:
        raise FigureError("bad_host", parts.netloc)
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise FigureError("ip_literal", ascii_host)
    if host is not None and ascii_host != host:
        raise FigureError("host_not_allowed", ascii_host)
    pathname = parts.path or "/"
    if path_pattern is not None and not path_pattern.fullmatch(pathname):
        raise FigureError("path_not_allowed", pathname)
    return urllib.parse.urlunsplit(("https", ascii_host, pathname, parts.query, ""))


def resolve_addresses(host, resolver=None, allow_loopback=False):
    """DNS 결과 전부가 공인 IP일 때만 통과(fail-closed). 첫 IP를 고정한다."""
    lookup = resolver or _default_resolver
    try:
        addrs = lookup(host, 443)
    except OSError as exc:
        raise FigureError("dns_failed", str(exc))
    addrs = [a for a in addrs if a]
    if not addrs:
        raise FigureError("dns_failed", "결과 없음")
    for addr in addrs:
        if _bad_address(addr):
            if not (allow_loopback and _is_loopback(addr)):
                raise FigureError("private_address", addr)
    return addrs[0]


def _is_loopback(text):
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """DNS 재조회 없이 고른 IP로만 붙는다(TLS servername은 호스트)."""

    def __init__(self, host, address, port=443, timeout=TIMEOUT_FILE_SECONDS,
                 context=None):
        super().__init__(host, port, timeout=timeout, context=context or ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _remaining(limit):
    deadline = limit.get("deadline")
    if deadline is None:
        return float(TIMEOUT_REQUEST_SECONDS)
    return deadline - time.monotonic()


class _HardDeadline:
    """파일 하나의 절대 시한(R-1). 시한이 지나면 소켓을 닫아 recv를 깨운다.

    `getresponse()`의 헤더 읽기는 우리 루프가 아니라서 deadline을 다시 볼
    기회가 없다. 조금씩 흘리는 서버도 여기서 실제로 끊긴다.
    """

    def __init__(self, file_seconds=None):
        self.expired = False
        self._sock = None
        self._deadline = None
        self._timer = None
        self._seconds = TIMEOUT_FILE_SECONDS if file_seconds is None else file_seconds

    def start(self, sock, request_deadline=None):
        now = time.monotonic()
        self._sock = sock
        self._deadline = now + self._seconds
        if request_deadline is not None:
            self._deadline = min(self._deadline, request_deadline)
        self._timer = threading.Timer(max(0.0, self._deadline - now), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self):
        self.expired = True
        sock = self._sock
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError):
            pass

    def remaining(self):
        if self._deadline is None:
            return self._seconds
        return self._deadline - time.monotonic()

    def cancel(self):
        timer = self._timer
        if timer is not None:
            timer.cancel()


def _slice_timeout(*values):
    """양수 대기 시간으로 자른다 — 0/음수는 즉시 실패가 아니라 최소 대기."""
    return max(0.001, min(values))


def _resolve_in_deadline(host, resolver, allow_loopback, limit):
    """DNS도 요청 전체 상한 안에서 센다(getaddrinfo는 자체 시간 상한이 없다)."""
    box = {}

    def work():
        try:
            box["address"] = resolve_addresses(host, resolver, allow_loopback)
        except BaseException as exc:  # noqa: BLE001 — 스레드 결과를 그대로 옮긴다
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(max(0.0, _remaining(limit)))
    if thread.is_alive():
        raise FigureError("timeout", "DNS 응답 시간 초과")
    if "error" in box:
        raise box["error"]
    return box["address"]


def http_get(url, *, rule, kind, resolver=None, allow_loopback=False,
             tls_context=None, limiter=None, follow_redirects=True):
    """고정 IP + 수동 리다이렉트(≤3) + 상한. (final_url, status, headers, body).

    새 ``rule`` 값 항목의 페이지는 ``follow_redirects=False``로 부른다 —
    3xx면 옮겨 갈 주소로 요청을 보내지 않고 ``redirect_not_allowed``.
    """
    limit = limiter or {"bytes": 0, "deadline": None}
    if limit.get("deadline") is None:
        limit["deadline"] = time.monotonic() + TIMEOUT_REQUEST_SECONDS
    pattern = rule["pagePath"] if kind == "page" else rule["imagePath"]
    if kind == "page" and rule.get("rule") in QUERY_KINDS:
        # 엄격한 꼴을 통과한 주소는 해석기가 바꿀 것이 없다 — 그대로 보낸다.
        target = url
    else:
        target = normalize_url(url, host=rule["host"], path_pattern=pattern)
    for _ in range(MAX_REDIRECTS + 1):
        remaining = _remaining(limit)
        if remaining <= 0:
            raise FigureError("timeout", "요청 전체 시간 초과")
        timeout = min(TIMEOUT_FILE_SECONDS, remaining)
        parts = urllib.parse.urlsplit(target)
        address = _resolve_in_deadline(parts.hostname, resolver, allow_loopback, limit)
        conn = _PinnedHTTPSConnection(parts.hostname, address, timeout=timeout,
                                      context=tls_context)
        hard = None
        try:
            conn.request("GET", _request_target(parts), headers={
                "accept": "*/*",
                "user-agent": "knuaf-doc/" + VERSION,
                "connection": "close",
            })
            # 헤더 읽기(getresponse)는 우리 루프 밖이라 deadline을 다시 볼 틈이
            # 없다 — 타이머가 소켓을 닫아 조금씩 흘리는 서버도 파일 상한에서
            # 실제로 끊는다(R-1).
            hard = _HardDeadline()
            hard.start(conn.sock, limit.get("deadline"))
            response = conn.getresponse()
            if hard.expired:
                raise FigureError("timeout", "파일 응답 시간 초과")
            if 300 <= response.status < 400 and not follow_redirects:
                raise FigureError("redirect_not_allowed", str(response.status))
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("location")
                if not location:
                    raise FigureError("bad_redirect", "Location 없음")
                target = normalize_url(urllib.parse.urljoin(target, location),
                                       host=rule["host"], path_pattern=pattern)
                continue
            if response.status != 200:
                raise FigureError("http_status", str(response.status))
            capped = MAX_HTML_BYTES if kind == "page" else MAX_IMAGE_BYTES
            body = bytearray()
            while True:
                # 한 번 읽기의 상한을 남은 시간으로 다시 건다 — 조금씩 흘리는
                # 서버에도 파일·요청 상한이 실제로 걸린다(R-1).
                remaining = _remaining(limit)
                if remaining <= 0:
                    raise FigureError("timeout", "요청 전체 시간 초과")
                if hard.remaining() <= 0:
                    raise FigureError("timeout", "파일 응답 시간 초과")
                if conn.sock is not None:
                    conn.sock.settimeout(
                        _slice_timeout(TIMEOUT_FILE_SECONDS, remaining,
                                       hard.remaining()))
                chunk = response.read1(8192)
                if hard.expired:
                    raise FigureError("timeout", "파일 응답 시간 초과")
                if not chunk:
                    break
                body.extend(chunk)
                limit["bytes"] += len(chunk)
                if len(body) > capped:
                    raise FigureError("too_large", str(capped))
                if limit["bytes"] > MAX_REQUEST_BYTES:
                    raise FigureError("too_large", "요청 합계")
            return target, response.status, dict(response.getheaders()), bytes(body)
        except (socket.timeout, TimeoutError):
            raise FigureError("timeout", "파일 응답 시간 초과")
        except ssl.SSLError as exc:
            if hard is not None and hard.expired:
                raise FigureError("timeout", "파일 응답 시간 초과")
            raise FigureError("tls_failed", str(exc))
        except (ConnectionError, http.client.HTTPException) as exc:
            if hard is not None and hard.expired:
                raise FigureError("timeout", "파일 응답 시간 초과")
            raise FigureError("network_failed", type(exc).__name__)
        except OSError:
            if hard is not None and hard.expired:
                raise FigureError("timeout", "파일 응답 시간 초과")
            raise
        finally:
            if hard is not None:
                hard.cancel()
            conn.close()
    raise FigureError("too_many_redirects", str(MAX_REDIRECTS))


def _canonical_component(text, safe):
    """퍼센트 인코딩을 한 가지 표기로 모은다(한글·소문자 %xx 대조용)."""
    quoted = urllib.parse.quote(text, safe=safe)
    return re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), quoted)


def _request_target(parts):
    """http.client가 ASCII만 보내므로 경로·쿼리를 퍼센트 인코딩한다."""
    path = _canonical_component(parts.path or "/", PATH_SAFE)
    if parts.query:
        path += "?" + _canonical_component(parts.query, QUERY_SAFE)
    return path


def _charset(headers):
    """Content-Type의 charset. 없거나 모르는 이름이면 utf-8."""
    value = ""
    for key, header in (headers or {}).items():
        if key.lower() == "content-type":
            value = header or ""
            break
    match = re.search(r"(?i)charset\s*=\s*\"?([\w.:-]+)", value)
    if not match:
        return "utf-8"
    name = match.group(1)
    try:
        codecs.lookup(name)
    except LookupError:
        return "utf-8"
    return name


def _decode_html(body, headers):
    return body.decode(_charset(headers), "replace")


def kogl_marks(html):
    """공공누리 마크. <script> 본문 제외, 주석 보존. (types, mark lines)."""
    text = SCRIPT_BODY.sub(" ", html or "")
    marks = []
    types = set()
    for pattern in MARK_PATTERNS:
        for match in pattern.finditer(text):
            types.add(int(match.group(1)))
            line = text[max(0, match.start() - 40):match.end() + 40].strip()
            marks.append(re.sub(r"\s+", " ", line))
    return types, marks


def _image_size(data):
    """헤더만으로 (format, width, height, frames)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return "png", width, height, 1
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 13:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return "gif", width, height, _gif_frames(data)
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0xD9):
                index += 2
                continue
            if marker == 0xDA:
                break
            length = int.from_bytes(data[index + 2:index + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height = int.from_bytes(data[index + 5:index + 7], "big")
                width = int.from_bytes(data[index + 7:index + 9], "big")
                return "jpeg", width, height, 1
            index += 2 + length
    raise FigureError("bad_image", "JPEG·PNG·GIF 아님")


def _gif_frames(data):
    if len(data) < 13:
        return 1
    flags = data[10]
    index = 13
    if flags & 0x80:
        index += 3 * (2 << (flags & 0x07))
    frames = 0
    while index < len(data):
        block = data[index]
        if block == 0x3B:
            break
        if block == 0x21:
            index += 2
            while index < len(data) and data[index]:
                index += 1 + data[index]
            index += 1
        elif block == 0x2C:
            if index + 9 >= len(data):
                break
            frames += 1
            local = data[index + 9]
            index += 10
            if local & 0x80:
                index += 3 * (2 << (local & 0x07))
            index += 1
            while index < len(data) and data[index]:
                index += 1 + data[index]
            index += 1
        else:
            break
    return max(frames, 1)


def _reference_key(value):
    """URL → 실재 대조용 키. 스킴은 키에 넣지 않고, 포트는 그 스킴의 기본만
    생략과 같게 본다(CONTRACT §8) — http:443·https:80은 다른 자원이다."""
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    if parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        return None
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return None
    return (host,
            _canonical_component(parts.path or "/", PATH_SAFE),
            _canonical_component(parts.query, QUERY_SAFE))


def _same_reference(candidate, url):
    """페이지의 `http://` 표기를 같은 자원으로 본다.

    CONTRACT §8: 받기는 https만이지만, "그림 URL이 페이지에 실재" 대조는
    호스트·경로·쿼리가 같고 포트가 기본일 때 http와 https를 같게 본다.
    """
    key = _reference_key(candidate)
    return key is not None and key == _reference_key(url)


def _page_references(html, url, page_url):
    """그림 URL이 페이지에 <img src>/<a href>로 실제 걸려 있는가.

    실재를 증명한 태그의 (시작, 끝) 위치를 돌려준다 — 제3자 표기 창은 그
    자리를 기준으로 잡는다(그림이 <a href>로만 걸린 경우 포함).
    상대 경로는 그림 URL이 아니라 **페이지의 최종 URL**로 푼다.
    """
    for match in IMG_TAG.finditer(html):
        tag = match.group(0)
        src = IMG_SRC.search(tag)
        if src and _same_reference(
                urllib.parse.urljoin(page_url, src.group(1).strip()), url):
            return match.span()
    for match in ANCHOR_HREF.finditer(html):
        href = match.group(1).strip()
        if href.startswith("#"):
            continue
        if _same_reference(urllib.parse.urljoin(page_url, href), url):
            return match.span()
    return None


def _third_party_near(html, anchor):
    """실재를 증명한 자리 ±250자의 제3자 표기. 공공누리 설명 문구는 뺀다."""
    if anchor is None:
        return None
    start, end = anchor
    window = html[max(0, start - THIRD_PARTY_WINDOW):end + THIRD_PARTY_WINDOW]
    found = THIRD_PARTY.search(window)
    return found.group(0) if found else None


# 목록 전용 판독기. 기존 실재 대조(IMG_SRC 등)의 넓은 해석은 바꾸지 않는다.
INACTIVE_TAGS = frozenset(("script", "style", "template", "noscript",
                           "textarea", "title", "xmp", "iframe"))
ASCII_SPACE = " \t\n\r\f"
CANDIDATE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~/?=&%:@!$'()*+,;")
TAG_START = re.compile(r"<(/?)([A-Za-z][^\t\n\r\f />]*)(?=[\t\n\r\f />])", re.ASCII)
ENTITY = re.compile(r"&(?:#[^;]*|[A-Za-z][A-Za-z0-9]*);")
BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _candidate_tag(html, start, name_end):
    """속성 단위로 4096자까지 읽는다 → (끝, 첫 src). 따옴표 안 >는 글자."""
    end = min(len(html), start + 4096)
    pos = name_end
    src = None
    seen_src = False
    while pos < end:
        while pos < end and html[pos] in ASCII_SPACE + "/":
            pos += 1
        if pos >= end:
            break
        if html[pos] == ">":
            return pos + 1, src
        begin = pos
        while pos < end and html[pos] not in ASCII_SPACE + "=/>":
            pos += 1
        name = html[begin:pos].lower()
        if pos == begin:
            pos += 1
            continue
        while pos < end and html[pos] in ASCII_SPACE:
            pos += 1
        value = ""
        if pos < end and html[pos] == "=":
            pos += 1
            while pos < end and html[pos] in ASCII_SPACE:
                pos += 1
            if pos < end and html[pos] in "\"'":
                quote = html[pos]
                pos += 1
                begin = pos
                while pos < end and html[pos] != quote:
                    pos += 1
                if pos >= end:
                    return None
                value = html[begin:pos]
                pos += 1
            else:
                begin = pos
                while pos < end and html[pos] not in ASCII_SPACE + ">":
                    pos += 1
                value = html[begin:pos]
        if name == "src" and not seen_src:
            src, seen_src = value, True
    return None


def _candidate_tags(html):
    """I-05: 한 번 훑기; 같은 이름 깊이, 모든 여는 태그의 따옴표 범위 건너뜀.

    가릴 구간을 복사하는 대신 밖의 img 자리만 낸다. 원본 자리 번호는 그대로다.
    """
    pos = 0
    inactive = None
    depth = 0
    while pos < len(html):
        start = html.find("<", pos)
        if start < 0:
            return
        if inactive is None and html.startswith("<!--", start):
            end = html.find("-->", start + 4)
            if end < 0:
                return
            pos = end + 3
            continue
        match = TAG_START.match(html, start)
        if match is None:
            pos = start + 1
            continue
        closing, name = match.group(1), match.group(2).lower()
        if closing:
            end = html.find(">", match.end())
            if end < 0:
                return
            pos = end + 1
            if name == inactive:
                depth -= 1
                if depth == 0:
                    inactive = None
            continue
        tag = _candidate_tag(html, start, match.end())
        if tag is None:
            return  # 끝 없는/너무 긴 여는 태그부터 끝까지 가린다.
        pos, src = tag
        if inactive is not None:
            if name == inactive:
                depth += 1
        elif name in INACTIVE_TAGS:
            inactive, depth = name, 1
        elif name == "img":
            yield start, pos, src


# RDA §3-2: 문자참조를 풀지 않는다. 글자를 만들지 않는 기호만 허용한다.
ORG_ENTITY_NAMES = (
    "lt", "gt", "amp", "quot", "apos", "nbsp", "middot", "times", "divide",
    "bull", "hellip", "ndash", "mdash", "lsquo", "rsquo", "ldquo", "rdquo",
    "larr", "rarr", "uarr", "darr", "deg", "plusmn")
ORG_SYMBOL_RANGES = (
    (0x00A0, 0x00BF), (0x00D7, 0x00D7), (0x00F7, 0x00F7),
    (0x2010, 0x205E), (0x2190, 0x23FF), (0x2460, 0x249B),
    (0x24EA, 0x24FF), (0x2500, 0x27BF), (0x2900, 0x2BFF), (0x3000, 0x303F))
ORG_FENCE = "\"'<>\n\r"
ORG_URL_END = " \t\n\r\"'<>"
HEX_CHARS = frozenset("0123456789ABCDEFabcdef")


def _body_region(html, rule):
    """§3-1: 표지 둘이 각각 한 번이고 순서가 맞을 때만 안쪽 범위."""
    start, end = rule["bodyStart"], rule["bodyEnd"]
    if html.count(start) != 1 or html.count(end) != 1:
        return None
    inside = html.find(start) + len(start)
    finish = html.find(end)
    return (inside, finish) if inside <= finish else None


def _org_entities_ok(text):
    pos = text.find("&")
    while pos >= 0:
        if not any(text.startswith("&" + name + ";", pos)
                   for name in ORG_ENTITY_NAMES):
            if not text.startswith("&#", pos):
                return False
            end = pos + 2
            while end < len(text) and "0" <= text[end] <= "9":
                end += 1
            if (not 1 <= end - pos - 2 <= 7 or end == len(text)
                    or text[end] != ";"):
                return False
            code = int(text[pos + 2:end])
            if not any(low <= code <= high for low, high in ORG_SYMBOL_RANGES):
                return False
        pos = text.find("&", pos + 1)
    return True


def _foreign_mark_in(text, rule):
    """§3-2 ①~⑦ 순서 고정. 정규식·공백/대소문자 정규화 없음."""
    if not _org_entities_ok(text):
        return True
    for char in text:
        code = ord(char)
        if (0x1100 <= code <= 0x1112 or 0x1161 <= code <= 0x1175
                or 0x11A8 <= code <= 0x11C2):
            return True
    # ③ r1.1: 표현의 양끝이 울타리여야 한다. 빈칸/쉼표는 울타리가 아니다.
    for alias in rule["orgAliases"]:
        for word in rule["creditWords"]:
            for phrase in (alias + word, alias + " " + word, word + alias,
                           word + " " + alias, word + ":" + alias,
                           word + ": " + alias, word + " : " + alias):
                pos = text.find(phrase)
                while pos >= 0:
                    end = pos + len(phrase)
                    if ((pos == 0 or text[pos - 1] in ORG_FENCE)
                            and (end == len(text) or text[end] in ORG_FENCE)):
                        text = text[:pos] + " " + text[end:]
                        pos = text.find(phrase, pos + 1)
                    else:
                        pos = text.find(phrase, end)
    # ④ 별칭 앞에 한글이 붙었으면 더 긴 남의 이름일 수 있다.
    for alias in rule["orgAliases"]:
        pos = text.find(alias)
        while pos >= 0:
            if pos > 0 and _hangul_syllable(text[pos - 1]):
                return True
            text = text[:pos] + " " + text[pos + len(alias):]
            pos = text.find(alias, pos + 1)
    if any(word in text for word in rule["orgTerms"]):
        return True
    for word in rule["creditWords"]:
        pos = text.find(word)
        while pos >= 0:
            end = pos + len(word)
            if end == len(text) or not _hangul_syllable(text[end]):
                return True
            pos = text.find(word, pos + 1)
    # ⑦만 주소·%XX를 지운 사본에 적용한다. ①~⑥은 주소 속 글도 검사했다.
    acronym_text = text
    pos = 0
    while pos < len(acronym_text):
        if (acronym_text.startswith("http://", pos)
                or acronym_text.startswith("https://", pos)):
            end = pos
            while end < len(acronym_text) and acronym_text[end] not in ORG_URL_END:
                end += 1
            acronym_text = acronym_text[:pos] + " " + acronym_text[end:]
        pos += 1
    pos = 0
    while pos < len(acronym_text):
        if (acronym_text[pos] == "%" and pos + 2 < len(acronym_text)
                and acronym_text[pos + 1] in HEX_CHARS
                and acronym_text[pos + 2] in HEX_CHARS):
            acronym_text = acronym_text[:pos] + " " + acronym_text[pos + 3:]
        pos += 1
    pos = 0
    while pos < len(acronym_text):
        if "A" <= acronym_text[pos] <= "Z":
            end = pos + 1
            while end < len(acronym_text) and "A" <= acronym_text[end] <= "Z":
                end += 1
            if end - pos >= 2 and acronym_text[pos:end] not in rule["latinOk"]:
                return True
            pos = end
        else:
            pos += 1
    return False


def _org_reading(html, final_image_url, page_url, rule):
    """§3-1: (none|hit|unknown, 구역 글). 기존 권리 정규식은 호출부 몫."""
    region = _body_region(html, rule)
    if region is None:
        return "unknown", None
    found = False
    for start, end, src in _candidate_tags(html):
        if src is None:
            continue
        try:
            resolved = urllib.parse.urljoin(page_url, src.strip(ASCII_SPACE))
        except ValueError:
            continue
        if not _same_reference(resolved, final_image_url):
            continue
        found = True
        if start < region[0] or end > region[1]:
            return "unknown", None
    if not found:
        return "unknown", None
    text = html[region[0]:region[1]]
    return ("hit" if _foreign_mark_in(text, rule) else "none"), text


def _candidate_value_ok(value, *, raw=False):
    if (not value or len(value) > 2048 or ENTITY.search(value)
            or BAD_PERCENT.search(value)):
        return False
    query = value.find("?")
    return all(char in CANDIDATE_CHARS or ord(char) >= 128
               or (raw and char in "[]" and (query < 0 or index < query))
               for index, char in enumerate(value))


def _candidate_url(value, page, rule):
    if value is None:
        return None
    value = value.strip(ASCII_SPACE).replace("&amp;", "&")
    if not _candidate_value_ok(value, raw=True):
        return None
    try:
        # impl-1 I-01: 해석 전에 거절. urljoin/WHATWG가 다른 자원으로 접지 못하게 한다.
        if value.endswith("?") or "?#" in value or value.startswith("///"):
            return None
        raw_path = urllib.parse.urlsplit(value).path
        if "//" in raw_path or any(
                re.sub(r"%2e", ".", part, flags=re.I) in (".", "..")
                for part in raw_path.split("/")):
            return None
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(page, value))
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or any(ord(c) >= 128 for c in parts.netloc)
                or ":" in parts.netloc or "@" in parts.netloc):
            return None
        url = urllib.parse.urlunsplit((
            "https", parts.netloc.lower(),
            urllib.parse.quote(parts.path or "/", safe=PATH_SAFE),
            urllib.parse.quote(parts.query, safe=QUERY_SAFE), ""))
        if len(url) > 2048:
            return None
        return normalize_url(url, host=rule["host"], path_pattern=rule["imagePath"])
    except (ValueError, UnicodeError, FigureError):
        return None


def extract_candidates(html, page, rule):
    """문서 순서의 {url, credit} 최대 30개와 잘림 여부. 승인 판정은 아니다."""
    candidates, seen = [], set()
    org_rule = rule.get("rule") == RULE_PAGE_QUERY_ORG
    region = _body_region(html, rule) if org_rule else None
    body_credit = False
    if region is not None:
        text = html[region[0]:region[1]]
        body_credit = bool(THIRD_PARTY.search(text)) or _foreign_mark_in(text, rule)
    for start, end, src in _candidate_tags(html):
        url = _candidate_url(src, page, rule)
        if url is None or url in seen:
            continue
        if len(candidates) == 30:
            return candidates, True
        seen.add(url)
        if org_rule:
            credit = (region is None or start < region[0] or end > region[1]
                      or body_credit)
        else:
            credit = bool(_third_party_near(html, (start, end)))
        candidates.append({"url": url, "credit": "credit" if credit else "none"})
    return candidates, False


def _validate_list_result(data, rule):
    """앱 결과 및 직접 목록을 자기 규칙 항목으로 다시 검사한다."""
    if (data.get("images") != [] or data.get("needsChoice") is not False
            or not isinstance(data.get("candidates"), list)
            or len(data["candidates"]) > 30
            or type(data.get("candidatesTruncated")) is not bool
            or not (data.get("kogl") is None or (
                type(data.get("kogl")) is int and 1 <= data["kogl"] <= 4))
            or "kogl" not in data or not isinstance(data.get("org"), str)
            or rule is None):
        raise FigureError("invalid_result", "list schema")
    seen = set()
    for item in data["candidates"]:
        if (not isinstance(item, dict) or set(item) != {"url", "credit"}
                or item.get("credit") not in ("none", "credit")):
            raise FigureError("invalid_result", "candidate")
        url = item["url"]
        if (not isinstance(url, str) or not url.isascii()
                or not _candidate_value_ok(url) or url in seen
                or _candidate_url(url, "", rule) != url):
            raise FigureError("invalid_result", "candidate url")
        seen.add(url)
    return data


def _store_dirs(folder):
    """`figures`·`public`가 폴더 밖을 가리키지 않는지 확인하고 만든다."""
    root = Path(folder).resolve()
    for name in ("figures", "figures/public"):
        path = root / name
        if path.is_symlink():
            raise FigureError("store_symlink", name)
        if path.exists() and not path.is_dir():
            raise FigureError("store_not_dir", name)
        path.mkdir(exist_ok=True)
        if not path.resolve().is_relative_to(root):
            raise FigureError("store_outside", name)
    return root / "figures" / "public"


def _guard_work_paths(folder):
    """`.gg-work`·receipt·계수 기록이 폴더 밖을 가리키지 않는지 쓰기 앞에 본다.

    `_store_dirs`는 그림 디렉터리만 봤다 — 링크된 `.gg-work`나 그 안의 파일로
    논문 폴더 밖에 증명·계수를 쓰던 구멍(R-2)을 여기서 닫는다.
    """
    root = Path(folder).resolve()
    work = root / ".gg-work"
    if work.is_symlink():
        raise FigureError("store_symlink", ".gg-work")
    if work.exists() and not work.is_dir():
        raise FigureError("store_not_dir", ".gg-work")
    if work.exists() and not work.resolve().is_relative_to(root):
        raise FigureError("store_outside", ".gg-work")
    for name in ("figure-receipts.jsonl", "figure-counters.json"):
        path = work / name
        if path.is_symlink():
            raise FigureError("store_symlink", name)
        if path.exists() and path.resolve().parent != work.resolve():
            raise FigureError("store_outside", name)
    return work


def _counter_path(folder):
    return Path(folder).resolve() / ".gg-work" / "figure-counters.json"


def _load_counters(folder):
    data = _read_json(_counter_path(folder))
    if not isinstance(data, dict):
        data = {}
    return {"v": 1, "bytes": int(data.get("bytes") or 0),
            "day": str(data.get("day") or ""),
            "files": int(data.get("files") or 0)}


def _store_quota(folder, size):
    """폴더당 200MB·하루 200파일(설계 §4-4). 넘으면 쓰지 않는다."""
    counters = _load_counters(folder)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if counters["day"] != today:
        counters = {"v": 1, "bytes": counters["bytes"], "day": today, "files": 0}
    if counters["files"] + 1 > MAX_STORE_FILES_PER_DAY:
        raise FigureError("store_quota_files", str(MAX_STORE_FILES_PER_DAY))
    if counters["bytes"] + size > MAX_STORE_BYTES:
        raise FigureError("store_quota_bytes", str(MAX_STORE_BYTES))
    return counters


def _store_commit(folder, counters, size):
    counters["bytes"] += size
    counters["files"] += 1
    _guard_work_paths(folder)
    _write_json_atomic(_counter_path(folder), counters)


def _store_direct(folder, sha, body, fmt):
    suffix = {"png": ".png", "jpeg": ".jpg", "gif": ".gif"}[fmt]
    public = _store_dirs(folder)
    dest = public / (sha + suffix)
    if not dest.resolve().is_relative_to(public):
        raise FigureError("store_outside", dest.name)
    if dest.is_symlink():
        raise FigureError("store_symlink", dest.name)
    if dest.exists():
        if digest_file(dest) != sha:
            raise FigureError("store_conflict", dest.name)
        return dest
    counters = _store_quota(folder, len(body))
    # 동시 실행이 같은 이름을 만들어도 서로 덮지 않게 tmp 이름을 고유하게.
    tmp = public / (".%s.%s.tmp" % (dest.name, secrets.token_hex(4)))
    try:
        with open(tmp, "xb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, dest)          # 있으면 FileExistsError — 덮지 않는다
        except FileExistsError:
            if digest_file(dest) != sha:
                raise FigureError("store_conflict", dest.name)
            return dest
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if digest_file(dest) != sha:
        raise FigureError("store_hash_mismatch", dest.name)
    _store_commit(folder, counters, len(body))
    return dest


def _receipt_path(folder):
    return Path(folder).resolve() / ".gg-work" / "figure-receipts.jsonl"


def _append_receipt(folder, *, req_id, slot, page, record, rule_id, at=None):
    """CONTRACT §5 한 줄. 덧붙이기만 한다."""
    line = {
        "v": 1,
        "at": _now_ms(None if at is None else lambda: at),
        "reqId": req_id,
        "folderHash": folder_hash(folder),
        "slot": slot,
        "page": {"url": page.get("url", ""), "sha256": page.get("sha256", ""),
                 "marks": list(page.get("marks") or [])},
        "image": {"url": record.get("url", ""),
                  "finalUrl": record.get("finalUrl", ""),
                  "origSha256": record.get("sha256"),
                  "sha256": record.get("sha256"),
                  "verdict": record.get("verdict", ""),
                  "reason": record.get("reason", ""),
                  "kogl": record.get("kogl"),
                  "org": record.get("org", ""),
                  "ruleId": rule_id},
    }
    path = _receipt_path(folder)
    _guard_work_paths(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def _verdict_one(folder, rule, html, final_page, mark_type, url, record, *,
                 resolver, allow_loopback, tls_context, limiter):
    """그림 한 장의 판정. 실패는 FigureError로 올려 호출부가 rejected로 적는다."""
    image_norm = normalize_url(url, host=rule["host"],
                              path_pattern=rule["imagePath"])
    image_final, _, _, body = http_get(
        image_norm, rule=rule, kind="image", resolver=resolver,
        allow_loopback=allow_loopback, tls_context=tls_context,
        limiter=limiter)
    sha = sha256_bytes(body)
    record["sha256"] = sha
    # 리다이렉트를 따라간 **실제** 그림 주소로 페이지와 대조한다(B-1).
    record["finalUrl"] = image_final
    fmt, width, height, frames = _image_size(body)
    if width * height > MAX_PIXELS:
        record.update(verdict="rejected", reason="too_many_pixels", sha256=None)
        return
    if frames > 1:
        record.update(verdict="rejected", reason="animated", sha256=None)
        return
    anchor = _page_references(html, image_final, final_page)
    if anchor is None:
        record.update(verdict="ambiguous", reason="not_on_page", sha256=None)
        return
    third = _third_party_near(html, anchor)
    if third:
        record.update(verdict="ambiguous", reason="third_party_mark:" + third,
                      sha256=None)
        return
    if rule.get("rule") == RULE_PAGE_QUERY_ORG:
        reading, text = _org_reading(html, image_final, final_page, rule)
        if reading == "unknown":
            record.update(verdict="ambiguous", reason="third_party_unknown", sha256=None)
            return
        body_third = THIRD_PARTY.search(text)
        if body_third:
            record.update(verdict="ambiguous",
                          reason="third_party_mark:" + body_third.group(0), sha256=None)
            return
        if reading == "hit":
            record.update(verdict="ambiguous", reason="third_party_org", sha256=None)
            return
    if mark_type is None:
        record.update(verdict="ambiguous", reason="kogl_ambiguous", sha256=None)
        return
    if mark_type not in APPROVED_KOGL:
        record.update(verdict="ambiguous", reason="kogl_%d" % mark_type,
                      sha256=None)
        return
    try:
        dest = _store_direct(folder, sha, body, fmt)
    except FigureError as exc:
        record.update(verdict="rejected", reason=exc.reason, sha256=None)
        return
    record["path"] = dest.relative_to(Path(folder).resolve()).as_posix()
    record["verdict"] = "approved"
    record["reason"] = "ok"


def fetch_direct(folder, page, images, slot, environ=None, *,
                 rules_path=None, rules=None, resolver=None,
                 allow_loopback=False, tls_context=None, limiter=None,
                 now=None, list_mode=False):
    """앱 밖 직접 받기. 목록은 페이지 GET 하나와 표준출력뿐, 작업 기록 무접촉."""
    try:
        folder, req_id = _begin_fetch(folder, slot, images, list_mode, "direct", now)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    if rules is None:
        rules = load_rules(rules_path)
    limiter = limiter or {"bytes": 0, "deadline": None}
    if limiter.get("deadline") is None:
        limiter["deadline"] = time.monotonic() + TIMEOUT_REQUEST_SECONDS
    if not rules:
        return _blocked("no_rules", "figure-sources.json 없음/깨짐")
    if not list_mode:
        try:
            _guard_work_paths(folder)
        except FigureError as exc:
            return _blocked(exc.reason, exc.detail)

    chosen = select_page_rule(page, rules)
    if chosen is None:
        return _blocked("no_rule", "허용목록에 맞는 이용조건 페이지 없음")
    rule, page_norm = chosen

    try:
        final_page, _, page_headers, page_body = http_get(
            page_norm, rule=rule, kind="page", resolver=resolver,
            allow_loopback=allow_loopback, tls_context=tls_context,
            limiter=limiter,
            follow_redirects=rule.get("rule") not in QUERY_KINDS)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    page_sha = sha256_bytes(page_body)
    html = _decode_html(page_body, page_headers)
    types, marks = kogl_marks(html)
    mark_type = next(iter(types)) if len(types) == 1 else None

    if list_mode:
        candidates, truncated = extract_candidates(html, final_page, rule)
        result = {"v": 1, "id": req_id, "status": "done", "slot": slot,
                  "needsChoice": False, "images": [], "kogl": mark_type,
                  "org": rule["org"], "candidates": candidates,
                  "candidatesTruncated": truncated}
        return _validate_list_result(result, rule)
    out_images = []
    for number, url in enumerate(images, 1):
        record = {
            "n": number,
            "url": url,
            "finalUrl": url,
            "verdict": "rejected",
            "reason": "",
            "sha256": None,
            "token": None,
            "kogl": mark_type,
            "org": rule.get("org", ""),
            "label": None,
        }
        # 한 장의 어떤 실패도 그 장의 rejected가 되고 다음 장으로 넘어간다 —
        # 예외가 main까지 올라가 JSON 없이 죽지 않는다(I-2).
        try:
            _verdict_one(folder, rule, html, final_page, mark_type, url, record,
                         resolver=resolver, allow_loopback=allow_loopback,
                         tls_context=tls_context, limiter=limiter)
        except FigureError as exc:
            record.update(verdict="rejected", reason=exc.reason, sha256=None,
                          token=None, path=None)
        except OSError as exc:
            record.update(verdict="rejected", reason="io_error", sha256=None,
                          token=None, path=None, detail=str(exc))
        out_images.append(record)
        _append_receipt(folder, req_id=req_id, slot=slot,
                        page={"url": page_norm, "sha256": page_sha,
                              "marks": marks},
                        record=record, rule_id=rule["id"],
                        at=None if now is None else now())

    approved = [item for item in out_images
                if item["verdict"] == "approved" and item.get("path")]
    needs_choice = len(approved) >= 2
    if needs_choice:
        for label, item in zip("ABC", approved):
            item["label"] = label
    notices = []
    if mark_type == 2:
        notices.append(TYPE2_NOTICE)
    return {
        "status": "done",
        "id": req_id,
        "slot": slot,
        "mode": "direct",
        "page": {"url": page_norm, "finalUrl": final_page, "sha256": page_sha,
                 "marks": marks, "kogl": mark_type, "ruleId": rule["id"]},
        "needsChoice": needs_choice,
        "notices": notices,
        "images": out_images,
    }


# --------------------------------------------------------------------------
# place — 원고에 넣기
# --------------------------------------------------------------------------
def _approved(result):
    images = result.get("images") or []
    return [item for item in images if item.get("verdict") == "approved"]


def _label_candidates(approved):
    """후보 이름을 A·B·C로 확정한다. 앱 res에 이름이 없으면 순서대로 붙인다."""
    for label, item in zip("ABC", approved):
        if not item.get("label"):
            item["label"] = label
    return approved


def _choice_path(home, folder, slot):
    return Path(home) / "choices" / folder_hash(folder) / (slot + ".json")


def _request_record_path(folder, slot):
    return Path(folder) / ".gg-work" / "figure-requests" / (slot + ".json")


def _slot_record_path(folder, slot):
    """링크·디렉터리·폴더 밖 경로는 읽기와 쓰기 모두 거절한다."""
    root = Path(folder).resolve()
    path = _request_record_path(root, slot)
    for part in (root / ".gg-work", path.parent):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise OSError("unsafe slot directory: " + str(part))
    if (path.is_symlink() or (path.exists() and not path.is_file())
            or not path.resolve().is_relative_to(root)):
        raise OSError("unsafe slot record: " + str(path))
    tmp = path.with_name(path.name + ".tmp")
    if tmp.is_symlink() or (tmp.exists() and not tmp.is_file()):
        raise OSError("unsafe slot temporary file: " + str(tmp))
    return path


SLOT_LOCK_STALE_SECONDS = 10 * 60
SLOT_LOCK_RETRY_NOTICE = "그림 요청 기록을 사용 중이거나 쓸 수 없습니다. 잠시 뒤 다시 시도해 주세요."


def _recover_slot_lock(path):
    """같은 stale inode의 회수자 하나만 rename한다. 실패하면 재시도하지 않는다."""
    named = path.lstat()
    if not stat.S_ISREG(named.st_mode) or time.time() - named.st_mtime <= SLOT_LOCK_STALE_SECONDS:
        raise OSError("slot lock still fresh or not a regular file")
    if os.name == "nt":
        import gg_winfs
        fd = gg_winfs.open_file(path, os.O_RDWR, share_delete=True)
    else:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        # 기존 플랫폼별 비차단 잠금 관례 재사용. 늦은 회수자가 새 lock을 옮기지 못한다.
        gg_update._os_lock(fd)
        try:
            opened, named = os.fstat(fd), path.lstat()
            if (not stat.S_ISREG(named.st_mode)
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                    or time.time() - named.st_mtime <= SLOT_LOCK_STALE_SECONDS):
                raise OSError("slot lock changed or still fresh")
            claimed = path.with_name(path.name + ".stale-" + secrets.token_hex(16))
            path.rename(claimed)
            claimed.unlink()
        finally:
            gg_update._os_unlock(fd)
    finally:
        os.close(fd)


def _acquire_slot_lock(folder, slot):
    """O_EXCL 슬롯 잠금. 10분 초과 mtime만 죽은 잠금으로 자동 회수한다.

    회수자는 stale inode를 비차단 잠금·identity 재검사 후 고유 이름으로 rename해
    삭제하고 O_EXCL을 딱 한 번 다시 시도한다. rename 실패/10분 이내/경합은
    기다리지 않고 일반 실패. 학생에게 파일 삭제를 요구하지 않는다.
    """
    record = _slot_record_path(folder, slot)
    record.parent.mkdir(parents=True, exist_ok=True)
    path = record.with_name(record.name + ".lock")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        _recover_slot_lock(path)
        fd = os.open(path, flags, 0o600)
    return path, fd


def _release_slot_lock(lock):
    # 윈도는 열린 파일을 지울 수 없다(공유 삭제 없이 연 핸들) — 내 잠금인지 확인한 뒤 닫고 지운다.
    path, fd = lock
    try:
        owned = os.fstat(fd)
        try:
            current = path.lstat()
        except FileNotFoundError:
            return
        mine = (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
    finally:
        os.close(fd)
    if mine:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _begin_fetch(folder, slot, images, list_mode, mode, now):
    """형식 검사 뒤, 어떤 채널·규칙 읽기·전송보다 먼저 일반 슬롯을 선기록."""
    if not SLOT.fullmatch(slot or ""):
        raise FigureError("invalid_slot", str(slot))
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise FigureError("invalid_folder", str(folder))
    if (not isinstance(images, list) or len(images) > MAX_IMAGES
            or (list_mode and images) or (not list_mode and not images)):
        raise FigureError("invalid_images", str(images))
    req_id = ("direct-" if mode == "direct" else "") + secrets.token_hex(8)
    if not list_mode:
        try:
            _write_request(folder, slot, req_id, mode, now)
        except (OSError, FigureError) as exc:
            raise FigureError("slot_record_failed", str(exc)) from exc
    return folder, req_id


def _write_request(folder, slot, req_id, mode, now=None):
    """CONTRACT §9: 이 폴더의 요청 id를 남긴다. place는 이 id로만 결과를 읽는다."""
    if not REQUEST_ID.match(req_id or ""):
        raise FigureError("invalid_id", str(req_id))
    payload = {"v": 1, "slot": slot, "reqId": req_id, "mode": mode,
               "at": _now_ms(now)}
    lock = _acquire_slot_lock(folder, slot)
    try:
        _write_json_atomic(_slot_record_path(folder, slot), payload)
    finally:
        _release_slot_lock(lock)
    return payload


def _read_request(folder, slot):
    try:
        path = _slot_record_path(folder, slot)
    except OSError:
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    if data.get("slot") != slot or not REQUEST_ID.match(str(data.get("reqId") or "")):
        return None
    if data.get("mode") not in ("app", "direct"):
        return None
    return data


def _result_file(home, folder, req):
    """요청 id로 지정해서 읽는다 — 슬롯·mtime으로 고르지 않는다."""
    req_id = req["reqId"]
    if req["mode"] == "direct":
        return Path(folder) / ".gg-work" / "figure-results" / (req_id + ".json")
    return Path(home) / "res" / (req_id + ".res.json")


def _load_result(home, folder, req):
    path = _result_file(home, folder, req)
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    if data.get("id") != req["reqId"]:
        return None
    return data


def _heading_level(line):
    match = HEADING.match(line)
    return len(match.group(1)) if match else 0


def _section_bounds(lines, heading):
    target = heading.strip().lstrip("#").strip()
    for index, line in enumerate(lines):
        if _heading_level(line) and line.strip().lstrip("#").strip() == target:
            level = _heading_level(line)
            end = len(lines)
            for probe in range(index + 1, len(lines)):
                other = _heading_level(lines[probe])
                if other and other <= level:
                    end = probe
                    break
            return index, end
    raise FigureError("heading_not_found", heading)


def _next_caption_number(text):
    numbers = [int(value) for value in CAPTION_NUMBER.findall(text)]
    return max(numbers, default=0) + 1


def _default_title(heading):
    title = TITLE_PREFIX.sub("", heading.strip().lstrip("#").strip()).strip()
    return title or heading.strip().lstrip("#").strip()


def _insert_block(text, heading, block):
    """절 끝(마지막 비어 있지 않은 줄 뒤)에 블록을 붙인다."""
    lines = text.splitlines()
    start, end = _section_bounds(lines, heading)
    last = start
    for probe in range(start + 1, end):
        if lines[probe].strip():
            last = probe
    insert_at = last + 1
    tail = list(block)
    if insert_at < len(lines) and lines[insert_at].strip():
        tail.append("")
    new_lines = lines[:insert_at] + [""] + tail + lines[insert_at:]
    return "\n".join(new_lines).rstrip("\n") + "\n"


def _manuscript(folder, rel):
    path = Path(folder) / rel
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(folder).resolve()):
        raise FigureError("manuscript_outside", rel)
    if not resolved.is_file():
        raise FigureError("manuscript_missing", rel)
    return resolved


def _block_for(item, number, title, org, direct):
    if direct:
        image_line = "![그림 %d. %s (출처 : %s)](%s)" % (
            number, title, org, item["path"])
        ref = item["path"]
    else:
        image_line = "![](app-figure:%s)" % item["sha256"]
        ref = "app-figure:" + item["sha256"]
    caption = "그림 %d. %s (출처 : %s)" % (number, title, org)
    return [image_line, "", caption], ref


def _section_text(text, heading):
    lines = text.splitlines()
    start, end = _section_bounds(lines, heading)
    return "\n".join(lines[start:end])


def _slot_photo(folder, slot, heading, manuscript, notice=None):
    try:
        path = _manuscript(folder, manuscript)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    result = _photo_placeholder(folder, path, heading,
                                path.read_text(encoding="utf-8"), slot)
    if notice:
        result["notice"] = notice
    return result


def place(folder, slot, heading, *, choose=None, title=None, org=None,
          manuscript="build/검토전_본문.md", environ=None, images=None,
          direct=None, home=None):
    if not SLOT.fullmatch(slot or ""):
        return _blocked("invalid_slot", str(slot))
    options = dict(choose=choose, title=title, org=org, manuscript=manuscript,
                   environ=environ, images=images, direct=direct, home=home)
    if images:
        return _place(folder, slot, heading, **options)
    initial = _read_request(folder, slot)
    if initial is None:
        return _slot_photo(folder, slot, heading, manuscript)
    try:
        lock = _acquire_slot_lock(folder, slot)
    except OSError:
        return _slot_photo(folder, slot, heading, manuscript,
                           SLOT_LOCK_RETRY_NOTICE + " 사진 자리로 남깁니다.")
    try:
        current = _read_request(folder, slot)
        if current is None or current["reqId"] != initial["reqId"]:
            return _slot_photo(folder, slot, heading, manuscript,
                               "새 그림 요청이 시작됐습니다. 새 요청 결과를 기다려 주세요.")
        # usedAt 재쓰기부터 원고 삽입까지 잠금을 유지한다.
        return _place(folder, slot, heading, request=current, **options)
    finally:
        _release_slot_lock(lock)


def _place(folder, slot, heading, *, choose=None, title=None, org=None,
           manuscript="build/검토전_본문.md", environ=None, images=None,
           direct=None, home=None, request=None):
    """직접 그림 또는 잠금 안에서 확인된 슬롯의 승인 결과만 원고에 넣는다."""
    if not SLOT.match(slot or ""):
        return _blocked("invalid_slot", str(slot))
    folder = Path(folder)
    try:
        path = _manuscript(folder, manuscript)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    text = path.read_text(encoding="utf-8")

    if images:
        if direct is None:
            direct = not _is_app_mode(environ)
        if not org:
            return _blocked("no_org", "직접 넣기에는 출처 기관이 필요하다")
        refs = []
        for rel in images:
            candidate = (folder / rel).resolve()
            if not candidate.is_relative_to(folder.resolve()) or not candidate.is_file():
                return _blocked("image_missing", rel)
            refs.append({"path": candidate.relative_to(folder.resolve()).as_posix(),
                         "sha256": digest_file(candidate)})
        result = {"status": "done", "slot": slot, "images": [
            {"n": index, "url": ref["path"], "finalUrl": ref["path"],
             "verdict": "approved", "reason": "ok", "sha256": ref["sha256"],
             "token": "app-figure:" + ref["sha256"], "kogl": None,
             "org": org, "label": None, "path": ref["path"]}
            for index, ref in enumerate(refs, 1)],
            "needsChoice": len(refs) >= 2}
        direct = True
    else:
        env_home = home if home is not None else _figure_home(environ)
        req = request
        if req is None:
            return _photo_placeholder(folder, path, heading, text, slot)
        try:
            req = dict(req, usedAt=_now_ms(None))
            _write_json_atomic(_slot_record_path(folder, slot), req)
        except OSError:
            return _photo_placeholder(folder, path, heading, text, slot)
        if req["mode"] == "app" and env_home is None:
            return _blocked("no_channel", "앱 결과를 읽으려면 KNUAF_FIGURE_HOME이 필요하다")
        result = _load_result(env_home, folder, req)
        if result is None:
            return _photo_placeholder(folder, path, heading, text, slot)
        direct = req["mode"] == "direct"

    approved = _label_candidates(_approved(result))
    if not approved:
        return _photo_placeholder(folder, path, heading, text, slot)

    if len(approved) >= 2:
        choice = None
        choice_home = home or _figure_home(environ)
        if choice_home is not None:
            choice = _read_json(_choice_path(choice_home, folder, slot))
            if isinstance(choice, dict) and choice.get("reqId") != result.get("id"):
                # 다른 요청의 후보표 — 이 결과에 쓰지 않는다.
                choice = None
        if choose is None:
            return _needs_choice(slot, result, approved, choice)
        return _place_chosen(folder, path, heading, text, slot, choose, result,
                             approved, choice, title, org, direct,
                             app_mode=_is_app_mode(environ))

    item = approved[0]
    return _place_one(folder, path, heading, text, slot, item,
                      result, title, org, direct)


def _place_one(folder, path, heading, text, slot, item, result, title, org, direct):
    org = org or item.get("org") or (result.get("page") or {}).get("org") or ""
    if not org:
        return _blocked("no_org", "승인 그림에 기관명 없음")
    number = _next_caption_number(text)
    title_text = title or _default_title(heading)
    block, ref = _block_for(item, number, title_text, org, direct)
    section = _section_text(text, heading)
    if ref in section:
        return {"status": "already_placed", "slot": slot, "ref": ref,
                "line": block[0]}
    try:
        updated = _insert_block(text, heading, block)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    path.write_text(updated, encoding="utf-8")
    return {"status": "placed", "slot": slot, "number": number,
            "ref": ref, "line": block[0], "caption": block[-1]}


def _needs_choice(slot, result, approved, choice):
    labels = [item.get("label") for item in approved]
    candidates = []
    if isinstance(choice, dict) and isinstance(choice.get("candidates"), list):
        for entry in choice["candidates"]:
            if isinstance(entry, dict) and entry.get("label") in labels:
                candidates.append({"label": entry["label"],
                                   "sha256": entry.get("sha256"),
                                   "desc": entry.get("desc", "")})
    if not candidates:
        for item in approved:
            candidates.append({"label": item.get("label"),
                               "sha256": item.get("sha256"),
                               "desc": "%s %s" % (item.get("org") or "",
                                                  item.get("finalUrl") or "")})
    question = None
    if isinstance(choice, dict) and isinstance(choice.get("question"), str):
        question = choice["question"]
    if not question or "A·B·C 중 하나만 적어 주세요" not in question:
        question = ("그림 후보가 %d장입니다. A·B·C 중 하나만 적어 주세요. "
                    "(예: A) — 고르기 전에는 원고를 바꾸지 않습니다." % len(candidates))
    return {"status": "needs_choice", "slot": slot,
            "reqId": result.get("id") or result.get("reqId"),
            "question": question, "candidates": candidates}


def _place_chosen(folder, path, heading, text, slot, choose, result, approved,
                  choice, title, org, direct, *, app_mode=True):
    """CONTRACT §9: 앱 안은 학생 메시지 일치, 앱 밖은 라벨만 맞으면 통과."""
    labels = [item.get("label") for item in approved]
    picked = (choose or "").strip()
    if picked not in labels:
        return {"status": "reask", "slot": slot,
                "reason": "not_a_candidate", "choose": choose,
                "candidates": labels}
    if app_mode:
        if not isinstance(choice, dict):
            return _blocked("no_student_message", "후보표 없음")
        message = choice.get("studentMessage")
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            return _blocked("no_student_message", "앱 기록에 학생 메시지 없음")
        if message["text"].strip() != picked:
            return {"status": "reask", "slot": slot,
                    "reason": "student_message_mismatch",
                    "choose": choose, "studentMessage": message["text"],
                    "candidates": labels}
    if isinstance(choice, dict) and choice.get("candidates"):
        candidate = next((entry for entry in choice["candidates"]
                          if isinstance(entry, dict) and entry.get("label") == picked),
                         None)
        item = next(entry for entry in approved if entry.get("label") == picked)
        if candidate is None or candidate.get("sha256") != item.get("sha256"):
            return _blocked("candidate_mismatch", "후보표와 승인 그림 해시 불일치")
    item = next(entry for entry in approved if entry.get("label") == picked)
    return _place_one(folder, path, heading, text, slot, item, result, title,
                      org, direct)


def _photo_placeholder(folder, path, heading, text, slot):
    what = _default_title(heading)
    line = "※ 이 자리에 %s 사진 추가 필요" % what
    try:
        section = _section_text(text, heading)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    if line in section:
        return {"status": "already_placed", "slot": slot, "line": line}
    try:
        updated = _insert_block(text, heading, [line])
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    path.write_text(updated, encoding="utf-8")
    return {"status": "needs_photo", "slot": slot, "line": line,
            "note": "승인 그림 0장 — 사진 자리 한 줄만 넣었다"}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _parser():
    ap = argparse.ArgumentParser(description="그림 받기·넣기 (앱 통로/직접 받기)")
    sub = ap.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="앱에 그림을 요청하거나 직접 받는다")
    fetch.add_argument("folder")
    fetch.add_argument("--slot", required=True)
    fetch.add_argument("--page", required=True, help="이용조건 페이지 URL")
    fetch.add_argument("--image", action="append", default=[], dest="images",
                       help="그림 URL (최대 3장)")
    fetch.add_argument("--list", action="store_true", dest="list_mode",
                       help="그림을 받지 않고 페이지의 그림 주소 후보만 읽는다")
    fetch.add_argument("--direct", action="store_true",
                       help="앱 밖에서 직접 받기(KNUAF_GUI=1이면 무시·거절)")

    put = sub.add_parser("place", help="원고에 넣기(1장 즉시·2장↑ 고르기·0장 사진 자리)")
    put.add_argument("folder")
    put.add_argument("--slot", required=True)
    put.add_argument("--heading", required=True, help="원고의 절 제목(# 제외)")
    put.add_argument("--choose", default=None, help="A·B·C 중 하나")
    put.add_argument("--title", default=None)
    put.add_argument("--org", default=None)
    put.add_argument("--manuscript", default="build/검토전_본문.md")
    put.add_argument("--image", action="append", default=[], dest="images",
                     help="폴더 기준 그림 경로(직접 넣기)")
    return ap


def main(argv=None):
    try:
        return _dispatch(argv)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — 남은 예외도 JSON 한 줄로
        out = _blocked("internal", "%s: %s" % (type(exc).__name__, exc))
        print(json.dumps(out, ensure_ascii=False))
        return 2


def _dispatch(argv=None):
    args = _parser().parse_args(argv)
    app_mode = _is_app_mode()
    if args.command == "fetch":
        if app_mode:
            if args.direct:
                try:
                    _begin_fetch(args.folder, args.slot, args.images, args.list_mode,
                                 "app", None)
                    result = _blocked("gui_no_direct",
                                      "KNUAF_GUI=1이면 직접 받을 수 없다")
                except FigureError as exc:
                    result = _blocked(exc.reason, exc.detail)
            else:
                result = fetch_app(args.folder, args.page, args.images, args.slot,
                                   list_mode=args.list_mode)
        else:
            result = fetch_direct(args.folder, args.page, args.images, args.slot,
                                  list_mode=args.list_mode)
            if not args.list_mode and result.get("status") == "done":
                req_id = result.get("id")
                _write_json_atomic(Path(args.folder) / ".gg-work"
                                   / "figure-results" / (req_id + ".json"), result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "done" else 2
    result = place(args.folder, args.slot, args.heading, choose=args.choose,
                   title=args.title, org=args.org, manuscript=args.manuscript,
                   images=args.images or None)
    print(json.dumps(result, ensure_ascii=False))
    status = result.get("status")
    return 2 if status in ("blocked", "reask") else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
