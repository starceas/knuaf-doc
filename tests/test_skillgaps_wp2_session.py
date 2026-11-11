"""WP-2 Office 세션 결함 잠금 시험 — G3-09(state=unknown 복구 명령).

가짜 transport와 실제 파일시스템 잠금만 쓴다. Office 앱을 실행하지 않는다.
"""

import json
import os
from pathlib import Path
import re
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


class _ScriptError(Exception):
    def __init__(self, message, number=-2700):
        super().__init__(message)
        self.message = message
        self.number = number


class _Unmodeled(Exception):
    """The script used a statement this model cannot check.

    Fail closed: an unrecognized statement must fail the test instead of
    being skipped, or a new side effect could pass the suite unnoticed.
    """


class _ExcelRun:
    """Run the generated Excel `on run argv` body, including `on error`.

    Models only the statements this template uses; anything else raises
    _Unmodeled. Excel itself is not started.
    """

    def __init__(self, template, mode, *, existing=0, owned=True,
                 read_only=False):
        self.mode = mode
        self.books = existing
        self.owned = owned
        self.read_only = read_only
        self.saved = True
        self.stage = None
        self.events = []
        self.error_stage = None
        self.error_message = None
        self.returned = None
        self._active_error = None
        main, handler = template.split("on run argv", 1)[1].split(
            "on error messageText number errorNumber", 1)
        self._main = main.splitlines()
        self._handler = handler.split("end try", 1)[0].splitlines()

    def run(self):
        try:
            self._run_lines(self._main)
        except _ScriptError as error:
            self.error_stage = self.stage
            self._run_handler(error)
        return self

    def _run_lines(self, lines):
        for raw in lines:
            line = raw.strip()
            if self._structural(line):
                continue
            if self._statement(line):
                break

    def _run_handler(self, error):
        self._active_error = error
        for raw in self._handler:
            line = raw.strip()
            if not line or line.startswith("--") or line == "end try":
                continue
            try:
                if self._statement(line):
                    break
            except _ScriptError as raised:
                self.error_message = self._message_of(raised.message)
                self.events.append(
                    ("error", self.error_stage, self.error_message))
                return

    @staticmethod
    def _message_of(envelope):
        prefix = "GG_OFFICE_SESSION|"
        if envelope.startswith(prefix):
            return envelope.split("|", 2)[2]
        return envelope

    @staticmethod
    def _structural(line):
        return (not line or line.startswith("--")
                or line in {"try", "end try", "end tell", "end timeout",
                            "end run", 'tell application "Microsoft Excel"'}
                or line.startswith("with timeout of ")
                or line.startswith("set jobPOSIX to ")
                or line.startswith("set jobHFS to "))

    def _owned_count(self):
        return 1 if self.books == 1 and self.owned else 0

    def _condition(self, text):
        text = " ".join(text.split())
        if text == "calculation is not calculation automatic":
            # Excel reports missing value when no workbook is open.
            current = self.mode if self.books else None
            self.events.append(("mode", current))
            return current != "automatic"
        if text == "(count of workbooks) is not 0":
            return self.books != 0
        if text == ("(count of (get workbooks whose "
                    "(full name is jobHFS or full name is jobPOSIX))) is not 0"):
            return self._owned_count() != 0
        if text == ("not (saved of "
                    "(item 1 of (get workbooks whose "
                    "(full name is jobHFS or full name is jobPOSIX))))"):
            return not self.saved
        raise _Unmodeled("condition: " + text)

    def _value(self, expression):
        parts = [part.strip() for part in expression.split(" & ")]
        values = [self._atom(part) for part in parts]
        if len(values) == 1:
            return values[0]
        return "".join(str(value) for value in values)

    def _atom(self, text):
        if text.startswith("(") and text.endswith(")"):
            return self._atom(text[1:-1].strip())
        if text.startswith('"') and text.endswith('"'):
            return text[1:-1]
        if text == "version":
            return "16.114.1"
        if text in {"sessionStage", "messageText", "errorNumber"}:
            if self._active_error is None:
                raise _Unmodeled("value outside error handler: " + text)
            return {"sessionStage": self.error_stage,
                    "messageText": self._active_error.message,
                    "errorNumber": self._active_error.number}[text]
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        raise _Unmodeled("value: " + text)

    def _require_owned(self):
        if self.books != 1:
            raise _ScriptError("foreign_or_missing_document")
        if self._owned_count() != 1:
            raise _ScriptError("owned_path_not_found")
        if self.read_only:
            raise _ScriptError("owned_document_read_only")
        self.events.append("owned")

    def _statement(self, line):
        if line.startswith("if ") and " then " in line:
            condition, action = line[3:].split(" then ", 1)
            if self._condition(condition):
                return self._statement(action.strip())
            return False
        if line == "open workbook workbook file name jobHFS":
            self.books += 1
            self.saved = True
            self.events.append("open")
            return False
        if line == "my requireOwned(jobHFS, jobPOSIX)":
            self._require_owned()
            return False
        if line == "calculate":
            self.events.append(("calculate", self.stage))
            self.saved = False
            return False
        if line.startswith("set calculation to "):
            mode = line[len("set calculation to "):].replace("calculation ", "")
            self.mode = mode
            self.events.append(("set_calculation", mode))
            return False
        if line.startswith("set sessionStage to "):
            self.stage = self._value(line[len("set sessionStage to "):])
            return False
        if line.startswith("save as "):
            self.events.append(("save_as_pdf", self.stage))
            self.saved = True
            return False
        if line.startswith("save "):
            self.events.append(("save", self.stage))
            self.saved = True
            return False
        if line.startswith("close every workbook saving no"):
            self.events.append(("close", "no"))
            self.books = 0
            self.saved = True
            return False
        if line.startswith("close ") and line.endswith("saving ask"):
            self.events.append(("close", "ask"))
            self.books -= 1
            self.saved = True
            return False
        if line.startswith("close ") and line.endswith("saving no"):
            self.events.append(("close", "no"))
            self.books -= 1
            self.saved = True
            return False
        if line == "quit":
            self.events.append(("quit",))
            self.books = 0
            return False
        if line.startswith("return "):
            self.returned = self._value(line[len("return "):])
            self.events.append(("return", self.returned))
            return True
        if line.startswith("error "):
            match = re.fullmatch(r"error (.+?) number (.+)", line)
            if not match:
                raise _Unmodeled(line)
            raise _ScriptError(self._value(match.group(1)),
                               int(self._value(match.group(2))))
        raise _Unmodeled(line)


