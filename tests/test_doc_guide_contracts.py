"""Contract tests for document and interview guidance (P0-2 PR-A: U01~U05).

Verifies that the command reference, interview guidance, intake policy,
and guideline-tracking references adhere to the canonical knuaf-doc contracts:
- U01: init uses the selected student folder directly without subfolder nesting
- U02: new paper intake asks major before crop
- U03: commands index documents current gg.py CLI and apply JSON contract
- U04: three interview question styles, multi-field Q2, single follow-ups
- U05: five finance questions asked one-by-one with equity/loan split
- #58: anonymous reference identity preserved across all guidance files
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPO_ROOT / "skills" / "knuaf-doc"
REFERENCES = SKILL_ROOT / "references"


class DocGuideContractTests(unittest.TestCase):
    def test_commands_file_structure(self):
        commands_md = REFERENCES / "commands.md"
        self.assertTrue(commands_md.is_file(), "commands.md must exist")
        text = commands_md.read_text(encoding="utf-8")
        self.assertIn("# 명령 목록", text)
        self.assertIn("구현 파일을 열지 말 것", text)
        self.assertIn("gg.py 명령 표", text)
        self.assertIn("apply 변경 JSON 계약", text)

        for cmd in (
            "init", "doctor", "status", "next", "check", "apply",
            "export", "question", "bundle", "paper", "major-plan",
            "adopt-output", "import", "unlock", "finance-tables", "tidy",
        ):
            with self.subTest(cmd=cmd):
                self.assertIn(f"`{cmd}`", text)

    def test_commands_documented_match_gg_cli(self):
        scripts_dir = SKILL_ROOT / "scripts"
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, str(scripts_dir / "gg.py"), "--help"],
            capture_output=True, text=True, check=True)
        help_output = proc.stdout

        commands_md = (REFERENCES / "commands.md").read_text(encoding="utf-8")
        # Extract commands from the table
        table_cmds = re.findall(r"^\|\s*`([a-z0-9_-]+)`\s*\|", commands_md, re.MULTILINE)
        self.assertTrue(table_cmds, "Must find commands in the table")
        for cmd in table_cmds:
            with self.subTest(cmd=cmd):
                self.assertIn(cmd, help_output, f"Command {cmd} must be in gg.py help")

    def test_skill_commands_link_intact(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("[명령 목록](references/commands.md)", skill_md)
        self.assertIn("구현 파일(`scripts/gg_core.py` 등)을 열어 확인하지 않는다", skill_md)

    def test_u01_init_folder_guidance(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("사용자가 지정한 폴더가 곧 작업폴더다", skill_md)
        self.assertIn("새 하위 폴더를 만들지 말고", skill_md)
        self.assertIn("학생 자료가 이미 들어 있는 폴더에도 그대로 실행한다", skill_md)

    def test_u02_major_before_crop_guidance(self):
        skill_md = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("원고가 없으면(새로 시작) 작목·사업 주제를 먼저 묻지 않는다", skill_md)
        self.assertIn("전공(학과 소속)부터", skill_md)

        intake_md = (REFERENCES / "intake-selection.md").read_text(encoding="utf-8")
        self.assertIn("원고가 없으면 Q1c에서 작목·주제를 새로 묻지 않고", intake_md)
        self.assertIn("전공(학과 소속)부터", intake_md)

    def test_u04_interview_ui_rules(self):
        ui_md = (REFERENCES / "interview-ui.md").read_text(encoding="utf-8")
        self.assertIn("금액·면적처럼 직접 적어야 하는 값은 보기로 만들지 않고", ui_md)
        self.assertIn("초반 [Q2]의 자금 칸을 제외한 별도 금액 질문에는 번호 하위 항목을 붙이지 않는다", ui_md)
        self.assertIn("질문 번호는 `[Q2]` 또는 `[Q2-3]`처럼 숫자 두 단계까지만 쓴다", ui_md)
        self.assertIn("선택형 질문은 보기 위에 물음표로 끝나는 한 문장, 25자 안팎으로 쓴다", ui_md)

        guide_md = (REFERENCES / "interview-guide.md").read_text(encoding="utf-8")
        self.assertIn("여러 칸 문안은 `[Q2] 제목` 아래 1부터 연속인", guide_md)
        self.assertIn("항목 이름에 쌍점을 쓰지 않는다", guide_md)

    def test_u05_finance_five_separate_questions(self):
        guide_md = (REFERENCES / "interview-guide.md").read_text(encoding="utf-8")
        self.assertIn("다음 다섯 값은 서로 다르므로 한 번에 하나씩 묻는다", guide_md)
        self.assertIn("이미 확인된 값은 다시 묻지 않는다", guide_md)
        self.assertIn("각 질문은 답을 받은 뒤 다음 질문으로 넘어간다", guide_md)
        self.assertIn("[Q2-4] 투자·자금 조달 · 재무 분석에 쓰여요", guide_md)
        self.assertIn("[Q2-4] 자금 조달 · 재무 분석에 쓰여요", guide_md)
        self.assertIn("[Q2-4] 정책자금 · 재무 분석에 쓰여요", guide_md)
        self.assertIn("[Q2-4] 대출 상환 · 재무 분석에 쓰여요", guide_md)
        self.assertIn("[Q2-4] 가계비 · 투자 분석에 쓰여요", guide_md)

    def test_anonymized_reference_names_preserved(self):
        for path in (
            SKILL_ROOT / "SKILL.md",
            REFERENCES / "commands.md",
            REFERENCES / "guideline-tracking.md",
            REFERENCES / "interview-guide.md",
            REFERENCES / "narrative-expansion.md",
            REFERENCES / "intake-selection.md",
            REFERENCES / "interview-ui.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("김원섭", text, f"{path} must not contain real name 김원섭")
            self.assertNotIn("서민서", text, f"{path} must not contain real name 서민서")

        gt_md = (REFERENCES / "guideline-tracking.md").read_text(encoding="utf-8")
        self.assertIn("서술 예시 02 선배논문", gt_md)
        self.assertIn("재무 예시 01 XLSX", gt_md)


if __name__ == "__main__":
    unittest.main()
