"""P5 read-only reuse resolver — frozen SCHEMA.md/API.md v19.

Implements stage-g006 API.md section 1 exactly:

- ``load_registry(registry_path, *, schema) -> RegistryResolution`` — the
  ONLY producer of the typed transport (SCHEMA section 2): validates
  ``schema_version == "p5-reuse-registry/3"``, resolves ``artifact_base``
  and ``p4_trust.packs_dir`` relative to the registry FILE's own
  directory (portable declaration + marker check, fail closed), and
  binds the load-time resolutions into the returned object.
  ``registry_digest`` covers the portable document only.
- ``load_accepted_catalog(catalog_path, *, registry, packs_dir)`` — the
  ONLY trusted-object construction route (SCHEMA section 6.2):
  ``p5-catalog-export/1`` parse -> section 4.3 key deserialization ->
  ``build_audit_index`` per declared pack -> ``accept_catalog`` with the
  file's named pinned authority.  Raises ``ValueError`` on any failure;
  the caller owns the per-operation mapping (section 6.1 sites 2/3).
- ``validate_requested_use(requested_use, *, key_catalog)`` — pure
  grammar/domain validation per SCHEMA section 4 + SPEC section 2.3.
- ``classify_source(registry, source_hash)`` — pure index lookup over
  ``registry.document``; no filesystem access, no trust verification.
- ``resolve_reuse(source_hash, requested_use, *, context)`` — the four
  condition check (SPEC section 2.2) with LIVE catalog trust per SCHEMA
  section 6.4 and identity per SCHEMA section 4, emitting the frozen
  ``ReuseVerdict`` wire type (API section 1.4) and the section 1.5
  closed reason set + total exit selector.

Boundary discipline (kept distinct, per the frozen sources):
- a malformed ``context`` (including ``context["packs_dir"]`` !=
  ``registry.resolved_packs_dir``) raises an escaping ``ValueError``
  naming the field — never a verdict (SCHEMA section 6.1 site 1);
- ``load_accepted_catalog`` failures are loader ``ValueError``; the
  mapping (resolver ``blocked / catalog_not_accepted`` vs registration
  ``not_committed / adoption_context_invalid``) is the CALLER's, not the
  loader's (V11-3);
- ``runtime_root != registry.resolved_base`` is a resolver VERDICT —
  ``blocked / runtime_base_mismatch`` — never an escaping error here
  and never a registration outcome token (V10-1);
- ``selection.byte_source.root`` is never a check operand in this
  module — the resolver compares ``context["runtime_root"]`` with
  ``context["registry"].resolved_base`` only (SCHEMA section 2 rule 3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import gg_rda_provenance as _prov
import gg_rda_research as _research

# ---------------------------------------------------------------------------
# Frozen constants (SCHEMA v19)

REGISTRY_SCHEMA = "p5-reuse-registry/3"
CATALOG_EXPORT_SCHEMA = "p5-catalog-export/1"

STATUSES = {"reuse_ready", "needs_excerpt", "unmapped", "blocked",
            "invalid_request"}

# API section 1.5 — the CLOSED reason set.
REASONS = {
    "invalid_request",
    "source_unmatched",
    "runtime_missing", "runtime_hash_mismatch", "runtime_base_mismatch",
    "coverage_incomplete",
    "lineage_missing", "lineage_invalid", "verification_absent",
    "authority_conflict",
    "role_mismatch", "duplicate_hash_collision",
    "catalog_not_accepted", "catalog_digest_mismatch",
    "audit_key_unresolved",
}

# API section 1.5 class table — every reason has exactly one class and the
# class fully determines the exit.
_REASON_CLASS = {
    "invalid_request": ("invalid_request", 2),
    "source_unmatched": ("unmapped", 2),
    "runtime_hash_mismatch": ("blocked", 2),
    "lineage_invalid": ("blocked", 2),
    "duplicate_hash_collision": ("blocked", 2),
    "role_mismatch": ("blocked", 2),
    "catalog_not_accepted": ("blocked", 2),
    "catalog_digest_mismatch": ("blocked", 2),
    "authority_conflict": ("blocked", 2),
    "runtime_base_mismatch": ("blocked", 2),
    "runtime_missing": ("needs_excerpt", 3),
    "coverage_incomplete": ("needs_excerpt", 3),
    "verification_absent": ("needs_excerpt", 3),
    "audit_key_unresolved": ("needs_excerpt", 3),
    "lineage_missing": ("needs_excerpt", 3),
}
assert set(_REASON_CLASS) == REASONS  # totality: closed set, one class each

# class precedence for the total selector (API section 1.5):
# invalid_request > blocked > unmapped > needs_excerpt > reuse_ready
_STATUS_PRECEDENCE = ["invalid_request", "blocked", "unmapped",
                      "needs_excerpt", "reuse_ready"]

# SCHEMA section 3.2 — role -> admissible requested_use.kind.
ROLE_USES = {
    "primary_finance_template": {"template_structure"},
    "secondary_finance_exemplar": {"template_structure",
                                   "narrative_reference"},
    "official_guideline": {"writing_rule"},
    "narrative_exemplar": {"narrative_reference"},
    "statistical_pack": {"statistical_observation"},
    "reference_only": set(),
}
ROLES = set(ROLE_USES)

USE_KINDS = {"writing_rule", "statistical_observation",
             "template_structure", "narrative_reference"}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DELIMITED = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}:[a-z0-9][a-z0-9._-]{0,63}$")
_AUTHORITY = {"verified", "unverified", "rejected"}
_AUTHORITY_RANK = {"verified": 0, "unverified": 1, "rejected": 2}
_LINEAGE_REL = {"derived_excerpt", "derived_snapshot", "verbatim_copy",
                "shared_artifact"}
_CATALOG_STATUS = {"verified_observation", "quarantined", "exploratory"}


def _canonical_json(value):
    """SCHEMA section 7.4 canonical JSON: UTF-8, sorted keys, compact
    separators, ensure_ascii=False, no trailing whitespace."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_hex64(value):
    return isinstance(value, str) and bool(_HEX64.match(value))


