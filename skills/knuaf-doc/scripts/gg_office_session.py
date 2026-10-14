"""Small macOS Office job guard; no app quit or timeout recovery commands.

The lock coordinates this CLI, not the user's UI. An interrupted AppleEvent
may still run in Office, so an in-flight/unknown record blocks subsequent
CLI sessions until a human reconciles it. Never infer ownership from focus.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import time

SESSION_MARKER = "-- gg-office-owned-session-v1"
SCHEMA = "gg-office-session/v1"
EXCEL_RECALCULATION_HOLD = "owned_workbook_recalculation_unproven"


def _template(kind: str) -> str:
    if kind == "excel":
        # No exact-workbook dependency-graph calculation is proven. Even
        # a direct template invocation must not enter Office operations.
        return f'''{SESSION_MARKER}
on run argv
    error "{EXCEL_RECALCULATION_HOLD}" number -2700
end run
'''
    app = "Microsoft Word" if kind == "word" else "Microsoft Excel"
    noun = "document" if kind == "word" else "workbook"
    plural = "documents" if kind == "word" else "workbooks"
    # Excel's remote 'first workbook whose ...' specifier raises -1728.
    # Resolve the exact-path filter to a local list, then use its object.
    # This is NOT workbook 1 (nor an active/name-only fallback).
    match = f"{plural} whose (full name is jobHFS or full name is jobPOSIX)"
    target = (f"(first {noun} whose (full name is jobHFS or full name is jobPOSIX))"
              if kind == "word" else f"(item 1 of (get {match}))")
    check = "my requireOwned(jobHFS, jobPOSIX)"
    if kind == "word":
        operations = f"""
                open (POSIX file jobPOSIX)
                set sessionStage to "updating"
                repeat 2 times
                    {check}
                    repaginate {target}
                    {check}
                    update field (fields of {target})
                end repeat
                {check}
                repaginate {target}
                set sessionStage to "saving"
                {check}
                save {target}
                set sessionStage to "exporting"
                {check}
                save as {target} file name (item 2 of argv) file format format PDF
                set sessionStage to "saving"
                {check}
                save {target}
"""
    return f"""{SESSION_MARKER}
on requireOwned(jobHFS, jobPOSIX)
    tell application "{app}"
        -- Count is a foreign-document veto, never the ownership proof.
        if (count of {plural}) is not 1 then error "foreign_or_missing_document" number -2700
        -- Force resolution to a list: Word rejects count on a filtered
        -- object specifier (-1708), but get + local list count is supported.
        if (count of (get {plural} whose (full name is jobHFS or full name is jobPOSIX))) is not 1 then error "owned_path_not_found" number -2700
        set verifiedPath to full name of {target}
        if verifiedPath is not jobHFS and verifiedPath is not jobPOSIX then error "owned_object_path_changed" number -2700
        if read only of {target} then error "owned_document_read_only" number -2700
    end tell
end requireOwned

on run argv
    set jobPOSIX to item 1 of argv
    set jobHFS to (POSIX file jobPOSIX) as text
    set sessionStage to "before_open"
    with timeout of {{timeout}} seconds
        tell application "{app}"
            try
                if (count of {plural}) is not 0 then error "existing_documents" number -2700
                {('if calculation is not calculation automatic then error "manual_calculation_not_supported" number -2700') if kind == 'excel' else ''}
                set sessionStage to "opening"
{operations}
                set sessionStage to "checking_saved"
                {check}
                if not (saved of {target}) then error "owned_document_dirty" number -2700
                set sessionStage to "closing"
                -- Ask protects changes racing the saved-state check.
                -- No automatic discard and no saving=yes on close.
                close {target} saving ask
                if (count of (get {plural} whose (full name is jobHFS or full name is jobPOSIX))) is not 0 then error "owned_document_still_open" number -2700
                return "GG_OFFICE_SESSION|closed|saved|" & version
            on error messageText number errorNumber
                -- Preserve documents on ALL errors, including open failure.
                error "GG_OFFICE_SESSION|" & sessionStage & "|" & messageText number errorNumber
            end try
        end tell
    end timeout
