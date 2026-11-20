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


class BrokenVenvRecoveryTests(ContractCase):
    """A venv whose interpreter link lost its target is preserved, not
    rebuilt over and never deleted (student B, 2026-11-19)."""

    MANIFEST = [{
        "dist": "synthetic-pkg",
        "spec": "synthetic-pkg>=1",
        "import_name": "synthetic_pkg",
        "purpose": "시험용",
    }]

    def _interp_rel(self):
        if os.name == "nt":
            return Path("Scripts") / "python.exe"
        return Path("bin") / "python3"

    def _make_broken_venv(self, project):
        """A .venv whose interpreter link points at a Python that moved away."""
        moved = project.parent / "runtime" / "python3"
        moved.parent.mkdir(parents=True, exist_ok=True)
        moved.write_text("#!/bin/sh\n", encoding="utf-8")
        link = project / ".venv" / self._interp_rel()
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(moved)
        except (OSError, NotImplementedError):
            self.skipTest("symlink를 만들 수 없는 환경")
        # While the target still exists, the link must reach exactly it.
        self.assertTrue(os.path.samefile(link, moved))
        moved.unlink()
        self.assertFalse(link.exists())
        # The link text as the OS reports it, read before ensure() runs.
        # Windows returns it with a \\?\ prefix; comparing against this
        # value (not the path we passed in) catches a rewritten record or
        # a link swapped during preservation without stripping anything.
        return os.readlink(link)

    def _patch(self, created):
        """Pip succeeds, probes report an in-range version, builder is fake."""
        builder = self

        class FakeBuilder:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def create(self, path):
                created.append(Path(path))
                target = Path(path) / builder._interp_rel()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("#!/bin/sh\n", encoding="utf-8")

        class Done:
            returncode = 0
            stdout = ""
            stderr = ""

        stack = [
            mock.patch.object(self.deps, "load_manifest",
                              return_value=[dict(d) for d in self.MANIFEST]),
            mock.patch.object(self.deps, "_import_and_version",
                              return_value=(True, "1.2")),
            mock.patch.object(self.deps.subprocess, "run",
                              return_value=Done()),
            mock.patch.object(self.deps.venv, "EnvBuilder", FakeBuilder),
        ]
        return stack

    def _run(self, project, *extra):
        for patch in extra:
            patch.start()
            self.addCleanup(patch.stop)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.deps.main(["ensure", str(project)])
        return code, json.loads(out.getvalue())

    def setUp(self):
        self.deps = runtime("gg_deps")
        self.tmp = tempfile.TemporaryDirectory(prefix="gg-deps-broken-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "paper"
        self.project.mkdir()

    def _preserved_dirs(self):
        return sorted(p for p in self.project.iterdir()
                      if p.name.startswith(".venv-broken-"))

    def test_broken_interpreter_link_is_preserved_then_rebuilt(self):
        link_target = self._make_broken_venv(self.project)
        created = []
        code, body = self._run(self.project, *self._patch(created))

        self.assertEqual(0, code, body)
        self.assertEqual("installed", body["status"])
        self.assertTrue(body["created"])
        self.assertEqual([self.project / ".venv"], created)
        kept = body["preserved_venv"]
        self.assertIsNotNone(kept)
        self.assertEqual(".venv-broken-", kept["name"][:len(".venv-broken-")])
        self.assertEqual(str(self.project / ".venv" / self._interp_rel()),
                         kept["link"])
        self.assertEqual(link_target, kept["link_target"])
        # The old environment still exists under the new name, link included.
        old = Path(kept["path"])
        self.assertTrue(old.is_dir())
        self.assertTrue((old / self._interp_rel()).is_symlink())
        self.assertEqual(link_target, os.readlink(old / self._interp_rel()))
        # The new environment is a real one, and it is the one used.
        self.assertTrue((self.project / ".venv" / self._interp_rel()).is_file())
        self.assertFalse((self.project / ".venv" / self._interp_rel()).is_symlink())
        self.assertTrue(body["venv_python"].startswith(str(self.project / ".venv")))

    def test_preserved_name_never_overwrites_an_earlier_copy(self):
        self._make_broken_venv(self.project)
        earlier = self.project / ".venv-broken-20260101-010101"
        earlier.mkdir()
        (earlier / "keep.txt").write_text("earlier", encoding="utf-8")
        fixed = mock.patch.object(self.deps.time, "strftime",
                                  return_value="20260101-010101")
        code, body = self._run(self.project, fixed, *self._patch([]))

        self.assertEqual(0, code, body)
        self.assertEqual(".venv-broken-20260101-010101-2",
                         body["preserved_venv"]["name"])
        self.assertEqual("earlier", (earlier / "keep.txt").read_text(encoding="utf-8"))

    def test_preserved_success_carries_a_student_notice(self):
        """보존 뒤 성공하면 학생용 고정 문장 + 기계용 폴더 이름을 싣는다."""
        self._make_broken_venv(self.project)
        code, body = self._run(self.project, *self._patch([]))

        self.assertEqual(0, code, body)
        notices = body["notices"]
        self.assertEqual(1, len(notices))
        notice = notices[0]
        self.assertEqual("venv_preserved", notice["code"])
        self.assertEqual(self.deps.VENV_PRESERVED_NOTICE["message"],
                         notice["message"])
        self.assertEqual(body["preserved_venv"]["name"], notice["folder"])

    def test_notice_message_is_fixed_and_names_no_folder_or_path(self):
        """문장은 늘 같은 바이트여야 한다 - 폴더 이름·경로를 끼워 넣지 않는다."""
        messages = []
        folders = []
        for stamp in ("20260101-010101", "20260202-020202"):
            project = self.root / ("paper-" + stamp)
            project.mkdir()
            self.project = project
            self._make_broken_venv(project)
            fixed = mock.patch.object(self.deps.time, "strftime",
                                      return_value=stamp)
            code, body = self._run(project, fixed, *self._patch([]))

            self.assertEqual(0, code, body)
            messages.append(body["notices"][0]["message"])
            folders.append(body["notices"][0]["folder"])

        self.assertEqual([messages[0]] * 2, messages)
        self.assertNotEqual(folders[0], folders[1])
        for message in messages:
            for folder in folders:
                self.assertNotIn(folder, message)
            self.assertNotIn(str(self.root), message)
            self.assertNotIn("\\", message)
            self.assertNotIn("/", message)

    def test_failed_install_after_preservation_keeps_its_response_shape(self):
        """실패 응답은 모양을 바꾸지 않는다 - 안내는 `reason`이 이미 담는다."""
        self._make_broken_venv(self.project)
        stack = self._patch([])
        stack[2] = mock.patch.object(self.deps.subprocess, "run", return_value=type(
            "Failed", (), {"returncode": 1, "stdout": "", "stderr": "pip failed"})())
        code, body = self._run(self.project, *stack)

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotIn("notices", body)
        self.assertIn(body["preserved_venv"]["name"], body["reason"])

    def test_healthy_venv_is_reused_without_preservation(self):
        interp = self.project / ".venv" / self._interp_rel()
        interp.parent.mkdir(parents=True)
        interp.write_text("#!/bin/sh\n", encoding="utf-8")

        class NoCreate:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                raise AssertionError("정상 .venv를 다시 만들면 안 됨")

        code, body = self._run(
            self.project,
            mock.patch.object(self.deps, "load_manifest",
                              return_value=[dict(d) for d in self.MANIFEST]),
            mock.patch.object(self.deps, "_import_and_version",
                              return_value=(True, "1.2")),
            mock.patch.object(self.deps.subprocess, "run", return_value=type(
                "Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()),
            mock.patch.object(self.deps.venv, "EnvBuilder", NoCreate),
        )

        self.assertEqual(0, code, body)
        self.assertFalse(body["created"])
        self.assertIsNone(body["preserved_venv"])
        self.assertNotIn("notices", body)
        self.assertEqual([], self._preserved_dirs())

    def test_venv_symlink_is_still_refused_untouched(self):
        real = self.root / "elsewhere"
        real.mkdir()
        try:
            (self.project / ".venv").symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink를 만들 수 없는 환경")
        code, body = self._run(self.project)

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertIn("symlink", body["reason"])
        self.assertTrue((self.project / ".venv").is_symlink())
        self.assertEqual([], self._preserved_dirs())

    def test_rename_failure_blocks_with_guidance_and_keeps_the_venv(self):
        self._make_broken_venv(self.project)
        busy = mock.patch.object(self.deps.os, "rename",
                                 side_effect=OSError(13, "in use"))

        class NeverBuild:
            """On the old code this test would reach a real venv builder and
            a real pip run; fail loudly instead, offline and deterministic."""

            def __init__(self, **kwargs):
                raise AssertionError("이름 바꾸기 실패 뒤에는 환경을 만들면 안 됨")

        code, body = self._run(
            self.project, busy,
            mock.patch.object(self.deps.venv, "EnvBuilder", NeverBuild),
        )

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertIn("끄고", body["reason"])
        self.assertIn("지우지 않았습니다", body["reason"])
        self.assertTrue((self.project / ".venv" / self._interp_rel()).is_symlink())
        self.assertEqual([], self._preserved_dirs())

    def test_rebuild_failure_still_names_the_preserved_copy(self):
        self._make_broken_venv(self.project)

        class Refuses:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                raise subprocess.CalledProcessError(7, ["synthetic-venv"])

        code, body = self._run(
            self.project,
            mock.patch.object(self.deps.venv, "EnvBuilder", Refuses),
        )

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        # The rename happened, so the message must point at the copy.
        kept = self._preserved_dirs()
        self.assertEqual(1, len(kept))
        self.assertIn(kept[0].name, body["reason"])
        self.assertTrue((kept[0] / self._interp_rel()).is_symlink())
    def test_rebuild_failure_response_includes_preservation_record(self):
        self._make_broken_venv(self.project)

        class Refuses:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                raise RuntimeError("builder failed")

        code, body = self._run(
            self.project,
            mock.patch.object(self.deps.venv, "EnvBuilder", Refuses),
        )

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])

    def test_missing_rebuilt_interpreter_names_preserved_copy(self):
        self._make_broken_venv(self.project)

        class NoInterpreter:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                pass

        code, body = self._run(
            self.project,
            mock.patch.object(self.deps.venv, "EnvBuilder", NoInterpreter),
        )

        self.assertEqual(1, code)
        self.assertEqual("failed", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])

    def test_verification_exception_reports_preserved_copy(self):
        self._make_broken_venv(self.project)
        stack = self._patch([])
        stack[1] = mock.patch.object(
            self.deps, "_import_and_version",
            side_effect=subprocess.TimeoutExpired(["synthetic-probe"], 60),
        )
        code, body = self._run(self.project, *stack)

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])


    def test_pip_exception_still_reports_preserved_copy(self):
        self._make_broken_venv(self.project)
        stack = self._patch([])
        stack[2] = mock.patch.object(
            self.deps.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["synthetic-pip"], 1),
        )
        code, body = self._run(self.project, *stack)

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])
        self.assertTrue(Path(body["preserved_venv"]["path"]).is_dir())

    def test_pip_failure_result_still_reports_preserved_copy(self):
        self._make_broken_venv(self.project)
        stack = self._patch([])
        stack[2] = mock.patch.object(self.deps.subprocess, "run", return_value=type(
            "Failed", (), {"returncode": 1, "stdout": "", "stderr": "pip failed"})())
        code, body = self._run(self.project, *stack)

        self.assertEqual(1, code)
        self.assertEqual("failed", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])

    def test_other_venv_failure_does_not_move_the_folder(self):
        """A venv broken some other way (no interpreter at all) keeps the
        old path: blocked, nothing renamed, nothing deleted."""
        (self.project / ".venv" / "junk").mkdir(parents=True)
        (self.project / ".venv" / "junk" / "keep.txt").write_text(
            "keep", encoding="utf-8")

        class Refuses:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                raise FileExistsError(17, "exists", str(path))

        code, body = self._run(
            self.project,
            mock.patch.object(self.deps.venv, "EnvBuilder", Refuses),
        )

        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertEqual([], self._preserved_dirs())
        self.assertEqual("keep", (self.project / ".venv" / "junk" /
                                  "keep.txt").read_text(encoding="utf-8"))


class RuntimeDependencyConsumerTests(ContractCase):
    def test_openpyxl_declares_school_verifier_consumer(self):
        manifest = json.loads((SCRIPTS / "runtime-deps.json").read_text(encoding="utf-8"))
        openpyxl = next(dep for dep in manifest["dependencies"] if dep["dist"] == "openpyxl")
        self.assertIn("scripts/gg_school_verify.py", openpyxl["used_by"])

if __name__ == "__main__":
    unittest.main()