# ---------------------------------------------------------------------------
# RegistryResolution — SCHEMA section 2 typed transport (V14-1).  The ONLY
# producer is load_registry(); it is never serialized or persisted.


@dataclass(frozen=True)
class RegistryResolution:
    """Loader-bound transport: the validated portable document plus its
    load-time resolutions (never serialized, never part of any digest)."""
    document: dict
    registry_path: Path
    resolved_base: Path
    resolved_packs_dir: "Path | None"
    registry_digest: str  # sha256(canonical_json(document)) — document only


def _require_hex64(value, field):
    if not _is_hex64(value):
        raise ValueError("%s must be a lowercase 64-hex string" % field)
    return value


def _resolve_declared_root(registry_dir, declaration, field):
    """SCHEMA section 2 / 6.1 portable resolution: normalized join of the
    registry file's own directory with ``relative_root``; ``marker`` must
    exist at the resolved root — fail closed, no fallback."""
    if not isinstance(declaration, dict):
        raise ValueError("%s must be an object" % field)
    relative_root = declaration.get("relative_root")
    marker = declaration.get("marker")
    if not isinstance(relative_root, str) or not relative_root:
        raise ValueError("%s.relative_root must be a non-empty string"
                         % field)
    if os.path.isabs(relative_root):
        raise ValueError("%s.relative_root must be relative" % field)
    if not isinstance(marker, str) or not marker or "/" in marker \
            or "\\" in marker:
        raise ValueError("%s.marker must be a single path segment" % field)
    resolved = Path(
        os.path.normpath(os.path.join(str(registry_dir), relative_root)))
    marker_path = resolved / marker
    if not marker_path.exists():
        raise ValueError(
            "%s marker %r absent at resolved root %s — load fails closed"
            % (field, marker, resolved))
    return resolved


def _validate_key_ref(key_ref, kind, field):
    """SCHEMA section 4 key_ref shape + P5-domain check (section 4.1).
    Returns the normalized key_ref dict (audit_key discriminator restored
    on output per section 4.2 rule 7).  Raises ValueError on any
    violation."""
    if not isinstance(key_ref, dict):
        raise ValueError("%s must be a key_ref object" % field)
    if kind == "statistical_observation":
        if key_ref.get("kind") != "audit_key":
            raise ValueError(
                "%s.kind must be 'audit_key' for statistical_observation"
                % field)
        # P5 domain check BEFORE parent normalization (section 4.2 rule 1).
        pack_id = key_ref.get("pack_id")
        if not isinstance(pack_id, str) or not _SOURCE_ID.match(pack_id):
            raise ValueError(
                "%s.pack_id outside the P5 domain grammar" % field)
        for f in ("records_file_sha256", "raw_line_sha256"):
            v = key_ref.get(f)
            if not _is_hex64(v):
                raise ValueError(
                    "%s.%s must be lowercase 64-hex (uppercase is rejected, "
                    "never silently lowercased)" % (field, f))
        line = key_ref.get("physical_jsonl_line_1based")
        # explicit bool rejection — isinstance alone is insufficient
        # (True == 1 in Python with different serialized spellings).
        if isinstance(line, bool) or type(line) is not int or line < 1:
            raise ValueError(
                "%s.physical_jsonl_line_1based must be an int >= 1 "
                "(bool is rejected explicitly)" % field)
        # parent's real normalization — never re-derived locally.
        norm = _prov.normalize_audit_key(
            {k: key_ref[k] for k in ("pack_id", "records_file_sha256",
                                     "physical_jsonl_line_1based",
                                     "raw_line_sha256")})
        return {"kind": "audit_key", **_prov.audit_key_dict(norm)}
    # delimited kinds
    if key_ref.get("kind") != "delimited":
        raise ValueError(
            "%s.kind must be 'delimited' for %s" % (field, kind))
    value = key_ref.get("value")
    if not isinstance(value, str) or not _DELIMITED.match(value):
        raise ValueError(
            "%s.value must match the delimited grammar "
            "<id>:<id> ([a-z0-9][a-z0-9._-]{0,63})" % field)
    return {"kind": "delimited", "value": value}


def canonical_key(key_ref, kind):
    """SCHEMA section 4.3 canonical key serialization: structured canonical
    JSON of ``audit_key_dict(normalize_audit_key(...))`` for
    ``statistical_observation`` (the parent's ``catalog_content_digest``
    key form — NO unit-separator join); the plain ``value`` string for
    delimited kinds."""
    if kind == "statistical_observation":
        fields = {k: key_ref[k] for k in ("pack_id", "records_file_sha256",
                                          "physical_jsonl_line_1based",
                                          "raw_line_sha256")}
        return json.dumps(
            _prov.audit_key_dict(_prov.normalize_audit_key(fields)),
            sort_keys=True, ensure_ascii=False)
    return key_ref["value"]


def _key_sort_tuple(key_ref, kind):
    """Canonical ordering key (SCHEMA section 4.2 rule 2): the AuditKey
    tuple for statistical keys, the value string for delimited keys."""
    if kind == "statistical_observation":
        k = _prov.normalize_audit_key(
            {f: key_ref[f] for f in ("pack_id", "records_file_sha256",
                                     "physical_jsonl_line_1based",
                                     "raw_line_sha256")})
        return (0,) + k
    return (1, key_ref["value"])


