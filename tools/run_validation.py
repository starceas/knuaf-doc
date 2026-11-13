#!/usr/bin/env python3
"""Single-command public validation for the knuaf-doc candidate.

Usage:
    python3 tools/run_validation.py [--json <output>] [--runtime-only]

Default mode runs ``tools/check_bundle.py --root <repo root>`` as a
subprocess, then verifies the declared runtime dependencies on THIS
interpreter, then runs both ``tests/`` and ``skills/knuaf-doc/tests/``.
``--runtime-only`` is a development aid and is never the final or CI
command; it always reports ``full_validation_completed=false`` and
``platform_validation_completed=false``.

Supported Python: 3.10 or newer (enforced before any test runs).

Exit contract (P2-R2):
  0  ok                  — everything ran; classification clean and no
                           external gate pending
  0  ok_platform_scoped  — default mode, clean run, exactly the declared
                           pending external gate(s) observed
  1  unexpected_failures — bundle tamper/fail, unexpected test
                           failure/error/XPASS, framework/accounting
                           mismatch, or inventory mismatch
  2  usage/environment   — unsupported Python
  3  incomplete          — checker missing, dependency readiness failed,
                           unapproved skip, or GG_VALIDATION_NESTED set in
                           default mode (no tests ran / run not trustworthy)

A green run is NOT a product PASS.  Results separate:
  * passed                — contract holds on this baseline
  * known_baseline_defect — a documented main defect reproduced exactly
  * unexpected_failed     — failure, error, unexpected pass (XPASS),
                            unauthorized expectedFailure, or failed
                            fixture outcome
  * skipped/not_applicable — skipped tests plus defects that target
    PR-only modules (see tests/regression_inventory.json)
  * pending_external_checks — declared gates that cannot run on this
    platform (current P2: P2-L09-native, a placeholder-only record —
    never a claim of native execution)

Outcome accounting (P2-R2): ``runtime.entries`` carries exactly one
terminal category per started parent test; ``tests_run`` equals the
started-parent count and the framework ``testsRun``.  Subtest outcomes
are recorded as events on their parent (a parent with any failed or
errored subtest is failed — never passed or known-baseline; a skipped
subtest skips the parent unless a failure/error takes precedence).
Class/module fixture outcomes with no started parent are separate
``fixture_events``: failure/error fixtures count as unexpected failures,
fixture skips are unapproved skips.  Framework outcome lists
(failures/errors/skipped/expectedFailures/unexpectedSuccesses) are
reconciled against the recorded events — any unexplained mismatch is an
accounting failure (exit 1), and ``wasSuccessful()==False`` can never
yield exit 0.

``GG_VALIDATION_NESTED`` exists so a test can spawn a nested
``--runtime-only`` runner without infinite recursion.  Only the exact
value ``1`` is sanctioned, and only inside ``--runtime-only``; then only
the finite test-ID list ``ALLOWED_NESTED_SKIP_IDS`` may skip.  Any other
non-empty value, or any value in default mode, makes the run incomplete.

External-gate rule (fail-closed, compiled in — not configurable): the
exact test ``tests.test_p2_windows.WindowsBandCase.
test_native_windows_two_process_contention`` skipping with the exact
reason ``native Windows+NTFS required — withheld`` on darwin or linux is
the sole approved native external skip; it records the P2-L09-native pending
gate.  The same test passing, failing, skipping with another reason,
running on another platform, appearing twice, or being absent from a
completed suite is never silently excused — it is an unexpected failure
or unapproved skip as applicable.

Skill tests use finite exact-ID/reason lists. ``ALLOWED_SKILL_SKIPS`` covers
optional external inputs on every platform. ``WINDOW_ONLY_SKILL_SKIPS``
covers only exact tests for a product path absent on Windows, and only
when ``sys.platform == "win32"``. The latter remain unapproved on darwin
and linux. Approved results remain visible as skipped, never passed.
"""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

MIN_PYTHON = (3, 10)
BASELINE_MAIN = "29abec0ec95369de7ae9e22207349d574f1f46b7"
NESTED_FLAG = "GG_VALIDATION_NESTED"
NESTED_VALUE = "1"

# Finite list of tests allowed to skip inside a nested --runtime-only run
# (GG_VALIDATION_NESTED=1).  Every entry spawns runner subprocesses;
# skipping prevents unbounded recursion.  Documented in
# docs/validation/REGRESSIONS.md.  No prefix matching, no reason-string
# exemptions — an exact ID or the skip is unapproved.
ALLOWED_NESTED_SKIP_IDS = frozenset({
    "tests.test_runner_negative_controls.RunnerNegativeControlTests."
    "test_missing_runtime_module_detected",
    "tests.test_runner_negative_controls.RunnerNegativeControlTests."
    "test_pythonpath_pollution_does_not_shadow_runtime",
    "tests.test_runner_negative_controls.RunnerNegativeControlTests."
    "test_tampered_runtime_module_detected",
    "tests.test_runner_contract.RunnerContractTests."
    "test_dependency_stub_versions",
    "tests.test_runner_contract.RunnerContractTests."
    "test_injected_skip_blocks_success",
    "tests.test_runner_contract.RunnerContractTests."
    "test_missing_dependencies_block_run",
    "tests.test_runner_contract.RunnerContractTests."
    "test_nested_flag_rejected_in_final_mode",
    "tests.test_runner_contract.RunnerContractTests."
    "test_unexpected_failure_beats_incomplete",
    "tests.test_runner_contract.RunnerContractTests."
    "test_xpass_surfaces_as_unexpected",
    "tests.test_runner_contract.RunnerContractTests."
    "test_fixed_defect_mapping_required",
})

# The single declared external gate for current P2: a native Windows+NTFS
# contention check that is deliberately not implemented yet — the test
# body is ``self.fail('native Windows run not recorded')``.  Only the
# exact skipped outcome + exact reason on darwin/linux qualifies as the
# approved external skip.  Nothing here may promote it to a pass.
NATIVE_GATE = {
    "gate_id": "P2-L09-native",
    "test_id": "tests.test_p2_windows.WindowsBandCase."
               "test_native_windows_two_process_contention",
    "reason": "native Windows+NTFS required — withheld",
    "allowed_platforms": frozenset({"darwin", "linux"}),
    "required_environment": "native Windows+NTFS",
    "support": "withheld",
    "implementation": "placeholder_only",
    "rule": "P2-L09-native accepted external placeholder",
}

