"""Unit-level discovery, deterministic sharding, and report merging.

A shard unit is a whole test module, except for the two modules in
``SPLIT_MODULES``: they start whole nested validation runs inside their tests
(about 17 nested full-suite runs in total, ~90% of the suite's wall time) and
keep no state between tests, so they are split into class units, and classes
without fixtures into single-method units.  Any class with
``setUpClass``/``tearDownClass`` stays one unit, and a module with
``setUpModule``/``tearDownModule`` stays one unit.  Unit keys are
``path.py`` (module), ``path.py::Class`` or ``path.py::Class::method``.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import unittest

CATEGORIES = frozenset({"passed", "failed", "skipped", "known_baseline_defect"})
SPLIT_MODULES = frozenset({
    "tests/test_runner_contract.py",
    "tests/test_runner_negative_controls.py",
})
# Nested full-suite runs started by a split unit (counted from the `_run*`
# calls in each test method).  One nested run costs about as much as all other
# modules together, so it weighs ~3000 ordinary tests.  These hints only
# balance the first assignment; replace them with measured
# `module_timings_seconds` from the first CI run.  A unit missing here is still
# discovered and run, it just weighs its test count.
NESTED_RUN_WEIGHT = 3000
NESTED_RUNS = {
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_missing_dependencies_block_run": 2,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_dependency_stub_versions": 2,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_injected_skip_blocks_success": 2,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_nested_flag_rejected_in_final_mode": 3,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_xpass_surfaces_as_unexpected": 1,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_fixed_defect_mapping_required": 1,
    "tests/test_runner_contract.py::RunnerContractTests::"
    "test_unexpected_failure_beats_incomplete": 5,
    "tests/test_runner_negative_controls.py::RunnerNegativeControlTests::"
    "test_tampered_runtime_module_detected": 1,
    "tests/test_runner_negative_controls.py::RunnerNegativeControlTests::"
    "test_missing_runtime_module_detected": 1,
    "tests/test_runner_negative_controls.py::RunnerNegativeControlTests::"
    "test_pythonpath_pollution_does_not_shadow_runtime": 1,
}
# PR scope (design §2.1/§2.2): the units above start whole nested validation
# runs inside themselves and are deferred to the main-branch run.  PRs run
# every other unit.  ``PR_DEFERRED_UNITS`` is the single definition of "what a
# PR skips"; ``expected_ids_for`` is the single definition of "which test IDs a
# scope contains" — execution, merging and the final report all derive from it.
PR_DEFERRED_UNITS = frozenset(NESTED_RUNS)
PR_SCOPES = frozenset({"full", "code"})


def strict_load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=unique, parse_constant=reject_constant)


def walk_tests(node):
    if isinstance(node, unittest.TestSuite):
        for child in node:
            yield from walk_tests(child)
    else:
        yield node


def _module_path(root, start, test):
    if test.__class__.__module__ == "unittest.loader":
        name = test.id().rsplit(".", 1)[-1]
        if name.startswith("test_"):
            matches = sorted(start.rglob(name + ".py"))
            if matches:
                return matches[0].relative_to(root).as_posix()
        raise ValueError("cannot map failed test discovery to a module: "
                         + test.id())
    module = Path(*test.__class__.__module__.split(".")).with_suffix(".py")
    candidate = root / module
    if candidate.is_file():
        return module.as_posix()
    return (start / module.name).relative_to(root).as_posix()


def _has_fixture(cls):
    base = unittest.TestCase
    return any(getattr(cls, name).__func__ is not getattr(base, name).__func__
               for name in ("setUpClass", "tearDownClass"))


def _unit_key(module, test):
    """Shard unit of one test: its module, or a class/method of a split module."""
    if module not in SPLIT_MODULES:
        return module
    owner = sys.modules.get(test.__class__.__module__)
    if owner is None or any(hasattr(owner, name)
                            for name in ("setUpModule", "tearDownModule")):
        return module
    key = module + "::" + test.__class__.__qualname__
    if _has_fixture(test.__class__):
        return key
    return key + "::" + test._testMethodName


def discover_modules(root):
    """Discover both public roots once; return {unit: [tests]} and all test IDs."""
    root = Path(root).resolve()
    test_roots = (root / "tests", root / "skills" / "knuaf-doc" / "tests")
    modules = {}
    ordered_ids = []
    for start in test_roots:
        top = root if start == test_roots[0] else start
        suite = unittest.TestLoader().discover(
            str(start), pattern="test_*.py", top_level_dir=str(top))
        for test in walk_tests(suite):
            module = _module_path(root, start, test)
            test._gg_module_path = module
            test_id = test.id()
            if test_id in ordered_ids:
                raise ValueError("duplicate discovered test id: " + test_id)
            ordered_ids.append(test_id)
            modules.setdefault(_unit_key(module, test), []).append(test)
    return modules, ordered_ids


def expected_ids(modules, ordered_ids, scope="full"):
    """Test IDs of one scope from one discovery result, in discovery order.

    The single ID authority: ``expected_ids_for`` is the same function over a
    fresh discovery, so callers that already discovered (shard merge) do not
    discover twice and still cannot derive a different list.
    """
    if scope not in PR_SCOPES:
        raise ValueError("unknown PR scope: %r" % (scope,))
    if scope == "full":
        return list(ordered_ids)
    keep = {unit for unit in modules if unit not in PR_DEFERRED_UNITS}
    ids = {test.id() for unit in keep for test in modules[unit]}
    return [test_id for test_id in ordered_ids if test_id in ids]


def expected_ids_for(root, scope="full"):
    """Test IDs of one scope, in discovery order — the single ID authority.

    ``full`` is exactly ``discover_modules`` (same objects, same order), so a
    full-scope run is byte-identical to the pre-scope behaviour.  ``code`` drops
    the PR-deferred units (the nested runners) and keeps the rest in discovery
    order.  Callers must not recompute IDs another way: execution, shard merging
    and the final report all read this function, so they cannot disagree.
    """
    return expected_ids(*discover_modules(root), scope=scope)


def scoped_modules(modules, scope="full"):
    """The units of ``modules`` that ``scope`` runs, in discovery order."""
    if scope not in PR_SCOPES:
        raise ValueError("unknown PR scope: %r" % (scope,))
    if scope == "full":
        return dict(modules)
    return {unit: tests for unit, tests in modules.items()
            if unit not in PR_DEFERRED_UNITS}


def assign_modules(modules, shard_count):
    """Greedily balance shard units by test count plus nested-run weight."""
    if (not isinstance(shard_count, int) or isinstance(shard_count, bool)
            or shard_count < 1):
        raise ValueError("shard count must be a positive integer")
    assigned = [[] for _ in range(shard_count)]
    loads = [0] * shard_count
    def weight(row):
        return len(row[1]) + NESTED_RUN_WEIGHT * NESTED_RUNS.get(row[0], 0)

    weighted = sorted(modules.items(), key=lambda row: (-weight(row), row[0]))
    for module, tests in weighted:
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        assigned[target].append(module)
        loads[target] += weight((module, tests))
    return [sorted(group) for group in assigned]


def parse_shard(value):
    try:
        index_text, count_text = value.split("/", 1)
        index, count = int(index_text), int(count_text)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("shard must be I/N, e.g. 1/5")
    if count < 1 or not 1 <= index <= count:
        raise argparse.ArgumentTypeError("shard index must be in 1..N")
    return index - 1, count


def merge_shard_reports(root, paths, shard_count, scope="full"):
    """Validate reports against fresh discovery, then rebuild full runtime data.

    ``scope`` is the same PR scope the shards ran with, so the expected ID set,
    the expected module assignment and the merged reconciliation all derive from
    one definition (``expected_ids``/``scoped_modules``).  A shard set produced
    for one scope is rejected by the other: the ID union cannot match.
    """
    root = Path(root).resolve()
    modules, ordered_ids = discover_modules(root)
    modules = scoped_modules(modules, scope)
    ordered_ids = expected_ids(modules, ordered_ids, scope)
    assignment = assign_modules(modules, shard_count)
    shard_ids = {
        index + 1: [test.id() for module in assignment[index]
                    for test in modules[module]]
        for index in range(shard_count)}
    expected = {index: set(ids) for index, ids in shard_ids.items()}
    reports = {}

    for path in paths:
        report = strict_load(path)
        if not isinstance(report, dict):
            raise ValueError(f"{path}: report is not an object")
        shard = report.get("shard")
        if not isinstance(shard, dict):
            raise ValueError(f"{path}: missing shard metadata")
        index, count = shard.get("index"), shard.get("count")
        if (not isinstance(index, int) or isinstance(index, bool)
                or not isinstance(count, int) or isinstance(count, bool)
                or count != shard_count or index not in expected
                or index in reports):
            raise ValueError(f"{path}: invalid or duplicate shard identity")

        runtime = report.get("runtime")
        if not isinstance(runtime, dict):
            raise ValueError(f"{path}: runtime is not an object")
        entries = runtime.get("entries")
        if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries):
            raise ValueError(f"{path}: runtime.entries is not a list of objects")
        ids = [entry.get("test") for entry in entries]
        if (any(not isinstance(test_id, str) for test_id in ids)
                or len(ids) != len(set(ids))):
            raise ValueError(f"{path}: malformed or duplicate test entries")
        if set(ids) != expected[index]:
            missing = sorted(expected[index] - set(ids))
            extra = sorted(set(ids) - expected[index])
            raise ValueError(
                f"{path}: entries differ from fresh discover; "
                f"missing={missing[:5]!r}, extra={extra[:5]!r}")

        discovered = shard.get("discovered_test_ids")
        if discovered != shard_ids[index]:
            raise ValueError(f"{path}: shard discovery differs from fresh discover")
        claimed = shard.get("executed_test_ids")
        if claimed != ids:
            raise ValueError(f"{path}: executed IDs differ from runtime.entries order")
        if runtime.get("discovered_test_ids") != discovered:
            raise ValueError(f"{path}: runtime discovery differs from shard metadata")

        assigned_modules = assignment[index - 1]
        if shard.get("modules") != assigned_modules:
            raise ValueError(f"{path}: module assignment mismatch")
        executed_modules = runtime.get("executed_modules")
        if executed_modules != assigned_modules:
            raise ValueError(f"{path}: executed modules differ from assigned shard")
        timings = runtime.get("module_timings_seconds")
        if not isinstance(timings, dict) or set(timings) != set(executed_modules):
            raise ValueError(f"{path}: incomplete module timing report")
        if any(not isinstance(seconds, (int, float))
               or isinstance(seconds, bool) or not math.isfinite(seconds)
               or seconds < 0 for seconds in timings.values()):
            raise ValueError(f"{path}: invalid module timing")

        if any(not isinstance(entry.get("category"), str)
               or entry["category"] not in CATEGORIES for entry in entries):
            raise ValueError(f"{path}: unknown runtime category")
        tally = {category: sum(entry["category"] == category
                               for entry in entries)
                 for category in CATEGORIES}
        for key, category in (("passed", "passed"),
                              ("known_baseline_defects", "known_baseline_defect"),
                              ("skipped", "skipped"),
                              ("unexpected_failed", "failed")):
            value = runtime.get(key)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value != tally[category]):
                raise ValueError(f"{path}: runtime.{key} differs from entries")
        tests_run = runtime.get("tests_run")
        if (not isinstance(tests_run, int) or isinstance(tests_run, bool)
                or tests_run != len(entries)):
            raise ValueError(f"{path}: runtime.tests_run differs from entries")

        accounting = runtime.get("accounting")
        framework = (accounting.get("framework")
                     if isinstance(accounting, dict) else None)
        if not isinstance(framework, dict):
            raise ValueError(f"{path}: framework accounting is missing")
        for key in ("testsRun", "failures", "errors", "skipped",
                    "expectedFailures", "unexpectedSuccesses"):
            counter = framework.get(key)
            if (not isinstance(counter, int) or isinstance(counter, bool)
                    or counter < 0):
                raise ValueError(
                    f"{path}: framework.{key} must be a nonnegative integer")
        if (framework.get("was_successful") is not True
                or framework.get("testsRun") != tests_run
                or any(framework.get(key) != 0 for key in
                       ("failures", "errors", "expectedFailures",
                        "unexpectedSuccesses"))
                or framework.get("skipped") != tally["skipped"]
                or not isinstance(accounting.get("errors"), list)
                or accounting["errors"] != []):
            raise ValueError(f"{path}: inconsistent or failed framework accounting")
        fixture_events = runtime.get("fixture_events")
        if fixture_events != []:
            raise ValueError(f"{path}: fixture outcomes prevent shard completion")
        failure_events = runtime.get("failure_events")
        if (not isinstance(failure_events, int) or isinstance(failure_events, bool)
                or failure_events != 0):
            raise ValueError(f"{path}: failure events prevent shard completion")

        exit_code = report.get("exit_code")
        if (report.get("verdict") != "shard_completed"
                or not isinstance(exit_code, int) or isinstance(exit_code, bool)
                or exit_code != 0
                or report.get("mode") != "default"
                or report.get("validation_scope") != "current_platform"
                or report.get("platform_validation_completed") is not False
                or report.get("full_validation_completed") is not False):
            raise ValueError(f"{path}: report did not pass the shard contract")
        reports[index] = report

    if set(reports) != set(expected):
        raise ValueError("missing shard reports: " + repr(
            sorted(set(expected) - set(reports))))

    template = reports[1]
    ordered_reports = [reports[index] for index in sorted(reports)]
    for index, report in sorted(reports.items()):
        for key in ("tool", "mode", "validation_scope", "baseline_main",
                    "python", "root", "dependency", "bundle"):
            if report.get(key) != template.get(key):
                raise ValueError(f"shard {index}: inconsistent {key}")

    entry_by_id = {
        entry["test"]: entry
        for report in ordered_reports
        for entry in report["runtime"]["entries"]}
    if (set(entry_by_id) != set(ordered_ids)
            or len(entry_by_id) != len(ordered_ids)):
        raise ValueError("shard test-id union differs from fresh discover")
    entries = [entry_by_id[test_id] for test_id in ordered_ids]
    category_counts = {category: sum(
        entry["category"] == category for entry in entries)
        for category in CATEGORIES}
    runtimes = [report["runtime"] for report in ordered_reports]
    fixture_events = [event for item in runtimes
                      for event in item["fixture_events"]]
    runtime = {
        "status": "completed",
        "tests_run": len(entries),
        "passed": category_counts["passed"],
        "known_baseline_defects": category_counts["known_baseline_defect"],
        "unexpected_failed": category_counts["failed"] + sum(
            event.get("type") in ("failure", "error") for event in fixture_events),
        "skipped": category_counts["skipped"],
        "failure_events": sum(item["failure_events"] for item in runtimes),
        "entries": entries,
        "fixture_events": fixture_events,
        "accounting": {
            "framework": {
                key: (all(item["accounting"]["framework"].get(key) is True
                          for item in runtimes) if key == "was_successful"
                      else sum(item["accounting"]["framework"].get(key, 0)
                               for item in runtimes))
                for key in ("testsRun", "failures", "errors", "skipped",
                            "expectedFailures", "unexpectedSuccesses",
                            "was_successful")},
            "errors": [error for item in runtimes
                       for error in item["accounting"]["errors"]]},
        "suite_output_tail": "\n".join(
            item.get("suite_output_tail", "") for item in runtimes)[-4000:],
        "known_defect_records": {
            entry["test"]: {key: entry[key] for key in
                            ("defect", "gate", "observed") if key in entry}
            for entry in entries
            if entry["category"] == "known_baseline_defect"},
        "executed_modules": sorted({
            module for item in runtimes for module in item["executed_modules"]}),
        "module_timings_seconds": {
            module: sum(item["module_timings_seconds"].get(module, 0.0)
                        for item in runtimes)
            for module in modules},
        "discovered_test_ids": ordered_ids,
    }
    if (runtime["tests_run"] != len(ordered_ids)
            or runtime["tests_run"] != runtime["accounting"]["framework"]["testsRun"]):
        raise ValueError("merged entries do not reconcile to fresh discover")
    return {key: value for key, value in template.items() if key != "shard"}, runtime
