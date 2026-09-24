"""RDA/MAFRA 통계 원자료 provenance layer — physical-row audit identity.

Implements stage-g005 SPEC.md §2–§5 / API.md §1:

- Audit key = ``{pack_id, records_file_sha256, physical_jsonl_line_1based,
  raw_line_sha256}`` — the ONLY acceptable primary key for row-level identity.
  Legacy ``record_id`` is a non-unique human-lookup attribute only; legacy IDs
  collide across distinct physical rows (e.g. mafra ``기타`` stat rows ×3,
  econ ``useful_life.*`` ×46).
- ``physical_jsonl_line_1based`` counts binary ``splitlines(keepends=True)``
  lines 1-based, including blank physical lines.
- ``raw_line_sha256`` covers the exact raw line bytes INCLUDING the original
  LF/CRLF terminator (none added when the final line lacks one). No JSON
  re-serialization or whitespace normalization before hashing (binds D04).
- Duplicate audit keys are detected on the RAW LIST before any dict/map is
  built — a duplicate raises ``DuplicateAuditKeyError``, never silently
  deduplicated (binds D01).
- ``verify_pack_integrity`` is a hard gate: a ``records_file_sha256`` that
  does not match the pinned value for its ``pack_id``/``pack_revision``
  refuses resolution (SPEC §5), never soft-warns.
- ``resolve_reference`` returns ``None`` for unregistered keys — it never
  fabricates a placeholder, and never promotes a source-observation status
  into ``CatalogStatus.VERIFIED_OBSERVATION`` (SPEC §3/§6).
- ``register_snapshot`` writes an immutable frozen snapshot; later pack
  updates never mutate it (binds D08).
"""

from __future__ import annotations

import enum
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Types (API.md sketch)

AuditKey = tuple  # (pack_id, records_file_sha256, physical_jsonl_line_1based,
                  #  raw_line_sha256) — canonical tuple form; the dict form
                  #  used by work/p4-identity/AUDIT-JOIN.jsonl is accepted by
                  #  normalize_audit_key().


class SourceObservationStatus(enum.Enum):
    MATCHED = "matched"
    CORRECTION_PROPOSED = "correction_proposed"
    UNRESOLVED = "unresolved"
    DUPLICATE_SAME_CELL = "duplicate_same_cell"


class CatalogStatus(enum.Enum):
    QUARANTINED = "quarantined"
    EXPLORATORY = "exploratory"
    VERIFIED_OBSERVATION = "verified_observation"


class LookupVerdict(enum.Enum):
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNIQUE = "unique"
    SERIES = "series"


class IntegrityError(Exception):
    """Pinned pack bytes do not match the declared identity — hard failure."""


class DuplicateAuditKeyError(Exception):
    """Two physical rows produced the same audit key on the raw list."""


class UnpinnedPackError(IntegrityError):
    """No pinned (pack_id, pack_revision) entry exists to verify against."""


class SnapshotConflictError(Exception):
    """Snapshot path already holds different bytes — snapshots are immutable."""


class ResolutionContextError(Exception):
    """A resolution/verification path was invoked without the required
    verified resolver context (verified index, catalog) — fails closed
    (binds D09: no context, no verification)."""


# ---------------------------------------------------------------------------
# Pinned pack registry (SPEC.md §1 — source of truth:
# implementation/20260919-astra/p4-source/input-pins.json).  pack_revision is
# explicit per lane, never inferred from mtime or file presence.

_BASE_DIR = Path(__file__).resolve().parents[1] / "references" / "benchmark-packs"

PINNED_PACKS = {
    "rda.income.national.2024": {
        "pack_revision": "2024",
        "records_file_sha256":
            "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
        "record_lines": 102,
        "relpath": "common/rda-income-national-2024/records.jsonl",
    },
    "rda.income.regional.2024": {
        "pack_revision": "2024",
        "records_file_sha256":
            "c7e38281ccfaaaf40c63ab0880ca5f3ddb1ae18aaa89438ffb7f6da43c87c6df",
        "record_lines": 365,
        "relpath": "common/rda-income-regional-2024/records.jsonl",
    },
    "rda.econ.2025": {
        "pack_revision": "2025",
        "records_file_sha256":
            "a1cbfb2fa1825f2f0b7594cebe915b26f81cf1c48aee3cafae6c18ceb9267d54",
        "record_lines": 2600,
        "relpath": "common/rda-econ-2025/records.jsonl",
    },
    "mafra.specialty.production.2024": {
        "pack_revision": "2024",
        "records_file_sha256":
            "fc1c0b35d63a67dc5a794128f873272d72f0850e6d6470e7d9501270eafdd4be",
        "record_lines": 2988,
        "relpath": "majors/specialty_crops/"
                  "mafra-specialty-production-2024/records.jsonl",
    },
}