def _validate_key_catalog(key_catalog):
    """SCHEMA section 5 shape: {kind: {canonical-serialization:
    {authority, catalog_digest, receipt_sha256}}}."""
    if not isinstance(key_catalog, dict):
        raise ValueError("key_catalog must be an object")
    for kind, entries in key_catalog.items():
        if not isinstance(kind, str) or not isinstance(entries, dict):
            raise ValueError("key_catalog must map kind -> object")
        for ser, claim in entries.items():
            if not isinstance(ser, str) or not isinstance(claim, dict):
                raise ValueError(
                    "key_catalog[%r] must map canonical key -> object"
                    % kind)
            if claim.get("authority") not in _AUTHORITY:
                raise ValueError(
                    "key_catalog[%r][%r].authority must be one of %s"
                    % (kind, ser, sorted(_AUTHORITY)))
            cd = claim.get("catalog_digest")
            if cd is not None and not _is_hex64(cd):
                raise ValueError(
                    "key_catalog[%r][%r].catalog_digest must be 64-hex "
                    "or null" % (kind, ser))
            rc = claim.get("receipt_sha256")
            if rc is not None and not _is_hex64(rc):
                raise ValueError(
                    "key_catalog[%r][%r].receipt_sha256 must be 64-hex "
                    "or null" % (kind, ser))


def _validate_registry_document(document):
    """The portable registry document (SCHEMA sections 1-3)."""
    if not isinstance(document, dict):
        raise ValueError("registry document must be a JSON object")
    observed = document.get("schema_version")
    if observed != REGISTRY_SCHEMA:
        raise ValueError(
            "registry schema_version %r != expected %r"
            % (observed, REGISTRY_SCHEMA))
    if not isinstance(document.get("classifier_version"), str):
        raise ValueError("registry classifier_version must be a string")
    base = document.get("artifact_base")
    if not isinstance(base, dict) \
            or base.get("kind") != "installed_runtime":
        raise ValueError(
            "registry artifact_base.kind must be 'installed_runtime' "
            "(never 'workspace')")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise ValueError("registry entries must be a list")
    seen_ids = set()
    for i, entry in enumerate(entries):
        f = "entries[%d]" % i
        if not isinstance(entry, dict):
            raise ValueError("%s must be an object" % f)
        sid = entry.get("source_id")
        if not isinstance(sid, str) or not _SOURCE_ID.match(sid):
            raise ValueError("%s.source_id must match %s"
                             % (f, _SOURCE_ID.pattern))
        if sid in seen_ids:
            raise ValueError("duplicate source_id %r" % sid)
        seen_ids.add(sid)
        hashes = entry.get("source_sha256")
        if not isinstance(hashes, list) or not hashes:
            raise ValueError("%s.source_sha256 must be a non-empty list"
                             % f)
        for h in hashes:
            _require_hex64(h, "%s.source_sha256[]" % f)
        if not isinstance(entry.get("class"), str) or not entry["class"]:
            raise ValueError("%s.class must be a non-empty string" % f)
        if entry.get("role") not in ROLES:
            raise ValueError("%s.role must be one of %s"
                             % (f, sorted(ROLES)))
        if not isinstance(entry.get("runtime_present"), bool):
            raise ValueError("%s.runtime_present must be a bool" % f)
        if entry.get("runtime") is not None \
                and not isinstance(entry["runtime"], str):
            raise ValueError("%s.runtime must be a string or null" % f)
        arts = entry.get("artifacts")
        if not isinstance(arts, list):
            raise ValueError("%s.artifacts must be a list" % f)
        for j, art in enumerate(arts):
            af = "%s.artifacts[%d]" % (f, j)
            if not isinstance(art, dict):
                raise ValueError("%s must be an object" % af)
            rp = art.get("relative_path")
            if not isinstance(rp, str) or not rp \
                    or os.path.isabs(rp) or ".." in rp.split("/"):
                raise ValueError(
                    "%s.relative_path must be a root-relative path" % af)
            _require_hex64(art.get("sha256"), "%s.sha256" % af)
            if not isinstance(art.get("bytes"), int) \
                    or isinstance(art["bytes"], bool) \
                    or art["bytes"] < 0:
                raise ValueError("%s.bytes must be an int >= 0" % af)
        coverage = entry.get("coverage")
        if coverage is not None:
            if not isinstance(coverage, dict):
                raise ValueError("%s.coverage must be an object" % f)
            for kind, cov in coverage.items():
                if kind not in USE_KINDS:
                    raise ValueError(
                        "%s.coverage kind %r is not a known use kind"
                        % (f, kind))
                if not isinstance(cov, dict) \
                        or not isinstance(cov.get("keys"), list):
                    raise ValueError(
                        "%s.coverage[%r].keys must be a list" % (f, kind))
                for k, claim in enumerate(cov["keys"]):
                    cf = "%s.coverage[%r].keys[%d]" % (f, kind, k)
                    if not isinstance(claim, dict):
                        raise ValueError("%s must be an object" % cf)
                    _validate_key_ref(claim.get("key_ref"), kind,
                                      "%s.key_ref" % cf)
                    if claim.get("authority") not in _AUTHORITY:
                        raise ValueError(
                            "%s.authority must be one of %s"
                            % (cf, sorted(_AUTHORITY)))
                        # claim digests optional, 64-hex when present
                    for df in ("catalog_digest", "receipt_sha256"):
                        dv = claim.get(df)
                        if dv is not None and not _is_hex64(dv):
                            raise ValueError("%s.%s must be 64-hex or null"
                                             % (cf, df))
        lineage = entry.get("lineage")
        if lineage is not None:
            if not isinstance(lineage, list):
                raise ValueError("%s.lineage must be a list" % f)
            for j, link in enumerate(lineage):
                lf = "%s.lineage[%d]" % (f, j)
                if not isinstance(link, dict):
                    raise ValueError("%s must be an object" % lf)
                if not isinstance(link.get("to"), str) or not link["to"]:
                    raise ValueError("%s.to must be a non-empty string" % lf)
                if link.get("relationship") not in _LINEAGE_REL:
                    raise ValueError("%s.relationship must be one of %s"
                                     % (lf, sorted(_LINEAGE_REL)))
                if link.get("verification") not in _AUTHORITY:
                    raise ValueError("%s.verification must be one of %s"
                                     % (lf, sorted(_AUTHORITY)))
                if link.get("verifier") is not None \
                        and not isinstance(link["verifier"], str):
                    raise ValueError("%s.verifier must be a string or null"
                                     % lf)
                if link.get("receipt_sha256") is not None \
                        and not _is_hex64(link["receipt_sha256"]):
                    raise ValueError(
                        "%s.receipt_sha256 must be 64-hex or null" % lf)
        if entry.get("build_allowlist_ref") is not None \
                and not isinstance(entry["build_allowlist_ref"], str):
            raise ValueError("%s.build_allowlist_ref must be a string "
                             "or null" % f)
    trust = document.get("p4_trust")
    if trust is not None:
        if not isinstance(trust, dict):
            raise ValueError("p4_trust must be an object")
        packs = trust.get("packs")
        if packs is not None:
            if not isinstance(packs, dict):
                raise ValueError("p4_trust.packs must be an object")
            for pid, pack in packs.items():
                pf = "p4_trust.packs[%r]" % pid
                if not isinstance(pack, dict):
                    raise ValueError("%s must be an object" % pf)
                if not isinstance(pack.get("records_relpath"), str) \
                        or not pack["records_relpath"]:
                    raise ValueError(
                        "%s.records_relpath must be a non-empty string"
                        % pf)
                _require_hex64(pack.get("records_file_sha256"),
                               "%s.records_file_sha256" % pf)
        indexes = trust.get("indexes")
        if indexes is not None:
            if not isinstance(indexes, list) \
                    or not all(_is_hex64(x) for x in indexes):
                raise ValueError(
                    "p4_trust.indexes must be a list of 64-hex strings")
            if indexes != sorted(indexes):
                raise ValueError("p4_trust.indexes must be sorted")
    return document


