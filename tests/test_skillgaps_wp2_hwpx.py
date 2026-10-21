"""WP-2 HWPX 결함 잠금 시험 — G3-10 + G2-10(표 폭·장 쪽 나눔·수작업 목록).

합성 HWPX 바이트와 가짜 kordoc CLI만 쓴다. 실제 kordoc·한글은 실행하지 않는다.
"""

import io
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
import zipfile

from tests._harness import (
    ContractCase, bind_major, runtime, script_source, text_calls_without_encoding,
)

TABLE = ('<hp:tbl id="7" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
         'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" '
         'repeatHeader="0" rowCnt="2" colCnt="2" cellSpacing="0" borderFillIDRef="2" '
         'noShading="0"><hp:sz width="44000" widthRelTo="ABSOLUTE" height="4000" '
         'heightRelTo="ABSOLUTE" protect="0"/><hp:tr><hp:tc header="1">'
         '<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
         '<hp:cellSz width="20000" height="2000"/></hp:tc><hp:tc header="1">'
         '<hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
         '<hp:cellSz width="24000" height="2000"/></hp:tc></hp:tr><hp:tr>'
         '<hp:tc><hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
         '<hp:cellSz width="20000" height="2000"/></hp:tc><hp:tc>'
         '<hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
         '<hp:cellSz width="24000" height="2000"/></hp:tc></hp:tr></hp:tbl>')

SECTION_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
                '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                '<hp:p><hp:run><hp:secPr><hp:pagePr landscape="WIDELY" '
                'width="59528" height="84188" gutterType="LEFT_ONLY">'
                '<hp:margin header="2835" footer="2835" gutter="0" left="5670" '
                'right="4252" top="8504" bottom="4252"/></hp:pagePr></hp:secPr>'
                '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" '
                'colCount="1" sameSz="1" sameGap="0"/></hp:ctrl></hp:run></hp:p>')

def section(*paragraphs):
    return SECTION_HEAD + "".join(paragraphs) + "</hs:sec>"

def para(text, para_ref="0", attrs=""):
    return ('<hp:p paraPrIDRef="' + para_ref + '" styleIDRef="0"' + attrs + '>'
            '<hp:run charPrIDRef="0"><hp:t>' + text + '</hp:t></hp:run></hp:p>')

def hwpx_bytes(sec):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("Contents/header.xml", "<hh:head/>")
        archive.writestr("Contents/section0.xml", sec)
    return buffer.getvalue()

