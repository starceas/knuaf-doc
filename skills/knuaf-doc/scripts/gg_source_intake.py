"""P5 external-source scanner — frozen API.md section 2 (v19).

Implements:

- ``scan_external_root(root_path, *, root, classification_version,
  registry, key_catalog, scan_policy, current_draft=None,
  requested_use=None) -> ScanResult`` — the whole-root no-follow typed
  enumeration with SCHEMA section 7.3 total classification, the section
  10 sealed receipt (field-for-field, nothing added) and volatile
  diagnostics.  ``registry`` MUST be the constructed
  ``RegistryResolution`` produced by ``gg_reuse.load_registry`` — a bare
  dict or a digest is an internal ``ValueError`` here (never a verdict).
- ``run_source_scan(...) -> ScanResult | ScanFailure`` — the public
  wrapper.  ``to_scan_failure`` is the SOLE conversion site and is
  called by exactly this one place: no raw BoundaryError /
  UnstableInputError / ValueError escapes the wrapper.
- ``to_scan_failure(exc) -> ScanFailure`` — the closed failure schema
  (section 2.3 table, every row complete with its mandatory payload and
  exit).
- ``write_scan_receipt(root, scan_result, *, capability) -> Path`` —
  requires the held capability (``gg_lock.assert_held``, matching
  ``gg_publication.py:909``), writes ``sources/intake/<scan_id>.json``
  through the P2 no-replace primitive and the volatile
  ``.diagnostics.json`` companion.  NEVER writes ``03_sources.md`` and
  never touches a generated view.

Boundary discipline (kept distinct, per the frozen sources):
- the scanner carries NO ``packs_dir`` operand and never consumes the
  ``packs_dir == resolved_packs_dir`` equality (V15-2) — that claim
  belongs to the resolver context field, the ``load_accepted_catalog``
  parameter and the ``--packs-dir`` flag only;
- a malformed scan argument is the scanner's OWN vocabulary —
  ``invalid_scan_request``, exit 2 — never a resolver ``blocked``
  verdict and never a registration reason;
- boundary rejections are ``boundary_rejected`` exit 3 with
  ``boundary_kind`` + ``relative_id``; ``unstable_input`` is its own
  status, exit 3 — reasons are never rounded up to a neighbouring
  status;
- ``registry_digest`` / ``scan_policy_digest`` in the sealed receipt are
  identification only (SCHEMA section 7.4); ``registry_digest`` is
  ``registry.registry_digest`` — document content only.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat as _stat
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import gg_fs
import gg_lock
import gg_reuse

SCAN_ID_VERSION = "p5-scan/1"
TREE_INVENTORY_SCHEMA = "p5-tree-inventory/1"

BOUNDARY_KINDS = {"file_symlink", "dir_symlink", "dangling_symlink",
                  "non_regular_entry", "path_escape",
                  "normalization_collision"}

ENTRY_REASONS = {"document", "included_hash", "excluded_no_hash",
                 "not_included", "excluded_dir", "traversed_dir"}

# API section 2.3 — the CLOSED failure schema.  reason ->
# (status, mandatory payload fields, exit).  Every row complete.
_FAILURE_TABLE = {
    "file_symlink": ("boundary_rejected", 3),
    "dir_symlink": ("boundary_rejected", 3),
    "dangling_symlink": ("boundary_rejected", 3),
    "non_regular_entry": ("boundary_rejected", 3),
    "path_escape": ("boundary_rejected", 3),
    "normalization_collision": ("boundary_rejected", 3),
    "unstable_input": ("unstable_input", 3),
    "invalid_scan_request": ("invalid_scan_request", 2),
    "classification_version_mismatch": ("invalid_scan_request", 2),
    "scan_policy_invalid": ("invalid_scan_request", 2),
}
_FAILURE_PAYLOAD = {
    "boundary_rejected": ["boundary_kind", "relative_id"],
    "unstable_input": ["relative_id"],
    "invalid_scan_request": ["required_fields"],
}


class BoundaryError(ValueError):
    """Raised by scan internals on a boundary violation — never
    followed, never read.  Carries the closed BOUNDARY_KINDS member and
    the ALWAYS-present relative_id."""

    def __init__(self, kind, relative_id):
        if kind not in BOUNDARY_KINDS:
            raise ValueError("unknown boundary kind %r" % kind)
        super().__init__("%s: %s" % (kind, relative_id))
        self.kind = kind
        self.relative_id = relative_id


class UnstableInputError(ValueError):
    """Identity/stat/inventory changed between the before- and
    after-check of a read (SPEC section 3.2)."""
    kind = "unstable_input"

    def __init__(self, relative_id):
        super().__init__("unstable_input: %s" % relative_id)
        self.relative_id = relative_id


class ScanRequestError(ValueError):
    """Internal malformed-input error — reason is a member of the
    invalid_scan_request family; ``fields`` names the offending fields.
    Converted by the sole conversion site; never escapes
    run_source_scan."""

    def __init__(self, reason, fields, detail=""):
        super().__init__("%s: %s (%s)" % (reason, ",".join(fields),
                                          detail))
        self.reason = reason
        self.fields = list(fields)


def _canonical_json(value):
    """SCHEMA section 7.4: UTF-8, sorted keys, separators (",", ":"),
    ensure_ascii=False."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _nfc(text):
    return unicodedata.normalize("NFC", text)


