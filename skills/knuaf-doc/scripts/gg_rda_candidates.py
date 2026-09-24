"""승인 후보(approved-candidate) 조회 — 새 전공 인식(major-aware) 작성 경로.

``gg_rda_lookup.lookup_rda_data``는 raw/legacy 계약이다: 물리 행 감사 키를
붙인 관측 후보 집합·순서와 ``not_found|ambiguous|unique|series`` 판정을
그대로 보존하며, 이 모듈은 그 결과를 변경하지 않고 소비만 한다. ``unique``
는 물리 관측 하나가 식별됐다는 뜻이지 작성 사용 허가가 아니다
(DECISION-20260924 §1·§3).

세 결과를 서로 다른 축으로 분리해 반환한다:

  observation  — raw 조회 판정과 후보의 물리 감사 키(그대로 통과)
  eligibility  — 후보별 축 평가: 원본 수용(바이트 확보/신형 영수증)·관측
                 검증·충돌·권리·적용 적합성(전공·종/제품/작목·지역·기간·
                 단위·모집단·use_scope)
  use approval — 선택이 모호하지 않은지(raw ``unique``만; 부분 필터 단독
                 후보·다중 관측·기간 미지정 series는 불가) + 팩 manifest가
                 이 팩에 고정된 승인 catalog 권위의 content digest를
                 재현하고 해당 행의 관측 영수증이 완전한지

모든 축은 fail-closed다 — 선언이 없거나 확인 불가하면 부합으로 간주하지
않고, 질의가 값을 주지 않은 축은 물리 레코드의 실측값으로 대조한다(레코드
에도 없으면 unconfirmed). 현재 팩은 확장 축 선언(``evidence``)을 갖지 않
으므로 현재 레코드는 승인되지 않는다. 명시적 ``audit_key`` 지정은 후보
선택일 뿐 어느 게이트도 우회하지 않으며, 선언된 cross-source 충돌을 해소
하지도 않는다. 후단 ``gg_rda_research``의 ``promotable`` 게이트는 별도로
유지되며 이 결과와 합쳐지지 않는다.
"""

import json
from datetime import datetime
from pathlib import Path

import gg_rda_lookup as _lookup
import gg_rda_provenance as _prov
import gg_rda_research as _research

SCHEMA = "knuaf-doc/approved-candidates@1"

_VERIFIED = _prov.CatalogStatus.VERIFIED_OBSERVATION.value

# rejection reasons belonging to the use-approval layer (selection +
# approved-catalog).  Every other reason belongs to eligibility — a row
# can be fully eligible yet not approvable when the observation set is
# ambiguous or the catalog is unauthenticated.
_APPROVAL_REASONS = frozenset({
    "observation_selection_unresolved",
    "series_period_unspecified",
    "catalog_not_accepted",
    "catalog_entry_absent",
    "catalog_receipt_incomplete",
})

_HEX = set("0123456789abcdef")


def _norm(v):
    return _lookup._normalize(v)


def _is_sha256(v):
    return isinstance(v, str) and len(v) == 64 and all(c in _HEX for c in v)


def _is_timestamp(v):
    """ISO-8601 datetime WITH a time component (date-only is not a
    취득 시각)."""
    if not isinstance(v, str) or "T" not in v:
        return False
    try:
        datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Approved catalog (pack manifest) loading + authentication
#
# A pack's manifest ``rows`` ARE its observation catalog.  Acceptance is
# authenticated, never claimed: the whole entry set must reproduce a
# content digest pinned for this pack_id in
# ``_research.PINNED_CATALOG_AUTHORITIES`` (SCC1 — same trust boundary as
# the research lane; a caller cannot mint acceptance by construction).


