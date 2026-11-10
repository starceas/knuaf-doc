"""학생에게 보이는 글의 말투·용어·전공 범위 계약 (DESIGN v0.4.8 §5, ⓖ).

``skills/knuaf-doc/`` 아래에서 ```` ```학생화면 ```` 울타리로 표시한 블록만
검사한다. 문서 전체를 검사하지 않는 이유는 같은 문서가 모델 지시용 내부
용어(정본·명령 이름·결과 코드)를 정당하게 담고 있기 때문이다. 학생에게
그대로 옮겨도 되는 문장만 울타리로 표시하고, 그 블록에 내부 말이 섞이면
이 시험이 막는다.

이 파일은 다른 갈래의 파일을 import하지 않는다(표준 라이브러리만).
``FORBIDDEN_STUDENT_TERMS`` 는 v0.4.8 통합 시험이 실제 코드 반환 문구를
같은 목록으로 검사할 수 있도록 모듈 상수로 둔다(DESIGN §5 시험 4).
"""

from pathlib import Path
import re
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "knuaf-doc"

FENCE_OPEN = "```학생화면"
FENCE_CLOSE = "```"

#: 학생에게 보이는 글에 쓰면 안 되는 말.
#: 앞 8개는 기존 금지 목록(SKILL.md·interview-ui.md), 다음 8개는 형님 맥
#: 실사례에서 새로 확인된 말, 나머지는 결과 코드·검사 이름·도구 이름이다.
#: 결과 코드는 v0.4.8에서 새로 문서화한 것까지 모두 넣는다 — 코드명이 학생
#: 안내 앞에 그대로 붙으면 이 시험이 막는다.
FORBIDDEN_STUDENT_TERMS = (
    # 기존 목록
    "정본",
    "project.json",
    "import",
    "프로세스",
    "잠금",
    "revision",
    "fact",
    "claims",
    # 형님 맥 실사례 추가(SKILL.md·interview-ui.md에 같이 넣는다)
    "바인딩",
    "개정",
    "RDA 팩",
    "격리",
    "18시트",
    "영수증 사슬",
    "지도",
    "field_id",
    # 결과 코드·검사 이름(H3·H4: 코드명 노출 0)
    "spec_field_invalid",
    "native_field_invalid",
    "invalid_spec",
    "body_finance_crosscheck",
    "body_finance_crosscheck_scope",
    "finance_plan_review_scope",
    "school_excel_17_scope",
    "farm_name_required",
    "legacy_hort_fact_conflict",
    "existing_fact_conflict",
    "fact ID",
    "사실 ID",
    # 도구·실행환경 이름
    "Kordoc",
    "gg.py",
    "gg_core",
    "gg_school_excel",
    "gg_excel_facts",
)

#: v0.4.8에서 새로 학생에게 보일 수 있는 결과 코드. 금지 목록에 하나라도
#: 빠지면 학생 안내 앞에 코드명이 그대로 남을 수 있다.
V048_RESULT_CODES = (
    "farm_name_required",
    "legacy_hort_fact_conflict",
    "existing_fact_conflict",
    "body_finance_crosscheck_scope",
    "finance_plan_review_scope",
    "school_excel_17_scope",
)

#: 전공 이름(학과 소속)으로 인식할 낱말. 등록 전공 밖의 학과 이름도 넣는다 —
#: 학생 보고에 이 학생 전공이 아닌 학과 이름이 붙으면 잡아야 하기 때문이다.
#: ``원예`` 가 ``원예환경시스템`` 을 포함하므로 겹치는 이름은 아래
#: ``_majors_in`` 이 긴 이름 하나로 합쳐 센다.
KNOWN_MAJOR_NAMES = (
    "특용작물",
    "원예환경시스템",
    "원예",
    "과수",
    "낙농",
    "산업곤충",
    "채소",
    "화훼",
    "식량작물",
    "조경",
    "축산",
    "말산업",
    "수산",
    "산림",
)

#: 전공 이름이 나오는 학생 블록과 그 블록이 말해야 하는 전공.
#: (문서, 블록을 찾는 표지 문장, 기대 전공). 표지가 사라지거나 다른 전공이
#: 섞이면 실패한다. 전공 이름이 있는데 이 표에 없는 블록도 실패한다 —
#: 그래야 ``채소전공`` 같은 목록 밖 학과 이름을 끼워 넣는 오염이 막힌다.
MAJOR_EXPECTATIONS = (
    ("references/interview-guide.md",
     "특용작물전공 / 도라지로 확인했습니다.",
     "특용작물"),
)

#: ``native_field_invalid`` 표의 행. 칸 이름(모델용)은 백틱 목록, 오른쪽은
#: 학생에게 그대로 보여 줄 말이다.
NATIVE_FIELD_ROW = re.compile(
    r"^\|\s*((?:`[a-z0-9_]+`\s*(?:,\s*`[a-z0-9_]+`\s*)*))\s*\|\s*([^|]+?)\s*\|\s*$")

