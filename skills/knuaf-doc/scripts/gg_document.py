"""Supported Markdown: headings, paragraphs, pipe tables, local images, emphasis."""

import json
import re
import unicodedata
from pathlib import Path
from gg_core import draft, START, END

# 학명 이탤릭은 이명법(속명+종소명)+명명자/등급 표지가 함께 나오는 경우만
# 적용한다. 종소명 자리의 영어 기능어(전치사·접속사·관사·조동사·대명사·흔한
# 부사/연결어)와, 명명자가 없는 "Growth response"·"Soil moisture" 같은 일반
# 영어 명사구를 학명으로 오인하지 않는다. 명명자 없는 학명은 로마체로 남으며
# 학명 검토는 여전히 사람 검토 대상이다.
_SCI_FUNCTION_WORDS = (
    "about above across after again against along although among and another "
    "any are around because been before behind being below beneath beside "
    "besides between beyond both but can could did does down during each "
    "either else even every except few for from had has have having hence her "
    "here how however in inside instead into its just least less like many "
    "may might more most much must near neither never next nor not now off "
    "once one only onto or other ought our out outside over own past per "
    "quite rather really same shall should since so some still such than that "
    "the their them then there therefore these they this those though through "
    "throughout thus till to too toward towards under underneath unless until "
    "unto up upon very via was were what when where which while who whom "
    "whose will with within without would yet you your"
).split()
SCI = re.compile(
    r"\b([A-Z][a-z]{2,}|[A-Z]\.)\s+((?!(?:"
    + "|".join(sorted(set(_SCI_FUNCTION_WORDS)))
    + r")\b)[a-z]{3,})(?=\s+(?:[A-Z][a-z]*\.?|var\.|subsp\.|f\.|ex\b|×))"
)
CAPTION = re.compile(r"^\s*(표|그림)\s*(\d+)\s*\.\s*(.+)$")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
CLIMATE = [
    "평균기온",
    "최고기온",
    "최저기온",
    "강우량",
    "평균적설량",
    "최고적설량",
    "주된풍향",
    "평균풍속",
    "최대풍속",
]
CLIMATE_FOOTER = ["관측장소", "최근 5개년 평균", "초상일", "만상일"]
DISASTER_FREQ = ("가장 자주 발생", "자주 발생", "가끔 발생")
DISASTER_TYPES = ("한발", "홍수", "태풍", "공장폐수", "공해가스", "야생동물")
FARM_MARKERS = ("농장명",)

# 세 어휘 검사는 식별 가능한 힌트일 뿐이며 "단어 없음=내용 부족 확정"이
# 아니다. 의미 충족 판정은 독립 내용검토·지침 계약의 소유다. 코어
# (gg_core)는 이 집합에 든 ID만 warning으로 집계하고 구조·수치·자료
# 누락 오류는 여기에 넣지 않는다.
CONTENT_REVIEW_HINTS = frozenset(
    {"intro_topics", "closing_elements", "process_plan"}
)

# 평문(졸업논문) 정책 검사 중 차단이 아니라 확인·보고 대상인 항목.
# 코어(gg_core)와 명령 어댑터(gg_commands)는 이 집합에 든 ID만
# severity="warning"으로 집계한다.
WARNING_CHECKS = frozenset(
    {
        "term_limit",
        "front_matter_long",
        "caption_long",
        "photo_placeholder",
    }
)

INFO_CHECKS = frozenset({"sourced_objects"})

POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "plain-thesis-policy.json"
)
HORT_PROFILE_PATH = POLICY_PATH.parent / "hort-env-systems" / "profile.json"

_POLICY_SHAPES = {
    "forbidden_body_terms": list,
    "forbidden_body_term_aliases": list,
    "limited_terms": dict,
    "front_matter": dict,
    "caption": dict,
    "objects": dict,
    "sources": dict,
    "images": dict,
}


def policy_for_major(policy, major_id):
    """모든 입력에 공통 값을 적용하고 해당 전공의 재정의만 합친다."""
    rules = {key: policy[key] for key in _POLICY_SHAPES}
    for key, value in policy["majors"].get(major_id, {}).items():
        rules[key] = {**rules[key], **value} if isinstance(value, dict) else value
    return rules


def _validate_plain_rules(rules):
    for key, shape in _POLICY_SHAPES.items():
        if not isinstance(rules.get(key), shape):
            raise ValueError("plain-thesis-policy.json 필드 오류: " + key)
    fm, cap, obj = rules["front_matter"], rules["caption"], rules["objects"]
    src, img = rules["sources"], rules["images"]
    for part, keys in ((fm, ("warn_chars", "error_chars")),
                       (cap, ("warn_chars",)), (obj, ("scan_lines",))):
        if not all(type(part.get(k)) is int and part[k] > 0 for k in keys):
            raise ValueError("plain-thesis-policy.json 수치 필드 오류")
    if fm["warn_chars"] >= fm["error_chars"]:
        raise ValueError("plain-thesis-policy.json front_matter 수치 오류")
    for values in (rules["forbidden_body_terms"], fm.get("title_keywords"),
                   cap.get("forbidden_terms"),
                   obj.get("credit_labels"), obj.get("missing_credit_values"),
                   src.get("forbidden_kinds"), img.get("ai_kinds"),
                   img.get("ai_markers")):
        if not isinstance(values, list) or not values or not all(
            isinstance(v, str) and v.strip() for v in values
        ):
            raise ValueError("plain-thesis-policy.json 목록 필드 오류")
    aliases = rules["forbidden_body_term_aliases"]
    if not all(isinstance(v, str) and v.strip() for v in aliases):
        raise ValueError("plain-thesis-policy.json 별칭 목록 오류")
    grades = src.get("forbidden_grades")
    if not isinstance(grades, list) or not grades or not all(
        type(v) is int and v > 0 for v in grades
    ):
        raise ValueError("plain-thesis-policy.json forbidden_grades 오류")
    for part, key in ((img, "placeholder_prefix"),):
        if not isinstance(part.get(key), str) or not part[key].strip():
            raise ValueError("plain-thesis-policy.json 문자열 필드 오류: " + key)
    if not all(isinstance(k, str) and k and type(v) is int and v > 0
               for k, v in rules["limited_terms"].items()):
        raise ValueError("plain-thesis-policy.json limited_terms 수치 오류")


def load_plain_policy(path=None):
    """정책 누락·손상은 전공 판정 전에 실패로 닫는다."""
    p = Path(path) if path is not None else POLICY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError("plain-thesis-policy.json 읽기 실패: %s" % e) from e
    if not isinstance(data, dict):
        raise ValueError("plain-thesis-policy.json 최상위가 객체 아님")
    if data.get("schema") != "knuaf-plain-thesis-policy/v1":
        raise ValueError("plain-thesis-policy.json schema 오류")
    majors = data.get("majors")
    if not isinstance(majors, dict):
        raise ValueError("plain-thesis-policy.json majors 필드 오류")
    if "counts" in data:
        raise ValueError("plain-thesis-policy.json 폐지된 counts 필드")
    _validate_plain_rules(data)
    for major, override in majors.items():
        if not isinstance(major, str) or not major or not isinstance(override, dict):
            raise ValueError("plain-thesis-policy.json majors 필드 오류")
        if set(override) - set(_POLICY_SHAPES):
            raise ValueError("plain-thesis-policy.json majors 미지원 정책 필드")
        for key, value in override.items():
            if not isinstance(value, _POLICY_SHAPES[key]):
                raise ValueError("plain-thesis-policy.json majors 필드 오류: " + key)
        _validate_plain_rules(policy_for_major(data, major))
    return data