def load_registry(registry_path, *, schema=None):
    """Validate the portable registry document and bind its load-time
    resolution — the ONLY producer of ``RegistryResolution``.

    ``schema`` is the expected registry schema token (defaults to the
    frozen ``p5-reuse-registry/3``); an argument naming anything else is
    a caller error.  ``ValueError`` on any violation (schema mismatch,
    missing marker, malformed entry).  No writes."""
    if schema is None:
        schema = REGISTRY_SCHEMA
    if schema != REGISTRY_SCHEMA:
        raise ValueError(
            "unsupported registry schema expectation %r (this module "
            "implements %r)" % (schema, REGISTRY_SCHEMA))
    # normalized absolute — SCHEMA section 2 binds resolution to the
    # registry file's own directory, so a relative caller argument must
    # still yield an absolute resolved_base for the rule-3 comparison.
    path = Path(os.path.normpath(str(Path(registry_path).absolute())))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("registry unreadable at %s: %s" % (path, exc))
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("registry at %s is not valid JSON: %s"
                         % (path, exc))
    _validate_registry_document(document)
    registry_dir = path.parent
    resolved_base = _resolve_declared_root(
        registry_dir,
        document["artifact_base"].get("declaration"),
        "artifact_base.declaration")
    resolved_packs_dir = None
    trust = document.get("p4_trust") or {}
    packs_decl = trust.get("packs_dir")
    if packs_decl is not None:
        resolved_packs_dir = _resolve_declared_root(
            registry_dir, packs_decl, "p4_trust.packs_dir")
    digest = _sha256_text(_canonical_json(document))
    return RegistryResolution(
        document=document,
        registry_path=path,
        resolved_base=resolved_base,
        resolved_packs_dir=resolved_packs_dir,
        registry_digest=digest)


# ---------------------------------------------------------------------------
# Catalog construction — SCHEMA section 6.2, the ONLY trusted-object route.


