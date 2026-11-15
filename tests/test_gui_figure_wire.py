"""GF2: 규칙집 그림 명령·docx 그림 검사가 입구 세 곳에 붙었는지 잠근다.

- `gg.py figure …` — argparse 전에 gg_figure로 넘긴다.
- `gg_document.check` — `app-figure:<sha256>` 토큰을 파일 없음으로 오판하지 않는다.
- `build_docx` — 토큰을 실제 그림으로 넣고, 증명이 없으면 빌드를 멈춘다.
- M-02: 그림 목적지를 푸는 기준(원고 옆 → 작업 폴더 뿌리)을 DOCX·검사·HWPX
  세 입구가 `gg_hwpx.resolve_figure` 하나로 공유한다 — 뿌리 기준 경로는 예전과
  같고, 원고 옆에만 있는 그림도 셋이 같이 찾으며, 같은 이름의 다른 그림이 두
  곳에 있으면 셋 다 같은 안내 문장으로 멈춘다.
"""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from docx import Document

from tests._harness import ContractCase, SCRIPTS, bind_major, runtime, write_text

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x06\x00\x00\x00" + b"\x1f\x15\xc4\x89"
       + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
         b"\r\n-\xb4" + b"\x00\x00\x00\x00IEND\xaeB`\x82")
SHA = hashlib.sha256(PNG).hexdigest()
CAPTION = "그림 1. 재배 기술 (출처 : 농촌진흥청)"
FIGURE = "## 가. 절\n\n![%s](app-figure:%s)\n\n%s\n" % (CAPTION, SHA, CAPTION)


class FigureCase(ContractCase):
    """그림 시험 공통 대역 — 프로젝트·런타임 모듈·가짜 앱 저장고."""

    def setUp(self):
        super().setUp()
        self.root = self.make_project()
        bind_major(self.root, "specialty_crops")
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")
        self.builder = runtime("build_docx")
        self.docmod = runtime("gg_document")
        self.gd = runtime("gg_figure_docx")

    def app_home(self, verdict="approved", body=PNG):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-figure-")
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / "store").mkdir()
        (home / "store" / (SHA + ".png")).write_bytes(body)
        line = {
            "v": 1, "at": "2026-11-11T10:00:00Z", "reqId": "req-1",
            "folderHash": "0123456789abcdef", "slot": "s1",
            "page": {"url": "https://www.nongsaro.go.kr/x", "sha256": "p",
                     "kogl": 2, "org": "농촌진흥청"},
            "image": {"sha256": SHA, "verdict": verdict, "kogl": 2,
                      "org": "농촌진흥청", "ruleId": "nongsaro-curation"},
        }
        (home / "receipts.jsonl").write_text(
            json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
        return home

    def at_home(self, home):
        """KNUAF_FIGURE_HOME만 바꾼다 — 빈 문자열이면 앱 밖이다."""
        return mock.patch.dict(os.environ,
                               {"KNUAF_FIGURE_HOME": str(home) if home else ""})

    def convert(self, text, base=None, root=None):
        return self.builder.convert(text, base=str(base or self.root),
                                    context=self.context,
                                    root=None if root is None else str(root))

    def missing_image(self, text, base=None):
        """검사 입구 — `base`는 원고가 있는 폴더, 뿌리는 늘 프로젝트 폴더다."""
        return [i for i in self.docmod.check(
                    text, str(base or self.root),
                    project_root=str(self.root))
                if i[0] == "missing_image"]

    # --- 진짜 CLI 대역 ----------------------------------------------------

    def cli_base_env(self):
        """자식 프로세스 공통 환경 — 출력 인코딩을 못 박는다.

        부모가 `PYTHONIOENCODING=ascii` 같은 값으로 떠 있어도 자식이 한글을
        뱉다 죽지 않게 한다(윈도 CI 거짓 실패 방지). `KNUAF_FIGURE_HOME`은
        건드리지 않는다.
        """
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return env

    def cli_env(self, home):
        """공통 환경 + `KNUAF_FIGURE_HOME` — None이면 앱 밖(빈 문자열)."""
        env = self.cli_base_env()
        env["KNUAF_FIGURE_HOME"] = str(home) if home else ""
        return env

    def build_cli(self, manuscript, out, home, *, inp="body.md"):
        """build_docx.py를 진짜 CLI로 돌린다 — 앱이 받는 JSON 봉투 그대로.

        ``inp``는 `--in`에 줄 원고 경로다. 기본값은 프로젝트 뿌리의 `body.md`
        (앱이 넘기는 모양)이고, M-02 시험은 하위 폴더 원고(`build/body.md`)를
        준다 — 원고 폴더와 프로젝트 뿌리가 다를 때의 배선을 잠그려고.
        """
        write_text(self.root, inp, manuscript)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "build_docx.py"),
             str(self.root), "--in", inp, "--out", out,
             "--major", "specialty_crops"],
            capture_output=True, text=True, encoding="utf-8",
            env=self.cli_env(home), cwd=self.root)

    def cli_pictures(self, path):
        """CLI가 만든 docx를 **다시 열어** 박힌 그림 개수를 센다.

        매니페스트는 앱이 읽는 요약일 뿐이라, 파일 자체에 그림이 들어갔는지는
        따로 확인해야 한다(웹 검토 3번).
        """
        return len(Document(str(path)).inline_shapes)

    def cli_media(self, path):
        """이미 저장된 DOCX의 `word/media/*` 바이트 목록."""
        with zipfile.ZipFile(path) as archive:
            return [archive.read(entry) for entry in archive.namelist()
                    if entry.startswith("word/media/")]


