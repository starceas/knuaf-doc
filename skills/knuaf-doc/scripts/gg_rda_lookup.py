"""RDA/MAFRA 통계 원자료 조회. 승격된 references/benchmark-packs JSON만 읽으며
모델 호출이나 네트워크 접근은 하지 않는다. rank 1(공통) -> rank 2(전공) 순서로
탐색하고, 부재 시 rank 3(웹)은 allow_web=True일 때만 스텁으로 시도한다.

모든 반환 레코드는 캐시와 공유되지 않는 깊은 복사본이며 ``verification``
묶음이 붙는다(K1/K7 — 팩 레코드가 스스로 ``verification`` 키를 갖고 있어도
항상 조회 계층의 계산값으로 덮어쓴다). 완전 필터 단일 관측 또는 audit_key
지정 선택은 검증된 관측만 ``unique``가 되고 그 외는 ``unverified``다
(K2 — 관측 검증 술어 ``is_verified_observation``는 candidates/research와
공용이다).
"""

import copy
import hashlib
import json
import unicodedata
from pathlib import Path

import gg_rda_provenance as _prov

BASE_DIR = Path(__file__).resolve().parents[1] / "references" / "benchmark-packs"

_SIDO_TABLE = {
    "서울": "서울", "서울특별시": "서울",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "인천": "인천", "인천광역시": "인천",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주",
}

# multi-period price/yield series kinds — scalar extraction disallowed
# (P4-BASELINE-PROVENANCE-BINDING §2 verdict 4, binds D03)
_SERIES_KINDS = {"official_market_price", "wholesale_price_series",
                 "yield_series"}

_CACHE = {}


def _normalize(value):
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _load_json(relpath):
    key = ("json", relpath)
    if key not in _CACHE:
        _CACHE[key] = json.loads((BASE_DIR / relpath).read_text(encoding="utf-8"))
    return _CACHE[key]


def _load_records(relpath):
    key = ("records", relpath)
    if key not in _CACHE:
        path = BASE_DIR / relpath
        records = []
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        _CACHE[key] = records
    return _CACHE[key]


def _catalog():
    return _load_json("catalog.json")


def _major_aliases():
    return _load_json("aliases/major-aliases.json")


def _crop_aliases(major_id):
    relpath = "aliases/crop-aliases.%s.json" % major_id
    if not (BASE_DIR / relpath).exists():
        return {"alias_to_canonical": {}}
    return _load_json(relpath)


def _pack_records(pack):
    """Verified, audit-key-annotated records for a cataloged pack.

    Each returned record dict carries an ``audit_key`` field (AUDIT-JOIN
    shape) bound to its exact physical JSONL line — colliding legacy
    ``record_id`` values stay distinguishable (binds D02).  Pack bytes are
    integrity-verified against the pinned registry before use (SPEC §5)."""
    key = ("audit_records", pack["relpath"])
    if key not in _CACHE:
        path = BASE_DIR / pack["relpath"]
        if not path.exists():
            # cataloged but not promoted -> empty, as the baseline loader
            # behaved (keeps _any_pack_not_promoted working)
            _CACHE[key] = []
        else:
            rows = _prov.load_pack_records(path, pack["pack_id"])
            _CACHE[key] = [
                dict(row.record,
                     audit_key=_prov.audit_key_dict(row.audit_key))
                for row in rows if row.record is not None
            ]
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Approved catalog (pack manifest) loading + authentication — shared with
# gg_rda_candidates (K4: one verification basis).  A pack's manifest
# ``rows`` ARE its observation catalog.  Acceptance is authenticated, never
# claimed: the whole entry set must reproduce a content digest pinned for
# this pack_id in ``gg_rda_research.PINNED_CATALOG_AUTHORITIES`` (SCC1).
# Authentication is evaluated against the manifest bytes as they exist at
# call time; the result memoizes only on (pack_id, manifest-bytes sha256,
# the pack's own pinned (authority, digest) pairs) so byte or authority
# changes always recompute (F1).  Cached entries stay internal — callers
# receive deep copies only (K7).


def _research():
    import gg_rda_research

    return gg_rda_research


def _pinned_authority_pairs(pack_id):
    """Sorted ``(authority, digest)`` pairs pinned for THIS pack — the
    accepted-catalog authentication is per pack_id, another pack's
    authority never authenticates this manifest."""
    pack_id = str(pack_id)
    return tuple(sorted(
        (auth, digest)
        for auth, digest in _research().PINNED_CATALOG_AUTHORITIES.items()
        if auth == pack_id or auth.endswith("/" + pack_id)))