def _catalog_entries(pack):
    """Read the pack manifest's rows as ``{AuditKey: entry}``.

    Returns ``(entries, accepted)`` — ``entries`` is ``None`` when the
    manifest is absent/malformed/duplicated; ``accepted`` is True only
    when the full content reproduces a pinned accepted-catalog authority
    digest naming this pack_id.
    """
    if not isinstance(pack, dict):
        return None, False
    relpath = pack.get("relpath")
    if not relpath:
        return None, False
    mpath = (Path(_lookup.BASE_DIR) / relpath).parent / "manifest.json"
    if not mpath.is_file():
        return None, False
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, False
    if not isinstance(manifest, dict) \
            or manifest.get("pack_id") != pack.get("pack_id"):
        return None, False
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        return None, False
    entries = {}
    for row in rows:
        if not isinstance(row, dict):
            return None, False
        try:
            key = _prov.normalize_audit_key(row.get("audit_key"))
        except (ValueError, TypeError):
            return None, False
        if key in entries:
            return None, False  # duplicate physical key — not authenticatable
        entries[key] = row
    digest = _research.catalog_content_digest(entries)
    pack_id = str(pack.get("pack_id"))
    accepted = any(
        digest == pinned
        for auth, pinned in _research.PINNED_CATALOG_AUTHORITIES.items()
        if auth == pack_id or auth.endswith("/" + pack_id)
    )
    return entries, accepted


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


# ---------------------------------------------------------------------------
# Eligibility axes — every axis fails closed when undeclared/unconfirmed


_SOURCE_REQUIRED = ("media_type", "source_id", "source_sha256",
                    "acquired", "locator", "parser", "output_sha256",
                    "unit", "population")


def _source_receipt_errors(src, pack):
    """New-format source receipt axis (DECISION §3): media type,
    ``source_id``/``source_sha256``/acquisition path+time, physical page or
    sheet/cell locator, parser identity+version, parsed-output hash,
    original unit/population — plus custody ``bytes_secured``
    (``link_only`` fails) and binding of ``source_sha256`` to the pack's
    catalog-pinned source hash."""
    if not isinstance(src, dict):
        return ["source_receipt_absent"], {}
    errs, detail = [], {}
    if src.get("custody") != "bytes_secured":
        errs.append("source_bytes_not_secured")
        detail["custody"] = src.get("custody")
    missing = [f for f in _SOURCE_REQUIRED if not src.get(f)]
    invalid = []
    if "source_sha256" not in missing \
            and not _is_sha256(src["source_sha256"]):
        invalid.append("source_sha256")
    if "output_sha256" not in missing \
            and not _is_sha256(src["output_sha256"]):
        invalid.append("output_sha256")
    parser = src.get("parser")
    if "parser" not in missing and not (
            isinstance(parser, dict)
            and isinstance(parser.get("id"), str) and parser["id"].strip()
            and isinstance(parser.get("version"), str)
            and parser["version"].strip()):
        invalid.append("parser")
    acq = src.get("acquired")
    if "acquired" not in missing:
        if not isinstance(acq, dict):
            invalid.append("acquired")
        else:
            if not (isinstance(acq.get("path"), str)
                    and acq["path"].strip()):
                invalid.append("acquired.path")
            if not _is_timestamp(acq.get("at")):
                invalid.append("acquired.at")
    loc = src.get("locator")
    if "locator" not in missing and not (
            isinstance(loc, dict) and (
                isinstance(loc.get("pdf_page"), int)
                or (loc.get("sheet") and loc.get("cell")))):
        invalid.append("locator")
    if missing or invalid:
        errs.append("source_receipt_incomplete")
        detail["missing"] = missing
        detail["invalid"] = invalid
    pinned_src = pack.get("source_sha256")
    if not pinned_src:
        errs.append("source_sha256_unbound")
    elif _is_sha256(src.get("source_sha256")) \
            and pinned_src != src["source_sha256"]:
        errs.append("source_sha256_mismatch")
        detail["pinned_source_sha256"] = pinned_src
    return errs, detail


def _conflict_errors(ev):
    """``none`` passes; ``unresolved`` and undeclared fail.  ``corrected``
    is held: no authenticated correction-evidence contract exists yet, so
    a bare ``corrected`` string can never prove resolution."""
    c = ev.get("conflict")
    if c == "none":
        return [], c
    if c == "unresolved":
        return ["conflict_unresolved"], c
    if c == "corrected":
        return ["conflict_correction_unverified"], c
    return ["conflict_status_unconfirmed"], c


