#!/usr/bin/env python3
"""gg_doi 오프라인 시험 — HTTP 계층을 가짜 응답으로 주입한다.

실행: python3 test_gg_doi.py   (스크립트 상위 tests/ 위치에서)
실제 네트워크 호출 시험은 GG_DOI_LIVE=1일 때만 1건 돌고 그 외엔 skip.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "gg_doi", Path(__file__).resolve().parents[1] / "scripts" / "gg_doi.py")
gg_doi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gg_doi)


def fake_fetch(table):
    def fetch(doi, timeout):
        return table[doi]
    return fetch


def crossref_body(**kw):
    return json.dumps({"message": kw}).encode("utf-8")


class ExtractDoi(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(
            gg_doi.extract_doi("10.11002/kjfp.2020.27.5.534"),
            "10.11002/kjfp.2020.27.5.534")

    def test_wrapped_and_trailing(self):
        self.assertEqual(
            gg_doi.extract_doi("doi:10.1234/abc.),"),
            "10.1234/abc")
        self.assertEqual(
            gg_doi.extract_doi("https://doi.org/10.9999/x1;"),
            "10.9999/x1")

    def test_none(self):
        self.assertIsNone(gg_doi.extract_doi("RISS http://riss.kr/x"))
        self.assertIsNone(gg_doi.extract_doi(None))
        self.assertIsNone(gg_doi.extract_doi(12345))


class CheckItems(unittest.TestCase):
    def setUp(self):
        self.table = {
            "10.1/ok": (200, crossref_body(
                DOI="10.1/ok",
                title=["합성 논문 제목"],
                issued={"date-parts": [[2020]]},
                author=[{"family": "Hong"}],
                **{"container-title": ["합성학회지"]}), None),
            "10.1/missing": (404, None, "HTTP 404"),
            "10.1/down": (None, None, "URLError"),
            "10.1/teapot": (418, None, "HTTP 418"),
        }

    def test_resolved(self):
        items = [{"doi": "10.1/ok", "ref_ids": ["R-1"]}]
        r = gg_doi.check_items(items, fetch=fake_fetch(self.table),
                               interval=0)[0]
        self.assertEqual(r["status"], "resolved")
        self.assertEqual(r["title"], "합성 논문 제목")
        self.assertEqual(r["year"], "2020")
        self.assertEqual(r["journal"], "합성학회지")
        self.assertEqual(r["ref_ids"], ["R-1"])
        self.assertEqual(set(r) & {"title", "year", "journal"},
                         {"title", "year", "journal"})

    def test_not_found_is_not_verified(self):
        items = [{"doi": "10.1/missing"}]
        r = gg_doi.check_items(items, fetch=fake_fetch(self.table),
                               interval=0)[0]
        self.assertEqual(r["status"], "not_found")
        self.assertNotIn("title", r)

    def test_network_error_never_promotes(self):
        for doi in ("10.1/down", "10.1/teapot"):
            r = gg_doi.check_items([{"doi": doi}],
                                   fetch=fake_fetch(self.table),
                                   interval=0)[0]
            self.assertEqual(r["status"], "network_error")
            self.assertNotIn("title", r)

    def test_bad_body_is_network_error(self):
        table = {"10.1/bad": (200, b"<html>oops</html>", None)}
        r = gg_doi.check_items([{"doi": "10.1/bad"}],
                               fetch=fake_fetch(table), interval=0)[0]
        self.assertEqual(r["status"], "network_error")

    def test_empty_record_is_not_resolved(self):
        for body in (b"{}", b'{"message": []}',
                     '{"message": {"title": ["제목"]}}'.encode("utf-8")):
            with self.subTest(body=body):
                r = gg_doi.check_items(
                    [{"doi": "10.1/empty"}],
                    fetch=lambda _doi, _timeout, body=body: (200, body, None),
                    interval=0)[0]
                self.assertEqual(r["status"], "network_error")

    def test_different_response_doi_is_not_resolved(self):
        body = crossref_body(DOI="10.1/other", title=["다른 DOI"])
        r = gg_doi.check_items(
            [{"doi": "10.1/requested"}],
            fetch=lambda _doi, _timeout: (200, body, None), interval=0)[0]
        self.assertEqual(r["status"], "network_error")

    def test_malformed_message_shape_is_network_error(self):
        body = b'{"message": ["not", "an", "object"]}'
        r = gg_doi.check_items(
            [{"doi": "10.1/shape"}],
            fetch=lambda _doi, _timeout: (200, body, None), interval=0)[0]
        self.assertEqual(r["status"], "network_error")

    def test_unparseable(self):
        r = gg_doi.check_items([{"doi": None, "input": "RISS 링크"}],
                               fetch=fake_fetch({}), interval=0)[0]
        self.assertEqual(r["status"], "unparseable")

    def test_no_calls_for_unparseable(self):
        calls = []

        def counting(doi, timeout):
            calls.append(doi)
            return 404, None, "HTTP 404"

        gg_doi.check_items(
            [{"doi": None, "input": "x"}, {"doi": "10.1/a"}],
            fetch=counting, interval=0)
        self.assertEqual(calls, ["10.1/a"])


class CollectFromProject(unittest.TestCase):
    def test_collects_locator_and_refs(self):
        with tempfile.TemporaryDirectory() as d:
            project = {
                "sources": {
                    "R-021": {"id": "R-021", "revision": 1,
                              "locator": "10.11002/kjfp.2020.27.5.534"},
                    "R-030": {"id": "R-030", "revision": 1,
                              "locator": "KCI 페이지"},
                },
                "facts": {
                    "f1": {"id": "f1", "source_refs": [
                        {"id": "R-030",
                         "locator": "doi:10.9999/abc."}]},
                },
            }
            (Path(d) / "project.json").write_text(
                json.dumps(project), encoding="utf-8")
            found = gg_doi.collect_from_project(d)
        self.assertIn("10.11002/kjfp.2020.27.5.534", found)
        self.assertIn("10.9999/abc", found)
        self.assertIn("R-021",
                      found["10.11002/kjfp.2020.27.5.534"]["ref_ids"])

    def test_source_id_filter(self):
        with tempfile.TemporaryDirectory() as d:
            project = {"sources": {
                "A": {"id": "A", "locator": "10.1234/aaa"},
                "B": {"id": "B", "locator": "10.5678/bbb"}},
                "facts": {}}
            (Path(d) / "project.json").write_text(
                json.dumps(project), encoding="utf-8")
            found = gg_doi.collect_from_project(d, ["A"])
        self.assertEqual(list(found), ["10.1234/aaa"])


class Cli(unittest.TestCase):
    def test_blocked_without_project(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gg_doi.main([d]), 2)

    def test_adhoc_doi_no_folder(self):
        original = gg_doi._urllib_fetch
        gg_doi._urllib_fetch = lambda doi, timeout: \
            self.fail("오프라인 시험에서 실제 호출 금지")
        self.addCleanup(setattr, gg_doi, "_urllib_fetch", original)
        self.assertEqual(
            gg_doi.main(["--doi", "not-a-doi"]), 0)

    def test_report_does_not_embed_workspace_path(self):
        with tempfile.TemporaryDirectory(prefix="private-workspace-") as d:
            report = gg_doi.build_report(Path(d), [])
        self.assertNotIn(d, json.dumps(report))

    def test_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "project.json").write_text(
                json.dumps({"sources": {}, "facts": {}}), encoding="utf-8")
            (Path(d) / "result.json").write_text("keep", encoding="utf-8")
            self.assertEqual(gg_doi.main([d, "--out", "result.json"]), 2)
            self.assertEqual((Path(d) / "result.json").read_text(), "keep")


@unittest.skipUnless(os.environ.get("GG_DOI_LIVE"),
                     "실제 Crossref 호출 — GG_DOI_LIVE=1일 때만")
class LiveCall(unittest.TestCase):
    def test_real_doi(self):
        # 공개 읽기 전용 1건 — 알려진 실재 DOI
        r = gg_doi.check_items([{"doi": "10.1038/nature12373"}],
                               interval=0)[0]
        self.assertEqual(r["status"], "resolved")
        self.assertTrue(r["title"])


if __name__ == "__main__":
    unittest.main()