def _is_hex64(v):
    return isinstance(v, str) and len(v) == 64 and all(
        c in "0123456789abcdef" for c in v)


# ---------------------------------------------------------------------------
# Glob matching — SCHEMA section 7.1: '*' within a segment, '**' any
# depth, '?' one character; evaluated against the NFC-normalized
# relative id, anchored at the external root.

import re as _re


def _glob_regex(pattern):
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # '**' — any depth, including zero segments
                while i + 1 < n and pattern[i + 1] == "*":
                    i += 1
                if i + 1 < n and pattern[i + 1] == "/":
                    out.append("(?:[^/]+/)*")
                    i += 1
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(_re.escape(c))
        i += 1
    return _re.compile("^" + "".join(out) + "$", _re.DOTALL)


def _pattern_escapes(pattern):
    """A pattern that would resolve outside the root is rejected at load
    time: absolute patterns or any '..' segment."""
    if pattern.startswith("/") or pattern.startswith("~"):
        return True
    return any(seg == ".." for seg in pattern.split("/"))


# ---------------------------------------------------------------------------
# Policy load/validation — SCHEMA section 7 + 7.3.1


def _validate_policy(scan_policy, classification_version, registry):
    """Validate the SCHEMA section 7 document.  ScanRequestError on any
    violation (converted to the invalid_scan_request family by the sole
    conversion site)."""
    if not isinstance(scan_policy, dict):
        raise ScanRequestError("scan_policy_invalid", ["scan_policy"],
                               "not an object")
    pv = scan_policy.get("classification_version")
    if not isinstance(pv, str) or not pv:
        raise ScanRequestError("scan_policy_invalid",
                               ["scan_policy.classification_version"],
                               "missing/non-string")
    reg_cv = registry.document.get("classifier_version")
    if pv != reg_cv or classification_version != reg_cv \
            or classification_version != pv:
        raise ScanRequestError(
            "classification_version_mismatch",
            ["classification_version"],
            "arg=%r registry=%r policy=%r"
            % (classification_version, reg_cv, pv))
    include = scan_policy.get("include")
    if not isinstance(include, list) or not all(
            isinstance(g, str) for g in include):
        raise ScanRequestError("scan_policy_invalid",
                               ["scan_policy.include"],
                               "must be a list of glob strings")
    always_hash = scan_policy.get("always_hash")
    if not isinstance(always_hash, list) or not all(
            isinstance(g, str) for g in always_hash):
        raise ScanRequestError("scan_policy_invalid",
                               ["scan_policy.always_hash"],
                               "must be a list of glob strings")
    exts = scan_policy.get("document_extensions")
    if not isinstance(exts, list) or not all(
            isinstance(e, str) for e in exts):
        raise ScanRequestError("scan_policy_invalid",
                               ["scan_policy.document_extensions"],
                               "must be a list of strings")
    exclude = scan_policy.get("exclude")
    if not isinstance(exclude, list):
        raise ScanRequestError("scan_policy_invalid",
                               ["scan_policy.exclude"],
                               "must be a list")
    validated = []
    seen_patterns = {}
    for i, rule in enumerate(exclude):
        f = "scan_policy.exclude[%d]" % i
        if not isinstance(rule, dict):
            raise ScanRequestError("scan_policy_invalid", [f],
                                   "not an object")
        pat = rule.get("pattern")
        if not isinstance(pat, str) or not pat:
            raise ScanRequestError("scan_policy_invalid",
                                   [f + ".pattern"], "missing/non-string")
        if _pattern_escapes(pat):
            raise ScanRequestError(
                "scan_policy_invalid", [f + ".pattern"],
                "pattern would resolve outside the root")
        if not isinstance(rule.get("reason"), str) or not rule["reason"]:
            raise ScanRequestError("scan_policy_invalid",
                                   [f + ".reason"], "required")
        if rule.get("hash_mode") not in ("excluded_no_hash",
                                         "included_hash"):
            raise ScanRequestError(
                "scan_policy_invalid", [f + ".hash_mode"],
                "must be excluded_no_hash|included_hash")
        if pat in seen_patterns:
            prev = validated[seen_patterns[pat]]
            if prev["reason"] != rule["reason"] \
                    or prev["hash_mode"] != rule["hash_mode"]:
                # identical pattern, different fields -> invalid at load
                raise ScanRequestError(
                    "scan_policy_invalid", [f],
                    "duplicate pattern %r with different fields" % pat)
            continue  # identical duplicate — deduplicated at load
        seen_patterns[pat] = len(validated)
        validated.append({"pattern": pat, "reason": rule["reason"],
                          "hash_mode": rule["hash_mode"],
                          "policy_ref": i})
    for coll in (include, always_hash):
        for j, pat in enumerate(coll):
            if _pattern_escapes(pat):
                raise ScanRequestError(
                    "scan_policy_invalid", ["scan_policy"],
                    "pattern %r would resolve outside the root" % pat)
    compiled = {
        "include": [(g, _glob_regex(g)) for g in include],
        "always_hash": [(g, _glob_regex(g)) for g in always_hash],
        "exclude": [dict(rule, _re=_glob_regex(rule["pattern"]))
                    for rule in validated],
        "document_extensions": {e.lower() for e in exts},
    }
    return compiled


