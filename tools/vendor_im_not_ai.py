#!/usr/bin/env python3
"""Vendor the upstream Codex copy-install layout without executing upstream code.

Update: check out the chosen release, then run
python3 tools/vendor_im_not_ai.py --src <checkout> --commit <full-sha> --tag <tag>
Run tests.test_vendor_im_not_ai with KNUAF_IM_NOT_AI_SRC=<checkout> afterwards;
update its independent file inventory when changing the pinned release.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

REPO = "https://github.com/epoko77-ai/im-not-ai"
DEST = Path(__file__).resolve().parents[1] / "skills/knuaf-doc/references/vendor/im-not-ai"
RUNTIME_SCRIPTS = (
    "prepare_monolith_input.py", "reassemble_chunks.py", "verify_change_rate.py",
    "verify_gates.py", "restore_modality.py", "strip_injected_commas.py",
    "sanitize_text.py", "checks.py", "console.py", "lexical_layer.py",
)
REFERENCE_PREFIX = "skills/humanize-korean/references/"


def git(src: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(src), *args], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def vendor(src: Path, commit: str, tag: str | None, dest: Path) -> dict:
    head = git(src, "rev-parse", "HEAD").decode("ascii").strip()
    resolved = git(src, "rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    if head != resolved:
        raise ValueError(f"source HEAD {head} differs from --commit {resolved}")
    if tag and git(src, "tag", "--list", tag).strip():
        tagged = git(src, "rev-parse", tag + "^{commit}").decode("ascii").strip()
        if tagged != resolved:
            raise ValueError("--tag does not identify --commit")
    tree = git(src, "ls-tree", "-rz", resolved).split(b"\0")
    entries = {}
    for row in tree:
        if row:
            info, name = row.split(b"\t", 1)
            entries[name.decode("utf-8")] = info.split()[0].decode("ascii")
    selected = {
        "LICENSE": "LICENSE",
        "humanize-korean/GUIDE.md": "codex/skills/humanize-korean/SKILL.md",
    }
    for path in sorted(entries):
        if path.startswith(REFERENCE_PREFIX):
            selected["humanize-korean/references/" + path[len(REFERENCE_PREFIX):]] = path
    for name in RUNTIME_SCRIPTS:
        selected["humanize-korean/scripts/" + name] = "scripts/" + name
    contents = {}
    for target, source in selected.items():
        if entries.get(source) not in {"100644", "100755"}:
            raise ValueError(f"not a regular source blob: {source}")
        contents[target] = git(src, "show", f"{resolved}:{source}")
    version = json.loads(git(src, "show", f"{resolved}:plugin.json"))["version"]
    notice = contents["LICENSE"].decode("utf-8")
    copyright_line = next(line for line in notice.splitlines() if line.startswith("Copyright"))
    manifest = {
        "schema": "knuaf-doc.vendor-upstream/1", "repo": REPO,
        "commit": resolved, "tag": tag, "version": version,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": "MIT", "copyright": copyright_line,
        "contributors_url": f"{REPO}/blob/{resolved}/CONTRIBUTORS.md",
        "layout_note": "Codex copy-install layout; SKILL.md renamed GUIDE.md only. "
                       "The references symlink is materialized from its source tree; "
                       "runtime scripts follow install.sh RUNTIME_SCRIPTS. Bytes unchanged.",
        "files": {target: {"upstream_path": selected[target],
                           "sha256": hashlib.sha256(data).hexdigest()}
                  for target, data in sorted(contents.items())},
        "excluded": [{"path": path, "reason": "Not used by the Codex bundled runtime; "
                     "development, alternate entry point, installation or attribution index."}
                     for path in sorted(set(entries) - set(selected.values()))],
    }
    # Fail before writing if the destination includes links or unowned files.
    for parent in (dest, *dest.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink destination: {parent}")
    old_files = set()
    if dest.exists():
        for path in dest.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"symlink destination: {path}")
        old_manifest = dest / "UPSTREAM.json"
        if old_manifest.is_file():
            old_files = set(json.loads(old_manifest.read_text(encoding="utf-8"))["files"])
        actual = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        unknown = actual - old_files - set(contents) - {"UPSTREAM.json"}
        if unknown:
            raise ValueError(f"unowned destination files: {sorted(unknown)}")
    for name in old_files:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe old manifest path: {name}")
    for name, data in contents.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in old_files - set(contents):
        (dest / name).unlink(missing_ok=True)
    (dest / "UPSTREAM.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "ok", "commit": resolved, "files": len(contents) + 1,
            "dest": str(dest)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", help="Release tag (explicit for shallow checkouts without tags)")
    parser.add_argument("--dest", type=Path, default=DEST)
    args = parser.parse_args()
    try:
        result = vendor(args.src, args.commit, args.tag, args.dest)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"vendor failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
