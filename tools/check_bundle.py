#!/usr/bin/env python3
"""Verify integrity of a knuaf-doc public repository tree.

Usage: python3 tools/check_bundle.py --root <public_root> [--json <path>]

Checks one tree (checkout, candidate, or clean export):

1. Baseline integrity — every file listed in tools/baseline-files.json
   (the pinned main snapshot hash list) must exist and match SHA-256,
   except:
   a. README.md, governed by readme_policy inside
      tools/validation-bundle.json (declared markers must survive);
   b. baseline paths declared in tools/runtime-changes.json, which must
      match that file's after_sha256 exactly and are reported as
      "declared_change". Declared-change verification is a hash-linkage
      check only — never an approval of the changed content.
2. Declared validation layer — every non-baseline file on disk must be
   listed in tools/validation-bundle.json with a matching SHA-256 and a
   recognised provenance origin.
3. Completeness — no undeclared files outside the explicit ignore list
   (VCS and interpreter caches only).
4. Manifest hygiene — the manifest must not list itself or baseline
   paths, its embedded validation_tree_sha256 must recompute, and its
   runtime_changes link must match the on-disk change list.

Runtime-change rejections: duplicate list keys, path escapes, entries for
non-baseline paths (which covers baseline/tool/manifest self-exemption),
before_sha256 that disagrees with the pinned main baseline, after_sha256
that disagrees with the on-disk file, no-op entries, missing declared
files, undeclared on-disk baseline changes, and any path declared both as
a runtime change and as a validation-layer file.

Exit codes: 0 clean, 1 integrity findings, 2 usage/operator error.

Trust root: tools/validation-bundle.json is generated but committed; it
cannot carry its own hash without a circular dependency. Its SHA-256 is
therefore printed in every report for external pinning, and CI
regenerates it deterministically to compare bytes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

BASELINE_SPEC = "tools/baseline-files.json"
MANIFEST = "tools/validation-bundle.json"
PROVENANCE = "tools/validation-provenance.json"
RUNTIME_CHANGES = "tools/runtime-changes.json"
MANIFEST_SCHEMA = "knuaf-doc/validation-bundle@3"
RUNTIME_CHANGES_SCHEMA = "knuaf-doc/runtime-changes@2"
KNOWN_ORIGINS = {"new", "reused", "transformed", "pinned-input", "generated"}
# The change list may never exempt the trust-root or spec files themselves.
# (Already unreachable via the baseline-membership rule; kept explicit.)
SELF_EXEMPT_PATHS = {BASELINE_SPEC, MANIFEST, PROVENANCE, RUNTIME_CHANGES,
                     "tools/check_bundle.py", "tools/build_validation_bundle.py"}
_SHA256_HEX = set("0123456789abcdef")
# Interpreter/VCS noise only. A plantable payload must never match these.
IGNORED_NAMES = {".git", ".DS_Store", "__pycache__"}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(files):
    """Digest over sorted 'path\\tsha256' lines. Deterministic, order-free."""
    lines = sorted(f"{name}\t{digest}" for name, digest in files.items())
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def ignored(relpath):
    parts = Path(relpath).parts
    if any(part in IGNORED_NAMES for part in parts):
        return True
    return relpath.endswith(IGNORED_SUFFIXES)


def disk_files(root):
    """All files under root as posix relpaths, minus the ignore list."""
    found = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if ignored(rel):
            continue
        found[rel] = path
    return found


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"


def _load_runtime_changes(path):
    """Parse runtime-changes.json, detecting duplicated change keys.

    Returns (doc, duplicate_keys, error)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, [], "missing"
    except (OSError, UnicodeDecodeError) as exc:
        return None, [], f"unreadable: {exc}"
    duplicates = []

    def _hook(items):
        if len(items) != len({k for k, _ in items}):
            seen = set()
            for key, _ in items:
                if key in seen and key not in duplicates:
                    duplicates.append(key)
                seen.add(key)
        return dict(items)

    try:
        return json.loads(text, object_pairs_hook=_hook), duplicates, None
    except json.JSONDecodeError as exc:
        return None, [], f"unreadable: {exc}"


