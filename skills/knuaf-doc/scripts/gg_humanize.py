#!/usr/bin/env python3
"""Run only the bundled im-not-ai release; never discover global installations.

Use paths before reading GUIDE.md. Pass upstream script arguments unchanged.
Windows: py -3 "<skill>/scripts/gg_humanize.py" <command> ...
macOS/Linux: python3 "<skill>/scripts/gg_humanize.py" <command> ...
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import runpy
import secrets
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
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
                return failed("bundle_integrity_failed", name, 4)
            path = BUNDLE_ROOT / relative
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
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
    parser = argparse.ArgumentParser(prog="gg_humanize.py new-run")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tag-hint", default=None)
    args = parser.parse_args(argv)
    tag = args.tag_hint if args.tag_hint is not None else secrets.token_hex(2)
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", tag):
        parser.error("--tag-hint must be 1–64 ASCII letters, digits or hyphens")
    workspace = args.root.resolve() / ".gg-work/humanize/_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        run_id = f"{date.today().isoformat()}-{number:03d}-{tag}"
        run_dir = workspace / run_id
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
    if command == "new-run":
        print(json.dumps(new_run(argv[1:]), ensure_ascii=False))
        return 0
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
