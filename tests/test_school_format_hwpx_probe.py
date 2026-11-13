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
from unittest import mock

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


def _corrupt_crc(data, name="Contents/header.xml"):
    """항목 payload 한 바이트를 뒤집어 CRC가 맞지 않는 ZIP을 만든다.

    `writestr`는 payload로 CRC를 새로 계산하므로, 만든 뒤 로컬 헤더를 따라가
    payload 바이트를 직접 건드려야 `ZipFile.read`가 BadZipFile을 낸다.
    """
    members = read_zip(data)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
    raw = bytearray(buffer.getvalue())
    offset = 0
    while True:
        offset = raw.find(b"PK\x03\x04", offset)
        if offset < 0:
            raise AssertionError("ZIP 로컬 헤더를 찾지 못함: " + name)
        name_len = int.from_bytes(raw[offset + 26:offset + 28], "little")
        found = bytes(raw[offset + 30:offset + 30 + name_len]).decode("utf-8")
        if found == name:
            extra_len = int.from_bytes(raw[offset + 28:offset + 30], "little")
            start = offset + 30 + name_len + extra_len
            raw[start] ^= 0xFF
            return bytes(raw)
        offset += 4


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

    def test_crc_corrupted_member_is_blocked_not_raised(self):
        # ZIP 구조는 멀쩡한데 항목 payload와 CRC가 어긋나면 archive.read()가
        # BadZipFile을 낸다. 예외로 죽지 않고 package 차단으로 닫혀야 한다.
        path = pathlib.Path(self.tmp.name) / "crc.hwpx"
        path.write_bytes(_corrupt_crc(self.output))
        with zipfile.ZipFile(path) as archive:
            with self.assertRaises(zipfile.BadZipFile):
                archive.read("Contents/header.xml")
        result = probe.check(path)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["food_crops.format.package"], self.codes(result))

    def test_numbered_contents_entry_is_not_blocked(self):
        # 목차 항목도 `1. 머리말`처럼 숫자·마침표로 시작하지만 12 pt 보통이다.
        # 번호형 규칙을 요약 구간에만 적용해야 이 문단이 오탐되지 않는다.
        path = _write(self.tmp.name, self.output, "toc-entry.hwpx")
        members = read_zip(self.output)
        entry = ('<hp:p paraPrIDRef="0" styleIDRef="0">'
                 '<hp:run charPrIDRef="2"><hp:t>1. 머리말</hp:t></hp:run></hp:p>')
        members["Contents/section1.xml"] = members["Contents/section1.xml"].replace(
            b"</hs:sec>", entry.encode("utf-8") + b"</hs:sec>", 1)
        _rewrite(members, path)
        result = probe.check(path)
        self.assertEqual([], result["violations"])
        self.assertEqual("pass", result["checks"]["charpr"]["status"])

    def test_numbered_summary_subheading_is_still_sixteen(self):
        # 요약 구간 안의 번호형 소제목은 그대로 16 pt 굵게여야 한다.
        path = _write(self.tmp.name, self.output, "summary-subheading.hwpx")
        members = read_zip(self.output)
        xml = members["Contents/section1.xml"].decode("utf-8")
        mutated = xml.replace('<hp:run charPrIDRef="5"><hp:t>1. 농장 개요',
                              '<hp:run charPrIDRef="2"><hp:t>1. 농장 개요', 1)
        self.assertNotEqual(xml, mutated)
        members["Contents/section1.xml"] = mutated.encode("utf-8")
        _rewrite(members, path)
        result = probe.check(path)
        self.assertIn("food_crops.format.charpr.front", self.codes(result))

    def test_reparse_failure_with_kordoc_is_blocked(self):
        # 실행 파일은 있는데 재파싱이 실패하면 검사 불가가 아니라 실패다.
        cli = self.fake_kordoc(
            "kordoc-reparse-fail",
            validate={"ok": True, "issues": []}, reparse="", reparse_exit=3)
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual("blocked", result["status"])
        self.assertIn("food_crops.format.reparse", self.codes(result))
        self.assertEqual("failed", result["checks"]["reparse"]["status"])
        self.assertEqual(3, result["checks"]["reparse"]["returncode"])

    def test_version_xml_only_failure_is_a_pre_existing_defect(self):
        # kordoc은 version.xml 부재만으로도 ok:false를 낸다. 그 항목만 있으면
        # 1판·양식 원본과 같은 사전 결함이므로 통과시킨다.
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections
                 for text in section["texts"] if text]
        cli = self.fake_kordoc(
            "kordoc-version-only",
            validate={"ok": False,
                      "issues": [{"message": "version.xml is missing"}]},
            reparse="\n\n".join(texts) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual([], result["violations"])
        self.assertEqual("ok", result["checks"]["structure"]["status"])
        self.assertFalse(result["checks"]["structure"]["kordoc_ok"])
        self.assertEqual(1, result["checks"]["structure"]["pre_existing"])

    def test_failure_without_any_reported_issue_is_still_blocked(self):
        # ok:false인데 문제를 하나도 보고하지 않으면 그 false의 근거를 이
        # 검사기가 확인할 수 없다. 통과시키지 않는다.
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections
                 for text in section["texts"] if text]
        cli = self.fake_kordoc(
            "kordoc-silent-failure",
            validate={"ok": False, "issues": []},
            reparse="\n\n".join(texts) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertEqual("blocked", result["status"])
        self.assertIn("food_crops.format.structure", self.codes(result))

    def test_reparse_with_reordered_paragraphs_is_reported(self):
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections
                 for text in section["texts"] if text]
        swapped = list(texts)
        swapped[8], swapped[9] = swapped[9], swapped[8]
        cli = self.fake_kordoc(
            "kordoc-reordered",
            validate={"ok": True, "issues": []},
            reparse="\n\n".join(swapped) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertIn("food_crops.format.reparse.order", self.codes(result))
        self.assertGreaterEqual(
            result["checks"]["reparse"]["out_of_order_count"], 1)

    def test_reparse_missing_a_repeated_paragraph_is_reported(self):
        # 반복 문단이 하나로 접히면 포함 여부만 보는 대조는 통과한다.
        # 개수 하한을 걸어야 잡힌다.
        measured, _ = probe.measure(self.path)
        texts = [text for section in measured.sections
                 for text in section["texts"] if text]
        counts = {}
        for text in texts:
            wanted = probe._normalize_text(text)
            counts[wanted] = counts.get(wanted, 0) + 1
        full = probe._normalize_text("\n\n".join(texts) + "\n")
        # 다른 문단의 부분 문자열인 문단은 개수 대조가 흔들리므로, 온전한
        # 재파싱에서 기대 개수와 실제 개수가 이미 같은 반복 문단만 고른다.
        repeated = next(text for text in texts
                        if counts[probe._normalize_text(text)] > 1
                        and full.count(probe._normalize_text(text))
                        == counts[probe._normalize_text(text)])
        index = len(texts) - texts[::-1].index(repeated) - 1
        cli = self.fake_kordoc(
            "kordoc-collapsed",
            validate={"ok": True, "issues": []},
            reparse="\n\n".join(texts[:index] + texts[index + 1:]) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertIn("food_crops.format.reparse.count", self.codes(result))
        self.assertGreaterEqual(
            result["checks"]["reparse"]["count_shortfall_count"], 1)

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

    def fake_kordoc(self, name, *, validate, reparse, reparse_exit=0):
        """kordoc 대역 — 같은 폴더의 validate.json/reparse.md를 그대로 낸다.

        `reparse_exit`가 0이 아니면 재파싱 호출에서 파일을 쓰지 않고 그 코드로
        끝난다(실행 파일은 있는데 재파싱이 실패하는 경우).
        """
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
            f"if {int(reparse_exit)}:\n"
            f"    print('reparse exploded', file=sys.stderr)\n"
            f"    sys.exit({int(reparse_exit)})\n"
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
        # 구분 이름을 은닉하면 같은 글자가 여러 문단에 나온다(예: 학위논문 표기).
        # '사라진 문단' 판정을 보려면 다른 문단의 부분 문자열이 아닌 문단을 골라야
        # 한다. 앞에서부터 고르면 어떤 문단이 은닉되느냐에 따라 시험이 흔들린다.
        lost = next(text for text in texts
                    if sum(text in other for other in texts) == 1)
        cli = self.fake_kordoc(
            "kordoc-lossy",
            validate={"ok": True, "issues": [{"message": "version.xml is missing"}]},
            reparse="\n\n".join(text for text in texts if text != lost) + "\n")
        result = probe.check(self.path, cli_command=cli)
        self.assertIn("food_crops.format.reparse", self.codes(result))
        self.assertEqual(lost, result["checks"]["reparse"]["missing"][0]["text"])

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

    def test_unrunnable_kordoc_blocks_both_checks(self):
        # I-04: `--kordoc`으로 명령을 지정했으면 그 검사를 실제로 해야 한다.
        # 파일이 없어 실행하지 못한 경우 `skipped`로 넘기면 검증이 빠진
        # 산출물이 `pass_with_notes`로 나간다. 두 검사 모두 위반 + failed다.
        result = probe.check(
            self.path, cli_command=[str(pathlib.Path(self.tmp.name) / "no-such-kordoc")])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["food_crops.format.structure", "food_crops.format.reparse"],
                         [value["code"] for value in result["violations"]])
        self.assertEqual("failed", result["checks"]["structure"]["status"])
        self.assertEqual("failed", result["checks"]["reparse"]["status"])
        self.assertEqual("FileNotFoundError",
                         result["checks"]["structure"]["error"])

    def test_kordoc_timeout_blocks_both_checks(self):
        # 같은 계약의 타임아웃 경로. `_run`이 TimeoutExpired를 그대로 돌려주는
        # 현 구조를 대역으로 재현한다(180초를 실제로 기다리지 않는다).
        expired = subprocess.TimeoutExpired(cmd=["kordoc"], timeout=180)
        with mock.patch.object(probe, "_run", return_value=expired):
            result = probe.check(
                self.path, cli_command=[str(pathlib.Path(self.tmp.name) / "slow-kordoc")])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["food_crops.format.structure", "food_crops.format.reparse"],
                         [value["code"] for value in result["violations"]])
        self.assertEqual("TimeoutExpired",
                         result["checks"]["reparse"]["error"])

    def test_absent_kordoc_is_skipped(self):
        # `--kordoc`을 아예 넘기지 않은 경우만 skip이다(호출자가 검사를
        # 요청하지 않았다).
        result = probe.check(self.path)
        self.assertEqual([], result["violations"])
        self.assertEqual("skipped", result["checks"]["structure"]["status"])
        self.assertEqual("skipped", result["checks"]["reparse"]["status"])


if __name__ == "__main__":
    unittest.main()
