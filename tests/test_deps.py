"""Runtime dependency plumbing: ASCII requirement files and a UTF-8 pip
child environment (JUDGE-B P2-1/P2-2).
"""
import contextlib
import io
import json
import ntpath
import os
import subprocess
import sys
import tempfile
import unittest
import venv
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


def _real_venv(path):
    """A real, pip-less venv (fast): returns its interpreter."""
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(path)
    for rel in (Path("bin") / "python3", Path("bin") / "python",
                Path("Scripts") / "python.exe"):
        if (path / rel).is_file():
            return path / rel
    raise AssertionError("venv interpreter not found under " + str(path))


def _site_packages(path):
    found = sorted(path.glob("lib/python*/site-packages")) + sorted(
        path.glob("Lib/site-packages"))
    if not found:
        raise AssertionError("site-packages not found under " + str(path))
    return found[0]


def _marker_writer(marker):
    return "import pathlib; pathlib.Path(%r).write_text('ran')\n" % str(marker)


class ImportProbeTests(ContractCase):
    def test_unicode_import_under_ascii_parent_returns_version(self):
        # The probe child runs with -I, so PYTHONPATH no longer reaches it:
        # the module and its metadata live in a real environment's
        # site-packages instead (the only place the probe may look).
        deps = runtime("gg_deps")
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "env"
            python = _real_venv(env)
            root = _site_packages(env)
            (root / "unicode_probe.py").write_text(
                "import sys\nsys.stderr.write('한글 import 진단')\n", encoding="utf-8")
            metadata = root / "synthetic_probe-1.2.3.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Name: synthetic-probe\nVersion: 1.2.3\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                    "PYTHONUTF8": "0", "PYTHONIOENCODING": "ascii"}):
                self.assertEqual(deps._import_and_version(
                    python, "unicode_probe", "synthetic-probe", cwd=env),
                    (True, "1.2.3"))

    def test_probe_ignores_pythonpath_and_working_directory(self):
        """-I: a same-named module on PYTHONPATH or in the child's working
        directory is never what the probe imports (app contract §3.3-2)."""
        deps = runtime("gg_deps")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = tmp / "env"
            python = _real_venv(env)
            path_dir = tmp / "pythonpath"
            cwd_dir = tmp / "cwd"
            path_dir.mkdir()
            cwd_dir.mkdir()
            marks = [tmp / "PWNED-pythonpath", tmp / "PWNED-cwd"]
            (path_dir / "planted_mod.py").write_text(_marker_writer(marks[0]))
            (cwd_dir / "planted_mod.py").write_text(_marker_writer(marks[1]))
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(path_dir)}):
                self.assertEqual(deps._import_and_version(
                    python, "planted_mod", "planted-mod", cwd=cwd_dir), (False, None))
            self.assertEqual([], [m.name for m in marks if m.exists()])

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