def _valid_sha(value):
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _SHA256_HEX)


def _plain_relpath(name):
    if not isinstance(name, str) or not name:
        return False
    p = Path(name)
    return (not p.is_absolute() and ".." not in p.parts
            and p.as_posix() == name)


def verify_runtime_changes(root, baseline_doc, on_disk):
    """Verify the declared runtime-change list against baseline and disk.

    Returns (change_states, declared_paths, errors). change_states maps
    each fully verified change path to "declared_change"; declared_paths
    lists every entry regardless of validity. A passing verification
    proves hash linkage only — the changed content is never approved here.
    """
    states = {}
    declared_paths = set()
    errors = []
    doc, duplicates, err = _load_runtime_changes(root / RUNTIME_CHANGES)
    if err:
        errors.append({"code": "runtime_changes_unreadable",
                       "path": RUNTIME_CHANGES, "detail": err})
        return states, declared_paths, errors
    for key in duplicates:
        errors.append({"code": "runtime_changes_duplicate_key",
                       "path": RUNTIME_CHANGES,
                       "detail": f"duplicate key: {key!r}"})
    if doc.get("schema") != RUNTIME_CHANGES_SCHEMA:
        errors.append({"code": "runtime_changes_schema",
                       "path": RUNTIME_CHANGES,
                       "detail": f"schema={doc.get('schema')!r}"})
    baseline_files = baseline_doc.get("files", {})
    if doc.get("baseline_main") != baseline_doc.get("main"):
        errors.append({"code": "runtime_changes_baseline",
                       "path": RUNTIME_CHANGES,
                       "detail": "baseline_main does not match baseline spec"})
    spec_path = root / BASELINE_SPEC
    if spec_path.is_file() and _valid_sha(doc.get("baseline_spec_sha256")):
        if doc["baseline_spec_sha256"] != sha256_file(spec_path):
            errors.append({"code": "runtime_changes_baseline_spec",
                           "path": RUNTIME_CHANGES,
                           "detail": "baseline_spec_sha256 does not match"})
    # The allowlist is the only door through which a file absent from the
    # pinned baseline may be declared: each before_sha256=null entry must
    # name an allowlisted path and carry a first_changed stage tag.
    approved_new = doc.get("approved_new_files", [])
    if not isinstance(approved_new, list):
        errors.append({"code": "runtime_change_allowlist_invalid",
                       "path": RUNTIME_CHANGES,
                       "detail": "approved_new_files must be a list"})
        approved_new = []
    approved_set = set()
    for allowed in approved_new:
        if not _plain_relpath(allowed) or allowed in SELF_EXEMPT_PATHS \
                or allowed in baseline_files:
            errors.append({"code": "runtime_change_allowlist_invalid",
                           "path": str(allowed),
                           "detail": "allowlist entry must be a plain "
                                     "non-baseline, non-spec path"})
            continue
        approved_set.add(allowed)

    changes = doc.get("changes")
    if not isinstance(changes, dict):
        errors.append({"code": "runtime_changes_shape",
                       "path": RUNTIME_CHANGES,
                       "detail": "changes must be an object"})
        return states, declared_paths, errors
    for name, meta in sorted(changes.items()):
        declared_paths.add(name)
        if not _plain_relpath(name):
            errors.append({"code": "runtime_change_path_escape",
                           "path": str(name), "detail": "not a plain relpath"})
            continue
        if name in SELF_EXEMPT_PATHS:
            errors.append({"code": "runtime_change_self_exempt",
                           "path": name,
                           "detail": "spec/tool/manifest paths cannot be "
                                     "runtime changes"})
            continue
        if not isinstance(meta, dict):
            errors.append({"code": "runtime_change_shape", "path": name,
                           "detail": "entry must be an object"})
            continue
        before = meta.get("before_sha256")
        after = meta.get("after_sha256")
        is_new = name not in baseline_files
        if is_new:
            if before is not None:
                errors.append({"code": "runtime_change_not_baseline",
                               "path": name,
                               "detail": "change entry names a path outside "
                                         "the pinned baseline"})
                continue
            if name not in approved_set:
                errors.append({"code": "runtime_change_new_not_allowlisted",
                               "path": name,
                               "detail": "new-file change is not on the "
                                         "approved_new_files allowlist"})
                continue
            if not meta.get("first_changed"):
                errors.append({"code": "runtime_change_new_no_stage",
                               "path": name,
                               "detail": "new-file change lacks a "
                                         "first_changed stage tag"})
                continue
            if not _valid_sha(after):
                errors.append({"code": "runtime_change_after_shape",
                               "path": name,
                               "detail": "after_sha256 must be a sha256 hex"})
                continue
            path = on_disk.get(name)
            if path is None:
                errors.append({"code": "runtime_change_missing", "path": name,
                               "detail": "declared new file absent on disk"})
                continue
            actual = sha256_file(path)
            if actual != after:
                errors.append({"code": "runtime_change_after_mismatch",
                               "path": name,
                               "detail": f"expected after {after[:12]}… "
                                         f"got {actual[:12]}…",
                               "expected": after, "actual": actual})
                continue
            states[name] = "declared_new"
            continue
        if before is None:
            errors.append({"code": "runtime_change_new_is_baseline",
                           "path": name,
                           "detail": "baseline file cannot be declared as a "
                                     "new file (before_sha256=null)"})
            continue
        if not _valid_sha(before) or before != baseline_files[name]:
            errors.append({"code": "runtime_change_before_mismatch",
                           "path": name,
                           "detail": "before_sha256 does not equal the "
                                     "original main baseline hash"})
            continue
        if not _valid_sha(after):
            errors.append({"code": "runtime_change_after_shape",
                           "path": name,
                           "detail": "after_sha256 must be a sha256 hex"})
            continue
        if after == before:
            errors.append({"code": "runtime_change_noop", "path": name,
                           "detail": "declared change has before == after"})
            continue
        path = on_disk.get(name)
        if path is None:
            errors.append({"code": "runtime_change_missing", "path": name,
                           "detail": "declared change file absent on disk"})
            continue
        actual = sha256_file(path)
        if actual != after:
            errors.append({"code": "runtime_change_after_mismatch",
                           "path": name,
                           "detail": f"expected after {after[:12]}… "
                                     f"got {actual[:12]}…",
                           "expected": after, "actual": actual})
            continue
        states[name] = "declared_change"
    return states, declared_paths, errors


