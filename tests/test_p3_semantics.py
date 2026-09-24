"""P3 S-lane contract tests for gg_fact_semantics.py (design-r2 sections 2-6.2).

Covers: METADATA_INVALID, MEASURE_CONFLICT, ROLE_CONFLICT, BINDING_CONFLICT,
SEMANTIC_UNRESOLVED, PARAMETER_UNRESOLVED, HISTORY_INVALID,
SOURCE_APPROVAL_UNRESOLVED, ASSUMPTION_APPROVAL_REQUIRED,
OUTPUT_CONTRACT_INVALID (NPV/BC/IRR/payback), REGISTRY_STALE,
OBSERVATION_BINDING_INVALID, and price-history channel states.
"""
import unittest

from tests._harness import ContractCase, runtime


def semantics():
    return runtime("gg_fact_semantics")


REGISTRY = {
    "version": "1", "sha256": "abc123",
    "meanings": {"sales.revenue": {}},
    "consumer_bindings": [
        {"source_id": "spec-1", "consumer_id": "G-SCH-spec:/x", "pointer": "/0",
         "fact_ids": ["f1"], "use": "parameter"},
    ],
}


class MetadataValidationTests(ContractCase):
    def test_no_metadata_is_silently_legacy(self):
        gs = semantics()
        self.assertEqual([], gs.validate_metadata(
            {"id": "f1"}, registry=REGISTRY, mode="legacy"))

    def test_bad_measure_kind_rejected(self):
        gs = semantics()
        issues = gs.validate_metadata(
            {"id": "f1", "measure": {"kind": "bogus"}},
            registry=REGISTRY, mode="new")
        self.assertTrue(any(i["code"] == "METADATA_INVALID" for i in issues))

    def test_unit_price_bad_denominator_rejected(self):
        gs = semantics()
        issues = gs.validate_metadata(
            {"id": "f1", "measure": {"kind": "unit_price", "denominator": "L"}},
            registry=REGISTRY, mode="new")
        self.assertTrue(any(i["code"] == "METADATA_INVALID" for i in issues))

    def test_finance_role_must_be_plan_or_context(self):
        gs = semantics()
        issues = gs.validate_metadata(
            {"id": "f1", "finance_role": "authority"},
            registry=REGISTRY, mode="new")
        self.assertTrue(any(i["code"] == "METADATA_INVALID" for i in issues))

    def test_currency_must_be_krw(self):
        gs = semantics()
        issues = gs.validate_metadata(
            {"id": "f1", "measure": {"kind": "monetary_total", "currency": "USD"}},
            registry=REGISTRY, mode="new")
        self.assertTrue(any(i["code"] == "METADATA_INVALID" for i in issues))


class ClassifyFactTests(ContractCase):
    def test_legacy_nonfinancial_unit_resolves(self):
        gs = semantics()
        result = gs.classify_fact(
            {"id": "f1", "unit": "kg", "value": "10"},
            registry=REGISTRY, bindings=[])
        self.assertEqual("resolved", result["status"])

    def test_legacy_ambiguous_unit_semantic_unresolved(self):
        gs = semantics()
        result = gs.classify_fact(
            {"id": "f1", "unit": "widgets", "value": "10"},
            registry=REGISTRY, bindings=[])
        self.assertEqual("unresolved", result["status"])
        self.assertTrue(any(
            i["code"] == "SEMANTIC_UNRESOLVED" for i in result["issues"]))

    def test_money_fact_declared_text_is_role_conflict(self):
        gs = semantics()
        result = gs.classify_fact(
            {"id": "f1", "measure": {"kind": "monetary_total"},
             "value_type": "text", "value": "1000"},
            registry=REGISTRY, bindings=[])
        self.assertEqual("invalid", result["status"])
        self.assertTrue(any(
            i["code"] == "ROLE_CONFLICT" for i in result["issues"]))

    def test_unit_mismatch_is_measure_conflict(self):
        gs = semantics()
        result = gs.classify_fact(
            {"id": "f1", "measure": {"kind": "monetary_total", "unit": "원"},
             "unit": "천원", "value": "1000"},
            registry=REGISTRY, bindings=[])
        self.assertTrue(any(
            i["code"] == "MEASURE_CONFLICT" for i in result["issues"]))

    def test_resolved_money_fact_normalizes_value(self):
        gs = semantics()
        result = gs.classify_fact(
            {"id": "f1", "measure": {"kind": "monetary_total"}, "value": "1,000"},
            registry=REGISTRY, bindings=[])
        self.assertEqual("resolved", result["status"])
        self.assertEqual("1000", result["normalized_value"])


