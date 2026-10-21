"""WP-4 검토 절차 문서 계약 (G1-06, G2-08, G2-13)."""
import re
from pathlib import Path
import unittest

REF = Path(__file__).resolve().parent.parent / "skills" / "knuaf-doc" / "references"


def read(name):
    return (REF / name).read_text(encoding="utf-8")


def section(text, heading):
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


class ReviewContract(unittest.TestCase):
    def setUp(self):
        self.cross = read("cross-review.md")

    def test_decision_reversal_is_checked_across_chapters(self):
        block = section(self.cross, "## 장 간 일관성")
        for token in ("바뀐 결정", "project.json", "감사의 글", "보완 필요"):
            self.assertIn(token, block)

    def test_school_guideline_conflict_is_recorded_not_exempted(self):
        block = section(self.cross, "## 학교 지침·금칙 충돌")
        for token in ("투자분석", "면제하지 않는다", "교수", "요약·목차 대조"):
            self.assertIn(token, block)

    def test_thanks_section_needs_factual_basis(self):
        thanks = section(read("rules/PRE_frontmatter.md"), "## 감사의 글 내용 근거")
        for token in ("원답변", "확인되지 않은", "장 간 일관성", "Q2-6"):
            self.assertIn(token, thanks)

    def test_preface_points_to_consistency_review(self):
        intro = read("rules/CH1_intro.md")
        self.assertIn("장 간 일관성", intro)
        self.assertIn("cross-review.md", intro)


class LinkContract(unittest.TestCase):
    def test_relative_links_resolve(self):
        for name in ("cross-review.md", "rules/PRE_frontmatter.md", "rules/CH1_intro.md"):
            path = REF / name
            for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = match.group(1).split("#")[0]
                if not target or target.startswith(("http", "mailto")):
                    continue
                self.assertTrue((path.parent / target).exists(),
                                f"{name} -> {target}")


if __name__ == "__main__":
    unittest.main()
