"""P4 audited statistical research — propose/apply over the accepted
provenance + lookup contracts (stage-g005 API.md §86-94, SPEC §4-§8).

Adapted from the PR3 baseline (``hit``/``records[0]`` contract) to consume
``gg_rda_lookup.lookup_rda_data``'s public verdicts
``not_found | ambiguous | unique | series`` and to bind every proposal to a
physical-row ``AuditKey`` — never to a legacy ``record_id`` alone.

Hard rules (ACCEPTANCE D05/D06/D09):
- ``propose`` returns a proposal object only — it never mutates state.
- ``ambiguous`` / ``not_found`` / ``series`` results stay unresolved; the
  first record is never auto-chosen and series values are never scalar-
  extracted (D03).
- ``verification_status`` mirrors the catalog status verbatim
  (``quarantined``/``exploratory`` stay so — no promotion, D05).
- ``apply`` enforces CAS: ``expected_revision`` must equal the proposal's
  bound pack revision, the live pin must still verify, and a caller-
  supplied current target must not already be answered (D06).
- ``apply`` never writes canonical state itself — it returns a payload the
  caller hands to ``gg_core.apply`` under the normal CAS path.
"""

import base64
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parents[1] / "references" / "benchmark-packs"
OBSERVATION_CATALOG = "observation-catalog.json"

PROPOSAL_SCHEMA = "gg-p4-research-proposal/1"
SNAPSHOT_SCHEMA = "gg-rda-provenance-snapshot/1"

# metrics key -> unit source inside record["basis"] (D07: value/unit bound
# to the same verified row; an unknown metric never fabricates a unit)
_METRIC_UNIT = {
    "quantity": "quantity_unit",
    "gross_receipts": "currency",
    "operating_cost": "currency",
    "income": "currency",
    "income_rate_pct": None,  # unit is "%"
    "farmgate_price": "currency",
}


def _prov():
    import gg_rda_provenance

    return gg_rda_provenance


def _lookup():
    import gg_rda_lookup

    return gg_rda_lookup


def lookup(crop, region, **kw):
    """Thin pass-through to the accepted lookup contract."""
    return _lookup().lookup_rda_data(crop, region, **kw)


# Pinned accepted-catalog authorities (binds SCC1 / D05·D09): an
# observation catalog may claim ``accepted`` status ONLY under a pinned
# authority id whose declared canonical content digest it reproduces
# exactly.  Catalog membership in verified pack bytes proves identity,
# NOT approval — the pinned digest binds the accepted CONTENT, so a
# fabricated-but-well-shaped entry (real key + forged status + forged
# receipt values) cannot reproduce it.
#
# Pin provenance: the accepted catalog lane
# ``work/g005-p4-source-catalog-transform-corrections-1/``,
# ``output-hashes.json`` sha256
# ``1778fd02fc2286ac40ed86890d3d0dbc2e10acf344afeacb5fcb2bee0a6698f4``,
# accepted per ``stage-g005/P4-SOURCE-CATALOG-TRANSFORM-CORRECTIONS-1-
# ACCEPTANCE.json`` (reviewer 3-P4CatalogFinalReview, CLEAR/APPROVE).
# Each value below is the canonical content digest of that pack's
# accepted ``manifest.json`` rows (see ``catalog_content_digest``).
PINNED_CATALOG_AUTHORITIES = {
    "accepted-catalog-20260921/mafra.specialty.production.2024":
        "0550d458a0747be9929b3205c1ff8ff27f15c6f4cff5d9f3cc12372e336c71c8",
    "accepted-catalog-20260921/rda.econ.2025":
        "0d2d0b1cfeceb2a7fb4b12f2c10e10882f6c1957404ff1834a6d1a41e19eca44",
    "accepted-catalog-20260921/rda.income.national.2024":
        "555719cf2565b21b5d4321eeaba7433d60807c280e995e7ed319064fb103adb4",
    "accepted-catalog-20260921/rda.income.regional.2024":
        "7c7d01c4ab866fcf787a67370f1f6f22d40f72a7e2266d64e6b7b701f5032062",
}


class AcceptedCatalog:
    """An independently accepted observation catalog bound into a resolver
    context (binds P1-4 / SCC1 / D05·D09).

    A plain ``dict`` catalog is *caller-claimed* — entries may assert any
    ``catalog_status`` without evidence.  ``accept_catalog`` validates the
    catalog artifact itself before the integration boundary trusts it:

    - every key must normalize to an audit key;
    - ``catalog_status`` must be a known status;
    - a ``verified_observation`` entry MUST carry an observation receipt:
      ``source_observation.observation_status`` (non-empty str),
      ``source_observation.source_pdf_sha256`` (64-hex), and
      ``source_observation.prep_receipt_ref`` (non-empty str) — a copied
      real audit key with a fabricated ``verified`` spelling and no
      receipt cannot validate;
    - quarantined/exploratory entries need no receipt (they can never
      promote anyway);
    - the WHOLE validated content must reproduce the pinned canonical
      digest of the accepted authority it claims — shape-valid entries
      whose content was never independently accepted cannot authenticate.

    Acceptance binds to the catalog's ACTUAL CONTENT, never to supplied
    digest text (SCC1/P1-4): the constructor takes entries only — there
    is no digest parameter to inject and no stored approval flag to
    preserve.  ``.digest`` and ``.accepted`` are recomputed over the live
    entries on every access, so any post-construction mutation — through
    ``.entries`` or the aliased caller mapping — changes the content and
    self-invalidates acceptance.  The wrapper is dict-like
    (``.get``/``.items``/``.get_entry``) so the accepted lane's
    ``_catalog_lookup`` consumes it unchanged."""

    def __init__(self, entries):
        if not isinstance(entries, dict):
            raise TypeError(
                "AcceptedCatalog entries must be a dict of "
                "audit_key -> entry — acceptance binds to content and "
                "cannot be minted from supplied digest text")
        self._entries = entries

    @property
    def entries(self):
        return self._entries

    @property
    def digest(self):
        return catalog_content_digest(self._entries)

    @property
    def accepted(self):
        try:
            return self.digest in PINNED_CATALOG_AUTHORITIES.values()
        except Exception:
            return False

    def get(self, key, default=None):
        return self.entries.get(key, default)

    def get_entry(self, key):
        for form in (key, _prov().audit_key_dict(key)
                     if not isinstance(key, dict) else key):
            try:
                hit = self.entries.get(form)
            except TypeError:
                hit = None
            if hit is not None:
                return hit
        return None

    def items(self):
        return self.entries.items()