def _rights_errors(ev):
    """Redistribution + use rights must BOTH carry explicit
    ``"confirmed"`` values — a bare list of grant names, internal-only,
    or unconfirmed rights never approve."""
    r = ev.get("rights")
    grants = {k for k, v in r.items() if v == "confirmed"} \
        if isinstance(r, dict) else set()
    if {"redistribution", "use"} - grants:
        return ["rights_unconfirmed"], r
    return [], r


def _declared_region(d):
    """Normalize one declared applicability region to the lookup layer's
    ``{level, sido}`` shape; ``None`` when unrecognizable."""
    if isinstance(d, str):
        return _lookup._resolve_region(_norm(d))
    if isinstance(d, dict):
        if d.get("sido"):
            return _lookup._resolve_region(_norm(d["sido"]))
        if _norm(d.get("level")) in ("national", "전국") \
                or _norm(d.get("name")) in ("전국", "national"):
            return {"level": "national", "sido": None}
    return None


def _period_covers(periods, year):
    try:
        y = int(year)
    except (TypeError, ValueError):
        return False
    for p in periods:
        try:
            if isinstance(p, dict):
                if p.get("year") is not None and int(p["year"]) == y:
                    return True
                if p.get("from") is not None and p.get("to") is not None \
                        and int(p["from"]) <= y <= int(p["to"]):
                    return True
            elif int(p) == y:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _record_region(record):
    """The physical row's own region, normalized; an unverifiable region
    becomes an uncoverable target (fails closed as mismatch)."""
    rr = record.get("region")
    if isinstance(rr, dict):
        lvl = rr.get("level")
        if lvl in ("national", "market"):
            return {"level": "national", "sido": None}
        if lvl in ("provincial", "metropolitan") and rr.get("sido"):
            return _lookup._resolve_region(_norm(rr["sido"]))
    return {"level": "__unverifiable__", "sido": None}


def _record_units(record):
    """Every non-null unit the physical row's basis actually carries."""
    basis = record.get("basis")
    if not isinstance(basis, dict):
        return []
    return [_norm(basis[k]) for k in
            ("quantity_unit", "area_unit", "currency")
            if basis.get(k)]


def _region_covers(decl, target):
    return decl is not None and (
        (target["level"] == "national" and decl["level"] == "national")
        or (target["level"] == "provincial"
            and decl["level"] == "provincial"
            and decl["sido"] == target["sido"]))


