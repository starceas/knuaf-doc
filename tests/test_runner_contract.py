"""Runner-contract meta tests (repair-N1).

Every test copies the public tree to a disposable folder, changes the
*environment* (interpreter flags, PYTHONPATH, GG_VALIDATION_NESTED, or a
documented synthetic patch — never the real candidate), runs
``tools/run_validation.py`` as a subprocess, and asserts the N1 exit and
JSON contract.

The whole class skips inside a nested run (GG_VALIDATION_NESTED is set
by the spawning test), because every test here spawns runner
subprocesses — skipping prevents unbounded recursion.  Its test IDs are
the runner's ``ALLOWED_NESTED_SKIP_IDS`` entries; that finite list and
its purpose are documented in docs/validation/REGRESSIONS.md and the
runner docstring.  An ``-S`` interpreter flag is used to simulate a
dependency-less environment without installing or removing anything.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests._harness import ContractCase, REPO_ROOT, copy_public_tree

NESTED = "GG_VALIDATION_NESTED"
NATIVE_TID = ("tests.test_p2_windows.WindowsBandCase."
              "test_native_windows_two_process_contention")
NATIVE_REASON = "native Windows+NTFS required — withheld"


def _report_path(root, name):
    # Reports live NEXT TO the copied root: writing inside the copy would
    # turn into an undeclared file for any later bundle check on it.
    return Path(root).parent / name


def _run(root, *, args=(), interpreter_flags=(), env_extra=None):
    """Nested run: GG_VALIDATION_NESTED=1 is always set (the sanctioned
    value); only meaningful with --runtime-only."""
    env = dict(os.environ)
    env[NESTED] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    out = _report_path(root, "nested-report.json")
    proc = subprocess.run(
        [sys.executable, *interpreter_flags,
         str(Path(root) / "tools" / "run_validation.py"),
         *args, "--json", str(out)],
        cwd=root, capture_output=True, text=True, env=env)
    report = (json.loads(out.read_text(encoding="utf-8"))
              if out.exists() else None)
    return proc, report


def _run_unnested(root, *, args=(), interpreter_flags=(), env_extra=None):
    """Run WITHOUT setting NESTED — simulates an operator invocation."""
    env = dict(os.environ)
    env.pop(NESTED, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    out = _report_path(root, "unnested-report.json")
    proc = subprocess.run(
        [sys.executable, *interpreter_flags,
         str(Path(root) / "tools" / "run_validation.py"),
         *args, "--json", str(out)],
        cwd=root, capture_output=True, text=True, env=env)
    report = (json.loads(out.read_text(encoding="utf-8"))
              if out.exists() else None)
    return proc, report


def _refresh_manifest(root):
    """Regenerate the copy's manifest in place — disposable scratch
    lineage only; the real candidate's D-owned metadata is untouched."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B",
         str(Path(root) / "tools" / "build_validation_bundle.py"),
         "--root", str(root), "--write-manifest"],
        cwd=root, capture_output=True, text=True, env=env)


def _runner_module():
    """Load the candidate's run_validation.py for pure in-process
    classification checks (no subprocess, no recursion)."""
    spec = importlib.util.spec_from_file_location(
        "run_validation_under_test",
        REPO_ROOT / "tools" / "run_validation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(os.environ.get(NESTED),
                 "contract meta tests do not recurse inside a nested run")
