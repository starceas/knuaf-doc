"""P3 C-lane: gg_finance external validation adapter tests (design-r2 §5/§9).

Covers the additive ``context`` kwarg on ``require_classified_workbook_inputs``
(draft behavior unchanged, admission raises CONTEXT_REQUIRED /
MAP_SLOT_UNRESOLVED) and the new pure ``validate_economic_inputs`` adapter
(fail Issues for native-slot/value/declaration conflicts, blocked Issues for
unconnected provenance, admission additionally blocks unresolved
economic.status entries).  The chosen conflict shape for item 7: a cell's
native slot is its ``economic.meaning_id`` — two clear cells claiming the same
meaning_id with contradictory unit/period declarations conflict.
"""
import unittest

from tests._harness import ContractCase, runtime


def _classified_cell(cell="B2", *, ref="src:answer-1", meaning="sales_amount",
                     unit="원", period="2026년", status="user_answer",
                     value="1000"):
    """A clear numeric workbook cell with a complete economic record."""
    return {
        "cell": cell,
        "map_action": "clear",
        "kind": "number",
        "economic": {
            "status": status,
            "unit": unit,
            "period": period,
            "source_ref": ref,
            "meaning_id": meaning,
            "value": value,
        },
    }


def _classified_workbook(**over):
    wb = {
        "economic_classification": "complete",
        "cells": [_classified_cell()],
    }
    wb.update(over)
    return wb


def _composite_spec(workbook):
    """A fully classified composite spec so composite_contract reaches
    status="calculable" through the workbook_inputs gate."""
    fin = runtime("gg_finance")
    contracts = [
        {
            "crop_id": c,
            "planting_id": "pl-%s" % c,
            "part": "수확부",
            "product_form": "생물",
            "cycle": "1기",
            "zone": "노지",
            "deduplication_note": "중복 없음",
        }
        for c in ("감자", "고구마")
    ]
    inventory = {
        f: {"status": "user_answer", "source_ref": "src:%s" % f,
            "value": "1"}
        for f in fin.COMPOSITE_INPUTS
    }
    return {
        "crops": ["감자", "고구마"],
        "crop_contracts": contracts,
        "input_inventory": inventory,
        "workbook_inputs": workbook,
    }


def _clean_period_spec():
    """A single-crop spec whose one period row is internally consistent —
    the provenance-free surface on which a draft pass finds nothing."""
    return {
        "start_year": 2026,
        "periods": [
            {"year": 2026, "quantity": "10", "sold": "9", "loss": "1",
             "price": "100", "variable_cost": "5", "fixed_cost": "50",
             "household": "0"},
        ],
    }


