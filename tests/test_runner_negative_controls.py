"""Runner-level negative controls on synthetic temp copies.

These tests copy the public tree to a disposable folder, damage it in one
documented way, and require the public runner to report a non-clean
result.  They never touch the real candidate: the copy is rebuilt each
time and deleted by TemporaryDirectory.  ``GG_VALIDATION_NESTED=1``
prevents recursion — inside a nested ``--runtime-only`` run this class's
three test IDs are on the runner's ``ALLOWED_NESTED_SKIP_IDS`` list (the
only sanctioned skips; see docs/validation/REGRESSIONS.md).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests._harness import REPO_ROOT, ContractCase

RUNNER = Path(__file__).resolve().parents[1] / "tools" / "run_validation.py"


def _copy_public(dest):
    for name in ("skills", "tests", "tools"):
        src = REPO_ROOT / name
        if src.is_dir():
            shutil.copytree(
                src, dest / name,
                ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("README.md", "LICENSE"):
        src = REPO_ROOT / name
        if src.exists():
            shutil.copy2(src, dest / name)


def _run_nested(root, env_extra=None):
    env = dict(os.environ)
    env["GG_VALIDATION_NESTED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    out = root / "nested-result.json"
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "run_validation.py"),
         "--runtime-only", "--json", str(out)],
        cwd=root, capture_output=True, text=True, env=env)
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return proc, report


@unittest.skipIf(os.environ.get("GG_VALIDATION_NESTED"),
                 "negative controls do not recurse inside a nested run")
class RunnerNegativeControlTests(ContractCase):
    def test_tampered_runtime_module_detected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-neg-") as tmp:
            root = Path(tmp) / "public"
            _copy_public(root)
            target = root / "skills/knuaf-doc/scripts/gg_core.py"
            target.write_text(
                target.read_text(encoding="utf-8") + "\nBROKEN (((",
                encoding="utf-8")
            proc, report = _run_nested(root)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNotNone(report)
        self.assertEqual(report["verdict"], "unexpected_failures")

    def test_missing_runtime_module_detected(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-neg-") as tmp:
            root = Path(tmp) / "public"
            _copy_public(root)
            (root / "skills/knuaf-doc/scripts/gg_document.py").unlink()
            proc, report = _run_nested(root)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNotNone(report)
        self.assertGreater(report["runtime"]["unexpected_failed"], 0)

    def test_pythonpath_pollution_does_not_shadow_runtime(self):
        with tempfile.TemporaryDirectory(prefix="knuaf-neg-") as tmp:
            root = Path(tmp) / "public"
            _copy_public(root)
            fake = Path(tmp) / "fakepath"
            fake.mkdir()
            (fake / "gg_core.py").write_text(
                "raise RuntimeError('polluted import won')", encoding="utf-8")
            env = {"PYTHONPATH": str(fake)}
            proc, report = _run_nested(root, env_extra=env)
        self.assertIsNotNone(report, proc.stderr)
        # had the polluted module won, its RuntimeError would surface in
        # output and abort collection; the run must complete instead.
        self.assertNotIn("polluted import won",
                         proc.stdout + proc.stderr)
        self.assertGreater(report["runtime"]["tests_run"], 0)
        # The shadow module must not have run anywhere — not just the
        # console.  A non-clean copied tree (mid-flight work in another
        # lane's files) may fail the run for unrelated reasons; what must
        # never appear is evidence the polluted module executed.
        self.assertNotIn(
            "polluted import won",
            json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
