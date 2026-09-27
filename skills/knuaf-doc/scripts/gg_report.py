"""학생 오류·불편 신고 CLI (설계 r1 + r2 + r3 수용).

학생이 "신고할래 / 불편해요 / 이런 기능 있었으면"이라고 말할 때 AI가
신고 초안을 만들고, 미리보기 그대로 학생이 확인한 뒤에만 배포된 수신
Worker로 보낸다.  공개 경로(issue)는 로컬 가림 + Worker 가림 2중,
비공개 경로(private)는 가림 없이 개발자 채널로 간다.

표준 라이브러리만 쓴다.  네트워크는 urllib.request + 리다이렉트 미추종
opener 하나다.  모든 파일 쓰기는 새 파일만('x' 모드), UTF-8 명시.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DRAFT_SCHEMA = "knuaf-doc/student-report-draft@1"
RECEIPT_SCHEMA = "knuaf-doc/student-report-receipt@1"
CLAIM_SCHEMA = "knuaf-doc/student-report-claim@1"

# r2 D1-04: 제품 수신처는 이 URL 하나로 고정. 임의 override는 없다.
PRODUCT_ENDPOINT = "https://knuaf-report.knuaf-report-worker.workers.dev/report"
TEST_ENDPOINT_ENV = "KNUAF_REPORT_TEST_ENDPOINT"
_LOOPBACK_BASE = re.compile(r"http://(127\.0\.0\.1|localhost):[0-9]+\Z")
_LOOPBACK_ENDPOINT = re.compile(
    r"http://(127\.0\.0\.1|localhost):[0-9]+/report\Z")

USER_AGENT = "knuaf-doc-report/1"
TIMEOUT_SECONDS = 20
MAX_BODY_BYTES = 16 * 1024
PRIVATE_COMPOSITE_MAX = 1900  # r2 D1-02: Worker sendPrivate의 slice(0,1900)

KINDS = ("issue", "private")
CATEGORIES = ("bug", "feature", "inconvenience", "other")
INPUT_FIELDS = (
    "kind", "category", "title", "description", "steps", "error_message")
TEXT_FIELDS = ("title", "description", "steps", "error_message")
OPTIONAL_FIELDS = ("steps", "error_message")
PAYLOAD_KEYS = INPUT_FIELDS + ("skill_version", "environment")
# F1-01: send 재검사 대상 — 공개 이슈 본문·제목에 들어가는 모든 문자열.
PUBLIC_SCAN_FIELDS = TEXT_FIELDS + ("skill_version", "environment")
# 로컬 보수 상한(문자 아닌 UTF-16 코드 단위, r2 D1-03). issue는 공개
# 저장소에 원고·긴 자료가 붙지 못하게 Worker보다 낮게 둔다.
FIELD_LIMITS = {
    "issue": {"title": 120, "description": 2000, "steps": 2000,
              "error_message": 4000},
    "private": {"title": 120, "description": 8000, "steps": 8000,
                "error_message": 8000},
}
EXTRA_LIMITS = {"skill_version": 64, "environment": 200}

CATEGORY_LABELS = {
    "bug": "오류", "feature": "기능 요청",
    "inconvenience": "불편", "other": "기타",
}
DESTINATION_LABELS = {
    "issue": "공개 GitHub 이슈(starceas/knuaf-doc)",
    "private": "개발자 비공개 채널",
}

MASK_TOKENS = (
    "[경로 가림]", "[파일명 가림]", "[이메일 가림]", "[주민번호 가림]",
    "[전화번호 가림]", "[학번 가림]",
)
WARN_WORDS = (
    "성명", "이름은", "이름:", "학번", "주소", "연락처", "전화",
    "생년월일", "주민",
)

# r2 D1-01 + L1-2: 경로 시작점 앞에 올 수 있는 경계 문자
# (공백·따옴표·괄호·= , ; : ·줄 시작).
_BOUNDARY = set(" \t\n\r\"'“‘`([{<=,;:")
# L1-2: 경계와 무관하게 언제나 경로 시작으로 보는 고정 접두
# (한글 바로 뒤 "폴더/Users/..." 대비).
_ALWAYS_PREFIXES = (
    "/Users/", "/home/", "/private/", "/Volumes/", "/var/folders/")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "`": "`"}
_FILENAME_EXT = (
    r"(?:docx|doc|hwp|hwpx|xlsx|xls|csv|pdf|pptx|json|md|txt|zip)")
# L1-1: 토큰은 공백·따옴표·괄호·,:=로 구분하고, 확장자 뒤에는 ASCII
# 영숫자·밑줄이 오지 않아야 한다(한글 조사·구두점은 가린다).
_FILENAME_TOKEN = re.compile(
    r"(?<![^\s\"'“‘’`()\[\]{}<>,:=])"
    r"[^\s\"'“‘’`()\[\]{}<>,:=]+\." + _FILENAME_EXT +
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RRN = re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")
# r2 D1-01: 구분자 없는 번호도 가린다(0212345678).
_MOBILE = re.compile(r"(?<!\d)01[016789][-.\s)]*\d{3,4}[-.\s)]*\d{4}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0[2-6]\d?[-.\s)]*\d{3,4}[-.\s)]*\d{4}(?!\d)")
_SID8 = re.compile(r"(?<!\d)(19|20)\d{6}(?!\d)")
_SID_AFTER = re.compile(
    r"(학번[^\d\n]{0,20}?)(?<!\d)(\d[\d-]{3,12}\d)(?!\d)")

# F1-02: ECMAScript String.prototype.trim이 지우는 문자(WhiteSpace +
# LineTerminator). Python str.strip()과 다르다(U+FEFF 포함, \x1c-\x1f·
# \x85 미포함). Worker와 같은 trim을 쓰기 위해 명시한다.
_JS_TRIM = (
    "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


def js_trim(text):
    """Worker(JS) value.trim()과 같은 결과."""
    return text.strip(_JS_TRIM)


class ReportError(Exception):
    """계약상 거부 사유. code가 출력의 error 값이 된다."""

    def __init__(self, code, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _utc_now():
    return (datetime.now(timezone.utc)
            .replace(microsecond=0).isoformat()
            .replace("+00:00", "Z"))


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _u16len(text):
    """r2 D1-03: 모든 상한은 UTF-16 코드 단위(JS String.length)로 잰다."""
    return len(text.encode("utf-16-le")) // 2


def _u16slice(text, units):
    """JS slice(0, n)와 같은 UTF-16 코드 단위 자르기."""
    raw = text.encode("utf-16-le")[: units * 2]
    return raw.decode("utf-16-le", errors="surrogatepass")


def _line_end(text, pos):
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _path_spans(text):
    """r2 D1-01 절대 경로 범위를 (start, end) 목록으로 돌려준다.

    시작점: POSIX는 경계(공백·따옴표·괄호·줄 시작) 뒤의 '/'로 시작하고
    그 구역 안에 '/'가 하나 더 있는 모든 경로(접두 목록 없음), '~/' ,
    Windows 드라이브 'X:\\'·'X:/', UNC '\\\\'.  끝: 시작점 바로 앞이
    따옴표면 짝 따옴표까지, 아니면 그 줄 끝까지.

    L1-2: 경계 문자에 = , ; : 를 추가하되 "://" 뒤(URL)는 제외한다.
    고정 접두(/Users/ /home/ /private/ /Volumes/ /var/folders/,
    드라이브+":\\Users\\"·":/Users/")는 경계 없이 언제나 시작.
    L1-3: 경계 뒤 '/' 바로 뒤가 공백이거나 '/'면 경로로 보지 않는다.
    """
    spans = []
    i, n = 0, len(text)
    while i < n:
        start = None
        need_second = False
        ch = text[i]
        if any(text.startswith(p, i) for p in _ALWAYS_PREFIXES):
            start = i
        elif ch.isalpha() and text[i + 1:i + 9].lower() == ":\\users\\":
            start = i
        elif ch.isalpha() and text[i + 1:i + 8].lower() == ":/users/":
            start = i
        else:
            prev = text[i - 1] if i else ""
            boundary = i == 0 or prev in _BOUNDARY
            if boundary:
                if ch == "/" and not (
                        prev == ":" and text[i + 1:i + 2] == "/"):
                    if text[i + 1:i + 2] != "/" \
                            and not text[i + 1:i + 2].isspace():
                        start, need_second = i, True
                elif text.startswith("~/", i):
                    start = i
                elif text.startswith("\\\\", i):
                    start = i
                elif ch.isalpha() and text[i + 1:i + 3] in (":\\", ":/"):
                    start = i
        if start is None:
            i += 1
            continue
        prev = text[i - 1] if i else ""
        if prev in _QUOTE_PAIRS:
            end = text.find(_QUOTE_PAIRS[prev], i)
            end = _line_end(text, i) if end < 0 else end
        else:
            end = _line_end(text, i)
        if need_second and "/" not in text[i + 1:end]:
            i += 1
            continue
        spans.append((start, end))
        i = end
    return spans


def _mask_paths(text):
    spans = _path_spans(text)
    if not spans:
        return text, 0
    out, pos = [], 0
    for start, end in spans:
        out.append(text[pos:start])
        out.append("[경로 가림]")
        pos = end
    out.append(text[pos:])
    return "".join(out), len(spans)


def _mask_quoted(text, open_q, close_q):
    inner = r"[^" + re.escape(close_q) + r"\n]*?"
    if open_q == close_q:
        inner = r"[^" + re.escape(open_q) + r"\n]*?"
    pattern = re.compile(
        re.escape(open_q) + r"(" + inner + r"\." + _FILENAME_EXT + inner +
        r")" + re.escape(close_q), re.IGNORECASE)
    return pattern.subn(
        lambda m: open_q + "[파일명 가림]" + close_q, text)


def _mask_filenames(text):
    total = 0
    for open_q, close_q in (('"', '"'), ("'", "'"), ("`", "`"),
                            ("“", "”"), ("‘", "’")):
        text, n = _mask_quoted(text, open_q, close_q)
        total += n
    text, n = _FILENAME_TOKEN.subn("[파일명 가림]", text)
    return text, total + n


def _mask_ids(text):
    text, n8 = _SID8.subn("[학번 가림]", text)
    text, nk = _SID_AFTER.subn(
        lambda m: m.group(1) + "[학번 가림]", text)
    return text, n8 + nk


def redact(text):
    """issue 본문 가림. (가림된 텍스트, 유형별 횟수)를 돌려준다.

    순서(r2 D1-01): 경로 → 파일명 → 이메일 → 주민번호 → 전화 → 학번.
    숫자 경계는 \\b 대신 (?<!\\d)…(?!\\d)를 써서 한글 인접도 걸린다.
    가림 표식 "[… 가림]" 자체는 어떤 규칙에도 걸리지 않는다.
    """
    counts = {}
    text, counts["path"] = _mask_paths(text)
    text, counts["filename"] = _mask_filenames(text)
    text, counts["email"] = _EMAIL.subn("[이메일 가림]", text)
    text, counts["resident_number"] = _RRN.subn("[주민번호 가림]", text)
    text, nm = _MOBILE.subn("[전화번호 가림]", text)
    text, nl = _LANDLINE.subn("[전화번호 가림]", text)
    counts["phone"] = nm + nl
    text, counts["student_id"] = _mask_ids(text)
    return text, counts


def environment():
    """호스트명·사용자명·경로를 넣지 않는 환경 문자열(≤200 UTF-16)."""
    py = platform.python_version()
    return _u16slice(
        "%s %s %s; Python %s" % (
            platform.system(), platform.release(), platform.machine(), py),
        EXTRA_LIMITS["environment"])


def _skill_version(plugin_root, skill_root=None):
    """스킬 루트 version.json 엄격 판독 → plugin.json → 'unknown' (U1).

    version.json은 schema/version/repo 세 키만 허용하고 중복 키·타입·
    repo 불일치는 판독 불가다(plugin.json으로 내려간다).
    skill_root 기본값은 이 파일의 스킬 루트(parents[1]).
    """
    root = (Path(__file__).resolve().parents[1]
            if skill_root is None else Path(skill_root))
    version = _read_version_json(root / "version.json")
    if version is None:
        try:
            doc = json.loads(
                (Path(plugin_root) / ".codex-plugin" / "plugin.json")
                .read_text(encoding="utf-8"))
            version = doc["version"]
            if not isinstance(version, str):
                version = "unknown"
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            version = "unknown"
    return _u16slice("knuaf-doc " + str(version),
                     EXTRA_LIMITS["skill_version"])


def _read_version_json(path):
    """knuaf-doc/version@1 엄격 판독. 판독 불가면 None."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    pairs = []
    try:
        doc = json.loads(raw,
                         object_pairs_hook=lambda p: pairs.append(p) or dict(p))
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict) or len(pairs) != 1:
        return None
    top = pairs[0]
    if sorted(k for k, _v in top) != ["repo", "schema", "version"]:
        return None
    if len(set(k for k, _v in top)) != 3:
        return None
    if (doc.get("schema") != "knuaf-doc/version@1"
            or doc.get("repo") != "starceas/knuaf-doc"
            or not isinstance(doc.get("version"), str)
            or re.fullmatch(
                r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\."
                r"(0|[1-9][0-9]{0,8})", doc["version"],
                flags=re.ASCII) is None):
        return None
    return doc["version"]