def _applicability_errors(decl, *, record, src, major_id, crop_key,
                          region_info, year, form, unit, use_scope):
    """Applicability axis (DECISION §2): a common-pack row is NOT applied
    to every major just because its ``major_id`` is null — each row must
    declare and match major/species/product/region/period/unit/population/
    use_scope.  Undeclared is unconfirmed, never assumed.

    Query-vs-declared matching is two-sided: a value the query did not
    specify is verified against the PHYSICAL record (its own form, basis
    year, region, basis units) and the bound source receipt (unit,
    population).  When neither side supplies a value the axis is
    ``*_unconfirmed`` — it never silently matches."""
    errs, axes = [], {}
    if not isinstance(decl, dict):
        decl = {}

    mids = decl.get("major_ids")
    if isinstance(mids, list) and major_id in mids:
        axes["major"] = "match"
    else:
        errs.append("major_not_applicable")
        axes["major"] = "not_applicable"

    # species — the declaration must cover the resolved query crop AND
    # the physical row's own identity (a declaration about another crop
    # does not apply to this row)
    species = decl.get("species")
    if not isinstance(species, list) or not species:
        errs.append("species_unconfirmed")
        axes["species"] = "unconfirmed"
    else:
        norm_sp = {_norm(s) for s in species}
        targets = {_norm(crop_key)}
        ident = _norm(_lookup._record_identity(record))
        if ident:
            targets.add(ident)
        if targets - norm_sp:
            errs.append("species_mismatch")
            axes["species"] = "mismatch"
        else:
            axes["species"] = "match"

    # product/form — targets = query form ∪ physical row form
    products = decl.get("products")
    norm_pr = {_norm(p) for p in products} \
        if isinstance(products, list) else set()
    p_targets = set()
    rec_form = _norm((record.get("crop") or {}).get("form"))
    if rec_form:
        p_targets.add(rec_form)
    q_form = _norm(form) if form is not None else ""
    if q_form:
        p_targets.add(q_form)
    if not norm_pr or not p_targets:
        errs.append("product_unconfirmed")
        axes["product"] = "unconfirmed"
    elif p_targets - norm_pr:
        errs.append("product_mismatch")
        axes["product"] = "mismatch"
    else:
        axes["product"] = "match"

    # region — declaration must cover the query region AND the physical
    # row's own region (a national observation does not become provincial
    # evidence by declaration alone, and vice versa)
    regions = decl.get("regions")
    if not isinstance(regions, list) or not regions:
        errs.append("region_unconfirmed")
        axes["region"] = "unconfirmed"
    else:
        decl_regions = [_declared_region(d) for d in regions]
        targets = [region_info, _record_region(record)]
        if all(any(_region_covers(d, t) for d in decl_regions)
               for t in targets):
            axes["region"] = "match"
        else:
            errs.append("region_mismatch")
            axes["region"] = "mismatch"

    # period — targets = query year ∪ physical row basis.year
    periods = decl.get("periods")
    p_years = set()
    if year is not None:
        p_years.add(year)
    rec_year = (record.get("basis") or {}).get("year") \
        if isinstance(record.get("basis"), dict) else None
    if rec_year is not None:
        p_years.add(rec_year)
    if not isinstance(periods, list) or not periods or not p_years:
        errs.append("period_unconfirmed")
        axes["period"] = "unconfirmed"
    elif any(not _period_covers(periods, y) for y in p_years):
        errs.append("period_mismatch")
        axes["period"] = "mismatch"
    else:
        axes["period"] = "match"

    # unit — targets = query unit ∪ physical basis units ∪ source unit
    units = decl.get("units")
    norm_un = {_norm(u) for u in units} \
        if isinstance(units, list) else set()
    u_targets = set(_record_units(record))
    if unit is not None and _norm(unit):
        u_targets.add(_norm(unit))
    src_unit = _norm(src.get("unit")) if isinstance(src, dict) else ""
    if src_unit:
        u_targets.add(src_unit)
    if not norm_un or not u_targets:
        errs.append("unit_unconfirmed")
        axes["unit"] = "unconfirmed"
    elif u_targets - norm_un:
        errs.append("unit_mismatch")
        axes["unit"] = "mismatch"
    else:
        axes["unit"] = "match"

    # population — the declaration must correspond to the bound source
    # receipt's population, not merely be a non-empty string
    pop = decl.get("population")
    src_pop = src.get("population") if isinstance(src, dict) else None
    if not (isinstance(pop, str) and pop.strip()) \
            or not (isinstance(src_pop, str) and src_pop.strip()):
        errs.append("population_unconfirmed")
        axes["population"] = "unconfirmed"
    elif _norm(pop) != _norm(src_pop):
        errs.append("population_mismatch")
        axes["population"] = "mismatch"
    else:
        axes["population"] = "match"

    scopes = decl.get("use_scope")
    if use_scope is None or not _norm(use_scope):
        errs.append("use_scope_unspecified")
        axes["use_scope"] = "unspecified"
    elif not isinstance(scopes, list) or not scopes:
        errs.append("use_scope_unconfirmed")
        axes["use_scope"] = "unconfirmed"
    elif _norm(use_scope) not in {_norm(s) for s in scopes}:
        errs.append("use_scope_mismatch")
        axes["use_scope"] = "mismatch"
    else:
        axes["use_scope"] = "match"

    return errs, axes


# ---------------------------------------------------------------------------
# Candidate evaluation


