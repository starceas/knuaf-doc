"""Scope decision contract for ``tools/ci_scope.py`` (design §2.3, §3).

Two halves are pinned here because either one alone is useless:

* the script prints **one value and nothing else** (no ``name=`` prefix), so
  the workflow's ``printf 'scope=%s\\n'`` is the only thing that builds
  ``$GITHUB_OUTPUT``;
* the workflow step actually does that, and a PR is the only event that asks
  the script at all.

Every undeterminable outcome must close as ``full`` — a failed diff must never
silently shrink the suite.  Fixtures are throwaway git repositories.
"""
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ci_scope  # noqa: E402

SCRIPT = ROOT / "tools" / "ci_scope.py"
WORKFLOW = ROOT / ".github" / "workflows" / "validation.yml"

_STEP = re.compile(r"^      - ")
_JOB = re.compile(r"^  [A-Za-z0-9_-]+:\s*$")
_UPLOAD_STEP = "Upload nested run reports"
_TEMPDIR_STEP = "Resolve temp dir"


def _steps(text):
    """Workflow steps as ``(job, name, block)`` — indentation-aware, no YAML lib.

    ``name`` is the step's ``- name:`` value, or ``None`` when the step starts
    with something else (``uses:``). A step's block runs to the next step
    marker or job header, so an assertion on the block cannot accidentally
    match a neighbouring step.
    """
    found = []
    job = None
    current = None
    in_jobs = False
    for line in text.splitlines():
        if line.rstrip() == "jobs:":
            in_jobs = True
            continue
        if in_jobs and _JOB.match(line):
            job = line.strip()[:-1]
            if current is not None:
                found.append(current)
            current = None
            continue
        if _STEP.match(line):
            if current is not None:
                found.append(current)
            current = [job, line, [line]]
            continue
        if current is not None:
            current[2].append(line)
    if current is not None:
        found.append(current)
    out = []
    for job_name, first, lines in found:
        match = re.match(r"^      - name: (.+)$", first)
        out.append((job_name, match.group(1).strip() if match else None,
                    "\n".join(lines).rstrip()))
    return out


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args), check=True,
                          capture_output=True, text=True)


def _init(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "commit.gpgsign", "false")


def _commit(root, files, message):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _scope(root, base, head):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root),
         "--base", base, "--head", head],
        capture_output=True, text=True)


class DecisionRuleTest(unittest.TestCase):
    """``decide`` — the path rule, without git."""

    def test_ordinary_change_is_code(self):
        self.assertEqual(ci_scope.decide(["tools/run_validation.py"]), "code")

    def test_unknown_or_new_path_is_code(self):
        # rule 2 is the catch-all: an unfamiliar path must not be skipped by
        # accident, and code scope still runs the whole ordinary suite.
        self.assertEqual(ci_scope.decide(["brand-new/dir/thing.json"]), "code")

    def test_version_files_force_full(self):
        for path in sorted(ci_scope.FULL_SCOPE_PATHS):
            with self.subTest(path=path):
                self.assertEqual(ci_scope.decide([path]), "full")
                self.assertEqual(ci_scope.decide(["docs/readme.md", path]), "full")

    def test_generated_ledgers_alone_are_not_a_reason_to_skip(self):
        # An empty *effective* diff is the undeterminable case: fail closed.
        self.assertEqual(ci_scope.decide(sorted(ci_scope.GENERATED_LEDGERS)),
                         "full")

    def test_ledgers_next_to_code_stay_code(self):
        self.assertEqual(
            ci_scope.decide(sorted(ci_scope.GENERATED_LEDGERS)
                            + ["tests/test_doc_contracts.py"]), "code")

    def test_empty_diff_is_full(self):
        self.assertEqual(ci_scope.decide([]), "full")


