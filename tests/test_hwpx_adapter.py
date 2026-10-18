"""gg_hwpx 어댑터 계약 시험 — kordoc generate → 학교 논문 규격 HWPX.

후처리·경로·인코딩·검증 로직은 가짜 CLI(파이썬 스크립트를 sys.executable로 실행,
셸 스크립트 아님)와 순수 함수 시험으로 kordoc 없이 돈다. skip은 두지 않는다 —
실물 kordoc 검증은 일회성 수동 확인으로 대체했다(RESULT-W1b/W1c 참조).
"""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from tests._harness import (
    ContractCase, bind_major, runtime, script_source, text_calls_without_encoding,
)

gh = runtime("gg_hwpx")

SAMPLE_MD = (
    "# 창업영농계획서\n"
    "\n"
    "## Ⅰ. 서론\n"
    "\n"
    "본 연구는 *Panax ginseng* 재배 농장을 분석한다.\n"
    "\n"
    "| 구분 | 1년차 | 2년차 |\n"
    "| --- | ---: | ---: |\n"
    "| 매출액 | 10,000 | 12,345 |\n"
)

# generate/validate/parse를 흉내내는 파이썬 가짜 kordoc. 동작은 자기 옆의
# control.json으로 정한다(어댑터가 허용목록 환경만 넘기므로 환경변수는 못 쓴다).
# 받은 argv를 argv-<부명령>.json으로 남겨 image-dir 전달 여부를 시험한다.
FAKE_CLI = """\
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = json.loads((HERE / "control.json").read_text(encoding="utf-8"))
SECTION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    '<hp:p><hp:run><hp:secPr>'
    '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="2835" footer="2835" gutter="0" left="5670" right="4252" '
    'top="8504" bottom="4252"/></hp:pagePr></hp:secPr>'
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
    'sameSz="1" sameGap="0"/></hp:ctrl>'
    '<hp:t>창업영농계획서</hp:t></hp:run></hp:p></hs:sec>'
)
HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
    '<hh:fontfaces itemCnt="1">'
    '<hh:fontface lang="HANGUL" fontCnt="1">'
    '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/></hh:fontface>'
    '<hh:fontface lang="LATIN" fontCnt="1">'
    '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/></hh:fontface>'
    '</hh:fontfaces></hh:head>'
)


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
    tag = args[0] if args and args[0] in ("generate", "validate") else "parse"
    (HERE / ("argv-" + tag + ".json")).write_text(
        json.dumps(argv), encoding="utf-8")
    if args and args[0] == "generate":
        if not CONTROL.get("generate_ok", True):
            sys.stderr.write("fake generate failed")
            return 1
        out = args[args.index("-o") + 1]
        if CONTROL.get("generate_empty"):
            Path(out).write_bytes(b"")
            return 0
        write_hwpx(out)
        return 0
    if args and args[0] == "validate":
        if CONTROL.get("validate_no_output"):
            sys.stderr.write("validate unavailable")
            return 1
        print(json.dumps(CONTROL.get(
            "validate", {"ok": True, "issues": [], "entryCount": 4})))
        return 0
    if "-o" in args:
        out = args[args.index("-o") + 1]
        if CONTROL.get("parse_fails"):
            sys.stderr.write("fake parse failed")
            return 1
        Path(out).write_text(CONTROL.get("markdown", ""), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
"""

SECTION_SAMPLE = (
    '<hs:sec xmlns:hp="p"><hp:p><hp:run><hp:secPr>'
    '<hp:pagePr><hp:margin header="3600" footer="3600" gutter="0" '
    'left="5102" right="5102" top="3685" bottom="3685"/></hp:pagePr>'
    '</hp:secPr>'
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
    'sameSz="1" sameGap="0"/></hp:ctrl>'
    '<hp:t>x</hp:t></hp:run></hp:p></hs:sec>'
)

HEADER_SAMPLE = (
    '<hh:head><hh:fontfaces itemCnt="1">'
    '<hh:fontface lang="HANGUL" fontCnt="2">'
    '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/>'
    '<hh:font id="1" face="HY견고딕" type="TTF" isEmbedded="0"/></hh:fontface>'
    '</hh:fontfaces></hh:head>'
)


def synthetic_hwpx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("Contents/header.xml", HEADER_SAMPLE)
        archive.writestr("Contents/section0.xml", SECTION_SAMPLE)
    return buffer.getvalue()


