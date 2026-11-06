"""WP-4 인터뷰·전공 범위 문서 계약 (G2-02, G2-03, G2-04, G2-12, G1-04)."""
import json
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
                "산업곤충", "작성요령 > 선배 본문", "S01", "HT2",
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
    def test_linked_research_guides_distinguish_text_drafts_from_outputs(self):
        checks = (
            ("fruit-trees/research-routes.md", "본문 초안(글)", "서식 본문 파일", "질문·문서계획·생산검산(kg/주/㎡) 지원, **논문 본문·"),
            ("dairy/research-chapters.md", "본문 초안(글)", "서식 본문 파일", "질문·계획·근거 검토만 지원하며, **논문 본문·"),
            ("research-route-index.md", "본문 초안(글)", "major-body-draft.md", "신규 본문 작성 없음"),
            ("figure-research-routes.md", "본문 초안(글)", "major-body-draft.md", "현재 본문·재무 출력이 미지원되는 전공"),
        )
        for name, supported, link, stale in checks:
            with self.subTest(document=name):
                body = read(name)
                self.assertIn(supported, body)
                self.assertIn(link, body)
                self.assertNotIn(stale, body)
                self.assertNotIn("본문 미지원", body)

        readme = REF.parents[2] / "README.md"
        fruit_row = next(line for line in readme.read_text(encoding="utf-8").splitlines()
                         if line.startswith("| 과수 |"))
        self.assertIn("본문 초안(글)", fruit_row)
        self.assertIn("서식 본문 파일·XLSX·재무 계산은 미지원", fruit_row)
        self.assertNotIn("본문·XLSX·재무 계산 미지원", fruit_row)

    def test_progress_uses_each_major_toc_and_marks_unsupported_work(self):
        skill = (REF.parent / "SKILL.md").read_text(encoding="utf-8")
        progress = section(skill, "## 진행 목록")
        for token in (
                "본문 Ⅰ~Ⅵ 초안", "특용작물 기준", "과수는 Ⅰ~Ⅳ",
                "낙농은 1~12", "원예환경시스템은 채택 목차 전체로 바꿔 표시",
                "과수: Ⅰ~Ⅳ 네 장", "낙농: 1~12 열두 장",
                "스킬의 남은 일로 표시하지 않는다",
                "⚑ 학생 몫 — 스킬 미지원", "전용 18시트 조건부"):
            with self.subTest(token=token):
                self.assertIn(token, progress)
        self.assertNotIn("Ⅰ~Ⅵ 여섯 장의 절 원고", progress)
        self.assertIn("특용작물: 값 입력 + 실제 앱 재계산", progress)
        self.assertIn("원예환경시스템: 기존 전용 18시트 프로필", progress)
    def test_dairy_json_command_does_not_claim_section_draft_saves_are_json(self):
        dairy = read("dairy/README.md")
        self.assertIn("낙농 전용 명령(예: `gg.py major-plan`)", dairy)
        self.assertIn("표준 출력으로 JSON만", dairy)
        self.assertIn("공용 본문 초안 저장", dairy)
        self.assertIn("`sections/*.md`와 `apply` 절차", dairy)


    def test_horticulture_uses_designated_ht2_format_basis(self):
        guide = read("major-body-draft.md")
        horticulture = read("hort-env-systems/README.md")
        profile = json.loads((REF / "hort-env-systems" / "profile.json")
                             .read_text(encoding="utf-8"))
        ht2 = next(item for item in profile["precedents"]
                   if item["ref"] == "HT2")
        for token in (
                "원예 학교 작성요령은 등록되어 있지 않고",
                "교수님이 틀 기준으로 지정한 HT2",
                "형식 기준(A에 준해 지킨다)",
                "HT1과 그 밖의 선배 논문은 내용·구성 참고(B)",
                "작성요령이 등록되면 작성요령을 우선한다",
                "해시로 HT2 확인"):
            with self.subTest(token=token):
                self.assertIn(token, guide)
        self.assertIn("학교 작성요령 미등록, 교수 지정 기준 HT2 사용",
                      guide)
        self.assertIn("해시로 HT2 확인", horticulture)
        self.assertEqual(
            "e5ca9ff33daa1fe8b3e7d56fbd1fff5a6bbba8c3d1e0368bbea1a36a0b3f8e4c",
            ht2["sha256"])
        self.assertIn("학교 작성요령 미등록, 교수 지정 기준 HT2 사용",
                      horticulture)

    def test_draft_year_authority_and_full_student_notices_are_explicit(self):
        guide = read("major-body-draft.md")
        self.assertIn("본문에 쓰는 연도(영농 시작연도·계획 연도)", guide)
        self.assertIn("정본·인터뷰 규칙에서 정해진 값만 쓰며 초안이 새로 정하지 않는다",
                      guide)
        self.assertIn("[확인 필요: 영농 시작연도]", guide)
        self.assertIn("문구를 줄이지 말고 그대로 쓴다", guide)
        self.assertIn(
            "검토용 초안 — 교수 확인·한글 마무리 필요. 표 제목 위치·참고문헌 양식은 자동 검사가 얕으니 직접 확인",
            guide)
        self.assertIn("장별 본문 글을 대신 드릴 수 있다는 제안을 반드시 붙인다",
                      guide)

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