# Optional external inputs, not missing shipped payloads or dependencies.
# No class/prefix matching: a new test or changed reason needs review.
ALLOWED_SKILL_SKIPS = {
    "test_gg_doi.LiveCall.test_real_doi":
        "실제 Crossref 호출 — GG_DOI_LIVE=1일 때만",
    **dict.fromkeys((
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_real_apply_roundtrip_on_fresh_bytes",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_real_propose_promotes_only_verified",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_real_verified_claim_verifies",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_real_wrong_value_rejected",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_scc1_copied_public_pin_cannot_mint_wrapper",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_scc1_forged_real_key_rejected_via_public_accept",
        "test_gg_rda_research_integration.AcceptedCatalogRealPackTests."
        "test_scc1_post_acceptance_mutation_self_invalidates",
    ), "accepted catalog tree not present"),
    **dict.fromkeys((
        "test_gg_reuse.TestCU7.test_cu7_negative_b_live_invalidation",
        "test_gg_reuse.TestCU7.test_cu7_positive_control_paired",
        "test_gg_reuse.TestI02Positives."
        "test_i02_7_statistical_positive_via_accepted_catalog",
        "test_gg_reuse_reasons.TestBoundaryDiscipline."
        "test_audit_key_unresolved_is_needs_excerpt",
        "test_gg_reuse_reasons.TestBoundaryDiscipline."
        "test_catalog_digest_mismatch",
        "test_gg_reuse_reasons.TestBoundaryDiscipline."
        "test_loader_failure_raises_valueerror_not_verdict",
        "test_gg_reuse_reasons.TestBoundaryDiscipline."
        "test_loader_packs_dir_claim_mismatch_raises",
        "test_gg_reuse_reasons.TestBoundaryDiscipline."
        "test_verification_absent_quarantined_key",
        "test_gg_source_i06_flow.TestI06Flow."
        "test_i06_1_intake_lookup_propose_apply_register",
        "test_gg_source_i06_flow.TestI06Flow."
        "test_i06_2_answers_sources_units_linked",
        "test_gg_source_i06_flow.TestI06Flow."
        "test_i06_3_revision_change_stales_review",
        "test_gg_source_i06_flow.TestI06Flow.test_i06_4_partial_progress",
        "test_gg_source_i06_flow.TestI06Flow.test_i06_5_diagnostics_accuracy",
        "test_gg_source_i06_flow.TestI06Flow.test_i06_6_no_synthetic_bypass",
        "test_gg_source_register.TestAdmission."
        "test_p4_combined_failure_check7_precedes_check8",
        "test_gg_source_register.TestAdmission."
        "test_p4_snapshot_requires_constructed_catalog",
        "test_gg_source_register.TestArtifacts.test_p4_snapshot_registers",
    ), "accepted g005 catalog tree not present"),
    # W-L fix1: R-01's 22-coordinate error footprint and the public-document
    # leak audit both read the private FD-A source workbook, which stays
    # off-repo. Two entries, not one dict.fromkeys group: the reasons differ
    # and _is_approved_skill_skip compares them with ==.
    "tests.test_hort_finance_pipeline.LandErrorRegression."
    "test_error_footprint_is_scenario_independent":
        "private FD-A source workbook absent (off-repo optional input)",
    "tests.test_hort_finance_contract.PublicContract."
    "test_real_h01_audit_reports_no_leak":
        "real H01 workbook not present (local-only FD-A step)",
}

# These seven mocked Office session tests exercise a POSIX fcntl/AppleScript
# product path absent on Windows. Their exact skip is legitimate only on
# win32; on darwin/linux the tests must run and a skip is incomplete.
WINDOW_ONLY_SKILL_SKIP_REASON = (
    "gg_office_session uses fcntl/AppleScript; Windows uses gg_office_win "
    "without a session guard. These tests use a mocked transport."
)
WINDOW_ONLY_SKILL_SKIPS = dict.fromkeys((
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_blocked_message_carries_runnable_recovery_command",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_blocked_receipt_on_disk_carries_recovery_command",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_non_object_guard_json_is_unreadable_and_recoverable",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_cli_recovers_non_object_guard_as_json",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_recovery_clears_record_without_touching_job_files",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_recovery_refuses_while_a_session_holds_the_lock",
    "tests.test_skillgaps_wp2_session.OfficeRecoveryTests."
    "test_cli_recovery_is_json_and_exit_zero",
), WINDOW_ONLY_SKILL_SKIP_REASON)
WINDOW_ONLY_SKILL_SKIP_RULE = "Windows-only product path (exact id and reason)"


SKILL_SKIP_RULE = "optional skill external input (exact id and reason)"


def _is_approved_skill_skip(entry):
    if entry.get("category") != "skipped" or entry.get("events"):
        return False
    test_id = entry.get("test")
    reason = entry.get("detail")
    if (
        test_id in ALLOWED_SKILL_SKIPS
        and ALLOWED_SKILL_SKIPS[test_id] == reason
    ):
        return True
    return (
        sys.platform == "win32"
        and test_id in WINDOW_ONLY_SKILL_SKIPS
        and WINDOW_ONLY_SKILL_SKIPS[test_id] == reason
    )


def _describe_exception(err):
    """``Type: message`` for an exc_info tuple, with no traceback work.

    Last-resort record: it must not read files, import modules or walk
    frames, because those are exactly the operations that can fail while
    an outcome is being recorded.
    """
    exc = err[1] if isinstance(err, tuple) and len(err) > 1 else None
    if exc is None:
        return repr(err)
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001 - a record must never raise
        message = "<unprintable %s>" % type(exc).__name__
    if message:
        return "%s: %s" % (type(exc).__name__, message)
    return type(exc).__name__


