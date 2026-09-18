"""RDA/MAFRA 통계 원자료 조회. 승격된 references/benchmark-packs JSON만 읽으며
모델 호출이나 네트워크 접근은 하지 않는다. rank 1(공통) -> rank 2(전공) 순서로
탐색하고, 부재 시 rank 3(웹)은 allow_web=True일 때만 스텁으로 시도한다.
"""

import json
import unicodedata
from pathlib import Path

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

_SERIES_KINDS = {"official_market_price", "wholesale_price_series"}

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
    return _load_records(pack["relpath"])


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


def _filter_records(records, *, crop_key, kind, year, form):
    matched = []
    for rec in records:
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


def _disambiguate(records, *, form, year):
    if form is not None and year is not None:
        return False, None
    by_kind = {}
    for rec in records:
        by_kind.setdefault(rec.get("kind"), []).append(rec)
    for kind_key, group in by_kind.items():
        if len(group) <= 1:
            continue
        if form is None:
            forms = {_normalize((r.get("crop") or {}).get("form")) for r in group}
            if len(forms) > 1:
                return True, "동점 후보 다수 — form 등으로 좁혀야 한다"
        if year is None and kind_key not in _SERIES_KINDS:
            years = {(r.get("basis") or {}).get("year") for r in group}
            if len(years) > 1:
                return True, "동점 후보 다수 — year 등으로 좁혀야 한다"
    return False, None


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


def lookup_rda_data(crop, region, *, major=None, kind=None, year=None, form=None, allow_web=False):
    crop_norm = _normalize(crop)
    region_norm = _normalize(region)
    if not crop_norm or not region_norm:
        return {
            "status": "blocked",
            "rank_used": None,
            "pack_id": None,
            "records": [],
            "reason": "crop과 region은 비어 있을 수 없다",
            "web": None,
        }
    major_id = None
    if major is not None:
        major_id, major_error = _resolve_major(major)
        if major_error:
            return {
                "status": "blocked",
                "rank_used": None,
                "pack_id": None,
                "records": [],
                "reason": major_error,
                "web": None,
            }
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
        return {
            "status": "miss",
            "rank_used": None,
            "pack_id": None,
            "records": [],
            "reason": reason,
            "web": web,
        }
    ambiguous, ambiguous_reason = _disambiguate(combined, form=form, year=year)
    if ambiguous:
        return {
            "status": "ambiguous",
            "rank_used": None,
            "pack_id": None,
            "records": combined,
            "reason": ambiguous_reason,
            "web": None,
        }
    rank_used = 1 if common_records else 2
    all_pack_ids = common_pack_ids + major_pack_ids
    pack_id = all_pack_ids[0] if all_pack_ids else None
    return {
        "status": "hit",
        "rank_used": rank_used,
        "pack_id": pack_id,
        "records": combined,
        "reason": None,
        "web": None,
    }