#: W-D2 @Roh 권고2: 표의 키↔문구 쌍. 순서와 키 집합까지 고정해
#: 시작/목표 면적의 키를 서로 바꾸는 돌연변이를 잡는다.
NATIVE_FIELD_PAIRS = (
    (("start_area_m2", "area_m2"), "시작 면적(㎡)"),
    (("target_area_m2", "goal_area_m2"), "목표 면적(㎡)"),
    (("start_sales_thousand", "sales_thousand"), "시작 매출액(천원)"),
    (("target_sales_thousand", "goal_sales_thousand"), "목표 매출액(천원)"),
    (("start_profit_thousand", "profit_thousand"), "시작 소득(천원)"),
    (("target_profit_thousand", "goal_profit_thousand"), "목표 소득(천원)"),
)

#: W-D2 @Roh 권고2: 검색어 규칙의 '써도 됨' 쪽에 고정해 둘 말.
#: 금지 예시를 허용 쪽에 끼워 넣는 돌연변이를 잡는다.
SEARCH_ALLOW_WORDINGS = (
    "시·군 단위 지역",
    "연령대",
    "작목·품종",
    "재배 면적 규모",
    "통계·제도 이름",
)

#: 표가 학생에게 매핑해야 하는 말 6개(DESIGN §5-5). 표와 블록이 같이
#: 틀어지는 것을 막으려고 기대값을 여기에 고정한다.
NATIVE_FIELD_WORDINGS = (
    "시작 면적(㎡)",
    "목표 면적(㎡)",
    "시작 매출액(천원)",
    "목표 매출액(천원)",
    "시작 소득(천원)",
    "목표 소득(천원)",
)

#: 학생 보기의 글자 접미(A)·B))와 숫자 보기(1)).
LETTER_OPTION = re.compile(r"(?m)^\s*[A-Z][\).]\s")
DIGIT_OPTION = re.compile(r"(?m)^\s*\d\)\s")
QUESTION_HEADER = re.compile(r"^\[Q\d+(?:-\d+)?\]\s+\S")

#: 울타리 블록이 있어야 하는 문서와 문서별 최소 개수. 블록을 지워서
#: 통과하는 것을 막는다(개수를 줄이려면 이 표와 문서를 같이 고쳐야 한다).
#: 값은 v0.4.8에서 실제로 쓴 블록 수와 같다 — 블록을 지우면 이 시험이 막는다.
MIN_BLOCKS_BY_FILE = {
    "SKILL.md": 1,
    "references/interview-ui.md": 5,
    "references/interview-guide.md": 30,
    "references/outputs.md": 14,
}
MIN_BLOCKS_TOTAL = sum(MIN_BLOCKS_BY_FILE.values())


def _markdown_files():
    return sorted(SKILL_ROOT.rglob("*.md"))


def _student_blocks(path):
    """`` ```학생화면 `` 으로 열린 블록의 본문 목록을 돌려준다."""
    blocks = []
    current = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if current is None:
            if stripped == FENCE_OPEN:
                current = [number, []]
            continue
        if stripped == FENCE_CLOSE:
            blocks.append((number, "\n".join(current[1])))
            current = None
            continue
        current[1].append(line)
    if current is not None:
        raise AssertionError(
            "%s:%d: 학생화면 울타리가 닫히지 않았다" % (path, current[0]))
    return blocks


def _all_blocks():
    found = []
    for path in _markdown_files():
        for line, body in _student_blocks(path):
            found.append((path.relative_to(SKILL_ROOT).as_posix(), line, body))
    return found


def _hits(body, terms):
    lowered = body.lower()
    return [term for term in terms if term.lower() in lowered]


def _majors_in(body):
    """블록이 말하는 전공 이름을 돌려준다. 긴 이름이 이기는 방식으로 센다."""
    found = [name for name in KNOWN_MAJOR_NAMES if name in body]
    return [name for name in found
            if not any(name != other and name in other for other in found)]