# One number grammar for rendering, summary scope, and length boundaries:
# I..X / Ⅰ..Ⅹ / positive Arabic digits, ASCII/fullwidth dot with optional
# whitespace on both sides; Unicode Roman alone also allows a space, no dot.
# ASCII "I study ..." is prose. Bare Arabic chapters advance 1,2,... only
# in an already plain Arabic outline; under #/Roman chapters they are level 2.
_CHAPTER_NUMBER = re.compile(
    r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|VIII|VII|III|VI|IV|IX|II|X|I|V|[1-9]\d*)"
    r"\s*[.．]\s*(\S.*)$"
)
_CHAPTER_UNICODE = re.compile(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\s+(\S.*)$")
_ROMAN_NUMBERS = dict(zip(("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"), range(1, 11)))
_ROMAN_NUMBERS.update(zip("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ", range(1, 11)))


def _numbered_heading(text):
    match = _CHAPTER_NUMBER.fullmatch(text) or _CHAPTER_UNICODE.fullmatch(text)
    if match is None:
        return None
    numeral, title = match.groups()
    # \d also matches Unicode decimal tails; they are still Arabic numbers.
    if numeral.isdecimal():
        if re.match(r"\d", title):  # 1.2 is not a chapter number.
            return None
        return "arabic", int(numeral), title, False
    return "roman", _ROMAN_NUMBERS[numeral], title, numeral.isascii()


def _intro_title(title, keywords):
    return any(re.match(re.escape(k) + r"(?:$|\s|[(:：—–-])", title) for k in keywords)


class _HeadingState:
    """Prefix-only heading rule shared by parse() and introduction length.

    A labelled 요약/초록/Abstract scope may contain bare ASCII Roman I/II
    subsections. A new chapter-1 sequence, a Unicode/Arabic chapter, an
    explicit # body chapter, or an I. 머리말/서론 starts the body once.
    Markdown ##+ is always a subsection. No suffix or lookahead is read.
    """

    def __init__(self):
        self.summary = False
        self.summary_number = None
        self.body = False
        self.outline = None
        self.number = 0
        self.marked = False

    def read(self, line):
        text = line.strip()
        if re.match(r"^[-+*]\s+", text):
            return None, text
        md = re.match(r"^(#{1,6})\s+(.+)$", text)
        level = len(md[1]) if md else None
        if md:
            text = md[2]
        chapter = _numbered_heading(text)
        title = chapter[2] if chapter else text
        if not self.body and level in (None, 1) and re.fullmatch(
            r"요약|초록|abstract", title, re.IGNORECASE
        ):
            self.summary = True
            return level or (1 if chapter else None), text
        if md and level != 1:
            return level, text
        if md:
            self.body, self.summary, self.marked = True, False, True
            self.outline = chapter[0] if chapter else None
            self.number = chapter[1] if chapter else 0
            return 1, text
        if chapter:
            family, number, title, ascii_roman = chapter
            if self.summary and ascii_roman and not _intro_title(title, ("머리말", "서론")):
                if self.summary_number is None or number != 1:
                    self.summary_number = number
                    return 2, text
            main = family == "roman" or (
                not self.marked and self.outline in (None, "arabic")
                and number == self.number + 1
            )
            if main:
                self.body, self.summary = True, False
                self.outline, self.number, self.marked = family, number, False
                return 1, text
            return 2, text
        for level, pattern in enumerate((
            r"\d+\. ", r"[가-힣]\. ", r"\d+\) ", r"[가-힣]\) ",
            r"\(\d+\) ", r"\([가-힣]\) ", r"[①-⑳]", r"[㉮-㉾]",
        ), 2):
            if re.match(pattern, text):
                return level, text
        return None, text


def _front_matter_length(text, keywords):
    # Stop at H without parsing its suffix. Raw spans retain table separators.
    lines = draft(text).splitlines()
    state, start = _HeadingState(), None
    if isinstance(keywords, str):
        keywords = [keywords]
    for index, line in enumerate(lines):
        level, title = state.read(line)
        if level != 1 or not state.body:
            continue
        if start is not None:
            return len(re.sub(r"\s+", "", "\n".join(lines[start:index])))
        chapter = _numbered_heading(title)
        if chapter is None or chapter[1] != 1 or not _intro_title(chapter[2], keywords):
            return None
        start = index + 1
    return len(re.sub(r"\s+", "", "\n".join(lines[start:]))) if start is not None else None


def _term_count(text, term):
    if term.isascii():
        # Korean particles may follow an acronym; Latin identifiers may not.
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])"
        return len(re.findall(pattern, text, re.IGNORECASE | re.ASCII))
    return text.count(term)


_HANGUL_FINALS = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_COMPAT_FINAL = {c: chr(0x11a8 + i) for i, c in enumerate(_HANGUL_FINALS)}


def _term_separator(c):
    return (c.isspace() or unicodedata.category(c) == "Cf"
            or unicodedata.category(c).startswith("P") or c == "\u034f"
            or 0xfe00 <= ord(c) <= 0xfe0f or 0xe0100 <= ord(c) <= 0xe01ef)


def _compact_term_text(text):
    """Remove separators first, then compose modern Hangul with source spans.

    Only the 19 L / 21 V / 27 T modern jamo and their compatibility forms
    are composed. A compatibility consonant before V starts a new syllable;
    otherwise it may close LV. Archaic/residual jamo are retained, without
    guessing missing vowels, emoji, digits or lookalike letters.
    """
    tokens = []
    start = 0
    while start < len(text):
        end = start + 1
        # Keep ordinary accent normalization; invisible combining separators
        # must not merge unrelated original character spans.
        while (end < len(text) and unicodedata.combining(text[end])
               and not _term_separator(text[end])):
            end += 1
        source = text[start:end]
        for c in unicodedata.normalize("NFKC", source).casefold():
            if _term_separator(c):
                continue
            # Precomposed syllables may also receive an explicit final jamo.
            pieces = unicodedata.normalize("NFD", c) if 0xac00 <= ord(c) <= 0xd7a3 else c
            for piece in pieces:
                tokens.append((piece, start, end, _COMPAT_FINAL.get(source)))
        start = end
    chars, spans, i = [], [], 0
    while i < len(tokens):
        c, start, end, _ = tokens[i]
        if (0x1100 <= ord(c) <= 0x1112 and i + 1 < len(tokens)
                and 0x1161 <= ord(tokens[i + 1][0]) <= 0x1175):
            vowel = tokens[i + 1]
            syllable = 0xac00 + ((ord(c) - 0x1100) * 21 + ord(vowel[0]) - 0x1161) * 28
            end, i = vowel[2], i + 2
            if i < len(tokens):
                final, _, final_end, compat_final = tokens[i]
                next_is_vowel = (i + 1 < len(tokens)
                                 and 0x1161 <= ord(tokens[i + 1][0]) <= 0x1175)
                if compat_final and not next_is_vowel:
                    final = compat_final
                # A compatibility cluster is a final even when followed by V;
                # a simple compatibility consonant before V stays an onset.
                if 0x11a8 <= ord(final) <= 0x11c2:
                    syllable += ord(final) - 0x11a7
                    end, i = final_end, i + 1
            chars.append(chr(syllable))
            spans.append((start, end))
        else:
            chars.append(c)
            spans.append((start, end))
            i += 1
    return "".join(chars), spans


