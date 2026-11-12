"""식량작물 HWPX 쪽별 서식 기계 검사기(`gg_school_format_hwpx`) 검사.

3구역 산출물에서 §6.2의 모든 행이 통과하는지, 결함이 있으면 해당 코드로
차단되는지, 구역이 빠진 ZIP이 예외 대신 blocked가 되는지를 본다.
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO

from tests._harness import runtime
from tests.test_build_hwpx import (FOOD_CROPS_PARAGRAPHS, food_crops_plan,
                                   food_crops_zip, hwpx_section, read_zip)

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "skills" / "knuaf-doc" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

gh = runtime("gg_hwpx")
probe = runtime("gg_school_format_hwpx")


def _transform(markdown=None):
    nodes, plan = food_crops_plan(*([markdown] if markdown else []))
    data, _ = gh.transform_hwpx(
        food_crops_zip(hwpx_section(list(FOOD_CROPS_PARAGRAPHS))),
        section_formats=gh.FOOD_CROPS_SECTION_FORMATS, nodes=nodes, plan=plan)
    return data


def _write(tmp, data, name="out.hwpx"):
    path = pathlib.Path(tmp) / name
    path.write_bytes(data)
    return path


def _rewrite(members, path):
    """members(dict)를 새 ZIP으로 다시 써서 고친 파일을 만든다(순서 보존)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.output = _transform()
        cls.path = _write(cls.tmp.name, cls.output)
        cls.result = probe.check(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def codes(self, result):
        return sorted({value["code"] for value in result["violations"]})

    def test_three_section_output_passes_every_row(self):
        self.assertEqual("pass_with_notes", self.result["status"], self.result["violations"])
        self.assertEqual([], self.result["violations"])
        checks = self.result["checks"]
        for key in ("section", "page_number", "charpr", "manifest"):
            self.assertEqual("pass", checks[key]["status"], (key, checks[key]))
        self.assertEqual([0, 1, 1], checks["page_number"]["page_num_counts"])
        self.assertEqual([0, 1, 1], checks["page_number"]["new_num_counts"])
        self.assertEqual(3, checks["section"]["count"])
        self.assertEqual("3", checks["section"]["sec_cnt"])
        # 앞부분 전용 charPr 14개가 id 1..14로 붙고, 본문에는 새지 않는다.
        self.assertEqual([str(index) for index in range(1, 15)],
                         sorted(checks["charpr"]["front_charpr_ids"], key=int))
        # version.xml은 1판·양식 원본에도 없는 사전 결함이라 차단하지 않고 기록만 한다.
        self.assertFalse(checks["manifest"]["version_xml_present"])
        self.assertIn("사전 결함", checks["manifest"]["version_xml_note"])
        # CLI 없이 돌렸으므로 구조·재파싱은 건너뛴 것으로 기록된다.
        self.assertEqual("skipped", checks["structure"]["status"])
        self.assertEqual("skipped", checks["reparse"]["status"])

    def test_every_violation_is_local_to_the_probe(self):
        mutated = _write(self.tmp.name, self.output, "margins.hwpx")
        members = read_zip(self.output)
        members["Contents/section1.xml"] = members["Contents/section1.xml"].replace(
            b'left="8504"', b'left="8503"')
        _rewrite(members, mutated)
        result = probe.check(mutated)
        self.assertEqual("blocked", result["status"])
        for violation in result["violations"]:
            self.assertTrue(violation["probe_local"], violation)
            self.assertEqual(violation["code"], violation["requirement_id"])
            self.assertTrue(violation["code"].startswith("food_crops.format."))

    def test_wrong_margin_reports_the_section_and_guideline_code(self):
        path = _write(self.tmp.name, self.output, "margin-code.hwpx")
        members = read_zip(self.output)
        members["Contents/section2.xml"] = members["Contents/section2.xml"].replace(
            b'right="8504"', b'right="5669"')
        _rewrite(members, path)
        result = probe.check(path)
        found = [value for value in result["violations"]
                 if value["code"] == "food_crops.format.section.margin.body_right"]
        self.assertEqual(1, len(found), self.codes(result))
        self.assertEqual(["OG-011"], found[0]["related"])
        self.assertEqual("8504", found[0]["expected"])
        self.assertEqual("5669", found[0]["actual"])

    def test_kordoc_a4_width_59527_is_accepted(self):
        # kordoc은 폭을 1 HWPUNIT 깎아 59527로 낸다(1판 산출물과 같다).
        path = _write(self.tmp.name, self.output, "a4-59527.hwpx")
        members = read_zip(self.output)
        for name in (b"Contents/section0.xml", b"Contents/section1.xml",
                     b"Contents/section2.xml"):
            members[name.decode()] = members[name.decode()].replace(
                b'width="59528"', b'width="59527"')
        _rewrite(members, path)
        result = probe.check(path)
        self.assertEqual([], result["violations"])
        self.assertEqual("pass", result["checks"]["section"]["status"])

    def test_other_page_width_is_rejected(self):
        path = _write(self.tmp.name, self.output, "a4-wide.hwpx")
        members = read_zip(self.output)
        members["Contents/section1.xml"] = members["Contents/section1.xml"].replace(
            b'width="59528"', b'width="59529"')
        _rewrite(members, path)
        result = probe.check(path)
        self.assertIn("food_crops.format.section.margin.front", self.codes(result))

    def test_cover_page_number_is_rejected(self):
        path = _write(self.tmp.name, self.output, "cover-number.hwpx")
        members = read_zip(self.output)
        control = (b'<hp:ctrl><hp:pageNum pos="BOTTOM_CENTER" formatType="DIGIT"'
                   b' sideChar="-"/></hp:ctrl>')
        members["Contents/section0.xml"] = members["Contents/section0.xml"].replace(
            b"</hp:run>", control + b"</hp:run>", 1)
        _rewrite(members, path)
        result = probe.check(path)
        self.assertIn("food_crops.format.page_number.cover", self.codes(result))
        self.assertIn("food_crops.format.page_number.count", self.codes(result))

    def test_wrong_front_number_format_is_rejected(self):
        path = _write(self.tmp.name, self.output, "front-format.hwpx")
        members = read_zip(self.output)
        members["Contents/section1.xml"] = members["Contents/section1.xml"].replace(
            b"ROMAN_CAPITAL", b"DIGIT", 1)
        _rewrite(members, path)
        result = probe.check(path)
        found = [value for value in result["violations"]
                 if value["code"] == "food_crops.format.page_number.front"]
        self.assertTrue(found, self.codes(result))
        self.assertEqual("ROMAN_CAPITAL", found[0]["expected"]["formatType"])

    def test_missing_restart_is_reported_for_each_anchor(self):
        path = _write(self.tmp.name, self.output, "no-restart.hwpx")
        members = read_zip(self.output)
        members["Contents/section2.xml"] = members["Contents/section2.xml"].replace(
            b'<hp:ctrl><hp:newNum num="1" numType="PAGE"/></hp:ctrl>', b"")
        _rewrite(members, path)
        codes = self.codes(probe.check(path))
        self.assertIn("food_crops.format.page_number.body_restart", codes)
        self.assertIn("food_crops.format.page_number.duplicate", codes)

    def test_front_charpr_leaking_into_body_is_rejected(self):
        path = _write(self.tmp.name, self.output, "leak.hwpx")
        members = read_zip(self.output)
        # 목차 항목(12 pt)이 아니라 본문 첫 문단을 앞부분 전용 14 pt 굵게로 바꾼다.
        section = members["Contents/section2.xml"]
        members["Contents/section2.xml"] = section.replace(
            b'<hp:run charPrIDRef="0">', b'<hp:run charPrIDRef="3">', 1)
        _rewrite(members, path)
        codes = self.codes(probe.check(path))
        self.assertIn("food_crops.format.charpr.body_invariant", codes)

    def test_wrong_consent_size_is_rejected(self):
        path = _write(self.tmp.name, self.output, "consent.hwpx")
        members = read_zip(self.output)
        section = members["Contents/section2.xml"]
        members["Contents/section2.xml"] = section.replace(
            b'<hp:run charPrIDRef="3">', b'<hp:run charPrIDRef="0">', 1)
        _rewrite(members, path)
        codes = self.codes(probe.check(path))
        self.assertIn("food_crops.format.charpr.consent", codes)

    def test_missing_section_is_blocked_not_raised(self):
        path = pathlib.Path(self.tmp.name) / "truncated.hwpx"
        members = read_zip(self.output)
        del members["Contents/section1.xml"]
        _rewrite(members, path)
        result = probe.check(path)
        self.assertEqual("blocked", result["status"])
        self.assertIn("food_crops.format.section.count", self.codes(result))
        self.assertEqual("skipped", result["checks"]["structure"]["status"])

    def test_broken_xml_is_blocked_not_raised(self):
        path = pathlib.Path(self.tmp.name) / "broken.hwpx"
        members = read_zip(self.output)
        members["Contents/section2.xml"] = b"<hs:sec><hp:p>unterminated"
        _rewrite(members, path)
        result = probe.check(path)
        self.assertEqual("blocked", result["status"])
        notes = json.dumps(result.get("notes", []), ensure_ascii=False)
        self.assertIn("Contents/section2.xml", notes)

    def test_broken_header_is_blocked_not_raised(self):
        path = pathlib.Path(self.tmp.name) / "header.hwpx"
        members = read_zip(self.output)
        members["Contents/header.xml"] = b'<hh:head xmlns:hh="urn:x">'
        _rewrite(members, path)
        result = probe.check(path)
        self.assertEqual("blocked", result["status"])
        self.assertTrue(self.codes(result))

    def test_not_a_zip_is_blocked(self):
        path = pathlib.Path(self.tmp.name) / "plain.hwpx"
        path.write_bytes(b"not a zip")
        result = probe.check(path)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["food_crops.format.package"], self.codes(result))

    def test_missing_file_is_blocked(self):
        result = probe.check(pathlib.Path(self.tmp.name) / "absent.hwpx")
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["food_crops.format.package"], self.codes(result))

    def test_cover_without_year_paragraph_is_not_a_violation(self):
        # 날짜가 비면 겉표지에 연도 문단이 아예 없다(자리표시자만 온다).
        # 연도 문단 부재는 결함이 아니므로 위반으로 만들지 않는다.
        path = _write(self.tmp.name, self.output, "no-year.hwpx")
        members = read_zip(self.output)
        section = members["Contents/section0.xml"].decode("utf-8")
        stripped = re.sub(
            r'<hp:p [^>]*><hp:run charPrIDRef="10"><hp:t>\d{4}년[^<]*</hp:t>'
            r"</hp:run></hp:p>", "", section)
        self.assertLess(len(stripped), len(section))
        members["Contents/section0.xml"] = stripped.encode("utf-8")
        _rewrite(members, path)
        result = probe.check(path)
        self.assertNotIn("food_crops.format.charpr.front", self.codes(result))
        self.assertEqual([], result["violations"])

    def test_wrong_year_size_is_still_rejected(self):
        path = _write(self.tmp.name, self.output, "year-size.hwpx")
        members = read_zip(self.output)
        members["Contents/section0.xml"] = members["Contents/section0.xml"].replace(
            b'<hp:run charPrIDRef="10"><hp:t>2027', b'<hp:run charPrIDRef="5"><hp:t>2027', 1)
        _rewrite(members, path)
        result = probe.check(path)
        self.assertIn("food_crops.format.charpr.front", self.codes(result))

    def test_duplicate_manifest_id_is_rejected(self):
        path = pathlib.Path(self.tmp.name) / "manifest.hwpx"
        members = read_zip(self.output)
        members["Contents/content.hpf"] = members["Contents/content.hpf"].replace(
            b'id="section1"', b'id="section0"')
        _rewrite(members, path)
        result = probe.check(path)
        self.assertIn("food_crops.format.manifest.spine", self.codes(result))

    def test_cli_writes_json_and_reports_exit_code(self):
        out = pathlib.Path(self.tmp.name) / "result.json"
        process = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format_hwpx.py"),
             str(self.path), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, process.returncode, process.stderr)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("pass_with_notes", payload["status"])
        self.assertEqual([], payload["violations"])
        summary = json.loads(process.stdout)
        self.assertEqual(0, summary["violations"])

    def test_cli_refuses_to_overwrite_its_input(self):
        target = _write(self.tmp.name, self.output, "same.hwpx")
        before = target.read_bytes()
        process = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg_school_format_hwpx.py"),
             str(target), "--out", str(target)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(2, process.returncode)
        self.assertIn("refusing overwrite", process.stderr)
        self.assertEqual(before, target.read_bytes())

    def fake_kordoc(self, name, *, validate, reparse):
        """kordoc 대역 — 같은 폴더의 validate.json/reparse.md를 그대로 낸다."""
        root = pathlib.Path(self.tmp.name) / name
        root.mkdir()
        (root / "validate.json").write_text(
            json.dumps(validate, ensure_ascii=False), encoding="utf-8")
        (root / "reparse.md").write_text(reparse, encoding="utf-8")
        script = root / "kordoc"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "here = pathlib.Path(__file__).resolve().parent\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'validate':\n"
            "    sys.stdout.write((here / 'validate.json').read_text(encoding='utf-8'))\n"
            "    sys.exit(0 if '\"ok\": true' in (here / 'validate.json').read_text(encoding='utf-8') else 1)\n"
            "target = pathlib.Path(args[args.index('-o') + 1])\n"
            "target.write_text((here / 'reparse.md').read_text(encoding='utf-8'), encoding='utf-8')\n",
            encoding="utf-8")
        script.chmod(0o755)
        return [sys.executable, "-B", str(script)]

    def test_structure_and_reparse_pass_with_kordoc(self):
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections for text in section["texts"] if text]
        cli = self.fake_kordoc(
            "kordoc-ok",
            validate={"ok": True, "issues": [{"message": "version.xml is missing"}]},
            reparse="\n\n".join(texts) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual([], result["violations"])
        self.assertEqual("ok", result["checks"]["structure"]["status"])
        self.assertEqual(1, result["checks"]["structure"]["pre_existing"])
        self.assertEqual([], result["checks"]["structure"]["issues"])
        self.assertEqual("ok", result["checks"]["reparse"]["status"])
        self.assertEqual(0, result["checks"]["reparse"]["missing_count"])

    def test_structure_issue_beyond_version_xml_is_reported(self):
        cli = self.fake_kordoc(
            "kordoc-broken",
            validate={"ok": False,
                      "issues": [{"message": "version.xml is missing"},
                                 {"message": "table cell count mismatch"}]},
            reparse="")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual("blocked", result["status"])
        self.assertIn("food_crops.format.structure", self.codes(result))
        self.assertEqual(1, result["checks"]["structure"]["pre_existing"])
        self.assertEqual(["{'message': 'table cell count mismatch'}"],
                         [str(issue) for issue in result["checks"]["structure"]["issues"]])

    def test_reparse_losing_a_paragraph_is_reported(self):
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections for text in section["texts"] if text]
        cli = self.fake_kordoc(
            "kordoc-lossy",
            validate={"ok": True, "issues": [{"message": "version.xml is missing"}]},
            reparse="\n\n".join(texts[1:]) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertIn("food_crops.format.reparse", self.codes(result))
        self.assertEqual(texts[0], result["checks"]["reparse"]["missing"][0]["text"])

    def test_reparse_tolerates_markdown_escaping_and_collapsed_spaces(self):
        # kordoc은 `*`를 `\*`로 이스케이프하고 연속 공백을 하나로 줄인다.
        # 글자가 살아 있는지만 보므로 이런 표기 차이는 위반이 아니다.
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections for text in section["texts"] if text]
        collapsed = [re.sub(r"\s+", " ", text).replace("*", "\\*") for text in texts]
        cli = self.fake_kordoc(
            "kordoc-markup",
            validate={"ok": True, "issues": []},
            reparse="\n\n".join(collapsed) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual("ok", result["checks"]["reparse"]["status"],
                         result["checks"]["reparse"])
        self.assertEqual(0, result["checks"]["reparse"]["missing_count"])

    def test_reparse_tolerates_table_flattening(self):
        # 표는 재파싱에서 `| 셀 | 셀 |` 한 줄로 펴진다. 셀 경계 문자가
        # 사라져도 셀 글자는 이어져 남으므로 '사라진 문단'으로 보지 않는다.
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections for text in section["texts"] if text]
        flattened = [text.replace(" ", "") for text in texts]
        cli = self.fake_kordoc(
            "kordoc-table",
            validate={"ok": True, "issues": []},
            reparse="\n\n".join(flattened) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual(0, result["checks"]["reparse"]["missing_count"],
                         result["checks"]["reparse"])

    def test_directory_entry_is_not_an_unlisted_item(self):
        # ZIP 디렉터리 항목(`BinData/`)은 파일이 아니라 manifest 대조에서 뺀다.
        path = _write(self.tmp.name, self.output, "dir-entry.hwpx")
        members = read_zip(self.output)
        members["BinData/"] = b""
        _rewrite(members, path)
        result = probe.check(path)
        self.assertNotIn("food_crops.format.manifest.items", self.codes(result))
        self.assertEqual([], result["violations"])

    def test_absent_kordoc_never_blocks(self):
        result = probe.check(
            self.path, cli_command=[str(pathlib.Path(self.tmp.name) / "no-such-kordoc")])
        self.assertEqual([], result["violations"])
        self.assertEqual("skipped", result["checks"]["structure"]["status"])
        self.assertEqual("skipped", result["checks"]["reparse"]["status"])


if __name__ == "__main__":
    unittest.main()
