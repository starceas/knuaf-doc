#!/usr/bin/env python3
"""Generate the validation-bundle manifest and deterministic clean exports.

Usage:
  python3 tools/build_validation_bundle.py --root <public_root> --write-manifest
  python3 tools/build_validation_bundle.py --root <public_root> --check
  python3 tools/build_validation_bundle.py --root <public_root> --out <new_dir>
      [--source-root NAME=PATH ...] [--receipt <path>]

The validation layer is defined as every on-disk file that is not part of
the pinned baseline (tools/baseline-files.json) and not on the explicit
ignore list. --write-manifest records those files with SHA-256 and
provenance from tools/validation-provenance.json into
tools/validation-bundle.json. The manifest never lists itself, so no
self-hash circularity exists; its own SHA-256 is emitted into the export
receipt instead.

Schema @2 links the declared runtime-change contract: the builder reads
tools/runtime-changes.json, refuses to emit a manifest while any baseline
file on disk differs from main without a declared entry (and vice versa —
every entry's before/after must match the baseline spec and the actual
file), and embeds the change list's own SHA-256 plus stage/parent linkage.
On-disk drift is never auto-promoted into the declared list.

--check regenerates the manifest in memory and byte-compares it with the
committed file: a stale manifest fails, so editing validation files
without rebuilding is detected.

--out writes a clean export (baseline + declared layer + manifest) into a
new directory and refuses to overwrite. Re-running against an unchanged
tree produces an identical export digest.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_bundle  # noqa: E402

BASELINE_SPEC = check_bundle.BASELINE_SPEC
MANIFEST = check_bundle.MANIFEST
PROVENANCE = check_bundle.PROVENANCE
RUNTIME_CHANGES = check_bundle.RUNTIME_CHANGES
GENERATOR = "tools/build_validation_bundle.py@3"


def load_inputs(root):
    """Read baseline spec + provenance file; return (baseline, provenance, errors)."""
    errors = []
    baseline, err = check_bundle._load_json(root / BASELINE_SPEC)
    if err:
        errors.append(f"{BASELINE_SPEC}: {err}")
        baseline = {"files": {}}
    provenance, err = check_bundle._load_json(root / PROVENANCE)
    if err:
        errors.append(f"{PROVENANCE}: {err}")
        provenance = {"files": {}, "readme_policy": {}}
    return baseline, provenance, errors


def verify_sources(provenance, source_roots):
    """Check declared source_sha256 values against source roots, when given.

    Returns (verified, problems). Verification is a build-time gate only:
    it changes whether the manifest may be written, never its bytes.
    """
    verified, problems = [], []
    for name, meta in sorted(provenance.get("files", {}).items()):
        source_path = meta.get("source_path")
        expected = meta.get("source_sha256")
        if not source_path or not expected:
            continue
        scheme, sep, rel = source_path.partition(":")
        if not sep:
            problems.append(f"{name}: source_path lacks scheme: {source_path!r}")
            continue
        base = source_roots.get(scheme)
        if base is None:
            problems.append(f"{name}: no --source-root for scheme {scheme!r}")
            continue
        candidate = Path(base) / rel
        if not candidate.is_file():
            problems.append(f"{name}: source missing: {candidate}")
            continue
        actual = check_bundle.sha256_file(candidate)
        if actual != expected:
            problems.append(
                f"{name}: source sha256 mismatch: expected {expected} got {actual}")
            continue
        verified.append(name)
    return verified, problems


class RuntimeChangeError(Exception):
    """The declared runtime-change list disagrees with baseline or disk."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def _runtime_changes_section(root, baseline):
    """Verify the declared change list and return the manifest link block.

    Fail-closed: any disagreement between the declared list, the pinned
    baseline, and on-disk files refuses manifest generation. The list is
    never auto-extended to match drift — the build only ever *links* the
    file the operator wrote.
    """
    on_disk = check_bundle.disk_files(root)
    states, declared_paths, errors = check_bundle.verify_runtime_changes(
        root, baseline, on_disk)
    problems = [f"{e['code']}: {e['path']}" for e in errors]
    baseline_files = baseline.get("files", {})
    readme_name = "README.md"
    for name, expected in baseline_files.items():
        path = on_disk.get(name)
        if path is None:
            if name not in declared_paths:
                problems.append(f"baseline_missing: {name}")
            continue
        actual = check_bundle.sha256_file(path)
        if actual != expected and name not in declared_paths \
                and name != readme_name:
            problems.append(f"undeclared_runtime_change: {name}")
    rc_path = root / RUNTIME_CHANGES
    if problems:
        raise RuntimeChangeError(problems)
    doc, _, _ = check_bundle._load_runtime_changes(rc_path)
    new_count = sum(1 for s in states.values() if s == "declared_new")
    return {
        "spec": RUNTIME_CHANGES,
        "spec_sha256": check_bundle.sha256_file(rc_path),
        "schema": doc.get("schema"),
        "stage": doc.get("stage"),
        "baseline_main": doc.get("baseline_main"),
        "parent_candidate": doc.get("parent_candidate"),
        "stage_lineage": doc.get("stage_lineage"),
        "approved_new_files": len(doc.get("approved_new_files") or []),
        "change_count": len(declared_paths),
        "verified_count": len(states),
        "new_count": new_count,
        "changes_sha256": check_bundle.tree_digest(
            {name: meta.get("after_sha256", "")
             for name, meta in (doc.get("changes") or {}).items()}),
    }