def _exclude_winner(compiled, rel_id):
    """SCHEMA 7.3.1 — the matching exclude entry with the longest pattern
    wins; a tie breaks to the lexicographically smallest pattern."""
    best = None
    for rule in compiled["exclude"]:
        if rule["_re"].match(rel_id):
            if best is None \
                    or len(rule["pattern"]) > len(best["pattern"]) \
                    or (len(rule["pattern"]) == len(best["pattern"])
                        and rule["pattern"] < best["pattern"]):
                best = rule
    return best


def _matches_any(compiled_list, rel_id):
    return any(rx.match(rel_id) for _g, rx in compiled_list)


def _extension_of(rel_id):
    base = rel_id.rsplit("/", 1)[-1]
    if "." not in base:
        return None
    return base.rsplit(".", 1)[-1].lower()


# ---------------------------------------------------------------------------
# Tree enumeration — lstat-typed, never follows a link


def _lstat_sig(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns)


def _symlink_kind(path):
    """Classify a symlink without following it into the scan: the target
    is probed with lstat only for kind naming (dangling / dir / file)."""
    try:
        tst = os.stat(path)  # target type probe; the LINK is never read
    except FileNotFoundError:
        return "dangling_symlink"
    except OSError:
        return "dangling_symlink"
    return "dir_symlink" if _stat.S_ISDIR(tst.st_mode) else "file_symlink"


