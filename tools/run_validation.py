#!/usr/bin/env python3
"""Single-command public validation for the knuaf-doc candidate.

Usage:
    python3 tools/run_validation.py [--json <output>] [--runtime-only]

Default mode runs ``tools/check_bundle.py --root <repo root>`` as a
subprocess, then verifies the declared runtime dependencies on THIS
interpreter, then runs the runtime suite under ``tests/``.
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
the sole approved external skip; it records the P2-L09-native pending
gate.  The same test passing, failing, skipping with another reason,
running on another platform, appearing twice, or being absent from a
completed suite is never silently excused — it is an unexpected failure
or unapproved skip as applicable.
"""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
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


def _run_runtime_tests(root):
    sys.path.insert(0, str(root))
    try:
        loader = unittest.TestLoader()
        suite = loader.discover(str(root / "tests"), top_level_dir=str(root))
        stream = io.StringIO()
        runner = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=RecordingResult)
        result = runner.run(suite)
        # tests._harness was already imported by the suite; this is the same
        # module object, so its KNOWN_DEFECTS registry is shared.
        import tests._harness as harness
        known = harness.KNOWN_DEFECTS
    finally:
        sys.path.remove(str(root))
    return _summarize_result(result, known)


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
    a = ap.parse_args(argv)

    if sys.version_info < MIN_PYTHON:
        print(
            "unsupported Python %s — this suite requires >=%s"
            % (sys.version.split()[0], ".".join(map(str, MIN_PYTHON))),
            file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    mode = "runtime_only" if a.runtime_only else "default"

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

    # --- bundle check ----------------------------------------------------
    if a.runtime_only:
        bundle = {"status": "skipped_runtime_only",
                  "note": "not the final/CI contract"}
    else:
        bundle = _run_bundle_check(root)
        if bundle["status"] == "fail":
            unexpected_causes.append(
                "bundle integrity check failed (see bundle.stdout_tail)")
        elif bundle["status"] == "incomplete":
            incomplete_causes.append(bundle["reason"])

    # --- dependency readiness (current interpreter) ----------------------
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
            "status": "not_run_dependency",
            "tests_run": 0, "passed": 0, "known_baseline_defects": 0,
            "unexpected_failed": 0, "skipped": 0, "failure_events": 0,
            "entries": [], "fixture_events": [],
            "accounting": {"framework": None, "errors": []},
            "suite_output_tail": "",
            "known_defect_records": {},
        }
    else:
        runtime = _run_runtime_tests(root)

    # --- classification vs inventory ------------------------------------
    inventory = _load_inventory(root)
    not_applicable = []
    deferred = []
    fixed = []
    inventory_warnings = []
    skipped_defects = []
    unapproved_skips = []

    suite_ran = runtime["status"] == "completed"
    approved_external_skips, gate_warnings, gate_observation = (
        _evaluate_external_gate(runtime["entries"], suite_ran))
    inventory_warnings.extend(gate_warnings)
    approved_ids = {s["test"] for s in approved_external_skips}
    pending_external_checks = _pending_external_checks(gate_observation)

    for e in runtime["entries"]:
        if e.get("category") != "skipped":
            continue
        if e["test"] in approved_ids:
            continue
        if nested_allowed and e["test"] in ALLOWED_NESTED_SKIP_IDS:
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

    defect_entries = [e for e in runtime["entries"]
                      if e.get("category") == "known_baseline_defect"]
    reproduced = {e["defect"] for e in defect_entries}
    reproduced_tests = {e["test"] for e in defect_entries}
    passed_tests = {e["test"] for e in runtime["entries"]
                    if e.get("category") == "passed"}
    ran_tests = {e["test"] for e in runtime["entries"]}

    def _entry_status(entry):
        # explicit status wins; derive one for inventories that predate it
        if entry.get("status"):
            return entry["status"]
        if not entry.get("applies_to_main"):
            return "not_applicable"
        if entry.get("gate") == "deferred":
            return "deferred"
        return "open"

    def _verify_entry(entry):
        status = _entry_status(entry)
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
                inventory_warnings.append(
                    "fixed defect %s has no regression test mapping"
                    % entry["id"])
            for tid in tests:
                if tid not in ran_tests:
                    inventory_warnings.append(
                        "fixed defect %s regression test did not run: %s"
                        % (entry["id"], tid))
                elif tid not in passed_tests:
                    inventory_warnings.append(
                        "fixed defect %s regression test did not pass: %s"
                        % (entry["id"], tid))
            if entry["id"] in reproduced or entry.get("test") in (
                    reproduced_tests):
                inventory_warnings.append(
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
                inventory_warnings.append(
                    "inventory defect %s was not reproduced by its "
                    "test" % entry["id"])

    if runtime["status"] == "completed":
        if inventory is None:
            inventory_warnings.append(
                "tests/regression_inventory.json missing")
        else:
            for entry in inventory.get("defects", []):
                _verify_entry(entry)
            known_ids = {e["id"] for e in inventory.get("defects", [])}
            for defect in reproduced:
                if defect not in known_ids:
                    inventory_warnings.append(
                        "reproduced defect %s is not in the inventory"
                        % defect)
    else:
        # tests never ran: still expose the classification lists
        if inventory:
            for entry in inventory.get("defects", []):
                status = _entry_status(entry)
                if status == "not_applicable" or not entry.get(
                        "applies_to_main"):
                    not_applicable.append({
                        "id": entry["id"], "gate": entry.get("gate"),
                        "reason": entry.get("reason", "")})
                elif status == "deferred":
                    deferred.append({
                        "id": entry["id"], "gate": entry.get("gate"),
                        "reason": entry.get("reason", "")})
                elif status == "fixed":
                    fixed.append({"id": entry["id"],
                                  "gate": entry.get("gate")})

    if runtime["unexpected_failed"]:
        unexpected_causes.append(
            "%d unexpected test failure/error/XPASS"
            % runtime["unexpected_failed"])
    if inventory_warnings:
        unexpected_causes.append(
            "inventory mismatches: " + "; ".join(inventory_warnings))
    for acc_err in runtime["accounting"]["errors"]:
        unexpected_causes.append("accounting mismatch: " + acc_err)

    # --- verdict ----------------------------------------------------------
    verdict, code = _decide_verdict(
        unexpected_causes, incomplete_causes, pending_external_checks, mode)
    platform_validation_completed, full_validation_completed = (
        _completion_flags(
            mode, bundle["status"], dep["ok"], runtime,
            unapproved_skips, inventory_warnings,
            incomplete_causes, unexpected_causes,
            pending_external_checks))

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
