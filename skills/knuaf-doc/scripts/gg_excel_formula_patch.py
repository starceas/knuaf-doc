#!/usr/bin/env python3
"""Apply reviewed formula replacements to a copied XLSX workbook.

Every replacement is bound to the source workbook hash and the exact formula
currently present in its cell.  The source is never edited; output publication
and receipt publication are staged and rolled back together.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET

from gg_excel_template import (
    NS_MAIN,
    _dom_attr,
    _dom_elements,
    _dom_invalidate_formula_cache,
    _serialize_dom,
    merged_cells,
    sha256,
    check_wage_gender,
    template_price_sources,
    workbook_sheets,
)


MAP_SCHEMA = "gg-xlsx-formula-patch-map/v1"
_UNSAFE_FORMULA = re.compile(r"(?:\[[^\]]+\]|\bDDE\s*\(|#REF!|EXTERNAL|\||\b(?:WEBSERVICE|HYPERLINK)\s*\()", re.I)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _formula_text(cell: ET.Element) -> str | None:
    f = cell.find(f"{{{NS_MAIN}}}f")
    return None if f is None else (f.text or "")


def _normalize_formula(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty formula string")
    formula = value.strip()
    if formula.startswith("="):
        formula = formula[1:]
    if not formula or _UNSAFE_FORMULA.search(formula):
        raise ValueError(f"unsafe external/DDE formula in {field}")
    return formula


def _expected_number(value: object) -> int | float:
    """Validate a finite numeric precondition for a value-to-formula patch."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected_value must be a finite number")
    try:
        finite = Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError):
        finite = False
    if not finite:
        raise ValueError("expected_value must be a finite number")
    return value


def _patches(data: dict) -> list[dict]:
    if data.get("schema") != MAP_SCHEMA:
        raise ValueError(f"map schema must be {MAP_SCHEMA}")
    source = data.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
        raise ValueError("map must contain source.sha256")
    patches = data.get("patches", data.get("entries"))
    if not isinstance(patches, list) or (not patches and "d8" not in data):
        raise ValueError("map must contain non-empty patches")
    return patches


def _evidence(patch: dict) -> dict:
    evidence = patch.get("evidence_ref")
    if not isinstance(evidence, dict):
        raise ValueError("patch missing evidence_ref")
    for key in ("source_id", "revision", "locator"):
        if key not in evidence:
            raise ValueError(f"evidence_ref missing {key}")
    if not isinstance(evidence["source_id"], str) or not evidence["source_id"].strip():
        raise ValueError("evidence_ref.source_id must be non-empty")
    if isinstance(evidence["revision"], bool) or not isinstance(evidence["revision"], (int, str)):
        raise ValueError("evidence_ref.revision must be an integer or string")
    if not isinstance(evidence["locator"], str) or not evidence["locator"].strip():
        raise ValueError("evidence_ref.locator must be non-empty")
    return evidence


D8_SPEC_PATH = (Path(__file__).resolve().parents[1] / "references"
                / "common-workbooks/corrections/d8-spec.json")


