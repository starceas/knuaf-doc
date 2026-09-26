"""Conservative common-axis review of an industrial-insects source receipt."""

from decimal import Decimal, InvalidOperation
import hashlib
import json

import gg_insect_source_receipt as source_receipt
import gg_major_contract as contract

# Comparison relations the receipt may emit.  The evidence path trusts no
# fixed conflict value: each row's conflict axis is derived from the receipt's
# own relation (DESIGN 6.2, audit finding #5).
_SALES_RELATIONS = (
    "exact_numeric_match",
    "display_rounding_consistent",
    "unresolved_numeric_difference",
)
_COUNT_RELATIONS = ("exact_numeric_match", "source_conflict")

# Owner decision 2026-09-26 (4): the farm_count row cites the published R0
# figure, and this fixed note is the only place the differing R1 figure may
# appear in the package (DESIGN-B2 §4, D-6).  The conflict stays unresolved.
FARM_COUNT_CITATION_NOTE = "원자료 엑셀 2,394, 원인 미확인"


def review_sources(registry, project, pdf_path, xlsx_path):
    """Reverify pinned source bytes, then classify selected observations.

    The caller cannot supply a detached receipt as evidence.  Every live
    review reparses the two independent pinned originals before axes are
    assigned.  Parser failures are reduced to stable codes without paths.
    """
    binding = contract.binding_from_project(registry, project)
    if binding.major_id != "industrial_insects":
        raise ValueError("insect evidence requires industrial_insects binding")
    module = registry.resolve(binding.major_id)
    try:
        proposal = contract.route(
            registry, binding.major_id, "evidence",
            output="evidence_review")
    except contract.UnsupportedOutputError:
        raise ValueError(
            "insect_output_unsupported:evidence_review") from None
    if proposal.status != "supported":
        raise ValueError("insect_capability_unsupported:evidence")
    try:
        receipt = source_receipt.verify_sources(pdf_path, xlsx_path)
    except source_receipt.ReceiptError as error:
        raise ValueError(
            "source_%s_%s" % (error.source_id, error.reason)
        ) from None
    return _review_verified_receipt(module, project, receipt)


def _conflict_axes(receipt):
    """conflict axis per aggregate, read from the receipt's comparisons."""
    comparisons = receipt.get("comparisons")
    if not isinstance(comparisons, list):
        raise ValueError("receipt comparisons missing")
    by_metric = {}
    for row in comparisons:
        if (not isinstance(row, dict)
                or not isinstance(row.get("metric"), str)
                or not isinstance(row.get("relation"), str)):
            raise ValueError("invalid receipt comparison")
        if row["metric"] in by_metric:
            raise ValueError("duplicate receipt comparison")
        by_metric[row["metric"]] = row["relation"]
    axes = {}
    for aggregate, metric in (("total", "sales_total"),
                              ("cricket", "sales_cricket"),
                              ("fly", "sales_fly")):
        relation = by_metric.get(metric)
        if relation not in _SALES_RELATIONS:
            raise ValueError("invalid receipt comparison")
        axes[aggregate] = ("unresolved"
                           if relation == "unresolved_numeric_difference"
                           else "none")
    count_relation = by_metric.get("reported_farm_count")
    if count_relation not in _COUNT_RELATIONS:
        raise ValueError("invalid receipt comparison")
    axes["farm_count"] = ("unresolved" if count_relation == "source_conflict"
                          else "none")
    return axes


