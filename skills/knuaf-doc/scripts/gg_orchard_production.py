#!/usr/bin/env python3
"""Read-only fruit-tree production and batch inventory reconciliation."""

import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from fractions import Fraction
from pathlib import Path

import gg_core
import gg_fruit_plan as plan
import gg_major_contract as mc

FRUIT = "fruit_trees"
NUMBER_UNITS = {"area_m2": "㎡", "bearing_area_m2": "㎡",
                "tree_count": "주", "bearing_trees": "주",
                "yield_kg_per_tree": "kg/주", "yield_kg_per_10a": "kg/10a",
                "quantity_kg": "kg"}
TEXT_FIELDS = {"crop_species", "cultivar", "grade", "channel",
               "cultivation_form", "heating"}
YEARS = {"business_start_year", "plan_end_year", "planting_year",
         "active_from_year", "active_to_year", "harvest_year",
         "opening_year", "year"}
ENUMS = {"yield_basis": {"per_tree", "per_area"},
         "yield_area_basis": {"bearing_area", "total_area"},
         "origin": {"harvest", "opening", "regrade", "mix"},
         "kind": {"sale", "loss", "own_use", "process", "experience",
                  "regrade", "mix_in"}}
OUT_KINDS = {"sale", "loss", "own_use", "process", "experience"}
ID = re.compile(r"^[a-z0-9_]{1,32}$")
SPACING = re.compile(r"^([^x×]+)[x×]([^x×]+)$")


def number(value):
    if not isinstance(value, str):
        raise ValueError("decimal string required")
    try:
        d = Decimal(value)
        if not d.is_finite():
            raise ValueError("nonfinite")
        return Fraction(d)
    except InvalidOperation as error:
        raise ValueError("invalid decimal") from error


