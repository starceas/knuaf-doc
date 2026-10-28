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


def _excel_number(value: object) -> str:
    """Serialize a finite number as the formula literal Excel persists.

    Excel truncates a formula's numeric literal text at 15 significant
    digits (it does not round), drops a trailing ".0", writes the stored
    literal back verbatim in fixed-point notation when that needs at most
    21 characters, and otherwise emits a "d.dddE±dd" mantissa form.  Emit
    that stored form directly so a native save leaves the token identical.
    For d = Decimal(repr(value)) with decimal order k, truncation gives
    |Decimal(token) - d| < 10**(k - 14), or < 1e-14 * |d|.
    Allowing for both binary-float conversions, normal finite rates differ
    by at most 1.1e-14 * |value|, inside the downstream rel/abs 1e-12
    tolerance. Zero is exact. Receipts retain the original rate in r and
    record this formula token separately in r_literal.
    """
    x = float(_expected_number(value))
    if x == 0:
        return "0"
    sign, digits, exp = Decimal(repr(x)).as_tuple()
    digits = list(digits)
    if len(digits) > 15:
        exp += len(digits) - 15
        del digits[15:]
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    if exp >= 0:
        fixed = "".join(map(str, digits)) + "0" * exp
    elif len(digits) + exp > 0:
        cut = len(digits) + exp
        fixed = "".join(map(str, digits[:cut])) + "." + "".join(map(str, digits[cut:]))
    else:
        fixed = "0." + "0" * -(len(digits) + exp) + "".join(map(str, digits))
    sign_text = "-" if sign else ""
    if len(fixed) <= 21:
        return sign_text + fixed
    exponent = exp + len(digits) - 1
    mantissa = str(digits[0])
    if len(digits) > 1:
        mantissa += "." + "".join(map(str, digits[1:]))
    return f"{sign_text}{mantissa}E{exponent:+03d}"


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
        load_registry, normalize_price_role, multiplier,
        resolve_series, check_observations, check_dimensions, canonical_dimensions,
    )
    if not isinstance(item, dict):
        raise ValueError(f"{kind} rate metadata required")
    if kind == "sales":
        a = normalize_price_role(
            {"writing_year": item.get("writing_year"),
             "price_assumptions": {"sales": item},
             "price_sources": price_sources or []},
            "sales", [item.get("application_base_year")])
        return {**a, "r": a["rate"]}
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
            "price_assumptions": {role: raw}}
    if "target_dimensions" in item:
        target = canonical_dimensions(item["target_dimensions"])
        spec["target_dimensions"] = target
        raw["target_dimensions"] = target
        if "crop" in target:
            crop = target["crop"]
            spec["crops"] = crop if isinstance(crop, list) else [crop]
    registry = load_registry()
    normalized = normalize_price_role(spec, role, list(range(year, year + 5)), registry=registry)
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


# 특용작물 X01의 임금 상승률은 연도별 상승률(AC26:AC30, AD26:AD30)의 산술평균
# AC38 =AVERAGE(AC26:AC37), AD38 =AVERAGE(AD26:AD37)이다(사용자 결정
# 2026-10-28, 교수님 양식 기준). D8은 이 수식을 바꾸지 않는다. 이전 판이 쓴
# 끝점 CAGR 수식이나 상승률 상수가 남은 사본은 원래 수식으로 되돌린다.
_X01_WAGE_RATE_COLUMNS = (("Y", "AC"), ("Z", "AD"))
_RATE_LITERAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _x01_wage_rate_formula(rate_col: str) -> str:
    return f"AVERAGE({rate_col}26:{rate_col}37)"


def _keep_x01_wage_rates(book, sheet: str, add) -> None:
    """Require the X01 yearly-rate formulas; restore AC38/AD38 to the X01
    arithmetic mean when an earlier patch replaced them."""
    for obs, rate_col in _X01_WAGE_RATE_COLUMNS:
        for row in range(26, 31):
            ref = f"{rate_col}{row}"
            old = book.formulas.get((sheet, ref))
            if old is None or old.text != f"({obs}{row+1}-{obs}{row})/{obs}{row}":
                raise ValueError(f"unreviewed yearly wage rate formula: {ref}")
        cell = f"{rate_col}38"
        desired = _x01_wage_rate_formula(rate_col)
        old = book.formulas.get((sheet, cell))
        if old is None:
            raise ValueError(f"unreviewed wage rate cell: {cell} must keep X01 ={desired}")
        if old.text == desired:
            continue
        legacy_cagr = f"({obs}31/{obs}26)^(1/(X31-X26))-1"
        if old.text != legacy_cagr and not _RATE_LITERAL.fullmatch(old.text):
            raise ValueError(f"unreviewed wage rate formula: {cell}")
        add(sheet, cell, desired)