_RECEIPT_FIELDS = ("observation_status", "source_pdf_sha256",
                   "prep_receipt_ref")


def _receipt_ok(entry):
    """True when a catalog entry carries a complete observation receipt —
    required before any ``verified_observation`` claim may promote.

    Matches the accepted catalog schema: the receipt IS
    ``source_observation.{observation_status, source_pdf_sha256,
    prep_receipt_ref}``.  Identity fields (``locator``, ``record_id``)
    live on the verified pack row itself and are bound at claim time,
    not duplicated on the catalog entry."""
    if not isinstance(entry, dict):
        return False
    so = entry.get("source_observation")
    if not isinstance(so, dict):
        return False
    if not isinstance(so.get("observation_status"), str) \
            or not so["observation_status"].strip():
        return False
    pdf = so.get("source_pdf_sha256")
    if not isinstance(pdf, str) or len(pdf) != 64 or not all(
            c in "0123456789abcdef" for c in pdf):
        return False
    return isinstance(so.get("prep_receipt_ref"), str) \
        and bool(so["prep_receipt_ref"].strip())


def catalog_content_digest(entries):
    """Canonical content digest of a validated observation catalog —
    the identity a pinned authority declares.  ``entries`` maps audit
    keys (tuple or dict form) to entry dicts."""
    prov = _prov()
    canonical = json.dumps(
        {json.dumps(prov.audit_key_dict(prov.normalize_audit_key(k)),
                    sort_keys=True): v
         for k, v in entries.items()},
        ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def accept_catalog(catalog, *, verified_indexes=None, authority=None):
    """Validate a candidate observation catalog and wrap it as an
    ``AcceptedCatalog`` — the integration trust boundary (P1-4/SCC1).

    Raises ``ValueError`` on any malformed entry, a ``verified`` claim
    without a complete receipt, or an entry outside ``verified_indexes``
    (when given).  Acceptance must additionally be AUTHENTICATED:
    ``authority`` must name a ``PINNED_CATALOG_AUTHORITIES`` id and the
    validated content must reproduce that authority's pinned canonical
    digest — a caller cannot declare its own catalog accepted."""
    prov = _prov()
    entries = catalog.entries if isinstance(catalog, AcceptedCatalog) \
        else catalog
    if not isinstance(entries, dict):
        raise ValueError("observation catalog must be a dict of "
                         "audit_key -> entry")
    validated = {}
    for raw_key, entry in entries.items():
        try:
            key = prov.normalize_audit_key(raw_key)
        except (ValueError, TypeError) as exc:
            raise ValueError("catalog key not an audit key: %r (%s)"
                             % (raw_key, exc))
        if not isinstance(entry, dict):
            raise ValueError("catalog entry for %r not a dict" % (raw_key,))
        status = entry.get("catalog_status")
        if isinstance(status, prov.CatalogStatus):
            status = status.value
        if status not in {s.value for s in prov.CatalogStatus}:
            raise ValueError("catalog entry %r has invalid "
                             "catalog_status %r" % (raw_key, status))
        if status == prov.CatalogStatus.VERIFIED_OBSERVATION.value \
                and not _receipt_ok(entry):
            raise ValueError(
                "verified_observation entry %r lacks a complete "
                "observation receipt (source_observation.%s)"
                % (raw_key, "/".join(_RECEIPT_FIELDS)))
        if verified_indexes is not None:
            index = (verified_indexes or {}).get(key[0]) or {}
            if key not in index:
                raise ValueError("catalog entry %r is not a member of "
                                 "verified pack bytes" % (raw_key,))
        validated[key] = entry
    digest = catalog_content_digest(validated)
    expected = PINNED_CATALOG_AUTHORITIES.get(authority)
    if expected is None:
        raise ValueError(
            "catalog acceptance unauthenticated: %r is not a pinned "
            "accepted-catalog authority" % (authority,))
    if digest != expected:
        raise ValueError(
            "catalog acceptance unauthenticated: content digest %s does "
            "not reproduce the pinned digest %s of %r" % (
                digest, expected, authority))
    return AcceptedCatalog(validated)


def _catalog_entry(catalog, key):
    """Best-effort catalog entry lookup mirroring the accepted lane's
    ``_catalog_lookup`` semantics (tuple and dict key forms)."""
    return _prov()._catalog_lookup(catalog, key)


def resolver_context(*, packs_dir=None, catalog=None, pack_ids=None):
    """Build a verified resolver context for ``resolve_reference``.

    ``verified indexes`` are rebuilt from pinned pack bytes — a missing or
    hash-mismatched pack is simply absent from ``indexes`` (fail closed).
    ``catalog`` maps audit keys to entries carrying ``catalog_status``; when
    omitted, ``<packs_dir>/observation-catalog.json`` is loaded if present.
    An ``AcceptedCatalog`` (from ``accept_catalog``) marks the context's
    catalog as independently validated — ``catalog_accepted`` reports it.
    Returns ``{"indexes": {pack_id: index}, "catalog": catalog,
    "catalog_accepted": bool}``.
    """
    prov = _prov()
    base = Path(packs_dir) if packs_dir is not None else PACKS_DIR
    if catalog is None:
        cat_path = base / OBSERVATION_CATALOG
        catalog = (
            json.loads(cat_path.read_text(encoding="utf-8"))
            if cat_path.exists()
            else {}
        )
    indexes = {}
    for pack_id, pin in prov.PINNED_PACKS.items():
        if pack_ids is not None and pack_id not in pack_ids:
            continue
        records_path = base / pin["relpath"]
        if not records_path.exists():
            continue
        try:
            indexes[pack_id] = prov.build_audit_index(
                records_path, pack_id, pin["records_file_sha256"]
            )
        except prov.IntegrityError:
            continue  # bytes no longer match the pin — excluded, not trusted
    try:
        cat_accepted = getattr(catalog, "accepted", False) is True
        cat_digest = getattr(catalog, "digest", None)
    except Exception:
        cat_accepted, cat_digest = False, None
    return {
        "indexes": indexes,
        "catalog": catalog,
        "catalog_accepted": cat_accepted,
        "catalog_digest": cat_digest,
    }


def _ctx_accepted(ctx):
    """Live acceptance of the context's bound catalog — recomputed from
    the catalog's CURRENT content on every call (SCC1).  Verdict paths
    must use this instead of the ``catalog_accepted`` snapshot recorded
    at context-build time so post-acceptance mutation cannot ride a
    stale flag."""
    try:
        return getattr(ctx.get("catalog"), "accepted", False) is True
    except Exception:
        return False


def _target_status(target):
    if not isinstance(target, dict):
        return None
    return target.get("answer_state") or target.get("status")


def _selection_key(selection):
    """Normalize the selection argument to an AuditKey dict or ``None``.

    Accepts an AuditKey (dict/tuple), a lookup candidate carrying
    ``audit_key``, or a whole ``lookup_rda_data`` result — only
    ``unique`` and ``unverified`` results contribute their single
    candidate's key (both are single-observation selections; the
    verification axis is carried separately); every other verdict
    returns ``None`` (fail closed, no first-record choice)."""
    if not isinstance(selection, dict):
        try:
            return _prov().audit_key_dict(selection)
        except (ValueError, TypeError):
            return None
    if "status" in selection and "records" in selection:
        if selection.get("status") not in ("unique", "unverified"):
            return None
        records = selection.get("records") or []
        if len(records) != 1:
            return None
        return records[0].get("audit_key")
    if "audit_key" in selection:
        return selection.get("audit_key")
    if "pack_id" in selection and "records_file_sha256" in selection:
        return selection
    return None


def propose(current_pack_revision, target, selection, *, context=None):
    """Build a research proposal bound to an audited physical row.

    ``current_pack_revision`` — the pack revision the caller believes
    current. ``target`` — dict carrying ``id``/``field_id`` and the metric
    to take (``metric``), plus the target's state at propose time.
    ``selection`` — an AuditKey, a lookup candidate, or a lookup result.

    Returns a Proposal dict; never applies, never fabricates, never
    promotes an unverified observation."""
    prov = _prov()
    key = _selection_key(selection)
    if key is None:
        verdict = (
            selection.get("status")
            if isinstance(selection, dict) and "status" in selection
            else None
        )
        return {
            "schema": PROPOSAL_SCHEMA,
            "status": "unresolved",
            "reason": verdict or "selection_not_an_audit_key",
            "target": target,
            "value": None,
        }
    try:
        norm = prov.normalize_audit_key(key)
    except (ValueError, TypeError):
        return {"schema": PROPOSAL_SCHEMA, "status": "unresolved",
                "reason": "malformed_audit_key", "target": target,
                "value": None}
    ctx = context if context is not None else resolver_context()
    index = (ctx.get("indexes") or {}).get(norm[0])
    catalog = ctx.get("catalog")
    try:
        resolved = prov.resolve_reference(
            key, catalog=catalog, verified_index=index
        )
    except prov.ResolutionContextError:
        return {"schema": PROPOSAL_SCHEMA, "status": "unresolved",
                "reason": "resolver_context_required", "target": target,
                "value": None}
    except prov.IntegrityError as exc:
        return {"schema": PROPOSAL_SCHEMA, "status": "unresolved",
                "reason": "integrity_error", "detail": str(exc),
                "target": target, "value": None}
    if resolved is None:
        return {"schema": PROPOSAL_SCHEMA, "status": "unresolved",
                "reason": "audit_key_unresolved", "target": target,
                "value": None}

    record = resolved.record or {}
    extraction = record.get("extraction")
    if isinstance(extraction, dict) \
            and extraction.get("status") == "rejected":
        return {"schema": PROPOSAL_SCHEMA, "status": "unresolved",
                "reason": "extraction_rejected", "target": target,
                "audit_key": prov.audit_key_dict(norm), "value": None}
    metric = target.get("metric") if isinstance(target, dict) else None
    metrics = record.get("metrics") or {}
    if metric and metric not in _METRIC_UNIT:
        return {
            "schema": PROPOSAL_SCHEMA,
            "status": "unresolved",
            "reason": "unit_mapping_unknown",
            "target": target,
            "audit_key": prov.audit_key_dict(norm),
            "value": None,
        }
    if not metric or metric not in metrics or metrics.get(metric) is None:
        return {
            "schema": PROPOSAL_SCHEMA,
            "status": "unresolved",
            "reason": "metric_absent_in_verified_row",
            "target": target,
            "audit_key": prov.audit_key_dict(norm),
            "value": None,
        }
    basis = record.get("basis") or {}
    unit_src = _METRIC_UNIT.get(metric)
    unit = "%" if metric == "income_rate_pct" else basis.get(unit_src)
    catalog_status = resolved.catalog_status.value
    # P1-4: ``verified_observation`` may promote ONLY when it came through
    # an independently accepted catalog AND the entry carries a complete
    # observation receipt — a caller-claimed verified spelling on a real
    # audit key (fabricated catalog) never promotes.
    entry = _catalog_entry(catalog, resolved.audit_key)
    catalog_accepted = _ctx_accepted(ctx)
    receipt_ok = _receipt_ok(entry)
    # K8: the promotable gate is the shared is_verified_observation
    # predicate (accepted catalog + verified status + complete receipt +
    # extracted row) — the same basis lookup's verification.verified and
    # the candidates observation axis use.  This only narrows promotion:
    # the previous rule never required an extracted row.
    promotable = _lookup().is_verified_observation(
        entry, record, catalog_accepted)
    proposal = {
        "schema": PROPOSAL_SCHEMA,
        "status": "proposal",
        "target": {
            "id": (target or {}).get("id") or (target or {}).get("field_id"),
            "metric": metric,
            "status_at_propose": _target_status(target),
        },
        "value": metrics[metric],
        "unit": unit,
        "basis": basis,
        "period": basis.get("year"),
        "price_character": record.get("price_character"),
        "caveats": list(record.get("caveats") or []),
        "verification_status": catalog_status,
        "catalog_accepted": catalog_accepted,
        "receipt_verified": receipt_ok,
        "promotable": promotable,
        "source_ref": "%s@%s#%s" % (
            resolved.pack_id, resolved.pack_revision, resolved.record_id),
        "audit_key": prov.audit_key_dict(resolved.audit_key),
        "pack_id": resolved.pack_id,
        "pack_revision": resolved.pack_revision,
        "expected_pack_revision_at_propose": current_pack_revision,
    }
    payload = json.dumps(proposal, ensure_ascii=False, sort_keys=True)
    proposal["proposal_id"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return proposal


# states in which a target counts as already answered — anything a user
# (or a prior apply) has resolved is ineligible for a fresh proposal
_ELIGIBLE_TARGET_STATES = {"not_provided"}


def _is_eligible_state(status):
    return status in _ELIGIBLE_TARGET_STATES


def apply(proposal, *, expected_revision, target_current=None,
          packs_dir=None):
    """CAS-guarded acceptance of a proposal — never writes state itself.

    Rejects (``status: "stale"``/``"rejected"``) when:
    - the proposal is malformed or was never a live proposal;
    - ``expected_revision`` differs from the bound pack revision;
    - the live pinned registry no longer verifies the bound pack identity
      (pack revision or declared content changed since propose);
    - the LIVE pack BYTES on disk no longer hash to the pinned value —
      ``verify_pack_integrity`` compares registry fields only, so apply
      re-reads ``<packs_dir>/<pin.relpath>`` itself (deletion/mutation of
      the file with an unchanged registry is detected);
    - ``target_current`` is omitted, names a different target, is already
      answered (regardless of equality with the propose-time state —
      a target answered at propose time was never eligible), or differs
      from the state recorded at propose time.

    On pass returns ``status: "ready"`` with the fact payload for the
    caller to submit through ``gg_core.apply``."""
    prov = _prov()
    if (
        not isinstance(proposal, dict)
        or proposal.get("schema") != PROPOSAL_SCHEMA
        or proposal.get("status") != "proposal"
        or not isinstance(proposal.get("audit_key"), dict)
    ):
        return {"status": "rejected", "reason": "malformed_proposal"}
    if str(expected_revision) != str(proposal.get("pack_revision")):
        return {
            "status": "stale",
            "reason": "expected_revision != bound pack_revision",
            "bound": proposal.get("pack_revision"),
            "expected": expected_revision,
        }
    try:
        prov.verify_pack_integrity(
            proposal["pack_id"],
            proposal["pack_revision"],
            proposal["audit_key"]["records_file_sha256"],
        )
    except prov.IntegrityError as exc:
        return {"status": "stale", "reason": "pack_integrity_changed",
                "detail": str(exc)}
    # live-byte freshness: the registry agreeing with the proposal says
    # nothing about the file on disk — re-read and re-hash it (D06).
    pin = prov.PINNED_PACKS.get(proposal["pack_id"])
    records_path = (Path(packs_dir) if packs_dir is not None
                    else PACKS_DIR) / pin["relpath"]
    if not records_path.is_file():
        return {"status": "stale", "reason": "pack_bytes_missing",
                "detail": "%s not on disk" % records_path}
    live_sha = prov.sha256_file(records_path)
    if live_sha != pin["records_file_sha256"] or \
            live_sha != proposal["audit_key"]["records_file_sha256"]:
        return {
            "status": "stale", "reason": "pack_bytes_changed",
            "detail": "live %s sha256 %s != bound %s" % (
                records_path, live_sha,
                proposal["audit_key"]["records_file_sha256"]),
        }
    target = proposal.get("target") or {}
    recorded = target.get("status_at_propose")
    if target_current is None:
        return {"status": "rejected", "reason": "target_current_required",
                "detail": "현재 대상 상태 없이 적용 불가 — apply는 항상 "
                          "실시간 대상 확인을 요구한다"}
    want_id = target.get("id")
    got_id = (target_current.get("id") or target_current.get("field_id")
              if isinstance(target_current, dict) else None)
    # SCC3/D06: identity must be bound on BOTH sides and equal — an empty
    # bound id cannot certify the proposal targets anything, and an empty
    # current id means the caller confirmed no concrete target at all.
    if not want_id:
        return {"status": "rejected", "reason": "bound_target_id_required",
                "detail": "제안에 대상 식별자가 없음 — 무엇을 적용하는지 "
                          "확인 불가"}
    if not got_id:
        return {"status": "rejected",
                "reason": "current_target_id_required",
                "detail": "현재 대상 식별자 없이 적용 불가 — apply는 항상 "
                          "실시간 대상 확인을 요구한다"}
    if want_id != got_id:
        return {"status": "rejected", "reason": "target_mismatch",
                "bound": want_id, "current": got_id}
    current = _target_status(target_current)
    if not _is_eligible_state(recorded):
        return {
            "status": "stale", "reason": "target_ineligible_at_propose",
            "detail": "제안 시점에 이미 응답된 대상 — 제안 자체가 무효",
            "recorded": recorded,
        }
    if not _is_eligible_state(current):
        return {
            "status": "stale", "reason": "target_already_answered",
            "detail": "대상이 이미 응답됨 — 동일 상태라도 재적용 불가",
            "recorded": recorded, "current": current,
        }
    if current != recorded:
        return {
            "status": "stale",
            "reason": "target_state_changed",
            "detail": "대상이 이미 응답되었거나 상태가 바뀜 — 제안 폐기",
            "recorded": recorded,
            "current": current,
        }
    return {
        "status": "ready",
        "target_verified": True,
        "fact": {
            "value": proposal["value"],
            "unit": proposal["unit"],
            "period": proposal["period"],
            "verification": "proposed_research",
            "verification_status": proposal["verification_status"],
            "promotable": proposal["promotable"],
            "price_character": proposal["price_character"],
            "source_ref": proposal["source_ref"],
            "audit_key": proposal["audit_key"],
            "caveats": proposal["caveats"],
        },
        "proposal_id": proposal.get("proposal_id"),
    }


# ---------------------------------------------------------------------------
# Shared statistical-reference adapters (P4 verification hooks for
# gg_core / gg_finance / gg_school_excel — binds D07, D09).  Every adapter
# fails closed: unverifiable or fabricated provenance is a conflict, never
# a silently-accepted reference.

_STAT_REF = re.compile(r"^([A-Za-z0-9_.\-]+)@([A-Za-z0-9_.\-]+)#(\S+)$")


def is_statistical_ref(ref):
    """True when a declared source_ref claims audited statistical
    provenance — a ``pack@pack_revision#record_id`` string or a dict
    carrying ``audit_key``.  Plain strings (``src:…``, ``synthetic:…``)
    are NOT statistical claims and are left to existing checks."""
    if isinstance(ref, dict):
        return "audit_key" in ref
    return isinstance(ref, str) and bool(_STAT_REF.match(ref))


def _numeric_ok(v):
    """A claimed/bound metric value is admissible iff every scalar leaf
    parses as a FINITE Decimal — ``'NaN'``, ``'Infinity'``/``'-Inf'``
    and unparseable strings reject regardless of equality (SCC6/D07(b);
    mirrors the provenance lane's ``_walk_nonfinite`` admission check).
    dict/list 'range' values are validated leaf-by-leaf and keep their
    structure — no scalar extraction."""
    if isinstance(v, dict):
        return bool(v) and all(_numeric_ok(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return bool(v) and all(_numeric_ok(x) for x in v)
    try:
        return Decimal(str(v)).is_finite()
    except (InvalidOperation, ValueError):
        return False


def _num_eq(a, b):
    """Numeric-or-string equality tolerant of int/float/str mixtures —
    but only after BOTH sides validate as finite numeric values
    (SCC6/D07(b)).  Identical non-numeric payloads never pass."""
    if not (_numeric_ok(a) and _numeric_ok(b)):
        return False
    if a == b:
        return True
    if isinstance(a, (dict, list, tuple)) \
            or isinstance(b, (dict, list, tuple)):
        # 'range' values compared leaf-by-leaf: equal structure already
        # handled by ``a == b``; any structural difference is a mismatch
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


def _expected_unit(record, metric):
    if metric == "income_rate_pct":
        return "%"
    src = _METRIC_UNIT.get(metric)
    return ((record.get("basis") or {}).get(src)
            if src is not None else None)


def _locator_ok(declared, resolved, key):
    """A declared locator must bind the SAME physical row: dict form is a
    subset-match against the resolved row's locator; the ``물리행 N``
    string form must name the audit key's physical line."""
    if isinstance(declared, dict):
        loc = resolved.locator or {}
        return bool(declared) and all(
            loc.get(k) == v for k, v in declared.items())
    if isinstance(declared, str):
        return declared == "물리행 %d" % key[2]
    return False


def _claim_mismatch(claim, ref, resolved, entry, key, *,
                    catalog_accepted=False):
    """Cross-check a submitted fact/economic claim against the trusted
    bound receipt (P1-1/D07).  Returns a reason string on the first
    mismatch, ``None`` when every declared field verifies.

    A statistical claim must bind: metric->value, unit, locator, and the
    official ``source_pdf_sha256`` from the observation receipt — fields
    it does not declare are unverifiable and fail closed.  The whole
    check only means anything through an independently accepted catalog
    (P1-4): a caller-claimed catalog can claim any receipt values."""
    if catalog_accepted is not True:
        return ("catalog not independently accepted — claim cannot bind "
                "a trusted receipt")
    record = resolved.record or {}
    metrics = record.get("metrics") or {}
    metric = claim.get("metric") or claim.get("field_id")
    if metric not in _METRIC_UNIT:
        return "claim metric %r cannot bind to a verified metric" % metric
    if metrics.get(metric) is None:
        return "claim metric %r absent in verified row" % metric
    if not _num_eq(claim.get("value"), metrics[metric]):
        return "claim value %r != verified row %s=%r" % (
            claim.get("value"), metric, metrics[metric])
    expected_unit = _expected_unit(record, metric)
    if claim.get("unit") != expected_unit:
        return "claim unit %r != verified row unit %r" % (
            claim.get("unit"), expected_unit)
    declared_loc = (ref.get("locator") if isinstance(ref, dict) else None)
    if declared_loc is None:
        declared_loc = claim.get("locator") or claim.get("source_locator")
    if not _locator_ok(declared_loc, resolved, key):
        return "claim locator %r does not bind the verified row" % (
            declared_loc,)
    declared_pdf = (ref.get("source_pdf_sha256")
                    if isinstance(ref, dict) else None) or \
        claim.get("source_pdf_sha256")
    receipt_pdf = (((entry or {}).get("source_observation") or {})
                   .get("source_pdf_sha256"))
    if not declared_pdf:
        return ("claim declares no source_pdf_sha256 — official PDF "
                "binding required")
    if declared_pdf != receipt_pdf:
        return "claim source_pdf_sha256 != observation receipt %r" % (
            receipt_pdf,)
    if not _receipt_ok(entry):
        return ("observation receipt incomplete — claim cannot bind a "
                "trusted receipt")
    return None


def _verify_snapshot_receipt(norm, ref, catalog, source):
    """Verify a registered snapshot/receipt WITHOUT the live pack (P1-3/
    D08).  The receipt anchors the historical identity:

    - the snapshot file's sha256 must equal the bound receipt;
    - its ``audit_key`` must equal the declared key;
    - ``raw_line_b64`` must re-hash to ``audit_key.raw_line_sha256`` and
      re-parse to the frozen ``record`` (a fabricated payload needs bytes
      that hash to the real raw_line_sha256 — infeasible);
    - ``pack_id`` must still be a pinned pack identity;
    - catalog membership/status is checked the same as the live path —
      a key absent from the accepted catalog never verifies.

    Returns ``(resolved_like, entry, verdict_or_None)``."""
    prov = _prov()
    receipt = ref.get("snapshot_receipt")
    if not isinstance(receipt, dict) or not receipt.get("path") \
            or not receipt.get("sha256"):
        return None, None, {"status": "invalid",
                            "reason": "malformed_snapshot_receipt"}
    path = Path(receipt["path"])
    if not path.is_file():
        return None, None, {"status": "unresolved",
                            "reason": "snapshot_missing"}
    actual = prov.sha256_file(path)
    if actual != receipt["sha256"]:
        return None, None, {"status": "invalid",
                            "reason": "snapshot sha256 != bound receipt"}
    declared = (source or {}).get("hash") if isinstance(source, dict) \
        else None
    if declared and declared != actual:
        return None, None, {
            "status": "invalid",
            "reason": "source hash != snapshot receipt sha256"}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None, {"status": "invalid",
                            "reason": "snapshot unreadable"}
    if body.get("schema") != SNAPSHOT_SCHEMA:
        return None, None, {"status": "invalid",
                            "reason": "snapshot schema mismatch"}
    try:
        snap_key = prov.normalize_audit_key(body.get("audit_key"))
    except (ValueError, TypeError):
        return None, None, {"status": "invalid",
                            "reason": "snapshot audit_key malformed"}
    if snap_key != norm:
        return None, None, {
            "status": "invalid",
            "reason": "snapshot audit_key != declared audit_key"}
    raw_b64 = body.get("raw_line_b64")
    if not isinstance(raw_b64, str):
        return None, None, {
            "status": "unresolved",
            "reason": "snapshot lacks raw_line receipt — cannot re-verify"}
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (ValueError, TypeError):
        return None, None, {"status": "invalid",
                            "reason": "snapshot raw_line_b64 malformed"}
    if prov.raw_line_sha256(raw) != norm[3]:
        return None, None, {
            "status": "invalid",
            "reason": "snapshot raw_line does not hash to the audit key"}
    try:
        raw_record = json.loads(raw.strip().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raw_record = None
    if raw_record is not None and raw_record != body.get("record"):
        return None, None, {
            "status": "invalid",
            "reason": "snapshot record != its bound raw line bytes"}
    if catalog is None:
        return None, None, {"status": "unresolved",
                            "reason": "resolver_context_required"}
    entry = _catalog_entry(catalog, norm)
    if entry is None:
        return None, None, {"status": "unresolved",
                            "reason": "audit_key_unresolved"}
    status = entry.get("catalog_status")
    if isinstance(status, prov.CatalogStatus):
        status = status.value
    if status not in {s.value for s in prov.CatalogStatus}:
        return None, None, {
            "status": "invalid",
            "reason": "catalog entry lacks valid catalog_status"}
    resolved = prov.ResolvedObservation(
        audit_key=norm,
        pack_id=norm[0],
        pack_revision=str(body.get("pack_revision") or ""),
        record_id=body.get("record_id") or entry.get("record_id"),
        record=body.get("record") or {},
        locator=body.get("locator") or entry.get("locator"),
        catalog_status=prov.CatalogStatus(status),
        raw_line=raw,
    )
    return resolved, entry, None


def _resolve_ref(source, ref, ctx):
    """Shared resolution core for ``audit_source_ref`` /
    ``verify_economic_claim``.  A ref carrying an explicit
    ``snapshot_receipt`` is routed through the snapshot validator
    UNCONDITIONALLY (the bound receipt is the authority — a live pack
    can never bypass it); every other ref resolves through the live
    verified index only.  Returns ``(resolved, entry, norm,
    verdict_or_None)``."""
    prov = _prov()
    key = ref.get("audit_key") if isinstance(ref, dict) else None
    try:
        norm = prov.normalize_audit_key(key)
    except (ValueError, TypeError) as exc:
        return None, None, None, {"status": "invalid",
                                  "reason": "malformed_audit_key: %s" % exc}
    declared = (source or {}).get("hash") if isinstance(source, dict) \
        else None
    if prov.PINNED_PACKS.get(norm[0]) is None:
        return None, None, norm, {"status": "invalid",
                                  "reason": "no pinned entry for pack_id "
                                            "%r" % (norm[0],)}
    # source-hash binding is a static claim — it fails closed BEFORE any
    # resolution attempt; the snapshot path re-checks it against the
    # bound receipt hash instead of the live pack hash.
    has_snapshot = isinstance(ref, dict) \
        and ref.get("snapshot_receipt") is not None
    if declared and not has_snapshot and declared != norm[1]:
        return None, None, norm, {
            "status": "invalid",
            "reason": "source hash != audit_key records_file_sha256"}
    catalog = ctx.get("catalog")
    if has_snapshot:
        # SCC4/D07(c): an explicit ``snapshot_receipt`` binds the
        # registered snapshot as THE authority for this ref — it is
        # verified unconditionally.  A still-valid live pack can never
        # bypass (or rescue) a bound receipt that is missing, rehashed,
        # or repointed at a different observation.
        resolved, entry, verdict = _verify_snapshot_receipt(
            norm, ref, catalog, source)
        if verdict is not None:
            return None, None, norm, verdict
        return resolved, entry, norm, None
    index = (ctx.get("indexes") or {}).get(norm[0])
    resolved, live_error = None, None
    if index is not None and \
            (prov.PINNED_PACKS[norm[0]]["records_file_sha256"]
             == norm[1]):
        try:
            resolved = prov.resolve_reference(
                norm, catalog=catalog, verified_index=index)
        except prov.ResolutionContextError:
            return None, None, norm, {
                "status": "unresolved",
                "reason": "resolver_context_required"}
        except prov.IntegrityError as exc:
            live_error = str(exc)
    else:
        live_error = ("pack_bytes_unverified" if index is None
                      else "audit_key records_file_sha256 != current pin")
    if resolved is None:
        return None, None, norm, {
            "status": "unresolved",
            "reason": live_error or "audit_key_unresolved"}
    entry = _catalog_entry(catalog, resolved.audit_key)
    return resolved, entry, norm, None


def audit_source_ref(source, ref, *, claim=None, context=None):
    """Verify one statistical source_ref against the provenance chain.

    ``source`` is the canonical source record the ref points at (its
    ``hash``, when present, must equal the audit key's
    ``records_file_sha256`` on the live path — the workspace bytes must
    BE the pinned pack bytes — or the bound snapshot receipt's sha256 on
    the snapshot path).  ``claim``, when given, is the submitted fact or
    economic declaration and is cross-checked against the trusted bound
    receipt (value/unit/locator/``source_pdf_sha256``).  Returns a verdict
    dict — ``status`` in ``resolved | unresolved | invalid`` — and never
    raises."""
    ctx = context if context is not None else resolver_context()
    resolved, entry, norm, verdict = _resolve_ref(source, ref, ctx)
    if verdict is not None:
        return verdict
    prov = _prov()
    out = {
        "status": "resolved",
        "catalog_status": resolved.catalog_status.value,
        "catalog_accepted": _ctx_accepted(ctx),
        "receipt_verified": _receipt_ok(entry),
        "source_ref": "%s@%s#%s" % (
            resolved.pack_id, resolved.pack_revision, resolved.record_id),
        "audit_key": prov.audit_key_dict(resolved.audit_key),
    }
    if claim is not None:
        reason = _claim_mismatch(
            claim, ref, resolved, entry, norm,
            catalog_accepted=_ctx_accepted(ctx))
        if reason is not None:
            return {"status": "invalid",
                    "reason": "claim_mismatch: %s" % reason}
        out["claim_verified"] = True
    return out


def verify_economic_claim(record, ref, *, context=None):
    """Cross-check an economic claim cell against the audited observation
    its ``source_ref`` resolves to (P1-1 for the finance lane).

    ``record`` — the cell's ``economic`` dict (``value``/``unit``/
    ``meaning_id``/optional ``source_locator`` + ``source_pdf_sha256``).
    ``ref`` — the cell's ``source_ref`` (``pack@rev#record`` string or an
    ``audit_key`` dict).  Returns the same verdict shape as
    ``audit_source_ref`` — never raises."""
    claim = {
        "metric": record.get("meaning_id"),
        "value": record.get("value"),
        "unit": record.get("unit"),
        "source_locator": record.get("source_locator"),
        "locator": record.get("source_locator"),
        "source_pdf_sha256": record.get("source_pdf_sha256"),
    }
    if isinstance(ref, str):
        resolved = resolve_pack_ref(ref, context=context)
        if resolved["status"] != "resolved":
            return resolved
        prov = _prov()
        ctx = context if context is not None else resolver_context()
        norm = prov.normalize_audit_key(resolved["audit_key"])
        index = (ctx.get("indexes") or {}).get(norm[0]) or {}
        catalog = ctx.get("catalog")
        entry = _catalog_entry(catalog, norm)
        row = index.get(norm)
        record = row.record if row is not None else {}
        obs = prov.ResolvedObservation(
            audit_key=norm, pack_id=norm[0],
            pack_revision=str(prov.PINNED_PACKS[norm[0]]["pack_revision"]),
            record_id=(entry or {}).get("record_id")
                      or record.get("record_id"),
            record=record,
            locator=(entry or {}).get("locator")
                    or record.get("locator"),
            catalog_status=prov.CatalogStatus(resolved["catalog_status"]),
            raw_line=row.raw_line if row is not None else None,
        )
        reason = _claim_mismatch(
            claim, ref, obs, entry, norm,
            catalog_accepted=_ctx_accepted(ctx))
        if reason is not None:
            return {"status": "invalid",
                    "reason": "claim_mismatch: %s" % reason}
        return dict(resolved, claim_verified=True,
                    catalog_accepted=_ctx_accepted(ctx),
                    receipt_verified=_receipt_ok(entry))
    if isinstance(ref, dict) and "audit_key" in ref:
        return audit_source_ref(None, ref, claim=claim, context=context)
    return {"status": "invalid", "reason": "not_a_statistical_ref"}


def resolve_pack_ref(ref, *, context=None):
    """Resolve a ``pack@pack_revision#record_id`` string to its audit-
    anchored observation.  ``record_id`` is NOT unique (SPEC §4): zero or
    several matches resolve to ``unresolved`` — never the first row."""
    prov = _prov()
    m = _STAT_REF.match(ref) if isinstance(ref, str) else None
    if not m:
        return {"status": "invalid", "reason": "malformed_statistical_ref"}
    pack_id, revision, record_id = m.groups()
    pin = prov.PINNED_PACKS.get(pack_id)
    if pin is None or str(pin["pack_revision"]) != str(revision):
        return {"status": "invalid",
                "reason": "unpinned pack or revision mismatch"}
    ctx = context if context is not None else resolver_context()
    index = (ctx.get("indexes") or {}).get(pack_id)
    if index is None:
        return {"status": "unresolved", "reason": "pack_bytes_unverified"}
    hits = [
        key for key, row in index.items()
        if (row.record or {}).get("record_id") == record_id
    ]
    if len(hits) != 1:
        return {"status": "unresolved",
                "reason": "record_id_absent" if not hits
                else "record_id_not_unique"}
    try:
        resolved = prov.resolve_reference(
            hits[0], catalog=ctx.get("catalog"), verified_index=index)
    except (prov.ResolutionContextError, prov.IntegrityError) as exc:
        return {"status": "unresolved", "reason": str(exc)}
    if resolved is None:
        return {"status": "unresolved", "reason": "audit_key_unregistered"}
    return {
        "status": "resolved",
        "catalog_status": resolved.catalog_status.value,
        "source_ref": "%s@%s#%s" % (
            resolved.pack_id, resolved.pack_revision, resolved.record_id),
        "audit_key": prov.audit_key_dict(resolved.audit_key),
    }


def resolve_statistical_refs(source_refs, *, context=None):
    """Resolve every ref claiming audited statistical provenance.

    Returns ``{"resolved_source_refs": [...], "conflicts": [...]}`` —
    resolved entries keep the caller's original ref string so existing
    ``resolved_source_refs`` membership checks stay byte-compatible;
    conflicts carry ``ref`` + ``kind`` + ``reason``.  Non-statistical refs
    pass through untouched."""
    resolved, conflicts = [], []
    for ref in source_refs:
        if isinstance(ref, dict) and "audit_key" in ref:
            verdict = audit_source_ref(None, ref, context=context)
        elif is_statistical_ref(ref):
            verdict = resolve_pack_ref(ref, context=context)
        else:
            resolved.append(ref)
            continue
        if verdict["status"] == "resolved":
            resolved.append(
                verdict["source_ref"] if isinstance(ref, dict) else ref)
        else:
            conflicts.append({"ref": ref, "kind": verdict["status"],
                              "reason": verdict.get("reason")})
    return {"resolved_source_refs": resolved, "conflicts": conflicts}


def build_rda_check(major_id, crop, comparisons):
    return {"major_id": major_id, "crop": crop,
            "comparisons": list(comparisons)}


def write_rda_check(root, data):
    import gg_core

    path = gg_core.local(root, "reviews/rda-check.json")
    gg_core.atomic(path, json.dumps(data, ensure_ascii=False,
                                    indent=2).encode("utf-8"))
    return path