def _latin_identifier_edge(text, index, step):
    # Invisible format marks must not turn an identifier into a standalone
    # acronym. Actual punctuation/space remains a lexical boundary.
    while 0 <= index < len(text) and unicodedata.category(text[index]) in {"Cf", "Mn", "Me"}:
        index += step
    if not 0 <= index < len(text):
        return False
    c = unicodedata.normalize("NFKC", text[index])
    return any(x.isdecimal() or x == "_" or "LATIN" in unicodedata.name(x, "") for x in c)


def _forbidden_spans(text, term, compact, spans, *, needle=None):
    if needle is None:
        needle = _compact_term_text(term)[0]
    if not needle:
        return []
    out, offset = [], 0
    while True:
        found = compact.find(needle, offset)
        if found < 0:
            return out
        start, end = spans[found][0], spans[found + len(needle) - 1][1]
        if not term.isascii() or not (
            _latin_identifier_edge(text, start - 1, -1)
            or _latin_identifier_edge(text, end, 1)
        ):
            if not out or out[-1] != (start, end):
                out.append((start, end))
            offset = found + len(needle)
        else:
            offset = found + 1


def _draft_region(text):
    """Use the same envelope as draft(), retaining its identified offset."""
    raw = draft(text)  # Also validates paired markers before reading offsets.
    if START in text or END in text:
        start, end = text.index(START) + len(START), text.index(END)
    else:
        match = re.search(
            r"^##\s+DRAFT[^\n]*\n(.*?)(?=^##\s+(?:STATUS|INPUT|RESEARCH|FACTS|OPEN)\b|\Z)",
            text, re.M | re.S,
        )
        start, end = match.span(1) if match else (0, len(text))
    region = text[start:end]
    return raw, start + len(region) - len(region.lstrip())


def _body_term_units(text, nodes):
    """Keep cells/sections separate; soft paragraph lines may form a term.

    Each character maps back to the unmodified input, including DRAFT
    wrappers, indentation and Markdown heading prefixes.
    """
    if any("term_parts" not in n for n in nodes):
        nodes = parse(text, with_spans=True, _with_term_positions=True)
    units, chunks, positions = [], [], []
    def flush():
        if chunks:
            units.append(("".join(chunks), positions.copy()))
            chunks.clear()
            positions.clear()
    for n in nodes:
        parts = n["term_parts"]
        if not n.get("soft_continue"):
            flush()
        if not parts:
            continue
        for part, start in parts:
            if not part:
                continue
            if chunks:
                chunks.append("\n")
                positions.append(start - 1)
            chunks.append(part)
            positions.extend(range(start, start + len(part)))
            if n["kind"] != "paragraph":
                flush()
    flush()
    return units


def project_major(p):
    """정본 프로젝트의 common.major_id 바인딩을 읽는다. 확인 불가 시
    None — 공통 기본 정책을 적용한다."""
    try:
        import gg_major_contract as mc

        return mc.binding_from_project(
            mc.default_registry(), p
        ).major_id
    except Exception:
        return None


def _project_major(root):
    """문서 검사 입력의 루트가 프로젝트 폴더일 때 전공을 읽는다."""
    if root is None:
        return None
    try:
        if not (Path(root) / "project.json").is_file():
            return None
        import gg_core as core_mod

        return project_major(core_mod.load(root))
    except Exception:
        return None


def plain_policy_check(text, issues, nodes, major_id):
    """단일 정본 정책 게이트. 학교 구조 검사를 건너뛰는 짧은 입력에도
    모든 전공·전공 미확인 입력에 공통 기본값을 적용하고 해당 전공의
    majors 재정의만 합친다."""
    try:
        policy = load_plain_policy()
    except ValueError as e:
        issues.append(("plain_policy_file", str(e)))
        return
    policy = policy_for_major(policy, major_id)
    body_parts = []
    for n in nodes:
        if n["kind"] in {"paragraph", "heading"}:
            body_parts.append(n["text"])
        elif n["kind"] == "table":
            body_parts.extend(c for row in n["rows"] for c in row)
        elif n["kind"] == "image":
            body_parts.append(n["alt"] + " " + n["path"])
    units = [(raw, positions, *_compact_term_text(raw))
             for raw, positions in _body_term_units(text, nodes)]
    line_offsets = [0] + [m.end() for m in re.finditer("\n", text)]
    for term in policy["forbidden_body_terms"] + policy["forbidden_body_term_aliases"]:
        needle = _compact_term_text(term)[0]
        occurrences, line_index = [], 0
        for raw, positions, compact, spans in units:
            for start, end in _forbidden_spans(raw, term, compact, spans, needle=needle):
                pos = positions[start]
                while line_index + 1 < len(line_offsets) and line_offsets[line_index + 1] <= pos:
                    line_index += 1
                line = line_index + 1
                column = pos - line_offsets[line_index] + 1
                original = text[pos:positions[end - 1] + 1]
                occurrences.append("%d행 %d열 %r" % (line, column, original))
        hits = len(occurrences)
        if hits:
            issues.append(
                (
                    "forbidden_term",
                    "본문 금칙 재무 용어 %d회(원문 %s): %s"
                    % (hits, "; ".join(occurrences), term),
                )
            )
    # Captions are printed once and must count towards limited terms too.
    limited_body = "\n".join(body_parts + [
        n["text"] for n in nodes if n["kind"] == "caption"
    ])
    for term, limit in policy["limited_terms"].items():
        hits = _term_count(limited_body, term)
        if hits > limit:
            issues.append(
                (
                    "term_limit",
                    "제한 용어 %s %d회(상한 %d회)" % (term, hits, limit),
                )
            )
    fm = policy["front_matter"]
    length = _front_matter_length(text, fm["title_keywords"])
    if length is not None:
        if length > fm["error_chars"]:
            issues.append(
                (
                    "front_matter_over",
                    "머리말 공백 제외 %d자(상한 %d자)"
                    % (length, fm["error_chars"]),
                )
            )
        elif length > fm["warn_chars"]:
            issues.append(
                (
                    "front_matter_long",
                    "머리말 공백 제외 %d자(권고 %d자 이하)"
                    % (length, fm["warn_chars"]),
                )
            )
    cap = policy["caption"]
    cap_terms = list(policy["forbidden_body_terms"]) + list(
        cap["forbidden_terms"]
    )
    for n in nodes:
        if n["kind"] != "caption":
            continue
        m = CAPTION.fullmatch(n["text"])
        title = m[3].strip() if m else n["text"]
        if len(title) > cap["warn_chars"]:
            issues.append(
                (
                    "caption_long",
                    "캡션 %d자(권고 %d자 이하): %s"
                    % (len(title), cap["warn_chars"], n["text"]),
                )
            )
        hits = [t for t in cap_terms if _term_count(title, t)]
        if hits:
            issues.append(
                (
                    "caption_forbidden",
                    "캡션 금지 표현(%s): %s" % (", ".join(hits), n["text"]),
                )
            )
    sourced_object_check(nodes, issues, policy)
    img = policy["images"]
    placeholders = text.count(img["placeholder_prefix"])
    if placeholders:
        issues.append(
            (
                "photo_placeholder",
                "미해결 사진 자리 표기 %d건(제출 전 학생 사진으로 교체): %s…"
                % (placeholders, img["placeholder_prefix"]),
            )
        )
    return policy


