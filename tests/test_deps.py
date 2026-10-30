"""Runtime dependency plumbing: ASCII requirement files and a UTF-8 pip
child environment (JUDGE-B P2-1/P2-2).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, REPO_ROOT, SCRIPTS, runtime


class RequirementFileTests(ContractCase):
    def test_pip_requirement_files_are_ascii(self):
        # pip reads -r files with the locale encoding: a Korean Windows
        # (cp949) or Western (cp1252) console dies on the first non-ASCII
        # byte before it can install anything.
        files = sorted(SCRIPTS.glob("requirements*.txt"))
        self.assertTrue(files)
        for path in files:
            raw = path.read_bytes()
            try:
                raw.decode("ascii")
            except UnicodeDecodeError as exc:
                self.fail("%s is not ASCII: %s" % (path.name, exc))


class PipEnvTests(ContractCase):
    def test_ensure_hands_pip_a_utf8_env(self):
        deps = runtime("gg_deps")
        with tempfile.TemporaryDirectory(prefix="p1-deps-") as tmp:
            project = Path(tmp)
            (project / ".venv").mkdir()
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs

                class Done:
                    returncode = 1
                    stdout = ""
                    stderr = ""

                return Done()

            with mock.patch.object(deps, "venv_python",
                                   return_value=sys.executable), \
                    mock.patch.object(deps.subprocess, "run",
                                      side_effect=fake_run):
                deps.ensure(project)
        kwargs = captured["kwargs"]
        self.assertEqual("utf-8", kwargs["encoding"])
        self.assertEqual("replace", kwargs["errors"])
        env = kwargs["env"]
        self.assertEqual("1", env["PYTHONUTF8"])
        self.assertEqual("utf-8", env["PYTHONIOENCODING"])
        # The rest of the parent environment still reaches pip.
        self.assertIn("PATH", env)


class ImportProbeTests(ContractCase):
    def test_unicode_import_under_ascii_parent_returns_version(self):
        deps = runtime("gg_deps")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unicode_probe.py").write_text(
                "import sys\nsys.stderr.write('한글 import 진단')\n", encoding="utf-8")
            metadata = root / "synthetic_probe-1.2.3.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Name: synthetic-probe\nVersion: 1.2.3\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                    "PYTHONPATH": tmp, "PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"}):
                self.assertEqual(deps._import_and_version(
                    sys.executable, "unicode_probe", "synthetic-probe"), (True, "1.2.3"))

    def test_subprocess_error_is_a_structured_cli_failure(self):
        deps = runtime("gg_deps")
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.object(deps.venv.EnvBuilder, "create",
                    side_effect=subprocess.CalledProcessError(7, ["synthetic-venv"])), \
                    contextlib.redirect_stdout(stdout):
                code = deps.main(["ensure", tmp, "--no-index"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