def build_manifest(root, provenance):
    """Compute the manifest dict for the current tree state (in memory)."""
    baseline, _, _ = load_inputs(root)
    baseline_files = baseline.get("files", {})
    prov_files = provenance.get("files", {})

    # Product files declared as new runtime changes (before_sha256=null)
    # belong to the change list, not the validation-layer manifest.
    rc_doc, _, _ = check_bundle._load_runtime_changes(root / RUNTIME_CHANGES)
    declared_new = {
        name for name, meta in ((rc_doc or {}).get("changes") or {}).items()
        if isinstance(meta, dict) and meta.get("before_sha256") is None
    }

    files = {}
    for rel, path in check_bundle.disk_files(root).items():
        if rel in (MANIFEST,) or rel in baseline_files or rel in declared_new:
            continue
        meta = {"sha256": check_bundle.sha256_file(path)}
        declared_meta = prov_files.get(rel, {})
        origin = declared_meta.get("origin", provenance.get("defaults", {}).get("origin", "new"))
        meta["origin"] = origin
        for key in ("source_path", "source_sha256", "transform", "note"):
            if key in declared_meta:
                meta[key] = declared_meta[key]
        files[rel] = meta

    return {
        "schema": check_bundle.MANIFEST_SCHEMA,
        "generated_by": GENERATOR,
        "baseline": {
            "main": baseline.get("main"),
            "spec": BASELINE_SPEC,
            "spec_sha256": check_bundle.sha256_file(root / BASELINE_SPEC)
            if (root / BASELINE_SPEC).is_file() else None,
            "file_count": len(baseline_files),
            "tree_sha256": check_bundle.tree_digest(baseline_files),
        },
        "runtime_changes": _runtime_changes_section(root, baseline),
        "readme_policy": provenance.get("readme_policy", {}),
        "files": files,
        "validation_tree_sha256": check_bundle.tree_digest(
            {name: meta["sha256"] for name, meta in files.items()}),
    }


def manifest_bytes(manifest):
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n").encode("utf-8")


