"""전공별 학교 서식 프로필 - 쪽별 서식·앞부분 쪽 나눔의 단일 표.

식량작물(F1/F1b)에서 `major_id == "food_crops"` 분기로 짠 장치를 한 표로
일반화한다. 프로필이 없는 전공은 `profile_for()`가 None을 돌려주고 빌더는
기존 기본 경로를 그대로 탄다(출력 바이트 불변).

값의 출처는 두 가지뿐이다.
- `food_crops`: F1b `41d3f97`의 현행 상수 그대로(리팩터 기준선).
- `dairy`: `DESIGN-v1.md` §F-0 확정값(작성요령 > 정명현 5차 견본 > 선배).

표를 고칠 때는 빌더가 아니라 이 파일을 고친다. 검사기
(`gg_school_format*.py`)는 이 표를 읽지 않고 자기 안에 값을 따로 적는다
(I-07: 빌더 표를 그대로 읽는 검사기는 틀린 표를 못 잡는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

BLACK = (0, 0, 0)

# 1판 header.xml에 없는 (포인트, 굵기) 조합만 만든다. front_titles·cover_text가
# 쓰는 크기 집합의 합집합이다.
FRONT_CHARPR_SPECS = (
    (10, False), (12, False),
    (14, True), (14, False),
    (16, True), (16, False),
    (18, True), (18, False),
    (20, True), (20, False),
    (22, True), (22, False),
    (32, True), (32, False),
)

# 구분 이름(`겉표지`·`표제면`·`제출서`·`인준서`)은 우리 문서 계획이 만든
# 관리용 줄이다. HWPX는 문단을 남기고 글자만 비운다(문단 수 1:1 계약).
HIDDEN_FRONT_ROLES = ("겉표지", "표제면", "제출서", "인준서")
# 쪽을 나누는 역할. `겉표지`는 구역 첫 문단, `요약`은 구역 시작 자체가 나눔.
BREAK_FRONT_ROLES = ("표제면", "제출서", "인준서")
TOC_ROLE = "목차"
TOC_SUB_HEADS = ("표 목차", "그림 목차")
# 요약 안 번호형 소제목(`1. 농장 개요`) 판정. 앞부분 글자 크기 규칙에 쓴다.
SUMMARY_ITEM_RE = re.compile(r"^\d+\s*\.")


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _sections(*rows):
    """구역 3개(cover/front/body) 여백·쪽번호 표를 불변으로 만든다."""
    return tuple(_freeze(row) for row in rows)


@dataclass(frozen=True)
class MajorFormatProfile:
    """한 전공의 쪽별 서식. 안쪽 매핑까지 불변(R-03)."""

    major_id: str
    section_formats: tuple
    # 본문 1~9단계 제목 크기(pt)
    level_pt: object
    # 앞부분·특수 쪽 제목: 역할 -> (pt, 굵기)
    front_titles: object
    # 요약 안 번호형 소제목 / 그 밖의 요약 본문
    summary_item_pt: tuple
    # 학부모 동의서 본문·`※` 주석
    consent_body_pt: tuple
    consent_note_pt: tuple
    # 겉표지·제출서·인준서·인준 문구 줄: 종류 -> (pt, 굵기)
    cover_text: object
    # 요약 앞에서 로마 대문자 구역을 여는가
    roman_front: bool
    # DOCX 본문 경계 뒤 1단계 제목(첫 장 제외)마다 새 쪽
    chapter_break_nodes: bool
    # DOCX 바닥글 쪽번호 꾸밈(`- 1 -`). None이면 맨 숫자만.
    page_label: object = None
    # HWPX 장 새 쪽 판정: "nodes"(노드 색인) / "regex"(문단 글자)
    hwpx_chapter: str = "nodes"
    hwpx_chapter_re: object = None
    # 제목 위아래 1행(0이면 미적용)
    heading_spacing_pt: float = 0.0
    heading_prev_hwpunit: int = 0
    heading_next_hwpunit: int = 0
    # 학부모 동의서가 본문 끝에 있는가(있으면 본문 문단 서식을 잠근다)
    consent_in_body: bool = False
    finish_notice: str = ""
    human_finish: tuple = ()
    # 목차 제목 크기(pt) - `contents()`가 따로 받는 값
    toc_title_pt: int = 20
    # 본문 문단(제목이 아닌 글) 크기(pt). 한글 빌더가 이 값으로 본문 charPr를
    # 만들고, 단계 제목은 `level_pt`를 쓴다(워드 빌더와 같은 표를 본다).
    body_pt: int = 12


# --- 식량작물: F1b 현행 값 그대로 -----------------------------------------

FOOD_CROPS_FINISH_NOTICE = (
    "저수준 미검증 출력. 목차 필드 갱신·전체 페이지·학교 원문·학명·그림·표·"
    "머리행 검토 필요. 한글에서 글꼴 신명조·여백(위30/아래30/머리0/꼬리10/"
    "좌30/우30/제본0 mm)·쪽 번호(요약은 Ⅰ부터, 본문은 1부터)를 확인한다. "
    "이 세 가지는 스킬 작성 완료를 막지 않으며 자동 통과하지 않는다. "
    "file_hash는 빌드 직후 파일 기준이다. 쪽 나눔·여백 등 후처리로 파일을 "
    "다시 저장했다면 --rehash로 매니페스트 해시를 갱신한다."
)
FOOD_CROPS_HUMAN_FINISH = (
    "표지·제출면·인준서의 쪽 나눔과 가운데 정렬",
    "목차의 실제 쪽번호",
    "표·그림 목차(있으면)",
    "표 폭이 본문 폭(150 mm)인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
    "장(Ⅰ~Ⅵ) 시작이 새 쪽인지 확인 — 자동으로 쪽 나눔을 넣었지만 한글에서 확인한다",
    "요약이 Ⅰ부터, 본문이 1부터인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
)

# 식량작물 본문은 아라비아 장 번호를 쓰고, 맺음말·참고문헌·학부모 동의서는
# 번호 없는 제목으로 새 쪽에서 시작한다.
FOOD_CROPS_CHAPTER_RE = re.compile(
    r"^\s*(?:[1-9]\s*[.．]\s*\S|맺음말\s*및\s*발전방향|참고문헌|학부모\s*동의서)")

FOOD_CROPS_PROFILE = MajorFormatProfile(
    major_id="food_crops",
    section_formats=_sections(
        {
            "role": "cover",
            "margin_mm": {"header": 0, "footer": 10, "gutter": 0, "left": 20,
                          "right": 20, "top": 20, "bottom": 10},
            "page_number": {"pos": "NONE", "format": "DIGIT", "restart": None},
        },
        {
            "role": "front",
            "margin_mm": {"header": 0, "footer": 10, "gutter": 0, "left": 30,
                          "right": 30, "top": 30, "bottom": 30},
            "page_number": {"pos": "BOTTOM_CENTER", "format": "ROMAN_CAPITAL",
                            "restart": 1},
        },
        {
            "role": "body",
            "margin_mm": {"header": 0, "footer": 10, "gutter": 0, "left": 30,
                          "right": 30, "top": 30, "bottom": 30},
            "page_number": {"pos": "BOTTOM_CENTER", "format": "DIGIT",
                            "restart": 1},
        },
    ),
    level_pt=_freeze({1: 20, 2: 18, 3: 16, 4: 14, 5: 13, 6: 13, 7: 13,
                      8: 13, 9: 13}),
    front_titles=_freeze({
        "요약": (22, True), "감사의 글": (20, True), "학부모 동의서": (20, True),
        "목차": (22, True), "표 목차": (20, False), "그림 목차": (20, False),
    }),
    summary_item_pt=(16, True),
    consent_body_pt=(14, True),
    consent_note_pt=(10, False),
    cover_text=_freeze({
        "degree": (18, True),
        "plan": (32, True),
        "statement": (18, True),
        "advisor": (18, True),
        "approval": (18, True),
        "default": (20, False),
    }),
    roman_front=True,
    chapter_break_nodes=True,
    page_label=None,
    hwpx_chapter="regex",
    hwpx_chapter_re=FOOD_CROPS_CHAPTER_RE,
    heading_spacing_pt=0.0,
    consent_in_body=True,
    finish_notice=FOOD_CROPS_FINISH_NOTICE,
    human_finish=FOOD_CROPS_HUMAN_FINISH,
    toc_title_pt=22,
)

# --- 낙농: DESIGN-v1 §F-0 ------------------------------------------------

# 세 구역 여백이 같다(작성요령 5절: 위·아래·좌·우 30, 머리 0, 꼬리 10).
_DAIRY_MARGIN = {"header": 0, "footer": 10, "gutter": 0, "left": 30,
                 "right": 30, "top": 30, "bottom": 30}

DAIRY_FINISH_NOTICE = (
    "저수준 미검증 출력. 목차 필드 갱신·전체 페이지·학교 원문·그림·표·"
    "머리행 검토 필요. 한글에서 글꼴 신명조·여백(위30/아래30/머리0/꼬리10/"
    "좌30/우30/제본0 mm)·쪽 번호(요약은 - I -부터, 본문은 - 1 -부터)를 "
    "확인한다. 이 세 가지는 스킬 작성 완료를 막지 않으며 자동 통과하지 않는다. "
    "file_hash는 빌드 직후 파일 기준이다. 쪽 나눔·여백 등 후처리로 파일을 "
    "다시 저장했다면 --rehash로 매니페스트 해시를 갱신한다."
)
DAIRY_HUMAN_FINISH = (
    "표지·제출서·인준서·학부모 동의서의 쪽 나눔과 가운데 정렬",
    "목차의 실제 쪽번호",
    "표·그림 목차(있으면)",
    "표 폭이 본문 폭(150 mm)인지 확인 - 자동으로 맞췄지만 한글에서 확인한다",
    "장마다 새 쪽인지 확인 - 자동으로 쪽 나눔을 넣었지만 한글에서 확인한다",
    "요약이 - I -부터, 본문이 - 1 -부터인지 확인 - 자동으로 맞췄지만 "
    "한글·Word에서 확인한다(쪽번호 모양·9pt는 렌더 눈 확인 항목)",
)

DAIRY_PROFILE = MajorFormatProfile(
    major_id="dairy",
    section_formats=_sections(
        {"role": "cover", "margin_mm": _DAIRY_MARGIN,
         "page_number": {"pos": "NONE", "format": "DIGIT", "restart": None}},
        {"role": "front", "margin_mm": _DAIRY_MARGIN,
         "page_number": {"pos": "BOTTOM_CENTER", "format": "ROMAN_CAPITAL",
                         "restart": 1}},
        {"role": "body", "margin_mm": _DAIRY_MARGIN,
         "page_number": {"pos": "BOTTOM_CENTER", "format": "DIGIT",
                         "restart": 1}},
    ),
    level_pt=_freeze({1: 20, 2: 18, 3: 16, 4: 15, 5: 14, 6: 12, 7: 12,
                      8: 12, 9: 12}),
    # 특수 쪽 제목은 전부 22pt 굵게 검정(견본 실측). 학부모 동의서는 낙농에서
    # 앞부분 쪽이라 본문 끝 동의서 규칙을 쓰지 않는다.
    front_titles=_freeze({
        "요약": (22, True), "감사의 글": (22, True), "학부모 동의서": (22, True),
        "목차": (22, True), "표 목차": (22, True), "그림 목차": (22, True),
    }),
    summary_item_pt=(16, True),
    consent_body_pt=(12, False),
    consent_note_pt=(10, False),
    cover_text=_freeze({
        "degree": (18, False),
        "plan": (32, True),
        "statement": (18, False),
        "advisor": (18, False),
        "approval": (18, False),
        "default": (20, False),
    }),
    roman_front=True,
    chapter_break_nodes=True,
    page_label=_freeze({"prefix": "- ", "suffix": " -", "pt": 9}),
    hwpx_chapter="nodes",
    hwpx_chapter_re=None,
    heading_spacing_pt=19.2,
    heading_prev_hwpunit=1920,
    heading_next_hwpunit=1920,
    consent_in_body=False,
    finish_notice=DAIRY_FINISH_NOTICE,
    human_finish=DAIRY_HUMAN_FINISH,
    toc_title_pt=22,
)

MAJOR_FORMAT_PROFILES = MappingProxyType({
    "food_crops": FOOD_CROPS_PROFILE,
    "dairy": DAIRY_PROFILE,
})


def profile_for(major_id):
    """전공 서식 프로필. 표에 없으면 None(기존 기본 경로)."""
    if not major_id:
        return None
    return MAJOR_FORMAT_PROFILES.get(major_id)


# 어느 전공이 학교 서식 프로필을 쓰는가 — 빌더 셋(본문 DOCX·앞부분 DOCX·
# HWPX)이 함께 보는 유일한 게이트. 값이 표에 있어도 여기 없으면 기본 경로를
# 탄다. 낙농(`dairy`)은 F9e에서 켰다: 이 줄 하나로 세 빌더가 함께 프로필
# 경로로 넘어간다. 목록에 없는 전공은 지금까지의 기본 경로를 그대로 탄다.
ENABLED_FORMAT_MAJORS = ("food_crops", "dairy")


def format_enabled(major_id, majors=None):
    """이 전공이 게이트 안인가.

    빌더는 자기 모듈의 옛 이름(`DOCX_PROFILE_MAJORS` 등)을 `majors`로
    넘긴다 — 값은 이 파일의 상수와 같은 객체이고, 기존 시험이 그 이름을
    패치해 게이트를 여는 통로가 그대로 살아 있다.
    """
    return bool(major_id) and major_id in (
        ENABLED_FORMAT_MAJORS if majors is None else majors)


def enabled_profile(major_id, majors=None):
    """게이트가 켠 전공의 서식 프로필. 게이트 밖이면 None(기본 경로)."""
    return profile_for(major_id) if format_enabled(major_id, majors) else None


def front_title(profile, role, default_pt=20):
    """앞부분 제목의 (pt, 굵기). 표에 없으면 보통 굵기 기본값."""
    if profile is None:
        return default_pt, False
    return tuple(profile.front_titles.get(role, (default_pt, False)))


# --- 특용작물: 게이트 밖 덮개 ---------------------------------------------
# 특용작물(`specialty_crops`)은 전공 서식 프로필을 두지 않는다. 프로필을 켜면
# 구역 여백·로마 쪽번호·장 새 쪽·앞부분 순서·안내문이 함께 켜져 출력이 통째로
# 바뀌므로, 지도교수 수정(v0.6.4)이 정한 값만 이 덮개로 따로 준다. 다른 전공은
# `None`을 받아 기존 기본 경로를 그대로 탄다(출력 바이트 불변).
SPECIALTY_CROPS = "specialty_crops"

# 제목(Ⅱ.) 20pt · 부제목(1.) 18pt · 소제목(가.) 이하 16pt 통일(형님 답).
SPECIALTY_LEVEL_PT = _freeze({1: 20, 2: 18, 3: 16, 4: 16, 5: 16, 6: 16,
                              7: 16, 8: 16, 9: 16})
# 목차·표 목차·그림 목차 제목 20pt(수정 10번)는 여기에 값이 없다: 기본 경로가 이미
# 20pt라(`build_docx.add_toc`·`contents(title_size=20)`) 특용작물 덮개가 바꿀 것이 없다.
# 시험(`test_toc_titles_are_20pt_already_in_the_base_path`)이 값만 잠근다.


def specialty_level_pt(major_id):
    """특용작물이면 단계 제목 크기 표, 아니면 None(기본 표를 쓴다)."""
    return SPECIALTY_LEVEL_PT if major_id == SPECIALTY_CROPS else None


def strip_caption_period(text, major_id):
    """특용작물 표·그림 제목 끝의 온점을 뺀다(지도교수 수정 2번).

    다른 전공은 글을 그대로 돌려준다. 끝의 마침표 문자만 걷어내고, 숫자 뒤
    소수점처럼 뒤에 글자가 이어지는 온점은 건드리지 않는다. 말줄임(`...`·`…`)으로
    끝나는 제목은 말줄임이 글의 일부이므로 `...`와 `…` 둘 다 그대로 둔다.
    """
    if major_id != SPECIALTY_CROPS:
        return text
    if re.search(r"(?:\.{3}|…|⋯)\s*$", text):
        return text.rstrip()
    return re.sub(r"\s*[.．。]+\s*$", "", text)


# 연도 열(지도교수 수정 11번): 머리칸이 연도·연차·기간을 가리키면 그 열을 가운데 정렬한다.
# 머리칸이 연도형이 아니면 값이 전부 연도 꼴이어도 우연일 수 있으므로(`면적(㎡)`에
# 2000/2020, 본문 행 하나뿐인 표) 본문 행이 2개 이상이고 값이 오름차순으로 가까이
# (앞 값과 5 이내) 이어질 때만 연도 열로 본다(2025·2026·2028처럼 건너뛰어도 된다).
# 맨 끝 `합계`·`계`·`평균` 행은 연도가 아니므로 빼고 판정한다. 다른 칸은 모두
# 왼쪽으로 맞춰 같은 표 안에서 칸마다 정렬이 달라지지 않게 한다(13번).
_YEAR_HEADER_RE = re.compile(
    r"^(?:연\s*도|년\s*도|연\s*차|기\s*간)(?:\s*별)?(?:\s*[/(（].*)?$")
_YEAR_ATOM = r"(?:(?:19|20)\d{2}\s*(?:년\s*도?)?|\d{1,2}\s*(?:년\s*)?차(?:\s*년\s*도)?)"
_YEAR_VALUE_RE = re.compile(r"^" + _YEAR_ATOM + r"(?:\s*[~∼-]\s*" + _YEAR_ATOM + r")?$")
_YEAR_NUMBER_RE = re.compile(r"\d+")
_SUMMARY_ROW_RE = re.compile(r"^(?:합\s*계|계|평\s*균)$")
_YEAR_MAX_STEP = 5


def _year_start(value):
    # 값의 첫 숫자(연도 또는 연차). `2027~2028`은 2027, `1차 년도`는 1.
    return int(_YEAR_NUMBER_RE.search(value).group())


def year_column_indexes(rows):
    """표 줄(머리 줄 포함)에서 연도 열 번호 집합. 연도 열이 없으면 빈 집합."""
    if not rows or not rows[0]:
        return frozenset()
    found = set()
    for index, header in enumerate(rows[0]):
        values = [str(row[index]).strip() for row in rows[1:]
                  if index < len(row) and str(row[index]).strip() not in ("", "^", "<")]
        if _YEAR_HEADER_RE.match(str(header).strip()):
            found.add(index)
            continue
        if values and _SUMMARY_ROW_RE.match(values[-1]):
            values = values[:-1]
        if (len(values) >= 2
                and all(_YEAR_VALUE_RE.match(value) for value in values)):
            starts = [_year_start(value) for value in values]
            if all(0 < later - earlier <= _YEAR_MAX_STEP
                   for earlier, later in zip(starts, starts[1:])):
                found.add(index)
    return frozenset(found)


_CLIMATE_KEYS = ("평균기온", "최고기온", "최저기온", "강우량", "풍속", "적설량")


def table_is_climate(rows):
    """기후표 판정(Word `add_table`과 같은 기준): 첫 칸에 기상 지표 이름이 있는가."""
    return any(any(key in (row[0] if row else "") for key in _CLIMATE_KEYS)
               for row in rows)
