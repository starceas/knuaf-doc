"""build_hwpx CLI 계약 시험 — 정책 B 전공 권한·무덮어쓰기·매니페스트.

가짜 CLI(파이썬 스크립트를 sys.executable로 실행, 셸 스크립트 아님)만 써서
kordoc 없이 돈다. skip은 두지 않는다 — 실물 kordoc end-to-end와 DOCX/HWPX 표 수치
동등성은 일회성 수동 검증으로 대체했다(RESULT-W1b/W1c 참조).
"""

import io
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, claim_of, runtime, script_source,
    source_op, text_calls_without_encoding, write_text,
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
SECTION = CONTROL.get("section") or (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run>'
    '<hp:secPr><hp:pagePr landscape="WIDELY" width="59528" height="84188" '
    'gutterType="LEFT_ONLY"><hp:margin header="2835" footer="2835" gutter="0" '
    'left="5670" right="4252" top="8504" bottom="4252"/></hp:pagePr></hp:secPr>'
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
    'sameSz="1" sameGap="0"/></hp:ctrl><hp:t>본문</hp:t></hp:run></hp:p></hs:sec>'
)
HEADER = CONTROL.get("header") or (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
    '<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="1">'
    '<hh:font id="0" face="함초롬바탕" type="TTF" isEmbedded="0"/></hh:fontface>'
    '</hh:fontfaces></hh:head>'
)
HPF = CONTROL.get("hpf")


def write_hwpx(path):
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        if HPF is not None:
            archive.writestr("Contents/content.hpf", HPF)
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