def _evaluate(record, *, raw_status, pack, entries, catalog_accepted, q):
    """Evaluate one raw candidate through every gate — never short-
    circuits, so ``rejections`` reports ALL failed axes deterministically.
    ``eligible`` = every eligibility axis passed; ``approved`` = eligible
    AND the use-approval layer passed (unambiguous selection + accepted
    catalog + complete receipt).  Neither is the raw verdict."""
    rejections = []
    ak = record.get("audit_key")
    try:
        norm = _prov.normalize_audit_key(ak)
    except (ValueError, TypeError):
        norm = None
    if norm is None:
        rejections.append("audit_identity_absent")

    # --- selection: only a raw ``unique`` verdict is an unambiguous
    # physical-observation selection.  ambiguous sets (incl. a lone
    # candidate under a partial filter) and series rows without explicit
    # period selection can never approve at this layer — an explicit
    # audit_key selects one row but does not resolve a multi-observation
    # set it was not taken from, and never resolves a declared conflict.
    if raw_status != "unique":
        rejections.append("observation_selection_unresolved")
    if record.get("kind") in _lookup._SERIES_KINDS:
        rejections.append("series_period_unspecified")

    entry = entries.get(norm) if (entries and norm) else None

    # --- use approval: authenticated catalog + member entry + complete
    # observation receipt.  catalog_accepted is recomputed from manifest
    # content at call time (no cached approval flag).
    receipt_complete = _catalog_receipt_complete(entry)
    use_approval = {
        "catalog_accepted": bool(catalog_accepted),
        "entry_present": entry is not None,
        "receipt_complete": receipt_complete,
    }
    if not catalog_accepted:
        rejections.append("catalog_not_accepted")
    if entry is None:
        rejections.append("catalog_entry_absent")
    elif not receipt_complete:
        rejections.append("catalog_receipt_incomplete")
    approval_ok = all(use_approval.values()) \
        and raw_status == "unique" \
        and record.get("kind") not in _lookup._SERIES_KINDS

    # --- observation verification: the catalog row's status claim, read
    # regardless of catalog acceptance (distinct axis — a status claim is
    # not an approval)
    status = (entry or {}).get("catalog_status")
    if isinstance(status, _prov.CatalogStatus):
        status = status.value
    if status != _VERIFIED:
        rejections.append("observation_not_verified")

    # --- extended evidence axes (current packs carry no ``evidence``
    # block — every axis below fails closed for them)
    ev = (entry or {}).get("evidence")
    if not isinstance(ev, dict):
        ev = {}
    src = ev.get("source")
    src_errs, src_detail = _source_receipt_errors(src, pack or {})
    conf_errs, conflict = _conflict_errors(ev)
    rights_errs, rights = _rights_errors(ev)
    app_errs, app_axes = _applicability_errors(
        ev.get("applicability"), record=record,
        src=src if isinstance(src, dict) else None, **q)
    rejections += src_errs + conf_errs + rights_errs + app_errs

    axes = {
        "source": {
            "ok": not src_errs,
            "detail": src_detail or (src if isinstance(src, dict) else None),
        },
        "observation_status": status,
        "conflict": conflict,
        "rights": rights,
        "applicability": app_axes,
        "use_approval": use_approval,
    }
    eligible = not [r for r in rejections if r not in _APPROVAL_REASONS]
    return {
        "audit_key": _prov.audit_key_dict(norm) if norm is not None else ak,
        "pack_id": (pack or {}).get("pack_id")
                   or (ak.get("pack_id") if isinstance(ak, dict) else None),
        "record": record,
        "observation_status": status,
        "axes": axes,
        "rejections": rejections,
        "eligible": eligible,
        "approved": eligible and approval_ok,
    }


def _empty_result(status, query, reason):
    return {
        "schema": SCHEMA,
        "status": status,
        "reason": reason,
        "major_id": query.get("major_id"),
        "query": query,
        "observation": {"status": None, "rank_used": None, "pack_id": None,
                        "reason": reason, "web": None,
                        "candidate_count": 0},
        "candidates": [],
        "approved_candidates": [],
        "counts": {"candidates": 0, "eligible": 0, "approved": 0},
    }


_UNAPPROVED_REASONS = {
    "ambiguous": "observation selection unresolved — multiple distinct "
                 "physical observations; explicit audit_key or complete "
                 "filter required",
    "series": "series observation — explicit period selection required "
              "before any scalar use",
}


