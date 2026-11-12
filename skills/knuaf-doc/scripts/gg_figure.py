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
import sys
import threading
import time
import urllib.parse
from pathlib import Path

VERSION = "0.4.11"

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


def request_payload(req_id, turn_id, slot, page, images):
    """요청 JSON. 이 밖의 필드가 없어야 앱이 받는다(CONTRACT §3)."""
    if not REQUEST_ID.match(req_id or ""):
        raise FigureError("invalid_id", str(req_id))
    if not SLOT.match(slot or ""):
        raise FigureError("invalid_slot", str(slot))
    if not isinstance(page, str) or not page.startswith("https://"):
        raise FigureError("invalid_page", str(page))
    if not images or len(images) > MAX_IMAGES:
        raise FigureError("invalid_images", str(len(images or ())))
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


def read_result(home, req_id):
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
              sleep=time.sleep, poll_seconds=POLL_SECONDS):
    """앱에 요청하고 결과를 기다린다. 어떤 경우에도 직접 받지 않는다."""
    env = os.environ if environ is None else environ
    home = _figure_home(env)
    if home is None:
        return _blocked("no_channel", "KNUAF_FIGURE_HOME 없음")
    ok, reason = channel_state(home, _now_ms(now))
    if not ok:
        return _blocked(reason, "앱 하트비트 없음/낡음 — 기다리지 않는다")
    turn_id, reason = turn_state(home, folder, now)
    if not turn_id:
        return _blocked(reason)
    req_id = secrets.token_hex(8)
    try:
        payload = request_payload(req_id, turn_id, slot, page, images)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    dest = Path(folder) / ".knuaf-gui" / "figure-requests" / (req_id + ".req.json")
    try:
        _write_json_atomic(dest, payload)
    except OSError as exc:
        return _blocked("request_write_failed", str(exc))
    deadline = time.monotonic() + poll_seconds
    while True:
        try:
            result = read_result(home, req_id)
        except FigureError as exc:
            out = _blocked(exc.reason, exc.detail)
            out["reqId"] = req_id
            return out
        if result is not None:
            result.setdefault("reqId", req_id)
            return result
        if time.monotonic() >= deadline:
            out = _blocked("timeout", "결과 없음 (%.0f초)" % poll_seconds)
            out["reqId"] = req_id
            return out
        sleep(0.2)