def _walk(root_path, policy, hash_files):
    """Enumerate the whole external tree.  Returns (entries, sigs,
    size_mtime).  Boundary violations raise BoundaryError; a path that
    escapes the root raises path_escape."""
    entries = []
    sigs = {}
    size_mtime = {}
    root_path = os.path.abspath(str(root_path))

    def visit_dir(abs_dir, rel_prefix, spell_prefix, excluded_ref):
        try:
            children = sorted(os.scandir(abs_dir), key=lambda d: d.name)
        except OSError as exc:
            raise UnstableInputError(rel_prefix or ".") from exc
        nfc_seen = {}
        for child in children:
            spell = child.name
            nfc = _nfc(spell)
            rel_id = (rel_prefix + "/" + nfc).lstrip("/")
            spell_id = (spell_prefix + "/" + spell).lstrip("/")
            abs_child = os.path.join(abs_dir, spell)
            # containment — a path that would resolve outside the root
            # is a boundary error, never followed
            if os.path.commonpath(
                    (os.path.abspath(abs_child), root_path)) != root_path:
                raise BoundaryError("path_escape", rel_id)
            try:
                st = os.lstat(abs_child)
            except OSError as exc:
                raise UnstableInputError(rel_id) from exc
            sigs[rel_id] = _lstat_sig(st)
            size_mtime[rel_id] = {
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc).isoformat()}
            # step 1 — symlink (file/dir/dangling): BoundaryError
            if _stat.S_ISLNK(st.st_mode):
                raise BoundaryError(_symlink_kind(abs_child), rel_id)
            # step 2 — not a regular file or directory: BoundaryError
            if not _stat.S_ISREG(st.st_mode) \
                    and not _stat.S_ISDIR(st.st_mode):
                raise BoundaryError("non_regular_entry", rel_id)
            # step 3 — NFC-normalization collision: two distinct real
            # paths normalizing equal are never merged; the scan's
            # registrations are blocked
            if nfc in nfc_seen:
                raise BoundaryError("normalization_collision", rel_id)
            nfc_seen[nfc] = spell
            if _stat.S_ISDIR(st.st_mode):
                # step 4 — directory exclusion changes classification,
                # never visibility: the subtree is still enumerated
                if excluded_ref is not None:
                    reason, ref = "excluded_dir", excluded_ref
                    entries.append({
                        "relative_id": rel_id,
                        "original_spelling": spell_id,
                        "content_sha256": None,
                        "type": "dir",
                        "entry_reason": reason,
                        "policy_ref": ref})
                    visit_dir(abs_child, rel_id, spell_id, excluded_ref)
                else:
                    winner = _exclude_winner(policy, rel_id)
                    if winner is not None:
                        entries.append({
                            "relative_id": rel_id,
                            "original_spelling": spell_id,
                            "content_sha256": None,
                            "type": "dir",
                            "entry_reason": "excluded_dir",
                            "policy_ref": winner["policy_ref"]})
                        visit_dir(abs_child, rel_id, spell_id,
                                  winner["policy_ref"])
                    else:
                        entries.append({
                            "relative_id": rel_id,
                            "original_spelling": spell_id,
                            "content_sha256": None,
                            "type": "dir",
                            "entry_reason": "traversed_dir",
                            "policy_ref": None})
                        visit_dir(abs_child, rel_id, spell_id, None)
                continue
            # regular file
            if excluded_ref is not None:
                # inside an excluded subtree steps 6-9 do not run; the
                # inherited classification replaces them — but
                # always_hash is NEVER suppressed
                if _matches_any(policy["always_hash"], rel_id):
                    entries.append({
                        "relative_id": rel_id,
                        "original_spelling": spell_id,
                        "content_sha256": _hash_stable(abs_child, rel_id),
                        "type": "file",
                        "entry_reason": "included_hash",
                        "policy_ref": None})
                else:
                    entries.append({
                        "relative_id": rel_id,
                        "original_spelling": spell_id,
                        "content_sha256": None,
                        "type": "file",
                        "entry_reason": "excluded_no_hash",
                        "policy_ref": excluded_ref})
                continue
            if _matches_any(policy["always_hash"], rel_id):
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "content_sha256": _hash_stable(abs_child, rel_id),
                    "type": "file",
                    "entry_reason": "included_hash",
                    "policy_ref": None})
                continue
            winner = _exclude_winner(policy, rel_id)
            if winner is not None:
                if winner["hash_mode"] == "excluded_no_hash":
                    entries.append({
                        "relative_id": rel_id,
                        "original_spelling": spell_id,
                        "content_sha256": None,
                        "type": "file",
                        "entry_reason": "excluded_no_hash",
                        "policy_ref": winner["policy_ref"]})
                else:
                    entries.append({
                        "relative_id": rel_id,
                        "original_spelling": spell_id,
                        "content_sha256": _hash_stable(abs_child, rel_id),
                        "type": "file",
                        "entry_reason": "included_hash",
                        "policy_ref": None})
                continue
            if policy["include"] \
                    and not _matches_any(policy["include"], rel_id):
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "content_sha256": None,
                    "type": "file",
                    "entry_reason": "not_included",
                    "policy_ref": None})
                continue
            ext = _extension_of(rel_id)
            if ext is not None and ext in policy["document_extensions"]:
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "content_sha256": _hash_stable(abs_child, rel_id),
                    "type": "file",
                    "entry_reason": "document",
                    "policy_ref": None})
                continue
            entries.append({
                "relative_id": rel_id,
                "original_spelling": spell_id,
                "content_sha256": _hash_stable(abs_child, rel_id),
                "type": "file",
                "entry_reason": "included_hash",
                "policy_ref": None})
    visit_dir(root_path, "", "", None)
    entries.sort(key=lambda e: e["relative_id"])
    return entries, sigs, size_mtime


