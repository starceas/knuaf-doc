"""Pinned bundle completeness and real CLI smoke, without model/network calls.

Local blob evidence: KNUAF_IM_NOT_AI_SRC=<checkout> python3 -B -m unittest
 tests.test_vendor_im_not_ai -v. Update EXPECTED_FILES with each upstream release.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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
    def test_routes_postprocessors_coverage_and_write_scope(self):
        bundle_before = snapshot(BUNDLE)
        with tempfile.TemporaryDirectory(prefix="humanize-smoke-") as tmp:
            root = Path(tmp)
            work = root / "논문 작업"
            work.mkdir()
            outside = root / "outside.txt"
            outside.write_bytes(b"unchanged")
            seen = set()
            counter = 0

            def run(*args, codes=(0,)):
                nonlocal counter
                counter += 1
                trace = work / f"trace-{counter}.json"
                proc = cli(*args, cwd=work, trace=trace)
                self.assertIn(proc.returncode, codes, proc.stdout + proc.stderr)
                seen.update(Path(p).name for p in json.loads(trace.read_text(encoding="utf-8"))
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
                gates = run("gates", "--before", source, "--after", final,
                            "--genre", "essay", "--json", codes=(0, 1))
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
            root = Path(tmp)
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