class GitDecisionTest(unittest.TestCase):
    """The same rule over real commits, plus the failure modes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        _init(self.root)
        self.base = _commit(
            self.root,
            {"skills/knuaf-doc/version.json": '{"version": "1"}\n',
             ".codex-plugin/plugin.json": '{"version": "1"}\n',
             "tools/validation-bundle.json": "{}\n"},
            "base")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ordinary_change_prints_the_value_only(self):
        head = _commit(self.root, {"docs/validation/BUNDLE.md": "x\n"}, "docs")
        done = _scope(self.root, self.base, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "code\n")   # value only, one line
        self.assertNotIn("scope=", done.stdout)
        self.assertNotIn("scope=", done.stderr)

    def test_version_bump_prints_full(self):
        head = _commit(self.root, {"skills/knuaf-doc/version.json":
                                   '{"version": "2"}\n'}, "bump")
        done = _scope(self.root, self.base, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_rename_out_of_a_version_file_is_full(self):
        # --no-renames classifies the delete side; a rename must not smuggle
        # a version change in as "some other path".
        _git(self.root, "mv", ".codex-plugin/plugin.json",
             "tools/plugin.json")
        head = _commit(self.root, {}, "move")
        done = _scope(self.root, self.base, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_ledger_only_change_is_full(self):
        head = _commit(self.root, {"tools/validation-bundle.json": '{"a": 1}\n'},
                       "ledger")
        done = _scope(self.root, self.base, head)
        self.assertEqual(done.stdout, "full\n")

    def test_empty_diff_is_full(self):
        done = _scope(self.root, self.base, self.base)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_unknown_revision_is_full(self):
        done = _scope(self.root, self.base, "0" * 40)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_missing_history_is_full(self):
        # A shallow checkout cannot answer; the answer is fail-closed, not
        # "nothing changed".
        done = _scope(self.root, "refs/heads/does-not-exist", self.base)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_not_a_repository_is_full(self):
        with tempfile.TemporaryDirectory() as other:
            done = _scope(other, self.base, self.base)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")


class ForkedHistoryTest(unittest.TestCase):
    """The PR base is main's tip, not the branch point — compare from the fork.

    Both directions are wrong when the two tips are diffed directly: main's
    own version bump after the fork leaks in (needless ``full``), and a
    version bump both sides made the same way cancels out (``full`` PR read as
    ``code``).  Design §2.3 takes ``pull_request.base.sha``/``head.sha``.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        _init(self.root)
        self.trunk = subprocess.run(
            ["git", "-C", self.root, "symbolic-ref", "--short", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        self.fork = _commit(
            self.root,
            {"skills/knuaf-doc/version.json": '{"version": "1"}\n',
             ".codex-plugin/plugin.json": '{"version": "1"}\n',
             "docs/readme.md": "v1\n"},
            "fork point")

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_version_bump_after_the_fork_does_not_leak_into_the_pr(self):
        _git(self.root, "checkout", "-q", "-b", "feature", self.fork)
        head = _commit(self.root, {"docs/readme.md": "pr edit\n"}, "pr docs")
        _git(self.root, "checkout", "-q", self.trunk)
        main = _commit(self.root, {"skills/knuaf-doc/version.json":
                                   '{"version": "2"}\n'}, "main bump")
        done = _scope(self.root, main, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "code\n")

    def test_the_same_version_bump_on_both_sides_is_still_full(self):
        _git(self.root, "checkout", "-q", "-b", "feature", self.fork)
        head = _commit(self.root,
                       {"skills/knuaf-doc/version.json": '{"version": "2"}\n',
                        "docs/readme.md": "pr edit\n"}, "pr bump")
        _git(self.root, "checkout", "-q", self.trunk)
        main = _commit(self.root,
                       {"skills/knuaf-doc/version.json": '{"version": "2"}\n'},
                       "main bump, identical")
        done = _scope(self.root, main, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_histories_without_a_common_ancestor_are_full(self):
        _git(self.root, "checkout", "-q", "--orphan", "island")
        for entry in Path(self.root).iterdir():
            if entry.name == ".git":
                continue
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        island = _commit(self.root, {"docs/readme.md": "unrelated\n"}, "island")
        done = _scope(self.root, self.fork, island)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")

    def test_several_common_ancestors_are_full(self):
        # Criss-cross histories have two equally valid bases and plain
        # ``git merge-base`` silently picks one; either pick can hide a path.
        _git(self.root, "checkout", "-q", "-b", "left", self.fork)
        left = _commit(self.root, {"docs/left.md": "l\n"}, "left")
        _git(self.root, "checkout", "-q", "-b", "right", self.fork)
        right = _commit(self.root, {"docs/right.md": "r\n"}, "right")
        _git(self.root, "checkout", "-q", "left")
        _git(self.root, "merge", "-q", "--no-ff", "-m", "merge right", "right")
        head = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        _git(self.root, "checkout", "-q", "right")
        _git(self.root, "merge", "-q", "--no-ff", "-m", "merge left", left)
        base = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(len(ci_scope._git(self.root, "merge-base", "--all",
                                           base, head).split()), 2)
        done = _scope(self.root, base, head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "full\n")


class WorkflowContractTest(unittest.TestCase):
    """The other half of the output contract, read from the workflow text."""

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_detect_step_builds_the_github_output_line(self):
        self.assertIn("printf 'scope=%s\\n' \"$s\" >> \"$GITHUB_OUTPUT\"",
                      self.text)
        self.assertIn('s=$(python3 tools/ci_scope.py --base "$B" --head "$H") '
                      '|| exit 1', self.text)

    def test_non_pull_request_events_declare_full_explicitly(self):
        self.assertIn('if [ "$EVENT_NAME" = "pull_request" ]; then', self.text)
        self.assertIn("else\n              s=full\n          fi", self.text)

    def test_the_output_is_declared_and_consumed(self):
        self.assertIn("scope: ${{ steps.detect.outputs.scope }}", self.text)
        self.assertEqual(
            self.text.count('--pr-scope "${{ needs.scope.outputs.scope }}"'), 4)
        self.assertIn("needs: [scope]", self.text)
        self.assertIn("needs: [scope, suite-shards-py310]", self.text)

    def test_the_script_does_not_write_github_output_itself(self):
        # The docstring names the contract; the code must not touch it. One
        # writer keeps the format in exactly one place.
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        body = tree.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        code = "\n".join(ast.unparse(node) for node in body)
        self.assertNotIn("GITHUB_OUTPUT", code)
        self.assertNotIn("outputs", code)


class NestedReportUploadContractTest(unittest.TestCase):
    """The whole-shard nested-run reports survive a failing shard.

    Ten units start whole nested validation runs and write their per-run
    report next to the temporary dir (``knuaf-nested-reports/``). A shard that
    fails must still publish them, otherwise the run leaves nothing to
    diagnose. The upload is therefore pinned step by step: the temp dir is
    resolved inside the job, the upload ignores the job's own failure, and it
    keeps the run index plus the ``.stderr`` of each run.
    """

    @classmethod
    def setUpClass(cls):
        cls.steps = _steps(WORKFLOW.read_text(encoding="utf-8"))

    def _upload(self, job):
        blocks = [(name, block) for job_name, name, block in self.steps
                  if job_name == job and name == _UPLOAD_STEP]
        self.assertEqual(1, len(blocks), f"{job}: exactly one {_UPLOAD_STEP}")
        return blocks[0][1]

    def test_both_shard_jobs_publish_nested_reports(self):
        uploads = {job for job, name, _block in self.steps
                   if name == _UPLOAD_STEP}
        self.assertEqual({"suite-shards-py310", "suite-shards-py314"}, uploads)

    def test_temp_dir_step_is_shared_and_always_runs(self):
        for job in ("suite-shards-py310", "suite-shards-py314"):
            with self.subTest(job=job):
                blocks = [block for job_name, name, block in self.steps
                          if job_name == job and name == _TEMPDIR_STEP]
                self.assertEqual(1, len(blocks), job)
                block = blocks[0]
                self.assertIn("if: always()", block)
                self.assertIn("id: tmpdir", block)
                self.assertIn("shell: bash", block)
                self.assertIn('printf \'path=%s\\n\'', block)
                self.assertIn('>> "$GITHUB_OUTPUT"', block)
                # The upload reads steps.tmpdir, so the resolve step must
                # come first in the same job.
                names = [name for job_name, name, _block in self.steps
                         if job_name == job]
                self.assertLess(names.index(_TEMPDIR_STEP),
                                names.index(_UPLOAD_STEP), job)

    def test_upload_runs_after_a_failed_shard_and_keeps_both_kinds(self):
        for job in ("suite-shards-py310", "suite-shards-py314"):
            with self.subTest(job=job):
                block = self._upload(job)
                self.assertIn("if: always()", block)
                self.assertIn("uses: actions/upload-artifact@v4", block)
                # Path comes from the job's own resolved temp dir, never from
                # ``runner.temp`` inline: the reports live under tempfile's
                # dir, which on the runner is not guaranteed to be the same
                # expression (Windows shards resolve it themselves).
                self.assertIn("${{ steps.tmpdir.outputs.path }}", block)
                self.assertIn("/knuaf-nested-reports/*.json", block)
                self.assertIn("/knuaf-nested-reports/*.stderr", block)
                # Missing reports (no nested unit ran) must not fail the job.
                self.assertIn("if-no-files-found: warn", block)
                self.assertNotIn("if-no-files-found: error", block)

    def test_artifact_names_are_unique_per_python_and_shard(self):
        for job, version in (("suite-shards-py310", "py3.10"),
                             ("suite-shards-py314", "py3.14")):
            with self.subTest(job=job):
                block = self._upload(job)
                # No f-string here: ``{{``/``}}`` are escapes in one, and the
                # workflow text really contains two braces.
                self.assertIn("name: validation-nested-reports-" + version
                              + "-${{ matrix.shard }}", block)


if __name__ == "__main__":
    unittest.main()
