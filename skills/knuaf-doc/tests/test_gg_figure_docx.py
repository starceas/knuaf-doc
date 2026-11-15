"""gg_figure_docx — 앱 승인 토큰 해석·학생 그림 목록 (P1).

가짜 앱 저장소(KNUAF_FIGURE_HOME)를 임시 폴더에 만들고 receipts.jsonl과
store/<sha256>.png만으로 판정한다. 외부 접속 없음.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(SCRIPTS))
import gg_figure_docx as gd  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x06\x00\x00\x00" + b"\x1f\x15\xc4\x89"
       + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
         b"\r\n-\xb4" + b"\x00\x00\x00\x00IEND\xaeB`\x82")
SHA = hashlib.sha256(PNG).hexdigest()
OTHER = hashlib.sha256(PNG + b"other").hexdigest()


def token(sha):
    return "app-figure:" + sha


def _env_none():
    """환경변수가 비어 있는 상태(앱 밖)를 명시적으로 만든다."""
    return gd.figure_home({})


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.receipts = self.home / "receipts.jsonl"

    def store(self, sha=SHA, body=PNG):
        path = self.home / "store" / (sha + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def receipt(self, sha=SHA, verdict="approved"):
        line = {
            "v": 1, "at": "2026-11-11T10:00:00Z", "reqId": "req-1",
            "folderHash": "0123456789abcdef", "slot": "s1",
            "page": {"url": "https://www.nongsaro.go.kr/x", "sha256": OTHER,
                     "kogl": 2, "org": "농촌진흥청"},
            "image": {"sha256": sha, "verdict": verdict, "kogl": 2,
                      "org": "농촌진흥청", "ruleId": "nongsaro-curation"},
        }
        with self.receipts.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line


class TokenShapeTests(unittest.TestCase):
    def test_token_sha(self):
        self.assertEqual(gd.token_sha(token(SHA)), SHA)
        self.assertEqual(gd.token_sha("  " + token(SHA) + "  "), SHA)
        self.assertIsNone(gd.token_sha("figures/student.jpg"))
        self.assertIsNone(gd.token_sha("app-figure:" + SHA.upper()))
        self.assertIsNone(gd.token_sha("app-figure:" + SHA[:-1]))
        self.assertIsNone(gd.token_sha(""))

    def test_placeholder_and_notice_text(self):
        self.assertEqual(gd.PLACEHOLDER, "앱에서 받은 그림")
        self.assertEqual(gd.student_notice(1),
                         "학생이 넣은 그림 1장 — 출처·권리는 학생 확인")

    def test_figure_home_from_env(self):
        self.assertIsNone(gd.figure_home({}))
        self.assertEqual(gd.figure_home({"KNUAF_FIGURE_HOME": "/tmp/f"}),
                         Path("/tmp/f"))


class ResolveTokenTests(_HomeCase):
    def test_approved_with_matching_bytes(self):
        stored = self.store()
        self.receipt()
        path, issue = gd.resolve_token(SHA, self.home)
        self.assertIsNone(issue)
        self.assertEqual(path, stored)
        self.assertEqual(gd.digest_file(path), SHA)

    def test_receipt_ambiguous(self):
        self.store()
        self.receipt(verdict="ambiguous")
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_receipt_rejected(self):
        self.store()
        self.receipt(verdict="rejected")
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_bytes_altered(self):
        self.store(body=PNG + b"tamper")
        self.receipt()
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_store_file_missing(self):
        self.receipt()
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_no_receipt(self):
        self.store()
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_last_line_wins(self):
        self.store()
        self.receipt(verdict="approved")
        self.receipt(verdict="rejected")
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_last_line_wins_approved(self):
        self.store()
        self.receipt(verdict="rejected")
        self.receipt(verdict="approved")
        path, issue = gd.resolve_token(SHA, self.home)
        self.assertIsNone(issue)
        self.assertEqual(path, self.home / "store" / (SHA + ".png"))

    def test_other_sha_receipt_does_not_cover(self):
        self.store()
        self.receipt(sha=OTHER)
        self.assertEqual(gd.resolve_token(SHA, self.home),
                         (None, "figure_unverified"))

    def test_bad_sha_argument(self):
        self.assertEqual(gd.resolve_token("nope", self.home),
                         (None, "figure_unverified"))

    def test_broken_receipt_line_skipped(self):
        self.store()
        with self.receipts.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        self.receipt()
        path, issue = gd.resolve_token(SHA, self.home)
        self.assertIsNone(issue)
        self.assertEqual(path, self.home / "store" / (SHA + ".png"))

    def test_home_defaults_to_env(self):
        self.store()
        self.receipt()
        with mock.patch.dict(os.environ,
                             {"KNUAF_FIGURE_HOME": str(self.home)}):
            path, issue = gd.resolve_token(SHA)
        self.assertIsNone(issue)
        self.assertEqual(path, self.home / "store" / (SHA + ".png"))


class NodeTargetTests(_HomeCase):
    def test_student_path_is_not_a_token(self):
        self.assertEqual(gd.node_target("figures/x.jpg"), (None, None))

    def test_token_outside_app(self):
        self.assertEqual(gd.node_target(token(SHA), home=None), (None, None))

    def test_token_verified_inside_app(self):
        self.store()
        self.receipt()
        path, issue = gd.node_target(token(SHA), home=self.home)
        self.assertIsNone(issue)
        self.assertEqual(path, self.home / "store" / (SHA + ".png"))

    def test_env_used_when_home_omitted(self):
        self.store()
        self.receipt()
        with mock.patch.dict(os.environ,
                             {"KNUAF_FIGURE_HOME": str(self.home)}):
            path, issue = gd.node_target(token(SHA))
        self.assertIsNone(issue)
        self.assertEqual(path, self.home / "store" / (SHA + ".png"))

    def test_placeholder_only_outside_app(self):
        """앱 밖 토큰만 자리 표시다 — 안에서는 진짜 경로/차단으로 판정한다."""
        self.store()
        self.receipt()
        self.assertTrue(gd.is_placeholder(token(SHA), home=None))
        self.assertFalse(gd.is_placeholder(token(SHA), home=self.home))
        self.assertFalse(gd.is_placeholder("figures/x.jpg", home=None))
        self.assertTrue(gd.is_placeholder(token(SHA), home=_env_none()))


class ImageCheckTests(_HomeCase):
    """I-6: gg_document.check의 missing_image 가지가 토큰을 오류로 만들지 않게."""

    def test_student_path_is_left_to_the_file_check(self):
        self.assertEqual(gd.image_check("figures/x.jpg"), (False, None))

    def test_token_outside_app_passes_as_placeholder(self):
        self.assertEqual(gd.image_check(token(SHA), home=None), (True, None))

    def test_token_verified_inside_app_passes(self):
        self.store()
        self.receipt()
        self.assertEqual(gd.image_check(token(SHA), home=self.home), (True, None))

    def test_unverified_token_reports_issue(self):
        self.assertEqual(gd.image_check(token(SHA), home=self.home),
                         (True, "figure_unverified"))


class BuildReportTests(_HomeCase):
    def test_student_figure_listed_and_not_blocked(self):
        self.store()
        self.receipt()
        report = gd.build_report([token(SHA), "figures/student.jpg"],
                                 home=self.home)
        self.assertEqual(report["student_count"], 1)
        self.assertEqual(report["student"], ["figures/student.jpg"])
        self.assertEqual(report["student_notice"],
                         "학생이 넣은 그림 1장 — 출처·권리는 학생 확인")
        self.assertEqual(report["blocked"], [])
        kinds = [item["kind"] for item in report["images"]]
        self.assertEqual(kinds, ["app", "student"])

    def test_verified_token_is_app_image(self):
        self.store()
        self.receipt()
        report = gd.build_report([token(SHA)], home=self.home)
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["images"][0]["kind"], "app")
        self.assertEqual(report["images"][0]["path"],
                         str(self.home / "store" / (SHA + ".png")))

    def test_unverified_token_blocks(self):
        self.store(body=PNG + b"tamper")
        self.receipt()
        report = gd.build_report([token(SHA)], home=self.home)
        self.assertEqual([entry["reason"] for entry in report["blocked"]],
                         ["figure_unverified"])
        self.assertEqual(report["images"][0]["kind"], "blocked")

    def test_outside_app_leaves_placeholder(self):
        report = gd.build_report([token(SHA), "figures/student.jpg"],
                                 home=None)
        self.assertEqual(report["placeholders"], [token(SHA)])
        self.assertEqual(report["placeholder_text"], "앱에서 받은 그림")
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["student_count"], 1)
        self.assertEqual(report["images"][0]["kind"], "placeholder")

    def test_env_home_used_when_home_omitted(self):
        self.store()
        self.receipt()
        with mock.patch.dict(os.environ,
                             {"KNUAF_FIGURE_HOME": str(self.home)}):
            report = gd.build_report([token(SHA)])
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["images"][0]["kind"], "app")

    def test_student_only_build(self):
        report = gd.build_report(["figures/a.png", "figures/b.png"],
                                 home=self.home)
        self.assertEqual(report["student_count"], 2)
        self.assertEqual(report["student_notice"],
                         "학생이 넣은 그림 2장 — 출처·권리는 학생 확인")
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["placeholders"], [])

    def test_empty_build(self):
        report = gd.build_report([], home=self.home)
        self.assertEqual(report["images"], [])
        self.assertEqual(report["student_count"], 0)
        self.assertEqual(report["blocked"], [])


class RecheckNoticeTests(_HomeCase):
    def test_retained_app_images_single_notice_and_no_mutation(self):
        image = self.store()
        self.receipt()
        before = (image.read_bytes(), self.receipts.read_bytes())
        report = gd.build_report([token(SHA), token(SHA)], home=self.home)
        self.assertEqual(report["blocked"], [])
        self.assertEqual([item["kind"] for item in report["images"]], ["app", "app"])
        self.assertEqual(len(report["notices"]), 1)
        self.assertIn("농사로 그림 2장", report["notices"][0])
        self.assertIn("다시 확인", report["notices"][0])
        self.assertEqual(report["recheck_figures"], {"nongsaro-curation": [1, 2]})
        self.assertEqual((image.read_bytes(), self.receipts.read_bytes()), before)

    def test_other_rule_has_no_recheck_notice(self):
        self.store()
        line = self.receipt()
        line["image"]["ruleId"] = "another-rule"
        self.receipts.write_text(json.dumps(line) + "\n", encoding="utf-8")
        report = gd.build_report([token(SHA)], home=self.home)
        self.assertEqual(report.get("notices", []), [])
        self.assertEqual(report["blocked"], [])

    def test_direct_matching_approved_receipt_and_unmatched_student(self):
        root = self.home / "project"
        picture = root / "figures" / "public" / (SHA + ".png")
        picture.parent.mkdir(parents=True)
        picture.write_bytes(PNG)
        receipt = root / ".gg-work" / "figure-receipts.jsonl"
        receipt.parent.mkdir()
        line = self.receipt()
        receipt.write_text(json.dumps(line) + "\n", encoding="utf-8")
        before = (picture.read_bytes(), receipt.read_bytes())
        ref = picture.relative_to(root).as_posix()
        report = gd.build_report([ref, "figures/student.png"], home=None, root=root)
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["student_count"], 2)
        self.assertEqual(report["recheck_figures"], {"nongsaro-curation": [1]})
        self.assertIn("농사로 그림 1장", report["notices"][0])
        self.assertEqual((picture.read_bytes(), receipt.read_bytes()), before)
        line["image"]["verdict"] = "rejected"
        receipt.write_text(json.dumps(line) + "\n", encoding="utf-8")
        self.assertEqual(gd.build_report([ref], home=None, root=root).get("notices", []), [])
        receipt.write_text("", encoding="utf-8")
        self.assertEqual(gd.build_report([ref], home=None, root=root).get("notices", []), [])


class AppBlockContractTests(_HomeCase):
    """가짜 앱 증명 → 실제 place → 검사·DOCX 산출물까지 확인한다."""

    def setUp(self):
        super().setUp()
        from tests._harness import bind_major, write_text
        import gg_core
        import gg_major_contract
        self.root = self.home / "project"
        self.root.mkdir()
        gg_core.init(self.root)
        bind_major(self.root, "specialty_crops")
        self.context = gg_major_contract.output_context(self.root, "specialty_crops")
        self.store()
        receipt = self.receipt()
        self.url = receipt["page"]["url"]
        self.manuscript = self.root / "body.md"
        self.manuscript.write_text("# Ⅰ. 머리말\n\n그림 1에서 확인한다.\n", encoding="utf-8")
        write_text(self.root, ".gg-work/figure-requests/s1.json", json.dumps(
                   {"v": 1, "reqId": "request1", "mode": "app", "slot": "s1"}))
        receipt["reqId"] = "request1"
        self.receipts.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        write_text(self.home, "res/request1.res.json", json.dumps(
                   {"v": 1, "id": "request1", "status": "done", "slot": "s1",
                    "images": [{"verdict": "approved", "sha256": SHA,
                                "org": "농촌진흥청"}]}))
        self.env = mock.patch.dict(os.environ, {"KNUAF_FIGURE_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def placed_text(self, org="농촌진흥청"):
        import gg_figure
        result = gg_figure.place(
            self.root, "s1", "Ⅰ. 머리말", manuscript="body.md", org=org,
            environ={"KNUAF_FIGURE_HOME": str(self.home)})
        self.assertEqual(result["status"], "placed", result)
        return self.manuscript.read_text(encoding="utf-8")

    def test_parenthesized_institution_place_check_convert_round_trip(self):
        import gg_document
        import build_docx
        org = "농촌진흥청(국립원예특작과학원)"
        text = self.placed_text(org=org)
        issues = gg_document.check(text, self.root, major_id="specialty_crops")
        self.assertFalse(any(code == "missing_image" for code, _ in issues), issues)
        document = build_docx.convert(text, base=self.root, context=self.context)
        self.assertEqual(len(document.inline_shapes), 1)
        paragraphs = [p.text for p in document.paragraphs]
        self.assertTrue(any(org in paragraph for paragraph in paragraphs), paragraphs)
        self.assertIn("주 : 원문 주소 " + self.url, paragraphs)

    def assert_blocked(self, text):
        import gg_document
        import build_docx
        issues = gg_document.check(text, self.root, major_id="specialty_crops")
        self.assertTrue(any("figure_source_note" in message for _, message in issues), issues)
        with self.assertRaisesRegex(ValueError, "blocked: figure_source_note"):
            build_docx.convert(text, base=self.root, context=self.context)

    def test_place_block_survives_actual_docx_serialization(self):
        import io
        from docx import Document
        import gg_document
        import build_docx
        text = self.placed_text()
        issues = gg_document.check(text, self.root, major_id="specialty_crops")
        self.assertFalse(any(code == "missing_image" for code, _ in issues), issues)
        document = build_docx.convert(text, base=self.root, context=self.context)
        stream = io.BytesIO()
        document.save(stream)
        stream.seek(0)
        actual = Document(stream)
        self.assertEqual(len(actual.inline_shapes), 1)
        paragraphs = [p.text for p in actual.paragraphs]
        self.assertIn("주 : 원문 주소 " + self.url, paragraphs)
        self.assertTrue(any(p.startswith("그림 1.") and "출처 : 농촌진흥청" in p.replace("\u00a0", " ")
                            for p in paragraphs), paragraphs)

    def test_handwritten_incomplete_and_wrong_blocks_are_rejected(self):
        text = self.placed_text()
        image = "![](" + token(SHA) + ")"
        caption = "그림 1. 안내 (출처 : 농촌진흥청)"
        note = "주 : 원문 주소 " + self.url
        for block in (image, image + "\n\n" + caption,
                      image + "\n\n" + note,
                      image + "\n\n" + caption + "\n\n주 : 원문 주소 https://wrong.example/x",
                      image + "\n\n그림 1. 안내\n\n" + note,
                      image + "\n\n" + caption + "\n\n다른 문단\n\n" + note,
                      image + "\n\n" + caption + "\n\n- " + note):
            with self.subTest(block=block):
                self.assert_blocked("# Ⅰ. 머리말\n\n" + block + "\n")
        # 같은 그림의 다른 정상 블록은 앞의 누락을 덮지 않는다.
        self.assert_blocked(image + "\n\n" + caption + "\n\n" + text)

    def test_missing_receipt_page_url_is_rejected(self):
        text = self.placed_text()
        receipt = json.loads(self.receipts.read_text(encoding="utf-8"))
        receipt["page"].pop("url")
        self.receipts.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        self.assert_blocked(text)

    def test_creator_annotation_after_exact_url_is_allowed(self):
        import gg_document
        text = self.placed_text().rstrip() + " 만든 사람: 홍길동\n"
        nodes = gg_document.parse(text)
        index = next(i for i, node in enumerate(nodes) if node["kind"] == "image")
        self.assertIsNone(gd.block_issue(nodes, index))

    def test_outside_app_retains_placeholder_without_note(self):
        import build_docx
        with mock.patch.dict(os.environ, {"KNUAF_FIGURE_HOME": ""}):
            document = build_docx.convert("![](" + token(SHA) + ")\n",
                                          base=self.root, context=self.context)
        self.assertEqual(len(document.inline_shapes), 0)
        self.assertIn(gd.PLACEHOLDER, [p.text for p in document.paragraphs])


if __name__ == "__main__":
    unittest.main()