def _official_rate(item: dict, kind: str, *, price_sources=None) -> dict:
    """Adapt template endpoints to the shared official-series contract."""
    from gg_price_assumptions import (
        load_registry, normalize_price_assumptions, multiplier,
        resolve_series, check_observations, check_dimensions, canonical_dimensions,
    )
    if not isinstance(item, dict):
        raise ValueError(f"{kind} rate metadata required")
    evidence = _evidence({"evidence_ref": item.get("evidence_ref")})
    if evidence.get("origin") != "factual":
        raise ValueError(f"{kind} requires official statistic evidence with factual origin")
    role = "wage" if kind in {"wage_male", "wage_female"} else kind
    if role not in {"general", "wage", "sales"}:
        raise ValueError("unknown price role")
    year = item.get("application_base_year")
    if type(year) is not int or not 1900 <= year <= 9999:
        raise ValueError(f"{kind}.application_base_year must be a year")
    years, values = item.get("observation_years"), item.get("observed_values")
    if (not isinstance(years, list) or len(years) < 2
            or any(type(y) is not int or not 1900 <= y <= 9999 for y in years)
            or any(b <= a for a, b in zip(years, years[1:]))
            or not isinstance(values, list) or len(values) != len(years)):
        raise ValueError("invalid rate observation years/values")
    if year < years[-1]:
        raise ValueError("application year precedes observation end")
    raw = {"source_id": evidence["source_id"], "application_base_year": year,
           "observation": {"start_year": years[0], "end_year": years[-1]}}
    if "base_year" in item:
        raw["base_year"] = item["base_year"]
    if "r" in item:
        raw["rate"] = _expected_number(item["r"])
    for field in ("dimensions", "target_dimensions"):
        if field in item:
            raw[field] = item[field]
    spec = {"price_sources": [] if price_sources is None else price_sources,
            "price_assumptions": {r: raw if r == role else
                {"status": "not_applied", "reason": "separate template role"}
                for r in ("general", "wage", "sales")}}
    if "target_dimensions" in item:
        target = canonical_dimensions(item["target_dimensions"])
        spec["target_dimensions"] = target
        raw["target_dimensions"] = target
        if "crop" in target:
            crop = target["crop"]
            spec["crops"] = crop if isinstance(crop, list) else [crop]
    registry = load_registry()
    normalized = normalize_price_assumptions(spec, list(range(year, year + 5)), registry=registry)[role]
    for plan_year in range(year, year + 5):
        multiplier(normalized, plan_year)
    # Template copies may carry observed_values for review, but those values
    # cannot replace a registered series under its official id.
    source = resolve_series(evidence["source_id"], registry=registry,
                            student_sources=template_price_sources(spec["price_sources"]))
    if kind in {"wage_male", "wage_female"}:
        check_wage_gender(source, "남자" if kind == "wage_male" else "여자", origin=kind)
    check_observations(source, years, values, origin=kind)
    if "dimensions" in item:
        check_dimensions(source, item["dimensions"], origin=kind)
    return {**item, "r": normalized["rate"], "unit": normalized["unit"],
            "evidence_ref": {**evidence, "source_id": source["id"]},
            "base_year": normalized["base_year"],
            "observation_range": [years[0], years[-1]],
            "application_counts": list(range(5)),
            "source_verification": "registered official series; locator not independently verified"}


def _reviewed_observation_formulas(source: Path, spec: dict) -> dict:
    """Admit exact formulas only for the registered SHA and pinned audit."""
    reviewed = {}
    source_digest = sha256(source)
    for entry in spec.get("reviewed_observation_formulas", []):
        if source_digest != entry["source_sha256"]:
            continue
        audit_path = D8_SPEC_PATH.parent.parent / entry["audit_file"]
        if sha256(audit_path) != entry["audit_sha256"]:
            raise ValueError("reviewed observation audit hash differs")
        audit = _json(audit_path)
        if (audit.get("schema") != "knuaf-workbook-formula-audit/v1"
                or audit.get("source_sha256") != source_digest
                or not any(all(row.get(k) == entry[k] for k in ("sheet", "cell", "formula"))
                           for row in audit.get("inventory", []))):
            raise ValueError("reviewed observation audit source/formula differs")
        reviewed[entry["sheet"], entry["cell"]] = entry
    return reviewed


