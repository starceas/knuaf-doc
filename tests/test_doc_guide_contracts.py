"""Executable contract tests for the command index (P0-2 PR-A).

``references/commands.md`` is the document SKILL.md tells agents to use
instead of reading the implementation.  These cases run ``gg.py`` the way
that document describes it and assert on the command results:

- the command table and the real CLI name the same commands and flags;
- documented required arguments are the ones the CLI actually refuses
  to run without, and "no required argument" commands really run;
- the apply example (major binding) commits, re-runs idempotently and
  produces the binding ``major-plan`` reports;
- ``init`` uses the selected folder itself and leaves existing files
  byte-identical (U01);
- no shipped skill file carries a forbidden real-name identifier (#58
  anonymization).  The identifiers are stored only as salted SHA-256
  digests, never as plain text.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
import unittest

from tests._harness import SCRIPTS, runtime

COMMANDS_MD = SCRIPTS.parent / "references" / "commands.md"

# Flags whose absence is reported as a held/blocked code instead of the
# flag name.
HELD_CODES = {
    "--major": "major_id_required",
    "--offline-confirmed": "offline_confirmation_required",
}
STORE_TRUE = {"--offline-confirmed"}
# Required flags the CLI checks only after it has read the other inputs
# (a real file is needed first), so a missing-flag run cannot isolate them.
# The --major guard is exercised end to end by the apply example below.
CHECKED_AFTER_INPUT = {
    ("adopt-output", "--major"),
    ("paper", "--major"),
    ("finance-tables", "--major"),
    ("rda-apply", "--target-current"),
}

SKILL_ROOT = SCRIPTS.parent
# Real names removed by #58.  SHA-256 of FORBIDDEN_SALT + the NFC name;
# both are FORBIDDEN_LENGTH Hangul syllables.  Never add plain names here.
FORBIDDEN_SALT = "knuaf-doc/forbidden-identifier/1\0"
FORBIDDEN_LENGTH = 3
FORBIDDEN_DIGESTS = frozenset({
    "0a0d84149ddd1aea3ccc38dc7750486b557f5db56ae3b33bf58ee916ba3a4948",
    "4f35269626a6071939ca912a3289b0281bbef0ff531c5e1b6d09913f5b0cba59",
})
_HANGUL_RUN = re.compile(r"[\uac00-\ud7a3]+")
# Separators a name can be split with and still read as the same name.
_NAME_SEPARATORS = re.compile(r"[\s\u00b7\u200b-\u200d\u2060\ufeff]+")
_JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _identifier_digest(text):
    return hashlib.sha256((FORBIDDEN_SALT + text).encode("utf-8")).hexdigest()


def forbidden_hits(text, digests=FORBIDDEN_DIGESTS,
                   length=FORBIDDEN_LENGTH):
    """Digest prefixes of forbidden identifiers found in ``text``.

    JSON ``\\uXXXX`` escapes are decoded, the text is NFC-normalized and
    whitespace/zero-width/middle-dot separators are removed, then every
    ``length``-syllable Hangul window is hashed.  Only digest prefixes are
    returned so a failure never prints the identifier."""
    text = _JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _NAME_SEPARATORS.sub("", unicodedata.normalize("NFC", text))
    hits = set()
    for run in _HANGUL_RUN.findall(text):
        for i in range(len(run) - length + 1):
            digest = _identifier_digest(run[i:i + length])
            if digest in digests:
                hits.add(digest[:12])
    return sorted(hits)


def shipped_text_files(root=SKILL_ROOT):
    """Every UTF-8 file of the installed skill tree (guidance, references,
    scripts, data); bytecode caches are not shipped."""
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue


def _env():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *args],
        capture_output=True, text=True, env=_env(), cwd=cwd)


def _doc():
    return COMMANDS_MD.read_text(encoding="utf-8")


def _table():
    """{command: {"required": [flags], "folderless": bool}} from the
    "gg.py 명령 표" section of the doc."""
    text = _doc()
    start = text.index("## gg.py 명령 표")
    end = text.find("\n## ", start + 1)
    rows = {}
    for line in text[start:end if end != -1 else None].splitlines():
        m = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|([^|]*)\|", line)
        if not m:
            continue
        cell = m.group(2)
        rows[m.group(1)] = {
            "required": re.findall(r"`(--[a-z-]+)", cell),
            "folderless": "폴더 없음" in cell,
        }
    return rows


def _cli_surface():
    """(commands, flags) declared by gg.py's argparse."""
    usage = _cli("--help").stdout
    commands = set(re.search(r"\{([a-z,-]+)\}", usage).group(1).split(","))
    flags = set(re.findall(r"(--[a-z][a-z-]*)", usage)) - {"--help"}
    return commands, flags


def _block(text, lang, start):
    m = re.compile(r"```%s\n(.*?)```" % re.escape(lang), re.S).search(
        text, start)
    return m.group(1), m.end()