class PriceHistoryTests(ContractCase):
    def test_absent_channel_no_projection(self):
        gs = semantics()
        self.assertEqual(
            "absent", gs.price_history_channel_state(None, None))

    def test_invalid_length_channel(self):
        gs = semantics()
        self.assertEqual(
            "invalid", gs.price_history_channel_state(["1", "2"], [2020, 2021]))

    def test_invalid_nonascending_years(self):
        gs = semantics()
        years = [2020, 2021, 2021, 2023, 2024]
        prices = ["1", "2", "3", "4", "5"]
        self.assertEqual(
            "invalid", gs.price_history_channel_state(prices, years))

    def test_invalid_negative_price(self):
        gs = semantics()
        years = [2020, 2021, 2022, 2023, 2024]
        prices = ["1", "-2", "3", "4", "5"]
        self.assertEqual(
            "invalid", gs.price_history_channel_state(prices, years))

    def test_valid_channel_and_projection_average(self):
        gs = semantics()
        years = [2020, 2021, 2022, 2023, 2024]
        prices = ["100", "200", "300", "400", "500"]
        state = gs.price_history_channel_state(prices, years)
        self.assertEqual("valid", state)
        proj, issues = gs.price_history_projection(
            prices, years, source_id="spec-1", source_revision=1,
            channel="수매", declared_scope="농장 전체")
        self.assertEqual([], issues)
        self.assertEqual("300", proj["average_price"])
        self.assertEqual(5, len(proj["history_pairs"]))
        self.assertEqual(2020, proj["history_pairs"][0]["year"])

    def test_invalid_channel_returns_history_invalid_no_fallback(self):
        gs = semantics()
        proj, issues = gs.price_history_projection(
            ["bad"], [2020], source_id="spec-1", source_revision=1,
            channel="수매", declared_scope="농장 전체")
        self.assertIsNone(proj)
        self.assertTrue(any(i["code"] == "HISTORY_INVALID" for i in issues))

    def test_two_channels_independent_invalid_does_not_mask_valid(self):
        gs = semantics()
        years = [2020, 2021, 2022, 2023, 2024]
        good_prices = ["100", "200", "300", "400", "500"]
        purchase_state = gs.price_history_channel_state(good_prices, years)
        direct_state = gs.price_history_channel_state(["bad"], [2020])
        self.assertEqual("valid", purchase_state)
        self.assertEqual("invalid", direct_state)