def materialize_d8(source: Path, assumptions: dict | None = None, *, prepare_observations=False) -> dict:
    """Materialize an exact SHA/precondition-bound map; no workbook is written.

    With no assumptions, only disconnected daily-wage chains are restored.
    Existing X01 chains remain byte-identical. Rates/observation preparation
    are explicit, separately reviewable operations.
    """
    from gg_excel_template import detect_layout
    from gg_workbook_audit import Workbook
    if detect_layout(source) != "x01":
        raise ValueError("layout_variant_unsupported: D8 requires x01")
    spec = _json(D8_SPEC_PATH)
    book = Workbook.load(source)
    sheet = spec["wage_sheet"]
    if sheet not in book.sheets:
        raise ValueError("D8 wage sheet missing")
    patches = []
    structural_evidence = {"source_id": "template.d8.wage_link", "revision": 1,
                           "locator": "d8-spec.json wage_rows/year_columns; DESIGN-PR2 P2-P6"}

    def add(s, cell, formula=None, *, prepare=False, evidence=None):
        old = book.formulas.get((s, cell))
        if not prepare and old is not None and _normalize_formula(old.text, "existing formula") == formula:
            return
        p = {"sheet": s, "cell": cell, "reason": "D8 explicit price/wage connection; preserve C1 and year labels",
             "evidence_ref": structural_evidence if evidence is None else evidence}
        if old is not None:
            p["expected_formula"] = "=" + old.text
        else:
            value = book.values.get((s, cell))
            p["expected_value"] = _expected_number(value)
        if prepare:
            if old is None:
                return
            p.update(operation="prepare_wage_input", new_value=None)
        else:
            p["new_formula"] = "=" + formula
        patches.append(p)

    for row, route in spec["wage_rows"].items():
        for i, col in enumerate(spec["year_columns"]):
            ref = col + row
            prior = None if not i else spec["year_columns"][i-1] + row
            desired = route["base"] if not i else f"{prior}*(1+${route['rate'][:2]}$38)"
            old = book.formulas.get((sheet, ref))
            if old is not None:
                expression = _normalize_formula(old.text, "existing formula")
                accepted = {desired, col + "9"} if row == "8" else {desired}
                if prior:
                    accepted.add(prior)
                if expression not in accepted and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", expression):
                    raise ValueError(f"unreviewed wage formula: {sheet}!{ref}")
            add(sheet, ref, desired)
    if prepare_observations:
        if assumptions is not None:
            raise ValueError("prepare observations, fill sourced values, then apply rate assumptions")
        reviewed = _reviewed_observation_formulas(source, spec)
        for row in range(26, 32):
            for col in "XYZ":
                ref = f"{col}{row}"
                old = book.formulas.get((sheet, ref))
                # Only the school's consecutive year chain and carried female
                # observation are convertible; other formulas need review.
                if old is not None:
                    expected = f"X{row-1}+1" if col == "X" and row > 26 else "Z29" if ref == "Z28" else None
                    evidence = None
                    if old.text != expected:
                        entry = reviewed.get((sheet, ref))
                        if entry is None or old.text != entry["formula"]:
                            raise ValueError(f"unreviewed observation formula: {ref}")
                        evidence = {**structural_evidence,
                                    "locator": (f"d8-spec.json reviewed_observation_formulas; "
                                                f"source_sha256={entry['source_sha256']}; "
                                                f"{entry['audit_file']} sha256={entry['audit_sha256']}; "
                                                f"{sheet}!{ref}={entry['formula']}")}
                    add(sheet, ref, prepare=True, evidence=evidence)
        for col, rate_cell in (("Y", "AC38"), ("Z", "AD38")):
            old = book.formulas.get((sheet, rate_cell))
            desired = f"({col}31/{col}26)^(1/(X31-X26))-1"
            if old is not None and old.text not in {f"AVERAGE({rate_cell[:2]}26:{rate_cell[:2]}37)", desired}:
                raise ValueError(f"unreviewed observation rate formula: {rate_cell}")
            add(sheet, rate_cell, desired)
    rates = {}
    if assumptions is not None:
        if not isinstance(assumptions, dict) or set(assumptions) - {"wage_male", "wage_female", "sales", "general", "price_sources"}:
            raise ValueError("unknown D8 assumptions")
        for kind in ("wage_male", "wage_female"):
            rates[kind] = _official_rate(assumptions.get(kind), kind, price_sources=assumptions.get("price_sources", []))
        sales = assumptions.get("sales")
        if isinstance(sales, dict) and sales.get("mode") == "not_applied":
            if sales.get("reason") != "확인 불가":
                raise ValueError("sales not_applied requires explicit 확인 불가")
            rates["sales"] = {"mode": "not_applied", "reason": "판매가 상승 미반영(확인 불가)"}
        else:
            rates["sales"] = _official_rate(sales, "sales", price_sources=assumptions.get("price_sources", []))
        # Pricing year is a preserved school label, including W-023. Never
        # choose a different year to make the arithmetic look plausible.
        first_year = book.evaluate(sheet, "F6")
        if isinstance(first_year, str) and re.fullmatch(r"[0-9]{4}년?", first_year.strip()):
            first_year = int(first_year.strip().rstrip("년"))
        if isinstance(first_year, bool) or not isinstance(first_year, (int, float)):
            raise ValueError("preserved F6 is not a plan year; W-023 review required")
        for kind, col, rate_cell in (("wage_male", "Y", "AC38"), ("wage_female", "Z", "AD38")):
            item = rates[kind]
            if item["application_base_year"] != first_year:
                raise ValueError("wage application year differs from preserved F6; W-023 review required")
            daily_base_year = book.evaluate(sheet, "X31")
            if (isinstance(daily_base_year, bool) or not isinstance(daily_base_year, (int, float))
                    or not 1900 <= daily_base_year <= 9999 or int(daily_base_year) != daily_base_year):
                raise ValueError("daily wage observation end year must be filled before rate application")
            count = item["application_base_year"] - daily_base_year
            if count < 0 or int(count) != count:
                raise ValueError("wage base observation year invalid")
            from gg_price_assumptions import multiplier
            # The daily-wage anchor can precede the rate's application base
            # by more than four years; validate its actual cumulative factor.
            multiplier({"role": "wage", "status": "applied", "rate": item["r"],
                        "application_base_year": int(daily_base_year)}, int(first_year) + 4)
            item["daily_base_year"] = daily_base_year
            item["daily_base_application_count"] = int(count)
            add(sheet, rate_cell, repr(item["r"]))
            add(sheet, col+"34", f"{col}31*(1+${rate_cell[:2]}$38)^{int(count)}")
        sales_sheet = spec["sales_sheet"]
        if sales_sheet not in book.sheets:
            raise ValueError("sales sheet missing")
        item = rates["sales"]
        sales_first_year = book.evaluate(sales_sheet, "C36")
        if isinstance(sales_first_year, str) and re.fullmatch(r"[0-9]{4}년?", sales_first_year.strip()):
            sales_first_year = int(sales_first_year.strip().rstrip("년"))
        if item.get("mode") != "not_applied" and item["application_base_year"] != sales_first_year:
            raise ValueError("sales application year differs from preserved C36")
        # Repoint from the exact quantity cell, never multiply an already
        # escalated revenue. This makes rate updates idempotent.
        for row, price in spec["sales_rows"].items():
            for i, (quantity, revenue) in enumerate(zip("CEGIK", "DFHJL")):
                base = f"{quantity}{row}*${price[0]}${price[1:]}/1000"
                old = book.formulas.get((sales_sheet, revenue+row))
                if old is None or not (old.text == base or re.fullmatch(re.escape(base) + r"\*\(1\+[0-9.eE+\-]+\)\^[0-4]", old.text)):
                    raise ValueError(f"unreviewed sales formula: {revenue}{row}")
                if item.get("mode") == "not_applied":
                    desired = base
                else:
                    desired = base + f"*(1+{item['r']!r})^{i}"
                add(sales_sheet, revenue+row, desired)
        note = (item["reason"] if item.get("mode") == "not_applied" else
                f"판매가 상승률 {item['r']:.8%}; "
                f"{'지수 기준연도 해당 없음' if item['base_year'] is None else str(item['base_year']) + ' 기준연도'}; "
                f"관측 {item['observation_years'][0]}~{item['observation_years'][-1]}; "
                f"적용 기초연도 {item['application_base_year']}; 적용 횟수 0~4; "
                f"출처: {item['evidence_ref']['source_id']} ({item['evidence_ref']['locator']})")
        old_note = book.values.get((sales_sheet, "B46"))
        if (sales_sheet, "B46") in book.formulas or not isinstance(old_note, str):
            raise ValueError("reviewed sales assumption note B46 missing or formula")
        if old_note != note:
            patches.append({"sheet": sales_sheet, "cell": "B46", "expected_text": old_note,
                            "new_text": note, "operation": "price_assumption_note",
                            "reason": "Replace stale 2.5% comment with actual sales-rate decision",
                            "evidence_ref": structural_evidence})
        if "general" in assumptions:
            rates["general"] = _official_rate(assumptions["general"], "general", price_sources=assumptions.get("price_sources", []))
            if rates["general"]["application_base_year"] != first_year:
                raise ValueError("general application year differs from preserved plan year")
            for (s, ref), old in book.formulas.items():
                match = re.fullmatch(r"([A-Z]+[1-9][0-9]*)\*(?:1\.025|\(1\+[0-9.eE+\-]+\))", old.text)
                if s in {"9 .경비계획", "7. 영농자재소요계획"} and match:
                    add(s, ref, match[1] + f"*(1+{rates['general']['r']!r})")
    return {"schema": MAP_SCHEMA, "source": {"sha256": sha256(source)},
            "patches": patches,
            "d8": {"schema": spec["schema"], "spec_sha256": sha256(D8_SPEC_PATH),
                   "assumptions": assumptions, "rates": rates,
                   "prepare_observations": prepare_observations,
                   "preserved_findings": spec["preserved_findings"],
                   "inherited_observations": "not verified; structural repair is not statistical approval"}}