def load_accepted_catalog(catalog_path, *, registry, packs_dir):
    """Construct the trusted catalog object — the ONLY route (SCHEMA
    section 6.2).

    Returns ``(catalog, indexes)``: the ``AcceptedCatalog`` produced by
    the parent's ``accept_catalog`` plus the ``{pack_id: {AuditKey:
    RawRow}}`` verified index map built by the parent's
    ``build_audit_index``.

    Raises ``ValueError`` on ANY failure — this loader prescribes no
    caller mapping (V11-3): ``resolve_reuse`` maps a construction
    failure to ``blocked / catalog_not_accepted``;
    ``register_source_snapshot`` maps it to the SCHEMA section 8.3
    step-1b admission rejection ``not_committed /
    adoption_context_invalid``, exit 2.  A ``packs_dir`` argument that
    differs from ``registry.resolved_packs_dir`` is a construction
    failure raised HERE (section 6.1 site 2) — distinct from the
    ``context["packs_dir"]`` field claim (site 1: a malformed-context
    ``ValueError`` at ``resolve_reuse``, never a verdict)."""
    if not isinstance(registry, RegistryResolution):
        raise ValueError(
            "registry must be the constructed RegistryResolution "
            "produced by load_registry")
    resolved = registry.resolved_packs_dir
    if (packs_dir is None) != (resolved is None) or (
            packs_dir is not None and Path(packs_dir) != resolved):
        raise ValueError(
            "packs_dir %r must equal registry.resolved_packs_dir %r "
            "(SCHEMA section 6.1)" % (packs_dir, resolved))
    packs_dir = Path(packs_dir) if packs_dir is not None else None
    try:
        raw = Path(catalog_path).read_bytes()
    except OSError as exc:
        raise ValueError("catalog unreadable at %s: %s"
                         % (catalog_path, exc))
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("catalog at %s is not valid JSON: %s"
                         % (catalog_path, exc))
    if not isinstance(document, dict) \
            or document.get("schema") != CATALOG_EXPORT_SCHEMA:
        raise ValueError(
            "catalog schema %r != expected %r"
            % (None if not isinstance(document, dict)
               else document.get("schema"), CATALOG_EXPORT_SCHEMA))
    authority = document.get("authority")
    if not isinstance(authority, str) or not authority:
        raise ValueError("catalog authority must name a "
                         "PINNED_CATALOG_AUTHORITIES key")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, dict):
        raise ValueError("catalog entries must be an object keyed by the "
                         "section 4.3 canonical serialization")
    entries = {}
    pack_ids = set()
    for ser, entry in raw_entries.items():
        try:
            key_dict = json.loads(ser)
            key = _prov.normalize_audit_key(key_dict)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                "catalog entry key is not a section 4.3 canonical "
                "audit-key serialization: %r (%s)" % (ser, exc))
        if not isinstance(entry, dict):
            raise ValueError("catalog entry for key %r is not an object"
                             % ser)
        entries[key] = entry
        pack_ids.add(key[0])
    trust_packs = ((registry.document.get("p4_trust") or {})
                   .get("packs") or {})
    indexes = {}
    for pack_id in sorted(pack_ids):
        decl = trust_packs.get(pack_id)
        if decl is None:
            raise ValueError(
                "catalog references pack_id %r absent from "
                "p4_trust.packs" % pack_id)
        if packs_dir is None:
            # a referenced pack has no base to resolve against when the
            # registry left p4_trust.packs_dir undeclared (resolved None
            # is legal per section 6.1) — a construction failure in the
            # loader's own ValueError vocabulary, never a raw TypeError.
            raise ValueError(
                "catalog references pack_id %r but the registry "
                "declares no p4_trust.packs_dir — records_relpath %r "
                "has no packs base to resolve against"
                % (pack_id, decl["records_relpath"]))
        records_path = packs_dir / decl["records_relpath"]
        try:
            indexes[pack_id] = _prov.build_audit_index(
                records_path, pack_id, decl["records_file_sha256"])
        except Exception as exc:
            raise ValueError(
                "verified index construction failed for pack %r: %s"
                % (pack_id, exc))
    try:
        catalog = _research.accept_catalog(
            entries, verified_indexes=indexes, authority=authority)
    except ValueError as exc:
        raise ValueError("accept_catalog rejected the export: %s" % exc)
    return catalog, indexes


# ---------------------------------------------------------------------------
# requested_use grammar (SPEC section 2.3 + SCHEMA section 4)


def validate_requested_use(requested_use, *, key_catalog):
    """Pure grammar/domain validation — ``(kind, tuple[key_ref])``.

    Normalizes each ``key_ref`` via SCHEMA section 4 (structured AuditKey
    on the declared P5 domain with explicit bool rejection for
    ``statistical_observation``; the delimited grammar for the rest) and
    validates the ``key_catalog`` shape (SCHEMA section 5).  Keys MUST be
    non-empty, canonically ordered and duplicate-free.  Any violation is
    a ``ValueError`` — the caller maps it (``invalid_request`` at the
    resolver; ``selection_invalid`` at registration admission)."""
    _validate_key_catalog(key_catalog)
    if not isinstance(requested_use, dict):
        raise ValueError("requested_use must be an object")
    kind = requested_use.get("kind")
    if kind not in USE_KINDS:
        raise ValueError(
            "requested_use.kind %r is not one of %s"
            % (kind, sorted(USE_KINDS)))
    keys = requested_use.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ValueError("requested_use.keys must be a non-empty list")
    normalized = [
        _validate_key_ref(k, kind, "requested_use.keys[%d]" % i)
        for i, k in enumerate(keys)]
    ordered = [_key_sort_tuple(k, kind) for k in normalized]
    if ordered != sorted(ordered):
        raise ValueError(
            "requested_use.keys must be canonically ordered "
            "(SCHEMA section 4.2 rule 2)")
    if len(set(ordered)) != len(ordered):
        raise ValueError("requested_use.keys must be duplicate-free")
    return kind, tuple(normalized)


# ---------------------------------------------------------------------------
# Pure registry index (no filesystem, no trust verification)


def classify_source(registry, source_hash):
    """Pure index lookup over ``registry.document`` — every entry whose
    ``source_sha256`` list contains ``source_hash``.  No filesystem
    access, no trust verification."""
    if not isinstance(registry, RegistryResolution):
        raise ValueError(
            "registry must be the constructed RegistryResolution "
            "produced by load_registry")
    if not _is_hex64(source_hash):
        raise ValueError("source_hash must be a lowercase 64-hex string")
    return [e for e in registry.document["entries"]
            if source_hash in e["source_sha256"]]


# ---------------------------------------------------------------------------
# Context validation — API section 1.2 is the single source of truth.  A
# missing or wrongly-typed field raises ValueError naming the field; the
# error ESCAPES and is never converted into a verdict (V15-1).

_PATH_TYPES = (str, os.PathLike)


def _ctx_field(context, name):
    if name not in context:
        raise ValueError("context is missing required field %r" % name)
    return context[name]