class GuiFigureWireTests(FigureCase):
    # --- §4 네 경우 -------------------------------------------------------

    def test_verified_app_token_is_inserted_and_passes_check(self):
        with self.at_home(self.app_home()):
            self.assertEqual(self.missing_image(FIGURE), [])
            doc = self.convert(FIGURE)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertNotIn(self.gd.PLACEHOLDER,
                         [p.text for p in doc.paragraphs])

    def test_unverified_token_blocks_build_and_reports_issue(self):
        with self.at_home(self.app_home(verdict="ambiguous")):
            issues = self.missing_image(FIGURE)
            self.assertEqual(
                issues, [("missing_image", "app-figure:%s: figure_unverified" % SHA)])
            with self.assertRaises(ValueError) as caught:
                self.convert(FIGURE)
        self.assertEqual(str(caught.exception), "blocked: figure_unverified")

    def test_outside_app_leaves_placeholder_not_picture(self):
        with self.at_home(None):
            self.assertEqual(self.missing_image(FIGURE), [])
            doc = self.convert(FIGURE)
        self.assertEqual(len(doc.inline_shapes), 0)
        self.assertIn(self.gd.PLACEHOLDER, [p.text for p in doc.paragraphs])

    def test_missing_plain_file_still_reports_missing_image(self):
        text = FIGURE.replace("app-figure:" + SHA, "figures/public/none.png")
        with self.at_home(None):
            self.assertEqual(self.missing_image(text),
                             [("missing_image", "figures/public/none.png")])
            with self.assertRaises(ValueError) as caught:
                self.convert(text)
        self.assertEqual(str(caught.exception),
                         "그림 파일 없음/외부 경로: figures/public/none.png")

    # --- 입구 세 곳 -------------------------------------------------------

    def test_gg_py_dispatches_figure_place(self):
        write_text(self.root, "build/검토전_본문.md",
                   "# Ⅲ. 결과 및 고찰\n\n## 가. 품종 선택\n\n본문 한 줄.\n")
        picture = self.root / "figures" / "public" / "a.jpg"
        picture.parent.mkdir(parents=True)
        picture.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 60)
        gg = runtime("gg")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gg.main([
                "figure", "place", str(self.root), "--slot", "s1",
                "--heading", "가. 품종 선택", "--image", "figures/public/a.jpg",
                "--org", "농촌진흥청",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "placed")
        manuscript = (self.root / "build" / "검토전_본문.md").read_text(
            encoding="utf-8")
        self.assertIn("![그림 1. 품종 선택 (출처 : 농촌진흥청)]"
                      "(figures/public/a.jpg)", manuscript)
        self.assertIn("그림 1. 품종 선택 (출처 : 농촌진흥청)", manuscript)

    def test_cli_entry_dispatches_figure_before_its_own_parser(self):
        write_text(self.root, "build/검토전_본문.md",
                   "## 가. 품종 선택\n\n본문.\n")
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg.py"), "figure", "place",
             str(self.root), "--slot", "s1", "--heading", "가. 품종 선택"],
            capture_output=True, text=True, encoding="utf-8",
            env=self.cli_base_env(), cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_photo")

    def test_manifest_carries_figures_report(self):
        result = self.build_cli(FIGURE, "build/f.docx", self.app_home())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cli_pictures(self.root / "build" / "f.docx"), 1)
        manifest = json.loads((self.root / "build" / "f.manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual([item["kind"] for item in manifest["figures"]["images"]],
                         ["app"])
        self.assertEqual(manifest["figures"]["student_count"], 0)
        self.assertEqual(manifest["figures"]["blocked"], [])

    # --- 웹 검토 R-01/R-02 ------------------------------------------------

    def test_cli_reports_unverified_token_as_blocked_json_and_exit_2(self):
        """R-01: 앱이 미승인 토큰을 넘기면 CLI가 JSON으로 거절하고 종료코드 2를
        낸다 — 앱이 traceback 줄을 파싱하지 않도록. 문서는 만들지 않는다."""
        result = self.build_cli(FIGURE, "build/f.docx",
                                self.app_home(verdict="ambiguous"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("figure_unverified", payload["reason"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "build" / "f.docx").exists())

    def test_student_local_figure_is_inserted_and_counted(self):
        """R-02①: 앱 밖에서 학생이 넣은 프로젝트 안 그림은 그대로 들어가고
        학생 그림 1건으로 집계된다 — 자리 표시·차단으로 새지 않는다."""
        picture = self.root / "figures" / "public" / "ok.png"
        picture.parent.mkdir(parents=True)
        picture.write_bytes(PNG)
        text = FIGURE.replace("app-figure:" + SHA, "figures/public/ok.png")
        with self.at_home(None):
            self.assertEqual(self.missing_image(text), [])
            doc = self.convert(text)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertNotIn(self.gd.PLACEHOLDER, [p.text for p in doc.paragraphs])
        result = self.build_cli(text, "build/student.docx", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        # 매니페스트가 아니라 만들어진 파일을 다시 열어 확인한다.
        self.assertEqual(self.cli_pictures(self.root / "build" / "student.docx"), 1)
        manifest = json.loads(
            (self.root / "build" / "student.manifest.json").read_text(
                encoding="utf-8"))
        figures = manifest["figures"]
        self.assertEqual([item["kind"] for item in figures["images"]],
                         ["student"])
        self.assertEqual(figures["student_count"], 1)
        self.assertEqual(figures["placeholders"], [])
        self.assertEqual(figures["blocked"], [])

    def test_outside_paths_are_rejected_by_check_and_build(self):
        """R-02②: 폴더 밖 경로는 문서 검사가 missing_image로 잡고 빌드는
        '그림 파일 없음/외부 경로'로 거절한다 — 경로 이탈 우회 없음.

        원고에 적히는 **문자열 그대로**(슬래시 `/` 표기)를 쓴다. `os.path.relpath`
        는 윈도에서 역슬래시를 내놓는데, 그건 POSIX에서 파일 이름 일부라
        '폴더 밖'이 아니라 '없는 파일'로 통과해 버린다(거짓 통과).
        같은 이름의 파일을 폴더 **안**에도 두어, 거절이 이름이 아니라
        위치 때문임을 대조로 못 박는다.
        """
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-outside-",
                                          dir=str(self.root.parent))
        self.addCleanup(tmp.cleanup)
        outside = Path(tmp.name) / "밖.png"
        outside.write_bytes(PNG)
        inside = self.root / "figures" / "public" / "밖.png"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(PNG)

        with self.at_home(None):
            # 대조: 같은 이름, 폴더 안 → 통과해야 한다.
            control = FIGURE.replace("app-figure:" + SHA, "figures/public/밖.png")
            self.assertEqual(self.missing_image(control), [])
            built = self.build_cli(control, "build/inside.docx", None)
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertEqual(self.cli_pictures(self.root / "build" / "inside.docx"),
                             1)

        refs = ("../%s/밖.png" % Path(tmp.name).name, outside.as_posix())
        for index, ref in enumerate(refs):
            with self.subTest(ref=ref):
                text = FIGURE.replace("app-figure:" + SHA, ref)
                with self.at_home(None):
                    self.assertEqual(self.missing_image(text),
                                     [("missing_image", ref)])
                result = self.build_cli(text, "build/out%d.docx" % index, None)
                self.assertEqual(result.returncode, 2, result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "blocked")
                self.assertIn("그림 파일 없음/외부 경로: " + ref,
                              payload["reason"])
                self.assertFalse(
                    (self.root / "build" / ("out%d.docx" % index)).exists())


def manuscript(ref, caption="그림 1. 밭 전경 (출처 : 농촌진흥청)"):
    """그림 한 줄 + 캡션. 캡션이 없으면 검사기가 missing_caption을 함께 낸다."""
    return "## 가. 절\n\n![밭](%s)\n\n%s\n" % (ref, caption)


class FigurePathResolveTests(FigureCase):
    """M-02: 그림 목적지를 푸는 기준이 입구마다 같은가.

    원고가 `build/` 아래에 있을 때 그림이 (a) 작업 폴더 뿌리 (b) 원고 옆 (c) 두
    곳 모두에 있는 세 경우를 `gg_document.check`(검사) · `build_docx`(DOCX) ·
    `gg_hwpx`(HWPX) 세 입구에 같은 원고로 넣고 결론을 맞춘다.

    계약: 그림 목적지는 원고 옆 → 작업 폴더 뿌리 순서로 찾고(`gg_hwpx.resolve_figure`
    하나를 세 입구가 함께 쓴다), 찾은 경로를 뿌리 기준 상대 경로로 다시 쓴다.
    두 곳에 같은 이름의 **다른** 그림이 있으면 어느 입구도 고르지 않고 같은 안내
    문장으로 멈춘다 — 틀린 그림이 조용히 들어가지 않는다(M-02).

    토큰(`app-figure:<sha256>`)은 경로가 아니므로 위치와 무관하게 앱 저장고에서만
    풀려야 한다.
    """

    def setUp(self):
        super().setUp()
        self.mc = runtime("gg_major_contract")
        self.authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_PAPER, self.context)
        self.gh = runtime("gg_hwpx")
        self.split = runtime("gg_draft_split")

    # --- 대역 ------------------------------------------------------------

    def picture(self, rel, body=PNG):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def fake_kordoc(self):
        """kordoc 없이 HWPX 발행 경로를 끝까지 돌리는 가짜 CLI.

        어댑터 시험의 것을 그대로 쓴다 — 받은 마크다운은 `input-md.txt`,
        generate argv는 `argv-generate.json`으로 프로젝트 뿌리에 남는다.
        """
        from tests.test_hwpx_adapter import FAKE_CLI

        script = self.root / "fake_kordoc.py"
        script.write_text(FAKE_CLI, encoding="utf-8")
        (self.root / "control.json").write_text(
            json.dumps({"markdown": ""}), encoding="utf-8")
        return [sys.executable, str(script)]

    def build_hwpx(self, out="out/검토.hwpx"):
        return self.gh.build(
            self.root / "build/본문.md", self.root / out,
            context=self.context, authorization=self.authorization,
            cli_command=self.fake_kordoc())

    def sent_markdown(self):
        return (self.root / "input-md.txt").read_text(encoding="utf-8")

    def generate_argv(self):
        return json.loads(
            (self.root / "argv-generate.json").read_text(encoding="utf-8"))

    def media(self, doc, name):
        """DOCX를 저장해 `word/media/*`를 꺼낸다 — 어떤 바이트가 박혔는지 본다."""
        path = self.root / name
        doc.save(str(path))
        return self.cli_media(path)

    # --- 셋이 같은 결론인 경우 --------------------------------------------

    def test_root_relative_figure_agrees_across_check_docx_and_hwpx(self):
        """(a) 뿌리에만 있는 그림: 셋 다 찾는다 — 경로도 그대로다.

        HWPX는 원고 옆(`build/`)에 없다가 뿌리에서 찾고, 목적지를 뿌리 기준
        그대로(`figures/public/farm.png`) 다시 쓴다. 생성기에 주는 `--image-dir`은
        작업 폴더 뿌리 하나뿐이다.
        """
        self.picture("figures/public/farm.png")
        text = manuscript("figures/public/farm.png")
        write_text(self.root, "build/본문.md", text)

        self.assertEqual(self.missing_image(text), [])
        doc = self.convert(text)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertIn(PNG, self.media(doc, "root-only.docx"))

        self.assertEqual("ready", self.build_hwpx()["status"])
        argv = self.generate_argv()
        self.assertEqual(1, argv.count("--image-dir"))
        self.assertEqual(str(self.root.resolve()),
                         argv[argv.index("--image-dir") + 1])
        self.assertIn("![밭](figures/public/farm.png)", self.sent_markdown())
        self.assertEqual(1, self.gh.embedded_picture_count(
            (self.root / "out/검토.hwpx").read_bytes()))

    def test_split_rewrite_leaves_root_relative_paths_untouched(self):
        """분할이 고쳐 둔 뿌리 기준 경로는 한 번 더 돌아도 그대로다(멱등).

        재작성이 값을 바꾸면 분할 뒤 발행에서 경로가 흔들린다 — HWPX가 받는
        경로가 곧 분할이 쓴 경로임을 못 박는다.
        """
        self.picture("figures/public/farm.png")
        body = manuscript("figures/public/farm.png")
        rewritten, problems = self.split._rewrite_images(
            body, self.root, self.root / "build")
        self.assertEqual((rewritten, problems), (body, []))
        write_text(self.root, "build/본문.md", rewritten)
        self.assertEqual("ready", self.build_hwpx()["status"])
        self.assertIn("![밭](figures/public/farm.png)", self.sent_markdown())

    def test_split_rewrite_roots_beside_manuscript_figure(self):
        """원고 옆에만 있는 그림은 분할이 뿌리 기준(`build/farm.png`)으로 고친다.

        고친 경로는 발행에서 원고 옆(`build/` + `build/farm.png` = 없음)이 아니라
        뿌리에서 찾힌다 — 분할과 발행이 같은 그림을 가리킨다.
        """
        self.picture("build/farm.png")
        rewritten, problems = self.split._rewrite_images(
            manuscript("farm.png"), self.root, self.root / "build")
        self.assertEqual(problems, [])
        self.assertIn("![밭](build/farm.png)", rewritten)
        write_text(self.root, "build/본문.md", rewritten)
        # 분할을 거친 뒤에는 두 입구가 같은 결론이다 — 고친 경로가 뿌리 기준이라
        # DOCX·검사도 찾고, HWPX는 원고 옆 후보가 사라져 모호도 아니다.
        self.assertEqual(self.missing_image(rewritten), [])
        self.assertEqual(len(self.convert(rewritten).inline_shapes), 1)
        self.assertEqual("ready", self.build_hwpx()["status"])
        self.assertEqual(1, self.gh.embedded_picture_count(
            (self.root / "out/검토.hwpx").read_bytes()))

    # --- 세 입구가 같은 그림을 고르는가(M-02 고침) ------------------------

    def test_beside_manuscript_figure_is_found_by_check_docx_and_hwpx(self):
        """(b) 원고 옆에만 있는 그림: 셋 다 찾는 ー 검사 통과·DOCX 삽입·HWPX 발행.

        그림 목적지는 원고 옆 -> 뿌리 순서로 푼다(#131이 HWPX에 넣은 규칙을
        `gg_hwpx.resolve_figure` 하나로 모아 DOCX·검사도 같은 함수를 쓴다).
        """
        beside = self.picture("build/farm.png")
        text = manuscript("farm.png")
        write_text(self.root, "build/본문.md", text)
        manuscript_dir = self.root / "build"

        self.assertEqual(self.missing_image(text, base=manuscript_dir), [])
        doc = self.convert(text, base=manuscript_dir, root=self.root)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertIn(beside.read_bytes(), self.media(doc, "beside.docx"))

        self.assertEqual("ready", self.build_hwpx()["status"])
        self.assertIn("![밭](build/farm.png)", self.sent_markdown())
        self.assertEqual(1, self.gh.embedded_picture_count(
            (self.root / "out/검토.hwpx").read_bytes()))

    def test_two_candidates_stop_check_docx_and_hwpx_with_the_same_sentence(self):
        """(c) 두 곳에 같은 이름의 다른 그림: 셋 다 멈추고 같은 문장을 보여 준다.

        뿌리 그림을 조용히 고르지 않는다(M-02). 세 입구가 같은 후보 순서
        (원고 옆 먼저)와 같은 안내 문장을 쓴다.
        """
        self.picture("figures/public/farm.png")
        self.picture("build/figures/public/farm.png", PNG + b"other")
        text = manuscript("figures/public/farm.png")
        write_text(self.root, "build/본문.md", text)
        manuscript_dir = self.root / "build"
        sentence = self.gh.ambiguous_image_message(
            ["build/figures/public/farm.png", "figures/public/farm.png"])

        self.assertEqual(self.missing_image(text, base=manuscript_dir),
                         [("missing_image",
                           "figures/public/farm.png: " + sentence)])
        with self.assertRaises(ValueError) as caught:
            self.convert(text, base=manuscript_dir, root=self.root)
        self.assertEqual(str(caught.exception), sentence)

        with self.assertRaises(self.gh.HwpxBlocked) as ctx:
            self.build_hwpx()
        self.assertEqual("image_ambiguous", ctx.exception.reason)
        self.assertEqual(["build/figures/public/farm.png",
                          "figures/public/farm.png"],
                         ctx.exception.details["candidates"])
        self.assertEqual(sentence, ctx.exception.details["action"])
        self.assertFalse((self.root / "out/검토.hwpx").exists())
        self.assertFalse((self.root / "argv-generate.json").exists(),
                         "모호하면 생성기를 부르기 전에 멈춘다")

    # --- 진짜 CLI가 두 단계를 넘기는가(R-01 회귀 시험) --------------------

    def test_cli_builds_figure_beside_subfolder_manuscript(self):
        """R-01①: `--in build/body.md`에서 원고 옆 그림만 있을 때 그 바이트가
        DOCX에 박힌다 — CLI가 `base`(원고 폴더)와 뿌리를 갈라 넘긴다는 증거다.

        원고 목적지는 `farm.png`(폴더 없음)다. 뿌리만 보는 호출(base=뿌리)이면
        찾지 못해 종료 2가 되고, 원고 옆을 보지 않으면 마찬가지다 — 그림이
        들어오려면 원고 폴더가 첫 후보여야 한다.
        """
        beside = self.picture("build/farm.png")
        text = manuscript("farm.png")
        result = self.build_cli(text, "build/beside.docx", None,
                                inp="build/body.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = self.root / "build" / "beside.docx"
        self.assertEqual(self.cli_pictures(out), 1)
        self.assertIn(beside.read_bytes(), self.cli_media(out))

    def test_cli_builds_figure_from_project_root_under_subfolder_manuscript(self):
        """R-01 보강: 원고 옆에 없고 뿌리에만 있는 그림도 CLI 빌드에서 들어온다.

        `--in build/body.md`는 원고 폴더가 뿌리와 다르므로, CLI가 `root`를 함께
        넘기지 않으면(`convert(..., src.parent)`만) 이 그림을 못 찾아 종료 2가
        된다 — ①은 원고 옆 다리, 이 시험은 뿌리 다리를 잠근다.
        """
        rooted = self.picture("figures/public/farm.png")
        result = self.build_cli(manuscript("figures/public/farm.png"),
                                "build/rooted.docx", None, inp="build/body.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = self.root / "build" / "rooted.docx"
        self.assertEqual(self.cli_pictures(out), 1)
        self.assertIn(rooted.read_bytes(), self.cli_media(out))

    def test_cli_stops_with_both_paths_when_beside_and_root_differ(self):
        """R-01②: 원고 옆과 뿌리에 같은 이름의 다른 그림이 있으면 종료 2·
        두 경로 안내·결과 미발행 — CLI가 뿌리(`root`)도 함께 넘긴다는 증거다.

        `root`를 빼고 원고 폴더 하나만 넘기면 원고 옆 후보 하나만 보여
        조용히 DOCX가 만들어지고, `base`를 뿌리로 되돌리면 원고 옆 그림을
        아예 못 봐 두 후보 안내가 나오지 않는다 — 이 시험이 두 변이를 모두 막는다.
        """
        beside = self.picture("build/figures/public/farm.png", PNG + b"beside")
        rooted = self.picture("figures/public/farm.png", PNG + b"root")
        self.assertNotEqual(beside.read_bytes(), rooted.read_bytes())
        sentence = self.gh.ambiguous_image_message(
            ["build/figures/public/farm.png", "figures/public/farm.png"])

        result = self.build_cli(manuscript("figures/public/farm.png"),
                                "build/ambiguous.docx", None,
                                inp="build/body.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"], "ValueError: " + sentence)
        # 안내에 두 후보 경로가 원고 옆→뿌리 순서로 그대로 보인다
        # (어느 쪽도 조용히 고르지 않는다).
        self.assertIn("build/figures/public/farm.png, "
                      "figures/public/farm.png", payload["reason"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / "build" / "ambiguous.docx").exists())
        self.assertFalse(
            (self.root / "build" / "ambiguous.manifest.json").exists())

    # --- 앱 그림 토큰은 위치와 무관하게 저장고에서만 ----------------------

    def test_app_token_resolves_from_the_store_not_from_the_manuscript_folder(self):
        """원고가 `build/` 아래에 있어도 토큰은 저장고에서 풀린다(위치 무관).

        검사·DOCX는 토큰을 통과시키고 저장고 바이트를 넣는다(HWPX는 토큰을 풀
        수 없어 거절하므로 여기서 부르지 않는다 — 아래 시험과 어댑터 시험이
        잠근다).
        """
        text = manuscript("app-figure:" + SHA)
        write_text(self.root, "build/본문.md", text)
        with self.at_home(self.app_home()):
            self.assertEqual(self.missing_image(text), [])
            doc = self.convert(text)
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertEqual([PNG], self.media(doc, "token.docx"))

    def test_app_token_from_subfolder_still_blocks_hwpx(self):
        """원고가 하위 폴더에 있어도 토큰은 HWPX에서 거절된다(생성기 호출 전)."""
        write_text(self.root, "build/본문.md", manuscript("app-figure:" + SHA))
        with self.assertRaises(self.gh.HwpxBlocked) as ctx:
            self.build_hwpx()
        self.assertEqual("app_figure_unsupported", ctx.exception.reason)
        self.assertEqual(["app-figure:" + SHA], ctx.exception.details["images"])
        self.assertFalse((self.root / "argv-generate.json").exists())


if __name__ == "__main__":
    unittest.main()
