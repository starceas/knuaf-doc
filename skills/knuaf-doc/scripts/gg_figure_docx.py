"""docx 그림 검사 — 앱 승인 토큰 해석 + 학생 그림 목록 (P1).

build_docx가 그림 노드를 docx에 넣기 전에 이 모듈 하나만 부르면 된다.

연결 줄 1 — 원고 검사 `gg_document.check`(그림 노드 가지):

    import gg_figure_docx

    handled, token_issue = gg_figure_docx.image_check(n["path"])
    if token_issue:
        issues.append(("missing_image", n["path"] + ": " + token_issue))
    elif not handled:
        # 절대 경로·URL·base 없음은 그대로 missing_image.
        # 아니면 gg_hwpx.resolve_figure(원고 옆 → 뿌리)가 판정한다(M-02).

연결 줄 2 — `build_docx.py` 그림 루프(`elif n["kind"] == "image":` 가지,
옛 `p = (Path(base) / n["path"]).resolve()` 자리 — 지금은 `gg_hwpx.resolve_figure`).
이 모듈과 같은 이유로 `import gg_hwpx`도 **모듈 수준**에 둔다.

**먼저 모듈 수준 import 한 줄**을 `from gg_body_layout import …` 바로 아래에
넣는다. 아래 가지 안에서 import하면 같은 함수 뒤쪽(연결 줄 3)에서 그 이름이
안 보여 `NameError`가 난다(기존 결함 I-6):

    import gg_figure_docx

가지 본문:

    if gg_figure_docx.is_placeholder(n["path"]):
        # 앱 밖 빌드: 토큰을 풀 수 없다. 그림 대신 자리 표시 문단(알려진 한계).
        note = doc.add_paragraph(gg_figure_docx.PLACEHOLDER)
        note.alignment = WD_ALIGN_PARAGRAPH.CENTER
        note.paragraph_format.keep_with_next = True
    else:
        target, issue = gg_figure_docx.node_target(n["path"])
        if issue:
            raise ValueError("blocked: " + issue)
        if target is None:
            # 학생 그림: 원고 옆(base) → 작업 폴더 뿌리(root) 두 단계.
            target, candidates = gg_hwpx.resolve_figure(n["path"], base, root)
            if candidates:
                raise ValueError(gg_hwpx.ambiguous_image_message(candidates))
        if target is None:
            raise ValueError("그림 파일 없음/외부 경로: " + n["path"])
        doc.add_picture(str(target), width=Mm(145))
        picture_paragraph = doc.paragraphs[-1]
        picture_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture_paragraph.paragraph_format.keep_with_next = True

연결 줄 3 — 빌드 결과 dict(`706~723행` manifest, `"photo_requests"` 옆).
`manifest`가 아니라 **dict 리터럴 안의 항목**이다:

    "figures": gg_figure_docx.build_report(
        [n["path"] for n in parse(source_text) if n["kind"] == "image"]),

학생 그림 알림(`student_notice`)은 `report` 안에 그대로 실려 매니페스트
`figures.student_notice`로 나간다 — 따로 print하지 않는다(빌드 표준출력은
docx 경로 한 줄만 쓴다).

원고에 쓰이는 모양(CONTRACT §9): 그림 노드는 그림 줄 한 줄 **+ 빈 줄 +
캡션 문단**이다. 앱 그림은 `![](app-figure:<sha256>)`, 학생·직접 그림은
`![그림 N. 제목 (출처 : 기관)](figures/...)` — 두 경우 모두 그 다음 문단이
캡션 `그림 N. 제목 (출처 : 기관)`이고, 출처는 그림 줄이 아니라 캡션에 있다
(`gg_figure._block_for`). 캡션 문단이 없으면 `gg_document.check`가
`missing_caption: 그림 객체의 캡션 없음`을 낸다.

계약(CONTRACT §6):
- `KNUAF_FIGURE_HOME`이 있으면 토큰 `![](app-figure:<sha256>)`은
  `receipts.jsonl` 마지막 줄이 `approved`이고 `store/<sha256>.png`가 있으며
  파일 sha256이 같을 때만 넣는다. 아니면 빌드 거절
  `blocked: figure_unverified`.
- 없으면(앱 밖) 토큰 자리는 "앱에서 받은 그림" 자리 표시로 남긴다 — 알려진 한계.
- 토큰이 아닌 폴더 안 그림 = 학생이 넣은 그림. 그대로 넣고
  "학생이 넣은 그림 n장 — 출처·권리는 학생 확인"을 목록으로 낸다(캡션과 무관).
"""

import hashlib
import json
import os
import re
from pathlib import Path

TOKEN = re.compile(r"^app-figure:([0-9a-f]{64})$")
PLACEHOLDER = "앱에서 받은 그림"
STUDENT_NOTICE = "학생이 넣은 그림 %d장 — 출처·권리는 학생 확인"