def _payload(proc):
    return json.loads(proc.stdout)


class CommandIndexMatchesCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-commands-doc-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "project"
        runtime("gg_core").init(self.root)
        from tests._harness import bind_major
        bind_major(self.root)

    def test_table_lists_exactly_the_cli_commands_and_real_flags(self):
        table = _table()
        commands, flags = _cli_surface()
        self.assertEqual(commands, set(table))
        documented = set(re.findall(r"`(--[a-z][a-z-]*)", _doc()))
        self.assertEqual(set(), documented - flags)

    def test_folderless_commands_are_the_ones_that_run_without_a_folder(self):
        for cmd, row in _table().items():
            with self.subTest(cmd=cmd):
                proc = _cli(cmd, cwd=self.tmp)
                if row["folderless"]:
                    self.assertEqual(2, proc.returncode,
                                     proc.stdout + proc.stderr)
                    self.assertEqual("blocked", _payload(proc)["status"])
                else:
                    self.assertNotEqual(0, proc.returncode)
                    self.assertIn("folder 필요", proc.stdout + proc.stderr)

    def _run(self, cmd, row, flags):
        args = [cmd] if row["folderless"] else [cmd, str(self.root)]
        for flag in flags:
            args.append(flag)
            if flag not in STORE_TRUE:
                args.append("0" if flag == "--expected-revision" else "x")
        return _cli(*args, cwd=self.tmp)

    def test_each_documented_required_flag_is_refused_when_missing(self):
        before = (self.root / "project.json").read_bytes()
        for cmd, row in _table().items():
            for missing in row["required"]:
                if (cmd, missing) in CHECKED_AFTER_INPUT:
                    continue
                others = [f for f in row["required"] if f != missing]
                with self.subTest(cmd=cmd, missing=missing):
                    proc = self._run(cmd, row, others)
                    self.assertEqual(2, proc.returncode,
                                     proc.stdout + proc.stderr)
                    reason = json.dumps(_payload(proc), ensure_ascii=False)
                    self.assertTrue(
                        missing in reason
                        or HELD_CODES.get(missing, missing) in reason,
                        "%s without %s: %s" % (cmd, missing, reason))
        self.assertEqual(before, (self.root / "project.json").read_bytes())

    def test_commands_documented_without_required_flags_run(self):
        for cmd, row in _table().items():
            if row["required"] or cmd == "init":
                continue
            with self.subTest(cmd=cmd):
                proc = self._run(cmd, row, [])
                payload = _payload(proc)
                if cmd == "insect-plan":
                    # Documented as industrial_insects-only; this project
                    # is bound to specialty_crops.
                    self.assertEqual(2, proc.returncode)
                    self.assertIn("industrial_insects", payload["reason"])
                elif cmd == "check":
                    self.assertIn(proc.returncode, (0, 1))
                    self.assertIsInstance(payload, list)
                else:
                    self.assertEqual(0, proc.returncode,
                                     proc.stdout + proc.stderr)


