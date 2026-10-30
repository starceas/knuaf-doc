"""Office preservation boundaries: fake transport, real filesystem/lock.

No Office launch or user files. Script checks cover targeting syntax; native
AppleScript semantics and saved output caches require separate app evidence.
"""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

try:
    import fcntl
except ModuleNotFoundError as exc:
    if exc.name != "fcntl":
        raise
    fcntl = None


@unittest.skipUnless(fcntl is not None, "requires native POSIX fcntl locks; Office automation is not exercised")
class OfficeSessionTests(unittest.TestCase):
    kind = "word"
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

    def run_job(self, raw, *, kind=None, prepare=None, work=None, pdf=None):
        kind = kind or self.kind
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
        lock = self.guard / (self.kind + ".lock")
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
        # Excel and Word own separate per-app guards.
        fd = os.open(self.guard / "excel.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = self.transport()
        self.assertEqual(self.run_job(raw, kind="word").returncode, 0)
        self.assertEqual(raw.call_count, 2)

    def test_inflight_crash_and_malformed_lock_fail_closed(self):
        self.guard.mkdir()
        for content in ('{"state":"inflight"}', '{"state":"unknown"}', '{'):
            with self.subTest(content=content):
                (self.guard / (self.kind + ".lock")).write_text(content, encoding="utf-8")
                raw = mock.Mock()
                self.assertEqual(self.run_job(raw).returncode, 2)
                raw.assert_not_called()


    def test_failure_after_confirmed_close_reports_cleanup_separately(self):
        res = self.run_job(self.transport())
        fail = {"status": "fail", "error": "PDF validation failed"}
        self.office._write_failure_receipt(self.work.parent.parent, fail)
        self.assertEqual(fail["status"], "fail")
        self.assertEqual(fail["office_session"]["cleanup"], "closed")
        self.assertNotIn("reason", fail)
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



class DarwinExcelSessionTests(OfficeSessionTests):
    """Exercise the same preservation transitions for Excel as for Word."""

    kind = "excel"

    def test_prepare_failure_records_unknown_and_preserves(self):
        prepare = mock.Mock(side_effect=RuntimeError("Word must not launch for Excel"))
        result = self.run_job(self.transport(), prepare=prepare)
        self.assertEqual(result.office_session["cleanup"], "closed")
        prepare.assert_not_called()

    def test_existing_document_reason_is_machine_readable(self):
        raw = mock.Mock(return_value=self.result("GG_OFFICE_INVENTORY|1"))
        result = self.run_job(raw)
        self.assertEqual(result.office_session["reason"], "existing_documents")
        self.assertEqual(result.office_session["cleanup"], "not_attempted")
        self.assertTrue(self.work.exists())


class OfficeJobCleanupTests(unittest.TestCase):
    def test_explicit_cleanup_preserves_live_unknown_and_unrecorded_jobs(self):
        office = runtime("gg_office")
        session = runtime("gg_office_session")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ids = []
            for i, state in enumerate(("closed", "preserved", "unknown", None)):
                job = root / f"job-{i:012x}-1"
                job.mkdir()
                (job / "payload").write_text("keep unless closed")
                if state:
                    (job / "office_session.json").write_text(json.dumps({
                        "schema": session.SCHEMA, "cleanup": state}))
                ids.append(job.name)
            listing = office.list_jobs(root)
            self.assertEqual([j["cleanup_allowed"] for j in listing["jobs"]],
                             [True, False, False, False])
            result = office.clean_jobs(ids + ["../outside"], root)
            self.assertEqual(result["deleted"], [{"id": ids[0]}])
            self.assertEqual({r["id"] for r in result["refused"]}, set(ids[1:] + ["../outside"]))
            self.assertFalse((root / ids[0]).exists())
            for job_id in ids[1:]:
                self.assertEqual((root / job_id / "payload").read_text(), "keep unless closed")


class WindowsOfficeOwnershipTests(unittest.TestCase):
    def invoke(self, kind, *, errors=False, close_fails=False, foreign=False):
        from types import SimpleNamespace
        win = runtime("gg_office_win")
        calls = []
        collection = SimpleNamespace(Count=0)
        cell = SimpleNamespace(Address="$A$1")
        sheet = SimpleNamespace(Name="합성시트", UsedRange=SimpleNamespace(Cells=[cell]))
        def close(**kwargs):
            calls.append("close")
            if close_fails:
                raise RuntimeError("close_failed")
            collection.Count = 1 if foreign else 0
        document = SimpleNamespace(
            Repaginate=lambda: calls.append("repaginate"),
            Fields=SimpleNamespace(Update=lambda: calls.append("fields")),
            Save=lambda: calls.append("save"),
            ExportAsFixedFormat=lambda *a, **kw: calls.append("pdf"),
            Close=close, Worksheets=[sheet])
        def opened(*args):
            collection.Count = 1
            return document
        collection.Open = opened
        app = SimpleNamespace(Documents=collection, Workbooks=collection, Version="synthetic",
            CalculateFullRebuild=lambda: calls.append("calculate"),
            WorksheetFunction=SimpleNamespace(IsError=lambda cell: errors),
            Quit=lambda: calls.append("quit"))
        def dispatch(prog):
            calls.append(("private", prog))
            return app
        com = SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
        client = SimpleNamespace(DispatchEx=dispatch)
        with mock.patch.dict(sys.modules, {"pythoncom": com,
                "win32com": SimpleNamespace(client=client), "win32com.client": client}):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    getattr(win, "run_" + kind)("staged", "out.pdf")
                except SystemExit:
                    pass
        return calls

    def test_success_uses_private_instance_and_closes_before_quit(self):
        for kind in ("word", "excel"):
            calls = self.invoke(kind)
            self.assertEqual(calls[0], ("private", "Word.Application" if kind == "word" else "Excel.Application"))
            self.assertLess(calls.index("save"), calls.index("pdf"))
            self.assertLess(calls.index("close"), calls.index("quit"))

    def test_close_failure_or_foreign_document_prevents_quit(self):
        for kind in ("word", "excel"):
            for flag in ("close_fails", "foreign"):
                with self.subTest(kind=kind, flag=flag):
                    self.assertNotIn("quit", self.invoke(kind, **{flag: True}))

    def test_excel_formula_error_prevents_pdf(self):
        calls = self.invoke("excel", errors=True)
        self.assertIn("calculate", calls)
        self.assertIn("save", calls)
        self.assertNotIn("pdf", calls)

if __name__ == "__main__":
    unittest.main()
