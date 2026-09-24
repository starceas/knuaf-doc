"""P1 core behavior tests — real-input verification of the A-owned fixes.

C2 UTF-8 text I/O, C3 record-ID rejection, C5 Python floor, H8 question
ladder.  Every check exercises the deployed runtime under
``skills/knuaf-doc/scripts``; baseline red evidence for each assertion was
captured against the frozen P0 input copy (see evidence/a/).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests._harness import (
    ContractCase,
    SCRIPTS,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    text_calls_without_encoding,
    write_text,
)

A_OWNED_SCRIPTS = {
    "build_status.py", "gg_commands.py", "gg_core.py", "gg_deps.py",
    "gg_finance.py", "gg_guidelines.py", "gg.py", "lint_evidence.py",
    "lint_format.py", "lint_plaintext.py", "merge_sections.py",
    "show_research.py",
}


class RecordIdTests(ContractCase):
    """C3: every collection mutation path rejects unusable record IDs
    before the canonical bytes move.  All ops funnel through apply(),
    including migrate_staged's generated records."""

    BAD_IDS = [7, None, ["x"], {"a": 1}, "", "   ", " x", "x ",
               "a\tb", "a\x00b", "a\x7fb"]

    def test_bad_ids_rejected_in_every_collection(self):
        core = runtime("gg_core")
        for col in core.COLLECTIONS:
            for bad in self.BAD_IDS:
                root = self.make_project()
                before = (root / "project.json").read_bytes()
                with self.subTest(collection=col, id=repr(bad)):
                    try:
                        core.apply(
                            root,
                            {"request_id": "rid",
                             "ops": [{"collection": col,
                                      "value": {"id": bad}}]},
                            0,
                        )
                    except ValueError as e:
                        self.assertIn("ID", str(e), repr(bad))
                    else:
                        self.fail("bad id %r stored in %s" % (bad, col))
                    self.assertEqual(
                        before, (root / "project.json").read_bytes(),
                        "canonical bytes moved on rejected id")
                    p = core.load(root)
                    self.assertEqual(p["revision"], 0)
                    self.assertEqual(p["requests"], {})

    def test_modify_path_rejects_bad_id(self):
        core = runtime("gg_core")
        root = self.make_project()
        p = core.apply(
            root,
            {"request_id": "seed",
             "ops": [{"collection": "tasks", "value": {"id": "t1"}}]},
            0)
        before = (root / "project.json").read_bytes()
        for bad in self.BAD_IDS:
            with self.subTest(id=repr(bad)):
                with self.assertRaises(ValueError):
                    core.apply(
                        root,
                        {"request_id": "mod",
                         "ops": [{"collection": "tasks",
                                  "value": {"id": bad, "x": 1}}]},
                        p["revision"])
                self.assertEqual(
                    before, (root / "project.json").read_bytes())

    def test_valid_ids_still_apply_and_roundtrip(self):
        core = runtime("gg_core")
        root = self.make_project()
        for rid in ("farm_name", "legacy:sections/01-서론.md",
                    "매출액-2026", "q_1.2"):
            with self.subTest(id=rid):
                p = core.apply(
                    root,
                    {"request_id": "add-" + rid,
                     "ops": [{"collection": "tasks",
                              "value": {"id": rid}}]},
                    core.load(root)["revision"])
                self.assertIn(rid, p["tasks"])
        p = core.load(root)
        self.assertEqual(
            {"farm_name", "legacy:sections/01-서론.md", "매출액-2026",
             "q_1.2"},
            set(p["tasks"]))
        # idempotent replay still works for a good request
        change = {"request_id": "replay",
                  "ops": [{"collection": "tasks", "value": {"id": "t9"}}]}
        p1 = core.apply(root, change, core.load(root)["revision"])
        p2 = core.apply(root, change, core.load(root)["revision"])
        self.assertEqual(p1["revision"], p2["revision"])