def _normalized_manifest(value, root):
    """실행 환경마다 달라지는 경로·인터프리터를 기준 dict의 자리표시자로 바꾼다."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    root = Path(root)
    paths = {str(root), str(root.resolve())}
    # macOS의 /var → /private/var 심볼릭 링크처럼 두 표기가 섞여도 같게 본다.
    for raw in sorted(paths, key=len, reverse=True):
        text = text.replace(json.dumps(raw)[1:-1], "<ROOT>")
    return json.loads(text.replace(json.dumps(sys.executable)[1:-1], "<PYTHON>"))


# 5488bd2(식량작물 모듈 이전) 트리에서 `BODY_MD`·`make_fake_cli`·specialty_crops로
# 뽑은 HWPX 매니페스트. 경로·인터프리터는 실행 환경에 맞춰 치환해 비교한다.
REFERENCE_HWPX_MANIFEST = {
    "font": "신명조",
    "font_available": "not_run",
    "hwp": "not_run",
    "hwpx": {
        "images": {"dir": "<ROOT>", "referenced": []},
        "kordoc": {
            "cli_command": ["<PYTHON>", "<ROOT>/fake/fake_kordoc.py"],
            "source": "injected",
            "version": None,
        },
        "layout": {"body_width": 42520, "chapter_breaks": [],
                   "previous_widths": [], "tables": 0},
        "margins_mm": {"bottom": 15, "footer": 15, "gutter": 0, "header": 15,
                       "left": 30, "right": 30, "top": 20},
        "page_numbers": True,
        "post_processing": {
            "font": ["Contents/header.xml"],
            "margins": ["Contents/section0.xml"],
            "page_numbers": ["Contents/section0.xml"],
        },
        "reparse": {"ok": True},
        "table_numbers": {"count": 6, "match": True},
        "validate": {"entry_count": 4, "issues": [], "process_ok": True,
                     "returncode": 0, "status": "ok"},
        "verify": True,
    },
    "input_hash": "fb8a210f0beec11404b6e3fbcbf3e5d031c9d3c87cf183a54a4a020b1116b393",
    "kind": "draft",
    "major_authorization": {
        "binding_evidence": "facts:selected_major",
        "contract_version": "knuaf-major-contract/1",
        "major_id": "specialty_crops",
        "module_version": "1.0.0",
        "output": "school_paper",
        "project_revision": 1,
    },
    "notice": "저수준 미검증 출력. kordoc validate(구조)와 HWPX 재파싱 표 수치 대조를 통과했다. 한글 프로그램에서 열어 (1) 글꼴 신명조 (2) 여백 위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm (3) 쪽번호를 확인한다. 표지·제출면·인준서의 쪽 나눔·가운데 정렬과 목차의 실제 쪽번호는 사람이 마무리한다. 이 항목들은 자동 통과하지 않는다.",
    "render": "not_run",
    "school_frontmatter": None,
}


# --- 식량작물 3구역 fixture ------------------------------------------------
# `gg_document.parse`가 50개 노드로 읽고 앞머리 표지 7개가 모두 계획에 잡히는
# 원고. HWPX fixture는 이 문단 목록을 그대로 옮겨 노드↔문단 색인을 맞춘다.

FOOD_CROPS_PARAGRAPHS = (
    "겉표지", "농업전문학사 학위논문", "영농창업계획", "합성 논문 제목",
    "2027년 2월", "한국농수산대학교", "식량작물학과", "가상학생",
    "표제면", "지도교수 가상교수", "농업전문학사 학위논문", "영농창업계획",
    "합성 논문 제목", "2027년 2월", "한국농수산대학교", "식량작물학과", "가상학생",
    "제출서", "학위논문", "영농창업계획", "합성 논문 제목",
    "이 논문을 농업전문학사 학위논문으로 제출함", "2026년 12월",
    "한국농수산대학교", "식량작물학과", "가상학생",
    "인준서", "가상학생의 농업전문학사 학위논문을 인준함", "2026년 12월",
    "위원장 가상위원 (인)", "위 원 가상위원 (인)",
    "요약", "1. 농장 개요", "합성 요약 내용이다.",
    "감사의 글", "합성 감사 내용이다.",
    "목차", "표 목차", "그림 목차",
    "1. 머리말", "합성 본문이다.",
    "2. 영농환경 분석", "합성 본문이다.",
    "맺음말 및 발전방향", "합성 맺음말이다.",
    "참고문헌", "가상 문헌",
    "학부모 동의서", "합성 동의 내용이다.",
    "※ 단, 합성 주석이다.",
)

FOOD_CROPS_MD = "\n\n".join(FOOD_CROPS_PARAGRAPHS) + "\n"

FOOD_CROPS_SECPR = (
    '<hp:secPr><hp:pagePr landscape="WIDELY" width="59528" height="84188"'
    ' gutterType="LEFT_ONLY"><hp:margin header="2835" footer="2835" gutter="0"'
    ' left="5670" right="4252" top="8504" bottom="4252"/></hp:pagePr></hp:secPr>'
)
FOOD_CROPS_COLPR = (
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1"'
    ' sameSz="1" sameGap="0"/></hp:ctrl>'
)

FOOD_CROPS_HPF = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/"'
    ' xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf/">'
    '<opf:manifest>'
    '<opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
    '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
    '</opf:manifest>'
    '<opf:spine>'
    '<opf:itemref idref="header" linear="no"/>'
    '<opf:itemref idref="section0" linear="yes"/>'
    '</opf:spine></opf:package>'
)

FOOD_CROPS_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" secCnt="1">'
    '<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="1">'
    '<hh:font id="0" face="' + gh.SCHOOL_FONT + '" type="TTF" isEmbedded="0"/>'
    '</hh:fontface></hh:fontfaces>'
    '<hh:charProperties itemCnt="1">'
    '<hh:charPr id="0" height="1000" textColor="#000000" shadeColor="none"'
    ' useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">'
    '<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0"'
    ' symbol="0" user="0"/></hh:charPr>'
    '</hh:charProperties></hh:head>'
)


def hwpx_para(text, controls=""):
    return ('<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
            + controls + '<hp:t>' + text + '</hp:t></hp:run></hp:p>')


def hwpx_section(paragraphs, *, extra=None):
    """최상위 `<hp:p>` 목록을 hs/hp 두 접두사를 선언한 `<hs:sec>`로 감싼다."""
    extra = extra or {}
    body = "".join(
        hwpx_para(text, FOOD_CROPS_SECPR + FOOD_CROPS_COLPR
                  if index == 0 else extra.get(index, ""))
        for index, text in enumerate(paragraphs))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
            ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            + body + '</hs:sec>')


def food_crops_zip(section_xml, *, header=FOOD_CROPS_HEADER, hpf=FOOD_CROPS_HPF):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        if hpf is not None:
            archive.writestr("Contents/content.hpf", hpf)
        archive.writestr("Contents/header.xml", header)
        archive.writestr("Contents/section0.xml", section_xml)
    return buffer.getvalue()


def food_crops_plan(markdown=FOOD_CROPS_MD):
    gd = runtime("gg_document")
    nodes = gd.parse(markdown, major_id="food_crops")
    return nodes, gd.school_frontmatter_plan(nodes, "food_crops")


def read_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info.filename)
                for info in archive.infolist()}


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
        capture_output=True, text=True, encoding="utf-8")


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
        write_text(root, "changed-major.txt", "전공 변경: " + major + "\n")
        fact["source_refs"] = [{
            "id": "changed-major", "revision": 1,
            "locator": "line 1", "claim_id": "major"}]
        src = source_op("changed-major.txt", claims=claim_of(fact, "major"))
        src["value"]["id"] = "changed-major"
        self.core.apply(root, {"request_id": "swap-" + major,
                               "ops": [src, {"collection": "facts", "value": fact}]},
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

    def test_other_major_manifest_matches_the_reference_dict(self):
        # C-01: 식량작물 전용 키(`charpr`)가 다른 전공 매니페스트에 새어
        # 들어가면 안 된다. 5488bd2(식량작물 모듈 이전)에서 같은 입력·같은
        # 가짜 CLI로 뽑은 매니페스트 전체를 기준으로 둔다. 파일 해시는 ZIP
        # 타임스탬프 때문에 실행마다 달라지므로 뺀다.
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        value = build_hwpx.export(self.root, "body.md", "out.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(self.root, SPECIALTY))
        manifest = dict(value["manifest"])
        manifest.pop("file_hash")
        self.assertEqual(REFERENCE_HWPX_MANIFEST,
                         _normalized_manifest(manifest, self.root))

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


class OtherMajorHwpxByteTests(_Base):
    """R-N01: 다른 전공 4개의 HWPX 멤버 바이트가 `5488bd2`와 같다.

    기준은 `5488bd2`(식량작물 모듈 이전) 트리에서 `BODY_MD`·`make_fake_cli`·
    각 전공으로 뽑은 산출물의 압축 해제 바이트다. ZIP 시각은 실행마다
    달라지므로 멤버 단위로 비교한다. 4전공이 모두 같은 해시를 내므로 기준
    dict 하나를 공유한다.
    """

    MEMBERS = {
        'mimetype': '5ab5230b5af04f78947736bf8dd1a269aa684e234f7f4a392efb85289d1655c2',
        'META-INF/container.xml': '1feb165533a201fbe460cf9fdbe756b271e3d4aad056d52049c57da7efd7f0de',
        'Contents/header.xml': 'e2656e9c06d85ce67f04b71cdef8548afeff76323b1fe301bcb68df4e58c3fa4',
        'Contents/section0.xml': '3ba717e5fa8f7f782379b6ed3eabeffc87c55e2ff66826e78fe8ccced7e574c0',
    }

    def test_other_majors_reproduce_the_reference_bytes(self):
        import hashlib
        for major in ("specialty_crops", "fruit_trees", "hort_env_systems",
                      "dairy"):
            with self.subTest(major=major):
                root = self._project(major)
                (Path(root) / "body.md").write_text(BODY_MD, encoding="utf-8")
                cli = make_fake_cli(root, markdown=BODY_MD)
                build_hwpx.export(root, "body.md", "out.hwpx",
                                  cli_command=cli,
                                  context=self._ctx(root, major))
                with zipfile.ZipFile(Path(root) / "out.hwpx") as archive:
                    digests = {info.filename:
                               hashlib.sha256(archive.read(info.filename)).hexdigest()
                               for info in archive.infolist()}
                self.assertEqual(sorted(self.MEMBERS), sorted(digests))
                self.assertEqual(self.MEMBERS, digests)


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


_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HH = "http://www.hancom.co.kr/hwpml/2011/head"
_OPF = "http://www.idpf.org/2007/opf/"


def paragraphs_of(xml):
    """최상위 `<hp:p>` 요소 목록(ElementTree)."""
    return [node for node in ET.fromstring(xml) if node.tag == "{%s}p" % _HP]


def ctrl_items(paragraph, name):
    """``<hp:ctrl>`` 바로 아래의 ``<hp:name>`` 요소 목록."""
    return [child for ctrl in paragraph.iter("{%s}ctrl" % _HP)
            for child in ctrl if child.tag == "{%s}%s" % (_HP, name)]


def first_run(paragraph):
    return next(node for node in paragraph if node.tag == "{%s}run" % _HP)


def opf_nodes(xml, name):
    return [node for node in ET.fromstring(xml).iter()
            if node.tag == "{%s}%s" % (_OPF, name)]


def replace_paragraph(section_xml, index, paragraph):
    """`index`번째 최상위 문단을 다른 문자열로 바꾼다."""
    start, end = gh.top_level_paragraph_spans(section_xml)[index]
    return section_xml[:start] + paragraph + section_xml[end:]


def add_head_attrs(section_xml, index, attrs):
    """`index`번째 문단의 `<hp:p …>` 헤드에 속성 문자열을 덧붙인다."""
    start, end = gh.top_level_paragraph_spans(section_xml)[index]
    head = re.match(r"<hp:p\b[^>]*>", section_xml[start:end]).group(0)
    if head.endswith("/>"):
        new_head = head[:-2] + " " + attrs + "/>"
    else:
        new_head = head[:-1] + " " + attrs + ">"
    return section_xml[:start] + new_head + section_xml[start + len(head):]


def paragraph_head(xml, index):
    start, end = gh.top_level_paragraph_spans(xml)[index]
    return re.match(r"<hp:p\b[^>]*>", xml[start:end]).group(0)


_TEXT_ELEMENT_RE = re.compile(r"<hp:t\b[^>]*>.*?</hp:t>|<hp:t\b[^>]*/>", re.S)


def without_text(paragraph):
    """문단에서 `<hp:t>` 요소만 걷어낸 나머지 문자열."""
    return _TEXT_ELEMENT_RE.sub("", paragraph)


class _FoodCropsTests(_Base):
    """식량작물 3구역 분할·구역별 쪽번호·앞부분 글자 크기(설계 §4.1~§4.3)."""

    COVER_MARGIN = ('header="0" footer="2835" gutter="0" left="5669"'
                    ' right="5669" top="5669" bottom="2835"')
    FRONT_MARGIN = ('header="0" footer="2835" gutter="0" left="8504"'
                    ' right="8504" top="8504" bottom="8504"')

    def _transform(self, section_xml, *, markdown=FOOD_CROPS_MD,
                   header=FOOD_CROPS_HEADER, **kw):
        nodes, plan = food_crops_plan(markdown)
        return gh.transform_hwpx(
            food_crops_zip(section_xml, header=header),
            section_formats=gh.FOOD_CROPS_SECTION_FORMATS,
            nodes=nodes, plan=plan, **kw)

    def test_split_hwpx_sections_are_three_and_wellformed(self):
        data, applied = self._transform(
            hwpx_section(list(FOOD_CROPS_PARAGRAPHS)))
        self.assertEqual(["Contents/section%d.xml" % i for i in range(3)],
                         applied["sections"])
        members = read_zip(data)
        for index in range(3):
            xml = members["Contents/section%d.xml" % index].decode("utf-8")
            self.assertTrue(paragraphs_of(xml), index)
            expected = self.COVER_MARGIN if index == 0 else self.FRONT_MARGIN
            self.assertIn(expected, xml)
        header = members["Contents/header.xml"].decode("utf-8")
        self.assertIn('secCnt="3"', header)
        manifest = members["Contents/content.hpf"].decode("utf-8")
        items = {node.get("id"): node.get("href")
                 for node in opf_nodes(manifest, "item")}
        refs = [node.get("idref") for node in opf_nodes(manifest, "itemref")]
        self.assertEqual(["section0", "section1", "section2"],
                         [ref for ref in refs if ref.startswith("section")])
        for index in range(3):
            self.assertEqual("Contents/section%d.xml" % index,
                             items["section%d" % index])
            self.assertIn("Contents/section%d.xml" % index, members)
        for href in items.values():
            self.assertIn(href, members)

    def test_food_crops_page_number_formats_per_section(self):
        data, _ = self._transform(hwpx_section(list(FOOD_CROPS_PARAGRAPHS)))
        members = read_zip(data)
        sections = [paragraphs_of(
            members["Contents/section%d.xml" % index].decode("utf-8"))
            for index in range(3)]
        # 겉표지는 제어 요소를 넣지 않는다(pos NONE → 삽입 생략).
        self.assertEqual([], ctrl_items(sections[0][0], "pageNum"))
        self.assertEqual([], ctrl_items(sections[0][0], "newNum"))
        for index, fmt in ((1, "ROMAN_CAPITAL"), (2, "DIGIT")):
            first = sections[index][0]
            numbers = ctrl_items(first, "pageNum")
            self.assertEqual(1, len(numbers), index)
            self.assertEqual(fmt, numbers[0].get("formatType"))
            self.assertEqual("BOTTOM_CENTER", numbers[0].get("pos"))
            restarts = ctrl_items(first, "newNum")
            self.assertEqual(1, len(restarts), index)
            self.assertEqual("PAGE", restarts[0].get("numType"))
            self.assertEqual("1", restarts[0].get("num"))
            run = first_run(first)
            self.assertTrue([node for node in run.iter("{%s}newNum" % _HP)],
                            "newNum이 구역 첫 문단 첫 run 밖에 있다")
            xml = members["Contents/section%d.xml" % index].decode("utf-8")
            self.assertEqual(1, xml.count("<hp:pageNum"))
            self.assertEqual(1, xml.count("<hp:newNum"))

    def test_split_rejects_missing_or_duplicate_anchors(self):
        # 원고와 HWPX를 **같은** 결함으로 맞춰 둔다. 문단 수만 어긋나게 하면
        # node_map_mismatch가 먼저 걸려 정작 앵커 판정은 시험되지 않는다.
        summary_at = list(FOOD_CROPS_PARAGRAPHS).index("요약")

        missing = list(FOOD_CROPS_PARAGRAPHS)
        missing[summary_at] = "요약 아님"
        # `요약` 앵커가 원고·HWPX 양쪽에서 사라졌다: 앞머리 표지가 일부만 남은
        # 것으로 보여 분할 전에 거절해야 한다.
        with self.subTest(case="missing-summary"):
            markdown = "\n\n".join(missing) + "\n"
            nodes, plan = food_crops_plan(markdown)
            self.assertIsNone(plan)
            with self.assertRaises(gh.HwpxBlocked) as caught:
                gh.transform_hwpx(
                    food_crops_zip(hwpx_section(missing)),
                    section_formats=gh.FOOD_CROPS_SECTION_FORMATS,
                    nodes=nodes, plan=plan)
            self.assertEqual("section_split_front_matter_partial",
                             caught.exception.reason, caught.exception.details)
            self.assertIn("요약", caught.exception.details["missing"])

        # `요약` 앵커가 둘이다. 문단 수는 원고와 같으므로 노드 대조로는 잡히지
        # 않고, 첫 번째만 계획에 잡혀 둘째가 요약 본문으로 조용히 들어간다.
        duplicated = (list(FOOD_CROPS_PARAGRAPHS[:summary_at]) + ["요약"]
                      + list(FOOD_CROPS_PARAGRAPHS[summary_at:]))
        with self.subTest(case="duplicate-summary"):
            markdown = "\n\n".join(duplicated) + "\n"
            nodes, plan = food_crops_plan(markdown)
            self.assertIsNotNone(plan)
            with self.assertRaises(gh.HwpxBlocked) as caught:
                gh.transform_hwpx(
                    food_crops_zip(hwpx_section(duplicated)),
                    section_formats=gh.FOOD_CROPS_SECTION_FORMATS,
                    nodes=nodes, plan=plan)
            self.assertEqual("section_split_anchor_ambiguous",
                             caught.exception.reason, caught.exception.details)
            self.assertEqual("요약", caught.exception.details["anchor"])
            self.assertEqual(2, len(caught.exception.details["occurrences"]))

    def test_body_summary_paragraph_is_not_an_ambiguous_anchor(self):
        # N-I01: 중복 검사는 앞부분 구간만 본다. 본문에 독립 `요약` 문단이
        # 있어도 그것은 앞부분 앵커가 아니므로 분할을 막으면 안 된다. 원고와
        # HWPX는 같은 문단 목록을 쓴다(노드↔문단 대응 유지).
        paragraphs = list(FOOD_CROPS_PARAGRAPHS)
        body_at = paragraphs.index("1. 머리말") + 1
        paragraphs.insert(body_at, "요약")
        data, applied = self._transform(
            hwpx_section(paragraphs),
            markdown="\n\n".join(paragraphs) + "\n")
        self.assertEqual(["Contents/section%d.xml" % i for i in range(3)],
                         applied["sections"])
        body = paragraphs_of(
            read_zip(data)["Contents/section2.xml"].decode("utf-8"))
        self.assertIn("요약", ["".join(node.itertext()) for node in body])

    def test_front_markers_hidden_and_pages_broken(self):
        # 구분 이름은 관리용 줄이라 글자를 비우고, 앞부분 제목마다 쪽을 나눈다.
        data, applied = self._transform(hwpx_section(list(FOOD_CROPS_PARAGRAPHS)))
        members = read_zip(data)
        sections = [members["Contents/section%d.xml" % index].decode("utf-8")
                    for index in range(3)]
        texts = "".join("".join(paragraph.itertext())
                        for xml in sections for paragraph in paragraphs_of(xml))
        for role in ("겉표지", "표제면", "제출서", "인준서"):
            self.assertNotIn(role, texts)
        # 검사기가 글자로 찾는 제목은 남긴다.
        for title in ("요약", "감사의 글", "목차", "표 목차", "그림 목차"):
            self.assertIn(title, texts)
        # 은닉은 글자만 비운다(겉표지 구역 제어 요소·구조 불변).
        first = paragraphs_of(sections[0])[0]
        self.assertEqual([], ctrl_items(first, "pageNum"))
        self.assertEqual([], ctrl_items(first, "newNum"))
        self.assertEqual(1, sections[0].count("<hp:secPr"))
        self.assertEqual(1, sections[0].count("<hp:colPr"))
        self.assertEqual([8, 17, 26], [
            index for index, paragraph in enumerate(paragraphs_of(sections[0]))
            if paragraph.get("pageBreak") == "1"])
        self.assertEqual([5, 6, 7], [
            index for index, paragraph in enumerate(paragraphs_of(sections[1]))
            if paragraph.get("pageBreak") == "1"])
        self.assertEqual([], [
            index for index, paragraph in enumerate(paragraphs_of(sections[2]))
            if paragraph.get("pageBreak") == "1"])
        self.assertEqual(
            [{"section": "Contents/section0.xml",
              "roles": ["겉표지", "표제면", "제출서", "인준서"],
              "paragraphs": [0, 8, 17, 26]}],
            applied["front_hidden"])
        self.assertEqual(
            {"Contents/section0.xml": [8, 17, 26],
             "Contents/section1.xml": [5, 6, 7]},
            {entry["section"]: entry["paragraphs"]
             for entry in applied["front_breaks"]})

    def test_front_marker_text_mismatch_blocks_hiding(self):
        # 웹 r1 중요 01: 문단 수만 맞고 글자가 다른 경우(노드↔문단 어긋남)에는
        # 제목 같은 실제 내용을 지우지 말고 멈춘다. 색인 8(`표제면`)은 분할
        # 앵커가 아니므로 은닉 단계까지 도달한다.
        paragraphs = list(FOOD_CROPS_PARAGRAPHS)
        paragraphs[8] = "합성 논문 제목"
        with self.assertRaises(gh.HwpxBlocked) as caught:
            self._transform(hwpx_section(paragraphs))
        self.assertEqual("front_marker_text_mismatch",
                         caught.exception.reason)
        details = caught.exception.details
        self.assertEqual("표제면", details["role"])
        self.assertEqual(8, details["paragraph"])
        self.assertEqual("표제면", details["expected"])
        self.assertEqual("합성 논문 제목", details["actual"])

    def test_page_break_handles_existing_attribute_in_any_quote(self):
        # 웹 r1 중요 02: 존재 판정이 `pageBreak="` 문자열 검사라 작은따옴표를
        # 놓쳐 같은 이름의 속성을 하나 더 붙였다(중복 속성 → XML 파싱 실패).
        cases = {
            "double-quoted": 'pageBreak="0"',
            "absent": None,
            "single-quoted": "pageBreak='0'",
        }
        for label, attrs in cases.items():
            with self.subTest(case=label):
                section = hwpx_section(list(FOOD_CROPS_PARAGRAPHS))
                if attrs is not None:
                    section = add_head_attrs(section, 8, attrs)
                data, _ = self._transform(section)
                xml = read_zip(data)["Contents/section0.xml"].decode("utf-8")
                paragraphs = paragraphs_of(xml)
                self.assertEqual("1", paragraphs[8].get("pageBreak"))
                self.assertEqual([8, 17, 26], [
                    index for index, paragraph in enumerate(paragraphs)
                    if paragraph.get("pageBreak") == "1"])
                head = paragraph_head(xml, 8)
                self.assertEqual(1, head.count("pageBreak"))
                self.assertIn('pageBreak="1"', head)
                # 헤드의 나머지 속성은 그대로 남는다.
                self.assertIn('paraPrIDRef="0"', head)
                self.assertIn('styleIDRef="0"', head)

    def test_self_closing_t_mixed_with_text_is_hidden_and_parses(self):
        # 권고 01: 자기닫힘 `<hp:t/>`가 일반 글자와 섞여도 글자만 비우고
        # XML은 그대로 유효해야 한다.
        section = hwpx_section(list(FOOD_CROPS_PARAGRAPHS))
        section = replace_paragraph(
            section, 8,
            '<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
            '<hp:t/><hp:t>표제면</hp:t><hp:t/></hp:run></hp:p>')
        data, applied = self._transform(section)
        self.assertEqual([0, 8, 17, 26],
                         applied["front_hidden"][0]["paragraphs"])
        xml = read_zip(data)["Contents/section0.xml"].decode("utf-8")
        paragraphs = paragraphs_of(xml)
        self.assertNotIn("표제면",
                         "".join("".join(node.itertext())
                                 for node in paragraphs))
        self.assertEqual(3, len(list(paragraphs[8].iter("{%s}t" % _HP))))
        self.assertEqual([""] * 3,
                         ["".join(node.itertext())
                          for node in paragraphs[8].iter("{%s}t" % _HP)])

    def test_body_toc_subhead_does_not_get_page_break(self):
        # 권고 01: 본문에 같은 `표 목차` 글자가 있어도 목차 블록 밖이면
        # 쪽을 나누지 않는다.
        paragraphs = list(FOOD_CROPS_PARAGRAPHS)
        paragraphs.insert(paragraphs.index("1. 머리말") + 1, "표 목차")
        data, applied = self._transform(
            hwpx_section(paragraphs),
            markdown="\n\n".join(paragraphs) + "\n")
        self.assertEqual(
            {"Contents/section0.xml": [8, 17, 26],
             "Contents/section1.xml": [5, 6, 7]},
            {entry["section"]: entry["paragraphs"]
             for entry in applied["front_breaks"]})
        body = read_zip(data)["Contents/section2.xml"].decode("utf-8")
        self.assertEqual([], [
            index for index, paragraph in enumerate(paragraphs_of(body))
            if paragraph.get("pageBreak") == "1"])
        self.assertIn("표 목차", ["".join(node.itertext())
                                  for node in paragraphs_of(body)])

    def test_hidden_paragraph_preserves_controls_byte_for_byte(self):
        # 권고 01: 은닉은 글자만 비운다. 은닉 문단의 secPr·colPr·linesegarray는
        # 변환 전과 문자열 그대로 같아야 한다.
        lineseg = ('<hp:linesegarray><hp:lineseg textpos="0" vertpos="0"'
                   ' vertsize="1000" textheight="1000" baseline="850"'
                   ' spacing="600" horzsize="42520"/></hp:linesegarray>')
        control = '<hp:ctrl><hp:bookmark name="표제면"/></hp:ctrl>'
        section = hwpx_section(list(FOOD_CROPS_PARAGRAPHS))
        section = replace_paragraph(
            section, 8,
            '<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="0">'
            + control + '<hp:t>표제면</hp:t>' + lineseg
            + '</hp:run></hp:p>')
        before = section[slice(*gh.top_level_paragraph_spans(section)[8])]
        data, _ = self._transform(section)
        xml = read_zip(data)["Contents/section0.xml"].decode("utf-8")
        hid = xml[slice(*gh.top_level_paragraph_spans(xml)[8])]
        self.assertEqual(without_text(before[before.index("<hp:run"):]),
                         without_text(hid[hid.index("<hp:run"):]))
        self.assertEqual([lineseg], re.findall(
            r"<hp:linesegarray>.*?</hp:linesegarray>", hid))
        self.assertEqual([control], re.findall(
            r'<hp:ctrl><hp:bookmark name="표제면"/></hp:ctrl>', hid))
        hid_paragraph = paragraphs_of(xml)[8]
        self.assertEqual([""], ["".join(node.itertext())
                                for node in hid_paragraph.iter("{%s}t" % _HP)])
        # `secPr`·`colPr`는 분할 단계가 모든 문단에서 걷어내 첫 문단에만 다시
        # 넣으므로, 색인 8이 아니라 색인 0(`겉표지`)에서 보존을 잠근다. 색인 0도
        # 은닉 대상이면서 두 제어 요소를 달고 있는 유일한 문단이다.
        cover = xml[slice(*gh.top_level_paragraph_spans(xml)[0])]
        self.assertIn(FOOD_CROPS_COLPR, cover)
        self.assertEqual(1, cover.count("<hp:secPr>"))
        self.assertEqual("", "".join(paragraphs_of(xml)[0].itertext()))

    def test_hide_step_changes_only_text(self):
        # 은닉 함수 하나만 떼어 본 불변식: 입력과 출력은 `<hp:t>` 안쪽에서만
        # 다르다. 여백·쪽번호 단계가 앞서 secPr 값(여백)을 바꾸므로 전체 변환
        # 결과로는 이 불변식을 잠글 수 없다.
        nodes, plan = food_crops_plan()
        section = hwpx_section(list(FOOD_CROPS_PARAGRAPHS))
        hidden, info = gh._hide_front_markers(
            section, nodes=nodes, plan=plan,
            para_start=0, para_end=len(nodes))
        self.assertEqual(["겉표지", "표제면", "제출서", "인준서"], info["roles"])
        self.assertEqual([0, 8, 17, 26], info["paragraphs"])
        self.assertEqual(without_text(section), without_text(hidden))
        self.assertIn(FOOD_CROPS_SECPR, hidden)
        self.assertIn(FOOD_CROPS_COLPR, hidden)
        for text in ("겉표지", "표제면", "제출서", "인준서"):
            self.assertNotIn(text, hidden)
        self.assertEqual(len(nodes), len(paragraphs_of(hidden)))

    def test_split_strips_existing_controls_before_insert(self):
        numbers = ('<hp:ctrl><hp:pageNum pos="TOP_CENTER" formatType="DIGIT"'
                   ' sideChar="-"/></hp:ctrl>')
        extra = {
            5: FOOD_CROPS_SECPR + numbers + gh.NEW_NUM_CONTROL,
            40: numbers,
        }
        data, applied = self._transform(
            hwpx_section(list(FOOD_CROPS_PARAGRAPHS), extra=extra))
        self.assertEqual(
            [{"secPr": 1, "colPr": 1, "pageNum": 0, "newNum": 0},
             {"secPr": 1, "colPr": 1, "pageNum": 1, "newNum": 1},
             {"secPr": 1, "colPr": 1, "pageNum": 1, "newNum": 1}],
            applied["section_controls"])
        members = read_zip(data)
        for index in range(3):
            xml = members["Contents/section%d.xml" % index].decode("utf-8")
            self.assertEqual(1, xml.count("<hp:secPr"), index)
            self.assertEqual(1, xml.count("<hp:colPr"), index)
            self.assertEqual(0 if index == 0 else 1, xml.count("<hp:pageNum"))
            self.assertEqual(0 if index == 0 else 1, xml.count("<hp:newNum"))
            self.assertNotIn('pos="TOP_CENTER"', xml)

    def test_split_is_noop_without_front_matter(self):
        # 앞머리 표지 7개가 하나도 없는 본문-only 입력은 유일하게 허용된 no-op.
        # 분할 단계만 보므로 여백·쪽번호 재작성은 끈다(그 둘은 별도 단계다).
        markdown = "본문이다.\n\n두 번째 문단.\n"
        data = food_crops_zip(hwpx_section(["본문이다.", "두 번째 문단."]))
        nodes, plan = food_crops_plan(markdown)
        self.assertIsNone(plan)
        out, applied = gh.transform_hwpx(
            data, margins_mm=None, page_numbers=False,
            section_formats=gh.FOOD_CROPS_SECTION_FORMATS,
            nodes=nodes, plan=plan)
        self.assertEqual(data, out)
        self.assertEqual("not_applicable", applied["sections"])
        self.assertEqual({}, applied["section_controls"])
        self.assertEqual([], applied["margins"])
        self.assertEqual([], applied["page_numbers"])
        self.assertEqual([], applied["charpr"])
        self.assertEqual([], applied["front_hidden"])
        self.assertEqual([], applied["front_breaks"])

    def test_split_rejects_partial_front_matter(self):
        cases = {
            "no-cover": list(FOOD_CROPS_PARAGRAPHS[1:]),
            "only-summary-and-contents": ["요약", "1. 농장 개요",
                                          "목차", "1. 머리말"],
        }
        for label, paragraphs in cases.items():
            with self.subTest(case=label):
                markdown = "\n\n".join(paragraphs) + "\n"
                nodes, plan = food_crops_plan(markdown)
                self.assertIsNone(plan)
                with self.assertRaises(gh.HwpxBlocked) as caught:
                    gh.transform_hwpx(
                        food_crops_zip(hwpx_section(paragraphs)),
                        section_formats=gh.FOOD_CROPS_SECTION_FORMATS,
                        nodes=nodes, plan=plan)
                self.assertEqual("section_split_front_matter_partial",
                                 caught.exception.reason,
                                 caught.exception.details)

    def test_final_file_has_exactly_one_page_number_per_section(self):
        root = self._project("food_crops")
        (Path(root) / "body.md").write_text(FOOD_CROPS_MD, encoding="utf-8")
        # 글꼴 단계가 실제로 값을 바꿔야 발행을 통과한다(원본 face가 이미 학교
        # 글꼴이면 applied["font"]가 비어 format_not_applied로 막힌다).
        header = FOOD_CROPS_HEADER.replace(gh.SCHOOL_FONT, "함초롬바탕")
        cli = make_fake_cli(root, markdown=FOOD_CROPS_MD,
                            section=hwpx_section(list(FOOD_CROPS_PARAGRAPHS)),
                            header=header, hpf=FOOD_CROPS_HPF)
        value = build_hwpx.export(
            root, "body.md", "out.hwpx", cli_command=cli,
            context=self._ctx(root, "food_crops"))
        members = read_zip((Path(root) / "out.hwpx").read_bytes())
        measured = []
        for index in range(3):
            xml = members["Contents/section%d.xml" % index].decode("utf-8")
            measured.append({"pageNum": xml.count("<hp:pageNum"),
                             "newNum": xml.count("<hp:newNum")})
            first = paragraphs_of(xml)[0]
            self.assertEqual(measured[-1]["pageNum"],
                             len(ctrl_items(first, "pageNum")))
            self.assertEqual(measured[-1]["newNum"],
                             len(ctrl_items(first, "newNum")))
        self.assertEqual(
            [{"pageNum": 0, "newNum": 0},
             {"pageNum": 1, "newNum": 1},
             {"pageNum": 1, "newNum": 1}], measured)
        applied = value["manifest"]["hwpx"]["post_processing"]
        self.assertEqual(
            [{"section": "Contents/section%d.xml" % index, **measured[index]}
             for index in range(3)], applied["page_numbers"])
        self.assertIn('face="신명조"',
                      members["Contents/header.xml"].decode("utf-8"))
        # 안내문은 식량작물 전용 문구를 고른다(공용 상수는 그대로).
        self.assertEqual(list(build_hwpx.FOOD_CROPS_HUMAN_FINISH),
                         value["manifest"]["school_frontmatter"]
                         ["human_finish_required"])
        self.assertIn("요약은 Ⅰ부터, 본문은 1부터",
                      value["manifest"]["notice"])
        self.assertNotIn(build_hwpx.FINISH_NOTICE, value["manifest"]["notice"])
        self.assertEqual(
            ["겉표지", "표제면", "제출서", "인준서", "요약", "감사의 글", "목차"],
            value["manifest"]["school_frontmatter"]["sections"])


    def test_appended_front_charprs_keep_header_parseable(self):
        # 템플릿 header.xml은 한 줄이다. 끝 태그 앞에 줄바꿈이 없을 때 예전
        # 구현은 XML 선언 앞에 charPr를 붙여 파싱 불가 파일을 만들었다.
        data, applied = self._transform(
            hwpx_section(list(FOOD_CROPS_PARAGRAPHS)))
        xml = read_zip(data)["Contents/header.xml"].decode("utf-8")
        self.assertTrue(xml.startswith("<?xml"), xml[:40])
        root = ET.fromstring(xml)
        self.assertEqual("{%s}head" % _HH, root.tag)
        self.assertEqual("3", root.get("secCnt"))
        properties = root.find("{%s}charProperties" % _HH)
        self.assertEqual("15", properties.get("itemCnt"))
        charprs = {"%s" % node.get("id"): node
                   for node in properties.findall("{%s}charPr" % _HH)}
        self.assertEqual(["0"] + [str(index) for index in range(1, 15)],
                         sorted(charprs, key=int))
        self.assertEqual(list(gh._FRONT_CHARPR_SPECS),
                         [(int(charprs[str(index)].get("height")) // 100,
                           charprs[str(index)].get("bold") == "1")
                          for index in range(1, 15)])
        for index in (3, 5, 7, 9, 11, 13):
            self.assertIsNotNone(
                charprs[str(index)].find("{%s}bold" % _HH), index)
        # 붙인 id가 세 구역 모두의 planned 문단에 실제로 걸린다.
        self.assertEqual(["Contents/section%d.xml" % index for index in range(3)],
                         sorted(applied["charpr"]))


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