class PureTransformTests(unittest.TestCase):
    def test_mm_to_hwpunit_school_values(self):
        self.assertEqual(8504, gh.mm_to_hwpunit(30))
        self.assertEqual(5669, gh.mm_to_hwpunit(20))
        self.assertEqual(4252, gh.mm_to_hwpunit(15))
        self.assertEqual(0, gh.mm_to_hwpunit(0))

    def test_rewrite_margins_applies_school_spec(self):
        updated = gh.rewrite_margins(SECTION_SAMPLE)
        self.assertIn(
            '<hp:margin header="4252" footer="4252" gutter="0" '
            'left="8504" right="8504" top="5669" bottom="4252"/>', updated)
        self.assertNotIn('left="5102"', updated)
        self.assertEqual(1, updated.count("<hp:margin"))

    def test_rewrite_fonts_sets_school_font_keeps_container(self):
        updated = gh.rewrite_fonts(HEADER_SAMPLE)
        self.assertNotIn("함초롬바탕", updated)
        self.assertNotIn("HY견고딕", updated)
        self.assertEqual(2, updated.count('face="신명조"'))
        self.assertIn("<hh:fontfaces", updated)
        self.assertEqual(updated, gh.rewrite_fonts(updated))

    def test_inject_page_numbers_once(self):
        once = gh.inject_page_numbers(SECTION_SAMPLE)
        self.assertIn("<hp:pageNum", once)
        self.assertEqual(1, once.count("<hp:pageNum"))
        self.assertEqual(once, gh.inject_page_numbers(once))

    def test_inject_page_numbers_without_colpr_is_noop(self):
        self.assertEqual("<hs:sec/>", gh.inject_page_numbers("<hs:sec/>"))

    def test_normalize_markdown_strips_bom_and_normalizes_newlines(self):
        raw = b"\xef\xbb\xbfa\r\nb\rc"
        self.assertEqual("a\nb\nc", gh.normalize_markdown(raw))

    def test_table_numbers_keep_sign_decimal_and_order(self):
        self.assertEqual(["1", "2", "10000", "12345"],
                         gh.table_numbers(SAMPLE_MD))

    def test_compare_table_numbers_is_exact(self):
        # R1-04: 부호·소수점·순서를 버리면 이 반례들이 '일치'로 통과한다.
        self.assertTrue(gh.compare_table_numbers(SAMPLE_MD, SAMPLE_MD)["match"])
        self.assertFalse(gh.compare_table_numbers("| p | -100 |", "| p | 100 |")["match"])
        self.assertFalse(gh.compare_table_numbers("| r | 1.25 |", "| r | 25.1 |")["match"])
        self.assertFalse(
            gh.compare_table_numbers("| s | 100 | 200 |", "| s | 200 | 100 |")["match"])
        self.assertTrue(
            gh.compare_table_numbers("| s | 10,000 | -1.25 |",
                                     "| s | 10000 | -1.25 |")["match"])
        result = gh.compare_table_numbers("| 매출액 | 10,000 |\n", SAMPLE_MD)
        self.assertFalse(result["match"])
        self.assertEqual(0, result["index"])

    def test_image_references_and_resolution(self):
        with tempfile.TemporaryDirectory(prefix="hwpx-img-") as tmp:
            base = Path(tmp)
            (base / "picture.png").write_bytes(b"\x89PNG")
            text = "![그림](picture.png)\n\n![없음](nope.png)\n"
            self.assertEqual(["picture.png", "nope.png"],
                             gh.image_references(text))
            referenced, missing = gh.resolve_images(text, base)
            self.assertEqual(["picture.png", "nope.png"], referenced)
            self.assertEqual(["nope.png"], missing)
            _, escaped = gh.resolve_images("![밖](../outside.png)\n", base)
            self.assertEqual(["../outside.png"], escaped)

    def test_transform_only_touches_header_and_section(self):
        raw = synthetic_hwpx()
        rebuilt, applied = gh.transform_hwpx(raw)
        self.assertEqual(["Contents/section0.xml"], applied["margins"])
        self.assertEqual(["Contents/header.xml"], applied["font"])
        self.assertEqual(["Contents/section0.xml"], applied["page_numbers"])
        with zipfile.ZipFile(io.BytesIO(raw)) as before, \
                zipfile.ZipFile(io.BytesIO(rebuilt)) as after:
            self.assertEqual("mimetype", after.namelist()[0])
            self.assertEqual(zipfile.ZIP_STORED,
                             after.getinfo("mimetype").compress_type)
            for name in before.namelist():
                if name in {"Contents/header.xml", "Contents/section0.xml"}:
                    continue
                self.assertEqual(before.read(name), after.read(name), name)
            section = after.read("Contents/section0.xml").decode("utf-8")
            header = after.read("Contents/header.xml").decode("utf-8")
        self.assertIn('left="8504"', section)
        self.assertIn("<hp:pageNum", section)
        self.assertIn('face="신명조"', header)

    def test_transform_without_page_numbers(self):
        rebuilt, applied = gh.transform_hwpx(synthetic_hwpx(), page_numbers=False)
        self.assertEqual([], applied["page_numbers"])
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as archive:
            section = archive.read("Contents/section0.xml").decode("utf-8")
        self.assertNotIn("<hp:pageNum", section)

    def test_env_allowlist_withholds_secrets(self):
        with mock.patch.dict(os.environ, {"SECRET_TOKEN": "leak"}):
            env = gh._env_for("kordoc")
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertIn("PATH", env)

    def test_module_is_library_only_and_io_guarded(self):
        source = script_source("gg_hwpx.py")
        self.assertEqual([], text_calls_without_encoding(source))
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn('__main__', source)
        self.assertFalse(hasattr(gh, "main"))