class SchemaOneRejectionTests(ContractCase):
    """S01/C3 on schema-1 projects: apply() routes them through the
    schema_version=1→2 upgrade, which must not persist before record-ID
    validation.  A rejected change leaves canonical bytes, revision,
    history and the request ledger untouched — identical to schema 2.
    (Red evidence: Astra ASTRA-S01-SCHEMA-OBSERVATION.json — schema1
    reject mutated project.json; schema2 did not.)"""

    def _schema1_project(self):
        """A minimal schema-1 canonical: init then pin schema_version=1
        (the upgrade recompute only touches reviews/approvals/outputs,
        so a fresh project round-trips cleanly)."""
        root = self.make_project()
        pj = root / "project.json"
        p = json.loads(pj.read_text(encoding="utf-8"))
        p["schema_version"] = 1
        pj.write_text(json.dumps(p, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        return root

    def test_schema1_bad_id_leaves_canonical_untouched(self):
        core = runtime("gg_core")
        for col in core.COLLECTIONS:
            for bad in RecordIdTests.BAD_IDS:
                root = self._schema1_project()
                before = (root / "project.json").read_bytes()
                with self.subTest(collection=col, id=repr(bad)):
                    with self.assertRaises(ValueError):
                        core.apply(
                            root,
                            {"request_id": "rid",
                             "ops": [{"collection": col,
                                      "value": {"id": bad}}]},
                            0)
                    self.assertEqual(
                        before, (root / "project.json").read_bytes(),
                        "schema-1 rejection moved canonical bytes")
                    p = core.load(root)
                    self.assertEqual(1, p["schema_version"])
                    self.assertEqual(0, p["revision"])
                    self.assertEqual({}, p["requests"])
                    self.assertEqual([], p["history"])

    def test_schema1_bad_id_on_existing_record_preserves_bytes(self):
        core = runtime("gg_core")
        root = self._schema1_project()
        pj = root / "project.json"
        p = json.loads(pj.read_text(encoding="utf-8"))
        seed = fact_op("f1", "crop", "무", "없음")["value"]
        seed["revision"] = 1
        src = source_op()["value"]
        src["revision"] = 1
        p["sources"]["answer"] = src
        p["facts"]["f1"] = seed
        pj.write_text(json.dumps(p, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        before = pj.read_bytes()
        for bad in RecordIdTests.BAD_IDS:
            with self.subTest(id=repr(bad)):
                with self.assertRaises(ValueError):
                    core.apply(
                        root,
                        {"request_id": "mod",
                         "ops": [{"collection": "facts",
                                  "value": {"id": bad, "value": "콩"}}]},
                        0)
        self.assertEqual(before, pj.read_bytes())
        p = core.load(root)
        self.assertEqual(1, p["schema_version"])
        self.assertEqual(seed, p["facts"]["f1"])

    def test_schema1_valid_request_after_rejection(self):
        core = runtime("gg_core")
        root = self._schema1_project()
        write_text(root, "answer.txt", "재배 작물: 무")
        with self.assertRaises(ValueError):
            core.apply(
                root,
                {"request_id": "bad",
                 "ops": [{"collection": "facts", "value": {"id": 7}}]},
                0)
        self.assertEqual(1, core.load(root)["schema_version"])
        change = {"request_id": "good",
                  "ops": [source_op(),
                          fact_op("crop", "crop", "무", "없음")]}
        p = core.apply(root, change, 0)
        self.assertEqual(2, p["schema_version"])
        self.assertIn("crop", p["facts"])

    def test_schema1_valid_request_upgrades_and_replays(self):
        core = runtime("gg_core")
        root = self._schema1_project()
        write_text(root, "answer.txt", "재배 작물: 무")
        change = {"request_id": "r1",
                  "ops": [source_op(),
                          fact_op("crop", "crop", "무", "없음")]}
        p = core.apply(root, change, 0)
        self.assertEqual(2, p["schema_version"])
        self.assertIn("crop", p["facts"])
        self.assertEqual(1, p["revision"])
        before = (root / "project.json").read_bytes()
        p2 = core.apply(root, change, core.load(root)["revision"])
        self.assertEqual(p["revision"], p2["revision"])
        self.assertEqual(before, (root / "project.json").read_bytes(),
                         "idempotent replay must not rewrite canonical")


class PythonFloorTests(ContractCase):
    """C5: the public CLI must reject Python <3.10 with a clear message
    and exit 2 *before* importing gg_core or touching the project.

    The band below shims ``sys.version_info`` in a subprocess and plants
    a poisoned ``gg_core.py`` beside the copied ``gg.py``: if the guard
    ever lets the import through, the poison exits 99 instead of 2.
    A real interpreter observation also exists — /usr/bin/python3 is
    3.9.6 on this machine; CONTRACT ran init/doctor/bare on it (exit 2,
    blocked JSON, zero side effects — evidence/a/c5-python39-real.txt)."""

    def test_version_shim_rejects_before_core_import(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-p1py-")
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        gg = d / "gg.py"
        gg.write_text((SCRIPTS / "gg.py").read_text(encoding="utf-8"),
                      encoding="utf-8")
        (d / "gg_core.py").write_text(
            "import sys\nsys.exit(99)  # poison: import must never happen\n",
            encoding="utf-8")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys, runpy\n"
             "sys.version_info = (3, 9, 18)\n"
             "sys.argv = ['gg.py', 'doctor', 'x']\n"
             "runpy.run_path('gg.py', run_name='__main__')\n"],
            cwd=d, capture_output=True, text=True, env=env)
        self.assertEqual(2, proc.returncode,
                         "stdout=%r stderr=%r" % (proc.stdout, proc.stderr))
        payload = json.loads(proc.stdout)
        self.assertEqual("blocked", payload["status"])
        self.assertIn("3.10", payload["reason"])

    def test_module_import_is_side_effect_free(self):
        """`import gg` on a supported interpreter must not exit — the
        floor guard lives inside main(), not at module top."""
        gg = runtime("gg")
        self.assertTrue(callable(gg.main))

    def test_supported_python_still_runs_commands(self):
        """The guard must not over-block: the real interpreter passes it
        and reaches normal argument/IO handling."""
        core = runtime("gg_core")
        root = self.make_project()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "gg.py"), "status", str(root)],
            capture_output=True, text=True, env=env)
        self.assertEqual(0, proc.returncode,
                         "stdout=%r stderr=%r" % (proc.stdout, proc.stderr))
        self.assertIn("skill_ready", json.loads(proc.stdout))


