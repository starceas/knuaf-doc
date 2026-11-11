#!/usr/bin/env python3
"""Fast interpreter compatibility lane: import/compile everything, run fixed smoke tests."""
import argparse
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE_TEST_IDS = (
    "tests.test_runner_contract.RunnerAccountingTests.test_formatter_failure_keeps_original_error",
    "tests.test_runner_contract.RunnerAccountingTests.test_failed_subtest_makes_parent_failed_once",
    "tests.test_finance_scope.FinanceScopeTests.test_all_empty_claims_blocks",
    "tests.test_question_gate.QuestionGateTests.test_unknown_answer_reaches_help",
    "test_n5_guidance_link.N5GuidanceLinkTests.test_entry_points_and_approval_boundary",
)
# Whole modules that exercise zip/XML/xlsx handling, where a new interpreter's
# standard-library changes would show first. Each takes seconds.
SMOKE_MODULES = (
    "tests.test_xlsx_structure",
    "tests.test_build_hwpx",
    "tests.test_hwpx_adapter",
)


def _walk(node):
    if isinstance(node, unittest.TestSuite):
        for child in node:
            yield from _walk(child)
    else:
        yield node


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, metavar="PATH")
    args = parser.parse_args(argv)
    roots = (ROOT / "tools", ROOT / "tests",
             ROOT / "skills" / "knuaf-doc" / "scripts",
             ROOT / "skills" / "knuaf-doc" / "tests")
    source_files = sorted(path for directory in roots
                          for path in directory.rglob("*.py"))
    compile_errors = []
    for path in source_files:
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            compile_errors.append({"file": path.relative_to(ROOT).as_posix(),
                                   "error": str(exc)})
    if compile_errors:
        report = {"status": "failed", "compile_errors": compile_errors}
        _write_report(args.json, report)
        print("quick validation failed: %d compile error(s)" % len(compile_errors))
        return 1

    sys.path.insert(0, str(ROOT))
    skill_tests = ROOT / "skills" / "knuaf-doc" / "tests"
    discovered = unittest.TestSuite()
    root_loader = unittest.TestLoader()
    skill_loader = unittest.TestLoader()
    try:
        discovered.addTests(root_loader.discover(
            str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)))
        discovered.addTests(skill_loader.discover(
            str(skill_tests), pattern="test_*.py", top_level_dir=str(skill_tests)))
        all_tests = list(_walk(discovered))
        tests_by_id = {test.id(): test for test in all_tests}
        duplicate_ids = len(tests_by_id) != len(all_tests)
        missing = sorted(set(SMOKE_TEST_IDS) - tests_by_id.keys())
        module_tests = {module: sorted(
            test_id for test_id in tests_by_id
            if test_id.startswith(module + ".")) for module in SMOKE_MODULES}
        missing += sorted(module for module, ids in module_tests.items()
                          if not ids)
        import_failures = sorted(test.id() for test in all_tests
                                 if test.__class__.__module__ == "unittest.loader")
        loader_errors = root_loader.errors + skill_loader.errors
        if duplicate_ids or missing or import_failures or loader_errors:
            report = {
                "status": "failed",
                "reason": "test discovery/import contract failed",
                "missing_smoke_test_ids": missing,
                "duplicate_test_ids": duplicate_ids,
                "test_module_import_failures": import_failures,
                "loader_errors": loader_errors,
            }
            _write_report(args.json, report)
            print("quick validation failed: test discovery/import contract")
            return 1
        smoke_ids = list(SMOKE_TEST_IDS) + [
            test_id for module in SMOKE_MODULES
            for test_id in module_tests[module]]
        smoke = unittest.TestSuite(tests_by_id[test_id] for test_id in smoke_ids)
        result = unittest.TextTestRunner(verbosity=2).run(smoke)
    finally:
        sys.path.remove(str(ROOT))
    passed = (result.testsRun == len(smoke_ids)
              and len(result.failures) == 0 and len(result.errors) == 0
              and len(result.skipped) == 0
              and len(result.expectedFailures) == 0
              and len(result.unexpectedSuccesses) == 0)
    imported_test_modules = sorted({
        test.__class__.__module__ for test in tests_by_id.values()})
    report = {
        "status": "passed" if passed else "failed",
        "python": sys.version.split()[0],
        "criteria": ["compile every Python file in validation/test roots",
                     "import every test module without loader errors",
                     "run the fixed smoke-test ID list and every test of the "
                     "smoke modules with no skips/failures"],
        "compiled_source_files": len(source_files),
        "compile_errors": [],
        "imported_test_module_count": len(imported_test_modules),
        "imported_test_modules": imported_test_modules,
        "discovered_test_count": len(tests_by_id),
        "smoke_test_ids": smoke_ids,
        "smoke_modules": list(SMOKE_MODULES),
        "smoke_tests_run": result.testsRun,
        "smoke_failures": len(result.failures),
        "smoke_errors": len(result.errors),
        "smoke_skipped": len(result.skipped),
    }
    _write_report(args.json, report)
    print("quick validation %s: compiled %d Python files, imported %d test modules, "
          "ran %d smoke tests"
          % (report["status"], report["compiled_source_files"],
             report["imported_test_module_count"], report["smoke_tests_run"]))
    return 0 if passed else 1


def _write_report(path, report):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