def _private_composite(payload):
    """r2 D1-02: Worker sendPrivate의 합성 문자열을 그대로 재현한다."""
    text = ("**[비공개 신고] " + payload["title"] + "**\n분류: "
            + payload["category"] + "\n\n" + payload["description"])
    if payload.get("steps"):
        text += "\n\n단계: " + payload["steps"]
    if payload.get("error_message"):
        text += "\n\n오류: " + payload["error_message"]
    if payload.get("skill_version"):
        # F1-06: Worker sendPrivate는 버전 앞에 줄바꿈 하나만 둔다.
        text += "\n버전: " + payload["skill_version"]
    return text


def worker_validate(payload):
    """배포 Worker src/index.js의 validate()를 Python으로 옮긴 것.

    trim + UTF-16 slice를 적용한 정규 결과를 돌려준다. r3 R2-01: 비교는
    payload에 실제로 있는 필드만 대상으로 한다(Worker가 채우는 빈
    기본값은 비교하지 않는다).
    """
    if not isinstance(payload, dict):
        return {"error": "invalid_json"}

    def _s(value, limit):
        if not isinstance(value, str):
            return ""
        return _u16slice(js_trim(value), limit)

    kind = payload.get("kind")
    if kind not in KINDS:
        return {"error": "invalid_kind"}
    category = payload.get("category")
    if category not in CATEGORIES:
        category = "other"
    title = _s(payload.get("title"), 120)
    description = _s(payload.get("description"), 8000)
    if not title or not description:
        return {"error": "missing_fields"}
    return {
        "kind": kind,
        "category": category,
        "title": title,
        "description": description,
        "steps": _s(payload.get("steps"), 8000),
        "error_message": _s(payload.get("error_message"), 8000),
        "skill_version": _s(payload.get("skill_version"), 64),
        "environment": _s(payload.get("environment"), 200),
    }