def check(root):
    """Return a report dict; report['errors'] non-empty means integrity failure."""
    root = Path(root)
    errors = []
    notes = []

    baseline_doc, err = _load_json(root / BASELINE_SPEC)
    if err:
        errors.append({"code": "baseline_spec_missing", "path": BASELINE_SPEC,
                       "detail": err})
        baseline_doc = {"files": {}}
    manifest_doc, err = _load_json(root / MANIFEST)
    if err:
        errors.append({"code": "manifest_missing", "path": MANIFEST,
                       "detail": err})
        manifest_doc = None

    baseline_files = baseline_doc.get("files", {})
    declared = (manifest_doc or {}).get("files", {})
    readme_policy = (manifest_doc or {}).get("readme_policy", {})
    readme_path = readme_policy.get("path", "README.md")
    readme_markers = readme_policy.get("required_markers_if_modified", [])

    if manifest_doc is not None:
        if manifest_doc.get("schema") != MANIFEST_SCHEMA:
            errors.append({"code": "manifest_schema", "path": MANIFEST,
                           "detail": f"schema={manifest_doc.get('schema')!r}"})
        if MANIFEST in declared:
            errors.append({"code": "manifest_self_reference", "path": MANIFEST,
                           "detail": "manifest must not list itself"})
        overlap = sorted(set(declared) & set(baseline_files))
        if overlap:
            errors.append({"code": "manifest_baseline_overlap", "path": MANIFEST,
                           "detail": "baseline paths must not be manifest-listed",
                           "paths": overlap})
        bad_origins = sorted(
            name for name, meta in declared.items()
            if not isinstance(meta, dict)
            or meta.get("origin", "new") not in KNOWN_ORIGINS)
        if bad_origins:
            errors.append({"code": "manifest_origin_unknown", "path": MANIFEST,
                           "detail": "unrecognised provenance origin",
                           "paths": bad_origins})
        # The manifest must re-declare the baseline spec hash it was built
        # against and link the runtime-change list actually on disk.
        spec_on_disk = root / BASELINE_SPEC
        manifest_baseline = manifest_doc.get("baseline", {})
        if spec_on_disk.is_file():
            embedded = manifest_baseline.get("spec_sha256")
            if embedded != sha256_file(spec_on_disk):
                errors.append({"code": "manifest_baseline_spec_sha",
                               "path": MANIFEST,
                               "detail": "embedded baseline spec sha256 does "
                                         "not match tools/baseline-files.json"})
        if manifest_baseline.get("main") != baseline_doc.get("main"):
            errors.append({"code": "manifest_baseline_main", "path": MANIFEST,
                           "detail": "embedded baseline main does not match "
                                     "the baseline spec"})
        rc_link = manifest_doc.get("runtime_changes")
        rc_path = root / RUNTIME_CHANGES
        if not isinstance(rc_link, dict):
            errors.append({"code": "manifest_runtime_changes",
                           "path": MANIFEST,
                           "detail": "manifest lacks a runtime_changes link"})
        elif rc_path.is_file():
            if rc_link.get("spec") != RUNTIME_CHANGES:
                errors.append({"code": "manifest_runtime_changes",
                               "path": MANIFEST,
                               "detail": "runtime_changes.spec mismatch"})
            if rc_link.get("spec_sha256") != sha256_file(rc_path):
                errors.append({"code": "manifest_runtime_changes",
                               "path": MANIFEST,
                               "detail": "runtime_changes.spec_sha256 does not "
                                         "match the on-disk change list"})
            if rc_link.get("baseline_main") != baseline_doc.get("main"):
                errors.append({"code": "manifest_runtime_changes",
                               "path": MANIFEST,
                               "detail": "runtime_changes.baseline_main "
                                         "mismatch"})

    on_disk = disk_files(root)
    on_disk.pop(MANIFEST, None)  # manifest is the trust root; hashed below

    change_states, declared_change_paths, change_errors = \
        verify_runtime_changes(root, baseline_doc, on_disk)
    errors.extend(change_errors)

    baseline_state = {}
    for name, expected in sorted(baseline_files.items()):
        path = on_disk.pop(name, None)
        if path is None:
            if name in declared_change_paths:
                # verify_runtime_changes already reported the precise error.
                baseline_state[name] = "declared_change_invalid"
            else:
                errors.append({"code": "baseline_missing", "path": name,
                               "detail": "baseline file absent"})
                baseline_state[name] = "missing"
            continue
        actual = sha256_file(path)
        if actual == expected:
            baseline_state[name] = "ok"
        elif name in change_states:
            baseline_state[name] = change_states[name]
        elif name in declared_change_paths:
            # verify_runtime_changes already reported the precise error.
            baseline_state[name] = "declared_change_invalid"
        elif name == readme_path and readme_policy.get("allow_modified"):
            readme_state = _check_readme(root / name, readme_markers, errors)
            baseline_state[name] = readme_state
        else:
            errors.append({"code": "undeclared_runtime_change", "path": name,
                           "detail": f"baseline file changed without a "
                                     f"runtime-changes entry: expected "
                                     f"{expected[:12]}… got {actual[:12]}…",
                           "expected": expected, "actual": actual})
            baseline_state[name] = "mismatch"

    if readme_path and readme_path not in baseline_files:
        notes.append(f"readme_policy names {readme_path} but it is not a baseline file")

    # Verified new-file changes live outside the baseline list; drop them
    # from the remaining on-disk set so they are not reported undeclared.
    for name in change_states:
        if name not in baseline_files:
            on_disk.pop(name, None)

    declared_state = {}
    for name, meta in sorted(declared.items()):
        expected = meta.get("sha256") if isinstance(meta, dict) else None
        path = on_disk.pop(name, None)
        if path is None:
            errors.append({"code": "declared_missing", "path": name,
                           "detail": "manifest-listed file absent"})
            declared_state[name] = "missing"
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append({"code": "declared_hash_mismatch", "path": name,
                           "detail": f"expected {expected} got {actual}",
                           "expected": expected, "actual": actual})
            declared_state[name] = "mismatch"
        else:
            declared_state[name] = "ok"

    # A path may never be declared both as a runtime change and as a
    # validation-layer file.
    double_declared = sorted(set(declared) & declared_change_paths)
    if double_declared:
        errors.append({"code": "runtime_change_double_declared",
                       "path": MANIFEST,
                       "detail": "path declared as both runtime change and "
                                 "validation-layer file",
                       "paths": double_declared})

    if on_disk:
        for name in sorted(on_disk):
            errors.append({"code": "undeclared_file", "path": name,
                           "detail": "file is neither baseline nor manifest-declared"})

    recomputed_tree = None
    if manifest_doc is not None and declared:
        recomputed_tree = tree_digest(
            {name: meta.get("sha256", "") for name, meta in declared.items()})
        if manifest_doc.get("validation_tree_sha256") not in (None, recomputed_tree):
            errors.append({"code": "manifest_tree_digest", "path": MANIFEST,
                           "detail": "embedded validation_tree_sha256 does not recompute"})

    baseline_doc_files = baseline_doc.get("files", {})
    report = {
        "root": str(root),
        "status": "ok" if not errors else "errors",
        "errors": errors,
        "notes": notes,
        "baseline": {
            "main": baseline_doc.get("main"),
            "spec": BASELINE_SPEC,
            "file_count": len(baseline_doc_files),
            "tree_sha256": tree_digest(baseline_doc_files),
            "states": baseline_state,
        },
        "runtime_changes": {
            "spec": RUNTIME_CHANGES,
            "declared_count": len(declared_change_paths),
            "verified": len(change_states),
            "new_count": sum(1 for s in change_states.values()
                             if s == "declared_new"),
            "note": "declared_change/declared_new verify declared hash "
                    "linkage only; they are not an approval of the "
                    "changed or added content",
        },
        "manifest": {
            "path": MANIFEST,
            "present": manifest_doc is not None,
            "file_count": len(declared),
            "validation_tree_sha256": (manifest_doc or {}).get("validation_tree_sha256"),
            "recomputed_tree_sha256": recomputed_tree,
            "states": declared_state,
        },
        "undeclared": sorted(on_disk),
        "python": sys.version.split()[0],
    }
    manifest_path = root / MANIFEST
    if manifest_path.is_file():
        report["manifest"]["sha256"] = sha256_file(manifest_path)
    return report


def _check_readme(path, markers, errors):
    """README may differ from baseline only under the declared policy."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append({"code": "readme_unreadable", "path": path.name,
                       "detail": str(exc)})
        return "modified_invalid"
    missing = [m for m in markers if m not in text]
    if missing:
        errors.append({"code": "readme_invariant_missing", "path": path.name,
                       "detail": "modified README lost required markers",
                       "missing_markers": missing})
        return "modified_invalid"
    return "modified_allowed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="public repository tree root")
    parser.add_argument("--json", dest="json_out", help="write full report to this path")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: --root {root} is not a directory", file=sys.stderr)
        return 2
    report = check(root)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {}
    for err in report["errors"]:
        counts[err["code"]] = counts.get(err["code"], 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
    print(f"check_bundle: status={report['status']} "
          f"baseline={report['baseline']['file_count']} "
          f"declared={report['manifest']['file_count']} "
          f"errors: {summary}")
    for note in report["notes"]:
        print(f"  note: {note}")
    for err in report["errors"]:
        print(f"  {err['code']}: {err['path']} — {err['detail']}")
    if "sha256" in report["manifest"]:
        print(f"manifest sha256: {report['manifest']['sha256']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
