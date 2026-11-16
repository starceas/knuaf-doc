"""식량작물·낙농 쪽별 서식 기계 검사(gg_school_format) 시험.

검사기는 DOCX를 고치지 않고 측정만 한다. 전공을 주지 않으면 구역 여백·쪽번호·
바닥글은 기록만 하고 위반을 만들지 않으며, `food_crops`를 주면 양식 값과
대조해 `food_crops.format.*` 코드로, `dairy`를 주면 낙농 양식 값과 대조해
`dairy.format.*` 코드로 낸다. 두 전공은 서로의 산출물을 통과시키지 않는다.
"""

import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime

CHAPTERS = ("# 1. 머리말", "# 2. 영농환경 분석", "# 3. 영농계획 수립",
            "# 4. 재무계획")
CLOSERS = ("# 맺음말 및 발전방향", "# 참고문헌", "# 학부모 동의서")

# 낙농 양식 여백(DESIGN-v1 §F-0): 위·아래·좌·우 30 mm, 머리 0, 꼬리 10 mm.
DAIRY_MARGIN = {"top": "1701", "right": "1701", "bottom": "1701", "left": "1701",
                "header": "0", "footer": "567", "gutter": "0"}


def edit_paragraph(document, needle, old, new, containing=None):
    """Replace ``old`` with ``new`` in the first paragraph that matches.

    ``needle`` picks the paragraph by its text; ``containing`` narrows to the
    paragraph carrying that evidence (e.g. the ``Heading 1`` style), which the
    table of contents repeats with the same text but different properties.
    """
    guard = r"(?:(?!</w:p>).)*?"
    pattern = (r"<w:p(?:\s[^>]*)?>" + guard + re.escape(needle) + guard
               + r"</w:p>")
    for match in re.finditer(pattern, document, re.S):
        span = match.group(0)
        if containing is not None and containing not in span:
            continue
        if old not in span:
            continue
        return document[:match.start()] + span.replace(old, new, 1) + document[match.end():]
    raise AssertionError("no paragraph for %r" % needle)


class _ProbeFixtures(ContractCase):
    """두 전공 시험이 함께 쓰는 준비(원고·빌드·코드 추출). 시험은 없다."""

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


