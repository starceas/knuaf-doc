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


if __name__ == "__main__":
    unittest.main()
