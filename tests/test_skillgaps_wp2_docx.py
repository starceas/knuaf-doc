"""WP-2 DOCX 결함 잠금 시험 — G3-06(긴 URL 표시)·G3-07(해시 재계산)·G3-08(cwd).

생성된 DOCX/매니페스트와 실제 파일시스템 경계만 본다.
"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from tests._harness import (
    ContractCase, bind_major, runtime, script_source, text_calls_without_encoding,
)

LONG_URL = ("https://www.nongsaro.go.kr/portal/ps/psv/psvr/psvre/"
            "curationDtl.ps?menuId=PS00001&cntntsNo=1941&pageUnit=10&"
            "searchKeyword=leafy")
SHORT_URL = "https://example.go.kr/short"

URL_MD = (
    "Ⅰ. 머리말\n"
    "\n"
    "참고 자료는 " + LONG_URL + " 이다.\n"
    "\n"
    "짧은 링크 " + SHORT_URL + " 도 남는다.\n"
)

def bound_context(case):
    project = case.make_project()
    bind_major(project)
    return runtime("gg_major_contract").output_context(project, "specialty_crops")

def paragraph_texts(doc):
    return [p.text for p in doc.paragraphs]

class LongUrlDisplayTests(ContractCase):
    """G3-06: 65자 초과 URL이 인쇄 문자열 '원문 링크'로 바뀌어 주소가
    사라졌다. 링크는 유지하되 표시 글자는 URL이어야 한다."""

    def test_long_url_keeps_address_in_printed_text(self):
        build = runtime("build_docx")
        doc = build.convert(URL_MD, context=bound_context(self))
        text = "\n".join(paragraph_texts(doc))
        self.assertNotIn("원문 링크", text)
        self.assertIn(LONG_URL, text)
        self.assertIn(SHORT_URL, text)

    def test_long_url_is_still_an_external_hyperlink(self):
        build = runtime("build_docx")
        doc = build.convert(URL_MD, context=bound_context(self))
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive:
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            document = archive.read("word/document.xml").decode("utf-8")
        self.assertIn(LONG_URL.replace("&", "&amp;"), rels)
        self.assertIn('TargetMode="External"', rels)
        self.assertIn("<w:hyperlink", document)
        self.assertIn(LONG_URL.replace("&", "&amp;"), document)

class RehashTests(ContractCase):
    """G3-07: 후처리로 다시 저장한 DOCX와 매니페스트 해시가 어긋난 채
    남았고 재계산 경로가 없었다."""

    def setUp(self):
        super().setUp()
        self.build = runtime("build_docx")
        self.core = runtime("gg_core")
        self.root = self.make_project()
        bind_major(self.root)
        (Path(self.root) / "body.md").write_text(URL_MD, encoding="utf-8")

    def _build(self, out="out.docx"):
        result = subprocess.run(
            [sys.executable, "-B",
             str(runtime("build_docx").__file__), str(self.root),
             "--in", "body.md", "--out", out, "--major", "specialty_crops"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return (Path(self.root) / out,
                (Path(self.root) / out).with_suffix(".manifest.json"))

    def _post_process(self, target):
        """한글에서 쪽 나눔을 넣고 다시 저장한 상황을 재현한다."""
        original = target.read_bytes()
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as zin, \
                zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/document.xml":
                    data = data.replace(b"</w:body>",
                                        b'<w:p><w:r><w:br w:type="page"/></w:r></w:p></w:body>')
                zout.writestr(info, data)
        target.write_bytes(buffer.getvalue())
        self.assertNotEqual(original, target.read_bytes())

    def test_manifest_hash_is_marked_as_build_stage(self):
        target, manifest_path = self._build()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("build", manifest["file_hash_stage"])
        self.assertEqual(self.core.digest(target.read_bytes()),
                         manifest["file_hash"])

    def test_rehash_updates_stale_hash_and_records_history(self):
        target, manifest_path = self._build()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale = manifest["file_hash"]
        self._post_process(target)
        self.assertNotEqual(stale, self.core.digest(target.read_bytes()))

        result = subprocess.run(
            [sys.executable, "-B", str(runtime("build_docx").__file__),
             str(self.root), "--rehash", "out.docx"],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("rehashed", payload["status"])
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(self.core.digest(target.read_bytes()),
                         updated["file_hash"])
        self.assertEqual("post_processed", updated["file_hash_stage"])
        self.assertEqual([stale], [item["from"] for item in updated["rehash"]])
        self.assertEqual([updated["file_hash"]],
                         [item["to"] for item in updated["rehash"]])
        self.assertEqual(manifest["input_hash"], updated["input_hash"])
        self.assertEqual(manifest["notice"], updated["notice"])

    def test_rehash_is_idempotent_and_keeps_single_history_entry(self):
        target, manifest_path = self._build()
        self._post_process(target)
        script = [sys.executable, "-B", str(runtime("build_docx").__file__),
                  str(self.root), "--rehash", "out.docx"]
        first = subprocess.run(script, capture_output=True, text=True)
        second = subprocess.run(script, capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual("rehashed", json.loads(first.stdout)["status"])
        self.assertEqual("unchanged", json.loads(second.stdout)["status"])
        updated = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(updated["rehash"]))

    def test_rehash_refuses_missing_or_foreign_manifest(self):
        target, manifest_path = self._build("other.docx")
        manifest_path.unlink()
        missing = subprocess.run(
            [sys.executable, "-B", str(runtime("build_docx").__file__),
             str(self.root), "--rehash", "other.docx"],
            capture_output=True, text=True)
        self.assertEqual(2, missing.returncode)
        self.assertIn("매니페스트 없음", missing.stdout)

        manifest_path.write_text(json.dumps({"kind": "other"}),
                                 encoding="utf-8")
        foreign = subprocess.run(
            [sys.executable, "-B", str(runtime("build_docx").__file__),
             str(self.root), "--rehash", "other.docx"],
            capture_output=True, text=True)
        self.assertEqual(2, foreign.returncode)
        self.assertIn("매니페스트가 아님", foreign.stdout)
        self.assertEqual({"kind": "other"},
                         json.loads(manifest_path.read_text(encoding="utf-8")))

class WorkingDirectoryTests(ContractCase):
    """G3-08: cwd 조회가 EPERM이면 절대 경로 실행도 죽었다."""

    def setUp(self):
        super().setUp()
        self.build = runtime("build_docx")

    def _without_cwd(self, work):
        original = os.getcwd
        os.getcwd = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
        self.addCleanup(setattr, os, "getcwd", original)
        try:
            return work()
        finally:
            os.getcwd = original

    def test_absolute_folder_needs_no_cwd_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = self._without_cwd(
                lambda: self.build.base_dir(str(Path(tmp).resolve())))
        self.assertTrue(Path(resolved).is_absolute())

    def test_relative_folder_falls_back_to_shell_pwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PWD": tmp}):
                with mock.patch("sys.stderr", new=io.StringIO()) as stderr:
                    resolved = self._without_cwd(lambda: self.build.base_dir("."))
                    note = stderr.getvalue()
        self.assertEqual(os.path.realpath(tmp), os.path.realpath(resolved))
        self.assertIn("PWD", note)

    def test_relative_folder_without_pwd_names_the_fix(self):
        with mock.patch.dict(os.environ, {"PWD": ""}, clear=False):
            with self.assertRaises(ValueError) as caught:
                self._without_cwd(lambda: self.build.base_dir("."))
        self.assertIn("절대 경로", str(caught.exception))

    def test_cli_builds_from_relative_folder_when_cwd_is_unreadable(self):
        root = self.make_project()
        bind_major(root)
        (Path(root) / "body.md").write_text(URL_MD, encoding="utf-8")
        code = (
            "import os,sys\n"
            "sys.path.insert(0, %r)\n"
            "def boom(): raise PermissionError(1, 'Operation not permitted')\n"
            "os.getcwd = boom\n"
            "import build_docx\n"
            "sys.exit(build_docx.main(['.', '--in', 'body.md', '--out',"
            " 'rel.docx', '--major', 'specialty_crops']))\n"
            % str(Path(runtime("build_docx").__file__).parent)
        )
        env = dict(os.environ, PWD=str(root))
        result = subprocess.run([sys.executable, "-B", "-c", code],
                                cwd=root, capture_output=True, text=True,
                                env=env)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((Path(root) / "rel.docx").is_file())
        self.assertTrue((Path(root) / "rel.manifest.json").is_file())
        self.assertIn("PWD", result.stderr)


class ConventionTests(unittest.TestCase):
    def test_text_io_and_execution_guards(self):
        source = script_source("build_docx.py")
        self.assertEqual([], text_calls_without_encoding(source))
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)

if __name__ == "__main__":
    unittest.main()
