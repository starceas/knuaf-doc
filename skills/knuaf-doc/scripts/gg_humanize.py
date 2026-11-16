#!/usr/bin/env python3
"""Run only the bundled im-not-ai release; never discover global installations.

Use paths before reading GUIDE.md. Upstream arguments must stay in one run.
Windows: py -3 "<skill>/scripts/gg_humanize.py" <command> ...
macOS/Linux: python3 "<skill>/scripts/gg_humanize.py" <command> ...
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import sys

BUNDLE_ROOT = Path(__file__).resolve().parents[1] / "references/vendor/im-not-ai"
SCRIPT_COMMANDS = {
    "prepare": "prepare_monolith_input.py",
    "reassemble": "reassemble_chunks.py",
    "gates": "verify_gates.py",
    "change-rate": "verify_change_rate.py",
    "restore-modality": "restore_modality.py",
    "strip-commas": "strip_injected_commas.py",
    "lexical": "lexical_layer.py",
    "sanitize": "sanitize_text.py",
}

# Pinned argparse contract of the eight bundled entry points. "run" anchors
# implicit writes; "file" is cwd-relative; "run-file" is run-dir-relative.
# All other options are explicitly scalar values or switches, never paths.
PATH_ARGUMENTS = {
    "prepare": {"--run-dir": "run", "--baseline": "file", "--diagnosis": "file"},
    "reassemble": {"--run-dir": "run", "--output": "run-file", "--report": "run-file"},
    "gates": {"--before": "file", "--after": "file"},
    "change-rate": {"--before": "file", "--after": "file"},
    "restore-modality": {"--before": "file", "--after": "file", "--out": "file"},
    "strip-commas": {"--before": "file", "--after": "file", "--out": "file"},
    "lexical": {"--input": "file", "--before": "file", "--after": "file", "--out": "file"},
    "sanitize": {"path": "file"},
}
VALUE_ARGUMENTS = {
    "prepare": ("--text", "--session-tag", "--genre"),
    "gates": ("--genre",), "lexical": ("--genre",),
}
SWITCH_ARGUMENTS = {
    "prepare": ("--chunk", "--no-sanitize"), "reassemble": ("--strict",),
    "gates": ("--json", "--ignore-markup"), "change-rate": ("--ignore-markup",),
    "restore-modality": ("--json",), "strip-commas": ("--json", "--all"),
    "lexical": ("--json",), "sanitize": ("--in-place", "--report"),
}


class GuardError(ValueError):
    """A rejected invocation; never enter bundled code after this error."""


class GuardParser(argparse.ArgumentParser):
    def error(self, message):
        raise GuardError(message)


def absolute_path(value: str | Path) -> Path:
    raw = str(value)
    if not raw or ".." in re.split(r"[/\\]", raw):
        raise GuardError(f"parent traversal or empty path: {raw}")
    if os.name != "nt" and "\\" in raw:
        raise GuardError(f"foreign path separator: {raw}")
    path = Path(raw)
    if path.drive and not path.is_absolute():
        raise GuardError(f"drive-relative path: {raw}")
    # Disallow Windows alternate data streams as well as drive spellings on POSIX.
    if any(":" in part for part in path.parts[1:] if path.is_absolute()) or (
            not path.is_absolute() and ":" in raw):
        raise GuardError(f"unsupported path spelling: {raw}")
    return path if path.is_absolute() else Path.cwd() / path


def is_path_redirect(info: os.stat_result) -> bool:
    # Name-surrogate tags redirect lookup (symlinks/junctions/mount points).
    # Cloud placeholders and deduplication tags do not: permit OneDrive folders.
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_reparse_tag", 0) & 0x20000000))


def check_links(path: Path) -> None:
    """Inspect before resolve: resolving first would erase the evidence."""
    for node in (*reversed(path.parents), path):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if is_path_redirect(info):
            raise GuardError(f"linked path component: {node}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise GuardError(f"hard-linked file: {node}")
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise GuardError(f"noncanonical path: {path}")


def checked_path(value: str | Path) -> Path:
    path = absolute_path(value)
    check_links(path)
    if path.is_relative_to(BUNDLE_ROOT.resolve()):
        raise GuardError(f"path inside bundle: {path}")
    return path


def run_for(path: Path) -> Path:
    for parent in (path, *path.parents):
        if [os.path.normcase(p.name) for p in
                (parent.parent.parent.parent, parent.parent.parent, parent.parent)] == [
                    ".gg-work", "humanize", "_workspace"]:
            if not parent.is_dir():
                raise GuardError(f"run directory missing: {parent}")
            return parent
    raise GuardError(f"outside .gg-work/humanize/_workspace/<run_id>: {path}")


def guard_arguments(command: str, argv: list[str]) -> None:
    parser = GuardParser(prog=f"gg_humanize.py {command}", allow_abbrev=False)
    for option in PATH_ARGUMENTS[command]:
        parser.add_argument(option, **({"nargs": "?"} if option == "path" else {}))
    for option in VALUE_ARGUMENTS.get(command, ()):
        parser.add_argument(option)
    for option in SWITCH_ARGUMENTS[command]:
        parser.add_argument(option, action="store_true")
    if command == "lexical":
        parser.add_argument("mode", choices=("hints", "restore"))
    args = vars(parser.parse_args(argv))
    required = {
        "prepare": ("run_dir",), "reassemble": ("run_dir",),
        "gates": ("before", "after"), "change-rate": ("before", "after"),
        "restore-modality": ("before", "after"), "strip-commas": ("before", "after"),
        "sanitize": ("path",),
        "lexical": ("input",) if args.get("mode") == "hints" else ("before", "after"),
    }[command]
    if any(not args.get(name) for name in required):
        raise GuardError(f"required paths missing: {', '.join(required)}; use new-run first")
    run = None
    for option, kind in PATH_ARGUMENTS[command].items():
        value = args[option.lstrip("-").replace("-", "_")]
        if value is None or kind == "run-file":
            continue
        path = checked_path(value)
        candidate = run_for(path)
        if kind == "run" and path != candidate:
            raise GuardError(f"--run-dir must name the run itself: {path}")
        if run is not None and candidate != run:
            raise GuardError(f"paths span multiple runs: {path}")
        run = candidate
    if run is None:
        raise GuardError("a path in an existing run directory is required")
    # prepare writes fixed names, sanitizes input, and deletes stale chunks.
    # Check the entire existing tree, including dangling links, before execution.
    for directory, dirs, files in os.walk(run, followlinks=False):
        for name in (*dirs, *files):
            check_links(Path(directory) / name)
    if command == "reassemble":
        for value in (args["output"] or "03_reassembled.md",
                      args["report"] or "03_reassembly_report.json"):
            absolute_path(value)  # Check raw Windows drive spelling before joining.
            target = checked_path(run / value)
            if not target.is_relative_to(run):
                raise GuardError(f"output outside run: {value}")
        # The manifest supplies additional input paths, not just CLI arguments.
        manifest = json.loads((run / "chunk_manifest.json").read_text(encoding="utf-8"))
        names = [manifest["source_file"], *(c["rewritten_file"] for c in manifest["chunks"]
                                         if not c["passthrough"])]
        for name in names:
            if not isinstance(name, str):
                raise GuardError("manifest path must be a string")
            absolute_path(name)
            if not checked_path(run / name).is_relative_to(run):
                raise GuardError(f"manifest path outside run: {name}")


def bundle_paths() -> tuple[dict, int]:
    root = BUNDLE_ROOT / "humanize-korean"
    result = {"status": "ok", "bundle_root": str(BUNDLE_ROOT),
              "skill_root": str(root), "guide": str(root / "GUIDE.md"),
              "quick_rules": str(root / "references/quick-rules.md"),
              "diagnosis_rules": str(root / "references/diagnosis-rules.md"),
              "roles": {name: str(root / f"references/roles/{name}.md")
                        for name in ("diagnostician", "monolith", "finalizer")},
              "scripts_dir": str(root / "scripts")}

    def failed(status: str, path: str, code: int) -> tuple[dict, int]:
        return {**result, "status": status, "first_path": path}, code

    manifest_path = BUNDLE_ROOT / "UPSTREAM.json"
    try:
        check_links(manifest_path)
    except (GuardError, OSError, RuntimeError):
        return failed("bundle_integrity_failed", "UPSTREAM.json", 4)
    if not manifest_path.is_file():
        return failed("bundle_missing", "UPSTREAM.json", 3)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("empty file inventory")
        required = {"LICENSE", "humanize-korean/GUIDE.md"}
        required.update(f"humanize-korean/scripts/{name}" for name in SCRIPT_COMMANDS.values())
        required.update(Path(value).relative_to(BUNDLE_ROOT).as_posix()
                        for value in (result["quick_rules"], result["diagnosis_rules"],
                                      *result["roles"].values()))
        if required - files.keys():
            return failed("bundle_integrity_failed", sorted(required - files.keys())[0], 4)
        for name, entry in sorted(files.items()):
            relative = Path(name)
            if (relative.is_absolute() or ".." in relative.parts
                    or "\\" in name or relative.as_posix() != name):
                return failed("bundle_integrity_failed", name, 4)
            path = BUNDLE_ROOT / relative
            try:
                check_links(path)
            except (GuardError, OSError, RuntimeError):
                return failed("bundle_integrity_failed", name, 4)
            if not path.is_file():
                return failed("bundle_missing", name, 3)
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                return failed("bundle_integrity_failed", name, 4)
        actual = {p.relative_to(BUNDLE_ROOT).as_posix()
                  for p in BUNDLE_ROOT.rglob("*") if p.is_file() or p.is_symlink()}
        extras = actual - files.keys() - {"UPSTREAM.json"}
        if extras:
            return failed("bundle_integrity_failed", sorted(extras)[0], 4)
        result["upstream"] = {key: manifest[key] for key in
                              ("repo", "commit", "tag", "version", "license", "copyright")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return failed("bundle_integrity_failed", "UPSTREAM.json", 4)
    return result, 0


def new_run(argv: list[str]) -> dict:
    parser = GuardParser(prog="gg_humanize.py new-run", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--tag-hint", default=None)
    args = parser.parse_args(argv)
    tag = args.tag_hint if args.tag_hint is not None else secrets.token_hex(2)
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", tag):
        parser.error("--tag-hint must be 1–64 ASCII letters, digits or hyphens")
    root = checked_path(args.root)
    if not root.is_dir():
        raise GuardError(f"work root must be an existing directory: {root}")
    workspace = root / ".gg-work/humanize/_workspace"
    check_links(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        run_id = f"{date.today().isoformat()}-{number:03d}-{tag}"
        run_dir = workspace / run_id
        check_links(run_dir)
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            number += 1
    return {"status": "ok", "run_id": run_id, "run_dir": str(run_dir),
            "input_file": str(run_dir / "01_input.txt")}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("paths", "new-run", *SCRIPT_COMMANDS)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=commands)
    if not argv or argv[0] in {"-h", "--help"}:
        parser.parse_args(argv)
    command = parser.parse_args(argv[:1]).command
    paths, code = bundle_paths()
    if command == "paths":
        parser.parse_args(argv)
    if code or command == "paths":
        print(json.dumps(paths, ensure_ascii=False))
        return code
    try:
        if command == "new-run":
            print(json.dumps(new_run(argv[1:]), ensure_ascii=False))
            return 0
        guard_arguments(command, argv[1:])
    except (GuardError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "run_guard_rejected", "command": command,
                          "reason": str(exc)}, ensure_ascii=False))
        return 5
    script = Path(paths["scripts_dir"]) / SCRIPT_COMMANDS[command]
    previous_argv, previous_path = sys.argv, sys.path[:]
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.argv = [str(script), *argv[1:]]
        sys.path.insert(0, str(script.parent))
        sys.dont_write_bytecode = True
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path
        sys.dont_write_bytecode = previous_bytecode
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