def cmd_write_manifest(root, provenance, source_roots):
    if source_roots:
        verified, problems = verify_sources(provenance, source_roots)
        for name in verified:
            print(f"  source verified: {name}")
        if problems:
            for problem in problems:
                print(f"  provenance error: {problem}", file=sys.stderr)
            return 1
    try:
        manifest = build_manifest(root, provenance)
    except RuntimeChangeError as exc:
        print("manifest refused: runtime-change contract failed",
              file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    dest = root / MANIFEST
    data = manifest_bytes(manifest)
    dest.write_bytes(data)
    print(f"wrote {MANIFEST}: {len(manifest['files'])} files, "
          f"tree {manifest['validation_tree_sha256'][:16]}…")
    return 0


def cmd_check(root, provenance):
    try:
        manifest = build_manifest(root, provenance)
    except RuntimeChangeError as exc:
        print("check: runtime-change contract failed — manifest cannot "
              "be rebuilt", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    expected = manifest_bytes(manifest)
    committed = root / MANIFEST
    if not committed.is_file():
        print(f"check: {MANIFEST} absent — run --write-manifest", file=sys.stderr)
        return 1
    actual = committed.read_bytes()
    if actual == expected:
        print(f"check: {MANIFEST} is current "
              f"({len(manifest['files'])} files, deterministic rebuild identical)")
        return 0
    old = {}
    try:
        old = json.loads(actual.decode("utf-8")).get("files", {})
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    new = manifest["files"]
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(new) & set(old)
                     if new[k].get("sha256") != old[k].get("sha256"))
    print(f"check: {MANIFEST} is STALE — rebuild required", file=sys.stderr)
    for label, names in (("added", added), ("removed", removed), ("changed", changed)):
        for name in names:
            print(f"  {label}: {name}", file=sys.stderr)
    return 1


def cmd_export(root, provenance, out, receipt_path):
    report = check_bundle.check(root)
    if report["errors"]:
        print("export refused: tree fails integrity check", file=sys.stderr)
        for err in report["errors"]:
            print(f"  {err['code']}: {err['path']}", file=sys.stderr)
        return 1
    stale = cmd_check_quiet(root, provenance)
    if stale:
        print("export refused: manifest is stale — run --write-manifest",
              file=sys.stderr)
        return 1
    out = Path(out)
    if out.exists():
        print(f"export refused: {out} exists (no-clobber)", file=sys.stderr)
        return 1
    copied = {}
    for rel, path in check_bundle.disk_files(root).items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        copied[rel] = check_bundle.sha256_file(dest)
    # The manifest itself is part of the exported tree.
    manifest_src = root / MANIFEST
    dest = out / MANIFEST
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_src, dest)
    copied[MANIFEST] = check_bundle.sha256_file(dest)

    manifest_doc, _ = check_bundle._load_json(root / MANIFEST)
    rc_link = (manifest_doc or {}).get("runtime_changes", {})
    receipt = {
        "schema": "knuaf-doc/validation-export-receipt@2",
        "export": out.name,
        "file_count": len(copied),
        "tree_sha256": check_bundle.tree_digest(copied),
        "manifest_sha256": copied[MANIFEST],
        "baseline_main": report["baseline"]["main"],
        "baseline_tree_sha256": report["baseline"]["tree_sha256"],
        "validation_tree_sha256": report["manifest"]["validation_tree_sha256"],
        "runtime_changes": {
            "stage": rc_link.get("stage"),
            "change_count": rc_link.get("change_count"),
            "changes_sha256": rc_link.get("changes_sha256"),
            "parent_candidate": rc_link.get("parent_candidate"),
        },
        "readme_state": report["baseline"]["states"].get("README.md"),
        "python": report["python"],
    }
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if receipt_path:
        Path(receipt_path).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def cmd_check_quiet(root, provenance):
    committed = root / MANIFEST
    if not committed.is_file():
        return True
    try:
        fresh = manifest_bytes(build_manifest(root, provenance))
    except RuntimeChangeError:
        return True
    return committed.read_bytes() != fresh


def parse_source_roots(pairs):
    roots = {}
    for pair in pairs or []:
        name, sep, path = pair.partition("=")
        if not sep or not name:
            raise SystemExit(f"--source-root expects NAME=PATH, got {pair!r}")
        roots[name] = Path(path)
    return roots


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="public repository tree root")
    parser.add_argument("--write-manifest", action="store_true",
                        help="regenerate tools/validation-bundle.json in place")
    parser.add_argument("--check", action="store_true",
                        help="fail unless committed manifest matches a fresh rebuild")
    parser.add_argument("--out", help="write a clean export tree into this new directory")
    parser.add_argument("--receipt", help="with --out, also write the export receipt here")
    parser.add_argument("--source-root", action="append", metavar="NAME=PATH",
                        help="verify provenance source_path scheme against this root")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: --root {root} is not a directory", file=sys.stderr)
        return 2
    if not (args.write_manifest or args.check or args.out):
        parser.error("one of --write-manifest/--check/--out is required")
    source_roots = parse_source_roots(args.source_root)
    _, provenance, input_errors = load_inputs(root)
    if input_errors:
        for err in input_errors:
            print(f"error: {err}", file=sys.stderr)
        return 1

    status = 0
    if args.write_manifest:
        status = cmd_write_manifest(root, provenance, source_roots)
    elif args.check:
        status = cmd_check(root, provenance)
    elif args.out:
        status = cmd_export(root, provenance, args.out, args.receipt)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