# --------------------------------------------------------------------------
# 앱 밖 직접 받기
# --------------------------------------------------------------------------
def load_rules(path=None):
    """허용목록. 없거나 깨지면 빈 목록 = 전부 거절."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "references" / "figure-sources.json"
    data = _read_json(path)
    if not isinstance(data, dict) or data.get("v") != 1:
        return []
    rules = data.get("rules")
    if not isinstance(rules, list):
        return []
    out = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if not all(isinstance(rule.get(k), str) for k in ("id", "host", "pagePath", "imagePath")):
            continue
        if rule.get("rule") != "page_kogl_applies_to_body_images":
            continue
        try:
            page_re = re.compile(rule["pagePath"])
            image_re = re.compile(rule["imagePath"])
        except re.error:
            continue
        out.append({
            "id": rule["id"],
            "host": rule["host"].rstrip(".").lower(),
            "org": rule.get("org") if isinstance(rule.get("org"), str) else "",
            "pagePath": page_re,
            "imagePath": image_re,
            "rule": rule["rule"],
        })
    return out


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
             tls_context=None, limiter=None):
    """고정 IP + 수동 리다이렉트(≤3) + 상한. (final_url, status, headers, body)."""
    limit = limiter or {"bytes": 0, "deadline": None}
    if limit.get("deadline") is None:
        limit["deadline"] = time.monotonic() + TIMEOUT_REQUEST_SECONDS
    pattern = rule["pagePath"] if kind == "page" else rule["imagePath"]
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
    record["path"] = str(dest.relative_to(Path(folder).resolve()))
    record["verdict"] = "approved"
    record["reason"] = "ok"


def fetch_direct(folder, page, images, slot, environ=None, *,
                 rules_path=None, rules=None, resolver=None,
                 allow_loopback=False, tls_context=None, limiter=None,
                 now=None):
    """앱 밖 직접 받기. 승인된 그림만 폴더 figures/public에 저장한다."""
    folder = Path(folder)
    if rules is None:
        rules = load_rules(rules_path)
    limiter = limiter or {"bytes": 0, "deadline": None}
    if limiter.get("deadline") is None:
        limiter["deadline"] = time.monotonic() + TIMEOUT_REQUEST_SECONDS
    if not rules:
        return _blocked("no_rules", "figure-sources.json 없음/깨짐")
    try:
        _guard_work_paths(folder)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)

    page_norm = None
    rule = None
    for candidate in rules:
        try:
            page_norm = normalize_url(page, host=candidate["host"],
                                      path_pattern=candidate["pagePath"])
        except FigureError:
            continue
        rule = candidate
        break
    if rule is None:
        return _blocked("no_rule", "허용목록에 맞는 이용조건 페이지 없음")

    try:
        final_page, _, page_headers, page_body = http_get(
            page_norm, rule=rule, kind="page", resolver=resolver,
            allow_loopback=allow_loopback, tls_context=tls_context,
            limiter=limiter)
    except FigureError as exc:
        return _blocked(exc.reason, exc.detail)
    page_sha = sha256_bytes(page_body)
    html = _decode_html(page_body, page_headers)
    types, marks = kogl_marks(html)
    mark_type = next(iter(types)) if len(types) == 1 else None

    req_id = "direct-" + secrets.token_hex(8)
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


def _write_request(folder, slot, req_id, mode, now=None):
    """CONTRACT §9: 이 폴더의 요청 id를 남긴다. place는 이 id로만 결과를 읽는다."""
    if not REQUEST_ID.match(req_id or ""):
        raise FigureError("invalid_id", str(req_id))
    payload = {"v": 1, "slot": slot, "reqId": req_id, "mode": mode,
               "at": _now_ms(now)}
    _write_json_atomic(_request_record_path(folder, slot), payload)
    return payload


def _read_request(folder, slot):
    data = _read_json(_request_record_path(folder, slot))
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


def place(folder, slot, heading, *, choose=None, title=None, org=None,
          manuscript="build/검토전_본문.md", environ=None, images=None,
          direct=None, home=None):
    """그 절의 승인 그림 수로 분기(1장 즉시·2장↑ 고르기·0장 사진 자리)."""
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
            refs.append({"path": str(candidate.relative_to(folder.resolve())),
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
        req = _read_request(folder, slot)
        if req is None:
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
                result = _blocked("gui_no_direct",
                                  "KNUAF_GUI=1이면 직접 받을 수 없다")
            else:
                result = fetch_app(args.folder, args.page, args.images, args.slot)
        else:
            result = fetch_direct(args.folder, args.page, args.images, args.slot)
            if result.get("status") == "done":
                req_id = result.get("id")
                _write_json_atomic(Path(args.folder) / ".gg-work"
                                   / "figure-results" / (req_id + ".json"), result)
        if result.get("status") == "done":
            # CONTRACT §9: place가 이 id로만 결과를 고른다.
            try:
                _write_request(args.folder, args.slot, result["id"],
                               "app" if app_mode else "direct")
            except FigureError as exc:
                result = _blocked(exc.reason, exc.detail)
        elif result.get("id") or result.get("reqId"):
            # 새 요청은 나갔는데 rejected/timeout — 옛 승인 요청의 기록은 더
            # 이상 이 슬롯의 결과가 아니다. 남기면 place가 옛 그림을 넣는다(I-3).
            try:
                _request_record_path(args.folder, args.slot).unlink()
            except OSError:
                pass
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