def _warnings(payload):
    """가림 뒤 텍스트에 남은 개인정보 의심 단어(전송을 막지는 않는다)."""
    warnings = []
    for field in TEXT_FIELDS:
        if field not in payload:
            continue
        stripped = payload[field]
        for token in MASK_TOKENS:
            stripped = stripped.replace(token, "")
        for word in WARN_WORDS:
            if word in stripped:
                warnings.append({"field": field, "word": word})
    return warnings


def _desired_destination(env_value):
    """지금 초안을 만들면 기록될 destination. r2 D1-04."""
    if env_value is not None and env_value != "":
        if not _LOOPBACK_BASE.fullmatch(env_value):
            raise ReportError("invalid_endpoint")
        return {"endpoint": env_value + "/report", "test_mode": True}
    return {"endpoint": PRODUCT_ENDPOINT, "test_mode": False}


def build_draft(input_dict, *, plugin_root, skill_root=None):
    """입력 dict를 검증·가림해 초안 dict를 만든다(파일은 쓰지 않는다)."""
    if not isinstance(input_dict, dict):
        raise ReportError("invalid_input")
    unknown = sorted(set(input_dict) - set(INPUT_FIELDS))
    if unknown:
        raise ReportError("unknown_field", field=unknown[0])
    kind = input_dict.get("kind")
    if kind not in KINDS:
        raise ReportError("invalid_kind")
    category = input_dict.get("category")
    if category not in CATEGORIES:
        raise ReportError("invalid_category")

    fields = {}
    for name in TEXT_FIELDS:
        value = input_dict.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ReportError("not_string", field=name)
        value = js_trim(value)
        if not value:
            if name in ("title", "description"):
                raise ReportError("missing_fields", field=name)
            continue
        fields[name] = value
    for name in ("title", "description"):
        if name not in fields:
            raise ReportError("missing_fields", field=name)

    limits = FIELD_LIMITS[kind]
    for name, value in fields.items():
        if _u16len(value) > limits[name]:
            raise ReportError("too_long", field=name, limit=limits[name])

    payload = {
        "kind": kind,
        "category": category,
        "title": fields["title"],
        "description": fields["description"],
    }
    for name in OPTIONAL_FIELDS:
        if name in fields:
            payload[name] = fields[name]
    payload["skill_version"] = _skill_version(plugin_root, skill_root)
    # r2 D1-02: private는 environment를 넣지 않는다(Worker가 전달하지 않음).
    if kind == "issue":
        payload["environment"] = environment()

    redactions = []
    if kind == "issue":
        for name in TEXT_FIELDS:
            if name not in payload:
                continue
            masked, counts = redact(payload[name])
            payload[name] = masked
            for rtype, count in counts.items():
                if count:
                    redactions.append(
                        {"field": name, "type": rtype, "count": count})

    if kind == "private" and _u16len(_private_composite(payload)) \
            > PRIVATE_COMPOSITE_MAX:
        raise ReportError("private_too_long", limit=PRIVATE_COMPOSITE_MAX)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ReportError("too_large")

    normalized = worker_validate(payload)
    if "error" in normalized:
        raise ReportError("worker_would_alter")
    for key, value in payload.items():
        if normalized.get(key) != value:
            raise ReportError("worker_would_alter", field=key)

    destination = _desired_destination(os.environ.get(TEST_ENDPOINT_ENV))

    return {
        "schema": DRAFT_SCHEMA,
        "created_at": _utc_now(),
        "destination": destination,
        "payload": payload,
        "redactions": redactions,
        "warnings": _warnings(payload) if kind == "issue" else [],
    }


