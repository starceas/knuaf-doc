#!/usr/bin/env python3
"""Validate the identity-only source catalogue; grants no reuse authority."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

import gg_major_contract
import gg_reuse


CATALOGUE_PATH = (Path(__file__).resolve().parents[1] / "references"
                  / "source-identities.json")
REGISTRY_PATH = (Path(__file__).resolve().parents[1] / "references"
                 / "builtin-sources.json")
SCHEMA = "knuaf-source-identities/v1"
MAX_JSON_DEPTH = 64
KINDS = {"public_no_derivatives", "student_example", "school_form",
         "official_publication", "official_download", "ncs_material",
         "professor_material", "other"}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_YEAR = re.compile(r"[0-9]{4}\Z")
_SEPARATORS = re.compile(r"[\s\-_.:/·,()]")
_DIGITS = re.compile(r"\d{6,}")
_PATH = re.compile(
    r"[A-Za-z]:[\\/]|\\\\|~[\\/]|file:|/(?:Users|home|private|var)/",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(r"(^|[\s(\"'=:])\/[^\s]+")


class _Object(dict):
    def __init__(self, pairs):
        super().__init__()
        self.duplicate = False
        for key, value in pairs:
            if key in self:
                self.duplicate = True
            self[key] = value


def _fail(where, rule):
    raise ValueError(f"{where}: {rule}") from None


def _shape(value, required, where):
    if not isinstance(value, dict):
        _fail(where, "shape_object")
    if getattr(value, "duplicate", False):
        _fail(where, "duplicate_key")
    if set(value) - set(required):
        _fail(where, "unknown_key")
    for key in required:
        if key not in value:
            _fail(f"{where}.{key}", "missing_field")


def _duplicates(value, where="$"):
    stack = [(value, where, 0)]
    known = {"schema", "entries", "sha256", "source_id", "major",
             "kind", "bibliographic", "title", "authors", "publisher",
             "year", "note"}
    while stack:
        node, location, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            _fail("$", "nesting_too_deep")
        if isinstance(node, _Object):
            if node.duplicate:
                _fail(location, "duplicate_key")
            for key, child in node.items():
                # Unknown keys are reported at their parent; never echo one.
                child_where = (f"{location}.{key}"
                               if isinstance(key, str) and key in known
                               else location)
                stack.append((child, child_where, depth + 1))
        elif isinstance(node, list):
            for i, child in enumerate(node):
                stack.append((child, f"{location}[{i}]", depth + 1))


def _privacy(value, where):
    normalized = unicodedata.normalize("NFKC", value)
    if _DIGITS.search(_SEPARATORS.sub("", normalized)):
        _fail(where, "digit_run")
    if _ABSOLUTE.search(normalized) or _PATH.search(normalized):
        _fail(where, "path_like")


def _text(value, where, *, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        _fail(where, "text_shape")
    _privacy(value, where)


def _identity_only(entry):
    return (entry.get("role") == "reference_only"
            and entry.get("runtime") is None
            and entry.get("runtime_present") is False
            and entry.get("artifacts") == []
            and not entry.get("coverage")
            and not entry.get("lineage"))


def load_source_identities(path=None, *, registry=None):
    """Return entries keyed by SHA-256 after validating catalogue and registry."""
    try:
        path = Path(path) if path is not None else CATALOGUE_PATH
    except Exception:
        _fail("$", "catalogue_unreadable")
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        _fail("$", "catalogue_unreadable")
    try:
        doc = json.loads(raw, object_pairs_hook=_Object)
    except RecursionError:
        _fail("$", "nesting_too_deep")
    except (ValueError, UnicodeError):
        _fail("$", "json_invalid")
    _duplicates(doc, "$")
    _shape(doc, ("schema", "entries"), "$")
    if doc["schema"] != SCHEMA:
        _fail("$.schema", "schema_invalid")
    if not isinstance(doc["entries"], list):
        _fail("$.entries", "list_required")

    if registry is None:
        try:
            registry = gg_reuse.load_registry(REGISTRY_PATH)
        except Exception:
            _fail("$.registry", "registry_invalid")
    registry_doc = getattr(registry, "document", registry)
    if not isinstance(registry_doc, dict) or not isinstance(
            registry_doc.get("entries"), list):
        _fail("$.registry", "registry_invalid")
    registry_by_sha = {}
    by_id = {}
    for entry in registry_doc["entries"]:
        if not isinstance(entry, dict):
            _fail("$.registry.entries", "registry_invalid")
        hashes = entry.get("source_sha256")
        if not isinstance(hashes, list) or not all(
                isinstance(sha, str) and _HEX64.fullmatch(sha)
                for sha in hashes):
            _fail("$.registry.entries", "registry_invalid")
        for sha in hashes:
            registry_by_sha.setdefault(sha, []).append(entry)
        sid = entry.get("source_id")
        if isinstance(sid, str):
            by_id[sid] = entry

    try:
        majors = set(gg_major_contract.default_registry().major_ids) | {"common"}
    except Exception:
        _fail("$.entries", "major_catalogue_invalid")
    result = {}
    for index, entry in enumerate(doc["entries"]):
        where = f"$.entries[{index}]"
        _shape(entry, ("sha256", "source_id", "major", "kind",
                       "bibliographic", "note"), where)
        sha = entry["sha256"]
        if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            _fail(f"{where}.sha256", "sha256_invalid")
        sid = entry["source_id"]
        if sid is not None:
            if not isinstance(sid, str):
                _fail(f"{where}.source_id", "source_id_grammar")
            _privacy(sid, f"{where}.source_id")
            if not _SOURCE_ID.fullmatch(sid):
                _fail(f"{where}.source_id", "source_id_grammar")
            if sid not in by_id:
                _fail(f"{where}.source_id", "registry_source_id_unknown")
        matching = registry_by_sha.get(sha, [])
        if matching and not all(_identity_only(e) for e in matching):
            _fail(f"{where}.sha256", "registry_not_identity_only")
        if matching and sid not in {e.get("source_id") for e in matching}:
            _fail(f"{where}.source_id", "registry_source_id_required")
        if not matching and sid is not None:
            _fail(f"{where}.source_id", "registry_source_id_must_be_null")
        if not isinstance(entry["major"], str) or entry["major"] not in majors:
            _fail(f"{where}.major", "major_unknown")
        if not isinstance(entry["kind"], str) or entry["kind"] not in KINDS:
            _fail(f"{where}.kind", "kind_unknown")
        bib = entry["bibliographic"]
        bp = f"{where}.bibliographic"
        _shape(bib, ("title", "authors", "publisher", "year"), bp)
        _text(bib["title"], f"{bp}.title", required=True)
        authors = bib["authors"]
        if not isinstance(authors, list):
            _fail(f"{bp}.authors", "list_required")
        for i, author in enumerate(authors):
            _text(author, f"{bp}.authors[{i}]", required=True)
        if bib["publisher"] is not None:
            _text(bib["publisher"], f"{bp}.publisher")
        if bib["year"] is not None and (
                not isinstance(bib["year"], str)
                or not _YEAR.fullmatch(bib["year"])):
            _fail(f"{bp}.year", "year_invalid")
        if entry["note"] is not None:
            _text(entry["note"], f"{where}.note")
        if entry["kind"] == "student_example" and (
                len(authors) != 1 or bib["publisher"] is not None
                or bib["year"] is not None or entry["note"] is not None):
            _fail(where, "student_example_shape")
        if sha in result:
            _fail(f"{where}.sha256", "duplicate_sha256")
        result[sha] = entry
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check",))
    args = parser.parse_args(argv)
    try:
        entries = load_source_identities()
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    if args.command == "check":
        print(json.dumps({"status": "ok", "entries": len(entries)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