class Utf8IoTests(ContractCase):
    """C2: deployed runtime text I/O is UTF-8 regardless of locale.

    The band forces the interpreter's default encoding to ASCII
    (LC_ALL=C + PYTHONUTF8=0, verified inside the subprocess) so any
    remaining implicit-encoding path crashes instead of silently passing.
    """

    DRIVER = r'''
import json, locale, sys
from pathlib import Path
enc = locale.getpreferredencoding(False)
assert any(t in enc.lower() for t in ("ascii", "ansi")), \
    "band not active: " + enc
sys.path.insert(0, sys.argv[1])
import gg_core as core

root = Path(sys.argv[2]) / "proj"
core.init(root)
(root / "answer.txt").write_text("농장명: 행복농장\n", encoding="utf-8")
(root / "intro.md").write_text("농장명은 행복농장이다.\n", encoding="utf-8")
fact = {"id": "farm_name", "field_id": "farm_name", "kind": "reported_fact",
        "value": "행복농장", "unit": None, "value_type": "text",
        "period": None, "scope": "farm", "answer_state": "provided",
        "verification": "source_located",
        "source_refs": [{"id": "answer", "locator": "line 1",
                         "revision": 1, "claim_id": "c1"}]}
claim = {"c1": {k: fact[k] for k in
               ("field_id", "value", "unit", "period", "scope",
                "answer_state", "kind")}}
ops = [
    {"collection": "sources", "value": {
        "id": "answer", "path": "answer.txt", "claims": claim,
        "claim_review": "synthetic"}},
    {"collection": "facts", "value": fact},
    {"collection": "sections", "value": {
        "id": "intro", "title": "머리말", "path": "intro.md", "order": 1,
        "status": "drafting",
        "claims": [{"fact_id": "farm_name", "quote": "농장명은 행복농장이다",
                    **{k: fact[k] for k in ("field_id", "value", "unit",
                                            "period", "scope", "kind")}}]}},
]
p = core.apply(root, {"request_id": "utf8", "ops": ops}, 0)
p = core.load(root)  # reload under ASCII default — implicit read would die
assert p["facts"]["farm_name"]["value"] == "행복농장"
out = Path(core.export(root, "draft")["path"])
assert "행복농장" in out.read_text(encoding="utf-8")

# finance manifest roundtrip (gg_finance workbook + .manifest.json)
import gg_finance
spec = dict(
    profile="single_annual_cash_v1", crops=["합성작목"],
    accounting_basis="cash_pre_tax_no_inventory",
    source_refs=["synthetic:행복농장-원답변"], unit="원", quantity_unit="kg",
    land="100", facility="300", equity="200", loan="200", loan_rate=".05",
    discount_rate=".05", salvage="0",
    investment_basis="school_farm_new_business", owner_labor_in_costs=False,
    start_year=2030, years=3, life=3, grace=0, term=3,
    repayment="equal_principal",
    periods=[dict(year=2030 + i, quantity="100", sold="90", loss="10",
                  price="5", variable_cost="1", fixed_cost="100",
                  household="50") for i in range(3)],
)
xlsx = Path(sys.argv[2]) / "finance.xlsx"
gg_finance.workbook(spec, xlsx)
manifest = json.loads(xlsx.with_suffix(".manifest.json")
                      .read_text(encoding="utf-8"))
assert manifest["file_hash"]

# CLI-level document-spec roundtrip: gg.py paper reads JSON spec and
# writes the generated document — both through the deployed CLI layer.
import gg
spec_json = root / "paper-spec.json"
spec_json.write_text(json.dumps(
    {"author": "합성", "writing_year": 2026, "years": 5,
     "school_profile": {"mode": "school"}},
    ensure_ascii=False), encoding="utf-8")
code = gg.main(["paper", str(root), "--input", "paper-spec.json",
                "--out", "build/검토전_본문.md"])
assert code == 0
assert "확인 필요" in (root / "build/검토전_본문.md").read_text(
    encoding="utf-8")
print("UTF8-BAND-OK")
'''

    def test_korean_flow_under_ascii_locale(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-p1utf8-")
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        driver = d / "driver.py"
        driver.write_text(self.DRIVER, encoding="utf-8")
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["PYTHONUTF8"] = "0"
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, str(driver), str(SCRIPTS), str(d)],
            capture_output=True, text=True, env=env)
        self.assertEqual(0, proc.returncode,
                         "stdout=%r stderr=%r" % (proc.stdout, proc.stderr))
        self.assertIn("UTF8-BAND-OK", proc.stdout)

    def test_a_owned_scripts_declare_encoding(self):
        """Narrow A-scope check: every A-owned script has zero implicit-
        encoding Path text calls.  The whole-runtime contract lives in
        TextIoTests.test_locale_independent_text_io (may stay red on
        B-owned files until P1_B lands)."""
        hits = {}
        for name in sorted(A_OWNED_SCRIPTS):
            lines = text_calls_without_encoding(
                (SCRIPTS / name).read_text(encoding="utf-8"))
            if lines:
                hits[name] = lines
        self.assertEqual({}, hits, json.dumps(hits))


