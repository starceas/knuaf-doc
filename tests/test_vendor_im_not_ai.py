"""Pinned bundle completeness and real CLI smoke, without model/network calls.

Local blob evidence: KNUAF_IM_NOT_AI_SRC=<checkout> python3 -B -m unittest
 tests.test_vendor_im_not_ai -v. Update EXPECTED_FILES with each upstream release.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from tests._harness import REPO_ROOT, SCRIPTS

COMMIT = "f37b616c4878b278423ba0f015e7a8f596791fb2"
BUNDLE = SCRIPTS.parent / "references/vendor/im-not-ai"
RUNNER = SCRIPTS / "gg_humanize.py"
# Independent inventory, not derived from the updater or UPSTREAM.json.
REFERENCE_FILES = """ai-tell-taxonomy.md baseline.json baseline_v2.json design-notes.md
 diagnosis-rules.md empirical-validation.md lexicon.json metrics.py metrics_v2.py
 quick-rules.footer.md quick-rules.header.md quick-rules.md rewriting-playbook.md
 roles/diagnostician.md roles/finalizer.md roles/monolith.md scholarship.md
 taxonomy-candidates.md web-service-spec.md""".split()
SCRIPT_COVERAGE = {
    "prepare_monolith_input.py": "prepare",
    "reassemble_chunks.py": "reassemble",
    "verify_change_rate.py": "change-rate",
    "verify_gates.py": "gates",
    "restore_modality.py": "restore-modality",
    "strip_injected_commas.py": "strip-commas",
    "sanitize_text.py": "sanitize",
    "lexical_layer.py": "lexical",
    "checks.py": "imported by gates",
    "console.py": "imported by prepare/gates",
}
EXPECTED_FILES = {"LICENSE", "UPSTREAM.json", "humanize-korean/GUIDE.md"}
EXPECTED_FILES.update("humanize-korean/references/" + p for p in REFERENCE_FILES)
EXPECTED_FILES.update("humanize-korean/scripts/" + p for p in SCRIPT_COVERAGE)


# The tracing wrapper below observes code as it executes, so a script that a
# command never executes is invisible to it. That is only sound while every
# bundled script is reached by a static import: a dynamic loader would run code
# the wrapper cannot predict, and the coverage claim would go silently wrong.
DYNAMIC_LOADING_MODULES = frozenset(
    {"ctypes", "imp", "importlib", "pkgutil", "runpy", "subprocess", "zipimport"})
# Bare builtin calls only: `re.compile` is an attribute, so it is not matched.
DYNAMIC_LOADING_CALLS = frozenset({"__import__", "compile", "eval", "exec"})


def static_import_closure(entry, roots):
    """Files reachable from `entry` by module-level imports alone.

    Imports resolve against the bundle's scripts and references directories, the
    same two sys.path entries `verify_gates.py` installs at runtime.
    """
    reachable, pending = {entry}, [entry]
    while pending:
        for node in ast.walk(ast.parse(pending.pop().read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module.split(".")[0]]
            for name in names:
                for root in roots:
                    candidate = root / f"{name}.py"
                    if candidate.is_file() and candidate not in reachable:
                        reachable.add(candidate)
                        pending.append(candidate)
    return reachable


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def cli(*args, cwd, runner=RUNNER, trace=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if trace is None:
        command = [sys.executable, "-B", str(runner), *map(str, args)]
    else:
        # Observe executed code, including imports, without changing runtime code.
        wrapper = """import json, pathlib, runpy, sys
seen = set()
def profile(frame, event, arg):
    if event == 'call': seen.add(frame.f_code.co_filename)
trace = pathlib.Path(sys.argv.pop(1))
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.setprofile(profile)
try:
    runpy.run_path(script, run_name='__main__')
finally:
    sys.setprofile(None)
    trace.write_text(json.dumps(sorted(seen)), encoding='utf-8')
