"""Small macOS Office job guard; no app quit or timeout recovery commands.

The lock coordinates this CLI, not the user's UI. An interrupted AppleEvent
may still run in Office, so an in-flight/unknown record blocks subsequent
CLI sessions until a human reconciles it. Never infer ownership from focus.

A blocked session reports the exact command that clears the record
(``gg_office_session.py --recover word``). That command never closes,
quits or deletes anything: it only rewrites the guard record after a human
has confirmed that Office no longer holds the staged document, and it
refuses while another session still holds the lock.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

SESSION_MARKER = "-- gg-office-owned-session-v1"
SCHEMA = "gg-office-session/v1"
AUDIT_NAME = "recovery-audit.jsonl"


def _template(kind: str) -> str:
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
    else:
        operations = f"""
                open workbook workbook file name jobHFS
                set sessionStage to "updating"
                {check}
                calculate
                set sessionStage to "saving"
                {check}
                save {target}
                set sessionStage to "exporting"
                {check}
                save as worksheet 1 of {target} filename (item 2 of argv) file format PDF file format
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


def default_guard_dir() -> Path:
    return (Path.home() / "Library" / "Group Containers"
            / "UBF8T346G9.Office" / "ginseng-goat" / ".sessions")

def recovery_command(kind: str, guard_dir: Path | None = None) -> str:
    """사람이 그대로 칠 수 있는 복구 명령. 가드 경로가 기본값이면 생략한다."""
    parts = [sys.executable, str(Path(__file__).resolve()), "--recover", kind]
    if guard_dir is not None and Path(guard_dir) != default_guard_dir():
        parts += ["--guard-dir", str(guard_dir)]
    return " ".join("'" + part + "'" if " " in part else part for part in parts)

def guard_record(raw: bytes) -> dict:
    """가드 파일 내용을 dict로 읽는다.

    깨진 JSON뿐 아니라 ``[]``·``null``·문자열·숫자처럼 dict가 아닌 유효
    JSON도 같은 '읽을 수 없음'으로 다룬다. 두 호출부(run_session·복구)가
    같은 판정을 쓰도록 한 곳에 둔다.
    """
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return {"state": "unreadable",
                "raw": raw[:200].decode("utf-8", "replace")}
    return value

def recover_session(kind: str, *, guard_dir: Path | None = None) -> dict:
    """unknown/inflight 가드 기록을 사람 확인 뒤 idle로 되돌린다.

    Office 문서를 닫거나 종료하지 않고, 잡 폴더도 지우지 않는다. 세션이
    실제로 잠금을 잡고 있으면 아무것도 바꾸지 않고 busy를 돌려준다.
    """
    import fcntl

    if kind not in {"word", "excel"}:
        raise ValueError("kind는 word 또는 excel이어야 합니다: " + str(kind))
    guard_dir = Path(guard_dir) if guard_dir is not None else default_guard_dir()
    lock_path = guard_dir / (kind + ".lock")
    if not lock_path.exists():
        return {"status": "idle", "kind": kind, "guard_path": str(lock_path),
                "note": "가드 기록이 없습니다. 복구할 것이 없습니다."}
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("가드 파일이 일반 파일이 아님: " + str(lock_path))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "kind": kind, "guard_path": str(lock_path),
                    "note": "다른 Office 세션이 아직 실행 중입니다. 끝난 뒤 다시 실행하세요."}
        previous = guard_record(os.read(fd, 65536))
        if previous.get("state") == "idle":
            return {"status": "idle", "kind": kind, "guard_path": str(lock_path),
                    "note": "이미 idle입니다."}
        record = {"state": "idle", "recovered_from": previous.get("state"),
                  "job_dir": previous.get("job_dir"), "kind": kind,
                  "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        data = json.dumps({"state": "idle", "job_dir": previous.get("job_dir")},
                          ensure_ascii=False).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)
        os.fsync(fd)
        _append_audit(guard_dir, record)
        return {"status": "recovered", "kind": kind, "guard_path": str(lock_path),
                "previous_state": record["recovered_from"],
                "job_dir": record["job_dir"],
                "audit": str(guard_dir / AUDIT_NAME),
                "note": ("가드 기록만 idle로 바꿨습니다. Office 문서·잡 폴더·"
                         "산출물은 그대로입니다. 남은 문서가 있으면 Word에서 "
                         "직접 닫으세요.")}
    finally:
        os.close(fd)

def _append_audit(guard_dir: Path, record: dict) -> None:
    path = guard_dir / AUDIT_NAME
    line = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Office 세션 가드 복구(문서 닫기·앱 종료·파일 삭제 없음)")
    parser.add_argument("--recover", choices=("word", "excel"), required=True,
                        help="unknown/inflight 가드 기록을 idle로 되돌린다")
    parser.add_argument("--guard-dir", help="기본 가드 폴더 대신 쓸 경로")
    args = parser.parse_args(argv)
    try:
        value = recover_session(args.recover, guard_dir=args.guard_dir)
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value["status"] in {"recovered", "idle"} else 2

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

    def blocked(reason, recovery=None):
        # The command must land in the on-disk receipt, not only in the
        # returned object: a later flow that reads office_session.json
        # alone still has to find it.
        text = reason
        if recovery:
            evidence["recovery"] = recovery
            text = (reason + "\n복구: " + recovery
                    + "\nOffice에 이 작업의 문서가 남아 있지 않은지 확인한 뒤 실행하세요. "
                      "이 명령은 문서를 닫거나 앱을 종료하거나 파일을 지우지 않습니다.")
        record(reason, "not_attempted")
        result = subprocess.CompletedProcess(["osascript"], 2, "", text)
        result.office_session = dict(evidence)
        return result

    if (kind not in {"word", "excel"} or work.parent.name != "output"
            or not job.name.startswith("job-") or pdf.parent != work.parent
            or not work.is_file() or work.is_symlink() or pdf.exists()):
        raise ValueError("Office automation requires a fresh staged job/output copy")
    guard_dir = guard_dir or default_guard_dir()
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
            recovery = recovery_command(kind, guard_dir)
            previous = guard_record(raw_state)
            if previous.get("state") == "unreadable":
                return blocked("office_guard_state_unreadable", recovery)
            if previous.get("state") != "idle":
                return blocked("previous_office_session_unknown", recovery)

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
            inventory = (preflight.stdout or "").strip()
            if preflight.returncode != 0 or inventory != "GG_OFFICE_INVENTORY|0":
                guard_state("idle")
                count = inventory.removeprefix("GG_OFFICE_INVENTORY|")
                reason = ("existing_documents" if preflight.returncode == 0
                          and count.isdecimal() and int(count) > 0
                          else "office_inventory_unknown")
                return blocked(reason)
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
                    message = detail.split("|", 1)[1] if "|" in detail else ""
                    reason = ("existing_documents" if stage == "before_open"
                              and message.startswith("existing_documents")
                              else "engine_failed:" + stage)
                    record(reason, "not_attempted" if stage == "before_open" else "preserved")
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

if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
