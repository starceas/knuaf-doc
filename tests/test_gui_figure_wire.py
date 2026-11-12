"""GF2: 규칙집 그림 명령·docx 그림 검사가 입구 세 곳에 붙었는지 잠근다.

- `gg.py figure …` — argparse 전에 gg_figure로 넘긴다.
- `gg_document.check` — `app-figure:<sha256>` 토큰을 파일 없음으로 오판하지 않는다.
- `build_docx` — 토큰을 실제 그림으로 넣고, 증명이 없으면 빌드를 멈춘다.
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
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, SCRIPTS, bind_major, runtime, write_text

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x06\x00\x00\x00" + b"\x1f\x15\xc4\x89"
       + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
         b"\r\n-\xb4" + b"\x00\x00\x00\x00IEND\xaeB`\x82")
SHA = hashlib.sha256(PNG).hexdigest()
CAPTION = "그림 1. 재배 기술 (출처 : 농촌진흥청)"
FIGURE = "## 가. 절\n\n![%s](app-figure:%s)\n\n%s\n" % (CAPTION, SHA, CAPTION)


class GuiFigureWireTests(ContractCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_project()
        bind_major(self.root, "specialty_crops")
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")
        self.builder = runtime("build_docx")
        self.docmod = runtime("gg_document")
        self.gd = runtime("gg_figure_docx")

    # --- 대역 앱 저장고 ---------------------------------------------------

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

    def convert(self, text):
        return self.builder.convert(text, base=str(self.root),
                                    context=self.context)

    def missing_image(self, text):
        return [i for i in self.docmod.check(text, str(self.root))
                if i[0] == "missing_image"]

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
            capture_output=True, text=True, encoding="utf-8", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_photo")

    def test_manifest_carries_figures_report(self):
        with self.at_home(self.app_home()):
            write_text(self.root, "body.md", FIGURE)
            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPTS / "build_docx.py"),
                 str(self.root), "--in", "body.md", "--out", "build/f.docx",
                 "--major", "specialty_crops"],
                capture_output=True, text=True, encoding="utf-8", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "build" / "f.manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual([item["kind"] for item in manifest["figures"]["images"]],
                         ["app"])
        self.assertEqual(manifest["figures"]["student_count"], 0)
        self.assertEqual(manifest["figures"]["blocked"], [])


if __name__ == "__main__":
    unittest.main()
