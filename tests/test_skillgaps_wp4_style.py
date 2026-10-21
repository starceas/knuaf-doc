"""WP-4 문체·눈높이 문서 계약 (G2-01, G1-13)."""
import re
from pathlib import Path
import unittest

REF = Path(__file__).resolve().parent.parent / "skills" / "knuaf-doc" / "references"


def read(name):
    return (REF / name).read_text(encoding="utf-8")


def between(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


class StyleContract(unittest.TestCase):
    def test_user_selected_style_overrides_fixed_ending(self):
        polishing = read("korean-polishing.md")
        self.assertIn("사용자 지정 문체가 이 문서의 기본 어투보다 우선한다", polishing)
        self.assertIn("지정이 없을 때의 기본값", polishing)
        self.assertIn("00_interview.md", polishing)
        self.assertIn("Q4", polishing)

    def test_narrative_defers_to_polishing_default(self):
        line = next(
            line for line in read("narrative-expansion.md").splitlines()
            if "사용자 지정 문체를 우선" in line)
        self.assertIn("korean-polishing.md", line)
        self.assertIn("지정이 없을 때의 기본값", line)

    def test_simple_arithmetic_scope_is_bounded(self):
        block = between(read("thesis-level.md"),
                        "**단순 계산식·풀이 금지 범위**", "**본문 허용 지표**")
        for token in ("**금지**", "**허용**", "10×5 cm", "한 단계 계산이어도 넣지 않는다"):
            self.assertIn(token, block)
        self.assertIn("정본", block)

    def test_investment_analysis_gate_is_not_exempted(self):
        level = read("thesis-level.md")
        block = between(level, "**학교 지침의 투자분석 요구**", "**본문 허용 지표**")
        self.assertIn("면제되지 않는다", block)
        self.assertIn("cross-review.md", block)


class LinkContract(unittest.TestCase):
    def test_relative_links_resolve(self):
        for name in ("korean-polishing.md", "narrative-expansion.md", "thesis-level.md"):
            path = REF / name
            for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                target = match.group(1).split("#")[0]
                if not target or target.startswith(("http", "mailto")):
                    continue
                self.assertTrue((path.parent / target).exists(),
                                f"{name} -> {target}")


if __name__ == "__main__":
    unittest.main()