def wrapped(value):
    if value.denominator == 1:
        return {"exact": str(value.numerator)}
    den = value.denominator
    while den % 2 == 0:
        den //= 2
    while den % 5 == 0:
        den //= 5
    if den == 1:
        d = value.denominator
        twos = fives = 0
        while d % 2 == 0:
            twos += 1
            d //= 2
        while d % 5 == 0:
            fives += 1
            d //= 5
        places = max(twos, fives)
        digits = str(abs(value.numerator) * (10 ** places // value.denominator))
        digits = digits.zfill(places + 1)
        exact = digits[:-places] + "." + digits[-places:]
        return {"exact": ("-" if value < 0 else "") + exact}
    with __import__("decimal").localcontext() as ctx:
        ctx.prec = max(50, len(str(abs(value.numerator))) + 10)
        shown = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return {"exact": "%d/%d" % (value.numerator, value.denominator),
            "display": format(shown, ".2f"), "precision": "0.01 half-even"}


def answer(fields, name, year=None):
    item = fields.get(name, {})
    if year is not None:
        item = item.get("by_period", {}).get(str(year), {})
    return item


def read(fields, name, *, year=None, issues=None, owner=None):
    a = answer(fields, name, year)
    state = a.get("answer_state", "no_fact")
    if state != "provided":
        return None, state, a.get("fact_ids", [])
    value = a.get("value")
    try:
        if name in NUMBER_UNITS:
            if a.get("unit") != NUMBER_UNITS[name]:
                raise ValueError("unit_mismatch")
            value = number(value)
            if value < 0 or name in {"tree_count", "bearing_trees"} and value.denominator != 1:
                raise ValueError("invalid_value")
        elif name in YEARS:
            if isinstance(value, bool) or not plan.valid_year(str(value)):
                raise ValueError("invalid_value")
            value = int(value)
        elif name in ENUMS and value not in ENUMS[name]:
            raise ValueError("invalid_value")
        elif name in TEXT_FIELDS and not isinstance(value, str):
            raise ValueError("invalid_value")
        elif name == "spacing_m":
            m = SPACING.fullmatch(value) if isinstance(value, str) else None
            if not m:
                raise ValueError("invalid_value")
            value = (number(m.group(1)), number(m.group(2)))
            if min(value) <= 0:
                raise ValueError("invalid_value")
    except (ValueError, TypeError) as error:
        kind = str(error) if str(error) == "unit_mismatch" else "invalid_value"
        if issues is not None:
            issues.append({"kind": kind, "owner": owner, "field": name,
                           **({"year": str(year)} if year is not None else {})})
        return None, kind, a.get("fact_ids", [])
    return value, "provided", a.get("fact_ids", [])


def required(fields, name, issues, owner, year=None):
    value, state, ids = read(fields, name, year=year, issues=issues, owner=owner)
    return value, state, ids


def ref(value, declared, issues, owner, field):
    if value is None:
        return None
    if not isinstance(value, str) or not ID.fullmatch(value):
        issues.append({"kind": "invalid_ref_value", "owner": owner, "field": field})
        return None
    if value not in declared:
        issues.append({"kind": "dangling_ref", "owner": owner, "field": field,
                       "ref": value})
        return None
    return value


def _base(project, module, status="presented"):
    return {"status": status, "major_id": FRUIT,
            "module_version": module.module_version,
            "project_revision": project["revision"], "derived": True,
            "persisted": False, "completeness": "partial", "issue_count": 0,
            "plan_window": {}, "cohorts": {}, "batches": {},
            "allocations": [], "violations": [], "groups": [],
            "warnings": [], "not_computable": [], "inputs": {}}


def _window(by_field, issues):
    fields = {name: plan._answer(by_field, "fruit_trees." + name) or {}
              for name in ("business_start_year", "plan_end_year")}
    start, ss, _ = read(fields, "business_start_year", issues=issues, owner="farm")
    end, es, _ = read(fields, "plan_end_year", issues=issues, owner="farm")
    if start is None:
        return None, {}
    end = end if end is not None else start + 9
    if end < start:
        issues.append({"kind": "invalid_value", "owner": "farm",
                       "field": "plan_end_year"})
        return None, {}
    return (start, end), {"start_year": str(start), "end_year": str(end)}


def _year_set(iid, fields, project, batches, window, warnings):
    explicit = set()
    for name in plan.YEARLY_FIELDS:
        for fact in project["facts"].values():
            if fact.get("field_id") == "fruit_trees.cohort.%s.%s" % (iid, name):
                if plan.valid_year(fact.get("period")):
                    explicit.add(int(fact["period"]))
    for b in batches.values():
        bf = b["fields"]
        if answer(bf, "origin").get("value") == "harvest" \
                and answer(bf, "cohort_ref").get("value") == iid:
            y = answer(bf, "harvest_year").get("value")
            if plan.valid_year(str(y)):
                explicit.add(int(y))
    if window:
        for y in sorted(explicit):
            if y < window[0] or y > window[1]:
                warnings.append({"kind": "outside_plan_window", "cohort": iid,
                                 "year": str(y)})
        from_answer = answer(fields, "active_from_year")
        start = from_answer.get("value")
        if from_answer.get("answer_state", "no_fact") in ("no_fact", "not_provided"):
            start = answer(fields, "planting_year").get("value")
        end = answer(fields, "active_to_year").get("value")
        if _active_reason(fields) is None and plan.valid_year(str(start)):
            lo = max(int(start), window[0])
            hi = min(int(end), window[1]) if plan.valid_year(str(end)) else window[1]
            if lo <= hi:
                explicit.update(range(lo, hi + 1))
    return list(range(min(explicit), max(explicit) + 1)) if explicit else []


def _active_reason(fields):
    state = answer(fields, "active_from_year").get("answer_state", "no_fact")
    if state in ("no_fact", "not_provided"):
        state = answer(fields, "planting_year").get("answer_state", "no_fact")
    if state not in ("provided", "no_fact", "not_provided"):
        return "active_period_" + state
    end_state = answer(fields, "active_to_year").get("answer_state", "no_fact")
    if end_state not in ("provided", "no_fact", "not_provided"):
        return "active_period_" + end_state
    return None


def _active(fields, year, issues, owner):
    if _active_reason(fields) is not None:
        return None
    start, state, _ = read(fields, "active_from_year", issues=issues, owner=owner)
    if start is None and state in ("no_fact", "not_provided"):
        start, state, _ = read(fields, "planting_year", issues=issues, owner=owner)
    end, es, _ = read(fields, "active_to_year", issues=issues, owner=owner)
    if start is None:
        return None if state in ("no_fact", "not_provided") else False
    if end is not None and end < start:
        issues.append({"kind": "invalid_value", "owner": owner,
                       "field": "active_to_year"})
        return False
    return start <= year and (end is None or year <= end)


def _production(iid, item, instances, project, window, years, issues,
                violations, not_computable, blocked, known):
    fields = item["fields"]
    out = {"years": {}}
    block_raw, block_state, _ = read(fields, "block_ref", issues=issues, owner=iid)
    block = ref(block_raw, instances["block"], issues, iid, "block_ref")
    area, as_, _ = read(fields, "area_m2", issues=issues, owner=iid)
    trees, ts, _ = read(fields, "tree_count", issues=issues, owner=iid)
    basis, bs, _ = read(fields, "yield_basis", issues=issues, owner=iid)
    block_fields = instances["block"][block]["fields"] if block else {}
    block_area, bas, _ = read(block_fields, "area_m2", issues=issues,
                              owner="block:" + block if block else iid)
    if not years:
        not_computable.append({"cohort": iid, "state": "not_computable",
                               "missing": ["year_range"]})
    for year in years:
        key = str(year)
        if ("cohort", iid) in blocked or ("block", block) in blocked:
            out["years"][key] = {"state": "held", "inputs": [],
                                  "reason": "instance_issue"}
            continue
        names = (("bearing_trees", "yield_kg_per_tree") if basis == "per_tree"
                 else ("bearing_area_m2", "yield_kg_per_10a"))
        values = [required(fields, n, issues, iid, year) for n in names]
        ids = [fid for _, _, fids in values for fid in fids]
        rec = {"state": "not_computable", "inputs": ids}
        if basis:
            rec["basis"] = basis
        missing = [n for n, (_, state, _) in zip(names, values)
                   if state != "provided"]
        if basis is None:
            missing.insert(0, "yield_basis")
        if missing:
            rec["missing"] = missing
            rec["reason"] = next((v[1] for v in values if v[1] not in
                                  ("provided", "no_fact", "not_provided")),
                                 bs if bs not in ("provided", "no_fact") else "missing_input")
            if rec["reason"] in ("invalid_value", "unit_mismatch"):
                rec["state"] = "held"
            else:
                not_computable.append({"cohort": iid, "year": key,
                                       "state": "not_computable", "missing": missing})
            out["years"][key] = rec
            continue
        if basis == "per_area":
            area_basis, abs_, _ = read(fields, "yield_area_basis",
                                       issues=issues, owner=iid)
            if area_basis != "bearing_area":
                rec.update(state="held", reason="area_basis_mismatch" if
                           area_basis == "total_area" else abs_)
                out["years"][key] = rec
                continue
        q = values[0][0] * values[1][0]
        if basis == "per_area":
            q /= 1000
        active = _active(fields, year, issues, iid)
        active_states = [read(fields, n, issues=issues, owner=iid)[1]
                         for n in ("active_from_year", "active_to_year")]
        if block_raw is not None and block is None or block_state == "duplicate_answer":
            rec.update(state="held", reason="invalid_block_ref")
        elif any(s in ("invalid_value", "unit_mismatch") for s in active_states):
            rec.update(state="held", reason="invalid_active_period")
        elif as_ in ("invalid_value", "unit_mismatch") or bas in (
                "invalid_value", "unit_mismatch") or ts in (
                "invalid_value", "unit_mismatch"):
            rec.update(state="held", reason="invalid_limit")
        elif active is False:
            issues.append({"kind": "invalid_value", "owner": iid,
                           "field": "outside_active_period", "year": key})
            rec.update(state="held", reason="outside_active_period")
        elif basis == "per_tree" and trees is not None and values[0][0] > trees:
            violations.append({"kind": "bearing_trees_exceeded", "cohort": iid,
                               "year": key})
            rec.update(state="held", reason="bearing_trees_exceeded")
        elif basis == "per_area" and area is not None and values[0][0] > area:
            violations.append({"kind": "bearing_area_exceeded", "cohort": iid,
                               "year": key})
            rec.update(state="held", reason="bearing_area_exceeded")
        elif active is None or area is None or block_area is None or block is None \
                or basis == "per_tree" and trees is None:
            rec.update(state="unverifiable", unverified=wrapped(q),
                       reason=(_active_reason(fields) or "active_period_unknown")
                       if active is None else "limit_unknown")
        else:
            rec.update(state="computed", production=wrapped(q))
            known[(iid, key)] = q
        out["years"][key] = rec
    return out


def _limits_and_theory(instances, cohorts, issues, violations, warnings, blocked):
    for bid, block in instances["block"].items():
        if ("block", bid) in blocked:
            continue
        bf = block["fields"]
        limit, _, _ = read(bf, "area_m2", issues=issues, owner="block:" + bid)
        members = {cid: c for cid, c in instances["cohort"].items()
                   if answer(c["fields"], "block_ref").get("value") == bid}
        all_years = sorted({int(y) for cid in members for y in cohorts[cid]["years"]})
        for year in all_years:
            occupied, unknown = Fraction(0), limit is None
            active_ids = []
            for cid, member in members.items():
                active = _active(member["fields"], year, issues, cid)
                if active is None:
                    unknown = True
                if active is True:
                    active_ids.append(cid)
                    area, _, _ = read(member["fields"], "area_m2",
                                      issues=issues, owner=cid)
                    if area is None:
                        unknown = True
                    else:
                        occupied += area
            exceeded = not unknown and occupied > limit
            if exceeded:
                violations.append({"kind": "block_area_exceeded", "block": bid,
                                   "year": str(year)})
            for cid in active_ids:
                rec = cohorts[cid]["years"].get(str(year))
                if not rec or rec["state"] not in ("computed", "unverifiable"):
                    continue
                if exceeded:
                    rec.pop("production", None)
                    rec.pop("unverified", None)
                    rec.update(state="held", reason="block_area_exceeded")
                elif unknown and rec.get("basis") == "per_area":
                    q = rec.pop("production", rec.pop("unverified", None))
                    if q is not None:
                        rec.update(state="unverifiable", unverified=q,
                                   reason="block_limit_unknown")
        if len(members) != 1 or limit is None:
            continue
        cid, member = next(iter(members.items()))
        cf = member["fields"]
        spacing, _, _ = read(cf, "spacing_m", issues=issues, owner=cid)
        if spacing is None:
            continue
        theoretical = limit / (spacing[0] * spacing[1])
        cohorts[cid]["theoretical_trees"] = wrapped(theoretical)
        declared, _, _ = read(cf, "tree_count", issues=issues, owner=cid)
        if declared is not None and declared != theoretical:
            warnings.append({"kind": "theoretical_vs_declared", "cohort": cid})
            for name in ("tree_count_basis", "layout_note"):
                if answer(cf, name).get("answer_state") != "provided":
                    warnings.append({"kind": "evidence_required", "cohort": cid,
                                     "field": name})


def _batches(instances, issues):
    batches, meta = {}, {}
    required = {
        "harvest": ("cohort_ref", "harvest_year", "grade", "quantity_kg"),
        "opening": ("crop_species", "cultivar", "harvest_year",
                    "opening_year", "grade", "quantity_kg"),
        "regrade": ("grade",), "mix": (),
    }
    for bid, item in instances["batch"].items():
        f = item["fields"]
        origin, os_, _ = read(f, "origin", issues=issues, owner=bid)
        year, ys, _ = read(f, "harvest_year", issues=issues, owner=bid)
        opening, ops, _ = read(f, "opening_year", issues=issues, owner=bid)
        qty, qs, _ = read(f, "quantity_kg", issues=issues, owner=bid)
        crop, cs, _ = read(f, "crop_species", issues=issues, owner=bid)
        cultivar, cvs, _ = read(f, "cultivar", issues=issues, owner=bid)
        grade, gs, _ = read(f, "grade", issues=issues, owner=bid)
        missing = [name for name in required.get(origin, ("origin",))
                   if answer(f, name).get("answer_state") != "provided"]
        if missing:
            issues.append({"kind": "missing_attribute", "owner": bid,
                           "fields": missing})
        if origin in ("regrade", "mix") and answer(f, "quantity_kg").get(
                "answer_state") == "provided":
            issues.append({"kind": "invalid_value", "owner": bid,
                           "field": "quantity_kg", "reason": "arrival_only"})
        cohort = ref(answer(f, "cohort_ref").get("value"), instances["cohort"],
                     issues, bid, "cohort_ref") if origin == "harvest" else None
        if origin == "opening" and year is not None and opening is not None \
                and year > opening:
            issues.append({"kind": "invalid_value", "owner": bid,
                           "field": "opening_year"})
            opening = None
        attrs = None
        if origin == "harvest" and cohort:
            combo = instances["cohort"][cohort]["combination"]
            attrs = (combo[0], combo[1], year, "harvest")
        elif origin == "opening":
            attrs = (crop, cultivar, year, "opening")
        state = "ready" if origin in ("regrade", "mix") or (
            origin == "harvest" and cohort and year is not None and qty is not None) or (
            origin == "opening" and year is not None and opening is not None
            and qty is not None) else "held"
        if missing or any(s == "invalid_value" for s in (gs, cs, cvs)):
            state = "held"
        if origin in ("regrade", "mix") and answer(f, "quantity_kg").get(
                "answer_state") == "provided":
            state = "held"
        batches[bid] = {"origin": origin or "unknown", "state": state,
                        "balances": {}}
        if origin == "mix":
            batches[bid]["mixed"] = True
        if cohort:
            batches[bid]["cohort_ref"] = cohort
        if year is not None:
            batches[bid]["harvest_year"] = str(year)
        if opening is not None and origin == "opening":
            batches[bid]["opening_year"] = str(opening)
        if qty is not None and origin in ("harvest", "opening"):
            batches[bid]["quantity"] = wrapped(qty)
        if isinstance(grade, str):
            batches[bid]["grade"] = grade
        meta[bid] = {"origin": origin, "year": year, "opening": opening,
                     "qty": qty, "cohort": cohort, "attrs": attrs,
                     "grade": grade, "state": state}
    return batches, meta


def _resolve_regrades(instances, meta, batches, moves):
    """Inherit upstream attributes, then reject every conflicting arrival."""
    edges = [m for m in moves if m["state"] == "ready" and m["kind"] == "regrade"]
    resolved, visiting = set(), set()

    def resolve(dst):
        if dst in resolved or dst in visiting:
            return
        visiting.add(dst)
        incoming = [m for m in edges if m["dst"] == dst and m["state"] == "ready"]
        tf = instances["batch"][dst]["fields"]
        expected_cohort = answer(tf, "cohort_ref").get("value")
        expected_year = answer(tf, "harvest_year").get("value")
        candidates = []
        for m in incoming:
            src = m["src"]
            if meta[src]["origin"] == "regrade":
                resolve(src)
            attr = meta[src]["attrs"]
            if attr is None or any(v is None for v in attr[:3]):
                m.update(state="held", reason="attribute_unknown")
            elif meta[dst]["grade"] is None or meta[dst]["grade"] == meta[src]["grade"]:
                m.update(state="invalid", reason="grade_mismatch")
            else:
                candidates.append((m, attr, meta[src]["cohort"]))
        if len({(attr, cohort) for _, attr, cohort in candidates}) > 1:
            for m, _, _ in candidates:
                m.update(state="invalid", reason="attribute_mismatch")
            candidates = []
        valid = []
        for m, attr, cohort in candidates:
            if (expected_cohort is not None and cohort != expected_cohort or
                    expected_year is not None and str(attr[2]) != str(expected_year)):
                m.update(state="invalid", reason="attribute_mismatch")
            else:
                valid.append((m, attr, cohort))
        if valid:
            src = valid[0][0]["src"]
            attr = valid[0][1]
            meta[dst]["attrs"] = attr
            meta[dst]["cohort"] = meta[src]["cohort"]
            meta[dst]["year"] = attr[2]
            batches[dst]["harvest_year"] = str(attr[2])
            if meta[dst]["cohort"]:
                batches[dst]["cohort_ref"] = meta[dst]["cohort"]
        visiting.remove(dst)
        resolved.add(dst)

    for dst in sorted({m["dst"] for m in edges}):
        resolve(dst)


def _temporal_filter(moves, meta, candidates, reasons):
    """Remove impossible spends until no remaining arrival date can change."""
    added = False
    while True:
        changed = False
        for m in moves:
            if m["id"] not in candidates or meta[m["src"]]["origin"] not in (
                    "regrade", "mix"):
                continue
            arrivals = [a["year"] for a in moves if a["state"] == "ready"
                        and a["dst"] == m["src"] and a["year"] is not None]
            reason = ("no_arrival" if not arrivals else
                      "before_arrival" if m["year"] < min(arrivals) else None)
            if reason:
                if m["id"] not in reasons:
                    added = changed = True
                reasons[m["id"]] = reason
                m.update(state="invalid", reason=reason)
        if not changed:
            return added


def _moves(instances, meta, batches, issues, violations, warnings, blocked):
    moves = []
    for mid, item in instances["move"].items():
        f = item["fields"]
        kind, ks, _ = read(f, "kind", issues=issues, owner=mid)
        year, ys, _ = read(f, "year", issues=issues, owner=mid)
        qty, qs, _ = read(f, "quantity_kg", issues=issues, owner=mid)
        _, channel_state, _ = read(f, "channel", issues=issues, owner=mid)
        src = ref(answer(f, "batch_ref").get("value"), batches, issues,
                  mid, "batch_ref")
        dst = ref(answer(f, "target_batch").get("value"), batches, issues,
                  mid, "target_batch") if kind in ("regrade", "mix_in") else None
        m = {"id": mid, "kind": kind, "year": year, "qty": qty,
             "src": src, "dst": dst, "state": "ready"}
        if (("move", mid) in blocked or ("batch", src) in blocked or
                ("batch", dst) in blocked or
                (src is not None and meta[src]["state"] != "ready") or
                (dst is not None and meta[dst]["state"] != "ready")):
            m.update(state="held", reason="instance_issue")
        elif kind is None or year is None or src is None or \
                kind in ("regrade", "mix_in") and dst is None:
            m["state"] = "held"
            m["reason"] = "missing_input"
        elif qs in ("invalid_value", "unit_mismatch"):
            m.update(state="invalid", reason=qs)
        elif channel_state == "invalid_value":
            m.update(state="held", reason="invalid_value")
        elif kind in ("regrade", "mix_in") and src == dst:
            m.update(state="invalid", reason="self")
        elif kind == "regrade" and meta[src]["origin"] == "mix" or \
                kind == "mix_in" and meta[src]["origin"] == "mix":
            m.update(state="held", reason="unsupported_move")
        elif kind == "regrade" and meta[dst]["origin"] != "regrade" or \
                kind == "mix_in" and meta[dst]["origin"] != "mix":
            m.update(state="invalid", reason="invalid_target")
        elif src and meta[src]["origin"] in ("harvest", "opening"):
            start = meta[src]["opening"] if meta[src]["origin"] == "opening" \
                else meta[src]["year"]
            if start is not None and year < start:
                m.update(state="invalid", reason="before_opening" if
                         meta[src]["origin"] == "opening" else "before_harvest")
        if m["state"] == "ready" and kind in ("process", "experience"):
            warnings.append({"kind": "unit_conversion_pending", "move": mid})
        moves.append(m)
    # A regrade graph is acyclic. Mark every edge that belongs to a cycle.
    edges = [m for m in moves if m["state"] == "ready" and m["kind"] == "regrade"]
    graph = {}
    for m in edges:
        graph.setdefault(m["src"], []).append(m)
    for edge in edges:
        stack = [edge["dst"]]
        seen = set()
        while stack:
            node = stack.pop()
            if node == edge["src"]:
                edge.update(state="invalid", reason="cycle")
                break
            if node not in seen:
                seen.add(node)
                stack.extend(e["dst"] for e in graph.get(node, []))
    base_ready = {m["id"] for m in moves if m["state"] == "ready"}
    initial_regrade = {
        bid: (b["year"], batches[bid].get("harvest_year"))
        for bid, b in meta.items() if b["origin"] == "regrade"}
    time_reasons = {}
    _temporal_filter(moves, meta, base_ready, time_reasons)
    # Each pass uses the previous pass's temporal exclusions to resolve
    # attributes, then derives temporal exclusions afresh from those results.
    # A temporary no_arrival must not survive a restored valid arrival.
    limit = max(8, 4 * len(base_ready) + 4)
    unstable = set()
    for _ in range(limit):
        for m in moves:
            if m["id"] in base_ready:
                m["state"] = "ready"
                m.pop("reason", None)
                if m["id"] in time_reasons:
                    m.update(state="invalid", reason=time_reasons[m["id"]])
        for bid, (year, output_year) in initial_regrade.items():
            meta[bid].update(attrs=None, cohort=None, year=year)
            batches[bid].pop("cohort_ref", None)
            if output_year is None:
                batches[bid].pop("harvest_year", None)
            else:
                batches[bid]["harvest_year"] = output_year
        _resolve_regrades(instances, meta, batches, moves)
        next_time_reasons = {}
        _temporal_filter(moves, meta, base_ready, next_time_reasons)
        if next_time_reasons == time_reasons:
            break
        unstable.update(mid for mid in time_reasons.keys() | next_time_reasons.keys()
                        if time_reasons.get(mid) != next_time_reasons.get(mid))
        time_reasons = next_time_reasons
    else:
        # Follow only derived batches connected to an unstable temporal move.
        # An original batch may feed independent paths, so it is not a bridge.
        related = set(unstable)
        pending = [bid for m in moves if m["id"] in unstable
                   for bid in (m["src"], m["dst"])
                   if bid is not None and meta[bid]["origin"] in ("regrade", "mix")]
        affected_batches = set()
        while pending:
            bid = pending.pop()
            if bid in affected_batches:
                continue
            affected_batches.add(bid)
            for m in moves:
                if m["id"] not in base_ready or bid not in (m["src"], m["dst"]):
                    continue
                related.add(m["id"])
                for neighbor in (m["src"], m["dst"]):
                    if neighbor is not None and meta[neighbor]["origin"] in (
                            "regrade", "mix") and neighbor not in affected_batches:
                        pending.append(neighbor)
        for m in moves:
            if m["id"] in related:
                m.update(state="held", reason="unresolved_dependency")
        for bid, (year, output_year) in initial_regrade.items():
            if bid not in affected_batches:
                continue
            meta[bid].update(attrs=None, cohort=None, year=year)
            batches[bid].pop("cohort_ref", None)
            if output_year is None:
                batches[bid].pop("harvest_year", None)
            else:
                batches[bid]["harvest_year"] = output_year
    for m in moves:
        if m["state"] == "invalid":
            violations.append({"kind": "invalid_move", "move": m["id"],
                               "reason": m["reason"]})
        elif m["state"] == "held":
            issues.append({"kind": "held_move", "move": m["id"],
                           "reason": m["reason"]})
    return moves


def _allocations(cohorts, meta, violations, known):
    result = []
    keys = {(cid, y) for cid, c in cohorts.items() for y in c["years"]}
    keys |= {(b["cohort"], str(b["year"])) for b in meta.values()
             if b["origin"] == "harvest" and b["cohort"] and b["year"] is not None}
    for cid, year in sorted(keys):
        rec = cohorts[cid]["years"].get(year, {})
        bs = [b for b in meta.values() if b["origin"] == "harvest"
              and b["cohort"] == cid and str(b["year"]) == year]
        allocation = {"cohort": cid, "year": year, "state": "unverifiable"}
        if any(b["state"] != "ready" for b in bs):
            allocation["reason"] = "batch_held"
        elif rec.get("state") == "computed" and all(b["qty"] is not None for b in bs):
            q = known[(cid, year)]
            used = sum((b["qty"] for b in bs), Fraction(0))
            allocation["state"] = "computed"
            allocation["allocation_state"] = ("overallocated" if used > q else
                                              "unallocated" if used < q else "balanced")
            allocation["production"] = wrapped(q)
            allocation["allocated"] = wrapped(used)
            if used < q:
                allocation["remainder"] = wrapped(q - used)
            if used > q:
                violations.append({"kind": "overallocated", "cohort": cid,
                                   "year": year})
        result.append(allocation)
    return result


def _inventory(batches, meta, moves, cohorts, violations, inputs):
    years = [b["year"] for b in meta.values() if b["year"] is not None]
    years += [b["opening"] for b in meta.values() if b["opening"] is not None]
    years += [m["year"] for m in moves if m["year"] is not None]
    years += [int(y) for c in cohorts.values() for y in c["years"]]
    if not years:
        return
    end = max(years)
    applied_out, applied_in = set(), set()
    for bid, b in meta.items():
        arrivals = [m for m in moves if m["dst"] == bid and m["state"] == "ready"
                    and m["year"] is not None]
        if b["origin"] == "harvest":
            start = b["year"]
        elif b["origin"] == "opening":
            start = b["opening"]
        else:
            start = min((m["year"] for m in arrivals), default=None)
        if start is None:
            continue
        if b["state"] != "ready":
            batches[bid]["balances"] = {
                str(year): {"state": "indeterminate", "cause": "batch:" + bid}
                for year in range(start, end + 1)}
            continue
        carry = Fraction(0)
        uncertain = None
        for year in range(start, end + 1):
            if year == start and b["origin"] in ("harvest", "opening"):
                if b["qty"] is None:
                    uncertain = bid
                else:
                    carry += b["qty"]
            for m in moves:
                if m["state"] != "ready" or m["year"] != year:
                    continue
                if m["src"] != bid and m["dst"] != bid:
                    continue
                if m["src"] == bid:
                    applied_out.add(m["id"])
                else:
                    applied_in.add(m["id"])
                if m["qty"] is None:
                    uncertain = m["id"]
                elif m["src"] == bid:
                    carry -= m["qty"]
                else:
                    carry += m["qty"]
                if m["kind"] == "mix_in" and m["dst"] == bid and m["qty"] is not None:
                    inputs.setdefault(bid, []).append({"batch": m["src"],
                                                        "quantity": wrapped(m["qty"])})
            if uncertain:
                rec = {"state": "indeterminate", "cause": uncertain}
            elif carry < 0:
                rec = {"state": "negative", "kg": wrapped(carry)}
                violations.append({"kind": "negative_inventory", "batch": bid,
                                   "year": str(year), "shortage": wrapped(-carry)})
            else:
                rec = {"state": "computed", "kg": wrapped(carry)}
            batches[bid]["balances"][str(year)] = rec
    ready = {m["id"] for m in moves if m["state"] == "ready"}
    ready_in = {m["id"] for m in moves if m["state"] == "ready"
                and m["dst"] is not None}
    assert applied_out == ready, "ready outgoing move omitted from inventory"
    assert applied_in == ready_in, "ready incoming move omitted from inventory"


def _groups(instances, cohorts):
    indexed = {}
    unknown = []
    for cid, item in instances["cohort"].items():
        combo = item["combination"]
        for year, rec in cohorts[cid]["years"].items():
            if any(x is None for x in combo):
                unknown.append({"combination": None, "cohort": cid,
                                "year": year, "known": rec.get("production", wrapped(Fraction(0))),
                                "complete": rec["state"] == "computed",
                                "excluded": [] if rec["state"] == "computed" else
                                [{"cohort": cid, "state": rec["state"]}]})
                continue
            key = (tuple(combo), year)
            indexed.setdefault(key, []).append((cid, rec))
    result = []
    for (combo, year), members in sorted(indexed.items()):
        known = sum((Fraction(rec["production"]["exact"])
                     for _, rec in members if rec["state"] == "computed"), Fraction(0))
        result.append({"combination": list(combo), "year": year,
                       "known": wrapped(known),
                       "complete": all(rec["state"] == "computed" for _, rec in members),
                       "excluded": [{"cohort": cid, "state": rec["state"]}
                                    for cid, rec in members if rec["state"] != "computed"]})
    return result + unknown


def orchard_production(root, *, registry=None):
    project = gg_core.load(Path(root))
    registry = registry or mc.default_registry()
    module = registry.resolve(FRUIT)
    result = _base(project, module)
    try:
        binding = mc.binding_from_project(registry, project)
    except mc.MajorContractError as error:
        result.update(status="held", reason=error.reason)
        return result
    if binding.major_id != FRUIT:
        result.update(status="held", reason="major_not_fruit_trees",
                      bound=binding.major_id)
        return result
    by_field, declared, issues = plan._instances(project)
    instances, _ = plan._instance_plan(project, by_field, declared, issues)
    for cid, cohort in instances["cohort"].items():
        for index, name in enumerate(("crop_species", "cultivar",
                                      "cultivation_form", "heating")):
            effective = cohort["effective"][name]
            if effective["answer_state"] == "provided" and not isinstance(
                    effective["value"], str):
                issues.append({"kind": "invalid_value", "owner": cid,
                               "field": name})
                cohort["combination"][index] = None
    result["violations"].extend(issues)
    issues = result["violations"]
    for f in project["facts"].values():
        if plan.is_yearly(f.get("field_id", "")) and not plan.valid_year(f.get("period")):
            issues.append({"kind": "invalid_period", "fact_id": f["id"]})
    blocked = set()
    for issue in issues:
        field_id = issue.get("field_id")
        if not field_id and issue.get("fact_id") in project["facts"]:
            field_id = project["facts"][issue["fact_id"]].get("field_id")
        m = plan._INSTANCE.match(field_id or "")
        if m and issue["kind"] in ("scope_mismatch", "duplicate_answer",
                                   "invalid_field_id"):
            blocked.add((m.group(1), m.group(2)))
        if issue["kind"] in ("invalid_ref_value", "dangling_ref"):
            blocked.add(("cohort", issue["cohort"]))
    window, view = _window(by_field, issues)
    result["plan_window"] = view
    years = {}
    known = {}
    for cid, item in instances["cohort"].items():
        years[cid] = _year_set(cid, item["fields"], project,
                               instances["batch"], window, result["warnings"])
        result["cohorts"][cid] = _production(
            cid, item, instances, project, window, years[cid], issues,
            result["violations"], result["not_computable"], blocked, known)
    _limits_and_theory(instances, result["cohorts"], issues,
                       result["violations"], result["warnings"], blocked)
    result["batches"], meta = _batches(instances, issues)
    for bid in result["batches"]:
        if ("batch", bid) in blocked:
            result["batches"][bid]["state"] = "held"
            meta[bid]["state"] = "held"
    moves = _moves(instances, meta, result["batches"], issues,
                   result["violations"], result["warnings"], blocked)
    result["allocations"] = _allocations(result["cohorts"], meta,
                                          result["violations"], known)
    _inventory(result["batches"], meta, moves, result["cohorts"],
               result["violations"], result["inputs"])
    for bid, batch in result["batches"].items():
        if batch["origin"] == "mix":
            batch["inputs"] = list(result["inputs"].get(bid, []))
    result["groups"] = _groups(instances, result["cohorts"])
    seen_issues = set()
    unique_issues = []
    for issue in result["violations"]:
        key = json.dumps(issue, sort_keys=True, ensure_ascii=False)
        if key not in seen_issues:
            seen_issues.add(key)
            unique_issues.append(issue)
    result["violations"] = unique_issues
    bad_years = any(rec["state"] != "computed" for c in result["cohorts"].values()
                    for rec in c["years"].values())
    bad_balances = any(rec["state"] != "computed" for b in result["batches"].values()
                       for rec in b["balances"].values())
    bad_batches = any(b["state"] != "ready" for b in result["batches"].values())
    bad_moves = any(m["state"] != "ready" for m in moves)
    bad_allocations = any(a["state"] == "unverifiable" or
                          a.get("allocation_state") == "overallocated"
                          for a in result["allocations"])
    result["completeness"] = "partial" if (bad_years or bad_balances or bad_batches
        or bad_moves or bad_allocations or result["violations"] or
        result["not_computable"]) else "complete"
    result["issue_count"] = len(result["violations"]) + len(result["not_computable"])
    return result


def main(argv):
    if len(argv) != 1:
        print(json.dumps({"status": "error",
                          "reason": "usage: gg_orchard_production.py <folder>"}))
        return 2
    try:
        value = orchard_production(argv[0])
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "reason": str(error)},
                         ensure_ascii=False))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "presented" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