class SourceApprovalLookupTests(ContractCase):
    def _evidence(self, covered=True):
        review = {
            "review_kind": "content", "status": "pass",
            "disposition": "resolved", "findings": [],
            "coverage": [{"source_id": "s1", "source_revision": 1,
                          "claim_id": "c1", "locator": "p.1"}] if covered else [],
        }
        return {
            "sources": {"s1": {"actual_sha256": "abc", "claim_review": "r1"}},
            "reviews": {"r1": review},
            "approvals": {},
        }

    def test_provided_with_approved_source_user_answer(self):
        gs = semantics()
        fact = {
            "id": "f1", "answer_state": "provided",
            "provenance_kind": "user_answer",
            "source_refs": [{"id": "s1", "revision": 1, "claim_id": "c1",
                             "locator": "p.1"}],
        }
        state = gs.lookup_input_state(
            fact, project={}, evidence=self._evidence(), consumer_ids=[])
        self.assertEqual("user_answer", state["state"])
        self.assertTrue(state["usable"])

    def test_provided_with_approved_source_research(self):
        gs = semantics()
        fact = {
            "id": "f1", "answer_state": "provided",
            "provenance_kind": "research",
            "source_refs": [{"id": "s1", "revision": 1, "claim_id": "c1",
                             "locator": "p.1"}],
        }
        state = gs.lookup_input_state(
            fact, project={}, evidence=self._evidence(), consumer_ids=[])
        self.assertEqual("research", state["state"])
        self.assertTrue(state["usable"])

    def test_provided_without_approval_is_source_approval_unresolved(self):
        gs = semantics()
        fact = {
            "id": "f1", "answer_state": "provided",
            "source_refs": [{"id": "s1", "revision": 1, "claim_id": "c1",
                             "locator": "p.1"}],
        }
        state = gs.lookup_input_state(
            fact, project={}, evidence=self._evidence(covered=False),
            consumer_ids=[])
        self.assertEqual("unresolved", state["state"])
        self.assertFalse(state["usable"])
        self.assertTrue(any(
            i["code"] == "SOURCE_APPROVAL_UNRESOLVED" for i in state["issues"]))

    def test_not_applicable_returns_not_usable(self):
        gs = semantics()
        state = gs.lookup_input_state(
            {"id": "f1", "answer_state": "not_applicable"},
            project={}, evidence={}, consumer_ids=[])
        self.assertEqual("not_applicable", state["state"])
        self.assertFalse(state["usable"])

    def test_unknown_state_falls_to_unresolved(self):
        gs = semantics()
        state = gs.lookup_input_state(
            {"id": "f1", "answer_state": "unknown"},
            project={}, evidence={}, consumer_ids=[])
        self.assertEqual("unresolved", state["state"])
        self.assertFalse(state["usable"])


class AssumptionUseApprovalTests(ContractCase):
    def _valid_approval(self, target_id="f1"):
        return {
            "id": "appr-1", "kind": "assumption_use", "status": "confirmed",
            "scope": "fact_economic_use",
            "target_refs": [{"collection": "facts", "id": target_id}],
            "input_revision": 1, "input_fingerprint": "fp1",
            "evidence_path": "evidence/x.json", "evidence_hash": "h1",
            "human_confirmation": {"explicit": True, "confirmed_by": "user"},
            "allowed_consumers": ["G-SCH-spec:/x"],
        }

    def test_valid_assumption_use_resolves(self):
        gs = semantics()
        fact = {"id": "f1", "kind": "assumption",
                "assumption_use_approval": "appr-1", "value": "5"}
        evidence = {"approvals": {"appr-1": self._valid_approval()}}
        state = gs.lookup_input_state(
            fact, project={}, evidence=evidence, consumer_ids=[])
        self.assertEqual("explicit_assumption", state["state"])
        self.assertTrue(state["usable"])

    def test_missing_approval_is_assumption_approval_required(self):
        gs = semantics()
        fact = {"id": "f1", "kind": "assumption", "value": "5"}
        state = gs.lookup_input_state(
            fact, project={}, evidence={"approvals": {}}, consumer_ids=[])
        self.assertEqual("unresolved", state["state"])
        self.assertTrue(any(
            i["code"] == "ASSUMPTION_APPROVAL_REQUIRED"
            for i in state["issues"]))

    def test_self_target_rejected(self):
        gs = semantics()
        approval = self._valid_approval(target_id="f1")
        approval["id"] = "f1"  # approval id equals the fact id it targets
        self.assertFalse(gs._valid_assumption_use(
            approval, fact={"id": "f1"}, project={}))

    def test_wrong_scope_rejected(self):
        gs = semantics()
        approval = self._valid_approval()
        approval["scope"] = "something_else"
        self.assertFalse(gs._valid_assumption_use(
            approval, fact={"id": "f1"}, project={}))

    def test_missing_confirmed_by_rejected(self):
        gs = semantics()
        approval = self._valid_approval()
        approval["human_confirmation"] = {"explicit": True, "confirmed_by": ""}
        self.assertFalse(gs._valid_assumption_use(
            approval, fact={"id": "f1"}, project={}))

    def test_wrong_target_fact_id_rejected(self):
        gs = semantics()
        approval = self._valid_approval(target_id="other-fact")
        self.assertFalse(gs._valid_assumption_use(
            approval, fact={"id": "f1"}, project={}))


