"""lookup_rda_data 결과를 economic research 후보값으로 변환하고, 대조 기록을
reviews/rda-check.json에 저장한다. user_answer는 절대 덮어쓰지 않는다.
"""

import json

import gg_core


def propose(lookup, field, *, current_status=None):
    if current_status is not None and current_status != "unresolved":
        return {
            "status": "keep_existing",
            "value": None,
            "source_ref": None,
            "reason": "이미 %s 상태 — research로 덮지 않는다." % current_status,
        }
    if not isinstance(lookup, dict) or lookup.get("status") != "hit":
        return {
            "status": "keep_unresolved",
            "value": None,
            "source_ref": None,
            "reason": "조회 결과가 hit이 아니어서 제안할 값이 없다.",
        }
    records = lookup.get("records") or []
    record = None
    for rec in records:
        metrics = rec.get("metrics") or {}
        if field in metrics and metrics.get(field) is not None:
            record = rec
            break
    if record is None:
        return {
            "status": "keep_unresolved",
            "value": None,
            "source_ref": None,
            "reason": "레코드에 '%s' 값이 없다." % field,
        }
    value = record["metrics"][field]
    source_ref = "%s#%s" % (record.get("pack_id"), record.get("record_id"))
    return {"status": "research", "value": str(value), "source_ref": source_ref, "reason": None}


def build_rda_check(major_id, crop, comparisons):
    return {"major_id": major_id, "crop": crop, "comparisons": list(comparisons)}


def write_rda_check(root, data):
    path = gg_core.local(root, "reviews/rda-check.json")
    gg_core.atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return path
