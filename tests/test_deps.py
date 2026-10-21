"""Runtime dependency plumbing: ASCII requirement files and a UTF-8 pip
child environment (JUDGE-B P2-1/P2-2).
"""
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


if __name__ == "__main__":
    unittest.main()
