"""#123 그림 넣기 → 분할 → DOCX·HWPX 상호운용 계약 시험.

`gg_figure.place`가 학생 원고에 직접 쓰는 그림 줄의 목적지 모양을 고정하고,
그 원고를 `gg_draft_split`으로 절 분할한 뒤에도 같은 그림이 DOCX
(`gg_document.parse` + `build_docx` 기준과 같은 뿌리)와 HWPX
(`gg_hwpx.resolve_images`, `build()` 기본 image-dir = 작업 폴더 뿌리)에서
살아 있는지 한 번에 확인한다.

합성 데이터만 쓴다(실제 학생 원고·사진 아님). skip 없음.
"""

import hashlib
import io
import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from docx import Document

from tests._harness import ContractCase, bind_major, runtime, write_text

fig = runtime("gg_figure")
split = runtime("gg_draft_split")
doc = runtime("gg_document")
gh = runtime("gg_hwpx")

HEADING = "# Ⅰ. 머리말"
TOC = [{"id": "I", "title": "머리말",
        "patterns": [r"^\s*#?\s*[ⅠI]\s*[\.．]?\s*머리말"]}]
ORG = "테스트기관"


def png_bytes(width=3, height=3):
    """실제로 해독되는 작은 PNG(DOCX/HWPX가 바이트를 확인하는 경로용)."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FigureInterop(unittest.TestCase):
    """그림 삽입 도구가 쓴 목적지는 분할·빌드 끝까지 그대로 살아야 한다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-figure-interop-")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name) / "work"
        self.work.mkdir()
        body = png_bytes()
        self.sha256 = hashlib.sha256(body).hexdigest()
        # 그림 넣기(gg_figure.place)가 확인하는 자리와 같은 곳에 둔다.
        self.figure_rel = "figures/public/%s.png" % self.sha256
        target = self.work / self.figure_rel
        target.parent.mkdir(parents=True)
        target.write_bytes(body)

    def _insert_figure(self):
        """#123 명령 기본값(build/검토전_본문.md) 그대로 그림을 넣는다."""
        manuscript = "build/검토전_본문.md"
        path = self.work / manuscript
        path.parent.mkdir(parents=True)
        path.write_text(HEADING + "\n본문 문장.\n", encoding="utf-8")
        result = fig.place(
            self.work, "1", HEADING, manuscript=manuscript,
            images=[self.figure_rel], org=ORG, direct=True)
        self.assertEqual("placed", result.get("status"), result)
        # 삽입된 목적지 모양을 고정한다 — 이 시험이 지키려는 입력 형태다.
        self.assertEqual(self.figure_rel, result.get("ref"))
        text = path.read_text(encoding="utf-8")
        # 목적지 표기 자체만 고정한다 — 제목·캡션 문구는 이 계약의 대상이 아니다.
        self.assertIn("(출처 : %s)](%s)" % (ORG, self.figure_rel), text, text)
        return path, text

    def test_figure_inserted_by_gg_figure_survives_split_and_build(self):
        source, text = self._insert_figure()
        toc_path = write_text(self.work, "toc.json",
                              json.dumps(TOC, ensure_ascii=False))
        plan = split.build_plan(text.splitlines(), split.load_toc(toc_path),
                                src_path=source, fmt="text",
                                workdir=self.work)
        result = split.apply_plan(self.work, plan, text.splitlines(),
                                  source_path=source)
        self.assertEqual([], result["skipped"],
                         "그림 삽입 직후 분할이 그림을 잃었다: %r"
                         % (result["skipped"],))
        self.assertEqual(["sections/I.md"], result["written"])

        section = (self.work / "sections" / "I.md").read_text(encoding="utf-8")
        self.assertIn("](%s)" % self.figure_rel, section, section)

        # DOCX: build_docx.convert는 base=작업 폴더 뿌리로 경로를 푼다.
        images = [n for n in doc.parse(section) if n["kind"] == "image"]
        self.assertEqual([self.figure_rel], [n["path"] for n in images])
        self.assertTrue((self.work / images[0]["path"]).is_file())

        # HWPX: build()의 기본 image-dir는 작업 폴더 뿌리다.
        references, missing = gh.resolve_images(section, self.work)
        self.assertEqual([self.figure_rel], references)
        self.assertEqual([], missing, "HWPX가 그림을 못 찾는다: %r" % (missing,))


class FigureInteropDocx(ContractCase):
    """R-02: 그림이 실제 DOCX 안에 들어갔는지 생성물에서 센다.

    parse 결과의 image 노드만 보면 "경로는 맞는데 파일이 안 들어갔다"를 놓친다.
    여기서는 build_docx.convert로 real DOCX를 만들어 word/media 항목 수와
    inline_shapes 수를 센다 — 퍼센트 인코딩된 이름도 같은 경로로 확인한다.
    """

    def setUp(self):
        super().setUp()
        self.root = self.make_project()
        bind_major(self.root, "specialty_crops")
        self.builder = runtime("build_docx")
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")

    def _embed(self, text):
        """(문서, ZIP 안 word/media 항목 이름들) — 실제 생성물에서 센 값."""
        document = self.builder.convert(text, base=self.root,
                                        context=self.context)
        stream = io.BytesIO()
        document.save(stream)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            media = sorted(name for name in archive.namelist()
                           if name.startswith("word/media/"))
        stream.seek(0)
        return Document(stream), media

    def _picture(self, rel, body=png_bytes()):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target

    def test_destination_that_parses_also_lands_in_the_docx(self):
        self._picture("figures/public/aaaa.png")
        document, media = self._embed(
            "![밭 사진](figures/public/aaaa.png)\n그림 1. 밭 사진\n")
        self.assertEqual(1, len(document.inline_shapes))
        self.assertEqual(["word/media/image1.png"], media)

    def test_percent_25_name_is_the_same_file_in_docx_and_hwpx(self):
        """``%25``는 ``%``다: 두 출력이 같은 실제 파일을 골라야 한다."""
        self._picture("figures/a%photo.png")
        text = "![특수](figures/a%25photo.png)\n그림 1. 특수 문자\n"
        # DOCX: 파싱한 경로가 실제 파일이고, 생성물에 그림이 하나 들어간다.
        path = [n["path"] for n in doc.parse(text) if n["kind"] == "image"]
        self.assertEqual(["figures/a%photo.png"], path)
        self.assertTrue((self.root / path[0]).is_file())
        document, media = self._embed(text)
        self.assertEqual(1, len(document.inline_shapes))
        self.assertEqual(1, len(media))
        # HWPX: 해석기도 같은 파일을 고르고, 생성기로 넘길 표기는 %25로 남는다.
        references, missing = gh.resolve_images(text, self.root)
        self.assertEqual(["figures/a%photo.png"], references)
        self.assertEqual([], missing)
        rewritten, problems = gh.rewrite_image_destinations(
            text, self.root, self.root)
        self.assertEqual([], problems)
        self.assertIn("(figures/a%25photo.png)", rewritten)
        # kordoc로 넘기는 단일 인코딩 표기도 같은 파일을 가리킨다.
        self.assertIn("(figures/a%25photo.png)",
                      gh.normalize_image_destinations(rewritten))


if __name__ == "__main__":
    unittest.main()