def lookup_approved_candidates(crop, region, major, *, kind=None, year=None,
                               form=None, unit=None, use_scope=None,
                               audit_key=None):
    """Approval-candidate lookup for new major-aware writing.

    ``major`` is REQUIRED — a writing path without a resolvable major_id
    withholds major-dependent evidence entirely (DECISION §2: 전공을 알 수
    없으면 전공 의존 작성·계산을 보류한다).  ``use_scope`` declares what
    the candidate would be used for (시장 맥락/기술 근거/계획 입력 등) —
    omitting it can never approve.

    The candidate set, order, and audit keys come verbatim from
    ``lookup_rda_data`` (raw/legacy contract, unchanged — pinned pack
    integrity failures still raise).  ``audit_key`` selects ONE candidate
    inside the filtered set exactly as the raw contract does; it is a
    selector, not a bypass — every gate still runs, and it cannot resolve
    an ambiguous set it was not taken from or a declared conflict.

    Top-level ``approved`` requires a raw ``unique`` verdict — ambiguity
    is preserved upward: no approval on partial filters, multi-observation
    sets, or series rows without period selection.  Returns ``status`` in
    ``approved | unapproved | not_found | major_unresolved`` plus per-
    candidate ``axes``/``rejections``/``eligible``/``approved`` — raw
    observation, eligibility, and use approval stay distinct results.
    """
    crop_norm = _norm(crop)
    region_norm = _norm(region)
    query = {
        "crop": crop_norm, "region": region_norm, "major": major,
        "kind": kind, "year": year, "form": form,
        "unit": unit, "use_scope": use_scope,
        "audit_key_given": audit_key is not None,
    }
    if major is None or not _norm(major):
        return _empty_result(
            "major_unresolved", query,
            "전공 미지정 — 전공 인식 작성 경로는 명시적 major가 필요하다")
    major_id, major_error = _lookup._resolve_major(major)
    query["major_id"] = major_id
    if major_error or not major_id:
        return _empty_result("major_unresolved", query,
                             major_error or "전공 식별 실패")

    raw = _lookup.lookup_rda_data(
        crop, region, major=major, kind=kind, year=year, form=form,
        audit_key=audit_key)
    records = raw.get("records") or []
    raw_status = raw.get("status")

    catalog = _lookup._catalog()
    packs_by_id = {p.get("pack_id"): p for p in catalog.get("packs", [])}
    q = {
        "major_id": major_id,
        "crop_key": _lookup._crop_key(crop_norm, major_id),
        "region_info": _lookup._resolve_region(region_norm),
        "year": year, "form": form, "unit": unit,
        "use_scope": use_scope,
    }

    catalogs = {}
    candidates = []
    for rec in records:
        ak = rec.get("audit_key") or {}
        pid = ak.get("pack_id") if isinstance(ak, dict) else None
        pack = packs_by_id.get(pid)
        if pid not in catalogs:
            catalogs[pid] = _catalog_entries(pack)
        entries, accepted = catalogs[pid]
        candidates.append(_evaluate(
            rec, raw_status=raw_status, pack=pack, entries=entries,
            catalog_accepted=accepted, q=q))

    approved = [c for c in candidates if c["approved"]]
    if not candidates:
        status, reason = "not_found", raw.get("reason")
    elif approved:
        status, reason = "approved", None
    else:
        status = "unapproved"
        reason = _UNAPPROVED_REASONS.get(
            raw_status,
            "no candidate passed every approval axis — raw verdict is "
            "not use permission")
    return {
        "schema": SCHEMA,
        "status": status,
        "reason": reason,
        "major_id": major_id,
        "query": query,
        "observation": {
            "status": raw_status,
            "rank_used": raw.get("rank_used"),
            "pack_id": raw.get("pack_id"),
            "reason": raw.get("reason"),
            "web": raw.get("web"),
            "candidate_count": len(records),
        },
        "candidates": candidates,
        "approved_candidates": approved,
        "counts": {
            "candidates": len(candidates),
            "eligible": sum(1 for c in candidates if c["eligible"]),
            "approved": len(approved),
        },
    }