_ENV = object()


def _resolve_home(home):
    """기본값 = 환경변수. 명시적 None이면 '앱 밖'을 뜻한다."""
    return figure_home() if home is _ENV else home


def token_sha(ref):
    """`app-figure:<sha256>` → sha256, 아니면 None."""
    match = TOKEN.match((ref or "").strip())
    return match.group(1) if match else None


def figure_home(environ=None):
    env = os.environ if environ is None else environ
    value = env.get("KNUAF_FIGURE_HOME")
    return Path(value) if value else None


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def receipts(home):
    """receipts.jsonl을 줄 단위로 읽는다. 깨진 줄은 건너뛴다."""
    path = Path(home) / "receipts.jsonl"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def receipt_for(home, sha):
    """그 sha256의 마지막 증명 줄. 없으면 None."""
    found = None
    for item in receipts(home):
        image = item.get("image")
        if isinstance(image, dict) and image.get("sha256") == sha:
            found = item
    return found


def resolve_token(sha, home=None):
    """토큰 sha256 → (경로, None) 또는 (None, 사유). 사유는 figure_unverified."""
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None, "figure_unverified"
    if home is None:
        home = figure_home()
    if home is None:
        return None, "figure_unverified"
    receipt = receipt_for(home, sha)
    if receipt is None:
        return None, "figure_unverified"
    image = receipt.get("image") or {}
    if image.get("verdict") != "approved":
        return None, "figure_unverified"
    stored = Path(home) / "store" / (sha + ".png")
    if not stored.is_file():
        return None, "figure_unverified"
    try:
        actual = digest_file(stored)
    except OSError:
        return None, "figure_unverified"
    if actual != sha:
        return None, "figure_unverified"
    return stored, None


def is_placeholder(ref, home=_ENV):
    """앱 밖에서 토큰을 만났는가 = docx에 '앱에서 받은 그림' 자리 표시를 넣는다."""
    if token_sha(ref) is None:
        return False
    return _resolve_home(home) is None


def node_target(ref, home=_ENV):
    """그림 노드 하나 → (절대경로 또는 None, 문제 사유 또는 None).

    토큰이 아니면 (None, None) — 경로 검사는 build_docx 몫.
    토큰이고 앱 밖이면 (None, None)이고 자리 표시 여부는
    `is_placeholder`가 답한다(빌드를 멈추지 않는다).
    """
    sha = token_sha(ref)
    if sha is None:
        return None, None
    home = _resolve_home(home)
    if home is None:
        return None, None
    return resolve_token(sha, home)


def image_check(ref, home=_ENV):
    """검사 단계의 그림 노드 판정 → (검사했는가, 문제 id 또는 None).

    (False, None)  토큰이 아니다 — 부르는 쪽의 기존 파일 존재 검사를 그대로 쓴다.
    (True, None)   토큰이고 지금 넣을 수 있다(앱 밖 자리 표시 포함).
    (True, "figure_unverified") 토큰인데 앱 기록으로 증명되지 않는다.

    `gg_document.check`의 `missing_image` 가지에서 부른다 — 토큰은 파일이
    아니라서 그대로 두면 정상 원고도 `missing_image` 오류가 난다(I-6).
    """
    sha = token_sha(ref)
    if sha is None:
        return False, None
    env_home = _resolve_home(home)
    if env_home is None:
        return True, None            # 앱 밖: 자리 표시로 남기므로 통과
    _, issue = resolve_token(sha, env_home)
    return True, issue


def student_figures(refs):
    """토큰이 아닌 그림 = 학생이 넣은 그림 목록."""
    return [ref for ref in refs if token_sha(ref) is None]


def student_notice(count):
    return STUDENT_NOTICE % count


def build_report(refs, home=_ENV):
    """빌드 결과에 실을 요약. blocked가 있으면 빌드를 멈춘다."""
    env_home = _resolve_home(home)
    images = []
    blocked = []
    placeholders = []
    for ref in refs:
        sha = token_sha(ref)
        if sha is None:
            images.append({"ref": ref, "kind": "student", "path": None})
            continue
        if env_home is None:
            images.append({"ref": ref, "kind": "placeholder", "path": None})
            placeholders.append(ref)
            continue
        path, issue = resolve_token(sha, env_home)
        if issue:
            blocked.append({"ref": ref, "reason": issue})
            images.append({"ref": ref, "kind": "blocked", "path": None})
            continue
        images.append({"ref": ref, "kind": "app", "path": str(path)})
    students = student_figures(refs)
    return {
        "images": images,
        "student": students,
        "student_count": len(students),
        "student_notice": student_notice(len(students)),
        "placeholders": placeholders,
        "placeholder_text": PLACEHOLDER,
        "blocked": blocked,
    }
