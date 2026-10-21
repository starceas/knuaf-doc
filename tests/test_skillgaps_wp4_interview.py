"""WP-4 인터뷰·전공 범위 문서 계약 (G2-02, G2-03, G2-04, G2-12, G1-04)."""
import re
from pathlib import Path
import unittest

REF = Path(__file__).resolve().parent.parent / "skills" / "knuaf-doc" / "references"


def read(name):
    return (REF / name).read_text(encoding="utf-8")


def section(text, heading):
    """Heading body up to the next heading of the same or higher level."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise AssertionError(f"heading not found: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    for i in range(start + 1, len(lines)):
        match = re.match(r"^(#+)\s", lines[i])
        if match and len(match.group(1)) <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


class InterviewContract(unittest.TestCase):
    def setUp(self):
        self.text = read("interview-guide.md")

    def test_preface_only_reuse_mode_is_offered(self):
        intake = read("intake-selection.md")
        q1c = section(intake, "## Q1c — 작성 기준 확인")
        self.assertIn("머리말만 재사용", q1c)
        mode = section(intake, "### Q1c-1")
        for token in ("Ⅰ장 머리말", "해시", "Ⅱ~Ⅵ", "원답변"):
            self.assertIn(token, mode)

    def test_specialty_crop_scope_includes_utilization(self):
        scope = section(read("majors.md"), "## 특용작물")
        self.assertIn("특용작물을 활용한 재배법", scope)
        self.assertIn("재배 없이 가공품만", scope)
        self.assertIn("버섯류", scope)

    def test_physical_constraint_question_filters_candidates(self):
        q23 = section(self.text, "### Q2-3.")
        self.assertIn("신체 제약", q23)
        self.assertIn("토경", q23)
        self.assertIn("저상", q23)
        self.assertIn("확인 필요", q23)

    def test_internship_question_collects_required_fields(self):
        q26 = section(self.text, "### Q2-6.")
        for token in ("실습처", "기간", "작목", "업무", "사진"):
            self.assertIn(token, q26)
        toc = section(self.text, "## 목차 ↔ 문항 대응")
        self.assertIn("Q2-6", toc)
        self.assertIn("감사의 글", toc)

    def test_mushroom_is_not_offered_as_default_crop(self):
        self.assertNotIn("표고버섯", self.text)
        self.assertIn("버섯류는 별도 전공", section(self.text, "### Q2."))


class LinkContract(unittest.TestCase):
    def test_relative_links_resolve(self):
        for name in ("interview-guide.md", "intake-selection.md", "majors.md"):
            path = REF / name
            for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = match.group(1).split("#")[0]
                if not target or target.startswith(("http", "mailto")):
                    continue
                self.assertTrue((path.parent / target).exists(),
                                f"{name} -> {target}")


if __name__ == "__main__":
    unittest.main()