def _qualified(dom: minidom.Document, root: minidom.Element, local: str) -> minidom.Element:
    prefix = root.prefix or ""
    return dom.createElementNS(NS_MAIN, f"{prefix}:{local}" if prefix else local)


def _expand_targeted_shared(root, dom, dom_cells, patches, sheet):
    """Materialize only complete shared groups touched by a reviewed patch.

    Translation preserves each member's effective formula. Ambiguous, sparse,
    array, or malformed groups are not guessed. The caller still checks every
    requested cell's exact expected formula before publishing anything.
    """
    cells = {c.get("r"): c for c in root.findall(f".//{{{NS_MAIN}}}c")}
    wanted = set()
    for patch in patches:
        cell = cells.get(patch["cell"])
        f = None if cell is None else cell.find(f"{{{NS_MAIN}}}f")
        if f is not None and f.get("t") == "shared":
            si = f.get("si")
            if si is None or not si.isdecimal():
                raise ValueError(f"invalid shared formula index: {sheet}!{patch['cell']}")
            wanted.add(si)
    if not wanted:
        return []
    from openpyxl.formula.translate import Translator, TranslatorError
    from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
    expanded = []
    for si in sorted(wanted):
        members = [(ref, c.find(f"{{{NS_MAIN}}}f")) for ref, c in cells.items()
                   if c.find(f"{{{NS_MAIN}}}f") is not None
                   and c.find(f"{{{NS_MAIN}}}f").get("si") == si]
        if any(f.get("t") != "shared" for _, f in members):
            raise ValueError(f"mixed shared formula group: {sheet} index {si}")
        anchors = [(ref, f) for ref, f in members if f.text and f.text.strip()]
        if len(anchors) != 1:
            raise ValueError(f"shared formula anchor missing or ambiguous: {sheet} index {si}")
        anchor, af = anchors[0]
        area = af.get("ref", "")
        if not re.fullmatch(r"[A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?", area):
            raise ValueError(f"shared formula anchor range missing or invalid: {sheet}!{anchor}")
        left, top, right, bottom = range_boundaries(area)
        if left > right or top > bottom or right > 16384 or bottom > 1048576:
            raise ValueError(f"invalid shared formula range: {sheet}!{area}")
        if len(members) != (right-left+1)*(bottom-top+1):
            raise ValueError(f"incomplete shared formula group: {sheet}!{area}")
        expression = _normalize_formula(af.text, "shared anchor")
        changes = []
        for ref, f in members:
            row, col = coordinate_to_tuple(ref)
            if not (left <= col <= right and top <= row <= bottom):
                raise ValueError(f"shared formula member outside anchor range: {sheet}!{ref}")
            if ref != anchor and (f.get("ref") is not None or (f.text and f.text.strip())):
                raise ValueError(f"ambiguous shared formula member: {sheet}!{ref}")
            try:
                translated = Translator("=" + expression, origin=anchor).translate_formula(ref)
            except TranslatorError as exc:
                raise ValueError(f"cannot translate shared formula: {sheet}!{ref}") from exc
            resolved = _normalize_formula(translated, "translated shared formula")
            nodes = [n for n in dom_cells[ref].childNodes if n.nodeType == n.ELEMENT_NODE
                     and n.namespaceURI == NS_MAIN and n.localName == "f"]
            if len(nodes) != 1:
                raise ValueError(f"formula node missing or duplicated: {sheet}!{ref}")
            changes.append((ref, f, nodes[0], resolved))
        for ref, f, node, resolved in changes:
            for key in ("t", "ref", "si"):
                f.attrib.pop(key, None)
                if node.hasAttribute(key):
                    node.removeAttribute(key)
            f.text = resolved
            while node.firstChild:
                node.removeChild(node.firstChild)
            node.appendChild(dom.createTextNode(resolved))
            expanded.append({"sheet": sheet, "shared_index": si, "anchor": anchor,
                             "range": area, "cell": ref, "formula": "=" + resolved})
    return expanded