TOTAL_PINNED_ROWS = sum(p["record_lines"] for p in PINNED_PACKS.values())


@dataclass(frozen=True)
class RawRow:
    """One physical records.jsonl line with its audit identity.

    ``record`` is the parsed JSON object, or ``None`` when the physical line
    is blank/unparseable — it is still a real physical identity (D01).
    """

    audit_key: AuditKey
    pack_id: str
    physical_jsonl_line_1based: int
    raw_line_sha256: str
    raw_line: bytes
    record: dict | None


@dataclass(frozen=True)
class ResolvedObservation:
    """An audit key resolved against the approved catalog + pinned pack.

    ``raw_line`` carries the verified physical line bytes (when the
    resolution came from a ``build_audit_index`` row) so that
    ``register_snapshot`` can freeze a re-hashable physical receipt —
    ``None`` for observations constructed by hand."""

    audit_key: AuditKey
    pack_id: str
    pack_revision: str
    record_id: str | None
    record: dict
    locator: dict | None
    catalog_status: CatalogStatus
    raw_line: bytes | None = None


@dataclass(frozen=True)
class SnapshotReceipt:
    """Receipt for an immutable registered snapshot (binds D08)."""

    path: str
    sha256: str
    audit_key: AuditKey
    written_utc: str


# ---------------------------------------------------------------------------
# Audit-key helpers


def normalize_audit_key(key) -> AuditKey:
    """Accept tuple/list ``(pack_id, file_sha, line, raw_sha)`` or the dict
    form ``{pack_id, records_file_sha256, physical_jsonl_line_1based,
    raw_line_sha256}`` (AUDIT-JOIN.jsonl shape) and return the canonical
    4-tuple. Raises ``ValueError`` on malformed input."""
    if isinstance(key, dict):
        try:
            key = (
                key["pack_id"],
                key["records_file_sha256"],
                key["physical_jsonl_line_1based"],
                key["raw_line_sha256"],
            )
        except KeyError as exc:
            raise ValueError(f"audit_key dict missing field {exc}") from exc
    if isinstance(key, (list, tuple)) and len(key) == 4:
        pack_id, file_sha, line_no, raw_sha = key
        if isinstance(pack_id, str) and isinstance(file_sha, str) \
                and isinstance(line_no, int) and isinstance(raw_sha, str):
            return (pack_id, file_sha, line_no, raw_sha)
    raise ValueError(f"malformed audit_key: {key!r}")


def audit_key_dict(key) -> dict:
    """Canonical tuple -> AUDIT-JOIN.jsonl dict form."""
    pack_id, file_sha, line_no, raw_sha = normalize_audit_key(key)
    return {
        "pack_id": pack_id,
        "records_file_sha256": file_sha,
        "physical_jsonl_line_1based": line_no,
        "raw_line_sha256": raw_sha,
    }


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_line_sha256(raw_line: bytes) -> str:
    """SHA-256 over the exact physical line bytes including its original
    terminator — no strip, no re-serialization (binds D04)."""
    return hashlib.sha256(raw_line).hexdigest()


def iter_raw_lines(records_path):
    """Yield ``(physical_line_1based, raw_line_bytes)`` over binary
    ``splitlines(keepends=True)`` — every physical line, blanks included."""
    data = Path(records_path).read_bytes()
    for i, raw in enumerate(data.splitlines(keepends=True), start=1):
        yield i, raw


# ---------------------------------------------------------------------------
# Integrity + index


def verify_pack_integrity(pack_id: str, pack_revision: str,
                          records_file_sha256: str) -> None:
    """Raise ``IntegrityError`` unless (pack_id, pack_revision) is pinned and
    ``records_file_sha256`` equals the pinned value (SPEC §5 hard gate)."""
    pin = PINNED_PACKS.get(pack_id)
    if pin is None:
        raise UnpinnedPackError(f"no pinned entry for pack_id {pack_id!r}")
    if str(pin["pack_revision"]) != str(pack_revision):
        raise IntegrityError(
            f"pack_revision mismatch for {pack_id}: pinned "
            f"{pin['pack_revision']!r}, got {pack_revision!r}")
    if pin["records_file_sha256"] != records_file_sha256:
        raise IntegrityError(
            f"records_file_sha256 mismatch for {pack_id}@{pack_revision}: "
            f"pinned {pin['records_file_sha256']}, got {records_file_sha256}")