end run
"""


WORD_APPLESCRIPT_TEMPLATE = _template("word")
EXCEL_APPLESCRIPT_TEMPLATE = _template("excel")


def inventory_script(kind: str) -> str:
    app = "Microsoft Word" if kind == "word" else "Microsoft Excel"
    noun = "documents" if kind == "word" else "workbooks"
    return f'''if application "{app}" is running then
    tell application "{app}" to return "GG_OFFICE_INVENTORY|" & (count of {noun})
else
    return "GG_OFFICE_INVENTORY|0"
end if'''


def read_session(job_dir: Path) -> dict | None:
    path = Path(job_dir) / "office_session.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") == SCHEMA:
            return value
    except (OSError, ValueError, AttributeError):
        pass
    # An unreadable session journal cannot authorize deleting its staging.
    return {"schema": SCHEMA, "cleanup": "unknown",
            "reason": "session_evidence_unreadable", "app_quit": "not_attempted"}


def retain_job(job_dir: Path) -> bool:
    data = read_session(job_dir)
    return bool(data and data.get("cleanup") in {"preserved", "unknown"})


def run_session(kind: str, script: str, args: list[str], timeout: int, *,
                run_raw, prepare_word=None, guard_dir: Path | None = None):
    """Drive one freshly staged job under a per-user, per-app nonblocking lock.

    Test injection is limited to the raw transport, preparation, and lock
    directory. Production always uses one fixed guard directory independent
    of --workspace. Failed/unknown cleanup is never followed by compensation.
    """
    if kind == "excel":
        from gg_major_contract import OutputHeldError

        raise OutputHeldError(EXCEL_RECALCULATION_HOLD)
    import fcntl  # Only called by the darwin dispatcher.

    work, pdf = (Path(a).absolute() for a in args)
    job = work.parent.parent
    evidence = {"schema": SCHEMA, "app": kind, "job_dir": str(job),
                "staged_working_file": str(work), "cleanup": "not_attempted",
                "save_state": "unknown", "app_quit": "not_attempted",
                "app_quit_reason": "app_instance_ownership_not_proven"}

    def record(reason, cleanup, save_state="unknown"):
        evidence.update(reason=reason, cleanup=cleanup, save_state=save_state,
                        timestamp_ns=time.time_ns())
        (job / "office_session.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    def blocked(reason):
        record(reason, "not_attempted")
        result = subprocess.CompletedProcess(["osascript"], 2, "", reason)
        result.office_session = dict(evidence)
        return result

    if (kind not in {"word", "excel"} or work.parent.name != "output"
            or not job.name.startswith("job-") or pdf.parent != work.parent
            or not work.is_file() or work.is_symlink() or pdf.exists()):
        raise ValueError("Office automation requires a fresh staged job/output copy")
    guard_dir = guard_dir or (Path.home() / "Library" / "Group Containers"
                             / "UBF8T346G9.Office" / "ginseng-goat" / ".sessions")
    guard_dir.mkdir(parents=True, exist_ok=True)
    lock_path = guard_dir / (kind + ".lock")
    evidence["guard_path"] = str(lock_path)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return blocked("office_guard_not_regular")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            return blocked("office_session_busy")
        raw_state = os.read(fd, 65536)
        if raw_state:
            try:
                previous = json.loads(raw_state)
                if previous.get("state") != "idle":
                    return blocked("previous_office_session_unknown")
            except (ValueError, AttributeError):
                return blocked("office_guard_state_unreadable")

        def guard_state(state):
            raw = json.dumps({"state": state, "job_dir": str(job)},
                             ensure_ascii=False).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, raw)
            os.fsync(fd)

        guard_state("inflight")
        record("preflight", "unknown")
        try:
            preflight = run_raw(inventory_script(kind), [], timeout=min(timeout, 10))
            if (preflight.returncode != 0 or
                    preflight.stdout.strip() != "GG_OFFICE_INVENTORY|0"):
                guard_state("idle")
                return blocked("existing_or_unknown_office_documents")
            if kind == "word" and prepare_word is not None:
                prepare_word()
            record("running", "unknown")
            result = run_raw(script, args, timeout=timeout)
            output = (result.stdout or "").strip()
            if result.returncode == 0 and output.startswith("GG_OFFICE_SESSION|closed|saved|"):
                record("closed_confirmed", "closed", "saved")
                guard_state("idle")
            elif result.returncode != 0 and "GG_OFFICE_SESSION|" in (result.stderr or ""):
                detail = result.stderr.split("GG_OFFICE_SESSION|", 1)[1]
                stage = detail.split("|", 1)[0]
                if stage == "closing" or "-1712" in result.stderr:
                    record("close_or_event_result_unknown", "unknown")
                    guard_state("unknown")
                else:
                    record("engine_failed:" + stage, "preserved")
                    guard_state("idle")
            else:
                record("engine_result_unconfirmed", "unknown")
                guard_state("unknown")
                if result.returncode == 0:
                    result = subprocess.CompletedProcess(result.args, 2, result.stdout,
                                                         "Office close/save evidence missing")
            result.office_session = dict(evidence)
            return result
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            record("transport_timeout", "unknown")
            guard_state("unknown")
            error = TimeoutError(f"Office session timed out ({timeout}s); documents preserved")
            error.office_session = dict(evidence)
            raise error from exc
        except Exception:
            record("transport_or_evidence_failure", "unknown")
            guard_state("unknown")
            raise
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
