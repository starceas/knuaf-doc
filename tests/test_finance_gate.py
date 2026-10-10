"""Body-before-finance gate: real canonical mutations and public CLI reads.

Synthetic amounts are copied result fixtures, not claims about a real farm or
native Office. The existing calculator is also exercised without modification.
"""
import copy
import itertools
import json
import subprocess
import sys
import unittest

from tests._harness import (
    ContractCase, SCRIPTS, bind_major, claim_of, fact_op, runtime,
    section_op, source_op, write_text,
)


class BodyFinanceGateTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.root = self.make_project()
        bind_major(self.root)

    def apply(self, ops):
        revision = self.core.load(self.root)["revision"]
        return self.core.apply(self.root, {
            "request_id": "gate-test:%d" % revision, "ops": ops}, revision)

    def body(self, *, status="drafting"):
        write_text(self.root, "intro.md", "# Ⅰ. 영농계획\n본인은 병풀을 재배할 계획이다.\n")
        self.apply([section_op("intro", "Ⅰ. 영농계획", "intro.md", status=status)])

    def record(self, state, profit=None, *, reason=None):
        period = "2030" if profit is not None else None
        facts = [fact_op("gate-state", "finance.gate.state", state, "",
                         scope="project", period=period,
                         verification="claim_supported", source_id="calculation",
                         claim_id="state")]
        if reason is not None:
            facts[0]["value"]["reason"] = reason
        if profit is not None:
            facts.append(fact_op(
                "gate-profit", "finance.gate.target_profit", str(profit), "원",
                value_type="decimal", period=period, scope="annual",
                kind="observation", verification="claim_supported",
                source_id="calculation", claim_id="profit"))
            facts[-1]["value"].update(
                finance_role="context", meaning_id="sales.net_profit",
                measure={"kind": "monetary_total", "currency": "KRW", "unit": "원"})
        claims = {}
        for fact in facts:
            cid = fact["value"]["source_refs"][0]["claim_id"]
            claims.update(claim_of(fact["value"], cid))
        write_text(self.root, "calculation.json", json.dumps({
            "description": "synthetic existing-result fixture",
            "target_year": period, "profit": profit, "reason": reason}, ensure_ascii=False))
        source = source_op("calculation.json", claims=claims)
        source["value"]["id"] = "calculation"
        self.apply([source, *facts])

    def answer(self, value="proceed_with_deficit", *, state="provided",
               question=True, link=True, decision=1, kind="reported_fact"):
        field = "finance.gate.acceptance"
        profit = self.core.load(self.root)["facts"]["gate-profit"]["value"]
        write_text(self.root, "gate-interview.txt",
                   "질문: 연도별 손익 2029=-200, 2030=%s원. 목표 2030년.\n"
                   "근거 있는 규모·판로 조정 후 재검산 / 적자를 알고 진행 / 모름.\n"
                   "학생 답: %s\n" % (profit, value if state == "provided" else state))
        fact = fact_op("gate-answer", field,
                       value if state == "provided" else None, "",
                       scope="project", verification="claim_supported",
                       source_id="gate-interview", claim_id="answer",
                       answer_state=state, kind=kind)
        if state in {"withheld", "explicit_none", "not_applicable"}:
            fact["value"]["reason"] = "학생 원답변 보존"
        if link:
            fact["value"]["question_ref"] = {
                "id": field, "decision_revision": decision}
        source = source_op("gate-interview.txt", claims=claim_of(fact["value"], "answer"))
        source["value"]["id"] = "gate-interview"
        ops = [source, fact]
        if question:
            ops.append({"collection": "questions", "value": {
                "id": field, "field_id": field, "attempts": 1,
                "decision_revision": 1,
                "source_ref": {"id": "gate-interview", "revision": 1,
                               "locator": "lines 1-2"}}})
        self.apply(ops)

    def gate_check(self):
        p = self.core.load(self.root)
        checks = self.core.checks(self.root, p)
        return [c for c in checks if c["check_id"] == "finance_gate"]

    def assert_allowed(self, expected):
        p = self.core.load(self.root)
        self.assertEqual(expected, self.core.finance_gate(p)["state"])
        check = self.gate_check()
        self.assertEqual(1, len(check))
        self.assertEqual("pass", check[0]["status"])
        self.assertEqual([], check[0]["required_for"])
        task = next(t for t in self.core.next_tasks(p) if t["id"] == "finance_gate")
        self.assertEqual("ready", task["status"])

    def assert_blocked(self, expected="deficit_pending"):
        p = self.core.load(self.root)
        self.assertEqual(expected, self.core.finance_gate(p)["state"])
        check = self.gate_check()[0]
        self.assertEqual(("fail", "error", "body"),
                         (check["status"], check["severity"], check["target"]))
        self.assertIn("submission_candidate", check["required_for"])
        self.assertTrue(any(c["check_id"] == "finance_gate"
                            and self.core.blocks_skill_candidate(c)
                            for c in self.core.gate(self.root, p)))
        task = next(t for t in self.core.next_tasks(p) if t["id"] == "finance_gate")
        self.assertEqual("needs_user", task["status"])

    def test_surplus_body_allowed(self):
        self.record("surplus", "100")
        self.body()
        self.assert_allowed("surplus")

    def replace_profit_metadata(self, changes):
        """Register revised metadata through apply with current matching claims."""
        p = self.core.load(self.root)
        facts = [copy.deepcopy(p["facts"][key])
                 for key in ("gate-state", "gate-profit")]
        for key, value in changes.items():
            if value is None:
                facts[1].pop(key, None)
            else:
                facts[1][key] = value
        claims = {}
        for fact in facts:
            ref = fact["source_refs"][0]
            ref["revision"] = p["sources"]["calculation"]["revision"] + 1
            claims.update(claim_of(fact, ref["claim_id"]))
        source = source_op("calculation.json", claims=claims)
        source["value"]["id"] = "calculation"
        self.apply([source, *[{"collection": "facts", "value": f} for f in facts]])
        self.assertIsNotNone(self.core._finance_gate_fact(
            self.core.load(self.root), "finance.gate.target_profit"))

    def test_positive_profit_requires_documented_record_shape(self):
        measures = {"kind": "monetary_total", "currency": "KRW", "unit": "원"}
        variants = [
            {"kind": "assumption"}, {"kind": "target"},
            {"finance_role": "plan"}, {"finance_role": None},
            {"meaning_id": "sales.revenue"}, {"meaning_id": None},
            {"measure": None},
            *[{"measure": {**measures, key: value}} for key, value in (
                ("kind", "quantity"), ("currency", "USD"), ("unit", "천원"))],
            *[{"measure": {k: v for k, v in measures.items() if k != key}}
              for key in measures],
        ]
        for label, changes in itertools.product(("surplus", "not_computable"), variants):
            with self.subTest(label=label, changes=changes):
                self.root = self.make_project()
                bind_major(self.root)
                self.record(label, "100", reason="기록 형식 불일치는 계산 부재가 아님")
                self.replace_profit_metadata(changes)
                self.body()
                self.assert_blocked("missing")

    def test_documented_record_shape_preserves_valid_paths(self):
        for label, profit in (("surplus", "100"), ("deficit_accepted", "-100"),
                              ("not_computable", None)):
            with self.subTest(label=label):
                self.root = self.make_project()
                bind_major(self.root)
                self.record(label, profit, reason="가격 입력 미제공")
                if label == "deficit_accepted":
                    self.answer()
                self.body()
                self.assert_allowed(label)

    def test_documented_measure_allows_existing_optional_metadata(self):
        for extras in ({"denominator": None}, {"basis": None},
                       {"denominator": None, "basis": None}):
            with self.subTest(extras=extras):
                self.root = self.make_project()
                bind_major(self.root)
                self.record("surplus", "100")
                measure = {"unit": "원", "currency": "KRW", "kind": "monetary_total",
                           **extras}
                semantics = runtime("gg_fact_semantics")
                self.assertEqual([], semantics.validate_metadata(
                    {"measure": measure}, registry=None, mode="new"))
                self.replace_profit_metadata({"measure": measure})
                self.body()
                self.assert_allowed("surplus")

    def test_accepted_deficit_body_allowed_and_answer_reused(self):
        self.record("deficit_accepted", "-100")
        self.answer()
        self.body()
        self.assert_allowed("deficit_accepted")
        self.assertEqual("reuse", self.core.question(
            self.core.load(self.root), "finance.gate.acceptance"))

    def test_missing_inputs_allow_non_price_body_only(self):
        self.record("not_computable", reason="수취가격 미제공: 가격 의존 수익 절만 보류")
        self.body()
        self.assert_allowed("not_computable")
        # Existing task dependencies still hold the price-dependent calculation.
        self.apply([{"collection": "tasks", "value": {
            "id": "revenue", "required_fields": ["selling_price"]}}])
        tasks = self.core.next_tasks(self.core.load(self.root))
        self.assertEqual("needs_evidence", next(t["status"] for t in tasks
                                               if t["id"] == "revenue"))

    def test_missing_record_with_body_is_error(self):
        self.body()
        self.assert_blocked("missing")

    def test_empty_project_needs_gate_but_no_body_error(self):
        self.assertEqual([], self.gate_check())
        self.assertEqual("needs_user", next(t["status"] for t in
                         self.core.next_tasks(self.core.load(self.root))
                         if t["id"] == "finance_gate"))

    def test_deficit_without_answer_is_error(self):
        self.record("deficit_pending", "-100")
        self.body()
        self.assert_blocked()

    def test_accepted_label_without_answer_cannot_pass(self):
        self.record("deficit_accepted", "-100")
        self.body()
        self.assert_blocked()

    def test_unknown_cannot_pass_and_question_budget_is_preserved(self):
        self.record("deficit_pending", "-100")
        self.answer(state="unknown")
        self.body()
        self.assert_blocked()
        p = self.core.load(self.root)
        self.assertEqual("help", self.core.finance_gate(p)["question"])
        q = dict(p["questions"]["finance.gate.acceptance"], attempts=2)
        self.apply([{"collection": "questions", "value": q}])
        self.assertEqual("deferred", self.core.finance_gate(self.core.load(self.root))["question"])

    def test_withheld_is_not_acceptance_and_is_not_reasked(self):
        self.record("deficit_pending", "-100")
        self.answer(state="withheld")
        self.body()
        self.assert_blocked()
        self.assertEqual("reuse", self.core.finance_gate(self.core.load(self.root))["question"])

    def test_refusal_is_not_acceptance(self):
        self.record("deficit_pending", "-100")
        self.answer("adjust_and_recalculate")
        self.body()
        self.assert_blocked()

    def test_provided_unknown_text_is_not_acceptance(self):
        self.record("deficit_pending", "-100")
        self.answer("모름")
        self.body()
        self.assert_blocked()

    def test_zero_target_profit_requires_acceptance(self):
        self.record("surplus", "0")
        self.body()
        self.assert_blocked()

    def test_surplus_label_cannot_hide_deficit(self):
        self.record("surplus", "-100")
        self.body()
        self.assert_blocked()

    def test_not_computable_label_cannot_hide_valid_deficit(self):
        self.record("not_computable", "-100", reason="가격 부족이라고 잘못 기록")
        self.body()
        self.assert_blocked()

    def test_not_computable_without_reason_cannot_pass(self):
        self.record("not_computable")
        self.body()
        self.assert_blocked("missing")

    def test_answer_without_question_cannot_pass(self):
        self.record("deficit_accepted", "-100")
        self.answer(question=False)
        self.body()
        self.assert_blocked()

    def test_answer_without_question_link_cannot_pass(self):
        self.record("deficit_accepted", "-100")
        self.answer(link=False)
        self.body()
        self.assert_blocked()

    def test_question_decision_mismatch_cannot_pass(self):
        self.record("deficit_accepted", "-100")
        self.answer(decision=2)
        self.body()
        self.assert_blocked()

    def test_assistant_assumption_cannot_be_student_acceptance(self):
        self.record("deficit_accepted", "-100")
        self.answer(kind="assumption")
        self.body()
        self.assert_blocked()

    def test_unreviewed_or_claim_mismatched_acceptance_cannot_pass(self):
        self.record("deficit_accepted", "-100")
        self.answer()
        p = self.core.load(self.root)
        for mutation in ("unreviewed", "mismatch"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(p)
                if mutation == "unreviewed":
                    bad["facts"]["gate-answer"]["verification"] = "source_located"
                else:
                    bad["sources"]["gate-interview"]["claims"]["answer"]["value"] = "모름"
                self.assertEqual("deficit_pending", self.core.finance_gate(bad)["state"])

    def test_duplicate_gate_fields_do_not_pass(self):
        self.record("surplus", "100")
        self.body()
        p = self.core.load(self.root)
        duplicate = dict(p["facts"]["gate-state"], id="gate-state-duplicate")
        self.apply([{"collection": "facts", "value": duplicate}])
        self.assert_blocked("missing")

    def test_duplicate_negative_profit_is_not_missing_calculation(self):
        self.record("not_computable", "-100", reason="수취가격 부족")
        self.body()
        duplicate = copy.deepcopy(self.core.load(self.root)["facts"]["gate-profit"])
        duplicate["id"] = "duplicate-profit"
        self.apply([{"collection": "facts", "value": duplicate}])
        self.assert_blocked("missing")

    def test_all_duplicate_gate_field_combinations(self):
        fields = ("gate-state", "gate-profit", "gate-answer")
        combinations = [subset for size in range(1, 4)
                        for subset in itertools.combinations(fields, size)]
        for state, profit in itertools.product(
                ("surplus", "deficit_pending", "deficit_accepted", "not_computable"),
                ("-100", "0", "100")):
            self.root = self.make_project()
            bind_major(self.root)
            self.record(state, profit, reason="수취가격 부족")
            self.answer()
            self.body()
            p = self.core.load(self.root)
            for subset in combinations:
                with self.subTest(state=state, profit=profit, duplicated=subset):
                    bad = copy.deepcopy(p)
                    for field in subset:
                        duplicate = copy.deepcopy(bad["facts"][field])
                        duplicate["id"] += "-duplicate"
                        bad["facts"][duplicate["id"]] = duplicate
                    if "gate-state" in subset or "gate-profit" in subset:
                        expected = "missing"
                    elif profit == "100":
                        expected = "surplus"  # A surplus needs no acceptance.
                    else:
                        expected = "deficit_pending"
                    self.assertEqual(expected, self.core.finance_gate(bad)["state"])
                    check = next(c for c in self.core.checks(self.root, bad)
                                 if c["check_id"] == "finance_gate")
                    task = next(t for t in self.core.next_tasks(bad)
                                if t["id"] == "finance_gate")
                    if expected == "surplus":
                        self.assertEqual(("pass", "ready"),
                                         (check["status"], task["status"]))
                    else:
                        self.assertEqual(("fail", "error", "needs_user"),
                                         (check["status"], check["severity"], task["status"]))
                        self.assertIn("submission_candidate", check["required_for"])

    def test_invalid_gate_bindings_are_not_absent_records(self):
        for state in ("surplus", "deficit_accepted", "not_computable"):
            self.root = self.make_project()
            bind_major(self.root)
            self.record(state, "-100", reason="수취가격 부족")
            self.answer()
            self.body()
            p = self.core.load(self.root)
            for field, mutation in itertools.product(
                    ("gate-state", "gate-profit", "gate-answer"),
                    ("unreviewed", "no_refs", "missing_source", "stale_revision",
                     "missing_claim", "claim_mismatch", "no_claim_review")):
                with self.subTest(state=state, field=field, mutation=mutation):
                    bad = copy.deepcopy(p)
                    fact = bad["facts"][field]
                    ref = fact["source_refs"][0]
                    source = bad["sources"][ref["id"]]
                    if mutation == "unreviewed":
                        fact["verification"] = "source_located"
                    elif mutation == "no_refs":
                        fact["source_refs"] = []
                    elif mutation == "missing_source":
                        ref["id"] = "missing-source"
                    elif mutation == "stale_revision":
                        ref["revision"] += 1
                    elif mutation == "missing_claim":
                        ref["claim_id"] = "missing-claim"
                    elif mutation == "claim_mismatch":
                        source["claims"][ref["claim_id"]]["value"] = "mismatched"
                    else:
                        source["claim_review"] = None
                    expected = "deficit_pending" if field == "gate-answer" else "missing"
                    self.assertEqual(expected, self.core.finance_gate(bad)["state"])

    def test_wrong_profit_period_or_unit_does_not_pass(self):
        self.record("surplus", "100")
        p = self.core.load(self.root)
        for key, value in (("period", "2029"), ("unit", "천원"), ("scope", "monthly")):
            with self.subTest(key=key):
                bad = copy.deepcopy(p)
                bad["facts"]["gate-profit"][key] = value
                bad["sources"]["calculation"]["claims"]["profit"][key] = value
                self.assertEqual("missing", self.core.finance_gate(bad)["state"])

    def test_unsupported_finance_majors_are_nonblocking(self):
        for major in ("fruit_trees", "industrial_insects"):
            with self.subTest(major=major):
                self.root = self.make_project()
                bind_major(self.root, major)
                self.body()
                self.assertEqual([], self.gate_check())
                self.assertFalse(any(t["id"] == "finance_gate" for t in
                                     self.core.next_tasks(self.core.load(self.root))))

    def test_supported_hort_major_also_requires_record(self):
        self.root = self.make_project()
        bind_major(self.root, "hort_env_systems")
        self.body()
        self.assert_blocked("missing")

    def test_first_year_deficit_target_year_surplus_reads_existing_calculator(self):
        finance = runtime("gg_finance")
        mc = runtime("gg_major_contract")
        spec = {
            "major_id": "specialty_crops", "profile": "single_annual_cash_v1",
            "crops": ["합성작목"], "accounting_basis": "cash_pre_tax_no_inventory",
            "source_refs": ["synthetic:gate"], "unit": "원", "quantity_unit": "kg",
            "investment_basis": "school_farm_new_business", "owner_labor_in_costs": False,
            "repayment": "equal_principal",
            "start_year": 2029, "years": 2, "life": 2, "grace": 0, "term": 2,
            "land": "0", "facility": "100", "equity": "200", "loan": "0", "salvage": "0",
            "discount_rate": "0.03", "loan_rate": "0",
            "periods": [
                {"year": 2029, "quantity": "1", "sold": "1", "loss": "0", "price": "10",
                 "variable_cost": "20", "fixed_cost": "10", "household": "0"},
                {"year": 2030, "quantity": "100", "sold": "100", "loss": "0", "price": "10",
                 "variable_cost": "1", "fixed_cost": "10", "household": "0"}],
        }
        calculated = finance.calculate(spec, context=mc.output_context(
            self.root, "specialty_crops"))
        self.assertLess(float(calculated["rows"][0]["profit"]), 0)
        self.assertGreater(float(calculated["rows"][-1]["profit"]), 0)
        self.record("surplus", calculated["rows"][-1]["profit"])
        self.body()
        self.assert_allowed("surplus")

    def test_body_file_cannot_hide_behind_empty_status(self):
        self.body(status="empty")
        self.assert_blocked("missing")

    def test_public_next_check_status_report_missing_and_accepted_gate(self):
        self.record("deficit_pending", "-100")
        self.body()
        for accepted in (False, True):
            if accepted:
                self.answer()
            for command in ("next", "check", "status"):
                with self.subTest(accepted=accepted, command=command):
                    before = (self.root / "project.json").read_bytes()
                    proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "gg.py"),
                                           command, str(self.root)],
                                          capture_output=True, text=True)
                    # check returns 1 for any error (the fixture deliberately
                    # has no school rules); status/next are read reports.
                    self.assertEqual(1 if command == "check" else 0,
                                     proc.returncode, proc.stderr + proc.stdout)
                    data = json.loads(proc.stdout)
                    entries = data["checks"] if command == "status" else data
                    entry = next(e for e in entries if e.get("check_id", e.get("id"))
                                 == "finance_gate")
                    self.assertEqual(("ready" if accepted else "needs_user")
                                     if command == "next" else ("pass" if accepted else "fail"),
                                     entry["status"])
                    self.assertEqual(before, (self.root / "project.json").read_bytes())

    def test_known_limit_changed_deficit_with_old_acceptance_is_not_auto_stale(self):
        self.record("deficit_accepted", "-100")
        self.answer()
        self.body()
        p = self.core.load(self.root)
        # DESIGN §4 explicitly excludes automatic stale detection. Reproduce
        # the remaining counterexample rather than claiming it is closed.
        changed = copy.deepcopy(p)
        changed["facts"]["gate-profit"]["value"] = "-10000"
        changed["sources"]["calculation"]["claims"]["profit"]["value"] = "-10000"
        self.assertEqual("deficit_accepted", self.core.finance_gate(changed)["state"])


if __name__ == "__main__":
    unittest.main()