def patch_copy(source: Path, map_path: Path, out: Path, *, context=None) -> dict:
    """Guarded public entry: authorize before any write, then run the
    existing patch engine (policy B)."""
    import gg_major_contract as mc

    authorization = mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
    mc.require_finance_profile(authorization, "school_17_sheet_v1")
    return _patch_copy(source, map_path, out,
                       _authorization=authorization, _context=context)


def _patch_copy(source: Path, map_path: Path, out: Path, *,
                _authorization=None, _context=None) -> dict:
    if out.exists():
        raise FileExistsError(f"refusing to overwrite output: {out}")
    data = _json(map_path)
    source_info = data.get("source")
    if not isinstance(source_info, dict) or not isinstance(source_info.get("sha256"), str):
        raise ValueError("map must contain source.sha256")
    expected_sha = source_info["sha256"]
    actual_sha = sha256(source)
    if expected_sha != actual_sha:
        raise RuntimeError(f"source version changed: map sha256={expected_sha}, current sha256={actual_sha}")
    if "d8" in data:
        d8 = data["d8"]
        if not isinstance(d8, dict) or not isinstance(d8.get("prepare_observations"), bool):
            raise ValueError("invalid D8 contract")
        reviewed = materialize_d8(source, d8.get("assumptions"),
                                  prepare_observations=d8["prepare_observations"])
        if reviewed["d8"] != d8 or reviewed["patches"] != data.get("patches"):
            raise ValueError("D8 map differs from materialized specification")
    patches = _patches(data)
    if not patches:
        import shutil
        if _authorization is not None:
            import gg_major_contract as mc
            mc.reconfirm_output(_authorization, _context)
        shutil.copyfile(source, out)
        receipt = {"schema": "gg-xlsx-formula-patch-receipt/v1",
                   "source": {"path": str(source.resolve()), "sha256": actual_sha},
                   "map": {"path": str(map_path.resolve()), "sha256": sha256(map_path)},
                   "output": {"path": str(out.resolve()), "sha256": sha256(out), "status": "unchanged"},
                   "patched": [], "expanded_shared_formulas": [],
                   "formulaCachesInvalidated": 0, "recalcNeeded": False, "d8": data["d8"]}
        if _authorization is not None:
            receipt["majorAuthorization"] = _authorization.to_dict()
        return receipt
    with zipfile.ZipFile(source, "r") as zin:
        sheets = dict(workbook_sheets(zin))
        by_sheet: dict[str, list[dict]] = {}
        seen: set[tuple[str, str]] = set()
        for patch in patches:
            if not isinstance(patch, dict):
                raise ValueError("patches must be objects")
            sheet, cell_ref = patch.get("sheet"), patch.get("cell")
            if not isinstance(sheet, str) or sheet not in sheets:
                raise ValueError(f"patch references missing sheet: {sheet!r}")
            if not isinstance(cell_ref, str) or not re.fullmatch(r"[A-Za-z]+[1-9][0-9]*", cell_ref.replace("$", "")):
                raise ValueError(f"patch cell must be an exact cell reference: {sheet}!{cell_ref}")
            cell_ref = cell_ref.upper().replace("$", "")
            key = (sheet, cell_ref)
            if key in seen:
                raise ValueError(f"duplicate patch target: {sheet}!{cell_ref}")
            seen.add(key)
            patch = dict(patch)
            patch["cell"] = cell_ref
            has_formula = "expected_formula" in patch
            has_value = "expected_value" in patch
            has_text = "expected_text" in patch
            if sum((has_formula, has_value, has_text)) != 1:
                raise ValueError("patch must contain exactly one of expected_formula or expected_value")
            if has_formula:
                patch["expected_formula"] = _normalize_formula(patch.get("expected_formula"), "expected_formula")
            elif has_value:
                patch["expected_value"] = _expected_number(patch.get("expected_value"))
            else:
                if ("d8" not in data or patch.get("operation") != "price_assumption_note"
                        or not isinstance(patch["expected_text"], str)
                        or not isinstance(patch.get("new_text"), str) or not patch["new_text"].strip()
                        or "new_formula" in patch):
                    raise ValueError("invalid reviewed price assumption note")
            prepare = patch.get("operation") == "prepare_wage_input"
            if prepare:
                if ("d8" not in data or not has_formula or "new_formula" in patch
                        or "new_value" not in patch or patch["new_value"] is not None):
                    raise ValueError("invalid reviewed wage-input preparation")
            elif not has_text:
                patch["new_formula"] = _normalize_formula(patch.get("new_formula"), "new_formula")
            if not isinstance(patch.get("reason"), str) or not patch["reason"].strip():
                raise ValueError("patch reason must be non-empty")
            _evidence(patch)
            by_sheet.setdefault(sheet, []).append(patch)

        modified: dict[str, bytes] = {}
        receipt = {
            "schema": "gg-xlsx-formula-patch-receipt/v1",
            "source": {"path": str(source.resolve()), "sha256": actual_sha, "size": source.stat().st_size},
            "map": {"path": str(map_path.resolve()), "sha256": sha256(map_path)},
            "output": {"path": str(out.resolve())},
            "patched": [], "expanded_shared_formulas": [],
            "formulaCachesInvalidated": 0, "recalcNeeded": True,
        }
        if "d8" in data:
            receipt["d8"] = data["d8"]
        for sheet, target in sheets.items():
            raw = zin.read(target)
            root = ET.fromstring(raw)
            dom = _serialize_dom(raw, target)
            dom_cells = {_dom_attr(c, "r"): c for c in _dom_elements(dom, NS_MAIN, "c") if _dom_attr(c, "r")}
            anchors, nonanchors = merged_cells(root)
            receipt["expanded_shared_formulas"].extend(
                _expand_targeted_shared(root, dom, dom_cells, by_sheet.get(sheet, []), sheet))
            for patch in by_sheet.get(sheet, []):
                ref = patch["cell"]
                et_cell = root.find(f".//{{{NS_MAIN}}}c[@r='{ref}']")
                if et_cell is None or ref not in dom_cells:
                    raise ValueError(f"patch references missing cell: {sheet}!{ref}")
                if ref in nonanchors:
                    raise ValueError(f"cannot patch merged non-anchor: {sheet}!{ref}")
                old = _formula_text(et_cell)
                f_et = et_cell.find(f"{{{NS_MAIN}}}f")
                dom_cell = dom_cells[ref]
                if patch.get("operation") == "price_assumption_note":
                    from gg_excel_template import load_shared, text_value
                    from gg_excel_fill import _set_cell_value
                    if old is not None or text_value(et_cell, load_shared(zin)) != patch["expected_text"]:
                        raise ValueError(f"expected text mismatch: {sheet}!{ref}")
                    _set_cell_value(dom_cell, "string", patch["new_text"])
                    receipt["patched"].append(patch)
                    continue
                if patch.get("operation") == "prepare_wage_input":
                    if old is None or _normalize_formula(old, "existing formula") != patch["expected_formula"]:
                        raise ValueError(f"expected formula mismatch: {sheet}!{ref}")
                    for child in list(dom_cell.childNodes):
                        if child.nodeType == child.ELEMENT_NODE and child.namespaceURI == NS_MAIN and child.localName in {"f", "v", "is"}:
                            dom_cell.removeChild(child)
                    if dom_cell.hasAttribute("t"):
                        dom_cell.removeAttribute("t")
                    receipt["patched"].append({**patch, "operation": "prepare_wage_input", "old_formula": "=" + old})
                    continue
                if "expected_value" in patch:
                    if old is not None:
                        raise ValueError(f"expected_value target is a formula cell: {sheet}!{ref}")
                    if et_cell.attrib.get("t") not in (None, "n"):
                        raise ValueError(f"expected_value target is not a numeric cell: {sheet}!{ref}")
                    value_node = et_cell.find(f"{{{NS_MAIN}}}v")
                    if value_node is None or value_node.text is None:
                        raise ValueError(f"expected_value target has no numeric value: {sheet}!{ref}")
                    try:
                        actual_decimal = Decimal(value_node.text)
                    except (TypeError, ValueError, InvalidOperation):
                        raise ValueError(f"expected_value target is not numeric: {sheet}!{ref}")
                    if not actual_decimal.is_finite():
                        raise ValueError(f"expected_value target is not finite: {sheet}!{ref}")
                    try:
                        expected_decimal = Decimal(str(patch["expected_value"]))
                    except (InvalidOperation, ValueError):
                        raise ValueError("expected_value must be a finite number")
                    if actual_decimal != expected_decimal:
                        raise ValueError(f"expected value mismatch: {sheet}!{ref}")
                    prefix = dom_cell.prefix or dom.documentElement.prefix or ""
                    formula_node = dom.createElementNS(NS_MAIN, f"{prefix}:f" if prefix else "f")
                    formula_node.appendChild(dom.createTextNode(patch["new_formula"]))
                    value_element = next((c for c in dom_cell.childNodes
                                          if c.nodeType == c.ELEMENT_NODE and c.namespaceURI == NS_MAIN
                                          and c.localName == "v"), None)
                    if value_element is not None:
                        dom_cell.insertBefore(formula_node, value_element)
                    else:
                        dom_cell.appendChild(formula_node)
                    receipt["patched"].append({"sheet": sheet, "cell": ref,
                        "expected_value": patch["expected_value"], "old_value": value_node.text,
                        "old_value_lexeme": value_node.text,
                        "new_formula": patch["new_formula"], "operation": "value_to_formula",
                        "reason": patch["reason"], "evidence_ref": patch["evidence_ref"]})
                else:
                    if old is None:
                        raise ValueError(f"patch target is not a formula cell: {sheet}!{ref}")
                    if f_et is not None and any(k in f_et.attrib for k in ("t", "ref", "si")):
                        raise ValueError(f"shared/array formula target is unsupported: {sheet}!{ref}")
                    if _normalize_formula(old, "existing formula") != patch["expected_formula"]:
                        raise ValueError(f"expected formula mismatch: {sheet}!{ref}")
                    f_nodes = [c for c in dom_cell.childNodes
                               if c.nodeType == c.ELEMENT_NODE and c.namespaceURI == NS_MAIN and c.localName == "f"]
                    if len(f_nodes) != 1:
                        raise ValueError(f"formula node missing or duplicated: {sheet}!{ref}")
                    while f_nodes[0].firstChild:
                        f_nodes[0].removeChild(f_nodes[0].firstChild)
                    f_nodes[0].appendChild(dom.createTextNode(patch["new_formula"]))
                    receipt["patched"].append({"sheet": sheet, "cell": ref,
                        "expected_formula": patch["expected_formula"], "old_formula": "=" + old,
                        "new_formula": patch["new_formula"], "operation": "formula_replace",
                        "reason": patch["reason"], "evidence_ref": patch["evidence_ref"]})
            for cell in dom_cells.values():
                if _dom_invalidate_formula_cache(cell):
                    receipt["formulaCachesInvalidated"] += 1
            modified[target] = dom.toxml(encoding="utf-8")

        try:
            wb_raw = zin.read("xl/workbook.xml")
            wb_dom = _serialize_dom(wb_raw, "xl/workbook.xml")
            calc_nodes = _dom_elements(wb_dom, NS_MAIN, "calcPr")
            calc = calc_nodes[0] if calc_nodes else None
            if calc is None:
                root = wb_dom.documentElement
                calc = _qualified(wb_dom, root, "calcPr")
                ext = next((c for c in root.childNodes if c.nodeType == c.ELEMENT_NODE and c.namespaceURI == NS_MAIN and c.localName == "extLst"), None)
                root.insertBefore(calc, ext) if ext is not None else root.appendChild(calc)
            for key, value in (("calcMode", "auto"), ("fullCalcOnLoad", "1"), ("forceFullCalc", "1")):
                calc.setAttribute(key, value)
            modified["xl/workbook.xml"] = wb_dom.toxml(encoding="utf-8")
        except KeyError:
            raise ValueError("workbook.xml missing; cannot set recalculation flags")

        if _authorization is not None:
            import gg_major_contract as mc
            mc.reconfirm_output(_authorization, _context)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                zout.writestr(info, modified.get(info.filename, zin.read(info.filename)))
    if _authorization is not None:
        receipt["majorAuthorization"] = _authorization.to_dict()
    receipt["output"].update({"path": str(out.resolve()), "sha256": sha256(out), "size": out.stat().st_size, "status": "patched"})
    return receipt


