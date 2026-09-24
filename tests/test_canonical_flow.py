"""Canonical init/apply/check/export flow on the deployed main runtime.

Positive controls assert the documented contract on paths that exist in
main; known-defect controls reproduce the documented baseline defect with
an exact signature.  Synthetics only — no real school/user documents.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests._harness import (
    ContractCase,
    SCRIPTS,
    claim_of,
    fact_op,
    runtime,
    script_source,
    section_claim,
    section_op,
    source_op,
    text_calls_without_encoding,
    write_text,
)


def _assert_v2_lock_released(case, root):
    """v2 contract: .gg-lock is a persistent protocol dir; the guard and
    protocol survive every operation while owner.json is released."""
    lock = Path(root) / ".gg-lock"
    case.assertTrue(lock.is_dir(), "v2 lock protocol dir missing")
    case.assertTrue((lock / "protocol.json").is_file())
    case.assertTrue((lock / "guard").exists())
    case.assertFalse(
        (lock / "owner.json").exists(),
        "owner.json must be released after the operation",
    )


class CanonicalFlowTests(ContractCase):
    def test_init_creates_schema2_canonical(self):
        core = runtime("gg_core")
        root = self.make_project()
        p = core.load(root)
        self.assertEqual(p["schema_version"], 2)
        self.assertEqual(p["revision"], 0)
        _assert_v2_lock_released(self, root)
        with self.assertRaises(ValueError):
            core.init(root)  # never overwrites an existing canonical

    def _apply_answer(self, root):
        write_text(root, "answer.txt", "농장명: 행복농장\n")
        write_text(root, "intro.md", "농장명은 행복농장이다.\n")
        fact = fact_op("farm_name", "farm_name", "행복농장", None,
                       claim_id="c1")
        ops = [
            source_op(claims=claim_of(fact["value"])),
            fact,
            section_op("intro", "머리말", "intro.md",
                       claims=[section_claim(fact["value"],
                                             "농장명은 행복농장이다")]),
        ]
        return ops

    def test_apply_revision_idempotency_and_request_conflict(self):
        core = runtime("gg_core")
        root = self.make_project()
        ops = self._apply_answer(root)
        change = {"request_id": "synthetic-claim", "ops": ops}
        p = core.apply(root, change, 0)
        self.assertEqual(p["revision"], 1)
        self.assertEqual(p["requests"]["synthetic-claim"]["revision"], 1)
        # Same request_id + identical change replays the recorded result.
        self.assertEqual(core.apply(root, change, 0)["revision"], 1)
        # Same request_id + different change is a conflict, not a write.
        with self.assertRaises(ValueError):
            core.apply(root, {"request_id": "synthetic-claim", "ops": []}, 0)

    def test_cas_conflict_and_missing_request_id_rejected(self):
        core = runtime("gg_core")
        root = self.make_project()
        before = (root / "project.json").read_bytes()
        with self.assertRaises(ValueError):
            core.apply(root, {"request_id": "x", "ops": []}, 5)
        for bad in ({"ops": []}, {"request_id": "", "ops": []},
                    {"request_id": 7, "ops": []}):
            with self.assertRaises(ValueError):
                core.apply(root, bad, 0)
        self.assertEqual(before, (root / "project.json").read_bytes())
        _assert_v2_lock_released(self, root)

    def test_invalid_op_leaves_canonical_bytes_unchanged(self):
        core = runtime("gg_core")
        root = self.make_project()
        self._apply_answer(root)
        before = (root / "project.json").read_bytes()
        with self.assertRaises(ValueError):
            core.apply(
                root,
                {"request_id": "bad-op",
                 "ops": [{"collection": "facts", "value": {"id": "broken"}}]},
                core.load(root)["revision"],
            )
        self.assertEqual(before, (root / "project.json").read_bytes())
        _assert_v2_lock_released(self, root)

    def test_utf8_canonical_roundtrip(self):
        core = runtime("gg_core")
        root = self.make_project()
        ops = self._apply_answer(root)
        core.apply(root, {"request_id": "utf8", "ops": ops}, 0)
        p = core.load(root)
        self.assertEqual(p["facts"]["farm_name"]["value"], "행복농장")
        raw = (root / "project.json").read_bytes()
        self.assertIn("행복농장".encode("utf-8"), raw)

    def test_checks_and_draft_export(self):
        core = runtime("gg_core")
        root = self.make_project()
        ops = self._apply_answer(root)
        p = core.apply(root, {"request_id": "synthetic-claim", "ops": ops}, 0)
        report = core.checks(root, p)
        self.assertIsInstance(report, list)
        self.assertFalse([
            r for r in report
            if r["check_id"] == "body_finance_crosscheck"
        ])
        value = core.export(root, "draft")
        self.assertTrue(Path(value["path"]).exists())
        _assert_v2_lock_released(self, root)

    def test_integer_id_rejected_before_store(self):
        """C3 fixed in P1: non-string record id fails before any write.

        Baseline evidence (main 29abec0): apply() stored the int-keyed
        record, canonical bytes changed, and the next load() raised
        ValueError "ID/개정번호 오류: 7".  The fixed contract rejects the
        op before storing: canonical bytes, revision, and the request
        ledger stay untouched and a following valid request still applies.
        """
        core = runtime("gg_core")
        root = self.make_project()
        before = (root / "project.json").read_bytes()
        with self.assertRaises(ValueError) as cm:
            core.apply(
                root,
                {"request_id": "rid",
                 "ops": [{"collection": "tasks", "value": {"id": 7}}]},
                0,
            )
        self.assertIn("ID", str(cm.exception))
        self.assertEqual(before, (root / "project.json").read_bytes())
        p = core.load(root)  # canonical still loads
        self.assertEqual(p["revision"], 0)
        self.assertEqual(p["requests"], {})
        # the project is not poisoned: a normal request applies afterwards
        p = core.apply(
            root,
            {"request_id": "ok",
             "ops": [{"collection": "tasks", "value": {"id": "t1"}}]},
            0,
        )
        self.assertEqual(p["revision"], 1)
        self.assertEqual(p["tasks"]["t1"]["revision"], 1)


class LockTests(ContractCase):
    def test_single_writer_exclusion_and_reacquire(self):
        core = runtime("gg_core")
        root = self.make_project()
        held = core.Lock(root)
        held.__enter__()
        try:
            with self.assertRaises(ValueError):
                core.Lock(root).__enter__()
        finally:
            held.__exit__(None, None, None)
        second = core.Lock(root)
        second.__enter__()
        second.__exit__(None, None, None)

    def test_stale_lock_has_no_recovery_path(self):
        """C4 fixed in P2: a crashed writer's stale owner record blocks
        writes, and ``gg.py unlock`` (gg_lock.cleanup_owner) is the
        documented recovery path — it removes only the stale owner under
        a freshly-acquired guard, then writes proceed.

        Baseline evidence (main 29abec0): a leftover .gg-lock made
        apply() raise '쓰기 잠금' forever; gg.py had no unlock/recovery
        command.  The v2 contract keeps .gg-lock as the persistent
        protocol dir and provides the unlock path."""
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        # Fabricate a crashed-writer leftover: a real owner record whose
        # guard was released without cleanup.  In v2 the guard (fd) is the
        # write authority and the owner record is evidence — the leftover
        # record does not wedge writes, and ``unlock`` clears it.
        # A live-held guard still blocks a second writer — that is the
        # lock working, and cleanup_owner refuses a live guard.
        live = gg_lock.Lock(root)
        live.__enter__()
        try:
            with self.assertRaises(ValueError):
                core.apply(root, {"request_id": "x", "ops": []}, 0)
            with self.assertRaises(gg_lock.LockError):
                gg_lock.cleanup_owner(root)
            owner = (root / ".gg-lock" / "owner.json").read_bytes()
        finally:
            live.__exit__(None, None, None)
        # Release removed the live owner; restoring it fabricates a
        # crashed-writer leftover — evidence without a live guard.
        (root / ".gg-lock" / "owner.json").write_bytes(owner)
        probe = gg_lock.probe_lock(root)
        self.assertEqual(probe["lock_state"], "free")
        self.assertIsNotNone(probe["owner"])
        # The recovery command exists on the public CLI; unlock removes
        # only the stale owner — guard/protocol are preserved.
        cli = script_source("gg.py")
        self.assertIn('"unlock"', cli)
        result = gg_lock.cleanup_owner(root)
        self.assertTrue(result["owner_cleaned"])
        self.assertFalse((root / ".gg-lock" / "owner.json").exists())
        self.assertTrue((root / ".gg-lock" / "protocol.json").is_file())
        p = core.apply(
            root,
            {"request_id": "after-unlock",
             "ops": [{"collection": "tasks", "value": {"id": "t1"}}]},
            0,
        )
        self.assertEqual(p["revision"], 1)


class EntryContractTests(ContractCase):
    def test_cli_status_exit_contract(self):
        root = self.make_project()
        gg = SCRIPTS / "gg.py"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        missing = subprocess.run(
            [sys.executable, str(gg), "status", str(root / "missing")],
            capture_output=True, text=True, env=env)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)["status"], "blocked")
        ready = subprocess.run(
            [sys.executable, str(gg), "status", str(root)],
            capture_output=True, text=True, env=env)
        self.assertEqual(ready.returncode, 0)
        payload = json.loads(ready.stdout)
        for key in ("skill_ready", "user_finish_pending",
                    "professor_approval_pending", "completion"):
            self.assertIn(key, payload)

    def test_python_floor_guard_declared(self):
        """C5 fixed in P1: gg.py pre-rejects Python <3.10 before the core
        import and any side effect.

        Baseline evidence (main 29abec0): gg.py had no version floor at
        all.  Behavioral subprocess proof (version shim + poisoned core)
        lives in tests/test_p1_core.py::PythonFloorTests.
        """
        src = script_source("gg.py")
        guard = src.find("sys.version_info < (3, 10)")
        self.assertNotEqual(-1, guard, "gg.py lost the <3.10 reject")
        core_import = src.find("import gg_core")
        self.assertNotEqual(-1, core_import)
        self.assertLess(
            guard, core_import,
            "the version guard must run before `import gg_core`",
        )
        # the guard must be inside main() before argparse — module import
        # alone must not exit, so library-style `import gg` stays safe.
        self.assertLess(src.find("def main"), guard)

    def test_landscape_rotation_suppressed(self):
        """H2 fixed in P1: the layout helper is called with
        allow_landscape=False so wide tables never rotate the section.

        Baseline signature (main 29abec0): no allow_landscape=False call
        in build_docx.py. Generated-DOCX orientation/content proof:
        tests.test_p1_outputs.WideTableOrientationTests.
        """
        src = script_source("build_docx.py")
        self.assertIn(
            "allow_landscape=False", src.replace(" ", ""),
            "H2 regression: suppression call lost from build_docx.py",
        )

    def test_unrendered_nodes_rejected(self):
        """H3 fixed in P1: leftover nodes between TOC and body raise
        ValueError instead of being dropped silently.

        Baseline signature (main 29abec0): no _unrendered rejection.
        Synthetic-node behavioural proof:
        tests.test_p1_outputs.UnrenderedNodeTests.
        """
        src = script_source("build_docx.py")
        self.assertIn(
            "_unrendered", src,
            "H3 regression: unrendered-node rejection lost",
        )
        self.assertIn(
            "raise ValueError",
            src[src.find("_unrendered"):src.find("_unrendered") + 800],
            "H3: the _unrendered check must raise, not warn",
        )

    def test_missing_wanted_cells_rejected(self):
        """H5 fixed in P1: blank-copy rejects a map whose wanted cells
        are absent from the template before any partial output.

        Baseline signature (main 29abec0): no missing_wanted check.
        Synthetic-template behavioural proof:
        tests.test_p1_outputs.MissingTemplateCellTests.
        """
        src = script_source("gg_excel_template.py")
        self.assertIn(
            "missing_wanted", src,
            "H5 regression: missing-wanted rejection lost",
        )
        self.assertIn(
            "raise ValueError",
            src[src.find("missing_wanted"):src.find("missing_wanted") + 1200],
            "H5: missing_wanted must raise before output",
        )

    def test_office_automation_quit_blocked(self):
        """C6 fixed in P1: Windows COM automation is blocked before any
        COM creation/connection — run_word/run_excel exit code 2 before
        `import pythoncom`, and the wrapper returns rc=2 without
        spawning the helper.

        Baseline signature (main 29abec0): EnsureDispatch then
        unconditional Quit(). The dormant EnsureDispatch/Quit code below
        the guard is intentionally retained until native Windows+Office
        verification (see evidence/b/HANDOFF.md). Behavioural proof
        with fake COM modules:
        tests.test_p1_outputs.WindowsComBlockTests.
        """
        src = script_source("gg_office_win.py")
        self.assertIn("WINDOWS_COM_BLOCKED", src)
        for fn in ("def run_word", "def run_excel"):
            body = src[src.find(fn):]
            nxt = body.find("\ndef ", 4)
            body = body[:nxt if nxt != -1 else len(body)]
            guard = body.find("WINDOWS_COM_BLOCKED")
            imp = body.find("import pythoncom")
            self.assertNotEqual(-1, guard, f"{fn} lost the COM block")
            self.assertNotEqual(-1, imp, f"{fn} lost the pythoncom import")
            self.assertLess(
                guard, imp,
                f"{fn}: the block must run before `import pythoncom`")
        wrap = script_source("gg_office.py")
        wrap_body = wrap[wrap.find("def run_windows_com"):]
        self.assertLess(
            wrap_body.find("WINDOWS_COM_BLOCKED"),
            wrap_body.find("res = subprocess.run("),
            "C6: run_windows_com must check the block before spawning",
        )


class TextIoTests(ContractCase):
    def test_locale_independent_text_io(self):
        """C2 fixed in P1: every deployed script's Path text I/O must
        declare an explicit encoding (whole-runtime contract, split across
        A-owned and B-owned files).

        Baseline evidence (main 29abec0): 20+ read_text()/write_text()
        calls across deployed scripts carried no encoding= — behaviour
        depended on the locale default (e.g. cp949 on Korean Windows).
        Behavioral locale-band proof (LC_ALL=C + PYTHONUTF8=0) lives in
        tests/test_p1_core.py::Utf8IoTests.
        """
        hits = {}
        for path in sorted(SCRIPTS.glob("*.py")):
            lines = text_calls_without_encoding(
                path.read_text(encoding="utf-8"))
            if lines:
                hits[path.name] = lines
        self.assertEqual(
            {}, hits,
            "deployed scripts still have implicit-encoding text I/O: "
            + json.dumps(hits),
        )


if __name__ == "__main__":
    unittest.main()