def _plain_objects(nodes):
    """지원 형식의 캡션+객체는 한 번만 센다. 무캡션 객체도 출처를 검사한다."""
    groups = []
    i = 0
    while i < len(nodes):
        n = nodes[i]
        kind = n["kind"]
        if kind == "caption" or kind in {"table", "image"}:
            label = n["label"] if kind == "caption" else (
                "표" if kind == "table" else "그림")
            end = i
            if i + 1 < len(nodes):
                after = nodes[i + 1]
                if (kind == "caption" and label == "표" and after["kind"] == "table") or (
                    kind == "image" and after["kind"] == "caption" and after["label"] == "그림"
                ):
                    end += 1
            groups.append((label, i, end))
            i = end + 1
        else:
            i += 1
    return groups


def _object_neighbors(nodes, start, end, window):
    for origin, step in ((start - 1, -1), (end + 1, 1)):
        pos = origin
        for distance in range(1, window + 1):
            if not 0 <= pos < len(nodes) or nodes[pos]["kind"] != "paragraph":
                break  # 다음 표·그림·절의 출처를 끌어오지 않는다.
            yield pos, distance
            pos += step


def _placeholder_figure(nodes, start, end, policy):
    if any(n["kind"] == "image" for n in nodes[start:end + 1]):
        return False
    return any(
        policy["images"]["placeholder_prefix"] in nodes[pos]["text"]
        for pos, _ in _object_neighbors(nodes, start, end, 1)
    )


def sourced_object_check(nodes, issues, policy):
    """출처 표기 존재만 검사한다. 수치 진위·이용권리는 사람 검토 대상이다."""
    obj = policy["objects"]
    labels = "|".join(re.escape(v) for v in obj["credit_labels"])
    credit_re = re.compile(r"^(?:[\\*＊·•]\s*)*(?:" + labels + r")\s*[:：]\s*(.*?)\s*$")
    absent = {re.sub(r"\s+", "", v).casefold() for v in obj["missing_credit_values"]}
    groups = _plain_objects(nodes)
    # 각 출처 줄은 가장 가까운 객체 하나에만 연결한다. 동거리면 앞 객체.
    owners = {}
    pending = set()
    for index, (label, start, end) in enumerate(groups):
        neighbors = list(_object_neighbors(nodes, start, end, obj["scan_lines"]))
        if label == "그림" and _placeholder_figure(nodes, start, end, policy):
            pending.add(index)
            continue
        for pos, distance in neighbors:
            m = credit_re.fullmatch(nodes[pos]["text"].strip().strip("*"))
            if not m:
                continue
            value = m[1].strip().strip("*[] ")
            if not value or re.sub(r"\s+", "", value).casefold() in absent:
                continue
            candidate = (distance, index)
            if candidate < owners.get(pos, (float("inf"), float("inf"))):
                owners[pos] = candidate
    credited = {index for _, index in owners.values()}
    counts = {"표": 0, "그림": 0}
    for index, (label, start, end) in enumerate(groups):
        if index in pending:
            continue
        if label == "그림":
            pieces = [n.get("text", "") + " " + n.get("alt", "") + " " + n.get("path", "")
                      for n in nodes[start:end + 1]]
            pieces += [nodes[pos]["text"] for pos, (_, owner) in owners.items() if owner == index]
            if any(marker.casefold() in "\n".join(pieces).casefold()
                   for marker in policy["images"]["ai_markers"]):
                issues.append(("image_ai_generated", "AI 생성 이미지 산출물 금지: " + pieces[0].strip()))
        if index in credited:
            counts[label] += 1
        else:
            n = next((n for n in nodes[start:end + 1] if n["kind"] == "caption"), nodes[start])
            name = n.get("text") or n.get("path") or "캡션 없는 표"
            issues.append(("object_credit", "%s 출처 표기 없음: %s" % (label, name)))
    issues.append(("sourced_objects", "출처 있는 표 %d개·그림 %d개" % (counts["표"], counts["그림"])))


def _norm_cell(s):
    return re.sub(r"\s+", "", s)


# 빈 값·미제공·미산정 표시는 채워진 자료 셀로 세지 않는다.
_EMPTY_CELL = frozenset(
    _norm_cell(x)
    for x in (
        "-", "—", "–", "―", "·", "…", "확인 필요", "[확인 필요]",
        "미산정", "자료 없음", "해당 없음", "없음", "없다", "N/A",
        "<", "^",
    )
)


def _filled_cell(cell):
    return _norm_cell(cell) not in _EMPTY_CELL


def _field_filled(rows, pattern):
    """항목 라벨 셀이 있고 같은 행(첫 열 라벨) 또는 같은 열(머리 셀)에
    채워진 자료 셀이 하나라도 있으면 참. 라벨만 있고 값이 비어 있으면
    미제공 항목이다."""
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if not pattern.search(cell):
                continue
            if c == 0:
                if any(_filled_cell(x) for x in row[1:]):
                    return True
            elif any(
                _filled_cell(rows[r2][c])
                for r2 in range(r + 1, len(rows))
                if c < len(rows[r2])
            ):
                return True
    return False


# 학교 양식 항목과 명백히 같은 뜻의 표기만 인정한다. 임의 동의어 사전이나
# 의미 추론은 두지 않는다.
CLIMATE_PATTERNS = {
    "평균기온": re.compile(r"평균\s*기온"),
    "최고기온": re.compile(r"최고\s*기온"),
    "최저기온": re.compile(r"최저\s*기온"),
    "강우량": re.compile(r"강우량|강수량"),
    "평균적설량": re.compile(r"평균\s*적설량"),
    "최고적설량": re.compile(r"최고\s*적설량"),
    "주된풍향": re.compile(r"풍향"),
    "평균풍속": re.compile(r"평균\s*풍속"),
    "최대풍속": re.compile(r"최대\s*풍속"),
}
# 각주 항목 라벨(표 안쪽 행)과, 표 바로 아래 주·자료 문맥에서 같은 정보를
# 적은 표현을 구분한다. 문맥 쪽에는 평균 기간의 연 범위 표기를 포함한다.
CLIMATE_FOOTER_LABEL = {
    "관측장소": re.compile(r"관측장소|관측지점|관측소|좌표"),
    "최근 5개년 평균": re.compile(r"5\s*개년|최근\s*5\s*년"),
    "초상일": re.compile(r"초상일|첫\s*서리|처음\s*서리"),
    "만상일": re.compile(r"만상일|늦\s*서리|마지막\s*서리"),
}
CLIMATE_FOOTER_CONTEXT = {
    **CLIMATE_FOOTER_LABEL,
    "최근 5개년 평균": re.compile(
        r"5\s*개년|최근\s*5\s*년"
        r"|(?:19|20)\d{2}\s*년?\s*[~–—-]\s*(?:19|20)\d{2}\s*년"
    ),
}
# 문장이 값을 적는 대신 없음·미산정을 선언하면 자료 있는 것으로 세지 않는다.
_ABSENT_MARKERS = tuple(
    _norm_cell(x)
    for x in (
        "미산정", "산정하지 않", "산정 불가", "알 수 없", "확인할 수 없",
        "확인 불가", "자료 없음", "자료가 없", "자료 부족", "미확보",
        "확인 필요", "구하지 못", "조사하지 않", "측정하지 않", "없음",
        "없다", "없습니다", "해당 없음", "제외",
    )
)