class HwpxLayoutTests(ContractCase):
    """G3-10 + G2-10: 표 폭 155.2 mm·장 쪽 나눔 없음·수작업 목록 누락."""

    def setUp(self):
        super().setUp()
        self.builder = runtime("build_hwpx")
        self.gh = runtime("gg_hwpx")

    def test_body_width_is_a4_minus_side_margins(self):
        self.assertEqual(42520, self.builder.body_width_units())
        self.assertEqual(42520, self.builder.body_width_units(
            {"left": 30, "right": 30}))

    def test_table_width_and_cells_fit_body_width(self):
        data, report = self.builder.apply_layout(hwpx_bytes(section(TABLE)))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertIn('<hp:sz width="42520" widthRelTo="ABSOLUTE"', xml)
        self.assertNotIn('width="44000"', xml)
        for row in re.findall(r"<hp:tr\b.*?</hp:tr>", xml, re.S):
            widths = [int(w) for w in re.findall(
                r'<hp:cellSz\b[^>]*?width="(\d+)"', row)]
            self.assertEqual(42520, sum(widths), row)
        self.assertEqual(1, report["tables"])
        self.assertEqual([44000], report["previous_widths"])

    def test_mimetype_stays_first_and_stored(self):
        data, _ = self.builder.apply_layout(hwpx_bytes(section(TABLE)))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            first = archive.infolist()[0]
            self.assertEqual("mimetype", first.filename)
            self.assertEqual(zipfile.ZIP_STORED, first.compress_type)

    def test_chapter_breaks_only_on_heading_style_chapters(self):
        sec = section(
            para("Ⅰ. 머리말", "1"),
            para("본문 문단이다."),
            para("Ⅱ. 외부환경분석", "1"),
            para("Ⅴ. 기초생장물재고액", "0"),   # 재무제표 주석 항목
            para("Ⅵ. 참고문헌", "1"),
        )
        data, report = self.builder.apply_layout(hwpx_bytes(sec))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertEqual(["Ⅰ. 머리말", "Ⅱ. 외부환경분석", "Ⅵ. 참고문헌"],
                         report["chapter_breaks"])
        self.assertEqual(3, len(re.findall(r'<hp:p\b[^>]*pageBreak="1"', xml)))
        self.assertNotRegex(xml, r'pageBreak="1">(?:<hp:run[^>]*><hp:t>)?Ⅴ\.')

    def test_existing_break_value_is_normalized_not_duplicated(self):
        sec = section(para("Ⅱ. 외부환경분석", "1", ' pageBreak="0"'))
        data, report = self.builder.apply_layout(hwpx_bytes(sec))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertEqual(["Ⅱ. 외부환경분석"], report["chapter_breaks"])
        self.assertEqual(1, xml.count('pageBreak="1"'))
        self.assertNotIn('pageBreak="0"', xml)

    def test_layout_is_idempotent(self):
        once, report = self.builder.apply_layout(hwpx_bytes(section(TABLE,
                                                                   para("Ⅰ. 머리말", "1"))))
        twice, second = self.builder.apply_layout(once)
        self.assertEqual(once, twice)
        self.assertEqual(0, second["tables"])
        self.assertEqual([], second["chapter_breaks"])
        self.assertEqual(1, report["tables"])

    def test_human_finish_lists_table_width_and_chapter_breaks(self):
        listed = " ".join(self.builder.HUMAN_FINISH)
        self.assertIn("표 폭", listed)
        self.assertIn("장(Ⅰ~Ⅵ) 시작", listed)

    def test_export_publishes_layout_and_hashes_published_bytes(self):
        """발행 파일의 해시·구조 검증이 후처리 뒤 파일 기준이어야 한다."""
        root = self.make_project()
        bind_major(root)
        (Path(root) / "body.md").write_text(
            "Ⅰ. 머리말\n\n표 1. 합성\n\n| 구분 | 값 |\n| --- | ---: |\n| 매출 | 1,000 |\n",
            encoding="utf-8")
        cli = _layout_cli(root)
        mc = runtime("gg_major_contract")
        value = self.builder.export(
            root, "body.md", "out.hwpx", cli_command=cli,
            context=mc.output_context(root, "specialty_crops"))
        target = Path(root) / "out.hwpx"
        manifest = json.loads(
            (Path(root) / "out.hwpx.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime("gg_core").digest(target.read_bytes()),
                         manifest["file_hash"])
        self.assertEqual(42520, manifest["hwpx"]["layout"]["body_width"])
        self.assertEqual(1, manifest["hwpx"]["layout"]["tables"])
        self.assertEqual(["Ⅰ. 머리말"],
                         manifest["hwpx"]["layout"]["chapter_breaks"])
        self.assertEqual("ok", manifest["hwpx"]["validate"]["status"])
        self.assertFalse((Path(root) / ".out.hwpx.layout-staging").exists())
        with zipfile.ZipFile(target) as archive:
            xml = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertIn('<hp:sz width="42520"', xml)
        self.assertEqual(value["manifest"]["file_hash"], manifest["file_hash"])

    def test_export_keeps_failed_layout_from_publishing(self):
        """후처리 검증이 실패하면 본체를 발행하지 않는다."""
        root = self.make_project()
        bind_major(root)
        (Path(root) / "body.md").write_text(
            "Ⅰ. 머리말\n\n표 1. 합성\n\n| 구분 | 값 |\n| --- | ---: |\n| 매출 | 1,000 |\n",
            encoding="utf-8")
        cli = _layout_cli(root, validate_after_first=False)
        mc = runtime("gg_major_contract")
        with self.assertRaises(self.gh.HwpxBlocked) as caught:
            self.builder.export(
                root, "body.md", "out.hwpx", cli_command=cli,
                context=mc.output_context(root, "specialty_crops"))
        self.assertEqual("layout_validate_failed", caught.exception.reason)
        self.assertFalse((Path(root) / "out.hwpx").exists())
        self.assertFalse((Path(root) / "out.hwpx.manifest.json").exists())
        self.assertFalse((Path(root) / ".out.hwpx.layout-staging").exists())

FAKE_CLI = """\
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = json.loads((HERE / "control.json").read_text(encoding="utf-8"))
SECTION = CONTROL["section"]
HEADER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
          '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
          '<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="1">'
          '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/></hh:fontface>'
          '</hh:fontfaces></hh:head>')

def write_hwpx(path):
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("Contents/header.xml", HEADER)
        archive.writestr("Contents/section0.xml", SECTION)

def main(argv):
    args = argv[1:]
    if args and args[0] == "generate":
        write_hwpx(args[args.index("-o") + 1])
        return 0
    if args and args[0] == "validate":
        counter = HERE / "validate-count"
        count = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(count + 1))
        if not CONTROL.get("validate_after_first", True) and count >= 1:
            sys.stderr.write("layout validate failed")
            return 1
        print(json.dumps({"ok": True, "issues": [], "entryCount": 4}))
        return 0
    if "-o" in args:
        Path(args[args.index("-o") + 1]).write_text(
            CONTROL.get("markdown", ""), encoding="utf-8")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
"""

BODY_SECTION = section(
    para("Ⅰ. 머리말", "1"),
    para("본문이다."),
    TABLE,
    para("표 1. 합성"),
)

def _layout_cli(root, **control):
    work = Path(root) / "fake"
    work.mkdir(exist_ok=True)
    script = work / "fake_kordoc.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    control.setdefault("section", BODY_SECTION)
    control.setdefault("markdown",
                       "Ⅰ. 머리말\n\n본문이다.\n\n표 1. 합성\n\n| 구분 | 값 |\n"
                       "| --- | ---: |\n| 매출 | 1,000 |\n")
    (work / "control.json").write_text(
        json.dumps(control, ensure_ascii=False), encoding="utf-8")
    return [sys.executable, str(script)]


class ConventionTests(unittest.TestCase):
    def test_text_io_and_execution_guards(self):
        source = script_source("build_hwpx.py")
        self.assertEqual([], text_calls_without_encoding(source))
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)

if __name__ == "__main__":
    unittest.main()