def preview(draft):
    """학생에게 그대로 보여 줄 한국어 미리보기."""
    payload = draft["payload"]
    kind = payload["kind"]
    lines = []
    if draft.get("destination", {}).get("test_mode"):
        lines.append("[시험 대상] " + draft["destination"]["endpoint"])
    lines.append("보낼 곳: " + DESTINATION_LABELS[kind])
    if kind == "issue":
        lines.append("공개 이슈라 누구나 볼 수 있습니다.")
    lines.append("분류: " + CATEGORY_LABELS[payload["category"]])
    lines.append("제목: " + payload["title"])
    lines.append("")
    lines.append("내용:")
    lines.append(payload["description"])
    if payload.get("steps"):
        lines += ["", "사용한 단계:", payload["steps"]]
    if payload.get("error_message"):
        lines += ["", "오류 메시지:", payload["error_message"]]
    lines.append("")
    if kind == "issue":
        lines.append("함께 보내는 정보: 스킬 버전 %s, 환경 %s" % (
            payload.get("skill_version", ""), payload.get("environment", "")))
    else:
        # r2 D1-02: 실제 전달되는 필드만 보여 준다.
        lines.append("함께 보내는 정보: 스킬 버전 %s"
                     % payload.get("skill_version", ""))
    return "\n".join(lines)


