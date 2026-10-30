"""Doctor observes lock and recovery state; it never repairs the workspace."""
import json
import os
import subprocess
import sys
from unittest import mock

from tests._harness import ContractCase, SCRIPTS, runtime, write_text


class DoctorTests(ContractCase):
    def test_inspection_reports_ready_unprobed_then_busy_without_writes(self):
        core = runtime("gg_core")
        lock = runtime("gg_lock")
        root = self.make_project()
        write_text(root, ".gg-import-orphan/input.txt", "preserved")
        before = self._tree_bytes(root)
        report = core.doctor(root)
        self.assertEqual(("ready", "unprobed"), (report["lock"]["structure"], report["lock"]["lock_state"]))
        self.assertEqual([".gg-import-orphan"], report["orphan_files"])
        self.assertEqual(before, self._tree_bytes(root))
        with lock.Lock(root):
            before = self._tree_bytes(root)
            report = core.doctor(root, probe=True)
            self.assertEqual("busy", report["lock"]["lock_state"])
            self.assertEqual(before, self._tree_bytes(root))
        self.assertEqual("free", core.doctor(root, probe=True)["lock"]["lock_state"])

    def test_damaged_project_and_legacy_lock_remain_inspectable(self):
        root = self.make_project()
        write_text(root, "project.json", "invalid json")
        write_text(root, ".gg-recovery-active.json", "{}")
        before = self._tree_bytes(root)
        report = runtime("gg_core").doctor(root, probe=True)
        self.assertEqual("unavailable", report["snapshots"]["status"])
        self.assertNotEqual("free", report["lock"]["lock_state"])
        self.assertEqual(before, self._tree_bytes(root))
        root = root / "legacy"
        write_text(root, ".gg-lock/owner.json", '{"pid":1}')
        before = self._tree_bytes(root)
        report = runtime("gg_core").doctor(root, probe=True)
        self.assertEqual("legacy", report["lock"]["structure"])
        self.assertNotEqual("free", report["lock"]["lock_state"])
        self.assertEqual(before, self._tree_bytes(root))

    def test_failed_project_probe_keeps_fallback_distinct_from_project_success(self):
        core = runtime("gg_core")
        deps = runtime("gg_deps")
        root = self.make_project()
        with mock.patch.object(deps, "doctor", side_effect=OSError("synthetic private path")):
            report = core.doctor(root)
        self.assertEqual({"docx", "openpyxl", "pypdf"}, set(report["dependencies"]))
        self.assertEqual("unavailable", report["deps"]["status"])
        self.assertEqual(sys.executable, report["deps"]["selected_python"])
        self.assertNotIn("synthetic private path", json.dumps(report))

    def test_cli_probe_observes_real_guard_and_does_not_unlock_it(self):
        root = self.make_project()
        lock = runtime("gg_lock")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        with lock.Lock(root):
            before = self._tree_bytes(root)
            proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "gg.py"), "doctor", str(root), "--probe"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(0, proc.returncode, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual("busy", report["lock"]["lock_state"])
            self.assertEqual("not_run", report["render"])
            self.assertEqual("not_run", report["recalculation"])
            self.assertFalse(report["kordoc"]["capability_verified"])
            self.assertEqual(before, self._tree_bytes(root))