class ConsumerBindingConflictTests(ContractCase):
    def test_missing_source_is_parameter_unresolved(self):
        gs = semantics()
        result = gs.resolve_consumers({}, inputs=[], registry=REGISTRY)
        self.assertTrue(any(
            i["code"] == "PARAMETER_UNRESOLVED" for i in result["issues"]))

    def test_present_source_resolves_binding(self):
        gs = semantics()
        inputs = [{"source_id": "spec-1", "source_revision": 1}]
        result = gs.resolve_consumers({}, inputs=inputs, registry=REGISTRY)
        self.assertEqual(1, len(result["bindings"]))
        self.assertEqual("G-SCH-spec:/x", result["bindings"][0]["consumer_id"])

    def test_duplicate_binding_position_is_conflict(self):
        gs = semantics()
        registry = {
            "consumer_bindings": [
                {"source_id": "spec-1", "consumer_id": "c1", "pointer": "/0"},
                {"source_id": "spec-1", "consumer_id": "c1", "pointer": "/0"},
            ],
        }
        inputs = [{"source_id": "spec-1", "source_revision": 1}]
        result = gs.resolve_consumers({}, inputs=inputs, registry=registry)
        self.assertTrue(any(
            i["code"] == "BINDING_CONFLICT" for i in result["issues"]))


class ResultShapeTests(ContractCase):
    def _base(self):
        return {
            "investment": {
                "npv": "1000", "bc": "1.5", "bc_status": "calculated",
                "irr": "0.1", "irr_status": "calculated", "payback": "3",
            },
        }

    def test_valid_shape_passes(self):
        gs = semantics()
        issues = gs.validate_result_shape(self._base(), profile=None, registry=None)
        self.assertEqual([], issues)

    def test_null_npv_rejected(self):
        gs = semantics()
        result = self._base()
        result["investment"]["npv"] = None
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertTrue(any(i["code"] == "OUTPUT_CONTRACT_INVALID" for i in issues))

    def test_bc_null_requires_undefined_zero_cost_status(self):
        gs = semantics()
        result = self._base()
        result["investment"]["bc"] = None
        result["investment"]["bc_status"] = "calculated"
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertTrue(any(i["code"] == "OUTPUT_CONTRACT_INVALID" for i in issues))

    def test_bc_null_undefined_zero_cost_ok(self):
        gs = semantics()
        result = self._base()
        result["investment"]["bc"] = None
        result["investment"]["bc_status"] = "undefined_zero_cost"
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertEqual([], issues)

    def test_irr_null_wrong_status_rejected(self):
        gs = semantics()
        result = self._base()
        result["investment"]["irr"] = None
        result["investment"]["irr_status"] = "calculated"
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertTrue(any(i["code"] == "OUTPUT_CONTRACT_INVALID" for i in issues))

    def test_irr_null_nonconventional_ok(self):
        gs = semantics()
        result = self._base()
        result["investment"]["irr"] = None
        result["investment"]["irr_status"] = "nonconventional_multiple_or_undefined"
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertEqual([], issues)

    def test_payback_null_is_ok(self):
        gs = semantics()
        result = self._base()
        result["investment"]["payback"] = None
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertEqual([], issues)

    def test_missing_investment_block_rejected(self):
        gs = semantics()
        issues = gs.validate_result_shape({}, profile=None, registry=None)
        self.assertTrue(any(i["code"] == "OUTPUT_CONTRACT_INVALID" for i in issues))

    def test_nan_npv_rejected(self):
        gs = semantics()
        result = self._base()
        result["investment"]["npv"] = "NaN"
        issues = gs.validate_result_shape(result, profile=None, registry=None)
        self.assertTrue(any(i["code"] == "OUTPUT_CONTRACT_INVALID" for i in issues))


