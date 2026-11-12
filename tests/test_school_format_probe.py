"""식량작물 쪽별 서식 기계 검사(gg_school_format) 시험.

검사기는 DOCX를 고치지 않고 측정만 한다. 전공을 주지 않으면 구역 여백·쪽번호·
바닥글은 기록만 하고 위반을 만들지 않으며, `food_crops`를 주면 양식 값과
대조해 `food_crops.format.*` 코드로 낸다.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime

CHAPTERS = ("# 1. 머리말", "# 2. 영농환경 분석", "# 3. 영농계획 수립",
            "# 4. 재무계획")
CLOSERS = ("# 맺음말 및 발전방향", "# 참고문헌", "# 학부모 동의서")


class SchoolFormatProbeTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")
        self.builder = runtime("build_docx")
        self.mc = runtime("gg_major_contract")
        self.probe = runtime("gg_school_format")
        self.root = self.make_project()

    def manuscript(self):
        profile = self.fm.normalize_school_profile({
            "major_id": "food_crops",
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월"}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "summary": "1. 농장 개요\n\n합성 요약 내용",
            "thanks": "합성 감사 내용"})
        body = []
        for chapter in CHAPTERS + CLOSERS:
            body += [chapter, "합성 본문 " + chapter[2:]]
        return "\n\n".join(lines + body)

    def docx(self, major):
        bind_major(self.root, major)
        text = (self.manuscript() if major == "food_crops"
                else "\n\n".join(["# 1. 머리말", "합성 본문 머리말"]))
        doc = self.builder.convert(
            text, context=self.mc.output_context(self.root, major))
        out = Path(self.root) / (major + ".docx")
        doc.save(str(out))
        return out

    def food_crops_codes(self, result):
        return [v["code"] for v in result["violations"]
                if v["code"].startswith("food_crops.format.")]

    def test_sections_record_margins_numbers_and_footer(self):
        result = self.probe.check(self.docx("food_crops"))
        self.assertIsNone(result["food_crops_format"])
        self.assertEqual([], self.food_crops_codes(result))
        sections = result["sections"]
        self.assertEqual(3, len(sections))
        self.assertEqual(
            {"top": "1134", "right": "1134", "bottom": "567", "left": "1134",
             "header": "0", "footer": "567", "gutter": "0"},
            sections[0]["pg_mar"])
        self.assertEqual(
            {"top": "1701", "right": "1701", "bottom": "1701", "left": "1701",
             "header": "0", "footer": "567", "gutter": "0"},
            sections[1]["pg_mar"])
        self.assertIsNone(sections[0]["pg_num_type"])
        self.assertEqual({"start": "1", "fmt": "upperRoman"},
                         sections[1]["pg_num_type"])
        self.assertEqual({"start": "1", "fmt": "decimal"},
                         sections[2]["pg_num_type"])
        self.assertFalse(sections[0]["footer"]["has_reference"])
        for section in sections[1:]:
            self.assertTrue(section["footer"]["has_reference"])
            self.assertFalse(section["footer"]["linked_to_previous"])
            self.assertEqual(1, section["footer"]["page_fields"])

    def test_food_crops_comparison_passes_on_food_crops_output(self):
        result = self.probe.check(self.docx("food_crops"),
                                  major_id="food_crops")
        self.assertEqual([], self.food_crops_codes(result))
        self.assertEqual([], result["food_crops_format"])

    def test_food_crops_comparison_flags_other_major_layout(self):
        # 한 구역짜리 기본 여백 산출물을 식량작물 양식과 대조하면 위반이 나야
        # 한다. 위반 0이 나오면 대조가 배선되지 않은 것이다.
        result = self.probe.check(self.docx("specialty_crops"),
                                  major_id="food_crops")
        codes = self.food_crops_codes(result)
        self.assertIn("food_crops.format.section.count", codes)
        violation = next(v for v in result["violations"]
                         if v["code"] == "food_crops.format.section.count")
        self.assertTrue(violation["probe_local"])
        self.assertEqual(3, violation["expected"])
        self.assertEqual(1, violation["actual"])

    def test_margin_violation_cites_shared_requirement(self):
        # 위반은 자기 코드만 쓰고 공용 OG 번호는 related에만 남긴다.
        result = self.probe.check(self.docx("specialty_crops"),
                                  major_id="food_crops")
        self.assertTrue(result["violations"])
        for violation in result["violations"]:
            if not violation["code"].startswith("food_crops.format."):
                continue
            self.assertNotIn(violation["requirement_id"], violation["related"])
            self.assertTrue(violation["code"].startswith("food_crops.format."))

    def test_cli_major_flag_reports_food_crops_violations(self):
        path = self.docx("specialty_crops")
        out = Path(self.root) / "probe.json"
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format.py"),
             str(path), "--out", str(out), "--major", "food_crops"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.root))
        self.assertEqual(1, run.returncode, run.stderr)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("food_crops.format.section.count",
                      [v["code"] for v in report["violations"]])
        self.assertTrue(report["food_crops_format"])

    def test_docx_probe_dispatch_forwards_major(self):
        # gg_commands.main("docx-probe")가 --major를 gg_school_format까지
        # 넘기는지 본다. 넘기지 않으면 식량작물 대조가 통째로 사라진다.
        bad = self.docx("specialty_crops")
        out = Path(self.root) / "dispatch.json"
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "docx_probe.py"),
             str(bad), "--out", str(out), "--major", "food_crops"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.root))
        self.assertEqual(1, run.returncode, run.stderr)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["food_crops_format"])

        good = self.docx("food_crops")
        out2 = Path(self.root) / "dispatch-ok.json"
        ok = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "docx_probe.py"),
             str(good), "--out", str(out2), "--major", "food_crops"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.root))
        self.assertEqual(0, ok.returncode, ok.stderr)
        self.assertEqual([], json.loads(
            out2.read_text(encoding="utf-8"))["food_crops_format"])

    def test_docx_probe_dispatch_without_major_stays_silent(self):
        # 전공을 안 주면 기존 호출부처럼 여백·쪽번호는 기록만 하고
        # food_crops.format.* 위반을 만들지 않는다. (합성 원고의 본문 크기는
        # 이 배선과 무관한 기존 검사라 상태 코드로 판정하지 않는다.)
        path = self.docx("food_crops")
        out = Path(self.root) / "dispatch-nomajor.json"
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "docx_probe.py"),
             str(path), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.root))
        self.assertEqual("", run.stderr)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(report["food_crops_format"])
        self.assertEqual([], self.food_crops_codes(report))
        self.assertEqual(3, len(report["sections"]))


if __name__ == "__main__":
    unittest.main()