class FinanceValidationAdapterTests(ContractCase):
    def test_require_classified_context_none_valid_workbook(self):
        """Positive control: context=None on an already-classified
        workbook keeps the legacy draft behavior — no exception."""
        fin = runtime("gg_finance")
        fin.require_classified_workbook_inputs(_classified_workbook())

    def test_require_classified_context_none_unclassified_raises(self):
        """Regression control: context=None on an unclassified workbook
        still raises the existing 미분류 경제 입력 error."""
        fin = runtime("gg_finance")
        bad = _classified_workbook(
            cells=[_classified_cell(status="unresolved", value=None,
                                    ref=""), ],
        )
        # drop the source_ref entirely: declaration incomplete
        bad["cells"][0]["economic"].pop("source_ref")
        with self.assertRaises(ValueError) as cm:
            fin.require_classified_workbook_inputs(bad)
        self.assertIn("미분류 경제 입력", str(cm.exception))

    def test_require_classified_falsy_context_raises_context_required(self):
        """Admission call with context={} (falsy) -> CONTEXT_REQUIRED."""
        fin = runtime("gg_finance")
        with self.assertRaises(ValueError) as cm:
            fin.require_classified_workbook_inputs(
                _classified_workbook(), context={})
        self.assertEqual("CONTEXT_REQUIRED", str(cm.exception))

    def test_require_classified_unresolvable_ref_raises_map_slot(self):
        """A classified cell whose source_ref is absent from
        context['resolved_source_refs'] -> MAP_SLOT_UNRESOLVED."""
        fin = runtime("gg_finance")
        with self.assertRaises(ValueError) as cm:
            fin.require_classified_workbook_inputs(
                _classified_workbook(),
                context={"resolved_source_refs": ["src:other"]})
        self.assertEqual("MAP_SLOT_UNRESOLVED", str(cm.exception))

    def test_require_classified_all_refs_resolved_ok(self):
        """Positive admission control: every cell's source_ref resolvable
        in context -> no exception."""
        fin = runtime("gg_finance")
        fin.require_classified_workbook_inputs(
            _classified_workbook(),
            context={"resolved_source_refs": ["src:answer-1"]})

    def test_validate_draft_clean_spec_returns_empty(self):
        """Draft pass on a consistent, provenance-free spec -> []."""
        fin = runtime("gg_finance")
        self.assertEqual(
            [],
            fin.validate_economic_inputs(
                _clean_period_spec(), purpose="draft"),
        )

    def test_validate_draft_slot_conflict_fails(self):
        """Conflict shape chosen: two clear cells claiming the same
        meaning_id slot with contradictory unit declarations -> a fail
        Issue.  Provenance is resolvable here so the conflict is the only
        thing reported."""
        fin = runtime("gg_finance")
        wb = _classified_workbook(cells=[
            _classified_cell("B2", ref="src:a", unit="원"),
            _classified_cell("B3", ref="src:b", unit="천원"),
        ])
        issues = fin.validate_economic_inputs(
            {"workbook_inputs": wb}, purpose="draft",
            context={"resolved_source_refs": ["src:a", "src:b"]})
        self.assertTrue(issues)
        self.assertTrue(all(i["status"] == "fail" for i in issues))
        self.assertIn("ECONOMIC_SLOT_CONFLICT",
                      {i["code"] for i in issues})
        for i in issues:
            self.assertEqual("finance_review", i["check_id"])
            self.assertEqual("error", i["severity"])

    def test_validate_draft_blocked_provenance_and_admission_blocks(self):
        """An unresolved-status cell with a declared source_ref: draft
        tolerates the status but still reports the unconnected provenance
        as blocked; under admission (with the ref resolvable) the
        unresolved status itself becomes a blocking issue."""
        fin = runtime("gg_finance")
        spec = {"workbook_inputs": _classified_workbook(cells=[
            _classified_cell(status="unresolved", value=None),
        ])}
        draft = fin.validate_economic_inputs(spec, purpose="draft")
        self.assertTrue(draft)
        self.assertTrue(all(i["status"] == "blocked" for i in draft))
        self.assertNotIn("ECONOMIC_STATUS_UNRESOLVED",
                         {i["code"] for i in draft})
        admission = fin.validate_economic_inputs(
            spec, purpose="admission",
            context={"resolved_source_refs": ["src:answer-1"]})
        self.assertTrue(admission)
        self.assertTrue(all(i["status"] == "blocked" for i in admission))
        self.assertIn("ECONOMIC_STATUS_UNRESOLVED",
                      {i["code"] for i in admission})

    def test_validate_admission_empty_context_blocked(self):
        """Defect 3c-1: context={} under admission is not a real context
        — parity with require_classified_workbook_inputs's falsy-or-
        wrong-type check, reported as a blocked CONTEXT_REQUIRED issue
        rather than silently accepted."""
        fin = runtime("gg_finance")
        issues = fin.validate_economic_inputs(
            _clean_period_spec(), purpose="admission", context={})
        self.assertTrue(issues)
        ctx = [i for i in issues if i["code"] == "CONTEXT_REQUIRED"]
        self.assertTrue(ctx)
        self.assertTrue(all(i["status"] == "blocked" for i in ctx))

    def test_validate_heterogeneous_inventory_keys_no_raise(self):
        """Defect 3c-2: mixed str/int input_inventory keys must not
        propagate TypeError from sorted() — both purposes return a list."""
        fin = runtime("gg_finance")
        spec = {"input_inventory": {
            1: {"status": "user_answer", "source_ref": "src:x",
                "value": "1"},
            "site_area": {"status": "user_answer", "source_ref": "src:y",
                          "value": "1"},
        }}
        for purpose in ("draft", "admission"):
            with self.subTest(purpose=purpose):
                result = fin.validate_economic_inputs(
                    spec, purpose=purpose,
                    context={"resolved_source_refs": ["src:y"]})
                self.assertIsInstance(result, list)

    def test_composite_contract_workbook_inputs_unchanged(self):
        """Regression: composite_contract still calls
        require_classified_workbook_inputs with no context and reaches
        calculable on a fully classified composite spec."""
        fin = runtime("gg_finance")
        result = fin.composite_contract(
            _composite_spec(_classified_workbook()))
        self.assertEqual("calculable", result["status"])
        self.assertEqual([], result["missing"])


if __name__ == "__main__":
    unittest.main()
