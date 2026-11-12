"""PR-scope contract: discovery sets, shard merge, final report, inventory, XPASS.

Design §7 ①②③④⑤⑥.  Everything here is derived from a live discovery, never
from a hardcoded count: adding a test module must not break this file.  No
shard is ever *run* — the reports are built from the same discovery functions
the runner uses, so a report that disagrees with discovery is exactly what the
negative cases exercise.
"""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_validation_report as cvr  # noqa: E402
from tools import run_validation as rv  # noqa: E402
from tools import validation_sharding as vs  # noqa: E402

REPORT_CHECKER = ROOT / "tools" / "check_validation_report.py"
H9_TEST = "tests.test_finance_scope.FinanceScopeTests.test_won_unit_fact_is_checked"


def _discovery(scope="code"):
    modules, ordered_ids = vs.discover_modules(ROOT)
    modules = vs.scoped_modules(modules, scope)
    return modules, vs.expected_ids(modules, ordered_ids, scope)


def _shard_ids(modules, index, count):
    assigned = vs.assign_modules(modules, count)[index]
    return assigned, [test.id() for module in assigned for test in modules[module]]


def _shard_report(index, count, scope="code", modules=None):
    """One shard report that satisfies every merge precondition."""
    if modules is None:
        modules, _ = _discovery(scope)
    assigned, ids = _shard_ids(modules, index, count)
    entries = [{"test": test_id, "category": "passed"} for test_id in ids]
    runtime = {
        "status": "completed",
        "tests_run": len(entries),
        "passed": len(entries),
        "known_baseline_defects": 0,
        "unexpected_failed": 0,
        "skipped": 0,
        "failure_events": 0,
        "entries": entries,
        "fixture_events": [],
        "accounting": {
            "framework": {
                "testsRun": len(entries), "failures": 0, "errors": 0,
                "skipped": 0, "expectedFailures": 0,
                "unexpectedSuccesses": 0, "was_successful": True},
            "errors": []},
        "suite_output_tail": "",
        "known_defect_records": {},
        "executed_modules": assigned,
        "module_timings_seconds": {module: 0.0 for module in assigned},
        "discovered_test_ids": ids,
    }
    return {
        "tool": "tools/run_validation.py",
        "verdict": "shard_completed",
        "exit_code": 0,
        "mode": "default",
        "validation_scope": "current_platform",
        "platform_validation_completed": False,
        "full_validation_completed": False,
        "python": {"version": "%d.%d.0" % sys.version_info[:2],
                   "executable": sys.executable, "requires": ">=3.10"},
        "root": str(ROOT),
        "baseline_main": "0" * 40,
        "dependency": {"ok": True, "failed": [], "manifest_error": None},
        "bundle": {"status": "pass"},
        "runtime": runtime,
        "shard": {"index": index + 1, "count": count, "modules": assigned,
                  "discovered_test_ids": ids, "executed_test_ids": ids},
    }


class ScopeSetTest(unittest.TestCase):
    """① the two scopes differ by exactly the deferred units, order preserved."""

    def test_code_scope_is_full_minus_the_deferred_units(self):
        modules, ordered_ids = vs.discover_modules(ROOT)
        full = vs.expected_ids(modules, ordered_ids, "full")
        code = vs.expected_ids(modules, ordered_ids, "code")
        dropped = {test.id() for unit in vs.PR_DEFERRED_UNITS
                   for test in modules[unit]}
        self.assertEqual(len(dropped), len(vs.NESTED_RUNS))
        self.assertEqual(set(code), set(full) - dropped)
        self.assertEqual(len(full) - len(code), len(dropped))
        self.assertEqual(full, list(ordered_ids))       # order preserved
        self.assertEqual(code, [t for t in full if t not in dropped])

    def test_deferred_units_exist_in_discovery(self):
        modules, _ = vs.discover_modules(ROOT)
        self.assertTrue(vs.PR_DEFERRED_UNITS <= set(modules))

    def test_unknown_scope_is_rejected(self):
        modules, ordered_ids = vs.discover_modules(ROOT)
        for scope in ("", "FULL", "fast", None):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    vs.expected_ids(modules, ordered_ids, scope)
                with self.assertRaises(ValueError):
                    vs.scoped_modules(modules, scope)

    def test_scope_never_hides_the_native_gate_or_the_fixed_h9_test(self):
        _, code = _discovery("code")
        self.assertIn(rv.NATIVE_GATE["test_id"], set(code))
        self.assertIn(H9_TEST, set(code))

    def test_scope_never_hides_an_approved_skill_skip(self):
        _, code = _discovery("code")
        missing = set(rv.ALLOWED_SKILL_SKIPS) - set(code)
        self.assertEqual(missing, set())