class SchoolFormatProbeTests(_ProbeFixtures):
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

    def test_duplicate_page_number_element_is_reported(self):
        # `find`는 첫 pgNumType만 본다. 둘이면 Word는 둘째를 적용해 화면이
        # 달라지므로, 개수까지 재서 위반으로 낸다.
        import zipfile
        path = self.docx("food_crops")
        with zipfile.ZipFile(path) as archive:
            members = {info.filename: archive.read(info.filename)
                       for info in archive.infolist()}
        document = members["word/document.xml"].decode("utf-8")
        marker = '<w:pgNumType w:start="1" w:fmt="upperRoman"/>'
        self.assertEqual(1, document.count(marker))
        patched = document.replace(marker, marker + marker, 1)
        members["word/document.xml"] = patched.encode("utf-8")
        target = Path(self.root) / "duplicate-number.docx"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        result = self.probe.check(target, major_id="food_crops")
        found = [value for value in result["violations"]
                 if value["code"] == "food_crops.format.page_number.front"]
        self.assertEqual(1, len(found), self.food_crops_codes(result))
        self.assertEqual(2, found[0]["actual"]["count"])
        self.assertEqual(1, found[0]["expected"]["count"])

    def test_duplicate_page_field_in_footer_is_reported(self):
        # 바닥글 PAGE 필드가 둘이면 쪽번호가 두 번 찍힌다. 개수 대조가 잡는다.
        import zipfile
        path = self.docx("food_crops")
        with zipfile.ZipFile(path) as archive:
            members = {info.filename: archive.read(info.filename)
                       for info in archive.infolist()}
        footer = members["word/footer1.xml"].decode("utf-8")
        marker = '<w:fldSimple w:instr="PAGE"/>'
        self.assertEqual(1, footer.count(marker))
        members["word/footer1.xml"] = footer.replace(
            marker, marker + marker, 1).encode("utf-8")
        target = Path(self.root) / "duplicate-field.docx"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        result = self.probe.check(target, major_id="food_crops")
        found = [value for value in result["violations"]
                 if value["code"] == "food_crops.format.footer.front"]
        self.assertEqual(1, len(found), self.food_crops_codes(result))
        self.assertEqual(2, found[0]["actual"]["page_fields"])
        self.assertEqual(1, found[0]["expected"]["page_fields"])

    def test_corrupt_docx_is_blocked_with_json_and_exit_2(self):
        # 손상 입력에 사람용 `BLOCK:`만 내면 자동 호출자가 상태를 못 읽는다.
        # 기계가 읽을 JSON을 stdout에 내고 종료 2는 그대로 둔다.
        path = Path(self.root) / "corrupt.docx"
        path.write_bytes(b"not a zip")
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format.py"),
             str(path), "--major", "food_crops"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(2, run.returncode, run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual(str(path), payload["input"]["path"])
        self.assertIn("BLOCK:", run.stderr)

    def test_corrupt_docx_writes_blocked_report_to_out(self):
        # N-I02: `--out`을 준 호출자는 그 파일을 읽는다. 손상 입력에서도 같은
        # 자리에 blocked 보고서가 남아야 '보고서 없음'과 '검사 안 함'을
        # 구분할 수 있다. 형식은 정상 경로의 기록과 같다(indent 2 + 개행).
        path = Path(self.root) / "corrupt-out.docx"
        path.write_bytes(b"not a zip")
        out = Path(self.root) / "nested" / "blocked.json"
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format.py"),
             str(path), "--out", str(out), "--major", "food_crops"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(2, run.returncode, run.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        payload = json.loads(text)
        self.assertEqual("blocked", payload["status"])
        self.assertEqual(str(path), payload["input"]["path"])
        self.assertEqual(str(out), payload["out"])
        self.assertIn("invalid DOCX OOXML", payload["reason"])
        self.assertEqual(json.loads(run.stdout), payload)

    def test_corrupt_docx_refuses_to_overwrite_the_input(self):
        # `--out`이 입력과 같으면 보고서를 쓰지 않는다 — 손상된 원본을
        # 덮어쓰면 증거가 사라진다.
        path = Path(self.root) / "corrupt-same.docx"
        path.write_bytes(b"not a zip")
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format.py"),
             str(path), "--out", str(path)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(2, run.returncode, run.stderr)
        self.assertEqual(b"not a zip", path.read_bytes())

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


class DairyFormatProbeTests(_ProbeFixtures):
    """낙농 양식 대조(I-07): 기대값은 검사기 안에 있고, 변조하면 잡힌다."""

    def dairy_manuscript(self):
        profile = self.fm.normalize_school_profile({
            "major_id": "dairy",
            "school_profile": {
                "title": "합성 낙농계획", "author": "가상학생",
                "submission_date": "2026년 12월", "graduation_date": "2027년 2월",
                "committee": {"chair": "가상위원장",
                              "members": ["가상위원1", "가상위원2"]}}})
        lines = self.fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "합성 동의 문장이다.",
            "summary": "Ⅰ. 합성 사업목적\n\n합성 요약 문장이다.",
            "thanks": "합성 감사 문장이다."})
        body = ["1. 머리말", "합성 본문 문장이다.",
                "가. 합성 1단계", "합성 하위 본문이다.",
                "1) 합성 2단계", "합성 하위 본문이다.",
                "가) 합성 3단계", "합성 하위 본문이다.",
                "(1) 합성 4단계", "합성 하위 본문이다.",
                "(가) 합성 5단계", "합성 하위 본문이다.",
                "2. 농업환경분석", "합성 본문 문장이다.",
                "3. 경영수지", "합성 본문 문장이다.",
                "# 참고문헌", "합성 참고문헌이다."]
        return "\n\n".join(lines + body)

    def dairy_docx(self):
        bind_major(self.root, "dairy")
        doc = self.builder.convert(
            self.dairy_manuscript(),
            context=self.mc.output_context(self.root, "dairy"))
        out = Path(self.root) / "dairy.docx"
        doc.save(str(out))
        return out

    def dairy_codes(self, result):
        return [v["code"] for v in result["violations"]
                if v["code"].startswith("dairy.format.")]

    def mutated(self, source, transform, name, part="word/document.xml"):
        """Copy ``source`` with the named package part(s) rewritten."""
        target = Path(self.root) / (name + ".docx")
        parts = [part] if isinstance(part, str) else list(part)
        with zipfile.ZipFile(source) as archive:
            entries = {n: archive.read(n) for n in archive.namelist()}
        for entry in parts:
            entries[entry] = transform(entries[entry].decode("utf-8")).encode("utf-8")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry, payload in entries.items():
                archive.writestr(entry, payload)
        return target

    def test_dairy_output_passes_its_own_form(self):
        result = self.probe.check(self.dairy_docx(), major_id="dairy")
        self.assertEqual([], self.dairy_codes(result))
        self.assertEqual([], result["dairy_format"])
        # 본문이 실제로 분류돼야 제목 검사가 헛돌지 않는다(bookmark gg_body_1).
        self.assertGreater(result["parts"]["body_paragraphs"], 0)

    def test_dairy_measurements_record_the_three_sections(self):
        result = self.probe.check(self.dairy_docx(), major_id="dairy")
        sections = result["sections"]
        self.assertEqual(3, len(sections))
        for section in sections:
            self.assertEqual(DAIRY_MARGIN, section["pg_mar"])
        self.assertIsNone(sections[0]["pg_num_type"])
        self.assertEqual({"start": "1", "fmt": "upperRoman"},
                         sections[1]["pg_num_type"])
        self.assertEqual({"start": "1", "fmt": "decimal"},
                         sections[2]["pg_num_type"])
        self.assertFalse(sections[0]["footer"]["has_reference"])
        for section in sections[1:]:
            self.assertEqual(1, section["footer"]["page_fields"])
            self.assertEqual({"prefix": "- ", "suffix": " -",
                              "size_half_points": ["18"]},
                             section["footer"]["page_label"])

    def test_dairy_form_is_not_read_from_the_builder_profile(self):
        # 검사기 기대값이 빌더 프로필을 읽으면 빌더가 같이 틀릴 때 아무것도
        # 못 잡는다(I-07). 검사기 소스에 프로필 import가 없어야 한다.
        source = (SCRIPTS / "gg_school_format.py").read_text(encoding="utf-8")
        self.assertNotIn("gg_major_format", source)
        self.assertNotIn("MAJOR_FORMAT_PROFILES", source)

    def test_margin_drift_is_reported(self):
        def transform(document):
            return document.replace(
                '<w:pgMar w:top="1701"', '<w:pgMar w:top="1134"', 1)
        path = self.mutated(self.dairy_docx(), transform, "margin")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.margin.cover_top", codes)

    def test_page_number_format_drift_is_reported(self):
        def transform(document):
            return document.replace(
                '<w:pgNumType w:start="1" w:fmt="upperRoman"/>', "", 1)
        path = self.mutated(self.dairy_docx(), transform, "numbering")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.page_number.front", codes)

    def test_cover_footer_appearing_is_reported(self):
        # 겉표지에 쪽번호가 찍히면 안 된다: 바닥글 참조를 첫 구역에 넣어 본다.
        def transform(document):
            first = document.index('<w:footerReference w:type="default"')
            ref = document[first:document.index("/>", first) + 2]
            end = document.index("</w:sectPr>")
            return document[:end] + ref + document[end:]
        path = self.mutated(self.dairy_docx(), transform, "cover-footer")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.footer.cover", codes)

    def test_page_label_drift_is_reported_twice_over(self):
        # 꾸밈(`- 1 -`)과 크기(9 pt)는 따로 잰다. 접두를 지우고 12 pt로 키운다.
        def transform(footer):
            footer = footer.replace(">- </w:t>", "></w:t>", 1)
            return footer.replace('<w:sz w:val="18"/>', '<w:sz w:val="24"/>')
        source = self.dairy_docx()
        with zipfile.ZipFile(source) as archive:
            footer_part = next(n for n in archive.namelist()
                               if re.fullmatch(r"word/footer\d+\.xml", n))
        path = self.mutated(source, transform, "label", part=footer_part)
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.page_label.front", codes)
        self.assertIn("dairy.format.page_label_size.front", codes)

    def complex_page_footer(self, footer, instructions):
        field = re.search(r'<w:fldSimple[^>]*instr="PAGE"[^>]*>.*?</w:fldSimple>',
                          footer, re.S)
        self.assertIsNotNone(field)
        run = re.search(r"<w:r>.*?</w:r>", field.group(0), re.S).group(0)
        command = "".join(
            '<w:r><w:instrText xml:space="preserve">%s</w:instrText></w:r>' % piece
            for piece in instructions)
        new = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>' + command +
               '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' +
               run.replace("</w:r>", "<w:t>1</w:t></w:r>", 1) +
               '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
        return footer[:field.start()] + new + footer[field.end():]

    def test_word_resaved_field_code_form_is_accepted(self):
        # Word는 손으로 만든 필드를 저장할 때 `fldSimple` 대신
        # begin/instrText/separate/end 꼴로 다시 쓴다. 그 꼴도 쪽번호 하나이며
        # 같은 `- 1 -` 꾸밈·9 pt로 통과해야 한다(정상 산출물을 막지 않는다).
        def transform(footer):
            return self.complex_page_footer(footer, (" PAGE ",))
        source = self.dairy_docx()
        with zipfile.ZipFile(source) as archive:
            footer_part = next(n for n in archive.namelist()
                               if re.fullmatch(r"word/footer\d+\.xml", n))
        path = self.mutated(source, transform, "trio", part=footer_part)
        result = self.probe.check(path, major_id="dairy")
        self.assertEqual([], self.dairy_codes(result))

    def test_split_page_field_keeps_correct_label_and_size(self):
        source = self.dairy_docx()
        with zipfile.ZipFile(source) as archive:
            parts = [name for name in archive.namelist()
                     if re.fullmatch(r"word/footer\d+\.xml", name)]
        path = self.mutated(
            source, lambda footer: self.complex_page_footer(footer, (" PA", "GE ")),
            "split-page", part=parts)
        result = self.probe.check(path, major_id="dairy")
        self.assertEqual([], self.dairy_codes(result))
        for section in result["sections"][1:]:
            self.assertEqual(1, section["footer"]["page_fields"])
            self.assertEqual({"prefix": "- ", "suffix": " -",
                              "size_half_points": ["18"]},
                             section["footer"]["page_label"])

    def test_split_page_field_rejects_label_and_size_drift(self):
        source = self.dairy_docx()
        with zipfile.ZipFile(source) as archive:
            parts = [name for name in archive.namelist()
                     if re.fullmatch(r"word/footer\d+\.xml", name)]
        for instructions in ((" PAGE ",), (" PA", "GE ")):
            with self.subTest(instructions=instructions):
                def transform(footer):
                    footer = self.complex_page_footer(footer, instructions)
                    return footer.replace(">- </w:t>", "></w:t>", 1).replace(
                        '<w:sz w:val="18"/>', '<w:sz w:val="24"/>')
                path = self.mutated(source, transform, "split-label-drift", part=parts)
                result = self.probe.check(path, major_id="dairy")
                codes = self.dairy_codes(result)
                for role, section in zip(("front", "body"), result["sections"][1:]):
                    self.assertEqual(1, section["footer"]["page_fields"])
                    self.assertIn("dairy.format.page_label." + role, codes)
                    self.assertIn("dairy.format.page_label_size." + role, codes)

    def test_page_field_without_paragraph_cannot_skip_label_check(self):
        # XML은 읽히고 PAGE는 있지만 문단이 없어 번호 꼴을 잴 수 없는 경우.
        source = self.dairy_docx()
        with zipfile.ZipFile(source) as archive:
            parts = [name for name in archive.namelist()
                     if re.fullmatch(r"word/footer\d+\.xml", name)]
        def transform(footer):
            return re.sub(r"<w:p(?:\s[^>]*)?>|</w:p>", "", footer)
        path = self.mutated(source, transform, "unreadable-label", part=parts)
        result = self.probe.check(path, major_id="dairy")
        for role, section in zip(("front", "body"), result["sections"][1:]):
            self.assertEqual(1, section["footer"]["page_fields"])
            self.assertIsNone(section["footer"]["page_label"])
            self.assertIn("dairy.format.page_label." + role, self.dairy_codes(result))

    def test_heading_size_drift_is_reported_per_level(self):
        def transform(document):
            return edit_paragraph(document, "가. 합성 1단계",
                                  '<w:sz w:val="32"/>', '<w:sz w:val="24"/>',
                                  containing='w:val="Heading3"')
        path = self.mutated(self.dairy_docx(), transform, "heading-size")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.heading_size.3", codes)

    def test_chapter_break_drift_is_reported(self):
        # 장 새 쪽은 두 번째 1단계 제목부터다. 첫 장(`1. 머리말`)은 면제,
        # 둘째 장에서 `pageBreakBefore`를 빼면 잡혀야 한다.
        def transform(document):
            return edit_paragraph(document, "2. 농업환경분석",
                                  "<w:pageBreakBefore/>", "",
                                  containing='w:val="Heading1"')
        path = self.mutated(self.dairy_docx(), transform, "chapter-break")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.chapter_break", codes)

    def test_prose_that_looks_numbered_is_not_a_heading(self):
        # 장 판정 음성: 스타일 없는 본문 산문이 `1)`로 시작하면 공용
        # `is_heading`의 글자 추정은 참이 되지만(그 판정은 이 단위 범위 밖의
        # 기존 동작), 낙농 단계 판정은 스타일 증거가 없으므로 level이 없어
        # 크기·간격·쪽나눔 검사 대상이 되지 않아야 한다.
        def transform(document):
            para = ('<w:p><w:pPr><w:spacing w:line="384" w:lineRule="auto"/>'
                    '<w:ind w:firstLine="480"/><w:jc w:val="both"/></w:pPr>'
                    '<w:r><w:rPr><w:rFonts w:ascii="신명조" w:hAnsi="신명조" '
                    'w:eastAsia="신명조"/><w:b w:val="0"/><w:sz w:val="24"/></w:rPr>'
                    '<w:t>1) 산문처럼 보이는 문장이다.</w:t></w:r></w:p>')
            anchor = document.index('w:name="gg_body_1"')
            insert = document.index("</w:p>", anchor) + len("</w:p>")
            return document[:insert] + para + document[insert:]
        path = self.mutated(self.dairy_docx(), transform, "prose")
        self.assertIn("1) 산문처럼 보이는 문장이다.",
                      zipfile.ZipFile(path).read("word/document.xml").decode("utf-8"))
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertEqual([], codes)

    def test_front_title_size_drift_is_reported(self):
        def transform(document):
            return edit_paragraph(document, "요약", '<w:sz w:val="44"/>',
                                  '<w:sz w:val="40"/>',
                                  containing='w:val="Heading1"')
        path = self.mutated(self.dairy_docx(), transform, "front-title")
        codes = self.dairy_codes(self.probe.check(path, major_id="dairy"))
        self.assertIn("dairy.format.front_title_size", codes)

    def test_dairy_output_fails_the_food_crops_form(self):
        # 두 양식은 서로를 통과시키지 않는다(설계 F-3). 낙농 겉표지 여백
        # (30 mm)은 식량작물 겉표지 값(20 mm)과 달라 여기서 먼저 걸린다.
        result = self.probe.check(self.dairy_docx(), major_id="food_crops")
        codes = [v["code"] for v in result["violations"]
                 if v["code"].startswith("food_crops.format.")]
        self.assertIn("food_crops.format.margin.cover_top", codes)

    def test_food_crops_output_fails_the_dairy_form(self):
        result = self.probe.check(self.docx("food_crops"), major_id="dairy")
        codes = [v["code"] for v in result["violations"]
                 if v["code"].startswith("dairy.format.")]
        self.assertIn("dairy.format.margin.cover_top", codes)

    def test_dairy_key_absent_without_the_major_flag(self):
        # 전공을 안 주면 낙농 키 자체가 없어야 한다(다른 전공 JSON 불변).
        result = self.probe.check(self.dairy_docx())
        self.assertNotIn("dairy_format", result)


if __name__ == "__main__":
    unittest.main()
