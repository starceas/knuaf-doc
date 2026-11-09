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
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "knuaf-doc"

FENCE_OPEN = "```학생화면"
FENCE_CLOSE = "```"

#: 학생에게 보이는 글에 쓰면 안 되는 말.
#: 앞 8개는 기존 금지 목록(SKILL.md·interview-ui.md), 다음 8개는 형님 맥
#: 실사례에서 새로 확인된 말, 나머지는 결과 코드·검사 이름·도구 이름이다.
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
    "fact ID",
    "사실 ID",
    # 도구·실행환경 이름
    "Kordoc",
    "gg.py",
    "gg_core",
    "gg_school_excel",
    "gg_excel_facts",
)

#: 한 학생 보고에 함께 나오면 안 되는 전공 이름 묶음.
#: ``원예`` 가 ``원예환경시스템`` 을 포함하므로 긴 이름을 따로 두지 않는다.
MAJOR_GROUPS = ("특용작물", "과수", "낙농", "원예", "산업곤충")

#: 울타리 블록이 있어야 하는 문서와 문서별 최소 개수. 블록을 지워서
#: 통과하는 것을 막는다(개수를 줄이려면 이 표와 문서를 같이 고쳐야 한다).
#: 값은 v0.4.8에서 실제로 쓴 블록 수와 같다 — 블록을 지우면 이 시험이 막는다.
MIN_BLOCKS_BY_FILE = {
    "SKILL.md": 1,
    "references/interview-ui.md": 5,
    "references/interview-guide.md": 30,
    "references/outputs.md": 9,
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

    def test_progress_example_names_only_its_own_major(self):
        found = _all_blocks()
        self.assertTrue(found, "학생화면 블록이 하나도 없다")
        checked = 0
        for name, line, body in found:
            present = [group for group in MAJOR_GROUPS if group in body]
            with self.subTest(file=name, line=line):
                self.assertLessEqual(
                    len(present), 1,
                    "한 학생 보고에 전공이 섞였다: %s" % (present,))
            checked += 1 if present else 0
        self.assertGreaterEqual(checked, 2, "전공 이름이 있는 학생 예시가 2개 미만")
        self.assertTrue(
            any("원예" in body for _, _, body in found),
            "원예환경시스템 학생 보고 예시가 없다")


if __name__ == "__main__":
    unittest.main()
