"""industrial_insects read-only CLI paths (gg.py insect-plan / insect-evidence).

Both commands return a plan or a review as JSON on stdout.  They are not
deliverables, so guard B leaves them open (G-B DESIGN §0/§2); what this
file locks is that they stay read-only and quiet:

- success and failure leave every file of the project byte-unchanged and
  add no file;
- stdout/stderr never echo an input path or a marker planted in it;
- the plan carries no body text, finance computation or output
  authorization.

Refusal of the insect paper/workbook/finance outputs on every writer is
covered by tests/test_major_guard_*.py (case T1, insect binding).
"""
import json
import subprocess
import sys

from tests._harness import SCRIPTS, ContractCase, bind_major, write_text

MARKER = "SECRET-MARKER-7f3a"


def _gg(*args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *map(str, args)],
        capture_output=True, text=True)


def _walk_keys(value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(value, list):
        for v in value:
            _walk_keys(v, out)
    return out


class InsectCliReadOnlyTests(ContractCase):
    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root, "industrial_insects")

    def _assert_quiet(self, proc, *needles):
        for stream in (proc.stdout, proc.stderr):
            self.assertNotIn(MARKER, stream)
            self.assertNotIn(str(self.root), stream)
            for needle in needles:
                self.assertNotIn(str(needle), stream)

    def _run_unchanged(self, *args):
        before = self._tree_bytes(self.root)
        proc = _gg(*args)
        self.assertEqual(before, self._tree_bytes(self.root))
        return proc

    def test_plan_success_is_read_only_and_has_no_output_claims(self):
        write_text(self.root, "sel.json", json.dumps([{
            "section_id": "preface", "action": "adapt",
            "reason": "대상 종에 맞게 도입 순서 조정",
            "observation_refs": [
                {"source_id": "F0", "physical_page": 7,
                 "object_label": "page"}],
        }], ensure_ascii=False))
        proc = self._run_unchanged("insect-plan", self.root,
                                   "--input", "sel.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual(value["status"], "review_plan")
        self.assertFalse(value["rendered_paper"])
        self.assertFalse(value["canonical_write"])
        self.assertEqual(value["finance_status"], "unsupported")
        keys = _walk_keys(value, set())
        for banned in ("calculated", "computed", "result", "authorization",
                       "body", "text", "paragraphs", "revenue", "total"):
            self.assertNotIn(banned, keys)
        self._assert_quiet(proc)

    def test_plan_empty_selection_needs_selection(self):
        write_text(self.root, "sel.json", "[]")
        proc = self._run_unchanged("insect-plan", self.root,
                                   "--input", "sel.json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "needs_selection")

    def test_plan_failures_are_fixed_codes_without_paths(self):
        cases = {
            "missing": None,
            "not_json": "{not json " + MARKER,
            "duplicate_key": ('[{"section_id":"preface","action":"adapt",'
                              '"action":"use","reason":"x"}]'),
            "nan": '[{"section_id":"preface","action":"use","x":NaN}]',
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                rel = MARKER + "-" + name + ".json"
                if text is not None:
                    write_text(self.root, rel, text)
                proc = self._run_unchanged("insect-plan", self.root,
                                           "--input", rel)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(json.loads(proc.stdout)["status"], "blocked")
                self._assert_quiet(proc, rel)

    def test_plan_input_outside_project_is_refused_quietly(self):
        outside = self.make_project() / (MARKER + ".json")
        outside.write_text("[]", encoding="utf-8")
        proc = self._run_unchanged("insect-plan", self.root,
                                   "--input", outside)
        self.assertNotEqual(proc.returncode, 0)
        self._assert_quiet(proc, outside)

    def test_evidence_failures_are_fixed_codes_without_paths(self):
        fake_pdf = write_text(self.root, MARKER + "-r0.pdf", "%PDF-1.4 fake")
        fake_xlsx = write_text(self.root, MARKER + "-r1.xlsx", "not a zip")
        cases = {
            "missing_files": (self.root / (MARKER + "-a.pdf"),
                              self.root / (MARKER + "-b.xlsx")),
            "hash_mismatch": (fake_pdf, fake_xlsx),
            "directory": (self.root, self.root),
        }
        for name, (pdf, xlsx) in cases.items():
            with self.subTest(case=name):
                proc = self._run_unchanged("insect-evidence", self.root,
                                           "--pdf", pdf, "--xlsx", xlsx)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(json.loads(proc.stdout)["status"], "blocked")
                self._assert_quiet(proc, pdf, xlsx)

    def test_other_major_binding_is_refused(self):
        other = self.make_project()
        bind_major(other, "specialty_crops")
        write_text(other, "sel.json", "[]")
        before = self._tree_bytes(other)
        proc = _gg("insect-plan", other, "--input", "sel.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(before, self._tree_bytes(other))
        proc = _gg("insect-plan", self.root, "--input", "sel.json",
                   "--major", "specialty_crops")
        self.assertNotEqual(proc.returncode, 0)

    def test_unbound_project_gets_common_only_plan(self):
        bare = self.make_project()
        write_text(bare, "sel.json", "[]")
        proc = _gg("insect-plan", bare, "--input", "sel.json")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("selected_sections", json.loads(proc.stdout))