def _catalog_entries(pack):
    """Read the pack manifest's rows as ``{AuditKey: entry}``.

    Returns ``(entries, accepted)`` — ``entries`` is ``None`` when the
    manifest is absent/malformed/duplicated; ``accepted`` is True only
    when the full content reproduces a pinned accepted-catalog authority
    digest naming this pack_id.  The manifest bytes are re-read and
    re-hashed on every call; cached results never outlive a manifest or
    pinned-authority change."""
    if not isinstance(pack, dict):
        return None, False
    relpath = pack.get("relpath")
    if not relpath:
        return None, False
    mpath = (BASE_DIR / relpath).parent / "manifest.json"
    if not mpath.is_file():
        return None, False
    try:
        mbytes = mpath.read_bytes()
    except OSError:
        return None, False
    msha = hashlib.sha256(mbytes).hexdigest()
    pack_id = str(pack.get("pack_id"))
    pinned = _pinned_authority_pairs(pack_id)
    key = ("catalog", pack_id, msha, pinned)
    if key in _CACHE:
        return _CACHE[key]
    entries, accepted = None, False
    try:
        manifest = json.loads(mbytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        manifest = None
    if isinstance(manifest, dict) \
            and manifest.get("pack_id") == pack.get("pack_id"):
        rows = manifest.get("rows")
        if isinstance(rows, list):
            parsed = {}
            ok = True
            for row in rows:
                if not isinstance(row, dict):
                    ok = False
                    break
                try:
                    ekey = _prov.normalize_audit_key(row.get("audit_key"))
                except (ValueError, TypeError):
                    ok = False
                    break
                if ekey in parsed:
                    # duplicate physical key — not authenticatable
                    ok = False
                    break
                parsed[ekey] = row
            if ok:
                entries = parsed
                try:
                    digest = _research().catalog_content_digest(entries)
                except (ValueError, TypeError):
                    # FIX1-M2: canonicalization failures (a JSON-escaped
                    # lone surrogate raising UnicodeEncodeError, a
                    # ValueError subclass, or a malformed audit key in
                    # normalization) are malformed content, not a crash
                    # - keep the parsed entries and fail authentication
                    # closed (level resolves to unknown).
                    digest = None
                accepted = (digest is not None and
                            any(digest == pinned_d
                                for _auth, pinned_d in pinned))
    result = (entries, accepted)
    _CACHE[key] = result
    return result


def _catalog_receipt_complete(entry):
    """Complete observation receipt on an approved-catalog entry — the
    existing accepted-catalog contract ``source_observation.{
    observation_status, source_pdf_sha256, prep_receipt_ref}`` unchanged."""
    if not isinstance(entry, dict):
        return False
    so = entry.get("source_observation")
    if not isinstance(so, dict):
        return False
    if not isinstance(so.get("observation_status"), str) \
            or not so["observation_status"].strip():
        return False
    if not _is_sha256(so.get("source_pdf_sha256")):
        return False
    return isinstance(so.get("prep_receipt_ref"), str) \
        and bool(so["prep_receipt_ref"].strip())


def _is_sha256(v):
    return isinstance(v, str) and len(v) == 64 \
        and all(c in "0123456789abcdef" for c in v)


def is_verified_observation(entry, record, catalog_accepted):
    """THE shared observation-verification predicate (K8): this physical
    row is a source-verified observation only when the manifest is an
    authenticated accepted catalog for its pack, the row's catalog entry
    claims ``verified_observation`` with a complete observation receipt,
    and the row itself is ``extraction.status == "extracted"``.

    ``record`` must come from hash-verified physical bytes (lookup's
    ``_pack_records`` / research's verified index row).  The same
    predicate backs ``verification.verified`` here, the candidates
    observation axis, and the research ``promotable`` gate — one basis,
    fail-closed."""
    if not catalog_accepted:
        return False
    if not isinstance(entry, dict):
        return False
    status = entry.get("catalog_status")
    if isinstance(status, _prov.CatalogStatus):
        status = status.value
    if status != _prov.CatalogStatus.VERIFIED_OBSERVATION.value:
        return False
    if not _catalog_receipt_complete(entry):
        return False
    extraction = (record or {}).get("extraction")
    return isinstance(extraction, dict) \
        and extraction.get("status") == "extracted"


def _observation_key(record):
    """Distinct-observation identity for cardinality: the FULL audit key
    ``(pack_id, records_file_sha256, physical_jsonl_line_1based,
    raw_line_sha256)`` — every physical row is its own observation.

    Every physical row counts directly (corrections P4-02): rows are NEVER
    collapsed on raw-byte equality, legacy-ID/value coincidence, or any
    unverified record field — there is no alias/merge path at this layer
    (SPEC §4, binds D02)."""
    ak = record.get("audit_key") or {}
    return (ak.get("pack_id"), ak.get("records_file_sha256"),
            ak.get("physical_jsonl_line_1based"), ak.get("raw_line_sha256"))


def _distinct_observations(records):
    """Ordered distinct observations, first occurrence order (deterministic,
    independent of caller argument order)."""
    seen, out = set(), []
    for rec in records:
        k = _observation_key(rec)
        if k not in seen:
            seen.add(k)
            out.append(rec)
    return out


def _resolve_major(major):
    key = _normalize(major)
    if not key:
        return None, None
    majors = _major_aliases().get("majors", {})
    if key in majors:
        return key, None
    for major_id, info in majors.items():
        for alias in info.get("aliases", []):
            if _normalize(alias) == key:
                return major_id, None
    return None, "전공 식별 실패 — 지원하는 전공명을 확인해 주세요."


def _crop_key(crop_normalized, major_id):
    if not crop_normalized or not major_id:
        return crop_normalized
    table = _crop_aliases(major_id).get("alias_to_canonical", {})
    return table.get(crop_normalized, crop_normalized)


def _resolve_region(region_normalized):
    if not region_normalized or region_normalized in ("전국", "national"):
        return {"level": "national", "sido": None}
    canonical = _SIDO_TABLE.get(region_normalized)
    if canonical:
        return {"level": "provincial", "sido": canonical}
    return {"level": "provincial", "sido": region_normalized}


def _crop_matches(query_key, crop_obj):
    if not crop_obj or not query_key:
        return False
    name = _normalize(crop_obj.get("name"))
    if name:
        if name == query_key:
            return True
        if name.startswith(query_key) and len(name) > len(query_key) and name[len(query_key)] in "(（":
            return True
    for alias in crop_obj.get("aliases") or []:
        if _normalize(alias) == query_key:
            return True
    return False


def _record_identity(record):
    crop = record.get("crop") or {}
    name = _normalize(crop.get("name"))
    if name:
        return name
    form = _normalize(crop.get("form"))
    if form:
        return form
    basis = record.get("basis") or {}
    return _normalize(basis.get("item_name"))


def _region_matches(record_region, region_info):
    level = (record_region or {}).get("level")
    if level == "national":
        return True
    if level == "market":
        return True
    if level in ("provincial", "metropolitan"):
        if region_info["level"] != "provincial":
            return False
        return _normalize((record_region or {}).get("sido")) == region_info["sido"]
    return True


def _tag_regional_caveat(record):
    if record.get("kind") != "crop_income_regional":
        return record
    caveats = record.get("caveats") or []
    if any("유의성" in _normalize(c) for c in caveats):
        return record
    tagged = dict(record)
    tagged["caveats"] = list(caveats) + ["통계적 유의성 없음"]
    return tagged


def _is_rejected(record):
    # fail-closed on malformed extraction payloads (a non-dict ``extraction``
    # is not "rejected" — it still can never verify via the K8 predicate)
    extraction = (record or {}).get("extraction")
    return isinstance(extraction, dict) \
        and extraction.get("status") == "rejected"


def _filter_records(records, *, crop_key, kind, year, form):
    matched = []
    for rec in records:
        if _is_rejected(rec):
            continue
        if crop_key:
            if not (_crop_matches(crop_key, rec.get("crop")) or _record_identity(rec) == crop_key):
                continue
        if kind and rec.get("kind") != kind:
            continue
        if year is not None and (rec.get("basis") or {}).get("year") != year:
            continue
        if form is not None and _normalize((rec.get("crop") or {}).get("form")) != _normalize(form):
            continue
        matched.append(rec)
    return matched


def _search_pack_group(packs, *, crop_key, kind, year, form, region_info):
    matched = []
    pack_ids = []
    for pack in packs:
        records = _pack_records(pack)
        hits = _filter_records(records, crop_key=crop_key, kind=kind, year=year, form=form)
        hits = [r for r in hits if _region_matches(r.get("region"), region_info)]
        hits = [_tag_regional_caveat(r) for r in hits]
        if hits:
            matched.extend(hits)
            pack_ids.append(pack["pack_id"])
    return matched, pack_ids


def _search_common(catalog, **kwargs):
    packs = [p for p in catalog.get("packs", []) if p.get("kind") == "common"]
    return _search_pack_group(packs, **kwargs)


def _search_major(catalog, major_id, **kwargs):
    packs = [p for p in catalog.get("packs", []) if p.get("major_id") == major_id]
    return _search_pack_group(packs, **kwargs)


def _resolve_verdict(candidates, *, complete_filter, verify=None):
    """D02 post-selection cardinality + observation verification.

    Public verdict enum: ``not_found | ambiguous | series | unverified |
    unique``.

    - Distinct observations are counted AFTER every supplied filter over
      FULL audit identity — every physical row is its own observation;
      no collapse on byte equality, legacy-ID/value coincidence, or any
      unverified alias field (corrections P4-02).
    - Multi-observation ambiguity takes precedence over series typing
      (correction P4-04): >1 distinct observation is ``ambiguous`` even
      when all are series kinds. ``series`` is returned only when exactly
      ONE observation remains and it is series-typed — a multi-period
      value where scalar extraction is disallowed (binds D03).
    - A single remaining observation is then gated by the shared
      ``is_verified_observation`` predicate (K2): ``unique`` only when
      verified, otherwise ``unverified``.  ``verify(record)`` returns the
      record's ``verification`` bundle; without one the gate fails closed
      (``unverified``/``unknown``).  No partial-filter combination
      without an explicit ``audit_key`` may reach this gate — a lone
      candidate under a partial filter is still ``ambiguous`` because
      partial specificity cannot establish uniqueness.
    """
    distinct = _distinct_observations(candidates)
    if not distinct:
        return "not_found", None
    if len(distinct) > 1:
        return "ambiguous", ("서로 다른 관측 %d건 — audit_key 또는 완전한 "
                             "필터 조합으로 좁혀야 한다" % len(distinct))
    if distinct[0].get("kind") in _SERIES_KINDS:
        return "series", ("다기간 시계열 관측 — period 등으로 명시 선택해야 "
                          "한다 (스칼라 자동 추출 금지)")
    if not complete_filter:
        return "ambiguous", ("후보 1건이나 부분 필터만으로는 고유성을 확정할 "
                             "수 없다 — audit_key 또는 crop/region/major/"
                             "kind/year/form 완전 조합이 필요하다")
    verification = verify(distinct[0]) if verify else {"level": "unknown"}
    if verification.get("verified") is True:
        return "unique", ("관측 1건 확인·검증됨 — 사용 승인 아님 "
                          "(rda-candidates)")
    return "unverified", (
        "관측 1건이나 검증되지 않음(%s) — 값을 본문·표에 옮기지 말 것. "
        "원문 대조·연구 제안(rda-propose)으로만 다룬다"
        % verification.get("level"))


def _relevant_packs(catalog, major_id):
    packs = [p for p in catalog.get("packs", []) if p.get("kind") == "common"]
    if major_id:
        packs += [p for p in catalog.get("packs", []) if p.get("major_id") == major_id]
    return packs


def _any_pack_not_promoted(packs):
    return any(not _pack_records(p) for p in packs)


def _miss_reason(catalog, major_id, crop_key):
    if major_id is None:
        return "no_match"
    packs_for_major = [p for p in catalog.get("packs", []) if p.get("major_id") == major_id]
    if not packs_for_major:
        return "major_pack_empty"
    if major_id == "specialty_crops" and crop_key.startswith("인삼"):
        for gap in catalog.get("gaps", []):
            if gap.get("gap_id") == "excluded_from_mafra_specialty_survey":
                return "excluded_from_mafra_specialty_survey"
    return "no_match"


def _web_lookup_stub():
    return {"status": "miss", "reason": "web_not_implemented_in_mvp"}


def _attempt_web(kind, allow_web):
    if not allow_web:
        return None
    if kind != "input_price":
        return {"status": "miss", "reason": "web_not_applicable"}
    return _web_lookup_stub()


def _verification_summary(records):
    """K3 top-level tally over the returned records' ``level`` — zero
    records means all counts zero, ``all_verified`` false, ``notice``
    null; any non-verified record attaches the notice string."""
    counts = {"verified": 0, "exploratory": 0, "quarantined": 0,
              "unknown": 0}
    for rec in records or []:
        level = ((rec.get("verification") or {}).get("level"))
        counts[level if level in counts else "unknown"] += 1
    unverified_n = len(records or []) - counts["verified"]
    return dict(counts,
                all_verified=bool(records) and unverified_n == 0,
                notice=(
                    "검증되지 않은 레코드 %d건 포함 — 각 레코드 "
                    "verification.label 확인" % unverified_n
                    if unverified_n else None))


def _result(status, *, rank_used=None, pack_id=None, records=None,
            reason=None, web=None):
    return {
        "status": status,
        "rank_used": rank_used,
        "pack_id": pack_id,
        "records": records if records is not None else [],
        "reason": reason,
        "web": web,
        "verification_summary": _verification_summary(records),
    }


def _select_by_audit_key(candidates, audit_key):
    """Explicit audit_key selection (D02 path a): pick the one candidate
    whose physical audit key matches, inside the already filter-matched
    set.  A key that is not among the candidates resolves to ``not_found``
    — it is never re-evaluated against the pre-selection ambiguous set."""
    wanted = _prov.normalize_audit_key(audit_key)
    for rec in candidates:
        if _prov.normalize_audit_key(rec.get("audit_key")) == wanted:
            return rec
    return None


_VERIFICATION_LABELS = {
    "verified":
        "검증됨 — 원문 대조 완료(관측 확인일 뿐, 본문·표 사용은 "
        "rda-candidates 승인 필요)",
    "exploratory":
        "탐색용 — 원문 대조 미완. 본문·표에 옮기지 말 것",
    "quarantined":
        "격리 — 원문과 불일치하거나 미확인. 본문·표에 옮기지 말 것",
    "unknown":
        "검증 상태 확인 불가 — 본문·표에 옮기지 말 것",
}


def _pack_catalogs(catalog, pack_ids):
    """pack_id -> (entries, accepted) for the cataloged packs that
    produced records — entries stay internal (never reach callers except
    as scalar field values)."""
    out = {}
    needed = set(pack_ids)
    for pack in catalog.get("packs", []):
        pid = pack.get("pack_id")
        if pid in needed and pid not in out:
            out[pid] = _catalog_entries(pack)
    return out


def _utf8_status(value):
    """Return a status only when it can be printed as UTF-8 JSON text."""
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _verification_bundle(record, catalogs):
    """K1 per-record verification bundle — computed by THIS layer only
    (a ``verification`` key already on the pack record is ignored and
    always overwritten).  ``level`` fails closed to ``unknown`` whenever
    the authenticated basis is absent, unauthenticated, malformed, or
    inconsistent with its own claim."""
    ak = record.get("audit_key")
    pid = ak.get("pack_id") if isinstance(ak, dict) else None
    entries, accepted = catalogs.get(pid, (None, False))
    try:
        norm = _prov.normalize_audit_key(ak) if ak is not None else None
    except (ValueError, TypeError):
        norm = None
    entry = entries.get(norm) if (entries and norm is not None) else None
    catalog_status = (entry or {}).get("catalog_status")
    if isinstance(catalog_status, _prov.CatalogStatus):
        catalog_status = catalog_status.value
    receipt_complete = _catalog_receipt_complete(entry)
    so = (entry or {}).get("source_observation")
    observation_status = (so.get("observation_status")
                          if isinstance(so, dict) else None)
    extraction = record.get("extraction")
    extraction_status = (extraction.get("status")
                         if isinstance(extraction, dict) else None)
    # Only UTF-8 encodable strings may reach the CLI's ensure_ascii=False
    # JSON output.  In particular, json.loads accepts escaped lone
    # surrogates that stdout cannot encode.  Never expose malformed status
    # values, or treat a partially malformed bundle as verified.
    statuses = (catalog_status, observation_status, extraction_status)
    safe_statuses = tuple(_utf8_status(value) for value in statuses)
    # absent (None) is not malformed — only a present value that is not
    # a UTF-8 encodable string is (K1: level keeps the catalog status)
    malformed = any(value is not None and safe is None
                    for value, safe in zip(statuses, safe_statuses))
    catalog_status, observation_status, extraction_status = safe_statuses
    verified = (not malformed and
                is_verified_observation(entry, record, accepted))
    if verified:
        level = "verified"
    elif not malformed and accepted and catalog_status in ("exploratory", "quarantined"):
        level = catalog_status
    else:
        level = "unknown"
    # Strings are immutable, so these scalar status values cannot expose
    # cached manifest entries or pack rows to caller mutation.
    return {
        "level": level,
        "verified": bool(verified),
        "catalog_status": catalog_status,
        "catalog_accepted": bool(accepted),
        "receipt_complete": bool(receipt_complete),
        "observation_status": observation_status,
        "extraction_status": extraction_status,
        "label": _VERIFICATION_LABELS[level],
    }


def _decorate_records(records, catalogs):
    """K7: every returned record is a deep copy detached from the cache,
    with a freshly computed ``verification`` dict attached."""
    decorated = []
    for rec in records:
        copied = copy.deepcopy(rec)
        copied["verification"] = _verification_bundle(copied, catalogs)
        decorated.append(copied)
    return decorated


def lookup_rda_data(crop, region, *, major=None, kind=None, year=None,
                    form=None, allow_web=False, audit_key=None):
    crop_norm = _normalize(crop)
    region_norm = _normalize(region)
    if not crop_norm or not region_norm:
        return _result("not_found",
                       reason="crop과 region은 비어 있을 수 없다")
    major_id = None
    if major is not None:
        major_id, major_error = _resolve_major(major)
        if major_error:
            return _result("not_found", reason=major_error)
    crop_key = _crop_key(crop_norm, major_id)
    region_info = _resolve_region(region_norm)
    catalog = _catalog()
    common_records, common_pack_ids = _search_common(
        catalog, crop_key=crop_key, kind=kind, year=year, form=form, region_info=region_info
    )
    major_records, major_pack_ids = [], []
    if major_id:
        major_records, major_pack_ids = _search_major(
            catalog, major_id, crop_key=crop_key, kind=kind, year=year, form=form, region_info=region_info
        )
    combined = common_records + major_records
    if not combined:
        relevant = _relevant_packs(catalog, major_id)
        reason = "pack_not_promoted" if _any_pack_not_promoted(relevant) else _miss_reason(catalog, major_id, crop_key)
        web = _attempt_web(kind, allow_web)
        return _result("not_found", reason=reason, web=web)
    catalogs = _pack_catalogs(
        catalog,
        [(r.get("audit_key") or {}).get("pack_id") for r in combined])
    if audit_key is not None:
        selected = _select_by_audit_key(combined, audit_key)
        if selected is None:
            return _result("not_found",
                           reason="audit_key가 필터 결과 후보와 일치하지 않는다")
        rank_used = 1 if selected in common_records else 2
        pack_id = selected["audit_key"]["pack_id"]
        # K2 audit path runs the SAME verdict ordering: a series
        # observation stays ``series`` no matter how it was selected,
        # a verified single is ``unique``, everything else ``unverified``.
        verification = _verification_bundle(selected, catalogs)
        if selected.get("kind") in _SERIES_KINDS:
            status = "series"
            reason = ("다기간 시계열 관측 — period 등으로 명시 선택해야 "
                      "한다 (스칼라 자동 추출 금지)")
        elif verification["verified"]:
            status = "unique"
            reason = ("관측 1건 확인·검증됨 — 사용 승인 아님 "
                      "(rda-candidates)")
        else:
            status = "unverified"
            reason = ("관측 1건이나 검증되지 않음(%s) — 값을 본문·표에 "
                      "옮기지 말 것. 원문 대조·연구 제안(rda-propose)으로만 "
                      "다룬다" % verification["level"])
        out = copy.deepcopy(selected)
        out["verification"] = verification
        return _result(status, rank_used=rank_used, pack_id=pack_id,
                       records=[out], reason=reason)
    complete_filter = (major is not None and kind is not None
                       and year is not None and form is not None)
    status, reason = _resolve_verdict(
        combined, complete_filter=complete_filter,
        verify=lambda rec: _verification_bundle(rec, catalogs))
    decorated = _decorate_records(combined, catalogs)
    if status not in ("unique", "unverified"):
        return _result(status, records=decorated, reason=reason)
    rank_used = 1 if common_records else 2
    all_pack_ids = common_pack_ids + major_pack_ids
    pack_id = all_pack_ids[0] if all_pack_ids else None
    return _result(status, rank_used=rank_used, pack_id=pack_id,
                   records=decorated, reason=reason)
