"""Office preservation boundaries: fake transport, real filesystem/lock.

No Office launch or user files. Script checks cover targeting syntax; native
AppleScript semantics and saved output caches require separate app evidence.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests._harness import runtime

try:
    import fcntl
except ModuleNotFoundError as exc:
    if exc.name != "fcntl":
        raise
    fcntl = None


@unittest.skipUnless(fcntl is not None, "requires native POSIX fcntl locks; Office automation is not exercised")
class OfficeSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = runtime("gg_office_session")
        self.office = runtime("gg_office")
        self.tmp = tempfile.TemporaryDirectory(prefix="office-hygiene-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.guard = self.root / "guards"
        self.work, self.pdf = self.job("first")

    def job(self, name):
        folder = self.root / ("job-" + name) / "output"
        folder.mkdir(parents=True)
        work = folder / "same-name.docx"
        work.write_bytes(b"synthetic staged file")
        return work, folder / "same-name.pdf"

    def result(self, stdout="GG_OFFICE_SESSION|closed|saved|synthetic", *,
               rc=0, stderr=""):
        return subprocess.CompletedProcess(["synthetic"], rc, stdout, stderr)

    def run_job(self, raw, *, kind="word", prepare=None, work=None, pdf=None):
        script = (self.session.WORD_APPLESCRIPT_TEMPLATE if kind == "word"
                  else self.session.EXCEL_APPLESCRIPT_TEMPLATE).format(timeout=9)
        return self.session.run_session(
            kind, script, [str(work or self.work), str(pdf or self.pdf)], 9,
            run_raw=raw, prepare_word=prepare, guard_dir=self.guard)

    def transport(self, outcome=None):
        return mock.Mock(side_effect=[
            self.result("GG_OFFICE_INVENTORY|0"), outcome or self.result()])

    def assert_no_compensation(self, raw):
        for call in raw.call_args_list:
            script = call.args[0]
            self.assertNotIn("saving no", script)
            self.assertNotIn("quit", script)
        self.assertLessEqual(raw.call_count, 2)

    def test_clean_and_dirty_existing_documents_block_before_launch_or_open(self):
        for dirty in (False, True):
            with self.subTest(dirty=dirty):
                user = {"path": "/user/same-name.docx", "dirty": dirty,
                        "content": "unsaved user work", "open": True}
                before = copy.deepcopy(user)
                raw = mock.Mock(return_value=self.result("GG_OFFICE_INVENTORY|1"))
                prepare = mock.Mock()
                res = self.run_job(raw, prepare=prepare)
                self.assertEqual(res.returncode, 2)
                self.assertEqual(user, before)
                prepare.assert_not_called()
                self.assertEqual(raw.call_count, 1)
                self.assertEqual(res.office_session["cleanup"], "not_attempted")

    def test_unknown_inventory_blocks(self):
        for response in (self.result(""), self.result("bad", rc=1),
                         self.result("GG_OFFICE_INVENTORY|2")):
            with self.subTest(response=response):
                raw = mock.Mock(return_value=response)
                self.assertEqual(self.run_job(raw).returncode, 2)
                self.assertEqual(raw.call_count, 1)

    def test_open_update_save_export_dirty_and_foreign_errors_preserve_job(self):
        cases = [("opening", "open_failed"), ("updating", "field_error"),
                 ("saving", "save_failed"), ("exporting", "pdf_failed"),
                 ("checking_saved", "owned_document_dirty"),
                 ("updating", "foreign_or_missing_document"),
                 ("exporting", "owned_path_not_found")]
        for stage, reason in cases:
            with self.subTest(stage=stage, reason=reason):
                raw = self.transport(self.result(rc=1, stdout="", stderr=
                    "execution error: GG_OFFICE_SESSION|" + stage + "|" + reason))
                res = self.run_job(raw)
                self.assertEqual(res.returncode, 1)
                self.assertEqual(res.office_session["cleanup"], "preserved")
                self.assertTrue(self.work.exists())
                self.assertTrue(self.session.retain_job(self.work.parent.parent))
                self.assert_no_compensation(raw)

    def test_close_error_is_unknown_and_blocks_next_workspace(self):
        raw = self.transport(self.result(rc=1, stdout="", stderr=
            "GG_OFFICE_SESSION|closing|owned_document_still_open"))
        res = self.run_job(raw)
        self.assertEqual(res.office_session["cleanup"], "unknown")
        second_work, second_pdf = self.job("second")
        next_raw = mock.Mock()
        second = self.run_job(next_raw, work=second_work, pdf=second_pdf)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.office_session["reason"], "previous_office_session_unknown")
        next_raw.assert_not_called()
        self.assertTrue(self.work.exists())

    def test_timeout_does_not_cleanup_or_allow_next_job(self):
        for when in ("preflight", "engine"):
            with self.subTest(when=when):
                # Separate guard files for independent timeout scenarios.
                self.guard = self.root / ("guards-" + when)
                replies = [TimeoutError("synthetic hung read")] if when == "preflight" else [
                    self.result("GG_OFFICE_INVENTORY|0"), TimeoutError("synthetic hung event")]
                raw = mock.Mock(side_effect=replies)
                with self.assertRaises(TimeoutError) as caught:
                    self.run_job(raw)
                self.assertEqual(caught.exception.office_session["cleanup"], "unknown")
                self.assertTrue(self.work.exists())
                self.assert_no_compensation(raw)
                next_raw = mock.Mock()
                self.assertEqual(self.run_job(next_raw).returncode, 2)
                next_raw.assert_not_called()

    def test_appleevent_timeout_is_unknown_even_when_script_returned(self):
        raw = self.transport(self.result(rc=1, stdout="", stderr=
            "GG_OFFICE_SESSION|opening|AppleEvent timed out (-1712)"))
        self.assertEqual(self.run_job(raw).office_session["cleanup"], "unknown")
        self.assert_no_compensation(raw)

    def test_prepare_failure_records_unknown_and_preserves(self):
        raw = mock.Mock(return_value=self.result("GG_OFFICE_INVENTORY|0"))
        prepare = mock.Mock(side_effect=RuntimeError("launch failure"))
        with self.assertRaisesRegex(RuntimeError, "launch failure"):
            self.run_job(raw, prepare=prepare)
        data = self.session.read_session(self.work.parent.parent)
        self.assertEqual(data["cleanup"], "unknown")
        self.assertEqual(raw.call_count, 1)
        self.assertTrue(self.work.exists())

    def test_unconfirmed_zero_exit_is_not_success(self):
        raw = self.transport(self.result("Word process completed"))
        res = self.run_job(raw)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.office_session["cleanup"], "unknown")
        self.assert_no_compensation(raw)

    def test_success_records_document_close_but_no_app_quit(self):
        raw = self.transport()
        res = self.run_job(raw)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.office_session["cleanup"], "closed")
        self.assertEqual(res.office_session["save_state"], "saved")
        self.assertEqual(res.office_session["app_quit"], "not_attempted")
        self.assertEqual(self.session.read_session(self.work.parent.parent), res.office_session)
        self.assert_no_compensation(raw)

    def test_other_cli_cannot_enter_same_app(self):
        import fcntl
        self.guard.mkdir()
        lock = self.guard / "word.lock"
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = mock.Mock()
        res = self.run_job(raw)
        self.assertEqual(res.office_session["reason"], "office_session_busy")
        raw.assert_not_called()

    def test_app_locks_are_independent(self):
        import fcntl
        self.guard.mkdir()
        fd = os.open(self.guard / "word.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = self.transport()
        self.assertEqual(self.run_job(raw, kind="excel").returncode, 0)
        self.assertEqual(raw.call_count, 2)

    def test_inflight_crash_and_malformed_lock_fail_closed(self):
        self.guard.mkdir()
        for content in ('{"state":"inflight"}', '{"state":"unknown"}', '{'):
            with self.subTest(content=content):
                (self.guard / "word.lock").write_text(content, encoding="utf-8")
                raw = mock.Mock()
                self.assertEqual(self.run_job(raw).returncode, 2)
                raw.assert_not_called()

    def test_templates_use_path_for_every_write_and_no_error_cleanup(self):
        for kind, template in [("word", self.session.WORD_APPLESCRIPT_TEMPLATE),
                               ("excel", self.session.EXCEL_APPLESCRIPT_TEMPLATE)]:
            with self.subTest(kind=kind):
                text = template.format(timeout=9)
                self.assertNotIn("saving no", text)
                self.assertNotIn("quit", text)
                self.assertNotIn("active document", text)
                self.assertNotIn("active workbook", text)
                self.assertNotIn("document 1", text)
                noun = "document" if kind == "word" else "workbook"
                target = (f"first {noun} whose (full name is jobHFS or full name is jobPOSIX)"
                          if kind == "word" else
                          "item 1 of (get workbooks whose (full name is jobHFS or full name is jobPOSIX))")
                writes = [line.strip() for line in text.splitlines() if line.strip().startswith(
                    ("repaginate ", "update field ", "calculate (", "save ", "save as ", "close "))]
                self.assertTrue(writes)
                self.assertTrue(all(target in line for line in writes), writes)
                self.assertIn("close (" + target + ") saving ask", text)
                self.assertLess(text.index("if not (saved of"), text.index("close ("))
                self.assertNotIn("close ", text.split("on error messageText", 1)[1])
                if kind == "excel":
                    self.assertNotIn("workbook 1", text)
                    self.assertIn("manual_calculation_not_supported", text)
                    self.assertNotIn("repeat 2 times", text)
                    self.assertNotIn("calculate full", text)

    def test_failure_after_confirmed_close_reports_cleanup_separately(self):
        res = self.run_job(self.transport())
        fail = {"status": "fail", "error": "PDF validation failed"}
        self.office._write_failure_receipt(self.work.parent.parent, fail)
        self.assertEqual(fail["status"], "fail")
        self.assertEqual(fail["office_session"]["cleanup"], "closed")
        self.assertEqual(res.office_session["cleanup"], "closed")

    def test_policy_hold_retains_live_job_but_removes_closed_job(self):
        mc = runtime("gg_major_contract")
        for cleanup, retained in [("preserved", True), ("unknown", True), ("closed", False)]:
            with self.subTest(cleanup=cleanup):
                work, _ = self.job("hold-" + cleanup)
                job = work.parent.parent
                (job / "office_session.json").write_text(json.dumps({
                    "schema": self.session.SCHEMA, "cleanup": cleanup}), encoding="utf-8")
                with mock.patch.object(mc, "reconfirm_output", side_effect=
                                       mc.OutputHeldError("project_revision_changed")):
                    with self.assertRaises(mc.OutputHeldError):
                        self.office._hold_if_moved([object()], object(), job)
                self.assertEqual(work.exists(), retained)

    def test_unreadable_session_never_authorizes_deletion(self):
        job = self.work.parent.parent
        (job / "office_session.json").write_text("{", encoding="utf-8")
        self.assertTrue(self.session.retain_job(job))

    def test_original_or_existing_pdf_cannot_enter_transport(self):
        raw = mock.Mock()
        with self.assertRaises(ValueError):
            self.run_job(raw, work=self.root / "original.docx")
        self.pdf.write_bytes(b"preexisting pdf")
        with self.assertRaises(ValueError):
            self.run_job(raw)
        raw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