def _validate_draft(draft):
    """send/show 공용 초안 재검증. 실패는 ReportError(코드별)."""
    if not isinstance(draft, dict) or draft.get("schema") != DRAFT_SCHEMA:
        raise ReportError("invalid_draft")
    allowed = {"schema", "created_at", "destination", "payload",
               "redactions", "warnings"}
    if set(draft) - allowed or not {"destination", "payload"} <= set(draft):
        raise ReportError("invalid_draft")
    dest = draft["destination"]
    if not isinstance(dest, dict) or set(dest) != {"endpoint", "test_mode"}:
        raise ReportError("invalid_draft")
    test_mode, endpoint = dest.get("test_mode"), dest.get("endpoint")
    if test_mode is True:
        if not isinstance(endpoint, str) \
                or not _LOOPBACK_ENDPOINT.fullmatch(endpoint):
            raise ReportError("invalid_draft")
    elif test_mode is False:
        if endpoint != PRODUCT_ENDPOINT:
            raise ReportError("invalid_draft")
    else:
        raise ReportError("invalid_draft")

    payload = draft["payload"]
    if not isinstance(payload, dict):
        raise ReportError("invalid_draft")
    if set(payload) - set(PAYLOAD_KEYS):
        raise ReportError("invalid_draft")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ReportError("invalid_draft")
    required = {"kind", "category", "title", "description", "skill_version"}
    if kind == "issue":
        required.add("environment")
    if not required <= set(payload):
        raise ReportError("invalid_draft")
    if kind == "private" and "environment" in payload:
        raise ReportError("invalid_draft")
    if payload.get("category") not in CATEGORIES:
        raise ReportError("invalid_draft")
    # r3 R2-01: payload에 있는 필드는 비어 있으면 안 된다.
    for name in PAYLOAD_KEYS:
        if name in payload and (
                not isinstance(payload[name], str) or not payload[name]):
            raise ReportError("invalid_draft")
    limits = FIELD_LIMITS[kind]
    for name in TEXT_FIELDS:
        if name in payload and _u16len(payload[name]) > limits[name]:
            raise ReportError("invalid_draft")
    for name in ("skill_version", "environment"):
        if name in payload and _u16len(payload[name]) > EXTRA_LIMITS[name]:
            raise ReportError("invalid_draft")
    if kind == "private" and _u16len(_private_composite(payload)) \
            > PRIVATE_COMPOSITE_MAX:
        raise ReportError("private_too_long", limit=PRIVATE_COMPOSITE_MAX)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) \
            > MAX_BODY_BYTES:
        raise ReportError("invalid_draft")

    normalized = worker_validate(payload)
    if "error" in normalized:
        raise ReportError("worker_would_alter")
    for key, value in payload.items():
        if normalized.get(key) != value:
            raise ReportError("worker_would_alter", field=key)

    if kind == "issue":
        # F1-01: 공개로 직렬화되는 모든 문자열 필드(메타데이터 포함)를
        # 재검사한다. payload를 고치지 않고 거부만 한다.
        for name in PUBLIC_SCAN_FIELDS:
            if name not in payload:
                continue
            _, counts = redact(payload[name])
            if any(counts.values()):
                raise ReportError("pii_remaining", field=name)


def _validate_receipt(receipt):
    """영수증 형태 검증 — schema·draft_sha256·kind·kind별 형태."""
    if not isinstance(receipt, dict) \
            or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ReportError("receipt_invalid")
    sha = receipt.get("draft_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ReportError("receipt_invalid")
    kind = receipt.get("kind")
    if kind not in KINDS:
        raise ReportError("receipt_invalid")
    if kind == "issue":
        number = receipt.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise ReportError("receipt_invalid")
        url = receipt.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ReportError("receipt_invalid")


def _receipt_summary(receipt):
    return {k: receipt[k] for k in
            ("kind", "number", "url", "sent_at", "test_mode")
            if k in receipt}


def _success_receipt(draft_path, sha):
    """이 초안 sha의 유효한 성공 영수증. 없으면 None."""
    receipt_path = Path(str(draft_path) + ".receipt.json")
    if not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        _validate_receipt(receipt)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError,
            ReportError):
        return None
    return receipt if receipt["draft_sha256"] == sha else None


