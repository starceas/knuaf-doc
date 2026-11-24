#!/usr/bin/env python3
"""윈도 PowerShell/메모장이 붙인 UTF-8 BOM·CRLF 입력 시험 — 합성 자료만 쓴다.

사용자가 공급하는 입력(apply --change JSON, observe --input JSON, 원답변 txt)은
BOM(EF BB BF)과 CRLF가 붙어도 없는 것과 같은 결과여야 한다. 증거 해시는 원
바이트 그대로 남는다.

실행: python3 -B tests/test_bom_inputs.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gg_core as core  # noqa: E402
import gg_manuscript_intake as intake  # noqa: E402

BOM = b"\xef\xbb\xbf"


def run_gg(*args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=env)


def windows_bytes(text, bom):
    """Write like PowerShell 5.1 `Set-Content -Encoding UTF8`: CRLF (+ BOM)."""
    data = text.replace("\n", "\r\n").encode("utf-8")
    return BOM + data if bom else data


CHANGE = {
    "request_id": "bom-input-test",
    "ops": [{
        "collection": "sections",
        "value": {"id": "I", "path": "sections/I.md",
                  "title": "합성 제목", "order": 1, "status": "drafting"},
    }],
}


class ApplyChangeBomTests(unittest.TestCase):
    def _apply(self, bom):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            init = run_gg("init", str(work))
            self.assertEqual(0, init.returncode, init.stdout + init.stderr)
            # sections op는 apply 때 본문 파일을 읽어 draft_hash를 계산하므로
            # change가 가리키는 파일을 작업 폴더에 미리 만들어 둔다.
            section = work / "sections" / "I.md"
            section.parent.mkdir(parents=True, exist_ok=True)
            section.write_text("# 합성 제목\n\n합성 본문.\n", encoding="utf-8")
            change = Path(tmp) / "change.json"
            change.write_bytes(windows_bytes(
                json.dumps(CHANGE, ensure_ascii=False, indent=2), bom))
            proc = run_gg("apply", str(work), "--change", str(change),
                          "--expected-revision", "0")
            # 임시 폴더 이름은 실행마다 달라 출력 비교에서 뺀다.
            scrub = lambda s: s.replace(tmp, "<tmp>")
            return proc.returncode, scrub(proc.stdout), scrub(proc.stderr)

    def test_bom_change_matches_plain_change(self):
        plain = self._apply(False)
        with_bom = self._apply(True)
        self.assertEqual(0, plain[0], plain)
        self.assertEqual(0, with_bom[0], with_bom)
        self.assertEqual(plain[1], with_bom[1])
        self.assertNotIn("BOM", with_bom[1] + with_bom[2])
        self.assertNotIn("JSONDecodeError", with_bom[2])


class InputJsonHelperTests(unittest.TestCase):
    def test_input_json_accepts_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bom in (False, True):
                path = Path(tmp) / ("in-%s.json" % bom)
                path.write_bytes(windows_bytes(
                    json.dumps({"가": [1, 2]}, ensure_ascii=False, indent=2),
                    bom))
                self.assertEqual({"가": [1, 2]}, core.input_json(path))

    def test_input_json_without_bom_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual({"a": 1}, core.input_json(path))

    def test_without_bom_strips_only_one_leading_bom(self):
        self.assertEqual(b"x", core.without_bom(BOM + b"x"))
        self.assertEqual(BOM + b"x", core.without_bom(BOM + BOM + b"x"))
        self.assertEqual(b"x", core.without_bom(b"x"))


class AnswerIntakeBomTests(unittest.TestCase):
    """답변 txt·원답변 source JSON: 파싱은 BOM 허용, 해시는 원 바이트."""

    ANSWER = ("1. 현재 원고: 합성원고.docx\n"
              "2. 원고 소유: 본인\n"
              "3. 작업 기준: 이어쓰기\n")

    def _claims(self):
        lines = self.ANSWER.splitlines()
        base = {"answer_state": "provided", "kind": "reported_fact",
                "value_type": "text", "scope": "project", "unit": "",
                "period": None}
        values = [("intake.current_manuscript", "합성원고.docx"),
                  ("intake.file_owner", "self"),
                  ("intake.work_basis", "resume")]
        return {"c%d" % (i + 1): dict(base, field_id=field, value=value,
                                       locator="line %d" % (i + 1),
                                       quote=lines[i])
                for i, (field, value) in enumerate(values)}

    def _run(self, bom):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "major-answer.txt").write_bytes(
                windows_bytes(self.ANSWER, bom))
            spec = {"path": "major-answer.txt", "claims": self._claims()}
            (root / "bind.json").write_bytes(windows_bytes(
                json.dumps(spec, ensure_ascii=False, indent=2), bom))
            path, raw, claims = intake._answer(root, "bind.json", "합성원고.docx")
            return raw, claims, core.digest(raw)

    def test_bom_answer_matches_plain_answer(self):
        raw_plain, claims_plain, hash_plain = self._run(False)
        raw_bom, claims_bom, hash_bom = self._run(True)
        self.assertEqual(claims_plain, claims_bom)
        # 증거 해시는 원 바이트 그대로: BOM 붙은 파일은 해시가 달라야 한다.
        self.assertTrue(raw_bom.startswith(BOM))
        self.assertEqual(core.digest(raw_bom), hash_bom)
        self.assertNotEqual(hash_plain, hash_bom)


class FinanceTablesInputBomTests(unittest.TestCase):
    """finance-tables --input: BOM+CRLF 각주 JSON이 평문과 같은 값으로 읽힌다."""

    def _footnotes(self, bom):
        import gg_finance_tables as ft
        from unittest import mock
        payload = {"footnotes": [{"id": "n1", "text": "합성 각주"}]}
        seen = {}

        def fake_generate(xlsx, major_id=None, footnotes=None):
            seen["footnotes"] = footnotes
            return {"status": "unsupported_layout", "failed_signatures": []}

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "fn.json").write_bytes(windows_bytes(
                json.dumps(payload, ensure_ascii=False, indent=2), bom))
            auth = mock.Mock(major_id="합성전공")
            with mock.patch.object(ft.mc, "output_context"), \
                    mock.patch.object(ft.mc, "authorize_output",
                                      return_value=auth), \
                    mock.patch.object(ft.mc, "require_any_workbook_profile"), \
                    mock.patch.object(ft, "generate", fake_generate):
                ft.run(base, base / "x.xlsx", input_path="fn.json",
                       out="build/out.md")
        return seen.get("footnotes")

    def test_bom_input_matches_plain_input(self):
        plain = self._footnotes(False)
        self.assertEqual([{"id": "n1", "text": "합성 각주"}], plain)
        self.assertEqual(plain, self._footnotes(True))


class DraftSplitPlanBomTests(unittest.TestCase):
    """gg_draft_split --plan: BOM+CRLF 계획 JSON이 JSON 파싱에서 막히지 않는다."""

    def _reason(self, bom):
        import gg_draft_split as ds
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            plan.write_bytes(windows_bytes(
                json.dumps({"schema": ds.SCHEMA}, ensure_ascii=False,
                           indent=2), bom))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = ds.main([str(root), "--apply", "--plan", str(plan)])
            return code, json.loads(buf.getvalue())["reason"]

    def test_bom_plan_matches_plain_plan(self):
        plain = self._reason(False)
        self.assertIn("--from", plain[1])  # 계획 파싱·스키마 통과 뒤 다음 검사까지 감
        self.assertEqual(plain, self._reason(True))


if __name__ == "__main__":
    unittest.main()
