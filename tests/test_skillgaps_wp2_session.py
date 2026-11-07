"""WP-2 Office 세션 결함 잠금 시험 — G3-09(state=unknown 복구 명령).

가짜 transport와 실제 파일시스템 잠금만 쓴다. Office 앱을 실행하지 않는다.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests._harness import (
    ContractCase, runtime, script_source, text_calls_without_encoding,
)

@unittest.skipIf(
    sys.platform == "win32",
    "gg_office_session uses fcntl/AppleScript; Windows uses gg_office_win "
    "without a session guard. These tests use a mocked transport.",
)
class OfficeRecoveryTests(ContractCase):
    """G3-09: state=unknown이 남으면 복구 명령이 없어 학생이 막혔다."""

    def setUp(self):
        super().setUp()
        self.session = runtime("gg_office_session")
        self.tmp = tempfile.TemporaryDirectory(prefix="wp2-guard-")
        self.addCleanup(self.tmp.cleanup)
        self.guard = Path(self.tmp.name) / "guards"
        self.guard.mkdir()
        self.job = Path(self.tmp.name) / "job-first"
        (self.job / "output").mkdir(parents=True)
        self.work = self.job / "output" / "same-name.docx"
        self.work.write_bytes(b"staged")
        self.pdf = self.job / "output" / "same-name.pdf"

    def _blocked(self, state):
        (self.guard / "word.lock").write_text(state, encoding="utf-8")
        raw = mock.Mock()
        result = self.session.run_session(
            "word", self.session.WORD_APPLESCRIPT_TEMPLATE.format(timeout=5),
            [str(self.work), str(self.pdf)], 5, run_raw=raw,
            guard_dir=self.guard)
        raw.assert_not_called()
        return result

    def test_blocked_message_carries_runnable_recovery_command(self):
        result = self._blocked('{"state":"unknown"}')
        self.assertEqual(2, result.returncode)
        self.assertIn("previous_office_session_unknown", result.stderr)
        command = self.session.recovery_command("word", self.guard)
        self.assertIn(command, result.stderr)
        self.assertEqual(command, result.office_session["recovery"])
        self.assertIn("--recover word", command)
        self.assertIn("--guard-dir", command)

    def test_blocked_receipt_on_disk_carries_recovery_command(self):
        result = self._blocked('{"state":"unknown"}')
        command = self.session.recovery_command("word", self.guard)
        receipt = json.loads(
            (self.job / "office_session.json").read_text(encoding="utf-8"))
        self.assertEqual(command, result.office_session["recovery"])
        self.assertEqual(command, receipt["recovery"])
        self.assertEqual("previous_office_session_unknown", receipt["reason"])

    def test_non_object_guard_json_is_unreadable_and_recoverable(self):
        for state in ("[]", "null", '"text"', "42"):
            with self.subTest(state=state):
                result = self._blocked(state)
                self.assertEqual(2, result.returncode)
                self.assertIn("office_guard_state_unreadable", result.stderr)
                command = self.session.recovery_command("word", self.guard)
                self.assertIn(command, result.stderr)
                receipt = json.loads(
                    (self.job / "office_session.json").read_text(encoding="utf-8"))
                self.assertEqual(command, receipt["recovery"])

                value = self.session.recover_session("word", guard_dir=self.guard)
                self.assertEqual("recovered", value["status"])
                self.assertEqual("unreadable", value["previous_state"])
                self.assertEqual(
                    {"state": "idle", "job_dir": None},
                    json.loads((self.guard / "word.lock").read_text(encoding="utf-8")))

                retry = self.session.run_session(
                    "word", self.session.WORD_APPLESCRIPT_TEMPLATE.format(timeout=5),
                    [str(self.work), str(self.pdf)], 5,
                    run_raw=mock.Mock(side_effect=[
                        subprocess.CompletedProcess(
                            ["osascript"], 0, "GG_OFFICE_INVENTORY|0", ""),
                        subprocess.CompletedProcess(
                            ["osascript"], 0,
                            "GG_OFFICE_SESSION|closed|saved|synthetic", "")]),
                    guard_dir=self.guard)
                self.assertEqual(0, retry.returncode)
                self.assertEqual("closed", retry.office_session["cleanup"])

    def test_cli_recovers_non_object_guard_as_json(self):
        for state in ("[]", "null"):
            with self.subTest(state=state):
                (self.guard / "word.lock").write_text(state, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-B", str(Path(self.session.__file__)),
                     "--recover", "word", "--guard-dir", str(self.guard)],
                    capture_output=True, text=True)
                self.assertEqual(0, result.returncode,
                                 result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual("recovered", payload["status"])
                self.assertEqual("unreadable", payload["previous_state"])

    def test_recovery_clears_record_without_touching_job_files(self):
        (self.guard / "word.lock").write_text('{"state":"unknown"}', encoding="utf-8")
        before = {str(p): p.read_bytes() for p in self.job.rglob("*") if p.is_file()}
        value = self.session.recover_session("word", guard_dir=self.guard)
        self.assertEqual("recovered", value["status"])
        self.assertEqual("unknown", value["previous_state"])
        self.assertEqual({"state": "idle", "job_dir": None},
                         json.loads((self.guard / "word.lock").read_text(encoding="utf-8")))
        after = {str(p): p.read_bytes() for p in self.job.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        audit = (self.guard / self.session.AUDIT_NAME).read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(audit))
        self.assertEqual("unknown", json.loads(audit[0])["recovered_from"])

        retry = self.session.run_session(
            "word", self.session.WORD_APPLESCRIPT_TEMPLATE.format(timeout=5),
            [str(self.work), str(self.pdf)], 5,
            run_raw=mock.Mock(side_effect=[
                subprocess.CompletedProcess(["osascript"], 0, "GG_OFFICE_INVENTORY|0", ""),
                subprocess.CompletedProcess(
                    ["osascript"], 0, "GG_OFFICE_SESSION|closed|saved|synthetic", "")]),
            guard_dir=self.guard)
        self.assertEqual(0, retry.returncode)
        self.assertEqual("closed", retry.office_session["cleanup"])

    def test_recovery_refuses_while_a_session_holds_the_lock(self):
        import fcntl
        fd = os.open(self.guard / "word.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        os.write(fd, b'{"state":"unknown"}')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        value = self.session.recover_session("word", guard_dir=self.guard)
        self.assertEqual("busy", value["status"])
        os.lseek(fd, 0, os.SEEK_SET)
        self.assertIn(b"unknown", os.read(fd, 65536))
        self.assertFalse((self.guard / self.session.AUDIT_NAME).exists())

    def test_cli_recovery_is_json_and_exit_zero(self):
        (self.guard / "word.lock").write_text('{"state":"inflight"}', encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(Path(self.session.__file__)),
             "--recover", "word", "--guard-dir", str(self.guard)],
            capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("recovered", payload["status"])
        self.assertEqual("inflight", payload["previous_state"])
        self.assertIn("그대로", payload["note"])


class ExcelCalculationModeTests(unittest.TestCase):
    """Execute the generated run body's Excel operations against a tiny model.

    No Office dependency: zero books reports missing, opening exposes the
    workbook's mode. Native AppleScript execution is recorded separately.
    """

    def execute(self, mode, existing=0, owned=True):
        script = runtime("gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        body = script.split("on run argv", 1)[1].split("on error messageText", 1)[0]
        books = existing
        events = []
        for raw in body.splitlines():
            line = raw.strip()
            if line.startswith('if (count of workbooks) is not 0'):
                if books:
                    return "existing_documents", events, books
            elif line.startswith("open workbook "):
                books += 1
                events.append("open")
            elif line == "my requireOwned(jobHFS, jobPOSIX)":
                if books != 1 or not owned:
                    return "owned_path_not_found", events, books
                events.append("owned")
            elif line.startswith("if calculation is not calculation automatic"):
                current = mode if books else None
                events.append(("mode", current))
                if current != "automatic":
                    return "manual_calculation_not_supported", events, books
            elif line == "calculate":
                events.append("calculate")
            elif line.startswith("save "):
                events.append("save")
            elif line.startswith("close "):
                books -= 1
                events.append("close")
        return "closed", events, books

    def test_zero_books_missing_mode_does_not_block_automatic_copy(self):
        result, events, remaining = self.execute("automatic")
        self.assertEqual("closed", result)
        self.assertEqual(["open", "owned", ("mode", "automatic"), "owned",
                          "calculate"], events[:5])
        self.assertEqual(0, remaining)

    def test_manual_or_missing_mode_preserves_open_copy_without_writes(self):
        for mode in ("manual", None):
            with self.subTest(mode=mode):
                result, events, remaining = self.execute(mode)
                self.assertEqual("manual_calculation_not_supported", result)
                self.assertEqual(["open", "owned", ("mode", mode)], events)
                self.assertEqual(1, remaining)

    def test_foreign_books_are_not_opened_or_read_for_mode(self):
        self.assertEqual(("existing_documents", [], 1),
                         self.execute("automatic", existing=1))
        self.assertEqual(("owned_path_not_found", ["open"], 1),
                         self.execute("automatic", owned=False))


class ConventionTests(unittest.TestCase):
    def test_text_io_and_execution_guards(self):
        source = script_source("gg_office_session.py")
        self.assertEqual([], text_calls_without_encoding(source))
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)

    def test_recovery_never_closes_or_deletes(self):
        source = script_source("gg_office_session.py")
        body = source.split("def recover_session", 1)[1].split("def _append_audit", 1)[0]
        for forbidden in ("unlink", "rmtree", "quit", "saving no", "osascript"):
            self.assertNotIn(forbidden, body)

if __name__ == "__main__":
    unittest.main()