def _review_verified_receipt(module, project, receipt):
    """Classify observations returned directly by the pinned verifier.

    The offline parser verifies selected cells, but its receipt explicitly
    has no visual crosscheck and cannot establish redistribution rights.
    Keeping those axes open prevents a selected aggregate from becoming a
    price, yield, calculated revenue, or publishable benchmark by accident.
    """
    if not isinstance(receipt, dict) or receipt.get("schema") != source_receipt.SCHEMA:
        raise ValueError("unknown insect receipt schema")
    if receipt.get("official_pins") is not True:
        raise ValueError("receipt pins differ from official originals")
    sources = receipt.get("sources") or {}
    if (sources.get("R0", {}).get("sha256") != source_receipt.PDF_SHA256
            or sources.get("R1", {}).get("sha256") != source_receipt.XLSX_SHA256):
        raise ValueError("receipt source hashes differ from pinned originals")
    if (sources["R0"].get("visual_crosscheck") != "not_performed_by_cli"
            or receipt.get("publication") != "not_promoted_to_catalog_or_manifest"
            or receipt.get("survey", {}).get("use_scope") != "industry_context_only"):
        raise ValueError("receipt claims a verification or use outside its scope")
    conflict_axes = _conflict_axes(receipt)
    # Diagnostic passthrough only: the locus describes the farm-count
    # conflict's location; it neither resolves the conflict nor lifts
    # approval (DESIGN 15.2/16.4).
    conflict_locus = receipt.get("conflict_locus")
    if (not isinstance(conflict_locus, dict)
            or conflict_locus.get("status") not in {"extracted", "not_extracted"}
            or conflict_locus.get("cause") != "unresolved"):
        raise ValueError("invalid or missing receipt conflict locus")
    audit_notes = receipt.get("audit_notes") or []
    if not isinstance(audit_notes, list):
        raise ValueError("invalid receipt audit notes")
    observations = receipt.get("observations")
    if not isinstance(observations, list) or len(observations) != 4:
        raise ValueError("expected four selected aggregate observations")
    expected_cells = {"total": "C23", "cricket": "X23",
                      "fly": "U23", "farm_count": "B23"}
    keyed = {}
    for row in observations:
        if not isinstance(row, dict) or not isinstance(row.get("aggregate"), str):
            raise ValueError("invalid aggregate observation")
        key = row["aggregate"]
        if key not in expected_cells or key in keyed:
            raise ValueError("duplicate aggregate observation")
        r0_ref, r1_ref = row.get("R0"), row.get("R1")
        if not isinstance(r0_ref, dict) or not isinstance(r1_ref, dict):
            raise ValueError("invalid aggregate locator")
        if (r0_ref.get("physical_page") != 4
                or r0_ref.get("table") != "참고 2 연도별 주요 통계"
                or r1_ref.get("sheet") != source_receipt.SHEET
                or r1_ref.get("cell") != expected_cells[key]
                or row.get("unit") != (
                    "count" if key == "farm_count" else "KRW_million"
                )):
            raise ValueError("unexpected aggregate locator or unit")
        try:
            r0 = Decimal(row["R0"]["value"])
            r1 = Decimal(row["R1"]["value"])
        except (KeyError, TypeError, InvalidOperation) as error:
            raise ValueError("invalid aggregate value") from error
        if not r0.is_finite() or not r1.is_finite() or r0 < 0 or r1 < 0:
            raise ValueError("invalid aggregate value")
        keyed[key] = row
    if set(keyed) != {"total", "cricket", "fly", "farm_count"}:
        raise ValueError("unexpected aggregate selection")
    selected = {
        source_id: {
            ("farm_count" if key == "farm_count" else "sales_" + key):
                row[source_id]["value"]
            for key, row in keyed.items()
        }
        for source_id in ("R0", "R1")
    }
    digest = hashlib.sha256(json.dumps(
        selected, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if receipt.get("selection_sha256") != digest:
        raise ValueError("selected values differ from receipt checksum")
    required_axes = tuple(
        module.evidence_applicability.get("required_axes", ()))
    results = []
    for key in ("total", "cricket", "fly", "farm_count"):
        row = keyed[key]
        # The R0 fly label is 아메리카동애등에, while R1 says 동애등에.
        applicability = "unverified" if key == "fly" else "applicable"
        axes = contract.evidence_axes(
            acquisition="bytes_held",
            observation="exploratory",
            rights="unconfirmed",
            conflict=conflict_axes[key],
            applicability=applicability,
            approval="unapproved",
        )
        missing = [axis for axis in required_axes if axis not in axes]
        if missing:
            raise ValueError("module requires evidence axes not produced")
        results.append({
            "aggregate": key,
            "use_scope": "industry_context",
            "axes": dict(axes),
            "promotion_blockers": list(contract.promotion_blockers(axes)),
            "approval_candidate": contract.approval_candidate(axes),
            **({"conflict_locus": conflict_locus,
                "citation": {"value": "2393", "source_id": "R0",
                             "note": FARM_COUNT_CITATION_NOTE}}
               if key == "farm_count" else {}),
            "source_refs": [
                {"id": "R0", "physical_page": row["R0"]["physical_page"],
                 "table": row["R0"]["table"]},
                {"id": "R1", "sheet": row["R1"]["sheet"],
                 "cell": row["R1"]["cell"]},
            ],
        })
    return {
        "major_id": module.major_id,
        "project_revision": project.get("revision"),
        "receipt_selection_sha256": receipt.get("selection_sha256"),
        "status": "review_only",
        "observations": results,
        "canonical_write": False,
        "pack_status": "empty_slot",
        "audit_notes": audit_notes,
    }