def _x01_wage_rate_value(book, sheet: str, obs: str) -> float:
    """The value X01 AVERAGE(AC26:AC37) yields over the filled table."""
    yearly = []
    for row in range(26, 31):
        start = book.evaluate(sheet, f"{obs}{row}")
        end = book.evaluate(sheet, f"{obs}{row+1}")
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (start, end))
                or start <= 0):
            raise ValueError("daily wage observations must be filled before rate application")
        yearly.append((end - start) / start)
    return sum(yearly) / len(yearly)


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
        _keep_x01_wage_rates(book, sheet, add)
    rates = {}
    if assumptions is not None:
        if not isinstance(assumptions, dict) or set(assumptions) - {"writing_year", "wage_male", "wage_female", "sales", "general", "price_sources"}:
            raise ValueError("unknown D8 assumptions")
        for kind, rate_cell in (("wage_male", "AC38"), ("wage_female", "AD38")):
            if isinstance(assumptions.get(kind), dict) and "r" in assumptions[kind]:
                raise ValueError(
                    f"{kind}.r: 임금 상승률은 X01 {rate_cell} 산술평균 수식"
                    "(연도별 상승률 평균)이 계산한다 — 직접 입력 불가")
            rates[kind] = _official_rate(assumptions.get(kind), kind, price_sources=assumptions.get("price_sources", []))
        sales = assumptions.get("sales")
        from gg_price_assumptions import normalize_price_role, footnote
        if isinstance(sales, dict) and (sales.get("mode") == "not_applied" or sales.get("status") == "not_applied"):
            from gg_price_assumptions import SalesPriceHeld
            raise SalesPriceHeld("not_applied는 판매가 계산 우회 불가",
                                 writing_year=assumptions.get("writing_year"), raw=sales)
        rates["sales"] = normalize_price_role(
            {"writing_year": assumptions.get("writing_year"),
             "price_assumptions": {"sales": sales},
             "price_sources": assumptions.get("price_sources", [])},
            "sales", [sales.get("application_base_year")] if isinstance(sales, dict) else [None])
        rates["sales"]["r"] = rates["sales"]["rate"]
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
            # The applied rate is the template's own arithmetic mean over the
            # filled observation table; the declared series is validated for
            # source, gender and year only.  The daily-wage anchor can precede
            # the application base by more than four years; validate its
            # actual cumulative factor.
            applied = _x01_wage_rate_value(book, sheet, col)
            multiplier({"role": "wage", "status": "applied", "rate": applied,
                        "application_base_year": int(daily_base_year)}, int(first_year) + 4)
            item.pop("r", None)
            item["rate_method"] = "x01_arithmetic_mean_of_yearly_rates"
            item["rate_formula"] = _x01_wage_rate_formula(rate_cell[:2])
            item["applied_rate"] = applied
            item["daily_base_year"] = daily_base_year
            item["daily_base_application_count"] = int(count)
            add(sheet, col+"34", f"{col}31*(1+${rate_cell[:2]}$38)^{int(count)}")
        _keep_x01_wage_rates(book, sheet, add)
        sales_sheet = spec["sales_sheet"]
        if sales_sheet not in book.sheets:
            raise ValueError("sales sheet missing")
        item = rates["sales"]
        anchors = item.get("anchors", {})
        allowed_anchors = set(spec["sales_rows"].values())
        if set(anchors) - allowed_anchors:
            raise ValueError("D8 sales anchors must use O7/O8/O9/O11/O12/O13")
        # Cell names alone do not identify a price condition: an entire
        # normalized record can move together with its own target_dimensions.
        # Check resolved source dimensions against the fixed X01 row contract
        # and any existing common plan conditions before adding price formulas.
        from gg_price_assumptions import canonical_dimensions, check_dimensions
        common_target = canonical_dimensions(sales.get("target_dimensions"))
        for price, anchor in anchors.items():
            origin = f"D8 sales anchor {price}"
            check_dimensions(anchor, common_target, origin=origin)
            check_dimensions(anchor, spec["sales_anchor_dimensions"][price],
                             required=("channel", "grade", "unit"), origin=origin)
        sales_first_year = book.evaluate(sales_sheet, "C36")
        if isinstance(sales_first_year, str) and re.fullmatch(r"[0-9]{4}년?", sales_first_year.strip()):
            sales_first_year = int(sales_first_year.strip().rstrip("년"))
        if item.get("mode") != "not_applied" and item["application_base_year"] != sales_first_year:
            raise ValueError("sales application year differs from preserved C36")
        if item.get("mode") != "not_applied":
            item["r_literal"] = _excel_number(item["r"])
        # Repoint from the exact quantity cell, never multiply an already
        # escalated revenue. This makes rate updates idempotent.
        for row, price in spec["sales_rows"].items():
            quantities = [book.evaluate(sales_sheet, c+row) for c in "CEGIK"]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in quantities):
                raise ValueError(f"D8 sales quantity unavailable: {row}")
            active = any(v > 0 for v in quantities)
            if active:
                if price not in anchors:
                    raise ValueError(f"D8 active sales anchor needs five-year prices: {price}")
                a = anchors[price]
                if a["unit"] != "원/kg":
                    raise ValueError("D8 sales anchor requires 원/kg")
                observed = [_excel_number(o["value"]) for o in a["price_observations"]]
                # Replace teaching averages/direct prices with the verified condition.
                add(sales_sheet, price, "(" + "+".join(observed) + ")/5")
            for i, (quantity, revenue) in enumerate(zip("CEGIK", "DFHJL")):
                base = f"{quantity}{row}*${price[0]}${price[1:]}/1000"
                old = book.formulas.get((sales_sheet, revenue+row))
                if old is None or not (old.text == base or re.fullmatch(re.escape(base) + r"\*\(1\+[0-9.eE+\-]+\)\^[0-4]", old.text)):
                    raise ValueError(f"unreviewed sales formula: {revenue}{row}")
                if item.get("mode") == "not_applied":
                    desired = base
                else:
                    desired = base + f"*(1+{item['r_literal']})^{i}"
                add(sales_sheet, revenue+row, desired)
        note = footnote(item)
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
            rates["general"]["r_literal"] = _excel_number(rates["general"]["r"])
            for (s, ref), old in book.formulas.items():
                match = re.fullmatch(r"([A-Z]+[1-9][0-9]*)\*(?:1\.025|\(1\+[0-9.eE+\-]+\))", old.text)
                if s in {"9 .경비계획", "7. 영농자재소요계획"} and match:
                    add(s, ref, match[1] + "*(1+" + rates["general"]["r_literal"] + ")")
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


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_CALC_CHAIN = "xl/calcChain.xml"