def build_audit_index(records_path, pack_id: str,
                      records_file_sha256: str) -> dict:
    """Read records.jsonl via binary splitlines(keepends=True); verify
    ``records_file_sha256`` against the whole file before indexing; detect
    duplicate audit keys on the RAW LIST before constructing the returned
    dict (raises ``DuplicateAuditKeyError``); never keys on legacy
    ``record_id`` (SPEC §2, binds D01)."""
    records_path = Path(records_path)
    actual = sha256_file(records_path)
    if actual != records_file_sha256:
        raise IntegrityError(
            f"{records_path} sha256 {actual} != declared "
            f"{records_file_sha256}")

    rows = []
    seen = set()  # raw-list duplicate detection BEFORE any dict build
    for line_no, raw in iter_raw_lines(records_path):
        key = (pack_id, records_file_sha256, line_no, raw_line_sha256(raw))
        if key in seen:
            raise DuplicateAuditKeyError(
                f"duplicate audit key on raw list at line {line_no} "
                f"of {records_path}")
        seen.add(key)
        record = None
        stripped = raw.strip()
        if stripped:
            record = json.loads(stripped.decode("utf-8"))
        rows.append(RawRow(key, pack_id, line_no, key[3], raw, record))
    return {row.audit_key: row for row in rows}


def check_unique_audit_keys(rows) -> list:
    """Duplicate detection over an ASSEMBLED observation list, before any
    dict/map construction (SPEC §2 ordering — binds D01 check 2).  Accepts
    RawRow items, audit keys (tuple/dict), or dicts carrying ``audit_key``;
    raises ``DuplicateAuditKeyError`` on the first repeated key.  Returns
    the normalized keys in input order.  Note this is distinct from a
    duplicate SOURCE LINE — byte-identical lines at different physical
    lines have different audit keys and are legal."""
    keys = []
    seen = set()
    for row in rows:
        if isinstance(row, RawRow):
            key = row.audit_key
        elif isinstance(row, dict) and "audit_key" in row:
            key = normalize_audit_key(row["audit_key"])
        else:
            key = normalize_audit_key(row)
        if key in seen:
            raise DuplicateAuditKeyError(
                f"duplicate audit key in observation list: "
                f"{audit_key_dict(key)}")
        seen.add(key)
        keys.append(key)
    return keys


def load_pack_records(records_path, pack_id: str) -> list:
    """Verified ordered ``[RawRow, ...]`` for a pinned pack: pins the file
    hash via ``verify_pack_integrity`` (hard-fails on mismatch or unpinned
    pack), then builds the audit index and returns rows in physical-line
    order."""
    pin = PINNED_PACKS.get(pack_id)
    if pin is None:
        raise UnpinnedPackError(f"no pinned entry for pack_id {pack_id!r}")
    verify_pack_integrity(pack_id, pin["pack_revision"],
                          sha256_file(records_path))
    index = build_audit_index(records_path, pack_id,
                              pin["records_file_sha256"])
    return [index[k] for k in sorted(index, key=lambda k: k[2])]


# ---------------------------------------------------------------------------
# Resolution + snapshots


