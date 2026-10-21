"""gg_commands: the four legacy entrypoints (build_status·lint_evidence·
merge_sections·lint_format) that share one dispatcher.

Migrated from the GUI contract suite (cb72ae1).  lint_format keeps one
capability gg.py does not have: it can check a single section file in a
folder that has no project.json yet.  The rest must refuse that folder
rather than pass judgement on an unimported ledger.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._harness import (
    ContractCase, SCRIPTS, bind_major, claim_of, fact_op, runtime,
    section_claim, section_op, source_op, write_text)

SHIMS = {"build_status": "status", "lint_evidence": "evidence",
         "merge_sections": "merge", "lint_format": "format"}


def run_script(script, *args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)


def write_section(root, rel, title, body):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# %s\n\n<!-- gg:draft:start -->\n%s\n<!-- gg:draft:end -->\n"
        % (title, body), encoding="utf-8")
    return path


class CommandShimTests(ContractCase):
    def loose_sections(self):
        tmp = tempfile.TemporaryDirectory(prefix="p1-cmd-")
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / "예전 작업"
        write_section(folder, "sections/01.md", "Ⅰ. 머리말",
                      "농장A의 재배 면적은 600평이다.")
        return folder

    def project(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "농장명: 행복농장\n")
        write_text(root, "intro.md", "농장명은 행복농장이다.\n")
        fact = fact_op("farm_name", "farm_name", "행복농장", None, claim_id="c1")
        core.apply(root, {"request_id": "seed", "ops": [
            source_op(claims=claim_of(fact["value"])),
            fact,
            section_op("intro", "머리말", "intro.md",
                       claims=[section_claim(fact["value"],
                                             "농장명은 행복농장이다")]),
        ]}, 0)
        bind_major(root)
        return root

    def rows(self, result):
        return json.loads(result.stdout)

    # --- an unimported folder is refused, not judged ----------------------

    def test_a_folder_without_a_canon_is_blocked(self):
        for shim in ("build_status", "lint_evidence", "merge_sections"):
            with self.subTest(shim=shim):
                r = run_script(shim + ".py", self.loose_sections())
                self.assertEqual(2, r.returncode, r.stdout)
                body = self.rows(r)
                self.assertEqual("blocked", body[0]["status"])
                self.assertEqual("execution", body[0]["check_id"])
                self.assertIn("import", body[0]["reason"])

    def test_the_evidence_refusal_says_why_a_string_is_not_proof(self):
        r = run_script("lint_evidence.py", self.loose_sections())
        self.assertIn("검증 완료로 인정하지 않음", self.rows(r)[0]["reason"])

    # --- the one thing gg.py cannot do ------------------------------------

    def test_format_checks_a_single_file_without_a_project(self):
        folder = self.loose_sections()
        r = run_script("lint_format.py", folder / "sections" / "01.md")
        self.assertEqual(0, r.returncode, r.stdout)
        body = self.rows(r)
        self.assertTrue(body)
        self.assertTrue(all(x["status"] == "pass" for x in body), body)

    # --- the canon path ---------------------------------------------------

    def test_status_on_a_real_project_reports_the_revision_and_checks(self):
        r = run_script("build_status.py", self.project())
        self.assertEqual(0, r.returncode, r.stderr)
        data = json.loads(r.stdout)
        self.assertGreaterEqual(data["revision"], 1)
        self.assertIsInstance(data["checks"], list)
        self.assertIsInstance(data["tasks"], list)

    def test_merge_on_a_real_project_publishes_a_review_export(self):
        project = self.project()
        r = run_script("merge_sections.py", project,
                       "--major", "specialty_crops")
        self.assertEqual(0, r.returncode, r.stderr)
        value = json.loads(r.stdout)
        self.assertEqual("generated", value["status"])
        self.assertTrue(value["path"].endswith(".md"), value)
        self.assertTrue(Path(value["path"]).is_file())
        self.assertTrue((project / "build").is_dir())

    def test_evidence_on_a_real_project_returns_the_same_checks_as_gg_check(self):
        project = self.project()
        shim = self.rows(run_script("lint_evidence.py", project))
        direct = json.loads(run_script(
            "gg.py", "check", project).stdout)
        self.assertEqual([x["check_id"] for x in shim],
                         [x["check_id"] for x in direct])

    # --- an empty target is not a pass ------------------------------------

    def test_format_blocks_an_empty_target_instead_of_passing_it(self):
        tmp = tempfile.TemporaryDirectory(prefix="p1-cmd-empty-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "빈폴더" / "sections").mkdir(parents=True)
        (root / "빈파일.md").write_text("", encoding="utf-8")
        for target in (root / "없는폴더", root / "빈폴더", root / "빈파일.md"):
            with self.subTest(target=target.name):
                r = run_script("lint_format.py", target)
                self.assertEqual(2, r.returncode, r.stdout)
                self.assertEqual("blocked", self.rows(r)[0]["status"])

    # --- failures are reported, never raised ------------------------------

    def test_a_missing_target_is_reported_as_json_not_a_traceback(self):
        tmp = tempfile.TemporaryDirectory(prefix="p1-cmd-miss-")
        self.addCleanup(tmp.cleanup)
        missing = Path(tmp.name) / "없는폴더"
        for shim in sorted(SHIMS):
            with self.subTest(shim=shim):
                r = run_script(shim + ".py", missing)
                self.assertEqual(2, r.returncode)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual("blocked", self.rows(r)[0]["status"])

    def test_every_shim_emits_parsable_json(self):
        folder = self.loose_sections()
        for shim in sorted(SHIMS):
            with self.subTest(shim=shim):
                json.loads(run_script(shim + ".py", folder).stdout)

    # --- the shims stay thin ----------------------------------------------

    def test_each_shim_only_dispatches_into_the_one_adapter(self):
        for shim, mode in SHIMS.items():
            with self.subTest(shim=shim):
                src = (SCRIPTS / (shim + ".py")).read_text(encoding="utf-8")
                self.assertIn("from gg_commands import main", src)
                self.assertIn('main("%s")' % mode, src)


if __name__ == "__main__":
    unittest.main()