def _drop_calcchain(modified: dict[str, bytes], zin: zipfile.ZipFile) -> list[str]:
    """Remove the calcChain part and every reference to it.

    ``prepare_wage_input`` deletes a formula node.  A calcChain that still
    lists that cell is an OOXML inconsistency (Excel's repair prompt), so the
    chain is dropped as a whole — Excel rebuilds it on the next calculation.
    """
    if _CALC_CHAIN not in zin.namelist():
        return []
    dropped = [_CALC_CHAIN]
    rels_name = "xl/_rels/workbook.xml.rels"
    if rels_name in zin.namelist():
        rels = _serialize_dom(modified.get(rels_name) or zin.read(rels_name), rels_name)
        for rel in list(rels.getElementsByTagNameNS(_REL_NS, "Relationship")):
            if (rel.getAttribute("Type") or "").endswith("/calcChain"):
                rel.parentNode.removeChild(rel)
        modified[rels_name] = rels.toxml(encoding="utf-8")
    types_name = "[Content_Types].xml"
    if types_name in zin.namelist():
        types = _serialize_dom(modified.get(types_name) or zin.read(types_name), types_name)
        for override in list(types.getElementsByTagNameNS(_CT_NS, "Override")):
            if override.getAttribute("PartName") == "/" + _CALC_CHAIN:
                override.parentNode.removeChild(override)
        modified[types_name] = types.toxml(encoding="utf-8")
    return dropped

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
        cleared_formulas = 0
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
                    cleared_formulas += 1
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

        dropped: list[str] = []
        if cleared_formulas:
            dropped = _drop_calcchain(modified, zin)
            if dropped:
                receipt["calcChainRemoved"] = True

        if _authorization is not None:
            import gg_major_contract as mc
            mc.reconfirm_output(_authorization, _context)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename in dropped:
                    continue
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