def _hash_stable(abs_path, rel_id):
    """Streamed content hash with per-file identity re-check before and
    after the read; a change is UnstableInputError."""
    try:
        before = _lstat_sig(os.lstat(abs_path))
    except OSError as exc:
        raise UnstableInputError(rel_id) from exc
    digest = _sha256_stream(abs_path)
    try:
        after = _lstat_sig(os.lstat(abs_path))
    except OSError as exc:
        raise UnstableInputError(rel_id) from exc
    if before != after:
        raise UnstableInputError(rel_id)
    return digest


def _recheck_tree(root_path, sigs):
    """The enclosing-inventory re-check after all reads: the whole tree
    is re-enumerated and compared; any added/removed/changed entry is
    UnstableInputError."""
    root_path = os.path.abspath(str(root_path))
    after = {}

    def visit(abs_dir, rel_prefix):
        try:
            children = sorted(os.scandir(abs_dir), key=lambda d: d.name)
        except OSError as exc:
            raise UnstableInputError(rel_prefix or ".") from exc
        for child in children:
            rel_id = (rel_prefix + "/" + _nfc(child.name)).lstrip("/")
            abs_child = os.path.join(abs_dir, child.name)
            try:
                st = os.lstat(abs_child)
            except OSError as exc:
                raise UnstableInputError(rel_id) from exc
            after[rel_id] = _lstat_sig(st)
            if _stat.S_ISDIR(st.st_mode) and not _stat.S_ISLNK(st.st_mode):
                visit(abs_child, rel_id)
    visit(root_path, "")
    if set(after) != set(sigs):
        diff = set(after) ^ set(sigs)
        raise UnstableInputError(sorted(diff)[0])
    for rel_id in sorted(sigs):
        if after[rel_id] != sigs[rel_id]:
            raise UnstableInputError(rel_id)


def tree_inventory(root_path):
    """SCHEMA section 7.6 — `p5-tree-inventory/1`, policy-blind, typed,
    no-follow: every entry the filesystem contains, a real streamed hash
    for every regular file, symlinks recorded AS symlinks."""
    root_path = os.path.abspath(str(root_path))
    entries = []

    def visit(abs_dir, rel_prefix, spell_prefix):
        try:
            children = sorted(os.scandir(abs_dir), key=lambda d: d.name)
        except OSError as exc:
            raise UnstableInputError(rel_prefix or ".") from exc
        for child in children:
            rel_id = (rel_prefix + "/" + _nfc(child.name)).lstrip("/")
            spell_id = (spell_prefix + "/" + child.name).lstrip("/")
            abs_child = os.path.join(abs_dir, child.name)
            try:
                st = os.lstat(abs_child)
            except OSError as exc:
                raise UnstableInputError(rel_id) from exc
            if _stat.S_ISLNK(st.st_mode):
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "type": "symlink",
                    "content_sha256": None})
            elif _stat.S_ISDIR(st.st_mode):
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "type": "dir",
                    "content_sha256": None})
                visit(abs_child, rel_id, spell_id)
            elif _stat.S_ISREG(st.st_mode):
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "type": "file",
                    "content_sha256": _hash_stable(abs_child, rel_id),
                    "bytes": st.st_size})
            else:
                entries.append({
                    "relative_id": rel_id,
                    "original_spelling": spell_id,
                    "type": "other",
                    "content_sha256": None})
    visit(root_path, "", "")
    entries.sort(key=lambda e: e["relative_id"])
    return {"schema": TREE_INVENTORY_SCHEMA,
            "root_kind": "external_scan_root",
            "entries": entries}


# ---------------------------------------------------------------------------
# Input validation — internal ValueError family (the sole conversion
# site turns these into ScanFailure; they never escape run_source_scan)