class BuildTests(ContractCase):
    """발행 계약: build()는 정본 전공 권한(context+authorization) 없이는 발행하지 않는다."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.root = self.make_project()
        bind_major(self.root, "specialty_crops")
        self.context = self.mc.output_context(self.root, "specialty_crops")
        self.authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_PAPER, self.context)
        self.fake = self.root / "fake_kordoc.py"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.control = self.root / "control.json"
        self.cli = [sys.executable, str(self.fake)]
        self.md = self.root / "in.md"
        self.md.write_text(SAMPLE_MD, encoding="utf-8", newline="\n")

    def _control(self, **cfg):
        self.control.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def _argv(self, tag="generate"):
        return json.loads((self.root / ("argv-" + tag + ".json")).read_text(
            encoding="utf-8"))

    def _build(self, out=None, **kw):
        target = out or (self.root / "out" / "result.hwpx")
        kw.setdefault("context", self.context)
        kw.setdefault("authorization", self.authorization)
        kw.setdefault("cli_command", self.cli)
        return gh.build(self.md, target, **kw), target

    def _json_only_validate_failure_cli(self):
        """validate가 JSON ok:true를 출력하고도 종료 1인 가짜 CLI(R1-03 남은 분기)."""
        old = ('"validate", {"ok": True, "issues": [], "entryCount": 4})))\n'
               '        return 0')
        new = ('"validate", {"ok": True, "issues": [], "entryCount": 4})))\n'
               '        return 1')
        fake = FAKE_CLI.replace(old, new)
        if fake == FAKE_CLI:
            raise AssertionError("anchor not found")
        script = self.root / "fake_json_fail.py"
        script.write_text(fake, encoding="utf-8")
        return [sys.executable, str(script)]

    def test_build_produces_school_spec_hwpx(self):
        self._control(markdown=SAMPLE_MD)
        before = self.md.read_bytes()
        result, target = self._build()
        self.assertEqual("ready", result["status"])
        self.assertEqual(["Contents/section0.xml"], result["applied"]["margins"])
        self.assertEqual(["Contents/header.xml"], result["applied"]["font"])
        self.assertEqual(before, self.md.read_bytes())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                         result["output_sha256"])
        self.assertEqual("ok", result["verification"]["validate"]["status"])
        self.assertTrue(result["verification"]["reparse"]["ok"])
        self.assertTrue(result["verification"]["table_numbers"]["match"])
        with zipfile.ZipFile(target) as archive:
            section = archive.read("Contents/section0.xml").decode("utf-8")
            header = archive.read("Contents/header.xml").decode("utf-8")
        self.assertIn('left="8504" right="8504" top="5669" bottom="4252"', section)
        self.assertIn("<hp:pageNum", section)
        self.assertNotIn("함초롬바탕", header)
        self.assertIn('face="신명조"', header)

    def test_build_requires_authorization_arguments(self):
        self._control(markdown=SAMPLE_MD)
        target = self.root / "out" / "result.hwpx"
        with self.assertRaises(TypeError):
            gh.build(self.md, target, cli_command=self.cli)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            gh.build(self.md, target, cli_command=self.cli,
                     context=self.context, authorization=None)
        self.assertEqual("authorization_required", ctx.exception.reason)
        self.assertFalse(target.exists())

    def test_forged_authorization_cannot_publish_unbound(self):
        # R1-02: 프로젝트·전공이 없는 곳에 위조 권한 객체를 넘겨도 reconfirm이
        # 정본과 대조해 거절한다(아무것도 하지 않는 콜백 우회가 사라졌다).
        self._control(markdown=SAMPLE_MD)
        empty = self.root / "unbound"
        empty.mkdir()
        forged = self.mc.OutputAuthorization(
            output=self.mc.OUTPUT_SCHOOL_PAPER, major_id="specialty_crops",
            module_version="1.0.0",
            contract_version=self.mc.CONTRACT_VERSION, project_revision=0,
            binding_evidence="forged")
        target = self.root / "out" / "result.hwpx"
        with self.assertRaises(self.mc.OutputHeldError) as ctx:
            gh.build(self.md, target, cli_command=self.cli,
                     context=self.mc.output_context(empty, "specialty_crops"),
                     authorization=forged)
        self.assertEqual("project_revision_changed", ctx.exception.reason)
        self.assertFalse(target.exists())

    def test_build_rejects_wrong_output_authorization(self):
        # R1-02 남은 반례: 다른 출력 종류(엑셀)의 유효한 권한으로는 논문을 발행하지
        # 않는다. 원예환경시스템전공은 school_excel_workbook만 지원한다.
        self._control(markdown=SAMPLE_MD)
        other = self.make_project()
        bind_major(other, "hort_env_systems")
        other_context = self.mc.output_context(other, "hort_env_systems")
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.mc.authorize_output(self.mc.OUTPUT_SCHOOL_PAPER, other_context)
        self.assertEqual("unsupported_output", held.exception.reason)
        workbook = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK, other_context)
        self.assertEqual(self.mc.OUTPUT_SCHOOL_WORKBOOK, workbook.output)
        target = self.root / "out" / "result.hwpx"
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            gh.build(self.md, target, context=other_context,
                     authorization=workbook, cli_command=self.cli)
        self.assertEqual("authorization_required", ctx.exception.reason)
        self.assertEqual(self.mc.OUTPUT_SCHOOL_WORKBOOK,
                         ctx.exception.details["output"])
        self.assertFalse(target.exists())

    def test_build_refuses_existing_output(self):
        self._control(markdown=SAMPLE_MD)
        target = self.root / "out" / "result.hwpx"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"keep me")
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build(out=target)
        self.assertEqual("output_exists", ctx.exception.reason)
        self.assertEqual(b"keep me", target.read_bytes())

    def test_build_rejects_same_input_and_output(self):
        self._control(markdown=SAMPLE_MD)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            gh.build(self.md, self.md, context=self.context,
                     authorization=self.authorization, cli_command=self.cli)
        self.assertEqual("input_output_same", ctx.exception.reason)

    def test_build_blocked_when_generate_fails(self):
        self._control(generate_ok=False)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("generate_failed", ctx.exception.reason)

    def test_build_blocked_when_generate_is_empty(self):
        self._control(generate_empty=True)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("empty_output", ctx.exception.reason)

    def test_verify_blocks_when_validate_failed(self):
        self._control(markdown=SAMPLE_MD,
                      validate={"ok": False, "issues": [{"message": "bad"}]})
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("validate_failed", ctx.exception.reason)
        self.assertFalse((self.root / "out" / "result.hwpx").exists())

    def test_verify_blocks_when_validate_unavailable(self):
        # R1-03: validate 부명령 실행이 실패(JSON 없음)해도 통과로 넘기지 않는다.
        self._control(markdown=SAMPLE_MD, validate_no_output=True)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("validate_failed", ctx.exception.reason)
        self.assertEqual("unsupported", ctx.exception.details["status"])
        self.assertFalse((self.root / "out" / "result.hwpx").exists())

    def test_verify_blocks_when_validate_process_fails(self):
        # R1-03 남은 분기: JSON ok:true를 출력해도 프로세스가 실패하면 불통과.
        self._control(markdown=SAMPLE_MD)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build(cli_command=self._json_only_validate_failure_cli())
        self.assertEqual("validate_failed", ctx.exception.reason)
        self.assertIs(False, ctx.exception.details["process_ok"])
        self.assertEqual(1, ctx.exception.details["returncode"])
        self.assertFalse((self.root / "out" / "result.hwpx").exists())

    def test_verify_blocks_when_reparse_fails(self):
        self._control(markdown=SAMPLE_MD, parse_fails=True)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("reparse_failed", ctx.exception.reason)
        self.assertFalse((self.root / "out" / "result.hwpx").exists())

    def test_build_blocked_on_table_number_mismatch(self):
        self._control(markdown="| 매출액 | 10,000 |\n")
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            self._build()
        self.assertEqual("table_numbers_mismatch", ctx.exception.reason)
        self.assertEqual(0, ctx.exception.details["index"])

    def test_input_change_during_verify_blocks(self):
        # R1-06: 검사 중 원문이 바뀌면 발행 직전 재확인에서 거절한다.
        self._control(markdown=SAMPLE_MD)
        target = self.root / "out" / "result.hwpx"
        real = gh.reparse_hwpx

        def reparse_then_change(*args, **kw):
            result = real(*args, **kw)
            self.md.write_text("CHANGED AFTER GENERATION", encoding="utf-8")
            return result

        with mock.patch.object(gh, "reparse_hwpx", reparse_then_change):
            with self.assertRaises(gh.HwpxBlocked) as ctx:
                gh.build(self.md, target, context=self.context,
                         authorization=self.authorization, cli_command=self.cli)
        self.assertEqual("input_changed", ctx.exception.reason)
        self.assertFalse(target.exists())

    def test_publish_race_reports_output_publish_failed(self):
        # R1-07: 경합으로 대상이 나타나면 reason 중복 없이 계약대로 거절한다.
        self._control(markdown=SAMPLE_MD)
        target = self.root / "out" / "result.hwpx"

        def other_writer():
            target.write_bytes(b"OTHER WRITER")

        with self.assertRaises(gh.HwpxBlocked) as ctx:
            gh.build(self.md, target, context=self.context,
                     authorization=self.authorization, cli_command=self.cli,
                     before_publish=other_writer)
        self.assertEqual("output_publish_failed", ctx.exception.reason)
        self.assertEqual(b"OTHER WRITER", target.read_bytes())

    def test_build_without_page_numbers(self):
        self._control(markdown=SAMPLE_MD)
        result, target = self._build(page_numbers=False)
        self.assertEqual([], result["applied"]["page_numbers"])
        with zipfile.ZipFile(target) as archive:
            self.assertNotIn("<hp:pageNum",
                             archive.read("Contents/section0.xml").decode("utf-8"))

    def test_build_skip_verify_reports_no_verification(self):
        self._control(generate_ok=True)
        result, _ = self._build(verify=False)
        self.assertEqual("ready", result["status"])
        self.assertIsNone(result["verification"]["validate"])
        self.assertIsNone(result["verification"]["table_numbers"])

    def test_image_dir_defaults_to_source_folder(self):
        # R1-05: 기본 호출도 원문 폴더를 --image-dir로 넘겨 상대 경로 그림을 보존한다.
        folder = self.root / "src"
        folder.mkdir()
        (folder / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        md = folder / "in.md"
        md.write_text("![그림](picture.png)\n", encoding="utf-8")
        self._control(markdown="")
        target = self.root / "img.hwpx"
        gh.build(md, target, context=self.context,
                 authorization=self.authorization, cli_command=self.cli)
        argv = self._argv("generate")
        self.assertIn("--image-dir", argv)
        self.assertEqual(str(folder.resolve()),
                         argv[argv.index("--image-dir") + 1])

    def test_missing_image_blocks_before_generate(self):
        # R1-05: 해결되지 않는 그림은 조용히 1×1로 만들지 않고 거절한다.
        self._control(markdown=SAMPLE_MD)
        md = self.root / "with-image.md"
        md.write_text("![그림](nope.png)\n", encoding="utf-8")
        target = self.root / "img.hwpx"
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            gh.build(md, target, context=self.context,
                     authorization=self.authorization, cli_command=self.cli)
        self.assertEqual("image_unresolved", ctx.exception.reason)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