class ReportFreshnessTests(ContractCase):
    def _fresh_pair(self):
        report = {
            "semantics": {
                "registry": {"version": "1", "sha256": "abc"},
                "target_refs": [{"collection": "facts", "id": "f1"}],
                "input_fingerprint": "fp1",
                "input_revision": 3,
            },
        }
        current = {
            "registry": {"version": "1", "sha256": "abc"},
            "target_refs": [{"collection": "facts", "id": "f1"}],
            "input_fingerprint": "fp1",
            "observation_report_path": "r.md",
            "review_path": "r.md",
            "observation_input_revision": 3,
            "review_input_revision": 3,
            "review_status": "pass",
        }
        return report, current

    def test_fresh_report_has_no_issues(self):
        gs = semantics()
        report, current = self._fresh_pair()
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertEqual([], issues)

    def test_registry_version_change_is_stale(self):
        gs = semantics()
        report, current = self._fresh_pair()
        current["registry"]["version"] = "2"
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(i["code"] == "REGISTRY_STALE" for i in issues))

    def test_report_path_mismatch_is_observation_binding_invalid(self):
        gs = semantics()
        report, current = self._fresh_pair()
        current["review_path"] = "other.md"
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(
            i["code"] == "OBSERVATION_BINDING_INVALID" for i in issues))

    def test_input_revision_triple_mismatch_is_binding_invalid(self):
        gs = semantics()
        report, current = self._fresh_pair()
        current["review_input_revision"] = 4
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(
            i["code"] == "OBSERVATION_BINDING_INVALID" for i in issues))

    def test_review_status_not_pass_is_stale(self):
        gs = semantics()
        report, current = self._fresh_pair()
        current["review_status"] = "revise"
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(i["code"] == "REGISTRY_STALE" for i in issues))

    def test_review_author_mismatch_is_stale_when_current_supplies_author(self):
        gs = semantics()
        report, current = self._fresh_pair()
        report["review"] = {"author_id": "alice", "reviewer_id": "bob",
                            "review_kinds": ["content"]}
        current["review_author_id"] = "alice"
        current["review_reviewer_id"] = "bob"
        current["review_kinds"] = ["content"]
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertEqual([], issues)
        current["review_author_id"] = "mallory"
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(i["code"] == "REGISTRY_STALE" for i in issues))

    def test_review_reviewer_mismatch_is_stale(self):
        gs = semantics()
        report, current = self._fresh_pair()
        report["review"] = {"author_id": "alice", "reviewer_id": "bob",
                            "review_kinds": ["content"]}
        current["review_author_id"] = "alice"
        current["review_reviewer_id"] = "mallory"
        current["review_kinds"] = ["content"]
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(i["code"] == "REGISTRY_STALE" for i in issues))

    def test_review_kinds_mismatch_is_stale(self):
        gs = semantics()
        report, current = self._fresh_pair()
        report["review"] = {"author_id": "alice", "reviewer_id": "bob",
                            "review_kinds": ["content"]}
        current["review_author_id"] = "alice"
        current["review_reviewer_id"] = "bob"
        current["review_kinds"] = ["content", "logic"]
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertTrue(any(i["code"] == "REGISTRY_STALE" for i in issues))

    def test_review_identity_check_skipped_when_current_omits_it(self):
        # backward-compat: callers not yet passing review identity in
        # `current` (pre-reconciliation call sites) must not spuriously fail.
        gs = semantics()
        report, current = self._fresh_pair()
        report["review"] = {"author_id": "alice"}
        issues = gs.validate_report_binding(report, registry=None, current=current)
        self.assertEqual([], issues)


class BuildCheckSetTests(ContractCase):
    def test_check_set_aggregates_facts_and_issues(self):
        gs = semantics()
        facts = [
            {"fact_id": "f1", "status": "resolved", "finance_role": "plan",
             "issues": []},
            {"fact_id": "f2", "status": "unresolved", "finance_role": None,
             "issues": [{"code": "SEMANTIC_UNRESOLVED", "status": "blocked"}]},
        ]
        cs = gs.build_check_set({}, facts=facts, consumers={}, registry=REGISTRY)
        self.assertIn("f1", cs["required_ids"])
        self.assertEqual(1, len(cs["issues"]))
        self.assertEqual("fallback", cs["mode"])


if __name__ == "__main__":
    unittest.main()