def _validate_inputs(root_path, root, classification_version, registry,
                     key_catalog, requested_use):
    if not isinstance(registry, gg_reuse.RegistryResolution):
        raise ScanRequestError(
            "invalid_scan_request", ["registry"],
            "must be the constructed RegistryResolution from "
            "load_registry — a bare dict or digest is not accepted")
    try:
        gg_reuse._validate_key_catalog(key_catalog)
    except ValueError as exc:
        raise ScanRequestError("invalid_scan_request", ["key_catalog"],
                               str(exc))
    if root_path is None:
        raise ScanRequestError("invalid_scan_request", ["root_path"],
                               "required")
    if not isinstance(root, (str, os.PathLike)):
        raise ScanRequestError("invalid_scan_request", ["root"],
                               "must be a path")
    if not isinstance(classification_version, str) \
            or not classification_version:
        raise ScanRequestError("invalid_scan_request",
                               ["classification_version"],
                               "must be a non-empty string")
    if requested_use is not None:
        try:
            kind, keys = gg_reuse.validate_requested_use(
                requested_use, key_catalog=key_catalog)
        except ValueError as exc:
            raise ScanRequestError("invalid_scan_request",
                                   ["requested_use"], str(exc))
        requested_use = {"kind": kind, "keys": list(keys)}
    return requested_use


# ---------------------------------------------------------------------------
# The scan


def scan_external_root(root_path, *, root, classification_version,
                       registry, key_catalog, scan_policy,
                       current_draft=None, requested_use=None):
    """Whole-root no-follow scan -> ScanResult (API section 2.2).

    Internal failures are BoundaryError / UnstableInputError /
    ScanRequestError (all ValueError) — the sole conversion site is
    run_source_scan; calling this directly exposes the raw exceptions."""
    requested_use = _validate_inputs(
        root_path, root, classification_version, registry, key_catalog,
        requested_use)
    policy = _validate_policy(scan_policy, classification_version,
                              registry)
    if current_draft is not None and (
            not isinstance(current_draft, str)
            or os.path.isabs(current_draft)):
        raise ScanRequestError("invalid_scan_request", ["current_draft"],
                               "must be a workspace-relative path or null")

    root_path = Path(root_path)
    try:
        st = os.lstat(str(root_path))
    except OSError as exc:
        raise ScanRequestError("invalid_scan_request", ["root_path"],
                               "unreadable: %s" % exc)
    if _stat.S_ISLNK(st.st_mode):
        raise BoundaryError(_symlink_kind(str(root_path)), "")
    if not _stat.S_ISDIR(st.st_mode):
        raise ScanRequestError("invalid_scan_request", ["root_path"],
                               "not a directory")

    started = time.monotonic()
    observed_utc = datetime.now(tz=timezone.utc).isoformat()

    files, sigs, size_mtime = _walk(str(root_path), policy, True)
    _recheck_tree(str(root_path), sigs)

    # scan_state (SCHEMA section 10): reuse_candidate iff a non-null
    # requested_use AND some file's content hash appears in some
    # registry entry's source_sha256; a scan NEVER emits reuse_ready.
    registry_hashes = {
        h for e in registry.document["entries"]
        for h in e["source_sha256"]}
    scan_state = "known_source"
    if requested_use is not None and any(
            f["content_sha256"] in registry_hashes for f in files):
        scan_state = "reuse_candidate"

    sealed = {
        "scan_id_version": SCAN_ID_VERSION,
        "files": files,
        "classification_version": classification_version,
        "registry_digest": registry.registry_digest,
        "scan_policy_digest": _sha256_text(_canonical_json(scan_policy)),
        "current_draft": current_draft,
        "requested_use": requested_use,
        "scan_state": scan_state,
    }
    preimage = dict(sealed)  # sealed receipt minus scan_id (section 10.1)
    scan_id = _sha256_text(_canonical_json(preimage))
    sealed["scan_id"] = scan_id
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "scan_id": scan_id,
        "sealed": sealed,
        "diagnostics": {
            "observed_utc": observed_utc,
            "duration_ms": duration_ms,
            "absolute_root": os.path.abspath(str(root_path)),
            "host_notes": "%s %s" % (sys.platform, platform.machine()),
            "size_mtime": size_mtime,
        },
    }


# ---------------------------------------------------------------------------
# Closed failure schema + the SOLE conversion site