def resolve_reference(audit_key, *, catalog, verified_index
                      ) -> ResolvedObservation | None:
    """Resolve one audit key to its registered catalog entry, or ``None``
    when unresolved — never a fabricated placeholder.

    Resolution requires a VERIFIED resolver context (binds D09 — fails
    closed without one):

    - ``verified_index`` — REQUIRED. The ``dict[AuditKey, RawRow]``
      produced by ``build_audit_index`` over bytes whose sha256 was
      actually verified. The key's physical membership in this index is
      checked: a key not present in verified bytes resolves to ``None``;
      a caller may not substitute an arbitrary declared hash.
    - ``catalog`` — maps audit keys (tuple or dict form) to entries
      carrying at least ``catalog_status``; entries may also carry
      ``record_id``/``locator``. Absent entry -> ``None``.

    The key's declared ``records_file_sha256`` is also checked against the
    pinned registry first (SPEC §5): a stale hash raises
    ``IntegrityError`` rather than resolving against unpinned bytes. The
    resolved ``record`` payload comes from the VERIFIED index row, not
    from the catalog entry — so resolution is bound to the same physical
    bytes that were hash-checked (correction P4-03). Never promotes a
    source-observation status into
    ``CatalogStatus.VERIFIED_OBSERVATION`` (SPEC §6)."""
    key = normalize_audit_key(audit_key)
    if verified_index is None or catalog is None:
        raise ResolutionContextError(
            "resolve_reference requires a verified index and an approved "
            "catalog context — refusing to resolve without them")
    pack_id, file_sha, _line, _raw = key
    pin = PINNED_PACKS.get(pack_id)
    if pin is None:
        raise UnpinnedPackError(f"no pinned entry for pack_id {pack_id!r}")
    verify_pack_integrity(pack_id, pin["pack_revision"], file_sha)

    row = verified_index.get(key)
    if row is None or getattr(row, "audit_key", None) != key:
        return None  # physical key is not a member of the verified index

    entry = _catalog_lookup(catalog, key)
    if entry is None:
        return None
    status = entry.get("catalog_status")
    if isinstance(status, str):
        status = CatalogStatus(status)
    if not isinstance(status, CatalogStatus):
        raise ValueError(f"catalog entry for {key} lacks valid "
                         f"catalog_status: {status!r}")
    return ResolvedObservation(
        audit_key=key,
        pack_id=pack_id,
        pack_revision=str(pin["pack_revision"]),
        record_id=entry.get("record_id") or (row.record or {}).get(
            "record_id"),
        record=row.record if row.record is not None
        else entry.get("record", {}),
        locator=entry.get("locator") or (row.record or {}).get("locator"),
        catalog_status=status,
        raw_line=row.raw_line,
    )


def _catalog_lookup(catalog, key):
    """Fetch a catalog entry by audit key, accepting dict-form keys too."""
    if hasattr(catalog, "get_entry"):
        return catalog.get_entry(key)
    for form in (key, audit_key_dict(key)):
        try:
            hit = catalog.get(form)
        except (TypeError, AttributeError):
            hit = None
        if hit is not None:
            return hit
    # tolerate catalogs keyed by serialized dict form
    target = audit_key_dict(key)
    try:
        items = catalog.items()
    except AttributeError:
        return None
    for k, v in items:
        try:
            if normalize_audit_key(k) == key or k == target:
                return v
        except (ValueError, TypeError):
            continue
    return None


def audit_source_refs(source_refs, *, catalog=None) -> dict:
    """Surface stale/duplicate/unresolvable source_ref entries explicitly
    (binds D04) — never silently resolves a conflict to the newest pack.

    ``source_refs`` items carry ``audit_key`` (tuple or dict form) plus any
    caller fields. Returns ``{"resolved": [...], "conflicts": [...]}``;
    conflicts carry ``kind`` in {duplicate_ref, stale_pin, unpinned_pack,
    unregistered} plus the offending ref."""
    resolved, conflicts = [], []
    seen = set()
    for ref in source_refs:
        key = ref.get("audit_key") if isinstance(ref, dict) else ref
        try:
            norm = normalize_audit_key(key)
        except (ValueError, TypeError) as exc:
            conflicts.append({"kind": "malformed", "ref": ref,
                              "detail": str(exc)})
            continue
        if norm in seen:
            conflicts.append({"kind": "duplicate_ref", "ref": ref,
                              "detail": "same audit key supplied twice"})
            continue
        seen.add(norm)
        pin = PINNED_PACKS.get(norm[0])
        if pin is None:
            conflicts.append({"kind": "unpinned_pack", "ref": ref,
                              "detail": f"no pin for pack {norm[0]!r}"})
            continue
        if pin["records_file_sha256"] != norm[1]:
            conflicts.append({"kind": "stale_pin", "ref": ref,
                              "detail": "records_file_sha256 != pinned value"})
            continue
        if catalog is not None and _catalog_lookup(catalog, norm) is None:
            conflicts.append({"kind": "unregistered", "ref": ref,
                              "detail": "audit key not in approved catalog"})
            continue
        resolved.append(norm)
    return {"resolved": resolved, "conflicts": conflicts}