class RunnerContractTests(ContractCase):
    """IDs are pinned in run_validation.ALLOWED_NESTED_SKIP_IDS."""

    def _public_copy(self, prefix):
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(tmp.cleanup)
        return copy_public_tree(Path(tmp.name) / "public")

    def _freshen(self, root):
        """Re-pin the scratch copy's manifest to its current bytes.

        The committed manifest pins the repair-input (R1) hashes; any
        scratch copy therefore drifts the moment an owned file changes,
        and ``test_bundle_lineage`` inside a nested suite run would
        report that drift as an unexpected failure.  Regenerating the
        manifest is permitted only for disposable scratch copies — the
        real candidate's D-owned metadata is never touched.
        """
        mp = _refresh_manifest(root)
        self.assertEqual(0, mp.returncode, mp.stderr[-500:])

    def test_missing_dependencies_block_run(self):
        """N1-02: with -S (no site-packages) the final runner must exit
        nonzero with runtime.status=not_run_dependency — a missing
        required dependency is a readiness failure, not a skip."""
        root = self._public_copy("knuaf-n1deps-")
        # freshen so the default-mode bundle gate stays clean and the
        # dependency failure is the sole cause under test
        self._freshen(root)
        proc, rep = _run_unnested(root, interpreter_flags=("-S",))
        self.assertIsNotNone(rep, proc.stderr)
        self.assertNotEqual(0, proc.returncode, proc.stdout)
        self.assertEqual("default", rep["mode"])
        self.assertEqual("not_run_dependency", rep["runtime"]["status"])
        self.assertEqual(0, rep["runtime"]["tests_run"])
        self.assertFalse(rep["full_validation_completed"])
        # the dependency diagnostics must be present in the JSON — a bare
        # nonzero exit or a bundle failure alone would not prove this.
        missing = {r["dist"] for r in rep["dependency"]["failed"]
                   if r["status"] == "missing"}
        self.assertTrue({"openpyxl", "python-docx", "pypdf"} <= missing,
                        rep["dependency"]["failed"])
        self.assertTrue(any("dependency not ready" in c
                            for c in rep["causes"]["incomplete"]),
                        rep["causes"])
        if sys.platform != "win32":
            na = {d["dist"] for d in rep["dependency"]["not_applicable"]}
            self.assertIn("pywin32", na)
        # pure dependency failure (bundle check out of the way) maps to
        # incomplete/exit 3 — and the nested path is not exempt either.
        root2 = self._public_copy("knuaf-n1deps2-")
        proc2, rep2 = _run(root2, args=("--runtime-only",),
                           interpreter_flags=("-S",))
        self.assertIsNotNone(rep2, proc2.stderr)
        self.assertEqual(3, proc2.returncode, proc2.stdout)
        self.assertEqual("incomplete", rep2["verdict"])
        self.assertEqual("not_run_dependency", rep2["runtime"]["status"])

    def test_dependency_stub_versions(self):
        """N1-03: synthetic stubs prove each failure form — importable
        but out-of-range version, and importable but version
        unverifiable — are readiness failures on THIS interpreter."""
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-n1stub-")
        self.addCleanup(tmp.cleanup)
        root = copy_public_tree(Path(tmp.name) / "public")
        stub = Path(tmp.name) / "stub"
        (stub / "openpyxl").mkdir(parents=True)
        (stub / "openpyxl" / "__init__.py").write_text(
            "# synthetic stub\n", encoding="utf-8")
        distinfo = stub / "openpyxl-9.9.9.dist-info"
        distinfo.mkdir()
        (distinfo / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: openpyxl\nVersion: 9.9.9\n",
            encoding="utf-8")
        proc, rep = _run(root, args=("--runtime-only",),
                         interpreter_flags=("-S",),
                         env_extra={"PYTHONPATH": str(stub)})
        self.assertIsNotNone(rep, proc.stderr)
        self.assertEqual(3, proc.returncode, proc.stdout)
        rows = {r["dist"]: r for r in rep["dependency"]["failed"]}
        self.assertEqual("unsupported-version",
                         rows["openpyxl"]["status"], rows)
        self.assertEqual("9.9.9", rows["openpyxl"]["version"])
        # same stub without dist metadata: import succeeds but the
        # declared version range cannot be verified -> still not ready.
        for p in distinfo.iterdir():
            p.unlink()
        distinfo.rmdir()
        proc2, rep2 = _run(root, args=("--runtime-only",),
                           interpreter_flags=("-S",),
                           env_extra={"PYTHONPATH": str(stub)})
        self.assertIsNotNone(rep2, proc2.stderr)
        self.assertEqual(3, proc2.returncode)
        rows2 = {r["dist"]: r for r in rep2["dependency"]["failed"]}
        self.assertEqual("installed-version-unchecked",
                         rows2["openpyxl"]["status"], rows2)

    def test_injected_skip_blocks_success(self):
        """N1-04: one ordinary unittest skip in a synthetic copy must
        turn the run incomplete — only the allowlisted IDs may skip."""
        injected = '''\
import unittest

class InjectedSkip(unittest.TestCase):
    def test_injected(self):
        self.skipTest("synthetic injected skip")
'''
        root = self._public_copy("knuaf-n1skip-")
        (root / "tests" / "test_injected_skip.py").write_text(
            injected, encoding="utf-8")
        self._freshen(root)
        proc, rep = _run(root, args=("--runtime-only",))
        self.assertIsNotNone(rep, proc.stderr)
        self.assertNotEqual(0, proc.returncode, proc.stdout)
        skipped = {s["test"] for s in rep["unapproved_skips"]}
        self.assertIn(
            "tests.test_injected_skip.InjectedSkip.test_injected", skipped)
        self.assertTrue(
            any("unapproved test skips" in c
                for c in rep["causes"]["incomplete"]),
            rep["causes"])
        self.assertFalse(rep["full_validation_completed"])
        self.assertFalse(rep["platform_validation_completed"])
        # R2: the external placeholder is classified, so the injected
        # skip is the sole blocker — deterministically incomplete/3.
        self.assertEqual("incomplete", rep["verdict"])
        self.assertEqual(3, proc.returncode, proc.stdout)
        # the declared gate is approved, not confused with the injection
        self.assertIn(NATIVE_TID, {s["test"]
                                   for s in rep["approved_external_skips"]})
        # a second skip copying the native reason verbatim is NOT exempt:
        # only the exact declared test id qualifies.
        injected2 = injected.replace(
            'self.skipTest("synthetic injected skip")',
            'self.skipTest(%r)' % NATIVE_REASON).replace(
            "InjectedSkip", "CopiedReason")
        root2 = self._public_copy("knuaf-n1skip2-")
        (root2 / "tests" / "test_injected_skip2.py").write_text(
            injected2, encoding="utf-8")
        self._freshen(root2)
        proc2, rep2 = _run(root2, args=("--runtime-only",))
        self.assertIsNotNone(rep2, proc2.stderr)
        self.assertEqual("incomplete", rep2["verdict"])
        self.assertEqual(3, proc2.returncode, proc2.stdout)
        self.assertIn(
            "tests.test_injected_skip2.CopiedReason.test_injected",
            {s["test"] for s in rep2["unapproved_skips"]})
        self.assertIn(NATIVE_TID, {s["test"]
                                   for s in rep2["approved_external_skips"]})

    def test_nested_flag_rejected_in_final_mode(self):
        """N1-04: GG_VALIDATION_NESTED non-empty in DEFAULT mode is a
        failed/incomplete run whatever its value; in --runtime-only only
        the exact value '1' is sanctioned."""
        for value in ("1", "yes"):
            with self.subTest(mode="default", value=value):
                root = self._public_copy("knuaf-n1flag-")
                self._freshen(root)
                proc, rep = _run_unnested(root,
                                          env_extra={NESTED: value})
                self.assertIsNotNone(rep, proc.stderr)
                self.assertNotEqual(0, proc.returncode,
                                    f"NESTED={value}: {proc.stdout}")
                self.assertTrue(any(NESTED in c
                                    for c in rep["causes"]["incomplete"]),
                                rep["causes"])
        with self.subTest(mode="runtime_only", value="bogus"):
            root = self._public_copy("knuaf-n1flag-")
            self._freshen(root)
            proc, rep = _run_unnested(root, args=("--runtime-only",),
                                      env_extra={NESTED: "bogus"})
            self.assertIsNotNone(rep, proc.stderr)
            self.assertNotEqual(0, proc.returncode, proc.stdout)
            # the bogus flag is the sole incomplete cause in a clean copy
            self.assertTrue(any(NESTED in c
                                for c in rep["causes"]["incomplete"]),
                            rep["causes"])
            self.assertEqual("incomplete", rep["verdict"])
            self.assertEqual(3, proc.returncode, proc.stdout)
        with self.subTest(mode="runtime_only", value="1"):
            root = self._public_copy("knuaf-n1flag-")
            self._freshen(root)
            proc, rep = _run(root, args=("--runtime-only",))
            self.assertIsNotNone(rep, proc.stderr)
            # the sanctioned value '1' is accepted: the flag must never
            # appear as an incomplete cause; the native placeholder skip
            # is approved-external so a clean nested run reports ok/0.
            self.assertFalse(any(NESTED in c
                                 for c in rep["causes"]["incomplete"]),
                             rep["causes"])
            self.assertEqual("ok", rep["verdict"], proc.stdout)
            self.assertEqual(0, proc.returncode, proc.stdout)
            self.assertEqual("development_runtime_only",
                             rep["validation_scope"])
            # runtime-only never completes either flag even when clean;
            # the pending gate is still reported separately.
            self.assertFalse(rep["full_validation_completed"])
            self.assertFalse(rep["platform_validation_completed"])
            self.assertEqual(
                ["P2-L09-native"],
                [p["gate_id"] for p in rep["pending_external_checks"]])

    def test_xpass_surfaces_as_unexpected(self):
        """N1-05: if a documented defect stops reproducing (XPASS), the
        runner must report it as an unexpected failure — never pass.

        No real defect is open anymore (H9/R6 fixed in this candidate),
        so the open-defect scenario is wholly synthetic and confined to
        the copy: its inventory gains a fake open defect (SYNTH-XPASS)
        whose test feeds ``expect_baseline_defect`` an observation that
        already matches the desired contract — the baseline signature
        cannot reproduce — so the harness fails it as an "unexpected
        pass" exactly like a real stopped-reproducing defect.  The
        exercise is deterministic regardless of production defect
        state."""
        injected = '''\
from tests._harness import ContractCase


class SyntheticXpass(ContractCase):
    def test_synthetic_xpass(self):
        self.expect_baseline_defect(
            "SYNTH-XPASS", gate="P3",
            spec="synthetic open defect: 2 + 2 == 5",
            observed=4,
            defect_match=lambda o: o == 5,
            desired_match=lambda o: o == 4)
'''
        root = self._public_copy("knuaf-n1xpass-")
        tid = ("tests.test_synthetic_xpass.SyntheticXpass."
               "test_synthetic_xpass")
        (root / "tests" / "test_synthetic_xpass.py").write_text(
            injected, encoding="utf-8")
        inv_path = root / "tests" / "regression_inventory.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        inv["defects"].append({
            "id": "SYNTH-XPASS",
            "title": "synthetic open defect for XPASS accounting",
            "applies_to_main": True,
            "status": "open",
            "gate": "P3",
            "evidence_type": "runtime",
            "baseline_signature": "2 + 2 == 5",
            "test": tid})
        inv_path.write_text(
            json.dumps(inv, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        # unlike the retired gg_core patch this touches no baseline file,
        # so the scratch manifest re-pins cleanly and the synthetic XPASS
        # stays the sole failure under test
        self._freshen(root)
        proc, rep = _run(root, args=("--runtime-only",))
        self.assertIsNotNone(rep, proc.stderr)
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertEqual("unexpected_failures", rep["verdict"])
        self.assertEqual(1, rep["runtime"]["unexpected_failed"])
        failed = {e["test"]: e for e in rep["runtime"]["entries"]
                  if e["category"] == "failed"}
        self.assertIn(tid, failed)
        self.assertIn("unexpected pass", failed[tid]["detail"])
        # the documented open defect is also reported not-reproduced —
        # both XPASS signals must fire, never a silent pass
        self.assertTrue(
            any("SYNTH-XPASS" in w and "was not reproduced" in w
                for w in rep["inventory_warnings"]),
            rep["inventory_warnings"])

    def test_fixed_defect_mapping_required(self):
        """P1: a defect marked fixed whose regression test silently
        disappears must surface as an inventory mismatch — a green suite
        cannot be built by deleting the evidence test."""
        root = self._public_copy("knuaf-p1fix-")
        gate = root / "tests" / "test_question_gate.py"
        src = gate.read_text(encoding="utf-8")
        patched = src.replace(
            "def test_unknown_answer_reaches_help",
            "def disabled_unknown_answer_reaches_help")
        self.assertNotEqual(src, patched,
                            "test rename patch did not apply to the copy")
        gate.write_text(patched, encoding="utf-8")
        self._freshen(root)
        proc, rep = _run(root, args=("--runtime-only",))
        self.assertIsNotNone(rep, proc.stderr)
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertEqual("unexpected_failures", rep["verdict"])
        self.assertTrue(any("H8" in w and "did not run" in w
                            for w in rep["inventory_warnings"]),
                        rep["inventory_warnings"])

    def test_unexpected_failure_beats_incomplete(self):
        """N1-05: an unexpected failure plus an unapproved skip keeps
        BOTH diagnostics; verdict priority is unexpected_failures."""
        injected = '''\
import unittest

class InjectedBad(unittest.TestCase):
    def test_fails(self):
        self.fail("synthetic injected failure")

    def test_skips(self):
        self.skipTest("synthetic injected skip")
'''
        root = self._public_copy("knuaf-n1both-")
        (root / "tests" / "test_injected_bad.py").write_text(
            injected, encoding="utf-8")
        self._freshen(root)
        proc, rep = _run(root, args=("--runtime-only",))
        self.assertIsNotNone(rep, proc.stderr)
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertEqual("unexpected_failures", rep["verdict"])
        self.assertTrue(rep["causes"]["unexpected"])
        self.assertTrue(rep["causes"]["incomplete"])
        skipped = {s["test"] for s in rep["unapproved_skips"]}
        self.assertIn(
            "tests.test_injected_bad.InjectedBad.test_skips", skipped)

        # R2 regression for the observed recorder blindness: a failed
        # subTest must surface as a failed parent entry — R1's runner
        # recorded 243 entries for 244 started tests and reported
        # unexpected_failed=0 while unittest said FAILED(failures=1).
        subfail = '''\
import unittest

class InjectedSubtest(unittest.TestCase):
    def test_parent(self):
        for i in range(2):
            with self.subTest(i=i):
                if i:
                    self.fail("synthetic subtest failure")
'''
        root2 = self._public_copy("knuaf-r2sub-")
        (root2 / "tests" / "test_injected_subtest.py").write_text(
            subfail, encoding="utf-8")
        self._freshen(root2)
        proc2, rep2 = _run(root2, args=("--runtime-only",))
        self.assertIsNotNone(rep2, proc2.stderr)
        self.assertEqual(1, proc2.returncode, proc2.stdout)
        self.assertEqual("unexpected_failures", rep2["verdict"])
        pid = ("tests.test_injected_subtest.InjectedSubtest."
               "test_parent")
        byid = {e["test"]: e for e in rep2["runtime"]["entries"]}
        self.assertIn(pid, byid)
        self.assertEqual("failed", byid[pid]["category"])
        ev_types = [e["type"] for e in byid[pid].get("events", [])]
        self.assertIn("subtest_failure", ev_types)
        # parent counted once as a started test and once as failed
        self.assertEqual(rep2["runtime"]["tests_run"],
                         len(rep2["runtime"]["entries"]))
        self.assertGreaterEqual(rep2["runtime"]["failure_events"], 1)
        self.assertFalse(rep2["runtime"]["accounting"]["errors"])

        # R2 scoped default run: refresh the copy's manifest (scratch
        # lineage only) so the bundle gate exercises real behaviour —
        # the edited runner file is otherwise stale vs the committed pin.
        # The copy's meta-test file is stubbed so the inner DEFAULT run
        # cannot recurse (NESTED cannot be set in default mode — that
        # would be an incomplete cause by contract).
        root3 = self._public_copy("knuaf-r2scoped-")
        (root3 / "tests" / "test_runner_contract.py").write_text(
            "import unittest\n\n\n"
            "class MetaStub(unittest.TestCase):\n"
            "    def test_placeholder(self):\n"
            "        pass\n", encoding="utf-8")
        self._freshen(root3)
        proc3, rep3 = _run_unnested(root3)
        self.assertIsNotNone(rep3, proc3.stderr)
        self.assertEqual("ok_platform_scoped", rep3["verdict"],
                         proc3.stdout)
        self.assertEqual(0, proc3.returncode, proc3.stdout)
        self.assertEqual("current_platform", rep3["validation_scope"])
        self.assertTrue(rep3["platform_validation_completed"])
        self.assertFalse(rep3["full_validation_completed"])
        self.assertEqual(0, rep3["runtime"]["unexpected_failed"])
        self.assertEqual(rep3["runtime"]["tests_run"],
                         len(rep3["runtime"]["entries"]),
                         "one terminal entry per started parent")
        # exact sole pending gate, and the skip record is preserved
        self.assertEqual(
            [{"gate_id": "P2-L09-native", "status": "not_run",
              "support": "withheld", "implementation": "placeholder_only",
              "test_id": NATIVE_TID,
              "required_environment": "native Windows+NTFS",
              "reason": NATIVE_REASON,
              "observed_platform": sys.platform}],
            rep3["pending_external_checks"])
        self.assertEqual(
            [{"test": NATIVE_TID, "reason": NATIVE_REASON,
              "rule": "P2-L09-native accepted external placeholder",
              "gate_id": "P2-L09-native"}],
            rep3["approved_external_skips"])
        self.assertNotIn(NATIVE_TID, {s["test"]
                                      for s in rep3["unapproved_skips"]})
        # the skipped record itself stays visible in entries
        nat = {e["test"]: e for e in rep3["runtime"]["entries"]
               if e["test"] == NATIVE_TID}
        self.assertEqual("skipped", nat[NATIVE_TID]["category"])

        # injected subtest failure PLUS the pending native skip in
        # default mode: failure wins (exit 1), both flags false, the
        # pending fact stays visible — never a repeat of R1's 0.
        root4 = self._public_copy("knuaf-r2subd-")
        (root4 / "tests" / "test_injected_subtest.py").write_text(
            subfail, encoding="utf-8")
        (root4 / "tests" / "test_runner_contract.py").write_text(
            "import unittest\n\n\n"
            "class MetaStub(unittest.TestCase):\n"
            "    def test_placeholder(self):\n"
            "        pass\n", encoding="utf-8")
        self._freshen(root4)
        proc4, rep4 = _run_unnested(root4)
        self.assertIsNotNone(rep4, proc4.stderr)
        self.assertEqual("unexpected_failures", rep4["verdict"])
        self.assertEqual(1, proc4.returncode, proc4.stdout)
        self.assertFalse(rep4["platform_validation_completed"])
        self.assertFalse(rep4["full_validation_completed"])
        self.assertEqual(1, rep4["runtime"]["unexpected_failed"])
        self.assertIn(pid, {e["test"]
                            for e in rep4["runtime"]["entries"]
                            if e["category"] == "failed"})
        self.assertEqual(
            ["P2-L09-native"],
            [p["gate_id"] for p in rep4["pending_external_checks"]])

        # R3: bundle tamper control — folded in here so the nested-skip
        # allowlist stays at its original ten IDs (a separate method
        # would need an eleventh).  A byte-level change to a declared
        # file without re-pinning is bundle tamper: the default run must
        # report bundle=fail and verdict unexpected_failures (exit 1).
        root5 = self._public_copy("knuaf-r3tamper-")
        # stub the meta-test file so the inner default run cannot
        # recurse (no NESTED allowed in default mode); do NOT refresh
        # the manifest — stub + tamper both stay detectable.
        (root5 / "tests" / "test_runner_contract.py").write_text(
            "import unittest\n\n\n"
            "class MetaStub(unittest.TestCase):\n"
            "    def test_placeholder(self):\n"
            "        pass\n", encoding="utf-8")
        victim = root5 / "tests" / "test_question_gate.py"
        src5 = victim.read_text(encoding="utf-8")
        victim.write_text(src5 + "\n# scratch tamper byte\n",
                          encoding="utf-8")
        proc5, rep5 = _run_unnested(root5)
        self.assertIsNotNone(rep5, proc5.stderr)
        self.assertEqual("default", rep5["mode"])
        self.assertEqual("fail", rep5["bundle"]["status"], rep5["bundle"])
        self.assertEqual("unexpected_failures", rep5["verdict"])
        self.assertEqual(1, proc5.returncode, proc5.stdout)
        self.assertTrue(any("bundle" in c
                            for c in rep5["causes"]["unexpected"]),
                        rep5["causes"])
        self.assertFalse(rep5["platform_validation_completed"])
        self.assertFalse(rep5["full_validation_completed"])


class RunnerAccountingTests(unittest.TestCase):
    """Pure, non-recursing controls for the R2 outcome accounting and
    the fail-closed external-gate classifier.

    These run synthetic unittest suites through RecordingResult and call
    the runner's pure functions in-process — no subprocess is spawned,
    so they execute identically inside a nested run and need no entry in
    ALLOWED_NESTED_SKIP_IDS.  Platform branches use mock.patch.object on
    sys.platform — labeled simulations, not native execution.
    """

    @classmethod
    def setUpClass(cls):
        cls.rv = _runner_module()

    def _summarize(self, *cases):
        suite = unittest.TestSuite()
        for c in cases:
            suite.addTests(unittest.TestLoader().loadTestsFromTestCase(c))
        result = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0,
            resultclass=self.rv.RecordingResult).run(suite)
        return self.rv._summarize_result(result, {})

    def _gate(self, entries, platform="darwin"):
        with mock.patch.object(sys, "platform", platform):
            return self.rv._evaluate_external_gate(entries, True)

    # -- parent/subtest accounting ---------------------------------------

    def test_failed_subtest_makes_parent_failed_once(self):
        """R1 defect shape: a failed subTest previously vanished from
        records (243 entries for 244 started, unexpected_failed=0)."""
        class T(unittest.TestCase):
            def test_parent(self):
                for i in range(2):
                    with self.subTest(i=i):
                        if i:
                            self.fail("boom")
        rep = self._summarize(T)
        e = rep["entries"][0]
        self.assertTrue(e["test"].endswith("test_parent"))
        self.assertEqual("failed", e["category"])
        self.assertIn("subtest_failure",
                      [ev["type"] for ev in e["events"]])
        self.assertEqual(1, rep["tests_run"])
        self.assertEqual(1, len(rep["entries"]))
        self.assertEqual(1, rep["unexpected_failed"])
        self.assertEqual(1, rep["failure_events"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_multiple_failed_subtests_one_failed_parent(self):
        class T(unittest.TestCase):
            def test_parent(self):
                for i in range(3):
                    with self.subTest(i=i):
                        if i:
                            self.fail("boom %d" % i)
        rep = self._summarize(T)
        self.assertEqual(1, rep["unexpected_failed"])
        self.assertEqual(2, rep["failure_events"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_subtest_error_and_skip_mixed_precedence(self):
        class T(unittest.TestCase):
            def test_parent(self):
                with self.subTest(x=1):
                    raise RuntimeError("err")
                with self.subTest(x=2):
                    self.skipTest("later skip")
        rep = self._summarize(T)
        e = rep["entries"][0]
        # failure/error beats skip; both events preserved for diagnostics
        self.assertEqual("failed", e["category"])
        self.assertEqual({"subtest_error", "subtest_skip"},
                         {ev["type"] for ev in e["events"]})
        self.assertEqual(0, rep["skipped"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_skipped_subtests_make_parent_skipped(self):
        class T(unittest.TestCase):
            def test_parent(self):
                with self.subTest(x=1):
                    self.skipTest("only skip")
        rep = self._summarize(T)
        e = rep["entries"][0]
        self.assertEqual("skipped", e["category"])
        self.assertEqual("only skip", e["detail"])
        self.assertEqual(1, rep["skipped"])
        self.assertEqual(0, rep["unexpected_failed"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_clean_subtests_do_not_inflate_counts(self):
        class T(unittest.TestCase):
            def test_parent(self):
                for i in range(3):
                    with self.subTest(i=i):
                        pass
        rep = self._summarize(T)
        self.assertEqual(1, rep["tests_run"])
        self.assertEqual(1, len(rep["entries"]))
        self.assertEqual("passed", rep["entries"][0]["category"])
        self.assertEqual(3, len(rep["entries"][0]["events"]))
        self.assertEqual(0, rep["unexpected_failed"])

    # -- fixture events -----------------------------------------------------

    def test_class_fixture_error_is_fixture_event(self):
        class T(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("class boom")
            def test_never(self):
                pass
        rep = self._summarize(T)
        self.assertEqual(0, rep["tests_run"])
        self.assertEqual([], rep["entries"])
        self.assertEqual(1, rep["unexpected_failed"])  # fixture counts
        fx = rep["fixture_events"]
        self.assertEqual(1, len(fx))
        self.assertEqual("error", fx[0]["type"])
        self.assertIn("setUpClass", fx[0]["test"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_class_fixture_skip_is_fixture_event(self):
        class T(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("class skip")
            def test_never(self):
                pass
        rep = self._summarize(T)
        self.assertEqual(0, rep["tests_run"])
        fx = rep["fixture_events"]
        self.assertEqual(1, len(fx))
        self.assertEqual("skip", fx[0]["type"])
        self.assertEqual(0, rep["unexpected_failed"])
        self.assertEqual(0, rep["skipped"])
        self.assertEqual([], rep["accounting"]["errors"])

    # -- expectedFailure / unexpectedSuccess --------------------------------

    def test_expected_failure_is_unexpected_failure(self):
        class T(unittest.TestCase):
            @unittest.expectedFailure
            def test_xf(self):
                self.fail("documented elsewhere — not a KNOWN_DEFECTS row")
        rep = self._summarize(T)
        self.assertEqual(1, rep["unexpected_failed"])
        self.assertEqual("failed", rep["entries"][0]["category"])
        self.assertEqual([], rep["accounting"]["errors"])

    def test_unexpected_success_is_unexpected_failure(self):
        class T(unittest.TestCase):
            @unittest.expectedFailure
            def test_xp(self):
                pass
        rep = self._summarize(T)
        self.assertEqual(1, rep["unexpected_failed"])
        self.assertEqual("failed", rep["entries"][0]["category"])
        self.assertEqual([], rep["accounting"]["errors"])

    # -- external-gate classification (pure, labeled platform simulation) ---

    def _skipped_gate_entry(self, reason=NATIVE_REASON):
        return {"test": NATIVE_TID, "category": "skipped",
                "detail": reason}

    def test_gate_approved_exact_skip_darwin(self):
        approved, warnings, obs = self._gate([self._skipped_gate_entry()])
        self.assertEqual(1, len(approved))
        self.assertEqual(NATIVE_TID, approved[0]["test"])
        self.assertEqual("P2-L09-native", approved[0]["gate_id"])
        self.assertEqual([], warnings)
        self.assertIsNotNone(obs)

    def test_gate_approved_exact_skip_linux(self):
        approved, warnings, _ = self._gate([self._skipped_gate_entry()],
                                           platform="linux")
        self.assertEqual(1, len(approved))
        self.assertEqual([], warnings)

    def test_gate_wrong_reason_not_approved(self):
        approved, warnings, _ = self._gate(
            [self._skipped_gate_entry(reason="some other reason")])
        self.assertEqual([], approved)
        self.assertEqual([], warnings)  # stays an ordinary unapproved skip

    def test_gate_skip_on_win32_not_approved(self):
        # simulated platform only — never a native claim
        approved, warnings, _ = self._gate(
            [self._skipped_gate_entry()], platform="win32")
        self.assertEqual([], approved)
        self.assertEqual([], warnings)

    def test_gate_forged_success_is_mismatch(self):
        approved, warnings, _ = self._gate(
            [{"test": NATIVE_TID, "category": "passed"}])
        self.assertEqual([], approved)
        self.assertTrue(any("forged" in w for w in warnings))

    def test_gate_forged_known_defect_is_mismatch(self):
        approved, warnings, _ = self._gate(
            [{"test": NATIVE_TID, "category": "known_baseline_defect"}])
        self.assertEqual([], approved)
        self.assertTrue(any("forged" in w for w in warnings))

    def test_gate_missing_id_is_mismatch(self):
        approved, warnings, obs = self._gate(
            [{"test": "tests.other.T.test_x", "category": "passed"}])
        self.assertEqual([], approved)
        self.assertTrue(any("missing" in w for w in warnings))
        self.assertIsNone(obs)

    def test_gate_duplicate_id_is_mismatch(self):
        approved, warnings, _ = self._gate(
            [self._skipped_gate_entry(), self._skipped_gate_entry()])
        self.assertEqual(1, len(approved))  # first is still classified
        self.assertTrue(any("appeared 2 times" in w for w in warnings))

    def test_gate_genuine_failure_never_exempt(self):
        approved, warnings, obs = self._gate(
            [{"test": NATIVE_TID, "category": "failed",
              "detail": "native Windows run not recorded"}])
        self.assertEqual([], approved)
        self.assertEqual([], warnings)  # normal failure path, exit 1

    # -- verdict / completion precedence (pure) ------------------------------

    def test_verdict_precedence_failure_over_incomplete(self):
        v, c = self.rv._decide_verdict(["u"], ["i"], [{}], "default")
        self.assertEqual(("unexpected_failures", 1), (v, c))

    def test_verdict_incomplete_before_scoped(self):
        v, c = self.rv._decide_verdict([], ["i"], [{}], "default")
        self.assertEqual(("incomplete", 3), (v, c))

    def test_verdict_scoped_only_in_default_with_pending(self):
        v, c = self.rv._decide_verdict([], [], [{"g": 1}], "default")
        self.assertEqual(("ok_platform_scoped", 0), (v, c))
        v2, c2 = self.rv._decide_verdict([], [], [{"g": 1}],
                                       "runtime_only")
        self.assertEqual(("ok", 0), (v2, c2))

    def test_verdict_ok_only_when_no_pending(self):
        v, c = self.rv._decide_verdict([], [], [], "default")
        self.assertEqual(("ok", 0), (v, c))

    def _clean_runtime(self):
        return {"status": "completed", "tests_run": 10,
                "unexpected_failed": 0,
                "accounting": {"errors": []}}

    def test_flags_scoped_pending_blocks_full(self):
        plat, full = self.rv._completion_flags(
            "default", "pass", True, self._clean_runtime(),
            [], [], [], [], [{"gate_id": "P2-L09-native"}])
        self.assertTrue(plat)
        self.assertFalse(full)

    def test_flags_no_pending_allows_full(self):
        plat, full = self.rv._completion_flags(
            "default", "pass", True, self._clean_runtime(),
            [], [], [], [], [])
        self.assertTrue(plat)
        self.assertTrue(full)

    def test_flags_runtime_only_never_completes(self):
        plat, full = self.rv._completion_flags(
            "runtime_only", "skipped_runtime_only", True,
            self._clean_runtime(), [], [], [], [], [])
        self.assertFalse(plat)
        self.assertFalse(full)

    def test_flags_incomplete_cause_blocks_both(self):
        plat, full = self.rv._completion_flags(
            "default", "pass", True, self._clean_runtime(),
            [], [], ["unapproved skip"], [], [])
        self.assertFalse(plat)
        self.assertFalse(full)


if __name__ == "__main__":
    unittest.main()