def _validate_context(context, *, statistical):
    if not isinstance(context, dict):
        raise ValueError("context must be a dict carrying the API "
                         "section 1.2 fields")
    root = _ctx_field(context, "root")
    if not isinstance(root, _PATH_TYPES):
        raise ValueError("context['root'] must be a path")
    runtime_root = _ctx_field(context, "runtime_root")
    if not isinstance(runtime_root, _PATH_TYPES):
        raise ValueError("context['runtime_root'] must be a path")
    registry = _ctx_field(context, "registry")
    if not isinstance(registry, RegistryResolution):
        raise ValueError(
            "context['registry'] must be the constructed "
            "RegistryResolution produced by load_registry — a bare dict "
            "or digest is not accepted")
    key_catalog = _ctx_field(context, "key_catalog")
    try:
        _validate_key_catalog(key_catalog)
    except ValueError as exc:
        raise ValueError("context['key_catalog'] invalid: %s" % exc)
    catalog = _ctx_field(context, "catalog")
    if catalog is not None and not isinstance(
            catalog, _research.AcceptedCatalog):
        raise ValueError(
            "context['catalog'] must be the constructed AcceptedCatalog "
            "produced by load_accepted_catalog/accept_catalog, or None — "
            "a serialized dict or a hand-built object is never a trust "
            "source")
    catalog_digest = _ctx_field(context, "catalog_digest")
    if catalog is None:
        if catalog_digest is not None:
            raise ValueError(
                "context['catalog_digest'] must be None iff "
                "context['catalog'] is None")
    elif not _is_hex64(catalog_digest):
        raise ValueError(
            "context['catalog_digest'] must be a 64-hex string when a "
            "catalog is bound")
    indexes = _ctx_field(context, "indexes")
    if not isinstance(indexes, dict):
        raise ValueError(
            "context['indexes'] must be the {pack_id: {AuditKey: RawRow}} "
            "mapping built by build_audit_index")
    scan_policy = context.get("scan_policy")
    if scan_policy is not None and not isinstance(scan_policy, dict):
        raise ValueError("context['scan_policy'] must be an object or None")
    # packs_dir — the caller's packs-root claim; a PRESENT value
    # inconsistent with the bound registry.resolved_packs_dir is a
    # malformed context (SCHEMA section 6.1 site 1): ValueError naming
    # the field, escaping, NEVER a verdict.  The field itself is required
    # iff the request carries statistical keys (API section 1.2).
    resolved_packs = registry.resolved_packs_dir
    if "packs_dir" in context:
        packs_dir = context["packs_dir"]
        if packs_dir is None:
            if resolved_packs is not None:
                raise ValueError(
                    "context['packs_dir'] is None but the registry "
                    "declares p4_trust.packs_dir resolving to %s"
                    % resolved_packs)
        else:
            if not isinstance(packs_dir, _PATH_TYPES):
                raise ValueError(
                    "context['packs_dir'] must be a path or None")
            if resolved_packs is None or Path(packs_dir) != resolved_packs:
                raise ValueError(
                    "context['packs_dir'] %r does not equal the bound "
                    "registry.resolved_packs_dir %r"
                    % (packs_dir, resolved_packs))
    else:
        packs_dir = None
        if statistical:
            raise ValueError(
                "context is missing required field 'packs_dir' for a "
                "statistical request")
    return {
        "root": Path(root),
        "runtime_root": Path(runtime_root),
        "registry": registry,
        "key_catalog": key_catalog,
        "catalog": catalog,
        "catalog_digest": catalog_digest,
        "indexes": indexes,
        "scan_policy": scan_policy,
        "packs_dir": Path(packs_dir) if packs_dir is not None else None,
    }


# ---------------------------------------------------------------------------
# The resolver


def _entry_artifact_base(entry, runtime_root, packs_dir):
    """Artifact root per SCHEMA section 3.2: a statistical_pack entry's
    artifacts are relative to resolved_packs_dir (the section 6.1 packs
    root), every other entry's to the section 2 artifact base
    (== runtime_root after the rule-3 check)."""
    if entry.get("role") == "statistical_pack":
        return packs_dir
    return runtime_root


def _excerpt_artifact(entry):
    arts = entry.get("artifacts") or []
    if arts:
        return arts[0]["relative_path"]
    rt = entry.get("runtime")
    return rt if isinstance(rt, str) else ""


def _range_hint(key_ref, kind):
    if kind == "statistical_observation":
        line = key_ref["physical_jsonl_line_1based"]
        return {"kind": "lines", "start_line": line, "end_line": line}
    return {"kind": "whole_file"}


def _lineage_for(entry, artifact_rel):
    for link in entry.get("lineage") or []:
        if link.get("to") == artifact_rel:
            return link
    return None


def _disambiguate(candidates, kind):
    """SPEC section 2.4 collision rule — never a first-entry pick.

    The request's own attributes may separate hash-colliding candidates:
    role admissibility first (SCHEMA section 3.2), then per-kind
    coverage, then a class that singles out exactly one survivor.
    Returns (entry, disambiguator) or (None, None) when no attribute of
    the request separates the candidates."""
    admissible = [e for e in candidates
                  if kind in ROLE_USES.get(e["role"], set())]
    if len(admissible) == 1:
        return admissible[0], "role:%s" % admissible[0]["role"]
    pool = admissible if admissible else candidates
    covering = [e for e in pool
                if kind in (e.get("coverage") or {})]
    if len(covering) == 1:
        return covering[0], "use:%s" % kind
    if len(covering) > 1:
        # every survivor still admissible and covering: a class held by
        # exactly one of them separates it from the rest.
        classes = [e.get("class") for e in covering]
        unique = [e for e in covering if classes.count(e["class"]) == 1]
        if len(unique) == 1:
            return unique[0], "class:%s" % unique[0]["class"]
    return None, None


