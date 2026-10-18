"""build_hwpx CLI 계약 시험 — 정책 B 전공 권한·무덮어쓰기·매니페스트.

가짜 CLI(파이썬 스크립트를 sys.executable로 실행, 셸 스크립트 아님)만 써서
kordoc 없이 돈다. skip은 두지 않는다 — 실물 kordoc end-to-end와 DOCX/HWPX 표 수치
동등성은 일회성 수동 검증으로 대체했다(RESULT-W1b/W1c 참조).
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile
from unittest import mock

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, runtime, script_source,
    text_calls_without_encoding,
)

build_hwpx = runtime("build_hwpx")
gh = runtime("gg_hwpx")

SPECIALTY = "specialty_crops"
INSECTS = "industrial_insects"
UNREGISTERED = "unregistered_probe_major"

BODY_MD = (
    "Ⅰ. 머리말\n"
    "\n"
    "본문 첫 문단이다.\n"
    "\n"
    "표 1. 연도별 손익 (단위: 천원)\n"
    "\n"
    "| 구분 | 1년차 | 2년차 |\n"
    "| --- | ---: | ---: |\n"
    "| 매출액 | 10,000 | 12,345 |\n"
    "| 소득 | 4,000 | 5,245 |\n"
)

SCHOOL_MD = (
    "겉표지\n농업전문학사 학위논문\n영농창업계획\n합성 논문 제목\n2026년 2월\n"
    "한국농수산대학교\n특용작물전공\n홍길동\n"
    "표제면\n지도교수 김교수\n농업전문학사 학위논문\n영농창업계획\n합성 논문 제목\n"
    "이 논문을 농업전문학사 학위논문으로 제출함\n2026년 1월\n한국농수산대학교\n"
    "특용작물전공\n홍길동\n"
    "제출서\n"
    "인준서\n위 내용을 심사하여 인준함\n2026년 2월\n위원장 김위원 (인)\n"
    "위원 이위원 (인)\n"
    "목차\n요약\n합성 요약 본문이다.\n감사의 글\n"
    + BODY_MD
)

FAKE_CLI = """\
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = json.loads((HERE / "control.json").read_text(encoding="utf-8"))
SECTION = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run>'
    '<hp:secPr><hp:pagePr landscape="WIDELY" width="59528" height="84188" '
    'gutterType="LEFT_ONLY"><hp:margin header="2835" footer="2835" gutter="0" '
    'left="5670" right="4252" top="8504" bottom="4252"/></hp:pagePr></hp:secPr>'
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
    'sameSz="1" sameGap="0"/></hp:ctrl><hp:t>본문</hp:t></hp:run></hp:p></hs:sec>'
)
HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
    '<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="1">'
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
        write_hwpx(args[args.index("-o") + 1])
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


def make_fake_cli(root, **control):
    work = Path(root) / "fake"
    work.mkdir(exist_ok=True)
    script = work / "fake_kordoc.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    (work / "control.json").write_text(
        json.dumps(control, ensure_ascii=False), encoding="utf-8")
    return [sys.executable, str(script)]


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "build_hwpx.py"), *map(str, args)],
        capture_output=True, text=True)