def to_scan_failure(exc):
    """Convert a scan-internal exception to the closed ScanFailure wire
    object.  Called by exactly one place: run_source_scan."""
    if isinstance(exc, BoundaryError):
        reason = exc.kind
        status, code = _FAILURE_TABLE[reason]
        return {
            "status": status,
            "reason": reason,
            "relative_id": exc.relative_id,
            "boundary_kind": exc.kind,
            "required_fields": _FAILURE_PAYLOAD[status],
            "preserved_paths": [],
            "exit": code,
        }
    if isinstance(exc, UnstableInputError):
        status, code = _FAILURE_TABLE["unstable_input"]
        return {
            "status": status,
            "reason": "unstable_input",
            "relative_id": exc.relative_id,
            "boundary_kind": None,
            "required_fields": _FAILURE_PAYLOAD[status],
            "preserved_paths": [],
            "exit": code,
        }
    if isinstance(exc, ScanRequestError):
        reason = exc.reason if exc.reason in _FAILURE_TABLE \
            else "invalid_scan_request"
        status, code = _FAILURE_TABLE[reason]
        return {
            "status": status,
            "reason": reason,
            "relative_id": "",
            "boundary_kind": None,
            "required_fields": exc.fields or ["request"],
            "preserved_paths": [],
            "exit": code,
        }
    # any other ValueError -> the invalid_scan_request family
    status, code = _FAILURE_TABLE["invalid_scan_request"]
    return {
        "status": status,
        "reason": "invalid_scan_request",
        "relative_id": "",
        "boundary_kind": None,
        "required_fields": ["request"],
        "preserved_paths": [],
        "exit": code,
    }


def run_source_scan(root_path, *, root, classification_version, registry,
                    key_catalog, scan_policy, current_draft=None,
                    requested_use=None):
    """THE public entry point.  Every internal failure is returned as a
    ScanFailure through to_scan_failure — the ONLY conversion site; no
    raw BoundaryError/UnstableInputError/ValueError escapes."""
    try:
        return scan_external_root(
            root_path, root=root,
            classification_version=classification_version,
            registry=registry, key_catalog=key_catalog,
            scan_policy=scan_policy, current_draft=current_draft,
            requested_use=requested_use)
    except (BoundaryError, UnstableInputError, ValueError) as exc:
        return to_scan_failure(exc)


# ---------------------------------------------------------------------------
# Receipt publication — requires the held capability


def write_scan_receipt(root, scan_result, *, capability):
    """Write the sealed receipt + volatile diagnostics companion.

    Requires the held capability (gg_lock.assert_held, matching
    gg_publication.py:909): a missing, forged, released or misbound
    capability raises LockError BEFORE any write — nothing is silently
    written.  The sealed receipt goes through the P2 no-replace
    primitive; the diagnostics file is volatile and regenerable.
    NEVER writes 03_sources.md and never touches a generated view."""
    if not isinstance(scan_result, dict) \
            or "scan_id" not in scan_result \
            or "sealed" not in scan_result \
            or "diagnostics" not in scan_result:
        raise ValueError(
            "scan_result must be a ScanResult — a ScanFailure has no "
            "receipt to write")
    scan_id = scan_result["scan_id"]
    if not _is_hex64(scan_id) \
            or scan_result["sealed"].get("scan_id") != scan_id:
        raise ValueError("scan_result.scan_id must be the sealed 64-hex "
                         "scan id")
    gg_lock.assert_held(capability, root)
    root = Path(root)
    intake = root / "sources" / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    parent_identity = gg_fs.identity(intake, kind="directory")
    sealed_bytes = gg_fs.canonical_json_bytes(scan_result["sealed"])
    result = gg_fs.publish_bytes_noreplace(
        str(intake / ("%s.json" % scan_id)), sealed_bytes,
        parent_identity=parent_identity,
        existing_sha256=gg_fs.sha256_bytes(sealed_bytes))
    if result["state"] != "installed":
        raise ValueError(
            "sealed scan receipt not written: %s (%s)"
            % (result["reason"], result["path"]))
    diag_bytes = gg_fs.canonical_json_bytes(scan_result["diagnostics"])
    diag_path = intake / ("%s.diagnostics.json" % scan_id)
    tmp = intake / (".%s.diagnostics.tmp" % scan_id)
    with open(tmp, "wb") as f:
        f.write(diag_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, diag_path)   # volatile: regenerable, may be replaced
    return intake / ("%s.json" % scan_id)