class QuestionLadderP1Tests(ContractCase):
    """H8: 'unknown' answers consume the ask/help ladder like missing
    ones; terminal states keep reusing."""

    def test_unknown_full_ladder(self):
        core = runtime("gg_core")
        p = {"facts": {"u": {"field_id": "area", "answer_state": "unknown"}},
             "questions": {}}
        self.assertEqual("ask", core.question(p, "area"))
        p["questions"] = {"area": {"attempts": 1}}
        self.assertEqual("help", core.question(p, "area"))
        p["questions"] = {"area": {"attempts": 2}}
        self.assertEqual("deferred", core.question(p, "area"))

    def test_terminal_states_still_reuse(self):
        core = runtime("gg_core")
        for state in ("provided", "explicit_none", "withheld",
                      "not_applicable"):
            p = {"facts": {"u": {"field_id": "area", "answer_state": state}},
                 "questions": {}}
            with self.subTest(state=state):
                self.assertEqual("reuse", core.question(p, "area"))

    def test_unknown_does_not_mark_not_provided_reuse(self):
        """Mixed field: an unknown answer must not be mistaken for a
        terminal answer when no provided fact exists."""
        core = runtime("gg_core")
        p = {"facts": {"u": {"field_id": "area", "answer_state": "unknown"},
                       "n": {"field_id": "area",
                             "answer_state": "not_provided"}},
             "questions": {"area": {"attempts": 1}}}
        self.assertEqual("help", core.question(p, "area"))


if __name__ == "__main__":
    unittest.main()