"""
        command = [sys.executable, "-B", "-c", wrapper, str(trace), str(runner), *map(str, args)]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=90)


class BundleContractTests(unittest.TestCase):
    def test_inventory_hashes_license_and_no_nested_skill_or_links(self):
        files = snapshot(BUNDLE)
        self.assertEqual(EXPECTED_FILES, set(files))
        self.assertFalse(any(p.is_symlink() for p in BUNDLE.rglob("*")))
        self.assertFalse(any(p.name == "SKILL.md" for p in BUNDLE.rglob("*")))
        manifest = json.loads((BUNDLE / "UPSTREAM.json").read_text(encoding="utf-8"))
        self.assertEqual(set(files) - {"UPSTREAM.json"}, set(manifest["files"]))
        for name, entry in manifest["files"].items():
            self.assertEqual(entry["sha256"], files[name], name)
        self.assertEqual(COMMIT, manifest["commit"])
        self.assertEqual("2.4.5", manifest["version"])
        self.assertEqual("v2.4.5", manifest["tag"])
        license_text = (BUNDLE / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 epoko77-ai", license_text)

    def test_nonstdlib_imports_and_instruction_paths_resolve(self):
        root = BUNDLE / "humanize-korean"
        local = {p.stem for directory in (root / "scripts", root / "references")
                 for p in directory.glob("*.py")}
        for path in (root / "scripts").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    self.assertIn(name, sys.stdlib_module_names | local, (path.name, name))
        documents = [root / "GUIDE.md", *(root / "references/roles").glob("*.md"),
                     *(root / "references").glob("quick-rules*.md")]
        for path in documents:
            for target in re.findall(r"(?:scripts|references)/[A-Za-z0-9_./-]+\.(?:py|md|json)\b",
                                     path.read_text(encoding="utf-8")):
                self.assertTrue((root / target).exists(), (path.name, target))

    def test_gates_import_closure_is_static_and_traced_elsewhere(self):
        # The smoke test stops tracing the heavy `gates` call because tracing costs
        # ~3x and that call is what blew the 90s child timeout. That trade is only
        # sound if gates executes exactly its static import closure at any input
        # size — a dynamic loader would run code the tracing wrapper cannot see, and
        # the coverage assertion would keep passing while a script went unobserved.
        root = BUNDLE / "humanize-korean"
        roots = (root / "scripts", root / "references")
        for path in sorted({p for directory in roots for p in directory.glob("*.py")}):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                    self.assertFalse(DYNAMIC_LOADING_MODULES & set(names), path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0],
                                     DYNAMIC_LOADING_MODULES, path.name)
                elif isinstance(node, ast.Name):
                    self.assertNotIn(node.id, DYNAMIC_LOADING_CALLS, path.name)
        # gates is the one untraced call; light/standard gates runs trace it, so the
        # files it opens are already in `seen` whatever the heavy input does.
        # `restore_modality` is imported lazily inside judge_modality (:379), which
        # still counts: the closure walk covers nested imports by design.
        closure = {p.relative_to(root).as_posix()
                   for p in static_import_closure(root / "scripts/verify_gates.py", roots)}
        self.assertEqual({"scripts/verify_gates.py", "scripts/checks.py",
                          "scripts/console.py", "scripts/restore_modality.py",
                          "references/metrics_v2.py", "references/metrics.py"}, closure)
        self.assertTrue(all(p.name in SCRIPT_COVERAGE for p in
                            static_import_closure(root / "scripts/verify_gates.py", roots)
                            if p.parent == root / "scripts"),
                        "every scripts/ file gates runs must be tracked by SCRIPT_COVERAGE")

    def test_polishing_paths_commands_and_platform_launchers(self):
        doc = (SCRIPTS.parent / "references/korean-polishing.md").read_text(encoding="utf-8")
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        guide = "references/vendor/im-not-ai/humanize-korean/GUIDE.md"
        self.assertIn(guide, doc)
        self.assertTrue((SCRIPTS.parent / guide).is_file())
        self.assertTrue(RUNNER.is_file())
        self.assertIn("py -3", skill)
        self.assertIn('py -3 "<이 스킬 루트>\\scripts\\gg_humanize.py"', doc)
        self.assertIn('python3 "<이 스킬 루트>/scripts/gg_humanize.py"', doc)
        self.assertNotRegex(doc, r"(?:`|\n)python\s+[^\n]*gg_humanize\.py")
        table_commands = set()
        for line in doc.splitlines():
            if line.startswith("|"):
                table_commands.update(re.findall(r"gg_humanize\.py ([a-z-]+)", line))
        self.assertEqual({"paths", "new-run", *list(SCRIPT_COVERAGE.values())[:8]}, table_commands)


class RunnerSmokeTests(unittest.TestCase):
    def test_windows_name_surrogates_reject_but_cloud_placeholders_pass(self):
        spec = importlib.util.spec_from_file_location("humanize_guard", RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        for tag in (0xA000000C, 0xA0000003):
            with self.subTest(redirecting_tag=hex(tag)):
                info = SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=tag,
                                       st_file_attributes=0x400)
                self.assertTrue(runner.is_path_redirect(info))
        for tag in (0, 0x9000001A, 0x9000101A, 0x80000013):
            with self.subTest(nonredirecting_tag=hex(tag)):
                info = SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=tag,
                                       st_file_attributes=0x400)
                self.assertFalse(runner.is_path_redirect(info))
        self.assertTrue(runner.is_path_redirect(SimpleNamespace(st_mode=stat.S_IFLNK)))

    def test_routes_postprocessors_coverage_and_write_scope(self):
        bundle_before = snapshot(BUNDLE)
        with tempfile.TemporaryDirectory(prefix="humanize-smoke-") as tmp:
            root = Path(tmp).resolve()
            work = root / "논문 작업"
            work.mkdir()
            outside = root / "outside.txt"
            outside.write_bytes(b"unchanged")
            seen = set()
            counter = 0

            def run(*args, codes=(0,), trace=True):
                nonlocal counter
                counter += 1
                path = work / f"trace-{counter}.json"
                proc = cli(*args, cwd=work, trace=path if trace else None)
                self.assertIn(proc.returncode, codes, proc.stdout + proc.stderr)
                if trace:
                    seen.update(Path(p).name
                                for p in json.loads(path.read_text(encoding="utf-8"))
                                if "/humanize-korean/scripts/" in p.replace("\\", "/"))
                return proc

            payload = json.loads(run("paths").stdout)
            self.assertEqual("ok", payload["status"])
            self.assertEqual(COMMIT, payload["upstream"]["commit"])
            self.assertEqual(str(BUNDLE / "humanize-korean"), payload["skill_root"])
            runs = []
            for mode in ("light", "standard", "heavy"):
                created = json.loads(run("new-run", "--root", work, "--tag-hint", "smoke").stdout)
                run_dir = Path(created["run_dir"])
                runs.append(run_dir)
                self.assertEqual(work.resolve() / ".gg-work/humanize/_workspace", run_dir.parent)
                text = "나는 밭에서 흙을 만졌다. 오늘은 비가 내려 집에서 쉬었다.\n"
                if mode == "heavy":
                    text = "\n\n".join(f"{i}번째 기록이다. " + text * 10 for i in range(50))
                    self.assertGreater(len(text), 15000)
                source = run_dir / "01_input.txt"
                source.write_text(text, encoding="utf-8")
                run("prepare", "--run-dir", run_dir, "--genre", "essay")
                metrics = json.loads((run_dir / "00_metrics.json").read_text(encoding="utf-8"))
                self.assertIn(metrics["route_hint"], {"light", "standard", "heavy"})
                if mode == "light":
                    self.assertEqual("light", metrics["route_hint"])
                if mode != "light":
                    diagnosis = run_dir / "02_diagnosis.md"
                    diagnosis.write_text("합성 진단: 사실과 수치를 보존한다.", encoding="utf-8")
                    args = ["prepare", "--run-dir", run_dir, "--genre", "essay",
                            "--diagnosis", diagnosis]
                    if mode == "heavy":
                        args.append("--chunk")
                    run(*args)
                    if mode == "standard":
                        self.assertIn("합성 진단", (run_dir / "01_input_with_metrics.txt")
                                      .read_text(encoding="utf-8"))
                final = source
                if mode == "heavy":
                    manifest = json.loads((run_dir / "chunk_manifest.json").read_text(encoding="utf-8"))
                    self.assertEqual("heavy", manifest["route_hint"])
                    self.assertGreater(manifest["body_chunk_count"], 1)
                    prepared = source.read_text(encoding="utf-8")
                    for entry in manifest["chunks"]:
                        if not entry["passthrough"]:
                            (run_dir / entry["rewritten_file"]).write_text(
                                prepared[entry["start"]:entry["end"]], encoding="utf-8")
                    run("reassemble", "--run-dir", run_dir, "--strict")
                    final = run_dir / "03_reassembled.md"
                    self.assertEqual(prepared, final.read_text(encoding="utf-8"))
                # The heavy input makes gates the one expensive call: profiling it
                # costs ~3x the untraced run and is what pushed this test past its
                # 90s child timeout on Python 3.10 CI. gates executes the same static
                # import closure at every input size (verified below), so the files it
                # would contribute are already in `seen` from the light/standard runs.
                # Its assertions below still run on the real subprocess result.
                gates = run("gates", "--before", source, "--after", final,
                            "--genre", "essay", "--json", codes=(0, 1),
                            trace=mode != "heavy")
                report = json.loads(gates.stdout[gates.stdout.index("{\n"):])
                self.assertEqual(gates.returncode, report["gate"]["exit_code"])
                self.assertEqual(0, report["change_rate"]["rate"])
                self.assertEqual([], report["golden"])
            self.assertEqual(3, len(set(runs)))
            run_dir = runs[0]
            before, after, output = [run_dir / name for name in ("before.txt", "after.txt", "out.txt")]
            original = "수익은 낮은 것으로 판단된다."
            before.write_text(original, encoding="utf-8")
            after.write_text("수익은 낮은 수치다.", encoding="utf-8")
            run("restore-modality", "--before", before, "--after", after, "--out", output, "--json")
            self.assertEqual(original, output.read_text(encoding="utf-8"))
            before.write_text("밥을 먹고 집으로 갔다.", encoding="utf-8")
            after.write_text("밥을 먹고, 천천히 집으로 갔다.", encoding="utf-8")
            run("strip-commas", "--before", before, "--after", after, "--out", output, "--json")
            self.assertEqual("밥을 먹고 천천히 집으로 갔다.", output.read_text(encoding="utf-8"))
            before.write_text(original, encoding="utf-8")
            after.write_text(original, encoding="utf-8")
            run("lexical", "restore", "--before", before, "--after", after, "--out", output, "--json")
            self.assertEqual(original, output.read_text(encoding="utf-8"))
            run("change-rate", "--before", before, "--after", after)
            after.write_text("한\u200b글\u00a0문장", encoding="utf-8")
            run("sanitize", after, "--in-place")
            self.assertEqual("한글 문장", after.read_text(encoding="utf-8"))
            self.assertEqual(set(SCRIPT_COVERAGE), seen & set(SCRIPT_COVERAGE))
            self.assertEqual({"outside.txt", "논문 작업"}, {p.name for p in root.iterdir()})
            self.assertEqual(b"unchanged", outside.read_bytes())
        self.assertEqual(bundle_before, snapshot(BUNDLE))

    def test_missing_or_changed_copy_never_executes_prepare(self):
        with tempfile.TemporaryDirectory(prefix="humanize-branches-") as tmp:
            root = Path(tmp).resolve()
            runner = root / "scripts/gg_humanize.py"
            runner.parent.mkdir()
            shutil.copyfile(RUNNER, runner)
            copied = root / "references/vendor/im-not-ai"
            shutil.copytree(BUNDLE, copied)
            target = copied / "humanize-korean/scripts/prepare_monolith_input.py"
            original = target.read_bytes()
            for missing, code, status in ((True, 3, "bundle_missing"),
                                          (False, 4, "bundle_integrity_failed")):
                if missing:
                    target.unlink()
                else:
                    target.write_bytes(b"raise RuntimeError('must not run')\n" + original)
                for command in ("paths", "prepare"):
                    proc = cli(command, "--text", "실행 금지", cwd=root, runner=runner) if command == "prepare" else cli(command, cwd=root, runner=runner)
                    self.assertEqual(code, proc.returncode, proc.stdout + proc.stderr)
                    payload = json.loads(proc.stdout)
                    self.assertEqual(status, payload["status"])
                    self.assertEqual("humanize-korean/scripts/prepare_monolith_input.py", payload["first_path"])
                self.assertFalse((root / "_workspace").exists())

    def assert_guard_rejected(self, root, *args, runner=RUNNER):
        trace = root / "rejected-trace.json"
        proc = cli(*args, cwd=root, runner=runner, trace=trace)
        self.assertEqual(5, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(proc.stdout.splitlines()), proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual("run_guard_rejected", payload["status"])
        self.assertTrue(payload["reason"])
        executed = json.loads(trace.read_text(encoding="utf-8"))
        self.assertFalse(any("/humanize-korean/" in p.replace("\\", "/") for p in executed))

    def make_link(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.fail("Symlink regression requires Windows Developer Mode or link privilege; "
                      f"no skip/fake pass is permitted: {exc}")

    def test_outside_outputs_inputs_traversal_and_unknown_options_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="humanize-guard-") as tmp:
            root = Path(tmp).resolve()
            run = root / ".gg-work/humanize/_workspace/guard"
            run.mkdir(parents=True)
            source = run / "01_input.txt"
            source.write_text("밥을 먹고, 집에 갔다.", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("외부 원문 보존", encoding="utf-8")
            second = run.parent / "second"
            second.mkdir()
            other = second / "other.txt"
            other.write_text("다른 실행", encoding="utf-8")
            cases = [
                ("strip-commas", "--before", source, "--after", source, "--out", outside),
                ("restore-modality", "--before", source, "--after", source, "--out", outside),
                ("lexical", "restore", "--before", source, "--after", source, "--out", outside),
                ("lexical", "hints", "--input", outside),
                ("sanitize", outside, "--in-place"),
                ("gates", "--before", source, "--after", other),
                ("change-rate", "--before", source, "--after", outside),
                ("prepare", "--run-dir", run, "--diagnosis", outside),
                ("prepare", "--run-dir", run, "--baseline", outside),
                ("prepare", "--text", "자동 생성 금지"),
                ("prepare", "--run-dir", run / ".." / run.name),
                ("prepare", "--run-dir", run, "--unknown-path", outside),
                ("strip-commas", "--before", source, "--after", source,
                 "--out=" + str(run / ".." / "escaped.txt")),
                ("reassemble", "--run-dir", run, "--output", outside),
                ("reassemble", "--run-dir", run, "--report", "../escaped.json"),
            ]
            for args in cases:
                with self.subTest(args=args):
                    self.assert_guard_rejected(root, *args)
            self.assertEqual("외부 원문 보존", outside.read_text(encoding="utf-8"))
            self.assertEqual({"01_input.txt"}, {p.name for p in run.iterdir()})
            self.assertFalse((root / "_workspace").exists())

    def test_linked_input_fixed_output_and_all_workspace_components_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="humanize-links-") as tmp:
            root = Path(tmp).resolve()
            run = root / ".gg-work/humanize/_workspace/guard"
            run.mkdir(parents=True)
            outside = root / "outside.txt"
            original = "한\u200b글 원문"
            outside.write_text(original, encoding="utf-8")
            source = run / "01_input.txt"
            self.make_link(source, outside)
            self.assert_guard_rejected(root, "prepare", "--run-dir", run)
            self.assertEqual(original, outside.read_text(encoding="utf-8"))
            source.unlink()
            source.write_text("일반 문장", encoding="utf-8")
            hardlink = run / "hardlink.txt"
            os.link(outside, hardlink)
            self.assert_guard_rejected(root, "sanitize", hardlink, "--in-place")
            hardlink.unlink()
            self.assertEqual(original, outside.read_text(encoding="utf-8"))
            for name in ("00_metrics.json", "01_input_with_metrics.txt", "nested"):
                with self.subTest(name=name):
                    link = run / name
                    self.make_link(link, outside)
                    self.assert_guard_rejected(root, "prepare", "--run-dir", run)
                    link.unlink()
                    self.assertEqual(original, outside.read_text(encoding="utf-8"))
            for level in range(3):
                work = root / f"work-{level}"
                target = root / f"target-{level}"
                work.mkdir()
                target.mkdir()
                relative = (".gg-work", "humanize", "_workspace")[:level + 1]
                link = work.joinpath(*relative)
                link.parent.mkdir(parents=True, exist_ok=True)
                self.make_link(link, target, directory=True)
                self.assert_guard_rejected(root, "new-run", "--root", work)
                self.assertEqual([], list(target.iterdir()))

    def test_bundle_as_run_and_linked_inventory_or_bundle_root_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="humanize-bundle-guard-") as tmp:
            root = Path(tmp).resolve()
            runner = root / "scripts/gg_humanize.py"
            runner.parent.mkdir()
            shutil.copyfile(RUNNER, runner)
            copied = root / "references/vendor/im-not-ai"
            shutil.copytree(BUNDLE, copied)
            before = snapshot(copied)
            self.assert_guard_rejected(root, "prepare", "--run-dir", copied,
                                       "--text", "동봉 폴더 쓰기 금지", runner=runner)
            self.assertEqual(before, snapshot(copied))
            manifest = copied / "UPSTREAM.json"
            external = root / "manifest.json"
            manifest.rename(external)
            self.make_link(manifest, external)
            for command in ("paths", "prepare"):
                proc = cli(command, cwd=root, runner=runner)
                self.assertEqual(4, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual("bundle_integrity_failed", json.loads(proc.stdout)["status"])
                self.assertEqual("UPSTREAM.json", json.loads(proc.stdout)["first_path"])
            manifest.unlink()
            external.rename(manifest)
            for component in (copied, copied.parent, copied.parent.parent):
                target = root / "moved-component"
                component.rename(target)
                self.make_link(component, target, directory=True)
                proc = cli("paths", cwd=root, runner=runner)
                self.assertEqual(4, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual("bundle_integrity_failed", json.loads(proc.stdout)["status"])
                component.unlink()
                target.rename(component)
            self.assertEqual(before, snapshot(copied))

    def test_reassembly_manifest_cannot_read_outside_run(self):
        with tempfile.TemporaryDirectory(prefix="humanize-manifest-guard-") as tmp:
            root = Path(tmp).resolve()
            run = root / ".gg-work/humanize/_workspace/guard"
            run.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("외부", encoding="utf-8")
            for field in ("source_file", "rewritten_file"):
                manifest = {"source_file": "01_input.txt", "chunks": [
                    {"rewritten_file": "02_chunk_01_rewritten.txt", "passthrough": False}]}
                if field == "source_file":
                    manifest[field] = str(outside)
                else:
                    manifest["chunks"][0][field] = "../outside.txt"
                (run / "chunk_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                self.assert_guard_rejected(root, "reassemble", "--run-dir", run)
            self.assertFalse((run / "03_reassembled.md").exists())
            self.assertEqual("외부", outside.read_text(encoding="utf-8"))


# No skip is registered in CI; local checkout evidence opts in to this class.
if os.environ.get("KNUAF_IM_NOT_AI_SRC"):
    class UpstreamBlobTests(unittest.TestCase):
        def test_selected_paths_and_bytes_match_pinned_git_blobs(self):
            src = Path(os.environ["KNUAF_IM_NOT_AI_SRC"])
            def git(*args):
                return subprocess.check_output(["git", "-C", str(src), *args])
            self.assertEqual(COMMIT, git("rev-parse", "HEAD").decode().strip())
            paths = set(git("ls-tree", "-r", "--name-only", COMMIT,
                            "skills/humanize-korean/references").decode().splitlines())
            paths.update("scripts/" + name for name in SCRIPT_COVERAGE)
            paths.update({"LICENSE", "codex/skills/humanize-korean/SKILL.md"})
            manifest = json.loads((BUNDLE / "UPSTREAM.json").read_text(encoding="utf-8"))
            self.assertEqual(paths, {entry["upstream_path"] for entry in manifest["files"].values()})
            for name, entry in manifest["files"].items():
                self.assertEqual(git("show", COMMIT + ":" + entry["upstream_path"]),
                                 (BUNDLE / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