def _footer_context_filled(lines, pattern):
    for line in lines:
        if not pattern.search(line):
            continue
        nline = _norm_cell(line)
        if any(m in nline for m in _ABSENT_MARKERS):
            continue
        # 값·날짜·좌표가 함께 적힌 문장만 자료 기록으로 본다.
        if not re.search(r"[\d:：°]", line):
            continue
        return True
    return False


def _disaster_tier(cell):
    """빈도 열 분류: 가장 자주=0, 가끔=1, 자주=2. 괄호 예시 목록("등")이
    든 라벨 셀은 빈도 열로 세지 않는다."""
    if "(" in cell and "등" in cell:
        return None
    n = _norm_cell(cell)
    if "가장" in n and "자주" in n and "발생" in n:
        return 0
    if "가끔" in n and "발생" in n:
        return 1
    if "자주" in n and "발생" in n:
        return 2
    return None


def _disaster_frequency_status(rows):
    """"none": 빈도 3열 구조 없음, "empty": 구조만 있고 유효 자료 행 없음,
    "ok": 헤더 아래 빈도 열에 채워진 셀이 있는 자료 행 존재."""
    for r, row in enumerate(rows):
        tiers = {}
        for c, cell in enumerate(row):
            t = _disaster_tier(cell)
            if t is not None:
                tiers[t] = c
        if len(tiers) != 3:
            continue
        cols = frozenset(tiers.values())
        ok = any(
            _filled_cell(rows[r2][c])
            for r2 in range(r + 1, len(rows))
            for c in cols
            if c < len(rows[r2])
        )
        return "ok" if ok else "empty"
    return "none"


# 행정구역 단계: 상위→하위 나열(도 > 시·군·구 > 읍·면 > 동·리·가)은
# 하나의 지역 표시이지 개인 공저자 나열이 아니다.
_GEO_LEVELS = {
    "도": 1, "시": 2, "군": 2, "구": 3, "읍": 3,
    "면": 3, "동": 4, "리": 5, "가": 5,
}
# 시·도 약칭(전북, 경남, 서울 등)은 접미 없이도 최상위 행정구역이다.
_GEO_NAMES = frozenset(
    "강원 경기 경남 경북 전남 전북 충남 충북 제주 세종 "
    "서울 부산 대구 인천 광주 대전 울산".split()
)
# 기관명 접미. 이 접미로 끝나는 토큰은 개인 성명으로 보지 않는다.
_ORG_TAILS = (
    "청", "부", "처", "위원회", "협회", "학회", "조합", "연맹", "총회",
    "중앙회", "연구소", "연구원", "개발원", "진흥원", "과학원", "기술원",
    "시험원", "관리원", "정보원", "대학교", "대학", "학교", "센터",
    "본부", "지부", "지국", "사업소", "사업단", "공사", "공단", "농협",
    "축협", "수협", "출판사", "신문사", "방송국", "도서관", "박물관",
    "미술관", "병원", "보건소", "사무소", "법인", "작목반", "영농조합",
    "마을",
)


def _geo_level(token):
    if token in _GEO_NAMES:
        return 1
    return _GEO_LEVELS.get(token[-1:])


def _is_org(token):
    return any(token.endswith(tail) for tail in _ORG_TAILS)


def _geo_descending(a, b):
    la, lb = _geo_level(a), _geo_level(b)
    return la is not None and lb is not None and la < lb


def _swot_placed(body_nodes):
    """Ⅲ-2 아래 마 단절 제목에 SWOT(또는 강점·약점·기회·위협 전부)가
    있으면 참. 다른 장·절의 제목과 본문의 단순 언급은 인정하지 않는다."""
    chapter = section = None
    roman = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "IV": "Ⅳ", "V": "Ⅴ", "VI": "Ⅵ"}
    for n in body_nodes:
        if n.get("kind") != "heading":
            continue
        t = n["text"].strip()
        m = re.match(r"^([ⅠⅡⅢⅣⅤⅥ])\s*[\.．]", t) or re.match(
            r"^(III|II|IV|VI|I|V)\s*[\.．]", t
        )
        if m:
            chapter = roman.get(m[1], m[1])
            section = None
            continue
        m = re.match(r"^(\d+)\s*[\.．]", t)
        if m:
            section = int(m[1])
            continue
        if chapter != "Ⅲ" or section != 2:
            continue
        if re.match(r"^마\s*[\.．\)]|^\(마\)", t) and (
            "SWOT" in t
            or all(k in t for k in ("강점", "약점", "기회", "위협"))
        ):
            return True
    return False


