"""초안 봉투(gg:draft 마커 + 메타 블록)가 HWPX·DOCX 본문에 찍히지 않는 시험.

- 봉투 든 입력: 발행기에 넘어가는 본문·DOCX 본문에 마커·메타 글자가 없다.
- 마커 없는 입력: 원문 그대로(매니페스트에 draft_envelope 키 없음).
- 깨진 마커: 조용히 넘기지 않고 오류.
- 입력 해시는 원 파일 바이트 해시를 유지하고, 뗀 본문 해시는 draft_envelope에 남는다.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime
from tests.test_build_hwpx import BODY_MD, SPECIALTY, _Base, make_fake_cli

START = "<!-- gg:draft:start -->"
END = "<!-- gg:draft:end -->"
META = "## STATUS\n- 상태: 초안\n\n## OPEN\n- 확인 필요 항목\n"
ENVELOPE_MD = (
    "# 합성 제목\n\n" + META + "\n## DRAFT\n" + START + "\n" + BODY_MD + "\n"
    + END + "\n\n## FACTS\n- 메타 사실\n"
)
BROKEN_MD = "# 합성 제목\n\n" + START + "\n" + BODY_MD + "\n"  # 끝 마커 없음
AFTER_MD = (START + "\n" + BODY_MD + "\n" + END
            + "\n\n## 결론\n\n여기가 사라지는 본문 문단이다.\n")
BEFORE_MD = ("서문 문단이다.\n\n" + START + "\n" + BODY_MD + "\n" + END + "\n")
QUOTED_MD = ("앞 문단이다.\n\n본문에서 `" + START + "` 와 `" + END
             + "` 를 인용한다.\n\n뒤 문단이다.\n")
OUTSIDE_CASES = (
    ("after", AFTER_MD, "사라지는"),
    ("before", BEFORE_MD, "서문 문단"),
    ("quoted", QUOTED_MD, "앞 문단"),
)


class HwpxEnvelopeTests(_Base):
    def setUp(self):
        super().setUp()
        self.root = self._project()

    def _export(self, name, raw, out):
        (Path(self.root) / name).write_bytes(raw)
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        gh = runtime("gg_hwpx")
        seen = {}
        real = gh.build

        def spy(source, *args, **kw):
            seen["path"] = Path(source)
            seen["bytes"] = Path(source).read_bytes()
            return real(source, *args, **kw)

        runtime("build_hwpx")
        with mock.patch.object(gh, "build", spy):
            value = runtime("build_hwpx").export(
                self.root, name, out, cli_command=cli,
                context=self._ctx(self.root, SPECIALTY))
        return value, seen

    def test_envelope_is_removed_before_build(self):
        raw = ENVELOPE_MD.encode("utf-8")
        value, seen = self._export("env.md", raw, "env.hwpx")
        fed = seen["bytes"].decode("utf-8")
        for needle in (START, END, "## STATUS", "## OPEN", "## FACTS",
                       "메타 사실", "확인 필요 항목"):
            self.assertNotIn(needle, fed)
        self.assertIn("본문 첫 문단이다.", fed)
        # 임시 본문은 지워지고, 원고는 그대로다.
        self.assertFalse(seen["path"].exists())
        self.assertEqual(raw, (Path(self.root) / "env.md").read_bytes())
        manifest = value["manifest"]
        self.assertEqual(self.core.digest(raw), manifest["input_hash"])
        self.assertEqual(
            self.core.digest(self.core.draft(ENVELOPE_MD).encode("utf-8")),
            manifest["draft_envelope"]["body_hash"])

    def test_plain_input_is_passed_through_unchanged(self):
        raw = BODY_MD.encode("utf-8")
        value, seen = self._export("plain.md", raw, "plain.hwpx")
        self.assertEqual(raw, seen["bytes"])
        self.assertEqual((Path(self.root) / "plain.md").resolve(), seen["path"])
        self.assertNotIn("draft_envelope", value["manifest"])
        self.assertEqual(self.core.digest(raw), value["manifest"]["input_hash"])

    def test_bom_input_is_accepted(self):
        raw = b"\xef\xbb\xbf" + ENVELOPE_MD.encode("utf-8")
        _, seen = self._export("bom.md", raw, "bom.hwpx")
        self.assertNotIn(START, seen["bytes"].decode("utf-8"))

    def test_broken_marker_is_an_error(self):
        (Path(self.root) / "bad.md").write_text(BROKEN_MD, encoding="utf-8")
        cli = make_fake_cli(self.root, markdown=BODY_MD)
        with self.assertRaises(ValueError) as caught:
            runtime("build_hwpx").export(
                self.root, "bad.md", "bad.hwpx", cli_command=cli,
                context=self._ctx(self.root, SPECIALTY))
        self.assertIn("DRAFT 경계 손상", str(caught.exception))
        self.assertFalse((Path(self.root) / "bad.hwpx").exists())

    def test_text_outside_markers_is_an_error(self):
        for key, md, needle in OUTSIDE_CASES:
            with self.subTest(case=key):
                name = key + ".md"
                (Path(self.root) / name).write_text(md, encoding="utf-8")
                cli = make_fake_cli(self.root, markdown=BODY_MD)
                with self.assertRaises(ValueError) as caught:
                    runtime("build_hwpx").export(
                        self.root, name, key + ".hwpx", cli_command=cli,
                        context=self._ctx(self.root, SPECIALTY))
                self.assertIn("DRAFT 마커 밖", str(caught.exception))
                self.assertIn(needle, str(caught.exception))
                names = [p.name for p in Path(self.root).iterdir()]
                self.assertNotIn(key + ".hwpx", names)
                self.assertFalse([n for n in names if n.endswith(".body.md")
                                  or "layout-staging" in n
                                  or n.startswith(key + ".hwpx")])


class DocxEnvelopeTests(ContractCase):
    def setUp(self):
        super().setUp()
        self.core = runtime("gg_core")
        self.root = self.make_project()
        bind_major(self.root)

    def _run(self, name, raw, out):
        (Path(self.root) / name).write_bytes(raw)
        return subprocess.run(
            [sys.executable, "-B", str(runtime("build_docx").__file__),
             str(self.root), "--in", name, "--out", out,
             "--major", "specialty_crops"],
            capture_output=True, text=True, encoding="utf-8")

    def _document_text(self, out):
        with zipfile.ZipFile(Path(self.root) / out) as archive:
            return archive.read("word/document.xml").decode("utf-8")

    def test_envelope_text_is_not_in_docx(self):
        raw = ENVELOPE_MD.encode("utf-8")
        result = self._run("env.md", raw, "env.docx")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        xml = self._document_text("env.docx")
        for needle in ("gg:draft", "STATUS", "메타 사실", "확인 필요 항목"):
            self.assertNotIn(needle, xml)
        self.assertIn("본문 첫 문단이다.", xml)
        manifest = json.loads(
            (Path(self.root) / "env.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(self.core.digest(raw), manifest["input_hash"])
        self.assertEqual(
            self.core.digest(self.core.draft(ENVELOPE_MD).encode("utf-8")),
            manifest["draft_envelope"]["body_hash"])

    def test_plain_input_has_no_envelope_key(self):
        result = self._run("plain.md", BODY_MD.encode("utf-8"), "plain.docx")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        manifest = json.loads(
            (Path(self.root) / "plain.manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("draft_envelope", manifest)

    def test_bom_input_is_accepted(self):
        raw = b"\xef\xbb\xbf" + ENVELOPE_MD.encode("utf-8")
        result = self._run("bom.md", raw, "bom.docx")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_broken_marker_is_blocked(self):
        result = self._run("bad.md", BROKEN_MD.encode("utf-8"), "bad.docx")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("DRAFT 경계 손상", result.stdout + result.stderr)
        self.assertFalse((Path(self.root) / "bad.docx").exists())

    def test_text_outside_markers_is_blocked(self):
        for key, md, needle in OUTSIDE_CASES:
            with self.subTest(case=key):
                result = self._run(key + ".md", md.encode("utf-8"),
                                   key + ".docx")
                self.assertNotEqual(0, result.returncode)
                text = result.stdout + result.stderr
                self.assertIn("DRAFT 마커 밖", text)
                self.assertIn(needle, text)
                self.assertFalse((Path(self.root) / (key + ".docx")).exists())
                self.assertFalse(
                    (Path(self.root) / (key + ".manifest.json")).exists())