class WindowsLauncherFailureReasonTests(ContractCase):
    """A copy-mode Windows venv whose launcher cannot reach its base Python
    (exit code 103) gets one plain retry sentence on ``reason`` and nothing
    on disk moves (WINVENV research 2026-11-20 ③-8, cases N1-N4)."""

    MANIFEST = BrokenVenvRecoveryTests.MANIFEST

    def setUp(self):
        self.deps = runtime("gg_deps")
        self.tmp = tempfile.TemporaryDirectory(prefix="gg-deps-win103-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "paper"
        self.project.mkdir()

    def _launcher_venv(self, project=None):
        """A Windows copy-mode venv: Scripts\\python.exe is a regular file and
        pyvenv.cfg points ``home`` at a base Python folder that is gone."""
        project = project or self.project
        interp = project / ".venv" / "Scripts" / "python.exe"
        interp.parent.mkdir(parents=True)
        interp.write_bytes(b"MZ launcher copy\n")
        (project / ".venv" / "pyvenv.cfg").write_text(
            "home = " + str(self.root / "gone" / "Python311") + "\n"
            "include-system-site-packages = false\n"
            "version = 3.11.9\n",
            encoding="utf-8")
        return interp

    def _nt(self):
        return mock.patch.object(self.deps, "_on_windows", return_value=True)

    def _posix(self):
        return mock.patch.object(self.deps, "_on_windows", return_value=False)

    def _run(self, project, rc, *extra):
        class Pip:
            def __init__(self, code):
                self.returncode = code
                self.stdout = ""
                self.stderr = "No Python at '<gone>'"

        patches = [
            mock.patch.object(self.deps, "load_manifest",
                              return_value=[dict(d) for d in self.MANIFEST]),
            mock.patch.object(self.deps, "_import_and_version",
                              return_value=(True, "1.2")),
            mock.patch.object(self.deps.subprocess, "run",
                              return_value=Pip(rc)),
        ]
        patches.extend(extra)
        # Scoped to this call: a second _run() in the same test must not
        # inherit the first one's mocks (different rc, different builder).
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self.deps.main(["ensure", str(project)])
        return code, json.loads(out.getvalue())

    def _preserved_dirs(self, project=None):
        project = project or self.project
        return sorted(p for p in project.iterdir()
                      if p.name.startswith(".venv-broken-"))

    def test_launcher_103_on_windows_adds_one_retry_sentence(self):
        interp = self._launcher_venv()
        before = interp.read_bytes()

        code, body = self._run(self.project, 103, self._nt())

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertEqual(103, body["pip_exit"])
        self.assertFalse(body["created"])
        self.assertIsNone(body["preserved_venv"])
        self.assertEqual(self.deps.VENV_LAUNCHER_UNPREPARED_REASON,
                         body["reason"])
        # Nothing was moved, renamed or recreated, and no new key appeared.
        self.assertEqual(before, interp.read_bytes())
        self.assertFalse(interp.is_symlink())
        self.assertEqual([], self._preserved_dirs())
        self.assertNotIn("notices", body)
        self.assertEqual(
            {"status", "venv", "venv_python", "created", "preserved_venv",
             "pip_argv", "pip_exit", "pip_stdout_tail", "pip_stderr_tail",
             "installed_versions", "verified_imports", "requirements",
             "reason"},
            set(body))

    def test_retry_sentence_names_no_cause_and_no_cleanup(self):
        """The one sentence must not assert absence/moving or suggest
        deleting `.venv` (roh284c), and must be byte-identical every run."""
        sentence = self.deps.VENV_LAUNCHER_UNPREPARED_REASON
        for banned in ("찾을 수 없", "없습니다", "삭제", "지우", "지워",
                       "옮기", "이동", "없어"):
            self.assertNotIn(banned, sentence)
        self.assertNotIn(".venv", sentence)

        seen = []
        for name in ("paper-a", "paper-b"):
            project = self.root / name
            project.mkdir()
            self._launcher_venv(project)
            code, body = self._run(project, 103, self._nt())
            self.assertEqual(1, code, body)
            seen.append(body["reason"])

        self.assertEqual([sentence] * 2, seen)
        self.assertNotIn(str(self.root), sentence)

    def test_other_launcher_exit_codes_keep_the_old_shape(self):
        """Only 103 earns the sentence: neighbouring codes (102, 104), the
        launcher's other failure (101), pip's own failure (1) and a negative
        code from an abnormal termination must all keep the old shape."""
        self._launcher_venv()
        for rc in (101, 102, 104, 1, -1):
            with self.subTest(rc=rc):
                code, body = self._run(self.project, rc, self._nt())
                self.assertEqual(1, code, body)
                self.assertEqual("failed", body["status"])
                self.assertEqual(rc, body["pip_exit"])
                self.assertNotIn("reason", body)
                self.assertNotIn("notices", body)

    def test_missing_launcher_keeps_the_existing_failed_reason(self):
        """No Scripts\\python.exe and the builder produces none: the old
        "생성 후 실행 파일을 찾지 못함" failure keeps its own reason and the
        103 sentence is not borrowed for it (no pip ever ran)."""
        (self.project / ".venv" / "Scripts").mkdir(parents=True)

        class BuildsNothing:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                pass

        code, body = self._run(
            self.project, 103, self._nt(),
            mock.patch.object(self.deps.venv, "EnvBuilder", BuildsNothing),
        )

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotEqual(self.deps.VENV_LAUNCHER_UNPREPARED_REASON,
                            body["reason"])
        self.assertIn("찾지 못함", body["reason"])
        self.assertNotIn("pip_exit", body)
        self.assertNotIn("notices", body)
        self.assertEqual([], self._preserved_dirs())

    def test_freshly_built_launcher_copy_that_dies_with_103_gets_it(self):
        """The sentence follows the fault, not the history: a venv built in
        this very run is still a copy-mode launcher, so 103 on it is the
        same student-facing situation."""
        class BuildsLauncher:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                target = Path(path) / "Scripts" / "python.exe"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"MZ launcher copy\n")

        code, body = self._run(
            self.project, 103, self._nt(),
            mock.patch.object(self.deps.venv, "EnvBuilder", BuildsLauncher),
        )

        self.assertEqual(1, code, body)
        self.assertTrue(body["created"])
        self.assertEqual(103, body["pip_exit"])
        self.assertEqual(self.deps.VENV_LAUNCHER_UNPREPARED_REASON,
                         body["reason"])

    def test_python_link_in_scripts_keeps_the_old_shape(self):
        """A symlinked Scripts\\python.exe is a dev-mode venv, not the copied
        launcher, so 103 says nothing about the student's setup."""
        target = self.root / "runtime" / "python.exe"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"MZ\n")
        link = self.project / ".venv" / "Scripts" / "python.exe"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink를 만들 수 없는 환경")

        code, body = self._run(self.project, 103, self._nt())

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotIn("reason", body)
        self.assertTrue(link.is_symlink())
        self.assertEqual([], self._preserved_dirs())

    def test_posix_host_never_adds_the_sentence(self):
        """The POSIX branch is a contract of the seam, not of the machine the
        suite happens to run on: pin ``_on_windows`` to False so a Windows
        host checks the same thing instead of skipping itself."""
        interp = self.project / ".venv" / "bin" / "python3"
        interp.parent.mkdir(parents=True)
        interp.write_bytes(b"#!/bin/sh\n")

        code, body = self._run(self.project, 103, self._posix())

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotIn("reason", body)

    def test_preserved_copy_keeps_its_own_reason(self):
        """When a broken venv was moved aside, that reason (with the copy's
        name) wins: the retry sentence never replaces it."""
        moved = self.root / "runtime" / "python.exe"
        moved.parent.mkdir(parents=True)
        moved.write_bytes(b"MZ\n")
        link = self.project / ".venv" / "Scripts" / "python.exe"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(moved)
        except (OSError, NotImplementedError):
            self.skipTest("symlink를 만들 수 없는 환경")
        moved.unlink()

        class FakeBuilder:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                target = Path(path) / "Scripts" / "python.exe"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"MZ launcher copy\n")

        code, body = self._run(
            self.project, 103, self._nt(),
            mock.patch.object(self.deps.venv, "EnvBuilder", FakeBuilder),
        )

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertIsNotNone(body["preserved_venv"])
        self.assertIn(body["preserved_venv"]["name"], body["reason"])
        self.assertNotEqual(self.deps.VENV_LAUNCHER_UNPREPARED_REASON,
                            body["reason"])

    def test_launcher_predicate_needs_windows_and_a_regular_file(self):
        deps = self.deps
        interp = self._launcher_venv()
        with self._nt():
            self.assertTrue(deps._is_windows_launcher_copy(interp))
            self.assertFalse(deps._is_windows_launcher_copy(None))
            self.assertFalse(deps._is_windows_launcher_copy(
                self.project / ".venv" / "Scripts" / "missing.exe"))
            self.assertFalse(deps._is_windows_launcher_copy(
                self.project / ".venv" / "bin" / "python3"))
            target = self.root / "runtime" / "python.exe"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MZ\n")
            link = self.project / ".venv" / "Scripts" / "linked.exe"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink를 만들 수 없는 환경")
            self.assertFalse(deps._is_windows_launcher_copy(link))
        if os.name != "nt":
            # The unpatched host really is POSIX here, so nothing qualifies.
            self.assertFalse(deps._is_windows_launcher_copy(interp))