def _verdict(source_hash, requested_use, reasons,
             selected=None, disambiguator=None,
             satisfied=(), unsatisfied=(), excerpt=(),
             artifacts=None, lineage=None, verification=None,
             catalog=None):
    """Derive status + exit from the UNION of reasons via the fixed API
    section 1.5 precedence (total selector — every reason has exactly one
    class, so a failure can never yield exit 0)."""
    reasons = sorted(set(reasons))
    status_rank = min(
        (_STATUS_PRECEDENCE.index(_REASON_CLASS[r][0]) for r in reasons),
        default=len(_STATUS_PRECEDENCE) - 1)
    status = _STATUS_PRECEDENCE[status_rank]
    # exit follows the winning status class (API section 1.5: the class
    # fully determines the exit — a mixed union does not blend codes)
    exit_code = {"reuse_ready": 0, "needs_excerpt": 3}.get(status, 2)
    cat_evidence = {"live_accepted": False, "digest": None}
    if catalog is not None:
        try:
            cat_evidence = {
                "live_accepted": getattr(catalog, "accepted", False) is True,
                "digest": getattr(catalog, "digest", None),
            }
        except Exception:
            cat_evidence = {"live_accepted": False, "digest": None}
    return {
        "status": status,
        "source_hash": source_hash,
        "requested_use": requested_use,
        "reasons": reasons,
        "satisfied": list(satisfied),
        "unsatisfied": list(unsatisfied),
        "selected_entry": selected,
        "disambiguator": disambiguator,
        "evidence": {
            "artifacts": artifacts or [],
            "lineage": lineage,
            "verification": verification,
            "catalog": cat_evidence,
        },
        "needed_excerpt": list(excerpt),
        "exit": exit_code,
    }


