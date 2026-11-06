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
        self.assertIn("확인 필요", q23)
        self.assertIn("후보 제외 기준", q23)
        self.assertIn("명시적으로 제외한", q23)
        self.assertIn("저상 베드는 굽은 작업 제약의 완화책이 아니다", q23)
        self.assertNotIn("저상 베드·관수 자동화", q23)

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


class MajorBodyDraftContract(unittest.TestCase):
    def test_skill_opens_only_text_drafts_for_three_bound_majors(self):
        skill = (REF.parent / "SKILL.md").read_text(encoding="utf-8")
        scope = section(skill, "## 할 수 있는 일과 못 하는 일")
        self.assertIn("references/major-body-draft.md", scope)
        self.assertIn("과수·원예환경시스템·낙농", scope)
        self.assertIn("산업곤충", scope)
        self.assertNotIn("한우는 질문·목차·계획 정리까지만", scope)
    def test_skill_submission_boundary_is_stated_once(self):
        scope = section(
            (REF.parent / "SKILL.md").read_text(encoding="utf-8"),
            "## 할 수 있는 일과 못 하는 일")
        self.assertEqual(1, scope.count("제출 대행은 하지 않는다"))


    def test_major_sections_preserve_file_output_guard(self):
        majors = read("majors.md")
        for heading in ("## 과수", "## 원예환경시스템", "## 낙농"):
            with self.subTest(major=heading):
                body = section(majors, heading)
                self.assertIn("major-body-draft.md", body)
                self.assertIn("unsupported_output", body)
                self.assertIn("파일", body)
    def test_fruit_and_horticulture_boundary_sentences_are_complete(self):
        majors = read("majors.md")
        fruit = section(majors, "## 과수")
        horticulture = section(majors, "## 원예환경시스템")
        self.assertIn("검산한다(금액 계산은 하지 않음)", fruit)
        self.assertNotIn("검산하며", fruit)
        self.assertIn("hort_18_sheet_reconstructed_v1", horticulture)
        self.assertIn("[hort-env-systems](hort-env-systems/README.md)",
                      horticulture)


    def test_draft_contract_keeps_sources_numbers_and_scope_bounded(self):
        guide = read("major-body-draft.md")
        for token in (
                "fruit_trees", "hort_env_systems", "dairy",
                "산업곤충", "작성요령 > 선배 본문", "S01", "HT1·HT2",
                "1~12장", "[확인 필요:", "계산·추정·환산하지 않는다",
                "시트와 셀 위치", "DRAFT", "`apply`", "검토용 초안",
                "DOCX/HWPX", "재무 계산"):
            with self.subTest(token=token):
                self.assertIn(token, guide)

    def test_major_readmes_link_text_draft_without_claiming_file_support(self):
        for name in ("fruit-trees/README.md", "hort-env-systems/README.md",
                     "dairy/README.md"):
            body = read(name)
            with self.subTest(major=name):
                self.assertIn("major-body-draft.md", body)
                self.assertIn("본문 초안(글)", body)
                self.assertIn("unsupported_output", body)
    def test_dairy_json_command_does_not_claim_section_draft_saves_are_json(self):
        dairy = read("dairy/README.md")
        self.assertIn("낙농 전용 명령(예: `gg.py major-plan`)", dairy)
        self.assertIn("표준 출력으로 JSON만", dairy)
        self.assertIn("공용 본문 초안 저장", dairy)
        self.assertIn("`sections/*.md`와 `apply` 절차", dairy)


    def test_draft_guidance_relative_links_resolve(self):
        path = REF / "major-body-draft.md"
        for match in re.finditer(r"\]\(([^)]+)\)",
                                 path.read_text(encoding="utf-8")):
            target = match.group(1).split("#")[0]
            if target and not target.startswith(("http", "mailto")):
                self.assertTrue((path.parent / target).exists(),
                                f"major-body-draft.md -> {target}")




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