class DepsVenvContractTests(ContractCase):
    """KNUAF_DEPS_VENV (app v0.7.5 design rev. 3 §3.1, D-U/D-A): when the
    app names its environment, the thesis folder's .venv is never run, moved
    or deleted, and -m/-c children run isolated inside that environment."""

    MANIFEST = BrokenVenvRecoveryTests.MANIFEST

    def setUp(self):
        self.deps = runtime("gg_deps")
        self.tmp = tempfile.TemporaryDirectory(prefix="gg-deps-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "남이 준 논문"
        self.project.mkdir()
        self.app_env = self.root / "userData" / "deps" / "k0"
        # Every test states the contract variables itself.
        self.environ = mock.patch.dict(os.environ)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        os.environ.pop(self.deps.DEPS_VENV_ENV, None)
        os.environ.pop(self.deps.DEPS_VENV_READONLY_ENV, None)

    def _interp_rel(self):
        if os.name == "nt":
            return Path("Scripts") / "python.exe"
        return Path("bin") / "python3"

    def _plain_interp(self, env_dir):
        """An interpreter file that is only looked up, never run."""
        interp = env_dir / self._interp_rel()
        interp.parent.mkdir(parents=True, exist_ok=True)
        interp.write_text("#!/bin/sh\n", encoding="utf-8")
        return interp

    def _folder_venv_trap(self):
        """The folder's .venv interpreter: running it leaves a marker on
        POSIX. On Windows it is a plain file (a shell script cannot run
        there); the test still proves the app env was the one selected."""
        marker = self.root / "PWNED-folder-venv"
        interp = self.project / ".venv" / self._interp_rel()
        interp.parent.mkdir(parents=True)
        interp.write_text("#!/bin/sh\necho ran > '%s'\nexit 0\n" % marker,
                          encoding="utf-8")
        if os.name != "nt":
            interp.chmod(0o755)
        return interp, marker

    def _set(self, value):
        os.environ[self.deps.DEPS_VENV_ENV] = str(value)

    def _main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.deps.main(list(argv))
        return code, out.getvalue()

    def _manifest(self):
        return mock.patch.object(self.deps, "load_manifest",
                                 return_value=[dict(d) for d in self.MANIFEST])

    def _folder_untouched(self, interp, before):
        self.assertEqual(before, interp.read_bytes())
        self.assertEqual([], [p.name for p in self.project.iterdir()
                              if p.name.startswith(".venv-broken-")])

    def test_contract_constants_are_declared(self):
        self.assertEqual("KNUAF_DEPS_VENV", self.deps.DEPS_VENV_ENV)
        self.assertEqual("KNUAF_DEPS_VENV_READONLY",
                         self.deps.DEPS_VENV_READONLY_ENV)

    def test_venv_python_follows_only_the_variable(self):
        folder = self._plain_interp(self.project / ".venv")
        app = self._plain_interp(self.app_env)
        # Absent: unchanged behavior, the folder's .venv.
        self.assertEqual(folder, self.deps.venv_python(self.project))
        # Present: that environment only.
        self._set(self.app_env)
        self.assertEqual(app, self.deps.venv_python(self.project))
        # Present but empty: nothing — never a fall back to the folder.
        self._set("")
        self.assertIsNone(self.deps.venv_python(self.project))

    def test_python_command_follows_the_variable(self):
        self._plain_interp(self.project / ".venv")
        app = self._plain_interp(self.app_env)
        self._set(self.app_env)
        code, out = self._main("python", str(self.project))
        self.assertEqual((0, str(app)), (code, out.strip()))
        self._set("")
        code, out = self._main("python", str(self.project))
        self.assertEqual((0, sys.executable), (code, out.strip()))

    def test_relative_variable_is_refused(self):
        self._plain_interp(self.project / ".venv")
        self._set(os.path.join("userData", "deps"))
        code, out = self._main("python", str(self.project))
        self.assertEqual(2, code)
        self.assertEqual("blocked", json.loads(out)["status"])

    def test_doctor_runs_the_app_env_never_the_folder(self):
        trap, marker = self._folder_venv_trap()
        before = trap.read_bytes()
        app_python = _real_venv(self.app_env)
        witness = self.root / "WITNESS-app-env"
        (_site_packages(self.app_env) / "synthetic_pkg.py").write_text(
            "import pathlib; pathlib.Path(%r).write_text('app-env')\n" % str(witness))
        decoy = self.root / "PWNED-thesis-module"
        (self.project / "synthetic_pkg.py").write_text(_marker_writer(decoy))
        os.environ["PYTHONPATH"] = str(self.project)
        self._set(self.app_env)
        self._chdir(self.project)
        with self._manifest(), mock.patch.object(
                self.deps, "_import_and_version_current", return_value=(False, None)):
            report = self.deps.doctor(self.project)

        self.assertEqual(str(app_python), report["venv_python"])
        self.assertEqual(str(app_python), report["selected_python"])
        self.assertTrue(report["dependencies"][0]["venv"]["imported"])
        self.assertTrue(witness.exists())  # the child really ran in the app env
        self.assertFalse(marker.exists())
        self.assertFalse(decoy.exists())
        self._folder_untouched(trap, before)

    def test_isolated_venv_search_path_stays_under_trusted_prefixes(self) -> None:
        vpy = _real_venv(self.app_env)
        path_dir = self.root / "pythonpath"
        path_dir.mkdir()
        self._chdir(self.project)
        child = subprocess.run(
            [str(vpy), "-I", "-X", "utf8", "-c",
             "import json, site, sys; print(json.dumps([sys.path, sys.prefix, "
             "sys.base_prefix, site.getusersitepackages()]))"],
            cwd=self.app_env, env={**os.environ, "PYTHONPATH": str(path_dir)},
            capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(0, child.returncode, f"{child.stdout}\n{child.stderr}")
        paths, prefix, base_prefix, user_site = json.loads(child.stdout)
        details = f"sys.path={paths!r}; sys.prefix={prefix!r}; sys.base_prefix={base_prefix!r}"
        try:
            selected_env = self.app_env.samefile(prefix)
        except OSError as exc:
            self.fail(f"Cannot identify the child's environment: {exc}; {details}")
        self.assertTrue(selected_env, details)
        self.assertTrue(paths, details)
        trusted = (Path(base_prefix).resolve(), self.app_env.resolve())
        excluded = (Path(user_site).resolve(), self.project.resolve(), path_dir.resolve())
        for entry in paths:
            self.assertTrue(entry and Path(entry).is_absolute(), details)
            resolved = Path(entry).resolve()
            # Different Windows drives are non-matches, not commonpath errors.
            self.assertTrue(any(resolved.is_relative_to(root) for root in trusted), details)
            self.assertFalse(any(resolved.is_relative_to(root) for root in excluded), details)

    def test_doctor_with_empty_variable_never_looks_at_the_folder(self):
        folder = self._plain_interp(self.project / ".venv")
        self._set("")

        def no_child(*args, **kwargs):
            raise AssertionError("빈 값이면 어떤 환경 실행기도 띄우면 안 됨")

        with self._manifest(), mock.patch.object(
                self.deps, "_import_and_version_current", return_value=(True, "1.2")), \
                mock.patch.object(self.deps.subprocess, "run", side_effect=no_child):
            report = self.deps.doctor(self.project)
        self.assertIsNone(report["venv_python"])
        self.assertEqual(sys.executable, report["selected_python"])
        self.assertNotIn(str(folder), json.dumps(report, ensure_ascii=False))

    def test_ensure_uses_the_app_env_and_leaves_the_folder_venv(self):
        folder = self._plain_interp(self.project / ".venv")
        before = folder.read_bytes()
        app = self._plain_interp(self.app_env)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(cmd=cmd, kwargs=kwargs)
            return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        self._set(self.app_env)
        with self._manifest(), \
                mock.patch.object(self.deps, "_import_and_version",
                                  return_value=(True, "1.2")) as probe, \
                mock.patch.object(self.deps.subprocess, "run", side_effect=fake_run):
            body, code = self.deps.ensure(self.project, find_links="wheels")

        self.assertEqual(0, code, body)
        self.assertEqual(str(self.app_env), body["venv"])
        self.assertEqual(str(app), body["venv_python"])
        self.assertEqual([str(app), "-I", "-X", "utf8", "-m", "pip"],
                         captured["cmd"][:6])
        self.assertEqual(str(self.app_env), captured["kwargs"]["cwd"])
        # A relative --find-links still means the caller's folder.
        links = captured["cmd"][captured["cmd"].index("--find-links") + 1]
        self.assertEqual(os.path.abspath("wheels"), links)
        self.assertEqual(self.app_env, probe.call_args.kwargs["cwd"])
        self._folder_untouched(folder, before)

    def test_ensure_creates_the_app_env_not_the_folder_venv(self):
        created = []
        builder = self

        class FakeBuilder:
            def __init__(self, **kwargs):
                pass

            def create(self, path):
                created.append(Path(path))
                builder._plain_interp(Path(path))

        self._set(self.app_env)
        with self._manifest(), \
                mock.patch.object(self.deps, "_import_and_version",
                                  return_value=(True, "1.2")), \
                mock.patch.object(self.deps.subprocess, "run", return_value=type(
                    "Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()), \
                mock.patch.object(self.deps.venv, "EnvBuilder", FakeBuilder):
            body, code = self.deps.ensure(self.project)

        self.assertEqual(0, code, body)
        self.assertTrue(body["created"])
        self.assertEqual([self.app_env], created)
        self.assertFalse((self.project / ".venv").exists())

    def test_ensure_never_moves_a_broken_folder_venv(self):
        moved = self.root / "runtime" / "python3"
        moved.parent.mkdir(parents=True)
        moved.write_text("#!/bin/sh\n", encoding="utf-8")
        link = self.project / ".venv" / self._interp_rel()
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(moved)
        except (OSError, NotImplementedError):
            self.skipTest("symlink를 만들 수 없는 환경")
        moved.unlink()
        self._plain_interp(self.app_env)
        self._set(self.app_env)
        with self._manifest(), \
                mock.patch.object(self.deps, "_import_and_version",
                                  return_value=(True, "1.2")), \
                mock.patch.object(self.deps.subprocess, "run", return_value=type(
                    "Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()):
            body, code = self.deps.ensure(self.project)

        self.assertEqual(0, code, body)
        self.assertIsNone(body["preserved_venv"])
        self.assertTrue(link.is_symlink())
        self.assertEqual([], [p.name for p in self.project.iterdir()
                              if p.name.startswith(".venv-broken-")])

    def test_pip_child_ignores_a_planted_pip_module(self):
        """The pip-less app venv must not import pip.py from the thesis folder,
        even when the caller starts there and passes it on PYTHONPATH."""
        _real_venv(self.app_env)
        decoy = self.root / "PWNED-thesis-pip"
        (self.project / "pip.py").write_text(_marker_writer(decoy))
        os.environ["PYTHONPATH"] = str(self.project)
        self._set(self.app_env)
        self._chdir(self.project)
        with self._manifest():
            body, code = self.deps.ensure(self.project, no_index=True)

        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotEqual(0, body["pip_exit"])
        self.assertIn("No module named pip", body["pip_stderr_tail"])
        self.assertFalse(decoy.exists())

    def test_empty_variable_blocks_ensure_without_touching_anything(self):
        folder = self._plain_interp(self.project / ".venv")
        before = folder.read_bytes()

        def never(*args, **kwargs):
            raise AssertionError("빈 값이면 아무것도 만들거나 실행하면 안 됨")

        self._set("")
        with self._manifest(), \
                mock.patch.object(self.deps.subprocess, "run", side_effect=never), \
                mock.patch.object(self.deps.venv, "EnvBuilder", side_effect=never):
            code, out = self._main("ensure", str(self.project))

        body = json.loads(out)
        self.assertEqual(2, code)
        self.assertEqual("blocked", body["status"])
        self.assertEqual("deps_venv_empty", body["code"])
        self._folder_untouched(folder, before)

    def test_readonly_refuses_ensure_with_a_code(self):
        self._plain_interp(self.app_env)

        def never(*args, **kwargs):
            raise AssertionError("READONLY면 설치를 시작하면 안 됨")

        os.environ[self.deps.DEPS_VENV_READONLY_ENV] = "1"
        for value in (str(self.app_env), None):
            with self.subTest(variable=value):
                if value is None:
                    os.environ.pop(self.deps.DEPS_VENV_ENV, None)
                else:
                    self._set(value)
                with mock.patch.object(self.deps.subprocess, "run", side_effect=never), \
                        mock.patch.object(self.deps.venv, "EnvBuilder", side_effect=never):
                    code, out = self._main("ensure", str(self.project))
                body = json.loads(out)
                self.assertNotEqual(0, code)
                self.assertEqual("blocked", body["status"])
                self.assertEqual("deps_venv_readonly", body["code"])
        self.assertFalse((self.project / ".venv").exists())

    def test_readonly_still_allows_doctor_and_python(self):
        app = self._plain_interp(self.app_env)
        os.environ[self.deps.DEPS_VENV_READONLY_ENV] = "1"
        self._set(self.app_env)
        code, out = self._main("python", str(self.project))
        self.assertEqual((0, str(app)), (code, out.strip()))
        with self._manifest(), \
                mock.patch.object(self.deps, "_import_and_version_current",
                                  return_value=(True, "1.2")), \
                mock.patch.object(self.deps, "_import_and_version",
                                  return_value=(True, "1.2")):
            code, out = self._main("doctor", str(self.project))
        self.assertEqual(0, code)
        self.assertEqual(str(app), json.loads(out)["venv_python"])

    def test_every_m_or_c_child_list_is_isolated(self):
        """Mirror of the app's static gate: a list literal that starts an
        interpreter with -m/-c must carry -I in the same literal."""
        import ast

        tree = ast.parse((SCRIPTS / "gg_deps.py").read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List):
                consts = [e.value for e in node.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if ("-m" in consts or "-c" in consts) and "-I" not in consts:
                    offenders.append(node.lineno)
        self.assertEqual([], offenders)

    # --- roh319 I-01: a relative working folder keeps working -------------

    def _chdir(self, path):
        old = os.getcwd()
        os.chdir(path)
        self.addCleanup(os.chdir, old)

    def _synthetic_dist(self, env_dir):
        """A module + metadata in the environment's own site-packages."""
        root = _site_packages(env_dir)
        (root / "synthetic_pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
        meta = root / "synthetic_pkg-1.2.dist-info"
        meta.mkdir()
        (meta / "METADATA").write_text(
            "Name: synthetic-pkg\nVersion: 1.2\n", encoding="utf-8")

    def test_doctor_with_a_relative_folder_keeps_the_old_output(self):
        """No variable: ``doctor <relative>`` ran in v0.6.5 and printed the
        relative paths. The child starts inside the venv folder, so the
        executable it is handed must already be absolute."""
        rel = self.project.name
        _real_venv(self.project / ".venv")
        self._synthetic_dist(self.project / ".venv")
        self._chdir(self.root)
        with self._manifest(), mock.patch.object(
                self.deps, "_import_and_version_current", return_value=(False, None)):
            code, out = self._main("doctor", rel)

        self.assertEqual(0, code, out)
        report = json.loads(out)
        shown = str(Path(rel) / ".venv" / self._interp_rel())
        self.assertEqual(rel, report["project"])
        self.assertEqual(shown, report["venv_python"])
        self.assertEqual(shown, report["selected_python"])
        self.assertTrue(report["dependencies"][0]["venv"]["imported"])
        self.assertEqual("1.2", report["dependencies"][0]["venv"]["version"])
        self.assertTrue(report["ready"])

    def test_doctor_with_a_relative_folder_and_the_app_env(self):
        _real_venv(self.app_env)
        self._synthetic_dist(self.app_env)
        self._set(self.app_env)
        self._chdir(self.root)
        with self._manifest(), mock.patch.object(
                self.deps, "_import_and_version_current", return_value=(False, None)):
            code, out = self._main("doctor", self.project.name)

        self.assertEqual(0, code, out)
        report = json.loads(out)
        self.assertEqual(self.project.name, report["project"])
        self.assertEqual(str(self.app_env / self._interp_rel()), report["venv_python"])
        self.assertTrue(report["dependencies"][0]["venv"]["imported"])

    def test_ensure_with_a_relative_folder_starts_pip_and_prints_as_before(self):
        """Real pip child (the env has no pip, so it exits non-zero on its
        own): it must start at all, which a relative executable cannot do
        from the environment folder. The result still names the relative
        paths the caller used."""
        rel = self.project.name
        _real_venv(self.project / ".venv")
        self._chdir(self.root)
        with self._manifest():
            code, out = self._main("ensure", rel, "--no-index")

        body = json.loads(out)
        self.assertEqual(1, code, body)
        self.assertEqual("failed", body["status"])
        self.assertNotEqual(0, body["pip_exit"])
        self.assertIn("No module named pip", body["pip_stderr_tail"])
        interp = str(Path(rel) / ".venv" / self._interp_rel())
        self.assertEqual(str(Path(rel) / ".venv"), body["venv"])
        self.assertEqual(interp, body["venv_python"])
        self.assertEqual(interp, body["pip_argv"][0])
        self.assertEqual(["-I", "-X", "utf8", "-m", "pip"], body["pip_argv"][1:6])

    def test_python_command_with_a_relative_folder_prints_the_relative_path(self):
        self._plain_interp(self.project / ".venv")
        self._chdir(self.root)
        code, out = self._main("python", self.project.name)
        self.assertEqual(0, code)
        self.assertEqual(
            str(Path(self.project.name) / ".venv" / self._interp_rel()), out.strip())

    def test_app_env_path_must_name_a_drive_or_unc_on_windows(self):
        """Windows path completeness and environment policy are distinct."""
        deps = self.deps
        for value, full, allowed in (
                (r"C:\env\k0", True, True), ("c:/env/k0", True, True),
                (r"\\host\share\env", True, True), ("//host/share/env", True, True),
                (r"\\host\share", True, False), ("\\\\host\\share\\", True, False),
                (r"\\host", False, False), (r"\\host\\env", False, False),
                (r"\\?\C:\env", True, True), (r"\\?\UNC\host\share\env", True, True),
                (r"\\?\UNC\host\share", True, False),
                (r"1:\env", False, False), (r"\\?\1:\env", False, False),
                (r"\env\k0", False, False), ("/env/k0", False, False),
                (r"C:env\k0", False, False), (r"env\k0", False, False),
                ("C:", False, False)):
            with self.subTest(value=value), mock.patch.object(
                    deps, "_on_windows", return_value=True):
                self.assertEqual(full, deps._is_full_path(value))
                self._set(value)
                if allowed:
                    self.assertEqual(value, deps._deps_venv_override())
                else:
                    with self.assertRaises(ValueError):
                        deps._deps_venv_override()
        self.assertTrue(deps._is_full_path("/env/k0", windows=False))
        self.assertFalse(deps._is_full_path("env/k0", windows=False))

    # --- roh319-r2 I-01 rest: --find-links is reported as given ---------

    def _ensure_reporting_find_links(self, links):
        """Run ``ensure`` with the pip child intercepted; return the result
        body and the argv that really reached ``subprocess``."""
        self._plain_interp(self.app_env)
        self._set(self.app_env)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["argv"] = list(cmd)
            return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with self._manifest(), \
                mock.patch.object(self.deps, "_import_and_version",
                                  return_value=(True, "1.2")), \
                mock.patch.object(self.deps.subprocess, "run", side_effect=fake_run):
            body, code = self.deps.ensure(self.project, find_links=links)
        self.assertEqual(0, code, body)
        return body, captured["argv"]

    @staticmethod
    def _value_after(argv, flag):
        return argv[argv.index(flag) + 1]

    def test_relative_local_find_links_is_reported_as_given_and_run_absolute(self):
        self._chdir(self.root)
        body, argv = self._ensure_reporting_find_links("wheelhouse")

        self.assertEqual("wheelhouse", self._value_after(body["pip_argv"], "--find-links"))
        self.assertEqual(os.path.abspath("wheelhouse"),
                         self._value_after(argv, "--find-links"))
        self.assertTrue(os.path.isabs(argv[0]), argv[0])

    def test_find_links_url_is_reported_and_run_unchanged(self):
        self._chdir(self.root)
        for url in ("file:///srv/wheels", "https://example.invalid/wheels"):
            with self.subTest(url=url):
                body, argv = self._ensure_reporting_find_links(url)
                self.assertEqual(url, self._value_after(body["pip_argv"], "--find-links"))
                self.assertEqual(url, self._value_after(argv, "--find-links"))

    def test_absolute_find_links_is_reported_and_run_unchanged(self):
        absolute = os.path.abspath(str(self.root / "wheelhouse"))
        body, argv = self._ensure_reporting_find_links(absolute)

        self.assertEqual(absolute, self._value_after(body["pip_argv"], "--find-links"))
        self.assertEqual(absolute, self._value_after(argv, "--find-links"))

    def test_windows_find_links_uses_the_caller_drive_not_the_child_drive(self) -> None:
        child = r"D:\env\Scripts\python.exe"
        for links, expected in ((r"\wheels", r"C:\wheels"),
                                ("//host/share", "//host/share")):
            with self.subTest(links=links), \
                    mock.patch.object(self.deps, "_on_windows", return_value=True), \
                    mock.patch.object(os.path, "abspath", side_effect=lambda path: (
                        ntpath.normpath(ntpath.join(r"C:\caller", path)))):
                reported = [child, "--find-links", links]
                actual = self.deps._exec_pip_argv(reported)
                self.assertEqual([child, "--find-links", expected], actual)
                self.assertEqual([child, "--find-links", links], reported)

    # --- roh319 I-02: a new environment's pip bootstrap is isolated -------

    def _record_ensure(self, project_arg):
        """Run ``ensure`` end to end with a REAL venv builder and every child
        process recorded (not run): the argv lists that really reach
        ``subprocess`` — including any ``ensurepip`` child the builder itself
        would start — and the cwd each was given."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            # The probe child prints a version; every other child just exits 0.
            return type("Done", (), {
                "returncode": 0, "stdout": "1.2\n", "stderr": ""})()

        with self._manifest(), mock.patch.object(
                self.deps.subprocess, "run", side_effect=fake_run):
            body, code = self.deps.ensure(project_arg)
        return body, code, calls

    def _assert_children_isolated(self, calls, env_dir):
        interpreter_children = [
            (cmd, kw) for cmd, kw in calls if "-m" in cmd or "-c" in cmd]
        modules = {cmd[cmd.index("-m") + 1] for cmd, _ in interpreter_children
                   if "-m" in cmd}
        self.assertEqual({"ensurepip", "pip"}, modules)
        self.assertTrue(any("-c" in cmd for cmd, _ in interpreter_children))
        for cmd, kw in interpreter_children:
            with self.subTest(argv=cmd[1:5]):
                self.assertEqual(["-I", "-X", "utf8"], cmd[1:4])
                self.assertTrue(os.path.isabs(cmd[0]), cmd[0])
                self.assertEqual(os.path.abspath(env_dir), kw["cwd"])
                self.assertEqual("1", kw["env"]["PYTHONUTF8"])

    def test_new_app_env_bootstrap_children_are_isolated(self):
        self._set(self.app_env)
        body, code, calls = self._record_ensure(self.project)

        self.assertEqual(0, code, body)
        self.assertTrue(body["created"])
        self._assert_children_isolated(calls, self.app_env)
        ensurepip = next(cmd for cmd, _ in calls if "ensurepip" in cmd)
        self.assertEqual(["-m", "ensurepip", "--upgrade", "--default-pip"],
                         ensurepip[4:])
        self.assertFalse((self.project / ".venv").exists())

    def test_new_folder_env_with_a_relative_folder_is_isolated_too(self):
        """No variable and a relative folder: both fixes together — the
        builder's pip bootstrap is ours and its executable is absolute."""
        self._chdir(self.root)
        body, code, calls = self._record_ensure(self.project.name)

        self.assertEqual(0, code, body)
        self.assertTrue(body["created"])
        self._assert_children_isolated(
            calls, os.path.join(self.project.name, ".venv"))
        # What the result prints keeps the caller's relative form.
        self.assertEqual(
            str(Path(self.project.name) / ".venv" / self._interp_rel()),
            body["venv_python"])

    def test_failed_ensurepip_is_reported_like_a_failed_builder(self):
        """The bootstrap now runs in our try block: a failure there takes the
        same blocked path (and keeps a preserved copy named) as before."""
        self._set(self.app_env)

        def failing(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        with self._manifest(), mock.patch.object(
                self.deps.subprocess, "run", side_effect=failing):
            code, out = self._main("ensure", str(self.project))
        self.assertEqual(2, code)
        self.assertEqual("blocked", json.loads(out)["status"])


class RuntimeDependencyConsumerTests(ContractCase):
    def test_openpyxl_declares_school_verifier_consumer(self):
        manifest = json.loads((SCRIPTS / "runtime-deps.json").read_text(encoding="utf-8"))
        openpyxl = next(dep for dep in manifest["dependencies"] if dep["dist"] == "openpyxl")
        self.assertIn("scripts/gg_school_verify.py", openpyxl["used_by"])

if __name__ == "__main__":
    unittest.main()