class _Base(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.core = runtime("gg_core")

    def _project(self, major=SPECIALTY):
        root = self.make_project()
        if major is not None:
            bind_major(root, major)
        return root

    def _ctx(self, root, major=None):
        return self.mc.output_context(root, major)

    def _rebind(self, root, major):
        fact = dict(self.core.load(root)["facts"]["selected_major"])
        fact.update(value=major,
                    module_version=self.mc.INDUSTRIAL_INSECTS_MODULE.module_version)
        self.core.apply(root, {"request_id": "swap-" + major,
                               "ops": [{"collection": "facts", "value": fact}]},
                        self.core.load(root)["revision"])

    def _held(self, reason, fn, *args, **kw):
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            fn(*args, **kw)
        self.assertEqual(reason, caught.exception.reason, caught.exception.detail)
        return caught.exception


class GuardTests(_Base):
    """정책 B: 보류는 발행 전에 끝나고 아무 파일도 남기지 않는다."""

    def test_export_holds_before_any_output(self):
        insect, bound, unbound = (self._project(INSECTS), self._project(),
                                  self._project(None))
        cases = (
            ("t1-insect", insect, INSECTS, "unsupported_output"),
            ("t1-unregistered", bound, UNREGISTERED, "unknown_major"),
            ("t2-no-id", bound, None, "major_id_required"),
            ("t2-unbound", unbound, SPECIALTY, "major_binding_required"),
        )
        for label, root, major, reason in cases:
            with self.subTest(case=label):
                (Path(root) / "body.md").write_text(BODY_MD, encoding="utf-8")
                before = self._tree_bytes(root)
                self._held(reason, build_hwpx.export, root, "body.md",
                           "out.hwpx", context=self._ctx(root, major))
                self.assertEqual(before, self._tree_bytes(root))
                self.assertFalse((Path(root) / "out.hwpx").exists())

    def test_export_requires_context(self):
        root = self._project()
        (Path(root) / "body.md").write_text(BODY_MD, encoding="utf-8")
        self._held("output_context_required", build_hwpx.export, root,
                   "body.md", "out.hwpx")

    def test_forged_allowed_flag_is_ignored(self):
        # context 매핑에 실린 allowed=True는 읽히지 않는다 — 판정은 정본에서
        # 다시 계산된다.
        root = self._project()
        (Path(root) / "body.md").write_text(BODY_MD, encoding="utf-8")
        cli = make_fake_cli(root, markdown=BODY_MD)
        value = build_hwpx.export(
            root, "body.md", "out.hwpx", cli_command=cli,
            context={"project_root": str(root), "major_id": SPECIALTY,
                     "allowed": True})
        self.assertEqual(SPECIALTY,
                         value["manifest"]["major_authorization"]["major_id"])
        self.assertTrue((Path(root) / "out.hwpx").is_file())

    def test_revision_change_before_publish_blocks(self):
        root = self._project()
        (Path(root) / "body.md").write_text(BODY_MD, encoding="utf-8")
        cli = make_fake_cli(root, markdown=BODY_MD)
        real_build = gh.build

        def build_then_swap(*args, **kw):
            self._rebind(root, INSECTS)
            return real_build(*args, **kw)

        with mock.patch.object(gh, "build", build_then_swap):
            self._held("project_revision_changed", build_hwpx.export, root,
                       "body.md", "out.hwpx", cli_command=cli,
                       context=self._ctx(root, SPECIALTY))
        self.assertFalse((Path(root) / "out.hwpx").exists())
        self.assertFalse((Path(root) / "out.hwpx.manifest.json").exists())


class ExportTests(_Base):
    def setUp(self):
        super().setUp()
        self.root = self._project()
        (Path(self.root) / "body.md").write_text(BODY_MD, encoding="utf-8")

    def test_export_publishes_hwpx_and_manifest(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        value = build_hwpx.export(self.root, "body.md", "build/논문.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        target = Path(self.root) / "build" / "논문.hwpx"
        manifest_path = Path(self.root) / "build" / "논문.hwpx.manifest.json"
        self.assertTrue(target.is_file())
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(str(target.resolve()), value["output"])
        self.assertEqual(str(manifest_path.resolve()), value["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("draft", manifest["kind"])
        self.assertEqual(self.core.digest(target.read_bytes()),
                         manifest["file_hash"])
        self.assertEqual(
            self.core.digest((Path(self.root) / "body.md").read_bytes()),
            manifest["input_hash"])
        self.assertEqual(gh.SCHOOL_FONT, manifest["font"])
        for key in ("font_available", "render", "hwp"):
            self.assertEqual("not_run", manifest[key])
        self.assertEqual(SPECIALTY, manifest["major_authorization"]["major_id"])
        self.assertTrue(manifest["hwpx"]["verify"])
        self.assertEqual(["Contents/section0.xml"],
                         manifest["hwpx"]["post_processing"]["margins"])
        self.assertEqual(["Contents/header.xml"],
                         manifest["hwpx"]["post_processing"]["font"])
        self.assertTrue(manifest["hwpx"]["page_numbers"])
        self.assertEqual("ok", manifest["hwpx"]["validate"]["status"])
        self.assertTrue(manifest["hwpx"]["reparse"]["ok"])
        self.assertTrue(manifest["hwpx"]["table_numbers"]["match"])
        self.assertEqual(str(Path(self.root).resolve()),
                         manifest["hwpx"]["images"]["dir"])
        self.assertIn("표 수치 대조를 통과했다", manifest["notice"])
        self.assertIn("한글", manifest["notice"])
        with zipfile.ZipFile(target) as archive:
            section = archive.read("Contents/section0.xml").decode("utf-8")
            header = archive.read("Contents/header.xml").decode("utf-8")
        self.assertIn('left="8504" right="8504" top="5669" bottom="4252"', section)
        self.assertIn("<hp:pageNum", section)
        self.assertIn('face="신명조"', header)

    def test_export_preserves_sibling_docx_receipt(self):
        # R1-01: 같은 basename의 DOCX 영수증(out.manifest.json)은 건드리지 않는다.
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        receipt = Path(self.root) / "out.manifest.json"
        receipt.write_text("ORIGINAL DOCX RECEIPT", encoding="utf-8")
        build_hwpx.export(self.root, "body.md", "out.hwpx", cli_command=cli,
                          context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("ORIGINAL DOCX RECEIPT",
                         receipt.read_text(encoding="utf-8"))
        self.assertTrue((Path(self.root) / "out.hwpx.manifest.json").is_file())

    def test_export_does_not_overwrite_manifest_named_input(self):
        # R1-01 강한 반례: 입력 이름이 매니페스트처럼 끝나도 본문은 보존된다.
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        source = Path(self.root) / "input.manifest.json"
        source.write_text(BODY_MD, encoding="utf-8")
        before = source.read_bytes()
        build_hwpx.export(self.root, "input.manifest.json", "input.hwpx",
                          cli_command=cli,
                          context=self._ctx(self.root, SPECIALTY))
        self.assertEqual(before, source.read_bytes())
        self.assertTrue((Path(self.root) / "input.hwpx.manifest.json").is_file())

    def test_export_refuses_existing_manifest(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        manifest_path = Path(self.root) / "out.hwpx.manifest.json"
        manifest_path.write_text("OLD", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("OLD", manifest_path.read_text(encoding="utf-8"))
        self.assertFalse((Path(self.root) / "out.hwpx").exists())

    def test_export_refuses_existing_output(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        target = Path(self.root) / "out.hwpx"
        target.write_bytes(b"keep me")
        with self.assertRaises(ValueError):
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))
        self.assertEqual(b"keep me", target.read_bytes())

    def test_export_missing_input(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        (Path(self.root) / "body.md").unlink()
        with self.assertRaises(ValueError):
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))

    def test_export_path_outside_base_rejected(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        with self.assertRaises(ValueError):
            build_hwpx.export(self.root, "../escaped.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))

    def test_export_blocked_when_kordoc_generate_fails(self):
        cli = make_fake_cli(self.root, generate_ok=False)
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("generate_failed", ctx.exception.reason)
        self.assertFalse((Path(self.root) / "out.hwpx").exists())
        self.assertFalse((Path(self.root) / "out.hwpx.manifest.json").exists())

    def test_export_blocks_when_verify_fails(self):
        # R1-03: 검증 실패면 본체·매니페스트 모두 발행하지 않는다.
        cli = make_fake_cli(self.root, markdown=BODY_MD,
                            validate={"ok": False, "issues": [{"message": "bad"}]})
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=cli,
                              context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("validate_failed", ctx.exception.reason)
        self.assertFalse((Path(self.root) / "out.hwpx").exists())
        self.assertFalse((Path(self.root) / "out.hwpx.manifest.json").exists())

    def test_export_blocks_when_validate_process_fails(self):
        # R1-03 남은 분기: validate가 JSON ok:true를 출력하고 종료 1이어도 발행하지 않는다.
        old = ('"validate", {"ok": True, "issues": [], "entryCount": 4})))\n'
               '        return 0')
        new = ('"validate", {"ok": True, "issues": [], "entryCount": 4})))\n'
               '        return 1')
        fake = FAKE_CLI.replace(old, new)
        self.assertNotEqual(FAKE_CLI, fake)
        script = Path(self.root) / "fake_json_fail.py"
        script.write_text(fake, encoding="utf-8")
        (Path(self.root) / "control.json").write_text(
            json.dumps({"markdown": BODY_MD}), encoding="utf-8")
        with self.assertRaises(gh.HwpxBlocked) as ctx:
            build_hwpx.export(self.root, "body.md", "out.hwpx",
                              cli_command=[sys.executable, str(script)],
                              context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("validate_failed", ctx.exception.reason)
        self.assertFalse((Path(self.root) / "out.hwpx").exists())
        self.assertFalse((Path(self.root) / "out.hwpx.manifest.json").exists())

    def test_notice_reports_skipped_verification(self):
        # R1-03: --no-verify면 실행하지 않은 검사를 통과했다고 쓰지 않는다.
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        value = build_hwpx.export(self.root, "body.md", "out.hwpx",
                                  cli_command=cli, verify=False,
                                  context=self._ctx(self.root, SPECIALTY))
        notice = value["manifest"]["notice"]
        self.assertIn("검증을 생략했다", notice)
        self.assertNotIn("통과했다", notice)
        self.assertFalse(value["manifest"]["hwpx"]["verify"])

    def test_input_change_during_verify_blocks_export(self):
        # R1-06: 검사 중 원문이 바뀌면 옛 출력에 새 해시를 붙이지 않고 거절한다.
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        real = gh.reparse_hwpx

        def reparse_then_change(*args, **kw):
            result = real(*args, **kw)
            (Path(self.root) / "body.md").write_text(
                "CHANGED AFTER GENERATION", encoding="utf-8")
            return result

        with mock.patch.object(gh, "reparse_hwpx", reparse_then_change):
            with self.assertRaises(gh.HwpxBlocked) as ctx:
                build_hwpx.export(self.root, "body.md", "out.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        self.assertEqual("input_changed", ctx.exception.reason)
        self.assertFalse((Path(self.root) / "out.hwpx").exists())
        self.assertFalse((Path(self.root) / "out.hwpx.manifest.json").exists())

    def test_manifest_input_hash_is_generation_snapshot(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        source = Path(self.root) / "body.md"
        snapshot = self.core.digest(source.read_bytes())
        value = build_hwpx.export(self.root, "body.md", "out.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        self.assertEqual(snapshot, value["manifest"]["input_hash"])
        self.assertEqual(snapshot, self.core.digest(source.read_bytes()))

    def test_frontmatter_summary_flags_human_finish(self):
        (Path(self.root) / "school.md").write_text(SCHOOL_MD, encoding="utf-8")
        cli = make_fake_cli(self.root, markdown=SCHOOL_MD)
        value = build_hwpx.export(self.root, "school.md", "school.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        summary = value["manifest"]["school_frontmatter"]
        self.assertTrue(summary["detected"])
        self.assertEqual("plain_markdown", summary["layout"])
        self.assertTrue(summary["sections"])
        self.assertTrue(summary["human_finish_required"])

    def test_frontmatter_summary_absent_for_plain_body(self):
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        value = build_hwpx.export(self.root, "body.md", "out.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        self.assertIsNone(value["manifest"]["school_frontmatter"])


class CliTests(_Base):
    def setUp(self):
        super().setUp()
        self.root = self._project()
        (Path(self.root) / "body.md").write_text(BODY_MD, encoding="utf-8")

    def test_cli_held_is_json_exit_2_without_output(self):
        before = self._tree_bytes(self.root)
        result = _cli(self.root, "--in", "body.md", "--out", "out.hwpx",
                      "--major", UNREGISTERED)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("held", json.loads(result.stdout)["status"])
        self.assertEqual("unknown_major", json.loads(result.stdout)["reason"])
        self.assertEqual(before, self._tree_bytes(self.root))
        self.assertFalse((Path(self.root) / "out.hwpx").exists())

    def test_cli_requires_major(self):
        result = _cli(self.root, "--in", "body.md", "--out", "out.hwpx")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("major_id_required", json.loads(result.stdout)["reason"])


class ConventionTests(unittest.TestCase):
    def test_text_io_and_execution_guards(self):
        source = script_source("build_hwpx.py")
        self.assertEqual([], text_calls_without_encoding(source))
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)

    def test_defaults_match_build_docx(self):
        self.assertEqual("build/본문_통합.md", build_hwpx.DEFAULT_IN)
        self.assertEqual("build/검토전_논문.hwpx", build_hwpx.DEFAULT_OUT)
        self.assertEqual(gh.SCHOOL_FONT, build_hwpx.FONT)


if __name__ == "__main__":
    unittest.main()