class ExcelCalculationModeTests(unittest.TestCase):
    """Execute the generated Excel body, including its error handler.

    The model fails closed on unmodeled statements, so adding a calculation
    assignment, weakening the mode check, or saving/closing/quitting from
    `on error` cannot pass silently. Excel itself is not started.
    """

    def execute(self, mode, template=None, **kwargs):
        template = template or runtime(
            "gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        return _ExcelRun(template, mode, **kwargs).run()

    def manual_contract_violations(self, template):
        """Return the observable breaches of the manual/missing-mode contract."""
        run = self.execute("manual", template=template)
        expected = ["open", "owned", ("mode", "manual"),
                    ("error", "updating", "manual_calculation_not_supported")]
        violations = []
        if run.error_message != "manual_calculation_not_supported":
            violations.append(("error_message", run.error_message))
        if run.error_stage != "updating":
            violations.append(("error_stage", run.error_stage))
        if run.books != 1:
            violations.append(("open_books", run.books))
        if run.events != expected:
            violations.append(("events", run.events))
        if run.returned is not None:
            violations.append(("returned", run.returned))
        return violations

    def test_automatic_mode_calculates_one_owned_copy_and_closes_it(self):
        run = self.execute("automatic")
        self.assertEqual(0, run.books)
        self.assertEqual("GG_OFFICE_SESSION|closed|saved|16.114.1", run.returned)
        self.assertEqual([
            "open", "owned", ("mode", "automatic"), "owned",
            ("calculate", "updating"), "owned", ("save", "saving"), "owned",
            ("save_as_pdf", "exporting"), "owned", ("close", "ask"),
            ("return", "GG_OFFICE_SESSION|closed|saved|16.114.1"),
        ], run.events)

    def test_manual_or_missing_mode_preserves_open_copy_without_writes(self):
        for mode in ("manual", None):
            with self.subTest(mode=mode):
                run = self.execute(mode)
                self.assertEqual("manual_calculation_not_supported",
                                 run.error_message)
                self.assertEqual("updating", run.error_stage)
                self.assertEqual(1, run.books)
                self.assertEqual(["open", "owned", ("mode", mode),
                                  ("error", "updating",
                                   "manual_calculation_not_supported")],
                                 run.events)

    def test_foreign_or_unowned_documents_are_rejected_before_mode_read(self):
        existing = self.execute("automatic", existing=1)
        self.assertEqual(("before_open", "existing_documents"),
                         (existing.error_stage, existing.error_message))
        self.assertEqual([("error", "before_open", "existing_documents")],
                         existing.events)
        self.assertEqual(1, existing.books)

        unowned = self.execute("automatic", owned=False)
        self.assertEqual(("updating", "owned_path_not_found"),
                         (unowned.error_stage, unowned.error_message))
        self.assertEqual(["open", ("error", "updating",
                                   "owned_path_not_found")], unowned.events)
        self.assertEqual(1, unowned.books)

        read_only = self.execute("automatic", read_only=True)
        self.assertEqual(("updating", "owned_document_read_only"),
                         (read_only.error_stage, read_only.error_message))
        self.assertEqual(["open", ("error", "updating",
                                   "owned_document_read_only")],
                         read_only.events)

    def test_contract_rejects_calculation_mode_assignment(self):
        template = runtime("gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        mutated = template.replace(
            "                my requireOwned(jobHFS, jobPOSIX)\n"
            "                -- Excel reports",
            "                my requireOwned(jobHFS, jobPOSIX)\n"
            "                set calculation to calculation automatic\n"
            "                -- Excel reports", 1)
        self.assertNotEqual(template, mutated)
        run = self.execute("manual", template=mutated)
        self.assertIn(("set_calculation", "automatic"), run.events)
        self.assertIsNone(run.error_message)
        self.assertEqual(0, run.books)
        self.assertTrue(self.manual_contract_violations(mutated))

    def test_contract_rejects_mode_check_that_stops_raising(self):
        template = runtime("gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        mutated = template.replace(
            'if calculation is not calculation automatic then error '
            '"manual_calculation_not_supported" number -2700',
            'if calculation is not calculation automatic then '
            'return "mode-check-skipped"', 1)
        self.assertNotEqual(template, mutated)
        run = self.execute("manual", template=mutated)
        self.assertEqual("mode-check-skipped", run.returned)
        self.assertIsNone(run.error_message)
        self.assertTrue(self.manual_contract_violations(mutated))

    def test_contract_rejects_mode_check_before_the_owned_copy_opens(self):
        # With no workbook open Excel reports missing value, so reading the
        # mode before opening would refuse every automatic copy.
        check = ('if calculation is not calculation automatic then error '
                 '"manual_calculation_not_supported" number -2700')
        template = runtime("gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        mutated = template.replace(check + "\n", "", 1).replace(
            "open workbook workbook file name jobHFS",
            check + "\n                open workbook workbook file name jobHFS",
            1)
        self.assertNotEqual(template, mutated)
        run = self.execute("automatic", template=mutated)
        self.assertEqual(("opening", "manual_calculation_not_supported"),
                         (run.error_stage, run.error_message))
        self.assertEqual([("mode", None),
                          ("error", "opening",
                           "manual_calculation_not_supported")], run.events)

    def test_contract_rejects_error_handler_that_closes_or_quits(self):
        template = runtime("gg_office_session").EXCEL_APPLESCRIPT_TEMPLATE
        failure = ('error "GG_OFFICE_SESSION|" & sessionStage & "|" '
                   '& messageText number errorNumber')
        for line in ("close every workbook saving no", "quit"):
            with self.subTest(line=line):
                mutated = template.replace(failure, line, 1)
                self.assertNotEqual(template, mutated)
                run = self.execute("manual", template=mutated)
                self.assertEqual(0, run.books)
                self.assertIn("close" if "close" in line else "quit",
                              [event[0] if isinstance(event, tuple) else event
                               for event in run.events])
                self.assertTrue(self.manual_contract_violations(mutated))


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