class RecordingResult(unittest.TextTestResult):
    """Complete outcome recorder.

    Framework ``add*`` callbacks alone lose subtest results (a parent
    with failed subtests gets no terminal addSuccess/addFailure) and
    fixture pseudo-tests (setUpClass/setUpModule outcomes carry no
    started parent).  This recorder therefore tracks started parents via
    startTest/stopTest, attaches every subtest event to its parent, and
    separates fixture events, then derives exactly one terminal status
    per started parent.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.started = []            # ordered ids, one per started parent
        self._started_ids = set()
        self.parent_terminal = {}    # id -> (kind, detail)
        self.parent_events = {}      # id -> [event dict]
        self.fixture_events = []     # [{test, type, detail}]
        self.executed_modules = set()
        self.module_timings_seconds = {}
        self._current = None

    # -- parent lifecycle -------------------------------------------------
    def startTest(self, test):
        super().startTest(test)
        tid = test.id()
        self.started.append(tid)
        self._started_ids.add(tid)
        self.parent_events.setdefault(tid, [])
        self._current = tid

    def stopTest(self, test):
        super().stopTest(test)
        self._current = None

    # -- helpers ------------------------------------------------------------
    def _exc_info_to_string(self, err, test):
        """Record the outcome even when traceback formatting itself fails.

        ``unittest`` formats inside ``addError``/``addFailure``/
        ``addSubTest`` (through ``super()``), so an unguarded formatter
        failure loses the original error *and* aborts the whole run.
        Observed 2026-10-22: Python 3.15's ``TracebackException`` probes
        the module directory of each frame
        (``_find_incompatible_extension_module``); the run's disposable
        ``git checkout-index`` copy had been removed mid-run, so the
        ``tests`` package directory no longer existed and ``iterdir()``
        raised ``FileNotFoundError`` while a ``ModuleNotFoundError`` was
        being recorded.  The fallback keeps the exception type and
        message, which is what makes the failure attributable.
        """
        try:
            return super()._exc_info_to_string(err, test)
        except Exception as fmt_err:  # noqa: BLE001 - never re-raise
            return "%s [traceback formatting failed: %s]" % (
                _describe_exception(err),
                _describe_exception((None, fmt_err)))

    def _is_subtest_pseudo(self, test):
        """True when ``test`` is a subtest pseudo-object reported while its
        parent is running (its id is ``<parent id> (<params>)``)."""
        if self._current is None:
            return False
        tid = test.id()
        return tid != self._current and tid.startswith(self._current + " (")

    def _terminal_or_fixture(self, test, kind, detail):
        tid = test.id()
        if self._is_subtest_pseudo(test):
            self.parent_events[self._current].append(
                {"type": "subtest_" + kind, "subtest": tid,
                 "detail": detail})
        elif tid in self._started_ids:
            self.parent_terminal[tid] = (kind, detail)
        else:
            self.fixture_events.append(
                {"test": tid, "type": kind, "detail": detail})

    # -- terminal callbacks ---------------------------------------------------
    def addSuccess(self, test):
        super().addSuccess(test)
        self.parent_terminal[test.id()] = ("success", None)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._terminal_or_fixture(
            test, "failure", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._terminal_or_fixture(
            test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._terminal_or_fixture(test, "skip", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        tid = test.id()
        if tid not in self._started_ids:
            # subtest reported for a non-started pseudo-parent: keep it
            # visible as a fixture event rather than dropping it.
            self.fixture_events.append({
                "test": tid,
                "type": "error" if err is not None else "skip",
                "detail": self._exc_info_to_string(err, test)
                if err is not None else "subtest on unstarted parent"})
            return
        sid = (subtest.id() if hasattr(subtest, "id")
               else str(subtest))
        if err is None:
            ev = {"type": "subtest_success", "subtest": sid}
        elif issubclass(err[0], test.failureException):
            ev = {"type": "subtest_failure", "subtest": sid,
                  "detail": self._exc_info_to_string(err, test)}
        else:
            ev = {"type": "subtest_error", "subtest": sid,
                  "detail": self._exc_info_to_string(err, test)}
        self.parent_events.setdefault(tid, []).append(ev)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.parent_terminal[test.id()] = (
            "expected_failure", self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.parent_terminal[test.id()] = ("unexpected_success", None)


def _derive_parent_status(terminal, events):
    """One terminal status per started parent.

    Precedence: any failure/error outcome (parent terminal, subtest
    event, unexpected success or unauthorized expectedFailure) beats a
    skip; a parent skip or skipped subtests yield skipped; otherwise the
    parent's success terminal wins.  ``unaccounted`` means the framework
    produced no outcome for a started parent — an accounting error.
    """
    kind = terminal[0] if terminal else None
    bad_events = [e for e in events
                  if e["type"] in ("subtest_failure", "subtest_error")]
    skip_events = [e for e in events if e["type"] == "subtest_skip"]
    if kind in ("failure", "error", "unexpected_success",
                "expected_failure") or bad_events:
        if kind in ("failure", "error"):
            detail = terminal[1]
        elif kind == "unexpected_success":
            detail = "unexpected success (XPASS)"
        elif kind == "expected_failure":
            detail = ("expectedFailure outcome — a generic decorator is "
                      "not authorization to hide failure")
        else:
            detail = bad_events[0].get("detail")
        return "failed", detail
    if kind == "skip":
        return "skipped", terminal[1]
    if skip_events:
        return "skipped", skip_events[0].get("detail")
    if kind == "success":
        return "success", None
    return "unaccounted", None


def _summarize_result(result, known):
    """Build the runtime section: one entry per started parent, separate
    fixture events, and a framework-vs-recorded reconciliation."""
    entries = []
    counts = {"passed": 0, "known_baseline_defects": 0,
              "unexpected_failed": 0, "skipped": 0}
    rec_fail_err = 0    # framework failure/error equivalents recorded
    rec_skip = 0
    rec_expf = 0
    rec_unexps = 0
    failure_events = 0  # diagnostic: every failure/error event
    unaccounted = []

    for tid in result.started:
        events = result.parent_events.get(tid, [])
        terminal = result.parent_terminal.get(tid)
        status, detail = _derive_parent_status(terminal, events)
        entry = {"test": tid}
        if events:
            entry["events"] = events
        for e in events:
            if e["type"] in ("subtest_failure", "subtest_error"):
                rec_fail_err += 1
                failure_events += 1
            elif e["type"] == "subtest_skip":
                rec_skip += 1
        if status == "success":
            if tid in known:
                entry["category"] = "known_baseline_defect"
                entry.update(known[tid])
                counts["known_baseline_defects"] += 1
            else:
                entry["category"] = "passed"
                counts["passed"] += 1
        elif status == "skipped":
            entry["category"] = "skipped"
            entry["detail"] = detail
            counts["skipped"] += 1
            # a framework skip exists only when the parent itself got a
            # terminal addSkip; a status derived from subtest-skip events
            # is already counted per event.
            if terminal and terminal[0] == "skip":
                rec_skip += 1
        elif status == "failed":
            entry["category"] = "failed"
            entry["detail"] = (detail or "")[-1500:]
            counts["unexpected_failed"] += 1
            tkind = terminal[0] if terminal else None
            if tkind in ("failure", "error"):
                rec_fail_err += 1
                failure_events += 1
            elif tkind == "expected_failure":
                rec_expf += 1
            elif tkind == "unexpected_success":
                rec_unexps += 1
        else:  # unaccounted
            entry["category"] = "failed"
            entry["detail"] = ("framework produced no outcome for a "
                               "started parent")
            counts["unexpected_failed"] += 1
            unaccounted.append(tid)
        entries.append(entry)

    for fe in result.fixture_events:
        if fe["type"] in ("failure", "error"):
            counts["unexpected_failed"] += 1
            rec_fail_err += 1
            failure_events += 1
        elif fe["type"] == "skip":
            rec_skip += 1

    framework = {
        "testsRun": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expectedFailures": len(result.expectedFailures),
        "unexpectedSuccesses": len(result.unexpectedSuccesses),
        "was_successful": result.wasSuccessful(),
    }
    accounting_errors = []
    if len(entries) != result.testsRun:
        accounting_errors.append(
            "started-parent count %d != framework testsRun %d"
            % (len(entries), result.testsRun))
    if rec_fail_err != framework["failures"] + framework["errors"]:
        accounting_errors.append(
            "recorded failure/error events %d != framework "
            "failures+errors %d"
            % (rec_fail_err,
               framework["failures"] + framework["errors"]))
    if rec_skip != framework["skipped"]:
        accounting_errors.append(
            "recorded skip events %d != framework skipped %d"
            % (rec_skip, framework["skipped"]))
    if rec_expf != framework["expectedFailures"]:
        accounting_errors.append(
            "recorded expectedFailure events %d != framework %d"
            % (rec_expf, framework["expectedFailures"]))
    if rec_unexps != framework["unexpectedSuccesses"]:
        accounting_errors.append(
            "recorded unexpectedSuccess events %d != framework %d"
            % (rec_unexps, framework["unexpectedSuccesses"]))
    if unaccounted:
        accounting_errors.append(
            "started parents with no recorded outcome: "
            + ", ".join(unaccounted))
    if not result.wasSuccessful() and not (
            framework["failures"] or framework["errors"]
            or framework["unexpectedSuccesses"]):
        accounting_errors.append(
            "framework reports wasSuccessful()==False with empty "
            "outcome lists — outcome could not be reconciled")
    if result.wasSuccessful() and (
            framework["failures"] or framework["errors"]):
        accounting_errors.append(
            "framework reports wasSuccessful()==True with nonempty "
            "failure/error lists — outcome could not be reconciled")

    return {
        "status": "completed",
        "tests_run": result.testsRun,
        **counts,
        "failure_events": failure_events,
        "entries": entries,
        "fixture_events": result.fixture_events,
        "accounting": {"framework": framework,
                       "errors": accounting_errors},
        "suite_output_tail": result.stream.getvalue()[-4000:]
        if hasattr(result, "stream") else "",
        "known_defect_records": known,
    }


def _run_bundle_check(root):
    checker = root / "tools" / "check_bundle.py"
    if not checker.is_file():
        return {
            "status": "incomplete",
            "reason": "tools/check_bundle.py missing — bundle check is part "
                      "of the default contract and is not silently skipped",
            "command": [sys.executable, "tools/check_bundle.py",
                        "--root", str(root)],
        }
    proc = subprocess.run(
        [sys.executable, str(checker), "--root", str(root)],
        capture_output=True, text=True, cwd=root)
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "exit": proc.returncode,
        "command": [sys.executable, "tools/check_bundle.py",
                    "--root", str(root)],
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def _dependency_preflight(root):
    """Verify declared runtime deps on THIS interpreter, before tests.

    Source of truth: the candidate's own
    ``skills/knuaf-doc/scripts/runtime-deps.json``, checked through the
    candidate's ``gg_deps.py`` read-only helpers (manifest validation,
    in-process import + dist metadata, declared-range comparison).
    ``tests._harness.runtime`` proves the helper module lives inside the
    candidate tree.  No install, no venv selection, no HOME/dev fallback,
    no COM instance — a dependency whose declared platform differs from
    ``sys.platform`` is recorded ``not_applicable`` and never counted
    missing.
    """
    manifest = root / "skills" / "knuaf-doc" / "scripts" / "runtime-deps.json"
    diag = {"manifest": str(manifest), "ok": False,
            "manifest_error": None, "checks": [],
            "not_applicable": [], "failed": []}
    try:
        sys.path.insert(0, str(root))
        try:
            from tests._harness import runtime
        finally:
            sys.path.remove(str(root))
        gd = runtime("gg_deps")
        helper = Path(getattr(gd, "__file__", "")).resolve()
        if not helper.is_relative_to(
                (root / "skills" / "knuaf-doc" / "scripts").resolve()):
            raise ImportError(f"gg_deps resolved outside candidate: {helper}")
    except Exception as e:
        diag["manifest_error"] = f"cannot load candidate gg_deps: {e}"
        return diag
    try:
        deps = gd.load_manifest(manifest)
    except Exception as e:
        diag["manifest_error"] = str(e)
        return diag
    for d in deps:
        if not gd._applicable(d):
            diag["not_applicable"].append({
                "dist": d["dist"],
                "declared_platform": d.get("platform"),
                "sys_platform": sys.platform})
            continue
        imported, version = gd._import_and_version_current(
            d["import_name"], d["dist"])
        row = gd._probe_row(d, imported, version)
        row.update({"dist": d["dist"], "spec": d["spec"],
                    "import_name": d["import_name"],
                    "purpose": d.get("purpose", "")})
        diag["checks"].append(row)
        if row["status"] != "installed":
            diag["failed"].append(row)
    diag["ok"] = diag["manifest_error"] is None and not diag["failed"]
    return diag


try:
    from validation_sharding import (
        PR_SCOPES, assign_modules, discover_modules, expected_ids,
        expected_ids_for, merge_shard_reports, parse_shard, scoped_modules)
except ImportError:
    from tools.validation_sharding import (
        PR_SCOPES, assign_modules, discover_modules, expected_ids,
        expected_ids_for, merge_shard_reports, parse_shard, scoped_modules)


class ModuleTimingSuite(unittest.TestSuite):
    """Run one complete module atomically and record fixture-inclusive time."""

    def __init__(self, module, tests):
        super().__init__(tests)
        self.module = module

    def run(self, result, debug=False):
        started = time.perf_counter()
        try:
            return super().run(result, debug)
        finally:
            result.executed_modules.add(self.module)
            result.module_timings_seconds[self.module] = round(
                time.perf_counter() - started, 6)


def _run_runtime_tests(root, shard=None, scope="full"):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    try:
        modules, all_ids = discover_modules(root)
        modules = scoped_modules(modules, scope)
        discovered_ids = expected_ids(modules, all_ids, scope)
        # Units (modules, or classes/methods of the two split modules) run in
        # the order the shard report lists them.
        selected_modules = list(modules)
        if shard is not None:
            index, count = shard
            selected_modules = assign_modules(modules, count)[index]
        suite = unittest.TestSuite()
        for module in selected_modules:
            suite.addTest(ModuleTimingSuite(module, modules[module]))
        runner = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=2, resultclass=RecordingResult)
        result = runner.run(suite)
        import tests._harness as harness
        runtime = _summarize_result(result, harness.KNOWN_DEFECTS)
        runtime["executed_modules"] = sorted(result.executed_modules)
        runtime["module_timings_seconds"] = dict(
            sorted(result.module_timings_seconds.items()))
    finally:
        sys.path.remove(str(root))
    if shard is not None:
        runtime["shard"] = {
            "index": shard[0] + 1, "count": shard[1],
            "modules": selected_modules,
            "discovered_test_ids": [
                test.id() for module in selected_modules for test in modules[module]],
            "executed_test_ids": list(result.started)}
        runtime["discovered_test_ids"] = runtime["shard"]["discovered_test_ids"]
    else:
        runtime["discovered_test_ids"] = discovered_ids
    return runtime


def _evaluate_external_gate(entries, suite_ran):
    """Fail-closed classification of the declared native placeholder.

    Returns (approved_external_skips, gate_warnings, observation).
    The gate's pending record is emitted separately; this function only
    decides whether the observed outcome is the exact authorized
    placeholder skip.
    """
    approved = []
    warnings = []
    observation = None
    if not suite_ran:
        return approved, warnings, observation
    obs = [e for e in entries if e["test"] == NATIVE_GATE["test_id"]]
    if not obs:
        warnings.append(
            "external gate %s declared test missing from completed "
            "suite: %s" % (NATIVE_GATE["gate_id"], NATIVE_GATE["test_id"]))
        return approved, warnings, observation
    if len(obs) > 1:
        warnings.append(
            "external gate %s test id appeared %d times: %s"
            % (NATIVE_GATE["gate_id"], len(obs), NATIVE_GATE["test_id"]))
    e = obs[0]
    observation = e
    cat = e["category"]
    if cat in ("passed", "known_baseline_defect"):
        warnings.append(
            "external gate %s recorded a %s outcome — the placeholder "
            "cannot legitimately pass; this looks forged"
            % (NATIVE_GATE["gate_id"], cat))
    elif cat == "skipped":
        if (e.get("detail") == NATIVE_GATE["reason"]
                and sys.platform in NATIVE_GATE["allowed_platforms"]):
            approved.append({
                "test": e["test"],
                "reason": e["detail"],
                "rule": NATIVE_GATE["rule"],
                "gate_id": NATIVE_GATE["gate_id"]})
        # otherwise it stays an ordinary unapproved skip
    # 'failed' needs no special handling — genuine failure/error on the
    # gate id is a normal unexpected failure, never exempt.
    return approved, warnings, observation


def _pending_external_checks(observation):
    """The declared pending-work record for the current P2 scope."""
    detail = NATIVE_GATE["reason"]
    if observation is not None and observation.get("detail"):
        detail = observation["detail"]
    return [{
        "gate_id": NATIVE_GATE["gate_id"],
        "status": "not_run",
        "support": NATIVE_GATE["support"],
        "implementation": NATIVE_GATE["implementation"],
        "test_id": NATIVE_GATE["test_id"],
        "required_environment": NATIVE_GATE["required_environment"],
        "reason": detail,
        "observed_platform": sys.platform,
    }]


def _decide_verdict(unexpected_causes, incomplete_causes,
                    pending_external_checks, mode):
    """Precedence: unexpected_failures(1) > incomplete(3) >
    ok_platform_scoped(0, default only) > ok(0)."""
    if unexpected_causes:
        return "unexpected_failures", 1
    if incomplete_causes:
        return "incomplete", 3
    if pending_external_checks and mode == "default":
        return "ok_platform_scoped", 0
    return "ok", 0


def _completion_flags(mode, bundle_status, dep_ok, runtime,
                      unapproved_skips, inventory_warnings,
                      incomplete_causes, unexpected_causes,
                      pending_external_checks):
    """(platform_validation_completed, full_validation_completed).

    platform flag requires default mode + clean complete run; full can
    never coexist with pending external gates.  Runtime-only always
    returns both False.
    """
    platform_done = bool(
        mode == "default"
        and bundle_status == "pass"
        and dep_ok
        and runtime["status"] == "completed"
        and runtime["tests_run"] > 0
        and runtime["unexpected_failed"] == 0
        and not unapproved_skips
        and not inventory_warnings
        and not incomplete_causes
        and not unexpected_causes
        and not runtime["accounting"]["errors"])
    return platform_done, bool(
        platform_done and not pending_external_checks)


def entry_status(entry):
    """Inventory status of one defect entry; explicit status wins."""
    if entry.get("status"):
        return entry["status"]
    if not entry.get("applies_to_main"):
        return "not_applicable"
    if entry.get("gate") == "deferred":
        return "deferred"
    return "open"


def verify_inventory(runtime, inventory, unapproved_skips=()):
    """Classify every inventory entry against one completed runtime result.

    Behaviour-identical extraction of the layer that used to be nested in
    ``main`` (design §7④): returns the classification lists and the warnings,
    and never raises — a mismatch is a warning that ``main`` promotes into
    ``unexpected_causes``.  ``runtime`` must be a completed result; the
    "tests never ran" classification stays in ``main`` because it makes no
    claim about outcomes.
    """
    not_applicable, deferred, fixed = [], [], []
    skipped_defects, warnings = [], []
    defect_entries = [e for e in runtime["entries"]
                      if e.get("category") == "known_baseline_defect"]
    reproduced = {e["defect"] for e in defect_entries}
    reproduced_tests = {e["test"] for e in defect_entries}
    passed_tests = {e["test"] for e in runtime["entries"]
                    if e.get("category") == "passed"}
    ran_tests = {e["test"] for e in runtime["entries"]}

    def _verify_entry(entry):
        status = entry_status(entry)
        if status == "not_applicable" or not entry.get("applies_to_main"):
            not_applicable.append({
                "id": entry["id"], "gate": entry.get("gate"),
                "reason": entry.get("reason", "")})
            return
        if status == "deferred":
            deferred.append({
                "id": entry["id"], "gate": entry.get("gate"),
                "reason": entry.get("reason", "")})
            return
        if status == "fixed":
            fixed.append({"id": entry["id"], "gate": entry.get("gate")})
            tests = entry.get("tests") or (
                [entry["test"]] if entry.get("test") else [])
            if not tests:
                warnings.append(
                    "fixed defect %s has no regression test mapping"
                    % entry["id"])
            for tid in tests:
                if tid not in ran_tests:
                    warnings.append(
                        "fixed defect %s regression test did not run: %s"
                        % (entry["id"], tid))
                elif tid not in passed_tests:
                    warnings.append(
                        "fixed defect %s regression test did not pass: %s"
                        % (entry["id"], tid))
            if entry["id"] in reproduced or entry.get("test") in (
                    reproduced_tests):
                warnings.append(
                    "fixed defect %s still reproduced its baseline "
                    "signature" % entry["id"])
            return
        # open: the exact documented signature must still reproduce
        if entry.get("test") and not (
            entry["id"] in reproduced
            or entry["test"] in reproduced_tests
        ):
            if any(s["test"] == entry["test"]
                   for s in unapproved_skips):
                skipped_defects.append({
                    "id": entry["id"], "test": entry["test"],
                    "reason": "defect test skipped — unapproved, "
                              "counted as incomplete not defect"})
            else:
                warnings.append(
                    "inventory defect %s was not reproduced by its "
                    "test" % entry["id"])

    for entry in inventory.get("defects", []):
        _verify_entry(entry)
    known_ids = {e["id"] for e in inventory.get("defects", [])}
    for defect in reproduced:
        if defect not in known_ids:
            warnings.append(
                "reproduced defect %s is not in the inventory" % defect)
    return {"not_applicable": not_applicable, "deferred": deferred,
            "fixed": fixed, "skipped_defects": skipped_defects,
            "warnings": warnings}


def _load_inventory(root):
    path = root / "tests" / "regression_inventory.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="knuaf-doc public validation")
    ap.add_argument("--json", dest="json_out", metavar="PATH",
                    help="write the machine-readable report to PATH")
    ap.add_argument("--runtime-only", action="store_true",
                    help="development aid: skip the bundle check; never the "
                         "final or CI command")
    ap.add_argument("--shard", type=parse_shard, metavar="I/N",
                    help="run one deterministic whole-module test shard")
    ap.add_argument("--merge-shards", nargs="+", metavar="REPORT",
                    help="merge shard JSON reports and run the final contract")
    ap.add_argument("--shard-count", type=int,
                    help="expected number of shard reports when merging")
    ap.add_argument("--pr-scope", choices=sorted(PR_SCOPES), default="full",
                    help="test scope: full runs every unit; code defers the "
                         "nested runner units to the main-branch run "
                         "(design §2.1). The workflow decides it once")
    a = ap.parse_args(argv)
    if a.shard is not None and a.merge_shards:
        ap.error("--shard and --merge-shards are mutually exclusive")
    if bool(a.merge_shards) != (a.shard_count is not None):
        ap.error("--merge-shards requires --shard-count")

    if sys.version_info < MIN_PYTHON:
        print(
            "unsupported Python %s — this suite requires >=%s"
            % (sys.version.split()[0], ".".join(map(str, MIN_PYTHON))),
            file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    mode = "runtime_only" if a.runtime_only else "default"
    shard_run = a.shard is not None
    template = None
    if a.merge_shards:
        if a.runtime_only or a.shard_count < 1:
            ap.error("--merge-shards needs a positive --shard-count and default mode")
        try:
            template, runtime = merge_shard_reports(
                root, a.merge_shards, a.shard_count, scope=a.pr_scope)
            if template.get("root") != str(root):
                raise ValueError("shard reports belong to another repository root")
            if template.get("baseline_main") != BASELINE_MAIN:
                raise ValueError("shard reports use another validation baseline")
            version = template.get("python", {}).get("version", "").split(".")[:2]
            if version != sys.version.split(".")[:2]:
                raise ValueError("shard reports use a different Python version")
            if template.get("tool") != "tools/run_validation.py":
                raise ValueError("shard reports have an unknown producer")
            bundle = template["bundle"]
            dep = template["dependency"]
        except (OSError, ValueError, KeyError, TypeError,
                json.JSONDecodeError) as exc:
            print("shard report merge failed: %s" % exc, file=sys.stderr)
            return 1
    else:
        bundle = None
        dep = None
        runtime = None

    unexpected_causes = []   # -> verdict unexpected_failures, exit 1
    incomplete_causes = []   # -> verdict incomplete, exit 3

    # --- nested-flag contract -------------------------------------------
    nested = os.environ.get(NESTED_FLAG)
    nested_allowed = False
    if nested:
        if not a.runtime_only:
            incomplete_causes.append(
                f"{NESTED_FLAG}={nested!r} is set in default (final) mode — "
                "the flag only exists to stop recursion inside "
                "--runtime-only nested runs")
        elif nested != NESTED_VALUE:
            incomplete_causes.append(
                f"{NESTED_FLAG}={nested!r} is not the sanctioned value "
                f"{NESTED_VALUE!r} — arbitrary values do not exempt skips")
        else:
            nested_allowed = True

    if template is None:
        bundle = ({"status": "skipped_runtime_only",
                  "note": "not the final/CI contract"} if a.runtime_only
                 else _run_bundle_check(root))
        if bundle["status"] == "fail":
            unexpected_causes.append(
                "bundle integrity check failed (see bundle.stdout_tail)")
        elif bundle["status"] == "incomplete":
            incomplete_causes.append(bundle["reason"])
        dep = _dependency_preflight(root)
        if not dep["ok"]:
            if dep["manifest_error"]:
                incomplete_causes.append(
                    "dependency manifest/loader error: " + dep["manifest_error"])
            for row in dep["failed"]:
                incomplete_causes.append(
                    "dependency not ready: {dist} spec={spec!r} "
                    "status={status} imported={imported} version={version}".format(
                        **row))
            runtime = {
                "status": "not_run_dependency", "tests_run": 0,
                "passed": 0, "known_baseline_defects": 0,
                "unexpected_failed": 0, "skipped": 0, "failure_events": 0,
                "entries": [], "fixture_events": [],
                "accounting": {"framework": None, "errors": []},
                "suite_output_tail": "", "known_defect_records": {},
                "executed_modules": [], "module_timings_seconds": {},
                "discovered_test_ids": []}
        else:
            runtime = _run_runtime_tests(root, a.shard, scope=a.pr_scope)

    shard_metadata = runtime.pop("shard", None) if shard_run else None
    if shard_run and shard_metadata is None:
        modules, _ = discover_modules(root)
        modules = scoped_modules(modules, a.pr_scope)
        index, count = a.shard
        selected_modules = assign_modules(modules, count)[index]
        shard_metadata = {
            "index": index + 1, "count": count,
            "modules": selected_modules,
            "discovered_test_ids": [
                test.id() for module in selected_modules
                for test in modules[module]],
            "executed_test_ids": runtime.get("discovered_test_ids", []),
        }

    if shard_run:
        if runtime["status"] != "completed":
            incomplete_causes.append("shard runtime did not complete")
        if runtime["tests_run"] != len(shard_metadata["executed_test_ids"]):
            incomplete_causes.append("shard started test IDs do not match tests_run")
        framework = runtime["accounting"]["framework"]
        if not isinstance(framework, dict) or framework.get("was_successful") is not True:
            incomplete_causes.append("shard framework reports unsuccessful")
        if runtime["accounting"]["errors"]:
            incomplete_causes.append("shard accounting contains errors")
        if runtime["fixture_events"]:
            incomplete_causes.append("shard contains fixture outcomes")
        if runtime["unexpected_failed"]:
            unexpected_causes.append("shard contains test failures")

    inventory = _load_inventory(root) if a.merge_shards else (
        None if shard_run else _load_inventory(root))
    not_applicable = []
    deferred = []
    fixed = []
    inventory_warnings = []
    skipped_defects = []
    unapproved_skips = []
    reproduced = {e["defect"] for e in runtime["entries"]
                  if e.get("category") == "known_baseline_defect"}
    suite_ran = runtime["status"] == "completed"
    if shard_run or a.merge_shards:
        gate_present = any(entry.get("test") == NATIVE_GATE["test_id"]
                           for entry in runtime["entries"])
        approved_external_skips, gate_warnings, gate_observation = (
            _evaluate_external_gate(runtime["entries"], gate_present))
        if a.merge_shards:
            inventory_warnings.extend(gate_warnings)
            if runtime.get("discovered_test_ids") != expected_ids_for(
                    root, a.pr_scope):
                inventory_warnings.append(
                    "merged discovered test IDs differ from fresh discover")
        elif not gate_present:
            gate_observation = None
    else:
        approved_external_skips, gate_warnings, gate_observation = (
            _evaluate_external_gate(runtime["entries"], suite_ran))
        inventory_warnings.extend(gate_warnings)
    approved_ids = {skip["test"] for skip in approved_external_skips}
    pending_external_checks = _pending_external_checks(gate_observation)
    approved_skill_skips = []
    for e in runtime["entries"]:
        if e.get("category") != "skipped":
            continue
        if e["test"] in approved_ids:
            continue
        if nested_allowed and e["test"] in ALLOWED_NESTED_SKIP_IDS:
            continue
        if _is_approved_skill_skip(e):
            rule = (WINDOW_ONLY_SKILL_SKIP_RULE
                    if e["test"] in WINDOW_ONLY_SKILL_SKIPS
                    else SKILL_SKIP_RULE)
            approved_skill_skips.append({
                "test": e["test"], "reason": e["detail"], "rule": rule})
            continue
        unapproved_skips.append({"test": e["test"],
                                 "reason": e.get("detail")})
    for fe in runtime["fixture_events"]:
        if fe["type"] == "skip":
            unapproved_skips.append({"test": fe["test"],
                                     "reason": fe.get("detail"),
                                     "fixture": True})
    if unapproved_skips:
        incomplete_causes.append(
            "unapproved test skips: " + "; ".join(
                f"{s['test']} ({s['reason']})" for s in unapproved_skips))

    if runtime["status"] == "completed" and (not shard_run or a.merge_shards):
        if inventory is None:
            inventory_warnings.append(
                "tests/regression_inventory.json missing")
        else:
            layer = verify_inventory(runtime, inventory, unapproved_skips)
            not_applicable.extend(layer["not_applicable"])
            deferred.extend(layer["deferred"])
            fixed.extend(layer["fixed"])
            skipped_defects.extend(layer["skipped_defects"])
            inventory_warnings.extend(layer["warnings"])
    elif runtime["status"] != "completed" and inventory:
        # tests never ran: still expose the classification lists
        for entry in inventory.get("defects", []):
            status = entry_status(entry)
            if status == "not_applicable" or not entry.get("applies_to_main"):
                not_applicable.append({
                    "id": entry["id"], "gate": entry.get("gate"),
                    "reason": entry.get("reason", "")})
            elif status == "deferred":
                deferred.append({"id": entry["id"], "gate": entry.get("gate"),
                                 "reason": entry.get("reason", "")})
            elif status == "fixed":
                fixed.append({"id": entry["id"], "gate": entry.get("gate")})
    if runtime["unexpected_failed"]:
        unexpected_causes.append(
            "%d unexpected test failure/error/XPASS"
            % runtime["unexpected_failed"])
    if inventory_warnings:
        unexpected_causes.append(
            "inventory mismatches: " + "; ".join(inventory_warnings))
    for acc_err in runtime["accounting"]["errors"]:
        unexpected_causes.append("accounting mismatch: " + acc_err)
    if shard_run and (runtime["unexpected_failed"]
                      or runtime["accounting"]["errors"]
                      or runtime["fixture_events"]):
        incomplete_causes.append("shard contains failed test or fixture outcomes")

    # --- verdict ----------------------------------------------------------
    verdict, code = _decide_verdict(
        unexpected_causes, incomplete_causes, pending_external_checks, mode)
    platform_validation_completed, full_validation_completed = (
        _completion_flags(
            mode, bundle["status"], dep["ok"], runtime,
            unapproved_skips, inventory_warnings,
            incomplete_causes, unexpected_causes,
            pending_external_checks))

    if shard_run:
        platform_validation_completed = False
        full_validation_completed = False

    report = {
        "tool": "tools/run_validation.py",
        "verdict": verdict,
        "exit_code": code,
        "mode": mode,
        "validation_scope": ("current_platform" if mode == "default"
                             else "development_runtime_only"),
        "platform_validation_completed": platform_validation_completed,
        "full_validation_completed": full_validation_completed,
        "pending_external_checks": pending_external_checks,
        "approved_external_skips": approved_external_skips,
        "approved_skill_skips": approved_skill_skips,
        "causes": {"unexpected": unexpected_causes,
                   "incomplete": incomplete_causes},
        "python": {"version": sys.version.split()[0],
                   "executable": sys.executable,
                   "requires": ">=%s" % ".".join(map(str, MIN_PYTHON))},
        "root": str(root),
        "baseline_main": BASELINE_MAIN,
        "bundle": bundle,
        "dependency": dep,
        "runtime": {k: v for k, v in runtime.items()
                    if k != "known_defect_records"},
        "fixed": fixed,
        "deferred": deferred,
        "not_applicable": not_applicable,
        "unapproved_skips": unapproved_skips,
        "defect_tests_skipped": skipped_defects,
        "inventory_warnings": inventory_warnings,
        "note": "runner verdict 'ok'/'ok_platform_scoped' is not a "
                "product PASS: known_baseline_defects are reproduced main "
                "defects owned by their gate units; pending_external_checks "
                "are declared gates not run on this platform (current P2: "
                "P2-L09-native, placeholder only — no native support claim); "
                "completion flags are run-integrity facts, not product "
                "approval",
    }
    if shard_metadata is not None:
        report["shard"] = shard_metadata
    if shard_run:
        report["verdict"] = "shard_completed" if code == 0 else "shard_failed"
        print("shard %d/%d: %d tests across %d modules; exit=%d"
              % (shard_metadata["index"], shard_metadata["count"],
                 runtime["tests_run"], len(shard_metadata["modules"]), code))
    else:
        print("module timings: %d modules reported" % len(
            runtime.get("module_timings_seconds", {})))
    report["runtime"]["executed_modules"] = runtime.get("executed_modules", [])
    report["runtime"]["module_timings_seconds"] = runtime.get(
        "module_timings_seconds", {})
    report["runtime"]["discovered_test_ids"] = runtime.get(
        "discovered_test_ids", [])

    summary = [
        "verdict=%s exit=%d" % (verdict, code),
        "mode=%s scope=%s platform_validation_completed=%s "
        "full_validation_completed=%s"
        % (mode, report["validation_scope"],
           str(platform_validation_completed).lower(),
           str(full_validation_completed).lower()),
        "bundle=%s" % bundle["status"],
        "dependency=%s" % ("ready" if dep["ok"] else "NOT READY"),
        "tests_run=%d passed=%d known_baseline_defects=%d "
        "unexpected_failed=%d skipped=%d not_applicable=%d" % (
            runtime["tests_run"], runtime["passed"],
            runtime["known_baseline_defects"],
            runtime["unexpected_failed"], runtime["skipped"],
            len(not_applicable)),
        "fixed=%d open=%d deferred=%d" % (
            len(fixed),
            len(reproduced),
            len(deferred)),
    ]
    if pending_external_checks:
        summary.append(
            "pending external checks: " + "; ".join(
                "%s status=%s support=%s implementation=%s env=%s"
                % (p["gate_id"], p["status"], p["support"],
                   p["implementation"], p["required_environment"])
                for p in pending_external_checks))
    if approved_external_skips:
        summary.append(
            "approved external skips: " + "; ".join(
                s["test"] for s in approved_external_skips))
    if approved_skill_skips:
        summary.append(
            "approved skill skips: " + "; ".join(
                "%s (%s)" % (s["test"], s["reason"])
                for s in approved_skill_skips))
    if runtime["status"] == "not_run_dependency":
        summary.append("runtime=not_run_dependency (tests not executed)")
    if runtime.get("known_defect_records"):
        summary.append("reproduced baseline defects: " + ", ".join(
            sorted(r["defect"]
                   for r in runtime["known_defect_records"].values())))
    if runtime["fixture_events"]:
        summary.append(
            "fixture events: " + "; ".join(
                "%s %s" % (fe["type"], fe["test"])
                for fe in runtime["fixture_events"]))
    if runtime["accounting"]["errors"]:
        summary.append(
            "accounting errors: "
            + " | ".join(runtime["accounting"]["errors"]))
    if unapproved_skips:
        summary.append("unapproved skips: " + "; ".join(
            s["test"] for s in unapproved_skips))
    if skipped_defects:
        summary.append("defect tests skipped (unapproved): " + ", ".join(
            sorted(d["id"] for d in skipped_defects)))
    if inventory_warnings:
        summary.append("inventory warnings: " + "; ".join(inventory_warnings))
    if unexpected_causes:
        summary.append("unexpected causes: " + " | ".join(unexpected_causes))
    if incomplete_causes:
        summary.append("incomplete causes: " + " | ".join(incomplete_causes))
    summary.append(report["note"])
    if verdict == "ok_platform_scoped":
        summary.append(
            "ok_platform_scoped means current-platform gates passed; "
            "declared external gate(s) above remain pending — not "
            "whole-product or native PASS")
    out = "\n".join(summary)
    print(out)

    if a.json_out:
        dest = Path(a.json_out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
