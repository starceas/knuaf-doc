#!/usr/bin/env python3
"""Independently validate the accepted full-suite CI report contract."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
GATE_ID = "P2-L09-native"
GATE_TEST = "tests.test_p2_windows.WindowsBandCase.test_native_windows_two_process_contention"
GATE_REASON = "native Windows+NTFS required — withheld"
GATE_RULE = "P2-L09-native accepted external placeholder"
BASELINE_MAIN = "29abec0ec95369de7ae9e22207349d574f1f46b7"
H9_TEST = "tests.test_finance_scope.FinanceScopeTests.test_won_unit_fact_is_checked"
CATEGORIES = ("passed", "failed", "skipped", "known_baseline_defect")
SUCCESS_EVENTS = {"subtest_success"}


def _strict_load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key %r" % key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite JSON literal %s" % value)

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=unique, parse_constant=reject_constant)


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _dict(value, name):
    _need(isinstance(value, dict), "%s must be an object" % name)
    return value


def _list(value, name):
    _need(isinstance(value, list), "%s must be a list" % name)
    return value


def _count(value, name, minimum=0, exact=None):
    _need(isinstance(value, int) and not isinstance(value, bool),
          "%s must be an integer" % name)
    _need(value >= minimum, "%s must be >= %d" % (name, minimum))
    if exact is not None:
        _need(value == exact, "%s must equal %d" % (name, exact))
    return value


def validate(report):
    report = _dict(report, "report")
    expected_top = {
        "tool": "tools/run_validation.py", "verdict": "ok_platform_scoped",
        "mode": "default", "validation_scope": "current_platform",
        "baseline_main": BASELINE_MAIN,
    }
    for key, expected in expected_top.items():
        _need(report.get(key) == expected, "%s must equal %r" % (key, expected))
    _count(report.get("exit_code"), "exit_code", exact=0)
    _need(report.get("platform_validation_completed") is True,
          "platform_validation_completed must be true")
    _need(report.get("full_validation_completed") is False,
          "full_validation_completed must be false")

    pending = _list(report.get("pending_external_checks"), "pending_external_checks")
    _need(len(pending) == 1, "exactly one pending external gate is required")
    gate = _dict(pending[0], "pending_external_checks[0]")
    for key, expected in {
        "gate_id": GATE_ID, "status": "not_run", "support": "withheld",
        "implementation": "placeholder_only", "test_id": GATE_TEST,
        "required_environment": "native Windows+NTFS", "reason": GATE_REASON,
    }.items():
        _need(gate.get(key) == expected, "pending gate %s is invalid" % key)
    _need(gate.get("observed_platform") in ("linux", "darwin"),
          "pending gate platform is invalid")

    approved_external = _list(report.get("approved_external_skips"),
                              "approved_external_skips")
    _need(approved_external == [{
        "test": GATE_TEST, "reason": GATE_REASON,
        "rule": GATE_RULE, "gate_id": GATE_ID,
    }], "approved external skip does not match the exact native gate")
    causes = _dict(report.get("causes"), "causes")
    for name in ("unexpected", "incomplete"):
        _need(_list(causes.get(name), "causes.%s" % name) == [],
              "causes.%s must be empty" % name)
    for name in ("unapproved_skips", "defect_tests_skipped", "inventory_warnings"):
        _need(_list(report.get(name), name) == [], "%s must be empty" % name)
    for name in ("fixed", "deferred", "not_applicable"):
        _list(report.get(name), name)

    runtime = _dict(report.get("runtime"), "runtime")
    _need(runtime.get("status") == "completed", "runtime must be completed")
    tests_run = _count(runtime.get("tests_run"), "runtime.tests_run", minimum=1)
    passed = _count(runtime.get("passed"), "runtime.passed")
    _count(runtime.get("known_baseline_defects"),
           "runtime.known_baseline_defects", exact=0)
    skipped = _count(runtime.get("skipped"), "runtime.skipped", minimum=1)
    _count(runtime.get("unexpected_failed"), "runtime.unexpected_failed", exact=0)
    _count(runtime.get("failure_events"), "runtime.failure_events", exact=0)
    _need(_list(runtime.get("fixture_events"), "runtime.fixture_events") == [],
          "fixture events must be empty")

    accounting = _dict(runtime.get("accounting"), "runtime.accounting")
    _need(_list(accounting.get("errors"), "runtime.accounting.errors") == [],
          "accounting errors must be empty")
    framework = _dict(accounting.get("framework"), "runtime.accounting.framework")
    _need(framework.get("was_successful") is True,
          "framework.was_successful must be true")
    for key in ("failures", "errors", "expectedFailures", "unexpectedSuccesses"):
        _count(framework.get(key), "framework.%s" % key, exact=0)
    _count(framework.get("testsRun"), "framework.testsRun", exact=tests_run)
    _count(framework.get("skipped"), "framework.skipped", exact=skipped)

    entries = _list(runtime.get("entries"), "runtime.entries")
    _need(len(entries) == tests_run, "entries length differs from tests_run")
    seen = set()
    tally = dict.fromkeys(CATEGORIES, 0)
    gate_seen = h9_seen = 0
    skill_skips = []
    policy = runpy.run_path(str(ROOT / "tools" / "run_validation.py"))
    allowed_skips = policy["ALLOWED_SKILL_SKIPS"]
    skill_skip_rule = policy["SKILL_SKIP_RULE"]

    for index, raw_entry in enumerate(entries):
        entry = _dict(raw_entry, "entries[%d]" % index)
        test_id = entry.get("test")
        _need(isinstance(test_id, str) and test_id,
              "entries[%d].test must be a nonempty string" % index)
        _need(test_id not in seen, "duplicate test id %s" % test_id)
        seen.add(test_id)
        category = entry.get("category")
        _need(category in tally, "unknown category for %s" % test_id)
        tally[category] += 1
        if test_id == H9_TEST:
            h9_seen += 1
            _need(category == "passed", "fixed H9 test must pass")
        _need(category != "known_baseline_defect",
              "known baseline defect remains: %s" % test_id)
        if test_id == GATE_TEST:
            gate_seen += 1
            _need(category == "skipped" and entry.get("detail") == GATE_REASON,
                  "native gate must be the exact withheld skip")
            _need(not entry.get("events"), "native gate skip cannot carry events")
        elif category == "skipped":
            _need(test_id in allowed_skips and entry.get("detail") == allowed_skips[test_id],
                  "unapproved skill skip: %s" % test_id)
            _need(not entry.get("events"), "approved skill skip cannot carry events")
            skill_skips.append({"test": test_id, "reason": entry["detail"],
                                "rule": skill_skip_rule})
        events = entry.get("events")
        if events is not None:
            for event_index, raw_event in enumerate(_list(events, "entry events")):
                event = _dict(raw_event, "entries[%d].events[%d]" %
                              (index, event_index))
                _need(event.get("type") in SUCCESS_EVENTS,
                      "non-success subtest event in accepted report")
                subtest = event.get("subtest")
                _need(isinstance(subtest, str) and subtest.startswith(test_id + " (")
                      and subtest.endswith(")") and len(subtest) > len(test_id) + 3,
                      "invalid subtest identity for %s" % test_id)

    _need(gate_seen == 1, "native gate test must appear exactly once")
    _need(h9_seen == 1, "fixed H9 test must appear exactly once")
    _need(set(allowed_skips) <= seen, "declared optional skill test is missing")
    _need(_list(report.get("approved_skill_skips"), "approved_skill_skips") == skill_skips,
          "approved skill skips do not match runtime entries")
    _need(skipped == 1 + len(skill_skips), "skip tally differs from approved skips")
    _need(tally["passed"] == passed, "passed tally differs from entries")
    _need(tally["skipped"] == skipped, "skipped tally differs from entries")
    _need(tally["failed"] == 0 and sum(tally.values()) == tests_run,
          "runtime category tally is inconsistent")
    _need(_dict(report.get("bundle"), "bundle").get("status") == "pass",
          "bundle integrity must pass")
    _need(_dict(report.get("dependency"), "dependency").get("ok") is True,
          "dependency preflight must pass")

    sys.path.insert(0, str(ROOT))
    try:
        from tools.validation_sharding import discover_modules
        expected_ids = discover_modules(ROOT)[1]
    finally:
        sys.path.remove(str(ROOT))
    discovered = _list(runtime.get("discovered_test_ids"),
                       "runtime.discovered_test_ids")
    _need(discovered == expected_ids and set(discovered) == seen,
          "merged IDs differ from fresh discovery")
    return tests_run


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="merged validation report JSON")
    args = parser.parse_args(argv)
    try:
        count = validate(_strict_load(args.report))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("validation report contract: REJECTED: %s" % exc,
              file=sys.stderr)
        return 1
    print("validation report contract OK: %d test IDs and all gates reconciled" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