def parse(text, *, with_spans=False, _with_term_positions=False):
    raw, origin = _draft_region(text) if _with_term_positions else (draft(text), 0)
    lines, nodes, i = raw.splitlines(), [], 0
    line_offsets, offset = [], origin
    if _with_term_positions:
        for line in raw.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(line)
    previous_prose = False
    heading_state = _HeadingState()
    def append(node, term_parts=()):
        if with_spans:
            node.update(start_line=start_line, end_line=i)
        if _with_term_positions:
            node["term_parts"] = term_parts
        nodes.append(node)

    while i < len(lines):
        start_line = i
        s = lines[i].strip()
        source_start = (line_offsets[i] + len(lines[i]) - len(lines[i].lstrip())
                        if _with_term_positions else 0)
        i += 1
        if not s:
            previous_prose = False
            continue
        if s.startswith("|"):
            rows, term_parts = [], []
            while True:
                row = [x.strip() for x in s.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", x) for x in row):
                    rows.append(row)
                    if _with_term_positions:
                        left = len(s) - len(s.lstrip("|"))
                        right = len(s.rstrip("|"))
                        cursor = left
                        for cell in s[left:right].split("|"):
                            term_parts.append((cell.strip(), source_start + cursor
                                               + len(cell) - len(cell.lstrip())))
                            cursor += len(cell) + 1
                if i >= len(lines) or not lines[i].strip().startswith("|"):
                    break
                s = lines[i].strip()
                if _with_term_positions:
                    source_start = line_offsets[i] + len(lines[i]) - len(lines[i].lstrip())
                i += 1
            append({"kind": "table", "rows": rows}, term_parts)
        elif IMAGE.fullmatch(s):
            m = IMAGE.fullmatch(s)
            append({"kind": "image", "alt": m[1], "path": m[2]},
                   [(m[1], source_start + m.start(1)), (m[2], source_start + m.start(2))])
        elif CAPTION.fullmatch(s):
            m = CAPTION.fullmatch(s)
            append(
                {"kind": "caption", "label": m[1], "number": int(m[2]), "text": s}
            )
        else:
            md = re.match(r"^(#{1,6})\s+(.+)$", s)
            level, s = heading_state.read(s)
            if md:
                source_start += md.start(2)
            list_item = False
            bullet = re.match(r"^[-+*]\s+", s)
            if bullet:
                list_item = True
                s = re.sub(r"^[-+*]\s+", "", s)
                source_start += bullet.end()
            if (
                s.startswith(("```", "<table", "<script", "> "))
                or re.search(r"\[[^\]]+\]\([^)]+\)", s)
                or re.search(
                    r"`|~~|__|<\/?[A-Za-z][^>]*>|^#{7,}\s|(?<!\w)_[^_]+_", s
                )
            ):
                raise ValueError("미지원 Markdown 요소: " + s[:80])
            tail = None
            if level and not list_item:
                # "가. 소제목: 본문 문장"처럼 한 줄에 소제목과 내용이 붙은 경우
                # 소제목만 제목으로 두고 본문은 별도 단락으로 분리한다.
                # "라. 모델농장분석 : 부제"처럼 콜론 앞에 공백이 있는
                # 부제 표기는 그대로 유지한다.
                m = re.search(r"\S:\s+(\S.*)$", s)
                if m:
                    head = s[: m.start() + 1].rstrip()
                    rest = m.group(1).strip()
                    if head and rest:
                        tail_start = source_start + m.start(1)
                        s, tail = head, rest
            node = {"kind": "heading" if (level and not list_item) else "paragraph", "text": s}
            if level:
                node["level"] = level
            if list_item:
                node["list_item"] = True
            prose = node["kind"] == "paragraph" and not list_item
            if prose and previous_prose:
                node["soft_continue"] = True
            append(node, [(s, source_start)])
            previous_prose = prose
            if tail:
                append({"kind": "paragraph", "text": tail}, [(tail, tail_start)])
                previous_prose = True
            # A Markdown hard break terminates this line's paragraph boundary.
            if lines[i - 1].endswith("  "):
                previous_prose = False
            continue
        previous_prose = False
    return nodes


def spans(rows):
    if not rows or not rows[0] or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError("표 열 수 불일치")
    anchors = {}
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v == "^":
                if r == 0:
                    raise ValueError("첫 행 세로 병합")
                a = anchors[r - 1, c]
            elif v == "<":
                if c == 0:
                    raise ValueError("첫 열 가로 병합")
                a = anchors[r, c - 1]
            else:
                a = (r, c)
            anchors[r, c] = a
    groups = {}
    for pos, a in anchors.items():
        groups.setdefault(a, []).append(pos)
    result = []
    for a, cells in groups.items():
        r1, c1 = max(r for r, c in cells), max(c for r, c in cells)
        if set(cells) != {
            (r, c) for r in range(a[0], r1 + 1) for c in range(a[1], c1 + 1)
        }:
            raise ValueError("비직사각형 병합")
        if len(cells) > 1:
            result.append((a[0], a[1], r1, c1))
    return result


def section_body(nodes, i):
    level = nodes[i].get("level") or 1
    parts = [nodes[i]["text"]]
    for extra in nodes[i + 1 :]:
        if extra["kind"] == "heading" and (extra.get("level") or 99) <= level:
            break
        if extra["kind"] in {"paragraph", "heading", "caption"}:
            parts.append(extra["text"])
        elif extra["kind"] == "table":
            parts.append("\n".join(" ".join(r) for r in extra["rows"]))
    return "\n".join(parts)


def require_tokens(issues, cid, body, tokens, reason):
    missing = [x for x in tokens if x not in body]
    if missing:
        issues.append((cid, reason + ": " + ", ".join(missing)))


def check(text, base, major_id=None):
    nodes = parse(text, with_spans=True, _with_term_positions=True)
    issues = []
    if major_id is None:
        major_id = _project_major(base)
    policy = plain_policy_check(text, issues, nodes, major_id)
    full = set()
    for n in nodes:
        if n["kind"] in {"paragraph", "heading"}:
            for m in SCI.finditer(n["text"]):
                g, species = m.groups()
                if g.endswith("."):
                    if not any(x.startswith(g[0]) and y == species for x, y in full):
                        issues.append(
                            (
                                "scientific_name",
                                "학명 전체표기가 본문 앞에 없음: " + m[0],
                            )
                        )
                else:
                    full.add((g, species))
            if re.search(
                r"\d(?:kg|㎏|mg|cm|mm|㎡|ha|mL|kg|g|m|L)(?=$|[^A-Za-z])", n["text"]
            ):
                issues.append(("unit_spacing", "수치·단위 붙여쓰기: " + n["text"]))
    for label in ("표", "그림"):
        caps = [n for n in nodes if n["kind"] == "caption" and n["label"] == label]
        if [n["number"] for n in caps] != list(range(1, len(caps) + 1)):
            issues.append(("global_numbering", label + " 전역 번호 중복/누락"))
        prose = "\n".join(n["text"] for n in nodes if n["kind"] == "paragraph")
        for n in caps:
            if not re.search(
                r"(?<![가-힣A-Za-z0-9])"
                + label
                + r"\s*"
                + str(n["number"])
                + r"(?!\d)",
                prose,
            ):
                issues.append(("object_reference", n["text"] + " 본문 참조 없음"))
        numbers = {n["number"] for n in caps}
        for m in re.finditer(r"(?<![가-힣A-Za-z0-9])" + label + r"\s*(\d+)", prose):
            if int(m[1]) not in numbers:
                issues.append(("missing_caption", m[0] + " 참조의 캡션 없음"))
    for i, n in enumerate(nodes):
        if n["kind"] == "caption":
            j = i + 1 if n["label"] == "표" else i - 1
            kind = "table" if n["label"] == "표" else "image"
            if j < 0 or j >= len(nodes) or nodes[j]["kind"] != kind:
                pending_photo = (
                    policy and n["label"] == "그림"
                    and _placeholder_figure(nodes, i, i, policy)
                )
                if not pending_photo:
                    issues.append(("missing_object", n["text"] + " 실제 객체 없음"))
        if n["kind"] == "image":
            if (
                i + 1 >= len(nodes)
                or nodes[i + 1]["kind"] != "caption"
                or nodes[i + 1]["label"] != "그림"
            ):
                issues.append(("missing_caption", "그림 객체의 캡션 없음"))
            p = Path(n["path"])
            if (
                p.is_absolute()
                or "://" in str(p)
                or base is None
                or not (Path(base) / p).is_file()
                or not (Path(base) / p).resolve().is_relative_to(Path(base).resolve())
            ):
                issues.append(("missing_image", n["path"]))
        if n["kind"] == "table":
            if (
                i == 0
                or nodes[i - 1]["kind"] != "caption"
                or nodes[i - 1]["label"] != "표"
            ):
                issues.append(("missing_caption", "표 객체의 캡션 없음"))
            try:
                spans(n["rows"])
            except ValueError as e:
                issues.append(("table_merge", str(e)))
            content = "\n".join(" ".join(r) for r in n["rows"])
            if re.search(r"평균\s*기온", content):
                # 각주 항목은 표 안쪽 라벨 행 또는 표 바로 아래 주·자료
                # 문맥에서만 확인한다. 다른 표나 본문 요약이 누락을
                # 덮지 않는다.
                footer_lines = []
                for extra in nodes[i + 1 : i + 4]:
                    if extra["kind"] in {"paragraph", "caption"}:
                        footer_lines.extend(extra["text"].splitlines())
                missing = [
                    x
                    for x in CLIMATE
                    if not _field_filled(n["rows"], CLIMATE_PATTERNS[x])
                ]
                missing += [
                    x
                    for x in CLIMATE_FOOTER
                    if not _field_filled(n["rows"], CLIMATE_FOOTER_LABEL[x])
                    and not _footer_context_filled(
                        footer_lines, CLIMATE_FOOTER_CONTEXT[x]
                    )
                ]
                if missing:
                    issues.append(
                        (
                            "climate_fields",
                            "학교 기상 필수항목 누락: " + ", ".join(missing),
                        )
                    )
            if any(x in content for x in DISASTER_FREQ + DISASTER_TYPES):
                status = _disaster_frequency_status(n["rows"])
                if status == "none":
                    issues.append(
                        (
                            "disaster_fields",
                            "학교 재해 빈도 구조 누락: " + ", ".join(DISASTER_FREQ),
                        )
                    )
                elif status == "empty":
                    issues.append(
                        (
                            "disaster_fields",
                            "학교 재해 빈도표 유효 자료 행 없음",
                        )
                    )
            if any(x in content for x in FARM_MARKERS):
                missing = [
                    x
                    for x in (
                        "농장명",
                        "경영주",
                        "농장위치",
                        "가족사항",
                        "주요작목",
                    )
                    if x not in content
                ]
                if "경영주" not in content and "농장주" in content:
                    missing = [x for x in missing if x != "경영주"]
                if missing:
                    issues.append(
                        (
                            "farm_overview_fields",
                            "학교 농장개요 필수항목 누락: " + ", ".join(missing),
                        )
                    )
    toc_range = school_toc_range(nodes)
    toc_start, body_start = toc_range if toc_range else (-1, -1)
    for i, n in enumerate(nodes):
        # These locations/content slots belong to the specialty-crop
        # outline. Peer majors have their own contracts, not this fallback.
        if major_id != "specialty_crops" or n["kind"] != "heading":
            continue
        if toc_start <= i < body_start:
            continue
        body = "\n".join(section_body(nodes, i).splitlines()[1:])
        if re.search(r"[ⅤV]\s*[\.．]", n["text"]) and "맺음말" in n["text"]:
            require_tokens(
                issues,
                "closing_elements",
                body,
                ("결론", "전략", "보완"),
                "Ⅴ 맺음말 어휘 미관측(충족 판정은 독립 내용검토)",
            )
        if re.search(r"시장환경", n["text"]):
            require_tokens(
                issues,
                "market_sections",
                body,
                ("수급", "소비", "가격", "유통"),
                "Ⅱ-2 시장환경 소절 누락",
            )
        if re.search(r"[ⅠI]\s*[\.．]", n["text"]) and "머리말" in n["text"]:
            require_tokens(
                issues,
                "intro_topics",
                body,
                ("형태", "방법", "조건", "전망", "애로", "비전"),
                "Ⅰ 머리말 어휘 미관측(충족 판정은 독립 내용검토)",
            )
        if "지리" in n["text"] and ("사회" in n["text"] or "경제" in n["text"]):
            require_tokens(
                issues,
                "geo_socio",
                body,
                ("지리", "생산", "유통"),
                "Ⅱ-1-다 지리·사회·경제 소절 누락",
            )
        if (
            (re.search(r"^\s*5\s*[\.．]", n["text"]) or n.get("level") in (1, 2))
            and "가공" in n["text"]
            and not re.match(r"^[가-힣]\s*[\.．\)]", n["text"].strip())
        ):
            require_tokens(
                issues,
                "process_plan",
                body,
                ("정의", "전망", "시장", "생산"),
                "Ⅲ-5 가공·판매 어휘 미관측(충족 판정은 독립 내용검토)",
            )
        if "관련" in n["text"] and "논문" in n["text"]:
            entries = {
                line.split("계획 연결:", 1)[0].strip()
                for line in body.splitlines()
                if re.search(r"\b(?:19|20)\d{2}\b", line)
            }
            if len(entries) < 3:
                issues.append(
                    ("related_papers", "관련 논문 3편 미만(연도 포함 개별 서지 기준)")
                )
        if re.search(r"가족구성|성장목표", n["text"]):
            if not re.search(r"천원", body) or not re.search(r"㎡", body):
                issues.append(
                    (
                        "plan_year_units",
                        "가족구성·성장목표 매출/순이익은 천원, 면적은 ㎡",
                    )
                )
        if re.search(r"[ⅥVI]+\s*[\.．]", n["text"]) and "참고문헌" in n["text"]:
            if not re.search(r"\d{4}", body):
                issues.append(("bibliography_year", "참고문헌 출판연도 없음"))
            for line in body.splitlines():
                m = re.search(r"\b(?:19|20)\d{2}\b", line)
                if not m:
                    continue
                author_part = line[: m.start()].strip().rstrip(".")
                if re.search(r"[가-힣]", author_part):
                    # 완전한 2–4음절 이름 토큰만 짝짓는다. 경계가 없으면
                    # "농촌진흥청 국립원예특작과학원" 같은 긴 기관명이 잘려
                    # 두 명의 개인으로 오인된다. 기관명 접미 토큰과
                    # 상위→하위 행정구역 나열(예: 전북 고창군 신림면)은
                    # 개인 공저가 아니므로 제외한다.
                    korean_name = r"(?<![가-힣])[가-힣]{2,4}(?![가-힣])"
                    names = list(re.finditer(korean_name, author_part))
                    has_multi = any(
                        re.fullmatch(
                            r"\s*(?:,|&|and\b)?\s*",
                            author_part[a.end() : b.start()],
                        )
                        and not (_is_org(a[0]) or _is_org(b[0]))
                        and not _geo_descending(a[0], b[0])
                        for a, b in zip(names, names[1:])
                    )
                    if has_multi and "ㆍ" not in author_part:
                        issues.append(
                            ("bibliography_join", "국문 저자 연결 기호 ㆍ 없음")
                        )
                        break
        if re.search(r"SWOT", n["text"]) and re.match(r"4\s*[\.．]", n["text"]):
            issues.append(("school_swot", "SWOT는 Ⅲ-2-마이지 Ⅱ-4가 아님"))
        if re.search(r"[ⅥVI]+\s*[\.．]", n["text"]) and "참고문헌" in n["text"]:
            if re.search(r"관련\s*논문", body):
                issues.append(("related_papers_place", "관련 논문은 Ⅲ-6이지 Ⅵ이 아님"))
    school_structure(text, issues, nodes=nodes, major_id=major_id)
    return issues


FRONT = ("겉표지", "인준서", "표제면", "제출서", "목차")
CHAPTERS = (
    (r"[ⅠI]\s*[\.．].*머리말", "머리말"),
    (r"[ⅡII]+\s*[\.．].*외부환경", "외부환경"),
    (r"[ⅢIII]+\s*[\.．].*영농계획", "영농계획"),
    (r"[ⅣIV]+\s*[\.．].*재무", "재무"),
    (r"[ⅤV]\s*[\.．].*맺음말", "맺음말"),
    (r"[ⅥVI]+\s*[\.．].*참고문헌", "참고문헌"),
)


def school_frontmatter_plan(nodes):
    """Locate school logical front sections in either supported order.

    Legacy drafts emitted approval before title/submission.  The official form
    order is title/submission before approval.  Both are accepted so generic
    Markdown exports remain compatible; callers can inspect ``order`` to choose
    physical page composition.
    """
    front_indices = {}
    for i, n in enumerate(nodes):
        t = n.get("text", "").strip()
        if t in FRONT and t not in front_indices:
            front_indices[t] = i
    if len(front_indices) != len(FRONT):
        return None
    orders = (
        ("겉표지", "인준서", "표제면", "제출서", "목차"),
        ("겉표지", "표제면", "제출서", "인준서", "목차"),
    )
    order = next((candidate for candidate in orders if all(
        front_indices[candidate[i]] < front_indices[candidate[i + 1]]
        for i in range(len(candidate) - 1)
    )), None)
    if order:
        i_toc = front_indices["목차"]
        # 본문은 목차 항목 뒤부터 시작한다. 목차의 마지막 항목(감사의 글)은 뒤에
        # 본문 절이 이어지고, 본문 끝의 감사의 글은 뒤가 비어 있어 둘을 구분한다.
        i_body = None
        for i in range(i_toc + 1, len(nodes)):
            if nodes[i].get("text", "").strip() == "감사의 글" and any(
                re.match(r"^[ⅠⅡⅢⅣⅤⅥ]\s*[\.．]", n.get("text", ""))
                for n in nodes[i + 1 :]
            ):
                i_body = i + 1
                break
        if i_body is None:
            for i in range(len(nodes) - 1, i_toc, -1):
                if re.search(r"^[ⅠI]\s*[\.．].*머리말", nodes[i].get("text", "")):
                    i_body = i
                    break
        if i_body is None:
            i_body = i_toc + 1
        return {
            "indices": front_indices,
            "order": order,
            "toc": i_toc,
            "body": i_body,
            "official_order": order == orders[1],
        }
    return None


def school_toc_range(nodes):
    plan = school_frontmatter_plan(nodes)
    if plan:
        return plan["toc"], plan["body"]
    return None


def _peer_swot_paths(major_id):
    """Only explicitly declared outline locations are structural rules.

    Fruit S01 is declared in the registry. Horticulture's optional plan
    links HT1/HT2 profile locations; neither precedent imposes six chapters
    or mandatory front matter. Majors without these contracts are skipped.
    """
    import gg_major_contract as mc

    if major_id not in {"fruit_trees", "hort_env_systems"}:
        return ()
    module = mc.default_registry().resolve(major_id)
    paths = set()
    if major_id == "fruit_trees":
        for node in module.document_plan:
            if node.role != "swot":
                continue
            match = re.match(r"([ⅠⅡⅢⅣⅤⅥ]|III|II|IV|VI|I|V)-(\d+)\b", node.rationale or "")
            if match:
                paths.add((_ROMAN_NUMBERS[match[1]], int(match[2])))
    else:
        refs = {ref for n in module.document_plan if n.role == "environment_analysis"
                for ref in n.precedent_refs}
        if not refs:
            return ()
        profile = json.loads(HORT_PROFILE_PATH.read_text(encoding="utf-8"))
        if profile["major_id"] != major_id:
            raise ValueError("원예 목차 프로필 전공 불일치")
        for precedent in profile["precedents"]:
            if precedent["ref"] not in refs:
                continue
            for section in precedent["sections"]:
                if not re.search(r"SWOT", section["title"], re.IGNORECASE):
                    continue
                match = re.fullmatch(r"ch(\d+)_sec(\d+)", section["item"])
                if match:
                    paths.add(tuple(int(x) for x in match.groups()))
    return tuple(sorted(paths))


def _check_peer_swot(body_nodes, paths, issues):
    if not paths:
        return
    chapter = section = None
    expected = ", ".join("%s-%d" % ("ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"[c - 1], s) for c, s in paths)
    for n in body_nodes:
        if n.get("kind") != "heading":
            continue
        title = n["text"].strip()
        if n.get("level") == 1:
            numbered = _numbered_heading(title)
            chapter = numbered[1] if numbered else None
            section = None
        elif n.get("level") == 2:
            match = re.match(r"^(\d+)\s*[.．]", title)
            section = int(match[1]) if match else None
        if re.search(r"SWOT", title, re.IGNORECASE) and (
            n.get("level") != 2 or (chapter, section) not in paths
        ):
            issues.append(("school_swot", "선택 전공 목차의 SWOT는 " + expected))


def school_structure(text, issues, nodes=None, major_id=None):
    if nodes is None:
        nodes = parse(text)
    toc_range = school_toc_range(nodes)
    if toc_range:
        body_nodes = nodes[toc_range[1] :]
        body_text = "\n".join(n.get("text", "") for n in body_nodes)
    else:
        body_nodes = nodes
        body_text = text

    if major_id != "specialty_crops":
        try:
            _check_peer_swot(body_nodes, _peer_swot_paths(major_id), issues)
        except (OSError, ValueError, KeyError, TypeError) as e:
            issues.append(("school_structure_policy", "전공 목차 계약 읽기 실패: %s" % e))
        return issues

    n_chapters = sum(1 for pat, _ in CHAPTERS if re.search(pat, body_text))
    has_front = all(x in text for x in FRONT)
    if not has_front and n_chapters < 3:
        return
    missing_front = [x for x in FRONT if x not in text]
    if missing_front:
        issues.append(("school_front", "머리지면 누락: " + ", ".join(missing_front)))
    missing_ch = [name for pat, name in CHAPTERS if not re.search(pat, body_text)]
    if missing_ch:
        issues.append(("school_chapters", "Ⅰ–Ⅵ 필수장 누락: " + ", ".join(missing_ch)))
    if "감사의 글" not in body_text:
        issues.append(("school_thanks", "감사의 글 없음"))
    elif body_text.rfind("감사의 글") < body_text.rfind("참고문헌"):
        issues.append(("school_thanks", "감사의 글은 참고문헌 뒤에"))
    if "SWOT" in body_text and not _swot_placed(body_nodes):
        issues.append(("school_swot", "SWOT는 Ⅲ-2-마"))
    writing = re.search(r"작성연도\s*(\d{4})", text)
    if writing:
        start = int(writing[1]) + 1
        if str(start) not in text or str(start + 4) not in text:
            issues.append(
                ("plan_year_calc", "창업연도=작성연도+1, 목표연도=창업연도+4 불일치")
            )
    return issues
