#!/usr/bin/env python3
"""Check declared deployment dispositions and required built-in sources."""

import argparse
import hashlib
import json
import posixpath
from pathlib import Path


def load(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def check(root):
    root = Path(root).resolve()
    skill = root / "skills" / "knuaf-doc"
    disposition_path = root / "docs" / "asset-disposition.json"
    registry_path = skill / "references" / "builtin-sources.json"
    manifest_path = skill / "install-manifest.json"
    errors = []
    try:
        disposition = load(disposition_path)
        registry = load(registry_path)
        manifest = load(manifest_path)
    except (OSError, ValueError) as exc:
        return [{"code": "input_unreadable", "path": str(exc)}]

    assets = disposition.get("assets")
    required = disposition.get("required_builtin_source_ids")
    if not isinstance(assets, list):
        return [{"code": "assets_invalid", "path": "assets"}]
    if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required):
        return [{"code": "required_sources_invalid",
                 "path": "required_builtin_source_ids"}]

    files = manifest.get("files", {})
    if not isinstance(files, dict):
        files = {}
        errors.append({"code": "manifest_files_invalid",
                       "path": "install-manifest.json"})
    for asset in assets:
        if not isinstance(asset, dict):
            errors.append({"code": "asset_invalid", "path": "assets"})
            continue
        if asset.get("disposition") not in {"배포", "배포(w5R)"}:
            continue
        label = asset.get("path", "")
        if not isinstance(label, str) or "\\" in label:
            errors.append({"code": "deployment_path_invalid", "path": str(label)})
            continue
        parts = Path(label).parts
        if len(parts) < 2 or parts[0] not in {"S0", "S1", "S2", "S3",
                                               "S4", "S5"}:
            errors.append({"code": "deployment_path_invalid", "path": label})
            continue
        rel = asset.get("payload_path", Path(*parts[1:]).as_posix())
        if (not isinstance(rel, str) or "\\" in rel or
                Path(rel).is_absolute() or
                any(part in {"", ".", ".."} for part in rel.split("/")) or
                posixpath.normpath(rel) != rel):
            errors.append({"code": "deployment_path_unsafe", "path": label})
            continue
        payload = (skill / rel).resolve()
        if skill.resolve() not in payload.parents:
            errors.append({"code": "deployment_path_unsafe", "path": label})
            continue
        expected = files.get(rel)
        if not payload.is_file():
            errors.append({"code": "deployment_missing_payload", "path": rel})
            continue
        actual = hashlib.sha256(payload.read_bytes()).hexdigest()
        if not isinstance(expected, str):
            errors.append({"code": "deployment_missing_manifest", "path": rel})
        elif actual != expected:
            errors.append({"code": "deployment_hash_mismatch", "path": rel})

    # Current registry schema is p5-reuse-registry/3 and calls this
    # collection "entries" (not "sources").
    entries = registry.get("entries", [])
    if not isinstance(entries, list):
        entries = []
        errors.append({"code": "registry_sources_invalid",
                       "path": "builtin-sources.json"})
    by_id = {item.get("source_id"): item for item in entries
             if isinstance(item, dict)}
    for source_id in required:
        source = by_id.get(source_id)
        if source is None:
            errors.append({"code": "required_source_missing_registry",
                           "path": source_id})
        elif source.get("runtime_present") is not True:
            errors.append({"code": "required_source_not_runtime",
                           "path": source_id})
    if len(set(required)) != len(required):
        errors.append({"code": "required_sources_duplicate",
                       "path": "required_builtin_source_ids"})
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    errors = check(args.root)
    print(json.dumps({"status": "fail" if errors else "ok",
                      "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
