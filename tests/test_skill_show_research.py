"""show_research reads the canon (project.json), not a retired flat ledger.

Migrated from the GUI contract suite (cb72ae5).  The old version read
``02_receipts.jsonl``/``04_research_log.md`` — files this canonical model
never writes — so "where did this number come from?" always answered "0건".
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._harness import (
    ContractCase, SCRIPTS, claim_of, fact_op, runtime, source_op, write_text)


def run_show(folder, *args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "show_research.py"),
         str(folder), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)


class ShowResearchTests(ContractCase):
    def project(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sources/answers.md",
                   "# 원답변\n\n재배 면적: 600평\n\n수취가격: 모름\n")
        area = fact_op("fact-area", "farm.area", "600", "평",
                       value_type="decimal", period="2026", scope="farm",
                       claim_id="c1")
        price = fact_op("fact-price", "farm.price", None, None,
                        answer_state="unknown", verification="unreviewed")
        core.apply(root, {"request_id": "seed", "ops": [
            source_op("sources/answers.md",
                      claims=claim_of(area["value"])),
            area,
            price,
        ]}, 0)
        return root

    def test_lists_facts_from_canon(self):
        r = run_show(self.project())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("사실 2", r.stdout)
        self.assertIn("fact-area", r.stdout)
        self.assertIn("farm.area", r.stdout)
        # An unverified fact must be marked, not quietly listed as evidence.
        self.assertIn("미검증 1건", r.stdout)
        self.assertIn("!! fact-price", r.stdout)

    def test_claim_lookup_is_comma_insensitive(self):
        r = run_show(self.project(), "--claim", "6,00")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("fact-area", r.stdout)
        self.assertIn("출처: answer", r.stdout)
        self.assertIn("line 1", r.stdout)

    def test_missing_claim_says_no_basis(self):
        r = run_show(self.project(), "--claim", "99999")
        self.assertEqual(0, r.returncode)
        self.assertIn("근거 없는 수치", r.stdout)

    def test_source_id_lists_citing_facts(self):
        r = run_show(self.project(), "--id", "answer")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("이 출처를 쓰는 사실 2건", r.stdout)

    def test_unknown_source_is_reported_not_raised(self):
        r = run_show(self.project(), "--id", "nope")
        self.assertEqual(0, r.returncode)
        self.assertIn("정본에 없다", r.stdout)

    def test_folder_without_canon_is_blocked_not_empty(self):
        tmp = tempfile.TemporaryDirectory(prefix="p1-research-")
        self.addCleanup(tmp.cleanup)
        r = run_show(Path(tmp.name))
        self.assertEqual(2, r.returncode)
        self.assertIn("정본을 읽을 수 없음", r.stdout)
        self.assertIn("import", r.stdout)


if __name__ == "__main__":
    unittest.main()
