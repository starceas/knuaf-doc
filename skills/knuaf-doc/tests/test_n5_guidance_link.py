"""N5 안내에서 현재 내장 RDA 승인 경로까지 연결되는지 확인한다."""

import ast
from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1]


class N5GuidanceLinkTests(unittest.TestCase):
    def test_entry_points_and_approval_boundary(self):
        research = (SKILL / "references" / "research-sources.md").read_text(
            encoding="utf-8")
        interview = (SKILL / "references" / "interview-guide.md").read_text(
            encoding="utf-8")
        self.assertIn("| 소득·경영비 |", research)
        self.assertIn("내장 RDA 자료를 먼저 조회", research)
        self.assertIn("재무 입력이나 논문 방향을 바꾸는 것.", interview)
        self.assertIn("내장 RDA 우선 조회 경로", interview)
        self.assertIn("approved_candidates", research)
        for state in ("candidate", "unverified", "ambiguous", "series",
                      "unapproved", "not_found"):
            self.assertIn(state, research)
        for rel in ("references/majors.md", "scripts/gg_rda_candidates.py",
                    "scripts/gg_rda_lookup.py"):
            self.assertTrue((SKILL / rel).is_file(), rel)

    def test_command_options_and_policy_consistency(self):
        research = (SKILL / "references" / "research-sources.md").read_text(
            encoding="utf-8")
        tree = ast.parse((SKILL / "scripts" / "gg.py").read_text(
            encoding="utf-8"))
        choices, arguments = set(), set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                arguments.add(node.args[0].value)
            for keyword in node.keywords:
                if keyword.arg == "choices" and isinstance(keyword.value,
                                                              ast.List):
                    choices.update(item.value for item in keyword.value.elts
                                   if isinstance(item, ast.Constant))
        self.assertTrue({"rda-candidates", "rda-lookup"}.issubset(choices))
        self.assertTrue({"--crop", "--region", "--year", "--unit",
                         "--use-scope"}.issubset(arguments))

        majors = (SKILL / "references" / "majors.md").read_text(
            encoding="utf-8")
        contracts = [
            majors,
            (SKILL / "references" / "thesis-level.md").read_text(
                encoding="utf-8"),
            (SKILL / "references" / "price-research-routes.md").read_text(
                encoding="utf-8"),
            (SKILL / "references" / "source-contract.md").read_text(
                encoding="utf-8"),
            (SKILL / "references" / "specialty-crops" / "finance-flow.md")
            .read_text(encoding="utf-8"),
        ]
        self.assertIn("rda-candidates", majors)
        self.assertIn("`approved`만 쓴다", majors)
        self.assertIn("approved_candidates", research)
        self.assertIn("공식 외부 자료 조사", research)
        self.assertIn("확인 불가", contracts[2])
        self.assertIn("source_refs", contracts[3])
        self.assertIn("추정소득분석", contracts[4])


if __name__ == "__main__":
    unittest.main()