def cli(argv: list[str]) -> int:
    if argv and argv[0] == "materialize-d8":
        parser = argparse.ArgumentParser(description="Materialize source-bound D8 formula patch map")
        parser.add_argument("--source", required=True, type=Path)
        parser.add_argument("--out", required=True, type=Path)
        parser.add_argument("--assumptions", type=Path)
        parser.add_argument("--prepare-observations", action="store_true")
        args = parser.parse_args(argv[1:])
        try:
            result = materialize_d8(args.source, _json(args.assumptions) if args.assumptions else None,
                                    prepare_observations=args.prepare_observations)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
            print(json.dumps({"status": "materialized", "patches": len(result["patches"]),
                              "out": str(args.out)}, ensure_ascii=False))
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"BLOCK: {exc}", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--project", default=None, type=Path,
                        help="프로젝트 정본 폴더")
    parser.add_argument("--major", default=None, help="명시 전공 ID")
    args = parser.parse_args(argv)
    source, out, receipt_path = args.source.resolve(), args.out.resolve(), args.receipt.resolve()
    if len({source, out, receipt_path}) != 3:
        print("BLOCK: source, out, and receipt must be different paths", file=sys.stderr)
        return 2
    staged_out = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    staged_receipt = receipt_path.with_name(f".{receipt_path.name}.tmp-{os.getpid()}")
    try:
        if out.exists() or receipt_path.exists() or staged_out.exists() or staged_receipt.exists():
            raise FileExistsError("refusing to overwrite output, receipt, or staging path")
        import gg_major_contract as mc
        context = (mc.output_context(args.project, args.major)
                   if args.project is not None else None)
        # Policy B pre-check before any directory/staging is created.
        mc.authorize_output(mc.OUTPUT_SCHOOL_WORKBOOK, context)
        out.parent.mkdir(parents=True, exist_ok=True)
        result = patch_copy(source, args.map.resolve(), staged_out,
                            context=context)
        result["output"]["path"] = str(out)
        staged_receipt.parent.mkdir(parents=True, exist_ok=True)
        staged_receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # Reconfirm after the receipt is staged, right before the first
        # final replace (policy B).
        mc.reconfirm_output(
            mc.OutputAuthorization(**result["majorAuthorization"]), context)
        published = False
        try:
            os.replace(staged_out, out); published = True
            os.replace(staged_receipt, receipt_path)
        except Exception:
            if published and out.exists(): out.unlink()
            raise
        print(json.dumps({"status": "patched", "out": result["output"], "patched": len(result["patched"]),
                          "formulaCachesInvalidated": result["formulaCachesInvalidated"]}, ensure_ascii=False))
        return 0
    except Exception as exc:
        for p in (staged_out, staged_receipt):
            if p.exists(): p.unlink()
        import gg_major_contract as mc
        if isinstance(exc, mc.OutputHeldError):
            print(json.dumps({"status": "held", "reason": exc.reason,
                              "detail": str(exc),
                              "guidance": exc.detail.get("guidance")},
                             ensure_ascii=False))
            return 2
        if isinstance(exc, (OSError, ValueError, RuntimeError,
                            zipfile.BadZipFile, ET.ParseError)):
            print(f"BLOCK: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(cli(sys.argv[1:]))