def resolve_reuse(source_hash, requested_use, *, context):
    """Full four-condition check (SPEC section 2.2) against
    ``context['runtime_root']`` — the deployment's OWN base, compared
    with ``context['registry'].resolved_base`` (SCHEMA section 2 rule 3)
    — with trust per SCHEMA section 6.4 (LIVE acceptance) and identity
    per SCHEMA section 4.  ``context`` is mandatory (keyword-only):
    omitting it is a binding ``TypeError``; a malformed context raises an
    escaping ``ValueError`` naming the field — never a verdict."""
    # --- context validation first: presence and types are knowable
    # before the request grammar is inspected (a statistical request
    # additionally requires the packs_dir field — section 1.2).
    wants_statistical = isinstance(requested_use, dict) \
        and requested_use.get("kind") == "statistical_observation"
    ctx = _validate_context(context, statistical=wants_statistical)
    registry = ctx["registry"]
    runtime_root = ctx["runtime_root"]
    packs_dir = ctx["packs_dir"]
    key_catalog = ctx["key_catalog"]
    catalog = ctx["catalog"]
    catalog_digest = ctx["catalog_digest"]
    indexes = ctx["indexes"]

    reasons = set()
    satisfied, unsatisfied, excerpt = [], [], []
    artifact_evidence, lineage_evidence, verification_evidence = \
        [], None, None

    # --- request grammar (invalid_request)
    if not _is_hex64(source_hash):
        reasons.add("invalid_request")
        return _verdict(source_hash, requested_use, reasons,
                        catalog=catalog)
    try:
        kind, keys = validate_requested_use(
            requested_use, key_catalog=key_catalog)
    except ValueError:
        reasons.add("invalid_request")
        return _verdict(source_hash, requested_use, reasons,
                        catalog=catalog)

    # --- SCHEMA section 2 rule 3: deployment base vs registry-bound base.
    # A mismatch is a resolver VERDICT (blocked/runtime_base_mismatch),
    # never a context ValueError, and selection.byte_source.root is never
    # an operand.
    if runtime_root != registry.resolved_base:
        reasons.add("runtime_base_mismatch")

    # --- condition 1: source match
    candidates = classify_source(registry, source_hash)
    if not candidates:
        reasons.add("source_unmatched")
        return _verdict(source_hash, requested_use, reasons,
                        catalog=catalog)

    disambiguator = None
    if len(candidates) == 1:
        entry = candidates[0]
    else:
        entry, disambiguator = _disambiguate(candidates, kind)
        if entry is None:
            reasons.add("duplicate_hash_collision")
            return _verdict(source_hash, requested_use, reasons,
                            unsatisfied=[k for k in keys],
                            excerpt=[{
                                "key_ref": k,
                                "reason": "coverage_incomplete",
                                "artifact": "",
                                "range_hint": _range_hint(k, kind)}
                                for k in keys],
                            catalog=catalog)
    selected = entry["source_id"]

    # --- role -> use admissibility (SCHEMA section 3.2)
    if kind not in ROLE_USES[entry["role"]]:
        reasons.add("role_mismatch")

    # --- condition 2: artifact present + measured hash == declared
    base = _entry_artifact_base(entry, runtime_root, packs_dir)
    artifact_missing = False
    artifact_mismatch = False
    for art in entry.get("artifacts") or []:
        rel = art["relative_path"]
        observed = None
        exists = False
        if base is not None:
            p = Path(base) / rel
            try:
                if p.is_file():
                    exists = True
                    observed = _prov.sha256_file(p)
            except OSError:
                exists = False
            # API section 1.4: evidence.artifacts[].path is relative to
            # runtime_root — rebase the emitted path from the ACTUAL
            # lookup target (packs-dir-relative for a statistical_pack
            # per SCHEMA section 3.2; the registry's declared
            # relative_path is looked up unchanged).
            emitted = Path(
                os.path.relpath(p, runtime_root)).as_posix()
        else:
            emitted = rel
        artifact_evidence.append({
            "path": emitted,
            "exists": exists,
            "declared_sha256": art["sha256"],
            "observed_sha256": observed,
        })
        if not exists:
            artifact_missing = True
            reasons.add("runtime_missing")
        elif observed != art["sha256"]:
            artifact_mismatch = True
            reasons.add("runtime_hash_mismatch")

    # --- per-key evaluation: condition 3 (coverage, effective authority
    # = min of the registry claim and the key-catalog claim, SCHEMA
    # sections 3.3/5.1) and condition 4 (lineage/verification for
    # delimited kinds; the section 6.4 LIVE trust quadruple for
    # statistical_observation).
    coverage_map = (entry.get("coverage") or {}).get(kind) or {}
    claims = {}
    for claim in coverage_map.get("keys") or []:
        claims[canonical_key(claim["key_ref"], kind)] = claim

    cat_lookup = {}
    if catalog is not None and kind == "statistical_observation":
        try:
            for ser_key, cat_entry in catalog.items():
                cat_lookup[_prov.normalize_audit_key(ser_key)] = cat_entry
        except Exception:
            cat_lookup = {}

    artifact_ok = not artifact_missing and not artifact_mismatch
    for key_ref in keys:
        ser = canonical_key(key_ref, kind)
        key_reasons = []
        # condition 2 — artifact truth first: when the bytes are absent
        # or tampered, that IS the actionable gap the excerpt route names.
        if not artifact_ok:
            key_reasons.append(
                "runtime_missing" if artifact_missing
                else "runtime_hash_mismatch")
        # condition 3 — coverage
        claim = claims.get(ser)
        if claim is None:
            key_reasons.append("coverage_incomplete")
        else:
            cat_claim = (key_catalog.get(kind) or {}).get(ser)
            if cat_claim is None:
                # claimed in the registry coverage but absent from the
                # key catalog — authority_conflict (SCHEMA section 5.1)
                key_reasons.append("authority_conflict")
            else:
                # effective authority = the WEAKER claim (SCHEMA
                # sections 3.3/5.1: precedence rejected > unverified >
                # verified)
                effective = max(
                    (claim["authority"], cat_claim["authority"]),
                    key=lambda a: _AUTHORITY_RANK[a])
                if effective != "verified":
                    key_reasons.append("coverage_incomplete")
        # condition 4 — verification for that use
        if kind == "statistical_observation":
            # SCHEMA section 6.4 conditions T1/T2 are INDEPENDENT and
            # their failures UNION: live acceptance is checked on every
            # call AND the live digest is compared to the bind-time
            # context["catalog_digest"] whenever a catalog object is
            # bound — the digest comparison is never skipped just
            # because acceptance already failed.
            live_accepted = False
            live_digest = None
            if catalog is not None:
                try:
                    live_accepted = getattr(
                        catalog, "accepted", False) is True
                except Exception:
                    live_accepted = False
                try:
                    live_digest = catalog.digest
                except Exception:
                    live_digest = None
            if catalog is None or not live_accepted:
                key_reasons.append("catalog_not_accepted")
            if catalog is not None and live_digest != catalog_digest:
                key_reasons.append("catalog_digest_mismatch")
            norm_key = _prov.normalize_audit_key(
                {f: key_ref[f] for f in (
                    "pack_id", "records_file_sha256",
                    "physical_jsonl_line_1based", "raw_line_sha256")})
            index = indexes.get(norm_key[0]) or {}
            if norm_key not in index:
                key_reasons.append("audit_key_unresolved")
            cat_entry = cat_lookup.get(norm_key)
            if cat_entry is None or not isinstance(cat_entry, dict) \
                    or cat_entry.get("catalog_status") != \
                    "verified_observation" \
                    or not _research._receipt_ok(cat_entry):
                key_reasons.append("verification_absent")
        else:
            art_rel = _excerpt_artifact(entry)
            link = _lineage_for(entry, art_rel)
            lineage_evidence = link
            if link is None:
                key_reasons.append("lineage_missing")
            elif link["verification"] == "rejected":
                key_reasons.append("lineage_invalid")
            elif link["verification"] != "verified":
                key_reasons.append("verification_absent")
        # SPEC section 2.2, conditions 2 and 4 read together (root
        # adjudication g006-p5-lane-a-corrections-2): a verification
        # label recorded for the DECLARED artifact identity is not
        # verification of the divergent OBSERVED bytes — on a measured
        # hash divergence the affected artifact carries no applicable
        # verification, an independent per-key predicate recorded as
        # the existing verification_absent gap.  Never applied to
        # healthy hash-matching bytes and never added merely to fill
        # needed_excerpt: the measured byte predicate, not the route
        # list, explains this reason.
        if artifact_mismatch and "verification_absent" not in key_reasons:
            key_reasons.append("verification_absent")
        if "role_mismatch" in reasons:
            key_reasons.append("role_mismatch")
        reasons.update(key_reasons)
        if key_reasons:
            unsatisfied.append(key_ref)
            # actionable excerpt route: the gap reason for this key,
            # the entry artifact carrying the content, and the range.
            # API section 1.4: needed_excerpt[].reason is a gap-class
            # token ONLY — excerpting cannot clear a conflict, so a
            # conflict-only key stays unsatisfied WITHOUT a fabricated
            # excerpt reason; a mixed conflict+gap key keeps its
            # genuine gap route.
            gap_reason = next(
                (r for r in key_reasons
                 if _REASON_CLASS[r][0] == "needs_excerpt"),
                None)
            if gap_reason is not None:
                excerpt.append({
                    "key_ref": key_ref,
                    "reason": gap_reason,
                    "artifact": _excerpt_artifact(entry),
                    "range_hint": _range_hint(key_ref, kind),
                })
        else:
            satisfied.append(key_ref)

    if lineage_evidence is None and kind != "statistical_observation":
        lineage_evidence = _lineage_for(entry, _excerpt_artifact(entry))
    verification_evidence = None
    if kind == "statistical_observation" or catalog is not None:
        # the authority NAME is not recoverable from the constructed
        # object — the export binds it at load time; report the observed
        # digest plus any receipt reference the coverage claim carries.
        receipt = None
        for k in keys:
            cl = claims.get(canonical_key(k, kind))
            if cl is not None and cl.get("receipt_sha256"):
                receipt = cl["receipt_sha256"]
                break
        verification_evidence = {
            "authority": None,
            "catalog_digest": catalog_digest,
            "receipt_sha256": receipt,
        }

    return _verdict(
        source_hash, requested_use, reasons,
        selected=selected, disambiguator=disambiguator,
        satisfied=satisfied, unsatisfied=unsatisfied,
        excerpt=excerpt, artifacts=artifact_evidence,
        lineage=lineage_evidence, verification=verification_evidence,
        catalog=catalog)