class ShardMergeTest(unittest.TestCase):
    """② one good merge, then nine ways a shard report must be refused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.modules, self.expected = _discovery("code")
        self.count = 5

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, reports, name="report.json"):
        path = self.tmp / name
        path.write_text(json.dumps(reports, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def _merge(self, reports, scope="code"):
        paths = [self._write(report, "shard-%d.json" % (index + 1))
                 for index, report in enumerate(reports)]
        return vs.merge_shard_reports(ROOT, paths, self.count, scope=scope)

    def _good(self):
        return [_shard_report(index, self.count, "code", self.modules)
                for index in range(self.count)]

    def test_good_merge_reconciles_to_discovery_order(self):
        template, runtime = self._merge(self._good())
        self.assertNotIn("shard", template)
        self.assertEqual(runtime["status"], "completed")
        self.assertEqual(runtime["discovered_test_ids"], self.expected)
        self.assertEqual([e["test"] for e in runtime["entries"]], self.expected)
        self.assertEqual(runtime["tests_run"], len(self.expected))
        self.assertEqual(runtime["accounting"]["framework"]["testsRun"],
                         len(self.expected))
        self.assertEqual(runtime["accounting"]["errors"], [])
        self.assertEqual(sorted(runtime["module_timings_seconds"]),
                         sorted(self.modules))

    def test_a_code_shard_set_is_refused_by_a_full_merge(self):
        with self.assertRaises(ValueError):
            self._merge(self._good(), scope="full")

    def test_full_scope_shards_merge_under_full(self):
        modules, expected = _discovery("full")
        reports = [_shard_report(index, self.count, "full", modules)
                   for index in range(self.count)]
        _, runtime = self._merge(reports, scope="full")
        self.assertEqual(runtime["discovered_test_ids"], expected)

    def test_missing_shard_is_refused(self):
        with self.assertRaises(ValueError):
            self._merge(self._good()[:-1])

    def test_entry_missing_from_a_shard_is_refused(self):
        reports = self._good()
        runtime = reports[0]["runtime"]
        runtime["entries"].pop()
        runtime["tests_run"] -= 1
        runtime["passed"] -= 1
        runtime["accounting"]["framework"]["testsRun"] -= 1
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_duplicate_entry_is_refused(self):
        reports = self._good()
        reports[0]["runtime"]["entries"].append(
            dict(reports[0]["runtime"]["entries"][0]))
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_entry_from_outside_the_scope_is_refused(self):
        reports = self._good()
        modules, _ = vs.discover_modules(ROOT)
        dropped = sorted({test.id() for unit in vs.PR_DEFERRED_UNITS
                          for test in modules[unit]})
        self.assertTrue(dropped)
        reports[0]["runtime"]["entries"].append(
            {"test": dropped[0], "category": "passed"})
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_missing_module_timing_is_refused(self):
        reports = self._good()
        timings = reports[0]["runtime"]["module_timings_seconds"]
        timings.pop(sorted(timings)[0])
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_completed_verdict_with_nonzero_exit_is_refused(self):
        reports = self._good()
        reports[0]["exit_code"] = 1
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_module_assignment_mismatch_is_refused(self):
        reports = self._good()
        shard = reports[0]["shard"]
        shard["modules"] = shard["modules"][:-1]
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_executed_modules_mismatch_is_refused(self):
        reports = self._good()
        runtime = reports[0]["runtime"]
        runtime["executed_modules"] = runtime["executed_modules"][:-1]
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_forged_validation_scope_is_refused(self):
        reports = self._good()
        reports[0]["validation_scope"] = "full_platform"
        with self.assertRaises(ValueError):
            self._merge(reports)

    def test_framework_tests_run_mismatch_is_refused(self):
        reports = self._good()
        reports[0]["runtime"]["accounting"]["framework"]["testsRun"] -= 1
        with self.assertRaises(ValueError):
            self._merge(reports)


def _merged_report(scope="code"):
    """A final report that ``check_validation_report.py`` accepts, built from
    discovery: the native gate skipped, the approved skill skips skipped, the
    rest passed."""
    _, ids = _discovery(scope)
    gate = rv.NATIVE_GATE["test_id"]
    entries = []
    for test_id in ids:
        if test_id == gate:
            entries.append({"test": test_id, "category": "skipped",
                            "detail": rv.NATIVE_GATE["reason"]})
        elif test_id in rv.ALLOWED_SKILL_SKIPS:
            entries.append({"test": test_id, "category": "skipped",
                            "detail": rv.ALLOWED_SKILL_SKIPS[test_id]})
        else:
            entries.append({"test": test_id, "category": "passed"})
    skill_skips = [{"test": test_id,
                    "reason": rv.ALLOWED_SKILL_SKIPS[test_id],
                    "rule": rv.SKILL_SKIP_RULE}
                   for test_id in ids if test_id in rv.ALLOWED_SKILL_SKIPS]
    skipped = 1 + len(skill_skips)
    return {
        "tool": "tools/run_validation.py",
        "verdict": "ok_platform_scoped",
        "exit_code": 0,
        "mode": "default",
        "validation_scope": "current_platform",
        "platform_validation_completed": True,
        "full_validation_completed": False,
        "baseline_main": cvr.BASELINE_MAIN,
        "pending_external_checks": [{
            "gate_id": rv.NATIVE_GATE["gate_id"],
            "test_id": gate,
            "status": "not_run", "support": "withheld",
            "implementation": "placeholder_only",
            "required_environment": rv.NATIVE_GATE["required_environment"],
            "reason": rv.NATIVE_GATE["reason"],
            "observed_platform": sys.platform,
        }],
        "approved_external_skips": [{
            "test": gate, "reason": rv.NATIVE_GATE["reason"],
            "rule": cvr.GATE_RULE,
            "gate_id": rv.NATIVE_GATE["gate_id"]}],
        "approved_skill_skips": skill_skips,
        "causes": {"unexpected": [], "incomplete": []},
        "bundle": {"status": "pass"},
        "dependency": {"ok": True, "failed": [], "manifest_error": None},
        "fixed": [], "deferred": [], "not_applicable": [],
        "unapproved_skips": [], "defect_tests_skipped": [],
        "inventory_warnings": [],
        "runtime": {
            "status": "completed",
            "tests_run": len(entries),
            "passed": len(entries) - skipped,
            "known_baseline_defects": 0,
            "unexpected_failed": 0,
            "skipped": skipped,
            "failure_events": 0,
            "entries": entries,
            "fixture_events": [],
            "accounting": {
                "framework": {
                    "testsRun": len(entries), "failures": 0, "errors": 0,
                    "skipped": skipped, "expectedFailures": 0,
                    "unexpectedSuccesses": 0, "was_successful": True},
                "errors": []},
            "discovered_test_ids": ids,
        },
    }


class FinalReportTest(unittest.TestCase):
    """③ the independent report checker honours the scope, and only that."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _check(self, report, scope="code"):
        path = self.tmp / "merged.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(REPORT_CHECKER), str(path),
             "--pr-scope", scope],
            cwd=str(ROOT), capture_output=True, text=True)

    def test_code_scope_report_is_accepted(self):
        done = self._check(_merged_report("code"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("validation report contract OK", done.stdout)

    def test_full_scope_report_is_accepted_under_full(self):
        done = self._check(_merged_report("full"), "full")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_code_report_is_not_accepted_as_full(self):
        done = self._check(_merged_report("code"), "full")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("REJECTED", done.stderr)

    def test_removing_the_native_gate_entry_is_rejected(self):
        report = _merged_report("code")
        gate = rv.NATIVE_GATE["test_id"]
        report["runtime"]["entries"] = [e for e in report["runtime"]["entries"]
                                       if e["test"] != gate]
        report["runtime"]["tests_run"] -= 1
        report["runtime"]["accounting"]["framework"]["testsRun"] -= 1
        report["runtime"]["skipped"] -= 1
        report["runtime"]["accounting"]["framework"]["skipped"] -= 1
        report["runtime"]["discovered_test_ids"] = [
            t for t in report["runtime"]["discovered_test_ids"] if t != gate]
        done = self._check(report)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("gate", done.stderr)

    def test_injecting_an_unapproved_skip_is_rejected(self):
        report = _merged_report("code")
        entries = report["runtime"]["entries"]
        index = next(i for i, e in enumerate(entries)
                     if e["category"] == "passed")
        entries[index] = {"test": entries[index]["test"],
                          "category": "skipped", "detail": "not approved"}
        report["runtime"]["passed"] -= 1
        report["runtime"]["skipped"] += 1
        report["runtime"]["accounting"]["framework"]["skipped"] += 1
        done = self._check(report)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("unapproved skill skip", done.stderr)

    def test_skip_tally_off_by_one_is_rejected(self):
        report = _merged_report("code")
        report["runtime"]["skipped"] += 1
        report["runtime"]["passed"] -= 1
        report["runtime"]["accounting"]["framework"]["skipped"] += 1
        done = self._check(report)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("skip tally", done.stderr)


class InventoryLayerTest(unittest.TestCase):
    """④ ``verify_inventory`` on isolated fixtures (promoted from ``main``)."""

    @staticmethod
    def _runtime(entries):
        return {"entries": entries}

    def _verify(self, defects, entries):
        return rv.verify_inventory(self._runtime(entries), {"defects": defects})

    def test_fixed_without_a_regression_test_warns(self):
        layer = self._verify([{"id": "D-1", "status": "fixed",
                               "applies_to_main": True}], [])
        self.assertEqual([f["id"] for f in layer["fixed"]], ["D-1"])
        self.assertTrue(any("no regression test mapping" in w
                            for w in layer["warnings"]), layer)

    def test_fixed_test_that_did_not_run_warns(self):
        layer = self._verify(
            [{"id": "D-2", "status": "fixed", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_c.C.d", "category": "passed"}])
        self.assertTrue(any("did not run" in w for w in layer["warnings"]),
                        layer)

    def test_fixed_test_that_ran_without_passing_warns(self):
        layer = self._verify(
            [{"id": "D-3", "status": "fixed", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "skipped",
              "detail": "x"}])
        self.assertTrue(any("did not pass" in w for w in layer["warnings"]),
                        layer)

    def test_fixed_defect_that_still_reproduces_warns(self):
        layer = self._verify(
            [{"id": "D-4", "status": "fixed", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "passed"},
             {"test": "tests.test_e.E.f", "category": "known_baseline_defect",
              "defect": "D-4"}])
        self.assertTrue(any("still reproduced" in w for w in layer["warnings"]),
                        layer)

    def test_clean_fixed_defect_returns_no_warning(self):
        layer = self._verify(
            [{"id": "D-5", "status": "fixed", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "passed"}])
        self.assertEqual(layer["warnings"], [])
        self.assertEqual(layer["skipped_defects"], [])

    def test_open_defect_must_still_reproduce(self):
        layer = self._verify(
            [{"id": "D-6", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "passed"}])
        self.assertTrue(any("was not reproduced" in w for w in layer["warnings"]),
                        layer)
        layer = self._verify(
            [{"id": "D-6", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "known_baseline_defect",
              "defect": "D-6"}])
        self.assertEqual(layer["warnings"], [])

    def test_unapproved_skip_of_a_defect_test_is_classified_not_warned(self):
        skip = {"test": "tests.test_a.A.b", "reason": "skipped"}
        layer = self._verify(
            [{"id": "D-7", "applies_to_main": True,
              "test": "tests.test_a.A.b"}],
            [{"test": "tests.test_a.A.b", "category": "skipped",
              "detail": "skipped"}])
        # without the skip list this is a plain inventory warning ...
        self.assertTrue(any("was not reproduced" in w for w in layer["warnings"]),
                        layer)
        # ... and with it the entry is reclassified as an incomplete defect
        # test instead, so the defect layer is never quietly loosened.
        layer = rv.verify_inventory(
            self._runtime([{"test": "tests.test_a.A.b", "category": "skipped",
                            "detail": "skipped"}]),
            {"defects": [{"id": "D-7", "applies_to_main": True,
                          "test": "tests.test_a.A.b"}]}, [skip])
        self.assertEqual([s["id"] for s in layer["skipped_defects"]], ["D-7"])

    def test_deferred_and_not_applicable_are_classified(self):
        layer = self._verify(
            [{"id": "D-8", "gate": "deferred", "applies_to_main": True,
              "reason": "main only"},
             {"id": "D-9", "applies_to_main": False, "reason": "absent"}], [])
        self.assertEqual([d["id"] for d in layer["deferred"]], ["D-8"])
        self.assertEqual([d["id"] for d in layer["not_applicable"]], ["D-9"])

    def test_unknown_reproduced_defect_warns(self):
        layer = self._verify([], [{"test": "tests.test_a.A.b",
                                   "category": "known_baseline_defect",
                                   "defect": "D-unknown"}])
        self.assertTrue(any("not in the inventory" in w
                            for w in layer["warnings"]), layer)


class XpassAccountingTest(unittest.TestCase):
    """⑤ an expected-failure test whose body passes must surface as failed."""

    def test_xpass_is_a_failure_and_reconciles(self):
        class Sample(unittest.TestCase):
            @unittest.expectedFailure
            def test_passes_anyway(self):
                self.assertTrue(True)

        suite = unittest.TestLoader().loadTestsFromTestCase(Sample)
        stream = io.StringIO()
        result = unittest.TextTestRunner(
            stream=stream, verbosity=0,
            resultclass=rv.RecordingResult).run(suite)
        runtime = rv._summarize_result(result, {})

        self.assertEqual(runtime["unexpected_failed"], 1)
        self.assertEqual(runtime["passed"], 0)
        self.assertEqual(runtime["accounting"]["errors"], [])
        entry = runtime["entries"][0]
        self.assertEqual(entry["category"], "failed")
        self.assertIn("XPASS", entry["detail"])
        self.assertEqual(
            runtime["accounting"]["framework"]["unexpectedSuccesses"], 1)


class VerdictPrecedenceTest(unittest.TestCase):
    """⑥ precedence only — XPASS classification is ⑤'s job."""

    def test_unexpected_beats_incomplete(self):
        self.assertEqual(
            rv._decide_verdict(["boom"], ["incomplete"], [{"g": 1}], "default"),
            ("unexpected_failures", 1))

    def test_incomplete_beats_ok_platform_scoped(self):
        self.assertEqual(
            rv._decide_verdict([], ["incomplete"], [{"g": 1}], "default"),
            ("incomplete", 3))

    def test_pending_gate_is_platform_scoped_only_in_default_mode(self):
        self.assertEqual(rv._decide_verdict([], [], [{"g": 1}], "default"),
                         ("ok_platform_scoped", 0))
        self.assertEqual(rv._decide_verdict([], [], [{"g": 1}], "runtime_only"),
                         ("ok", 0))

    def test_clean_default_run_is_ok(self):
        self.assertEqual(rv._decide_verdict([], [], [], "default"), ("ok", 0))

    @staticmethod
    def _flags(runtime_result, **over):
        kwargs = dict(mode="default", bundle_status="pass", dep_ok=True,
                      runtime=runtime_result, unapproved_skips=[],
                      inventory_warnings=[], incomplete_causes=[],
                      unexpected_causes=[], pending_external_checks=[])
        kwargs.update(over)
        return rv._completion_flags(**kwargs)

    def test_a_clean_scoped_run_completes_the_platform(self):
        runtime = {"status": "completed", "tests_run": 1,
                   "unexpected_failed": 0, "accounting": {"errors": []}}
        self.assertEqual(self._flags(runtime), (True, True))
        self.assertEqual(self._flags(runtime, pending_external_checks=[{"g": 1}]),
                         (True, False))

    def test_not_run_dependency_is_not_green(self):
        runtime = {"status": "not_run_dependency", "tests_run": 0,
                   "unexpected_failed": 0, "accounting": {"errors": []}}
        self.assertEqual(self._flags(runtime), (False, False))

    def test_each_blocker_keeps_the_platform_flag_false(self):
        runtime = {"status": "completed", "tests_run": 1,
                   "unexpected_failed": 0, "accounting": {"errors": []}}
        for over in ({"bundle_status": "fail"}, {"dep_ok": False},
                     {"unapproved_skips": [{"test": "t"}]},
                     {"inventory_warnings": ["w"]},
                     {"incomplete_causes": ["i"]},
                     {"unexpected_causes": ["u"]},
                     {"runtime": {"status": "completed", "tests_run": 0,
                                  "unexpected_failed": 0,
                                  "accounting": {"errors": []}}}):
            with self.subTest(over=sorted(over)):
                self.assertEqual(self._flags(runtime, **over), (False, False))


if __name__ == "__main__":
    unittest.main()