def _is_hex_sha256(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(
        c in "0123456789abcdef" for c in v)


def _audit_key_schema_errors(key) -> list:
    """Declared-type validation for one audit key (D01 check 4): pack_id
    non-empty str, hashes 64-hex str, line a positive int."""
    errs = []
    if not isinstance(key, dict):
        errs.append("audit_key not a dict")
        return errs
    if not isinstance(key.get("pack_id"), str) or not key["pack_id"]:
        errs.append("pack_id must be a non-empty str")
    if not _is_hex_sha256(key.get("records_file_sha256")):
        errs.append("records_file_sha256 must be a 64-hex str")
    if not isinstance(key.get("physical_jsonl_line_1based"), int) \
            or isinstance(key.get("physical_jsonl_line_1based"), bool) \
            or key.get("physical_jsonl_line_1based", 0) < 1:
        errs.append("physical_jsonl_line_1based must be an int >= 1")
    if not _is_hex_sha256(key.get("raw_line_sha256")):
        errs.append("raw_line_sha256 must be a 64-hex str")
    return errs


def _walk_nonfinite(obj, path="$"):
    """Yield dotted paths of non-finite float values (NaN/±Inf) — these are
    rejected at admission (binds D07(b)-style value sanity; JSON has no
    literal for them so any occurrence means a constructed/mutated row)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        yield path
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_nonfinite(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk_nonfinite(v, f"{path}[{i}]")


def _semantic_fingerprint(entry):
    """Canonical semantic content of one observation — the parsed record
    payload, excluding its physical audit identity.  Two DISTINCT physical
    keys sharing this fingerprint are a semantic duplicate (D01 check 5)."""
    if isinstance(entry, RawRow):
        record = entry.record
    elif isinstance(entry, dict):
        # only an explicit ``record`` payload is comparable; a bare
        # audit-key entry carries no semantic claim
        record = entry.get("record")
        if isinstance(record, dict):
            record = {k: v for k, v in record.items() if k != "audit_key"}
    else:
        record = None
    if record is None:
        return None
    return json.dumps(record, ensure_ascii=False, sort_keys=True,
                      default=str)


def validate_observation_admission(observations, *, verified_index) -> dict:
    """D01 admission validation over a source-observation list joined to a
    verified physical index (binds D01 checks 3/4/5 — run BEFORE any
    catalog admission; a FAIL verdict is a rejection, never a warning).

    ``observations``: iterable of entries each carrying an ``audit_key``
    (dict or tuple form) — RawRow items accepted too. ``verified_index``:
    the ``dict[AuditKey, RawRow]`` produced by ``build_audit_index`` over
    verified bytes — membership in it is the physical-identity ground
    truth (correction P4-03).

    An entry's ``record`` payload is validated when present; when absent,
    the payload is resolved from ``verified_index[key].record`` before
    validating — a key-only entry can NEVER bypass schema, nonfinite, or
    semantic-duplicate checks (corrections-2 P4-01). Orphan keys carry no
    resolvable payload and are already rejected by the join check.

    Checks:
      join_1to1 — every observation key exists in the index and every
        index key is observed exactly once (zero orphans/missing/dupes);
      schema_types — audit-key field types + locator/record shapes;
      nonfinite_values — NaN/±Inf anywhere in an observation payload;
      semantic_duplicates — identical semantic content admitted under
        DIFFERENT physical keys (distinct failure mode from a duplicate
        audit key on the raw list — that one is caught earlier by
        ``check_unique_audit_keys``/``build_audit_index``).
    """
    if verified_index is None:
        raise ResolutionContextError(
            "admission validation requires a verified index context")
    obs_list = list(observations)
    index_keys = set(verified_index.keys())

    def _payload(obs, key):
        """The record payload actually validated: the entry's own record
        if it carries one, else the verified-index row's record for this
        physical key (never None-by-bypass — a RawRow with record=None
        resolves from the index exactly like a key-only dict entry)."""
        if isinstance(obs, RawRow):
            rec = obs.record
        elif isinstance(obs, dict):
            rec = obs.get("record")
        else:
            rec = None
        if rec is not None:
            return rec
        row = verified_index.get(key)
        return row.record if row is not None else None

    schema_errors, nonfinite = [], []
    seen_keys, dup_keys = set(), []
    keyed = []  # (obs, normalized key, payload) — only successful parses
    for i, obs in enumerate(obs_list):
        try:
            key = (obs.audit_key if isinstance(obs, RawRow)
                   else normalize_audit_key(obs.get("audit_key")
                                            if isinstance(obs, dict)
                                            else obs))
        except (ValueError, TypeError, AttributeError) as exc:
            schema_errors.append({"index": i, "errors": [str(exc)]})
            continue
        record = _payload(obs, key)
        keyed.append((obs, key, record))
        errs = _audit_key_schema_errors(audit_key_dict(key))
        if record is not None and not isinstance(record, dict):
            errs.append("record must be a JSON object or null")
        if isinstance(record, dict) and "locator" in record \
                and record["locator"] is not None \
                and not isinstance(record["locator"], dict):
            errs.append("locator must be a dict or null")
        if errs:
            schema_errors.append({"index": i, "audit_key": key,
                                  "errors": errs})
        if key in seen_keys:
            dup_keys.append(audit_key_dict(key))
        seen_keys.add(key)
        for p in _walk_nonfinite(record):
            nonfinite.append({"index": i, "audit_key": audit_key_dict(key),
                              "path": p})

    obs_keys = [k for _, k, _ in keyed]
    obs_key_set = set(obs_keys)
    orphans = [audit_key_dict(k) for k in obs_key_set - index_keys]
    missing = [audit_key_dict(k) for k in index_keys - obs_key_set]

    by_fp = {}
    for obs, key, record in keyed:
        fp = _semantic_fingerprint({"record": record})
        if fp is not None:
            by_fp.setdefault(fp, []).append(key)
    semantic_duplicates = [
        {"audit_keys": [audit_key_dict(k) for k in ks]}
        for ks in (v for v in by_fp.values() if len(v) > 1)]

    checks = {
        "join_1to1": {"orphan_observation_keys": sorted(
                          map(str, orphans)),
                      "missing_index_keys": sorted(map(str, missing)),
                      "duplicate_observation_keys": dup_keys,
                      "ok": not (orphans or missing or dup_keys)},
        "schema_types": {"errors": schema_errors, "ok": not schema_errors},
        "nonfinite_values": {"paths": nonfinite, "ok": not nonfinite},
        "semantic_duplicates": {"groups": semantic_duplicates,
                                "ok": not semantic_duplicates},
    }
    return {"verdict": "PASS" if all(c["ok"] for c in checks.values())
            else "FAIL",
            "checked_observations": len(obs_keys),
            "index_size": len(index_keys),
            "checks": checks}


def register_snapshot(resolved: ResolvedObservation, *,
                      sources_dir) -> SnapshotReceipt:
    """Write an immutable snapshot under ``sources_dir`` linking the pinned
    pack identity, the resolved observation, and its audit key. Snapshot
    content is frozen at registration time; a later pack update never
    mutates it (binds D08). Refuses to overwrite differing bytes."""
    sources_dir = Path(sources_dir)
    sources_dir.mkdir(parents=True, exist_ok=True)
    key = resolved.audit_key
    name = "snapshot-%s-%s-%s.json" % (
        key[0], key[2], hashlib.sha256(
            json.dumps(key).encode("utf-8")).hexdigest()[:16])
    path = sources_dir / name
    body = {
        "schema": "gg-rda-provenance-snapshot/1",
        "audit_key": audit_key_dict(key),
        "pack_id": resolved.pack_id,
        "pack_revision": resolved.pack_revision,
        "record_id": resolved.record_id,
        "catalog_status": resolved.catalog_status.value,
        "locator": resolved.locator,
        "record": resolved.record,
    }
    # the verified physical line bytes make the snapshot re-verifiable
    # after the live pack is deleted or mutated: a consumer re-hashes
    # ``raw_line_b64`` against ``audit_key.raw_line_sha256`` and re-parses
    # it against ``record`` (binds D08 — a fabricated record or key whose
    # bytes were never index-verified fails closed).
    if resolved.raw_line is not None:
        import base64
        body["raw_line_b64"] = base64.b64encode(
            resolved.raw_line).decode("ascii")
    # deterministic content: re-registering the same observation is
    # idempotent; the same path holding different bytes is a conflict
    data = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      indent=1) + "\n"
    if path.exists():
        if path.read_bytes() != data.encode("utf-8"):
            raise SnapshotConflictError(
                f"snapshot {path} already holds different bytes — "
                f"snapshots are immutable")
    else:
        path.write_text(data, encoding="utf-8")
    return SnapshotReceipt(path=str(path), sha256=sha256_file(path),
                           audit_key=key,
                           written_utc=datetime.now(timezone.utc).isoformat())