def _native_field_rows():
    text = (SKILL_ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        match = NATIVE_FIELD_ROW.match(line)
        if match:
            rows.append(match.group(2))
    return rows


def _native_field_pairs():
    """(key tuple, wording) per row — keys in written order."""
    text = (SKILL_ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
    pairs = []
    for line in text.splitlines():
        match = NATIVE_FIELD_ROW.match(line)
        if not match:
            continue
        keys = tuple(re.findall(r"`([a-z0-9_]+)`", match.group(1)))
        pairs.append((keys, match.group(2)))
    return pairs


def _first_line(body):
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


class ForbiddenTermConstantTests(unittest.TestCase):
    def test_constant_is_a_flat_sequence_of_terms(self):
        self.assertIsInstance(FORBIDDEN_STUDENT_TERMS, tuple)
        self.assertGreaterEqual(len(FORBIDDEN_STUDENT_TERMS), 20)
        for term in FORBIDDEN_STUDENT_TERMS:
            with self.subTest(term=term):
                self.assertIsInstance(term, str)
                self.assertTrue(term.strip())

    def test_constant_has_no_duplicates(self):
        lowered = [term.lower() for term in FORBIDDEN_STUDENT_TERMS]
        self.assertEqual(len(lowered), len(set(lowered)))

    def test_every_v048_result_code_is_forbidden(self):
        lowered = [term.lower() for term in FORBIDDEN_STUDENT_TERMS]
        for code in V048_RESULT_CODES:
            with self.subTest(code=code):
                self.assertIn(code.lower(), lowered)


class StudentBlockTests(unittest.TestCase):
    def test_student_blocks_carry_no_internal_terms(self):
        found = _all_blocks()
        self.assertTrue(found, "학생화면 블록이 하나도 없다")
        for name, line, body in found:
            with self.subTest(file=name, line=line):
                self.assertTrue(body.strip(), "빈 학생화면 블록")
                self.assertEqual([], _hits(body, FORBIDDEN_STUDENT_TERMS))

    def test_each_document_keeps_its_student_examples(self):
        counts = {}
        for path in _markdown_files():
            relative = path.relative_to(SKILL_ROOT).as_posix()
            counts[relative] = len(_student_blocks(path))
        for name, minimum in MIN_BLOCKS_BY_FILE.items():
            with self.subTest(file=name):
                self.assertGreaterEqual(
                    counts.get(name, 0), minimum,
                    "%s: 학생화면 블록 %d개(최소 %d개)"
                    % (name, counts.get(name, 0), minimum))
        self.assertGreaterEqual(sum(counts.values()), MIN_BLOCKS_TOTAL)


class MajorExampleTests(unittest.TestCase):
    """"이 학생의 전공만 쓴다"를 문서 안에서 실제로 대조한다."""

    def test_major_bearing_blocks_match_their_expected_major(self):
        blocks = _all_blocks()
        self.assertTrue(blocks, "학생화면 블록이 하나도 없다")
        by_file = {}
        for name, line, body in blocks:
            if _majors_in(body):
                by_file.setdefault(name, []).append((line, body))
        covered = set()
        for name, anchor, expected in MAJOR_EXPECTATIONS:
            candidates = [(line, body)
                          for line, body in by_file.get(name, ())
                          if anchor in body]
            self.assertEqual(
                1, len(candidates),
                "%s: 표지 %r 블록을 찾지 못했다" % (name, anchor))
            line, body = candidates[0]
            with self.subTest(file=name, line=line, major=expected):
                self.assertEqual([expected], _majors_in(body),
                                 "이 전공 예시에 다른 전공이 섞였다")
            covered.add((name, line))
        for name, entries in by_file.items():
            for line, body in entries:
                if (name, line) in covered:
                    continue
                self.fail("%s:%d: 전공 이름 %s 이(가) 있는데 기대 전공 표에 없다"
                          % (name, line, _majors_in(body)))

    def test_no_block_names_two_majors(self):
        for name, line, body in _all_blocks():
            majors = _majors_in(body)
            with self.subTest(file=name, line=line):
                self.assertLessEqual(
                    len(majors), 1,
                    "한 학생 보고에 전공이 섞였다: %s" % (majors,))


class ProgressTitleTests(unittest.TestCase):
    """앱 1단계 파서가 제목 줄을 그대로 읽는다 — 전공 이름을 끼우지 않는다."""

    TITLE = re.compile(r"^\*\*창업논문 진행 목록\*\* · 7단계 중 \d+단계")

    def test_progress_title_matches_the_app_parser(self):
        titles = [(name, line, _first_line(body))
                  for name, line, body in _all_blocks()
                  if "창업논문 진행 목록" in _first_line(body)]
        self.assertTrue(titles, "진행 목록 제목 예시가 없다")
        for name, line, title in titles:
            with self.subTest(file=name, line=line):
                self.assertRegex(title, self.TITLE)
                self.assertNotIn("(", title.split("·")[0])


class NativeFieldWordingTests(unittest.TestCase):
    """코드→학생 말 표의 학생 말은 모두 검사되는 블록 안에 있어야 한다."""

    def test_native_field_wording_lives_in_a_student_block(self):
        rows = _native_field_rows()
        self.assertEqual(list(NATIVE_FIELD_WORDINGS), rows,
                         "outputs.md: 칸 이름 표의 학생 말 6행이 바뀌었다")
        blocks = [body for _, _, body in _all_blocks()]
        for wording in rows:
            with self.subTest(wording=wording):
                self.assertTrue(
                    any(wording in body for body in blocks),
                    "표의 학생 말 %r 이 학생화면 블록에 없다" % (wording,))

    def test_native_field_key_pairs_are_pinned_in_order(self):
        """W-D2 @Roh 권고2: 키 집합과 문구의 쌍까지 고정한다. 시작/목표
        면적의 키를 서로 바꾸는 돌연변이는 이 시험에서 실패해야 한다."""
        self.assertEqual(list(NATIVE_FIELD_PAIRS), _native_field_pairs(),
                         "outputs.md: 칸 이름↔학생 말 쌍이 바뀌었다")


class OptionCardTests(unittest.TestCase):
    def test_option_lists_use_numbered_cards_with_a_question_header(self):
        for name, line, body in _all_blocks():
            with self.subTest(file=name, line=line, format="letter"):
                self.assertEqual(
                    [], LETTER_OPTION.findall(body),
                    "학생 보기에 A)·B) 글자 보기를 썼다")
            if DIGIT_OPTION.search(body):
                with self.subTest(file=name, line=line, format="header"):
                    self.assertRegex(
                        _first_line(body), QUESTION_HEADER,
                        "보기 카드에 [Qn-m] 제목이 없다")


class SearchPrivacyTests(unittest.TestCase):
    """검색어 개인정보 규칙 (ORDER-W-D 추가 절, 형님 결정 ts 1794230407).

    앱 AI 인터넷 검색(v0.6.2)은 학생 허락 없이 허용한다. 대신 검색어에
    넣지 말아야 할 개인정보를 SKILL.md에 명시하고, 금지 예시가 '써도 됨'
    쪽에 빠지지 않게 잠근다.
    """

    # 금지 예시: 이름·생년월일/정확한 나이·읍면 이하 주소·지번·연락처·
    # 학번·학생 본인을 식별하는 농장 상호. 규칙 문장에 그대로 있어야 한다.
    FORBIDDEN_EXAMPLES = (
        "이름", "생년월일", "정확한 나이", "읍면 이하 주소", "지번",
        "연락처", "학번", "농장 상호",
    )

    def _rule_text(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        hits = [ln for ln in skill.splitlines() if "인터넷 검색은" in ln]
        self.assertTrue(hits, "검색어 개인정보 규칙 문장이 없다")
        return hits

    def test_search_privacy_rule_exists_with_allow_and_deny_lists(self):
        for ln in self._rule_text():
            self.assertIn("써도 됨", ln)
            self.assertIn("쓰지 않음", ln)
            self.assertIn("시·군", ln)
            self.assertIn("연령대", ln)

    def test_forbidden_examples_sit_on_the_deny_side(self):
        for ln in self._rule_text():
            allow, _, deny = ln.partition("쓰지 않음")
            self.assertTrue(deny, "'쓰지 않음' 구분자 없음")
            for example in self.FORBIDDEN_EXAMPLES:
                with self.subTest(example=example):
                    self.assertIn(example, deny,
                                  "%r — 규칙의 '쓰지 않음' 쪽에 있어야 한다" % example)
            # 허용 쪽 키워드만 세고 금지 예시가 '이름'을 포함하진 않음
            for example in self.FORBIDDEN_EXAMPLES:
                # example이 허용 쪽에만 나타나면 allow 키워드 검사를 우회할 수 있다
                self.assertIn(example, deny)

    def test_allow_side_wordings_are_pinned(self):
        """W-D2 @Roh 권고2: 허용 쪽 목록을 고정한다. 학생 이름을 허용
        쪽에 추가하는 돌연변이(또는 금지 예시를 허용 쪽으로 옮기는
        돌연변이)는 이 시험에서 실패해야 한다."""
        for ln in self._rule_text():
            allow, _, _ = ln.partition("쓰지 않음")
            for wording in SEARCH_ALLOW_WORDINGS:
                with self.subTest(wording=wording):
                    self.assertIn(wording, allow,
                                  "%r — 규칙의 '써도 됨' 쪽에 고정된 문구가 없다" % wording)
            # Tokenize the allow list by its own separators so '이름'
            # inside '제도 이름' is not a token (W-D2 @Roh 권고2).
            allow_tokens = [t.strip().rstrip(".") for t in allow.split(",")]
            for example in self.FORBIDDEN_EXAMPLES:
                with self.subTest(forbidden=example):
                    self.assertFalse(
                        any(example == tok or tok.startswith(example + " ")
                            or tok.endswith(" " + example)
                            and tok != "통계·제도 이름"
                            for tok in allow_tokens),
                        "%r — 금지 예시가 '써도 됨' 쪽에 섞였다" % (example,))


if __name__ == "__main__":
    unittest.main()