class ApplyExampleRunsAsDocumented(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-apply-doc-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "project"

    def _example(self):
        text = _doc()
        start = text.index("### 예제: 새 작업폴더에 전공 바인딩 등록")
        answer, pos = _block(text, "text", start)
        change, pos = _block(text, "json", pos)
        script, _ = _block(text, "sh", pos)
        return answer, json.loads(change), script

    def _argv(self, line, change_path):
        argv = []
        for token in shlex.split(line):
            if token == "python3":
                argv += [sys.executable, "-B"]
            elif token == "<스킬>/scripts/gg.py":
                argv.append(str(SCRIPTS / "gg.py"))
            elif token == "<폴더>":
                argv.append(str(self.root))
            elif token == "bind-major.json":
                argv.append(str(change_path))
            else:
                argv.append(token)
        return argv

    def test_bind_major_example_commits_and_binds(self):
        answer, change, script = self._example()
        source = next(op["value"] for op in change["ops"]
                      if op["collection"] == "sources")
        fact = next(op["value"] for op in change["ops"]
                    if op["collection"] == "facts")
        answer_path = self.root / source["path"]
        answer_path.parent.mkdir(parents=True)
        answer_path.write_text(answer, encoding="utf-8")
        change_path = self.tmp / "bind-major.json"
        change_path.write_text(json.dumps(change, ensure_ascii=False),
                               encoding="utf-8")

        results = {}
        for line in script.strip().splitlines():
            argv = self._argv(line, change_path)
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  env=_env(), cwd=self.tmp)
            self.assertEqual(0, proc.returncode,
                             line + "\n" + proc.stdout + proc.stderr)
            results[argv[3]] = _payload(proc)

        self.assertEqual(1, results["apply"]["revision"])
        module = runtime("gg_major_contract").default_registry().resolve(
            fact["value"])
        self.assertEqual(module.module_version, fact["module_version"])
        binding = results["major-plan"]["binding"]
        self.assertEqual(fact["value"], binding["major_id"])
        self.assertEqual(module.module_version, binding["module_version"])
        self.assertEqual(answer, answer_path.read_text(encoding="utf-8"))

        # Same request id and content: no new revision.
        canonical = (self.root / "project.json").read_bytes()
        again = _cli("apply", str(self.root), "--change", str(change_path),
                     "--expected-revision", "0")
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)
        self.assertEqual(1, _payload(again)["revision"])
        self.assertEqual(canonical, (self.root / "project.json").read_bytes())

        # The claim_review blocker the document says remains.
        rows = _payload(_cli("check", str(self.root)))
        self.assertIn(("claim_review", fact["id"], "blocked"),
                      {(r["check_id"], r["target"], r["status"])
                       for r in rows})

        # Major contract: --major is required and must match the binding.
        for args, reason in (
            ((), "major_id_required"),
            (("--major", "fruit_trees"), "major_binding_mismatch"),
        ):
            with self.subTest(args=args):
                proc = _cli("export", str(self.root), "--kind", "draft",
                            *args)
                self.assertEqual(2, proc.returncode)
                self.assertEqual(reason, _payload(proc)["reason"])

    def test_change_with_unknown_collection_is_refused_without_commit(self):
        runtime("gg_core").init(self.root)
        change_path = self.tmp / "bad.json"
        change_path.write_text(json.dumps({
            "request_id": "bad", "ops": [
                {"collection": "notes", "value": {"id": "n1"}}]}),
            encoding="utf-8")
        before = (self.root / "project.json").read_bytes()
        proc = _cli("apply", str(self.root), "--change", str(change_path),
                    "--expected-revision", "0")
        self.assertEqual(2, proc.returncode)
        self.assertEqual("not_committed", _payload(proc)["commit_state"])
        self.assertEqual(before, (self.root / "project.json").read_bytes())


class InitUsesSelectedFolder(unittest.TestCase):
    """U01: init runs in the folder the user chose, next to their files."""

    def test_init_keeps_existing_files_and_refuses_to_reinitialize(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-init-doc-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "student"
        (root / "자료").mkdir(parents=True)
        files = {"원고.md": "# 초안\n", "자료/답변.txt": "농장명: 합성농장\n"}
        for rel, text in files.items():
            (root / rel).write_text(text, encoding="utf-8")

        proc = _cli("init", str(root))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual({"원고.md", "자료", "project.json", ".gg-lock"},
                         {p.name for p in root.iterdir()})
        for rel, text in files.items():
            self.assertEqual(text, (root / rel).read_text(encoding="utf-8"))

        canonical = (root / "project.json").read_bytes()
        again = _cli("init", str(root))
        self.assertEqual(2, again.returncode)
        self.assertEqual("existing_canonical", _payload(again)["reason"])
        self.assertEqual(canonical, (root / "project.json").read_bytes())


class ForbiddenIdentifiersStayOut(unittest.TestCase):
    """#58: removed real names must not come back into the shipped skill."""

    def test_shipped_skill_files_carry_no_forbidden_identifier(self):
        scanned = set()
        found = {}
        for path, text in shipped_text_files():
            rel = path.relative_to(SKILL_ROOT).as_posix()
            scanned.add(rel)
            hits = forbidden_hits(text)
            if hits:
                found[rel] = hits
        self.assertIn("SKILL.md", scanned)
        self.assertIn("references/guideline-tracking.md", scanned)
        self.assertIn("references/source-contract.md", scanned)
        self.assertEqual({}, found)

    def test_scanner_sees_through_spacing_escapes_and_decomposition(self):
        # Synthetic identifier: proves the normalization without a real name.
        token = "\uac00\ub098\ub2e4"
        digests = frozenset({_identifier_digest(token)})
        hit = [_identifier_digest(token)[:12]]
        decomposed = unicodedata.normalize("NFD", token)
        escaped = "".join("\\u%04x" % ord(c) for c in token)
        for text in (
            "작성자 " + token + " 학생",
            "출처: " + " ".join(token) + ".",
            "\u200b".join(token),
            "\u00b7".join(token),
            decomposed,
            '{"author": "%s"}' % escaped,
        ):
            with self.subTest(text=text.encode("unicode_escape")):
                self.assertEqual(hit, forbidden_hits(text, digests))
        for text in ("", token[:2], token[::-1], "abc " + token[1:]):
            with self.subTest(text=text.encode("unicode_escape")):
                self.assertEqual([], forbidden_hits(text, digests))

    def test_failure_output_never_contains_the_identifier(self):
        token = "\uac00\ub098\ub2e4"
        digests = frozenset({_identifier_digest(token)})
        hits = forbidden_hits("앞 " + token + " 뒤", digests)
        self.assertEqual(1, len(hits))
        self.assertNotIn(token, json.dumps(hits, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