def _done_state(draft_path, sha):
    """Return the done-record state: "none" | "valid" | "unknown".

    F1-04: 이름만으로 성공이라 하지 않는다 — claim schema·현재
    draft_sha256·token을 확인한 기록만 "valid"다. F1 R2-02: 읽을 수 없거나
    필수 필드가 빠진 기록은 '없음'과 합치지 않고 "unknown"(판별 불가)이다.
    다른 sha의 완전한 기록은 이 초안과 무관하므로 무시한다.
    """
    draft_path = Path(draft_path)
    pattern = draft_path.name + ".sending.*.done.json"
    state = "none"
    for record in draft_path.parent.glob(pattern):
        try:
            doc = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            state = "unknown"
            continue
        well_formed = (
            isinstance(doc, dict)
            and doc.get("schema") == CLAIM_SCHEMA
            and isinstance(doc.get("draft_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", doc["draft_sha256"])
            and isinstance(doc.get("token"), str) and doc["token"])
        if not well_formed:
            state = "unknown"
            continue
        if doc["draft_sha256"] == sha:
            return "valid"
    return state


def _write_new(path, text):
    """새 파일에 UTF-8 바이트 그대로 쓴다.

    F1-05: 텍스트 모드의 줄바꿈 변환(Windows CRLF)을 피해 draft 출력
    sha256이 실제 저장 바이트의 sha256과 언제나 같게 한다.
    """
    with open(path, "xb") as fh:
        fh.write(text.encode("utf-8"))


def _rename_claim(claim_path, draft_path, suffix, *, sha, token=None):
    """claim을 "<초안>.sending.<UTC>.<suffix>.json"으로 옮긴다.

    r3 R2-02: 자기 claim만 옮긴다 — token이 주어지면 파일 속 token과
    대조하고, 언제나 schema·draft_sha256를 확인한다.
    """
    try:
        doc = json.loads(claim_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(doc, dict):
        return
    if (doc.get("schema") != CLAIM_SCHEMA
            or doc.get("draft_sha256") != sha
            or (token is not None and doc.get("token") != token)):
        return
    target = Path("%s.sending.%s.%s.json" % (draft_path, _stamp(), suffix))
    try:
        claim_path.rename(target)
    except OSError:
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 따라가지 않고 실패로 처리한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    return urllib.request.build_opener(_NoRedirect)


def _already_sent(draft_path, sha, dest, receipt=None):
    result = {"status": "already_sent", "draft": str(draft_path),
              "sha256": sha, "test_mode": dest["test_mode"],
              "message": "이미 개발자에게 전달된 신고입니다. "
                         "같은 내용을 다시 보내지 않았습니다."}
    if receipt:
        result.update(_receipt_summary(receipt))
    return result


def _delivery_unknown(reason, draft_path, sha, dest, http_status=None):
    result = {
        "status": "delivery_unknown",
        "reason": reason,
        "draft": str(draft_path),
        "sha256": sha,
        "test_mode": dest.get("test_mode"),
        "message": "전에 보내기를 시도했지만 접수됐는지 확인하지 못했습니다.",
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _error(code, **fields):
    result = {"status": "error", "error": code}
    result.update(fields)
    return result


# F1-03: 배포 Worker(src/index.js)가 명시적 실패로 돌려주는 상태·error 코드.
_EXPLICIT_FAILURES = {
    400: {"issue": {"invalid_json", "invalid_kind", "missing_fields"},
          "private": {"invalid_json", "invalid_kind", "missing_fields"}},
    404: {"issue": {"not_found"}, "private": {"not_found"}},
    413: {"issue": {"too_large"}, "private": {"too_large"}},
    429: {"issue": {"rate_limited"}, "private": {"rate_limited"}},
    502: {"issue": {"github_failed"}, "private": {"discord_failed"}},
    503: {"issue": {"issue_channel_not_configured"},
          "private": {"private_channel_not_configured"}},
}


def _explicit_failure(status, body_doc, kind):
    """Worker 계약 형태의 명시적 실패 응답이면 True."""
    allowed = _EXPLICIT_FAILURES.get(status, {}).get(kind)
    error = body_doc.get("error")
    # F1 R2-01: 문자열이 아닌 error(배열·객체·null·숫자)는 계약 밖이다.
    return bool(allowed) and body_doc.get("ok") is False \
        and isinstance(error, str) and error in allowed


def send(draft_path, confirm, *, endpoint=None, opener=None,
         retry_unknown=False, _pre_claim_hook=None):
    """초안을 수신처로 보낸다. 항상 결과 dict를 돌려준다(예외 없음).

    r2 D1-05 + r3 R2-02 순서: 초안 바이트 한 번 읽기 → sha 대조 →
    파싱·검증·직렬화 → 영수증 검사 → claim O_EXCL 획득 → 영수증·done
    기록 재확인 → POST → 결과 처리.
    """
    draft_path = Path(draft_path)
    try:
        raw = draft_path.read_bytes()
    except OSError:
        return _error("invalid_draft", draft=str(draft_path))
    sha = hashlib.sha256(raw).hexdigest()
    if sha != confirm:
        return _error("confirm_mismatch", sha256=sha)
    try:
        draft = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("invalid_draft", sha256=sha)
    try:
        _validate_draft(draft)
    except ReportError as exc:
        return _error(exc.code, sha256=sha, **exc.detail)

    payload = draft["payload"]
    dest = draft["destination"]
    kind = payload["kind"]

    if endpoint is not None and endpoint != dest["endpoint"]:
        return _error("endpoint_changed", sha256=sha)
    try:
        desired = _desired_destination(os.environ.get(TEST_ENDPOINT_ENV))
    except ReportError:
        desired = None
    if desired != dest:
        return _error("endpoint_changed", sha256=sha)

    receipt_path = Path(str(draft_path) + ".receipt.json")
    if receipt_path.is_file():
        try:
            receipt = json.loads(
                receipt_path.read_text(encoding="utf-8"))
            _validate_receipt(receipt)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError,
                ReportError):
            return _error("receipt_invalid", sha256=sha)
        if receipt["draft_sha256"] != sha:
            return _error("receipt_conflict", sha256=sha)
        return _already_sent(draft_path, sha, dest, receipt)

    # 시험 훅(r3 R2-02): claim 획득 직전에 다른 전송을 끼워 넣어
    # A-then-B 경합을 재현한다.
    if _pre_claim_hook is not None:
        _pre_claim_hook()

    claim_path = Path(str(draft_path) + ".sending.json")
    token = secrets.token_hex(8)
    claim = json.dumps(
        {"schema": CLAIM_SCHEMA, "draft_sha256": sha,
         "started_at": _utc_now(), "token": token,
         "test_mode": dest["test_mode"]},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        _write_new(claim_path, claim)
    except FileExistsError:
        if not retry_unknown:
            return _delivery_unknown(
                "previous_attempt_unconfirmed", draft_path, sha, dest)
        _rename_claim(claim_path, draft_path, "stale", sha=sha)
        try:
            _write_new(claim_path, claim)
        except FileExistsError:
            return _delivery_unknown(
                "previous_attempt_unconfirmed", draft_path, sha, dest)

    # r3 R2-02: claim을 잡은 뒤 영수증·done 기록을 다시 확인한다 —
    # 앞선 전송이 성공을 마쳤으면 자기 claim을 .dup로 옮기고 POST 없이
    # already_sent를 돌려준다.
    receipt = _success_receipt(draft_path, sha)
    done = _done_state(draft_path, sha)
    if receipt is not None or done == "valid":
        _rename_claim(claim_path, draft_path, "dup", sha=sha, token=token)
        return _already_sent(draft_path, sha, dest, receipt)
    # F1 R2-02: 판별할 수 없는 done 기록은 미접수의 증거가 아니다 — 기본
    # send는 보내지 않는다. 명시적 불명 재시도(--retry-unknown)만 진행한다.
    if done == "unknown" and not retry_unknown:
        _rename_claim(claim_path, draft_path, "held", sha=sha, token=token)
        return _delivery_unknown(
            "unverifiable_done_record", draft_path, sha, dest)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        dest["endpoint"], data=body, method="POST",
        headers={"content-type": "application/json; charset=utf-8",
                 "user-agent": USER_AGENT})
    try:
        response = (opener or _default_opener()).open(
            request, timeout=TIMEOUT_SECONDS)
        with response:
            status, data = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            data = exc.read()
        except OSError:
            data = b""
        finally:
            exc.close()
    except Exception:
        # 네트워크 오류·시간 초과·연결 끊김: 접수 여부를 알 수 없다.
        return _delivery_unknown("outcome_unknown", draft_path, sha, dest)

    body_doc = {}
    try:
        body_doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        body_doc = {}
    if not isinstance(body_doc, dict):
        body_doc = {}

    if status == 201:
        ok = (body_doc.get("ok") is True
              and body_doc.get("kind") == kind)
        if ok and kind == "issue":
            number = body_doc.get("number")
            url = body_doc.get("url")
            ok = (isinstance(number, int) and not isinstance(number, bool)
                  and isinstance(url, str) and url.startswith("https://"))
        if not ok:
            # 201인데 계약 형태가 아니면 접수 여부를 알 수 없다.
            return _delivery_unknown(
                "outcome_unknown", draft_path, sha, dest, http_status=201)
        receipt = {"schema": RECEIPT_SCHEMA, "draft_sha256": sha,
                   "sent_at": _utc_now(), "kind": kind,
                   "test_mode": dest["test_mode"]}
        if kind == "issue":
            receipt["number"] = body_doc["number"]
            receipt["url"] = body_doc["url"]
        try:
            _write_new(
                receipt_path,
                json.dumps(receipt, ensure_ascii=False,
                           indent=2, sort_keys=True) + "\n")
        except FileExistsError:
            existing = _success_receipt(draft_path, sha)
            if existing is not None:
                return _already_sent(draft_path, sha, dest, existing)
            return {"status": "sent_unrecorded", "kind": kind,
                    "number": body_doc.get("number"),
                    "url": body_doc.get("url"),
                    "test_mode": dest["test_mode"],
                    "draft": str(draft_path), "sha256": sha,
                    "message": "개발자에게 전달됐지만 이 컴퓨터에 "
                               "접수 기록을 남기지 못했습니다."}
        except OSError:
            return {"status": "sent_unrecorded", "kind": kind,
                    "number": body_doc.get("number"),
                    "url": body_doc.get("url"),
                    "test_mode": dest["test_mode"],
                    "draft": str(draft_path), "sha256": sha,
                    "message": "개발자에게 전달됐지만 이 컴퓨터에 "
                               "접수 기록을 남기지 못했습니다."}
        _rename_claim(claim_path, draft_path, "done", sha=sha, token=token)
        if kind == "issue":
            message = "공개 이슈 #%d로 접수됐습니다: %s" % (
                body_doc["number"], body_doc["url"])
        else:
            message = "개발자에게 비공개로 전달됐습니다."
        result = {"status": "sent", "kind": kind,
                  "test_mode": dest["test_mode"],
                  "draft": str(draft_path), "sha256": sha,
                  "message": message}
        if kind == "issue":
            result["number"] = body_doc["number"]
            result["url"] = body_doc["url"]
        return result

    # 확정 미접수: Worker가 계약 형태({ok:false, error:<상태·kind별 코드>})로
    # 명시적 실패를 돌려준 경우만 claim을 .failed로 보존하고 같은 초안을
    # 다시 보낼 수 있다. F1-03: 형태가 다르면(HTML 게이트웨이 오류 등)
    # 접수 여부를 알 수 없으므로 delivery_unknown으로 claim을 남긴다.
    if _explicit_failure(status, body_doc, kind):
        _rename_claim(claim_path, draft_path, "failed", sha=sha, token=token)
        if status == 400:
            return {"status": "rejected",
                    "reason": body_doc.get("error", "invalid"),
                    "http_status": status, "test_mode": dest["test_mode"],
                    "draft": str(draft_path), "sha256": sha,
                    "message": "신고가 접수되지 않았습니다: %s"
                               % body_doc.get("error", "잘못된 형식")}
        if status == 413:
            return {"status": "rejected", "reason": "too_large",
                    "http_status": status, "test_mode": dest["test_mode"],
                    "draft": str(draft_path), "sha256": sha,
                    "message": "내용이 너무 길어 접수되지 않았습니다."}
        if status == 429:
            return {"status": "not_sent", "reason": "rate_limited",
                    "retry": "1분 뒤", "http_status": status,
                    "test_mode": dest["test_mode"],
                    "draft": str(draft_path), "sha256": sha,
                    "message": "요청이 많아 잠시 뒤(1분 후) 다시 "
                               "보낼 수 있습니다."}
        return {"status": "not_sent", "reason": "server_error",
                "http_status": status, "test_mode": dest["test_mode"],
                "draft": str(draft_path), "sha256": sha,
                "message": "지금은 보내지 못했습니다. 초안은 그대로 "
                           "있으니 잠시 뒤 다시 보낼 수 있습니다."}

    # 그 밖의 상태 코드·형태는 접수 여부 불명 — claim을 그대로 둔다.
    return _delivery_unknown(
        "outcome_unknown", draft_path, sha, dest, http_status=status)


def show(draft_path):
    """초안 미리보기와(있으면) 영수증 요약을 돌려준다."""
    draft_path = Path(draft_path)
    try:
        raw = draft_path.read_bytes()
    except OSError:
        return _error("invalid_draft", draft=str(draft_path))
    sha = hashlib.sha256(raw).hexdigest()
    try:
        draft = json.loads(raw.decode("utf-8"))
        _validate_draft(draft)
    except (UnicodeDecodeError, json.JSONDecodeError, ReportError):
        return _error("invalid_draft", sha256=sha)
    dest = draft["destination"]
    sent = None
    receipt = _success_receipt(draft_path, sha)
    if receipt is not None:
        sent = _receipt_summary(receipt)
    elif Path(str(draft_path) + ".receipt.json").is_file():
        sent = {"unreadable": True}
    return {
        "status": "draft",
        "draft": str(draft_path),
        "sha256": sha,
        "destination": DESTINATION_LABELS[draft["payload"]["kind"]],
        "test_mode": dest["test_mode"],
        "preview": preview(draft),
        "redactions": draft.get("redactions", []),
        "warnings": draft.get("warnings", []),
        "sent": sent,
    }


def _plugin_root():
    return Path(__file__).resolve().parents[3]


def _cmd_draft(args):
    out_path = Path(args.out)
    try:
        input_doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 2, _error("cannot_read_input", input=args.input)
    if not out_path.parent.is_dir():
        return 2, _error("out_parent_missing", out=args.out)
    if out_path.exists():
        return 2, _error("out_exists", out=args.out)
    try:
        draft = build_draft(input_doc, plugin_root=_plugin_root())
    except ReportError as exc:
        return 2, _error(exc.code, **exc.detail)
    text = json.dumps(draft, ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"
    try:
        _write_new(out_path, text)
    except FileExistsError:
        return 2, _error("out_exists", out=args.out)
    except OSError:
        return 2, _error("out_parent_missing", out=args.out)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return 0, {
        "status": "drafted",
        "draft": str(out_path),
        "sha256": sha,
        "destination": DESTINATION_LABELS[draft["payload"]["kind"]],
        "test_mode": draft["destination"]["test_mode"],
        "preview": preview(draft),
        "redactions": draft["redactions"],
        "warnings": draft["warnings"],
    }


def _cmd_show(args):
    result = show(args.draft)
    return (2 if result.get("status") == "error" else 0), result


def _cmd_send(args):
    result = send(args.draft, args.confirm,
                  retry_unknown=args.retry_unknown)
    status = result.get("status")
    code = 0 if status in ("sent", "already_sent", "sent_unrecorded") \
        else 1 if status in ("not_sent", "delivery_unknown") else 2
    return code, result


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gg_report.py",
        description="학생 오류·불편 신고 초안 작성·확인·전송")
    sub = parser.add_subparsers(dest="command", required=True)
    p_draft = sub.add_parser("draft", help="신고 초안 파일 만들기")
    p_draft.add_argument("--input", required=True,
                         help="입력 JSON 파일 경로")
    p_draft.add_argument("--out", required=True,
                         help="새 초안 JSON 파일 경로(덮어쓰기 불가)")
    p_show = sub.add_parser("show", help="초안 미리보기·영수증 확인")
    p_show.add_argument("--draft", required=True)
    p_send = sub.add_parser("send", help="확인한 초안을 개발자에게 전송")
    p_send.add_argument("--draft", required=True)
    p_send.add_argument("--confirm", required=True,
                        help="학생에게 보여 준 초안 파일의 SHA-256")
    p_send.add_argument("--retry-unknown", action="store_true",
                        help="접수 불명 claim을 .stale로 보존하고 재시도")
    args = parser.parse_args(argv)
    if args.command == "draft":
        code, result = _cmd_draft(args)
    elif args.command == "show":
        code, result = _cmd_show(args)
    else:
        code, result = _cmd_send(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
