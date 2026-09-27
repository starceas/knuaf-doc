"""gg_insect_document: selection parsing, plan contract, question states.

Synthetic fixtures only — the committed locator map is used for
precedents, but no original PDFs or student data are read.  The common
guard-B paths (file writes, output authorization) are covered by
tests/test_major_guard_*.py; this file locks the insect document-plan
contract from .ii-work/DESIGN.md sections 4-5 and 14.1-14.3.
"""
import copy
import dataclasses
import json
import tempfile
import types
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime

MAJOR = "industrial_insects"
FIELD = "industrial_insects.species"
F0_TABLE_21 = {
    "source_id": "F0",
    "physical_page": 55,
    "object_label": "table_21",
}
E1_GAP = {
    "source_id": "E1",
    "physical_page": 1,
    "object_label": "text_layer_gap",
}
E2_GAP_3 = {
    "source_id": "E2",
    "physical_page": 3,
    "object_label": "text_layer_gap",
}


def _fact(fid, field_id, *, answer_state="provided",
          verification="source_located", value="synthetic-value",
          scope="farm"):
    return {
        "id": fid,
        "field_id": field_id,
        "kind": "reported_fact",
        "value": value if answer_state == "provided" else None,
        "unit": "",
        "value_type": "text",
        "period": None,
        "scope": scope,
        "answer_state": answer_state,
        "verification": verification,
        "source_refs": [
            {"id": "major-answer", "locator": "line 1", "revision": 1}
        ],
    }


class ParseSelectionTests(ContractCase):
    def setUp(self):
        self.doc = runtime("gg_insect_document")

    def _reject(self, text):
        with self.assertRaises(ValueError) as cm:
            self.doc.parse_selection(text)
        self.assertEqual(str(cm.exception), "insect_selection_invalid:json")

    def test_valid_json(self):
        parsed = self.doc.parse_selection(
            '[{"section_id": "preface", "action": "use"}]')
        self.assertEqual(
            parsed, [{"section_id": "preface", "action": "use"}])

    def test_duplicate_keys_rejected(self):
        for text in (
                '{"a": 1, "a": 2}',
                '[{"a": 1, "b": {"x": 1, "x": 2}}]'):
            with self.subTest(text=text):
                self._reject(text)

    def test_nonfinite_numbers_rejected(self):
        for text in (
                '{"a": NaN}', '{"a": Infinity}', '[{"a": -Infinity}]'):
            with self.subTest(text=text):
                self._reject(text)

    def test_malformed_and_nontext_rejected(self):
        for bad in ("{", '["a"', "", '{"a": }', 123, None, ["x"],
                    b"\xff\xfe"):
            with self.subTest(bad=bad):
                self._reject(bad)

    def test_error_code_fixed_no_input_echo(self):
        marker = "SECRET-MARKER-W1"
        with self.assertRaises(ValueError) as cm:
            self.doc.parse_selection('{"%s": 1, "%s": 2}' % (marker, marker))
        self.assertNotIn(marker, str(cm.exception))


class _InsectCase(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.doc = runtime("gg_insect_document")
        self.core = runtime("gg_core")
        self.registry = self.mc.default_registry()
        self.root = self.make_project()
        bind_major(self.root, MAJOR)
        self.project = self.core.load(self.root)

    def plan(self, selections=None, *, project=None, registry=None,
             precedent_path=None):
        kw = {}
        if precedent_path is not None:
            kw["precedent_path"] = precedent_path
        return self.doc.build_plan(
            registry or self.registry,
            project if project is not None else self.project,
            [] if selections is None else selections,
            **kw)

    def reject(self, code, selections=None, **kw):
        with self.assertRaises(ValueError) as cm:
            self.plan(selections, **kw)
        self.assertEqual(str(cm.exception), code)

    def mutated_map(self, mutate):
        doc = copy.deepcopy(self.doc.load_precedents())
        mutate(doc)
        tmp = tempfile.TemporaryDirectory(prefix="insect-map-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "precedents.json"
        path.write_text(json.dumps(doc, ensure_ascii=False),
                        encoding="utf-8")
        return path


class PlanShapeTests(_InsectCase):
    def test_empty_selection_needs_selection(self):
        plan = self.plan([])
        self.assertEqual(plan["status"], "needs_selection")
        self.assertEqual(plan["selected_sections"], [])
        self.assertEqual(
            set(plan["available_sections"]),
            {"preface", "farm_status", "environment_analysis",
             "vision_goals", "detailed_plan", "closing",
             "sources_appendix"})
        self.assertEqual(plan["major_id"], MAJOR)
        self.assertEqual(plan["finance_status"], "unsupported")
        self.assertIs(plan["rendered_paper"], False)
        self.assertIs(plan["canonical_write"], False)
        self.assertEqual(plan["answer_scope"], "project_level_summary")
        self.assertEqual(plan["source_ref_check"], "registered_id_only")
        self.assertIn("재질문", plan["question_status_meaning"])
        self.assertIn("검증", plan["question_status_meaning"])
        self.assertEqual(
            set(plan["question_states"]),
            {q.field_id for q in self.mc.default_registry()
             .resolve(MAJOR).question_schema})
        for state in plan["question_states"].values():
            self.assertEqual(
                set(state), {"question_status", "answer_states", "flags"})
            self.assertEqual(state["question_status"], "ask")
            self.assertEqual(state["answer_states"], ["not_provided"])
            self.assertEqual(state["flags"], [])

    def test_use_aligned_locator(self):
        plan = self.plan([{
            "section_id": "detailed_plan",
            "action": "use",
            "observation_refs": [F0_TABLE_21],
        }])
        self.assertEqual(plan["status"], "review_plan")
        sel = plan["selected_sections"][0]
        self.assertEqual(sel["section_id"], "detailed_plan")
        self.assertEqual(sel["action"], "use")
        self.assertEqual(sel["evidence_status"], "locator_linked")
        ref = sel["observation_refs"][0]
        self.assertEqual(
            ref,
            {"source_id": "F0", "physical_page": 55,
             "object_label": "table_21", "status": "example_observed",
             "observed_role": "profit_and_loss", "role_fit": "role_aligned"})
        self.assertIsNone(sel["role_fit_reason"])

    def test_adapt_without_reason_rejected(self):
        self.reject("insect_selection_invalid:reason", [{
            "section_id": "preface", "action": "adapt",
            "observation_refs": [F0_TABLE_21]}])

    def test_reason_must_be_string(self):
        self.reject("insect_selection_invalid:reason", [{
            "section_id": "preface", "action": "use", "reason": 3}])

    def test_role_mismatch_requires_adapt_and_fit_reason(self):
        bad = [{
            "section_id": "preface",
            "action": "use",
            "observation_refs": [F0_TABLE_21],
        }]
        self.reject("insect_role_mismatch", bad)
        bad[0]["action"] = "adapt"
        bad[0]["reason"] = "synthetic"
        self.reject("insect_role_mismatch", bad)
        bad[0]["role_fit_reason"] = "   "
        self.reject("insect_role_mismatch", bad)

    def test_role_mismatch_with_fit_reason_needs_review(self):
        plan = self.plan([{
            "section_id": "preface",
            "action": "adapt",
            "reason": "synthetic adaptation",
            "role_fit_reason": "손익 표를 전략 근거로 전용",
            "observation_refs": [F0_TABLE_21],
        }])
        sel = plan["selected_sections"][0]
        self.assertEqual(sel["evidence_status"], "fit_needs_review")
        self.assertEqual(
            sel["observation_refs"][0]["role_fit"], "fit_needs_review")
        self.assertEqual(sel["role_fit_reason"], "손익 표를 전략 근거로 전용")


class SelectionRejectionTests(_InsectCase):
    def test_unregistered_locator_rejected(self):
        ghost = {"source_id": "F0", "physical_page": 55,
                 "object_label": "table_99"}
        self.reject("insect_example_unregistered", [{
            "section_id": "detailed_plan", "action": "use",
            "observation_refs": [ghost]}])

    def test_unresolved_pages_rejected(self):
        for ref in (E1_GAP, E2_GAP_3):
            with self.subTest(ref=ref):
                self.reject("insect_example_unresolved", [{
                    "section_id": "detailed_plan", "action": "use",
                    "observation_refs": [ref]}])

    def test_unusable_code_rejected_even_with_content_role(self):
        path = self.mutated_map(lambda d: d["observations"].append({
            "source_id": "F0", "physical_page": 70,
            "object_label": "last_page_image",
            "role": "references_and_back_matter",
            "status": "example_observed",
            "verification":
                "under_20_extracted_chars; render_or_ocr_pending",
        }))
        self.reject("insect_example_unresolved", [{
            "section_id": "detailed_plan", "action": "use",
            "observation_refs": [{
                "source_id": "F0", "physical_page": 70,
                "object_label": "last_page_image"}]}],
            precedent_path=path)

    def test_bool_physical_page_rejected(self):
        ref = dict(F0_TABLE_21, physical_page=True)
        self.reject("insect_selection_invalid:observation_ref", [{
            "section_id": "detailed_plan", "action": "use",
            "observation_refs": [ref]}])

    def test_ref_shape_rejected(self):
        for ref in ("F0:55:table_21",
                    {"source_id": "F0", "physical_page": 55},
                    dict(F0_TABLE_21, extra=1),
                    dict(F0_TABLE_21, source_id=5)):
            with self.subTest(ref=ref):
                self.reject("insect_selection_invalid:observation_ref", [{
                    "section_id": "detailed_plan", "action": "use",
                    "observation_refs": [ref]}])

    def test_unknown_selection_key_rejected(self):
        self.reject("insect_selection_invalid:selection", [{
            "section_id": "preface", "action": "use", "surprise": 1}])

    def test_selections_must_be_list(self):
        self.reject("insect_selection_invalid:selections",
                    {"section_id": "preface"})

    def test_selection_entry_must_be_object(self):
        self.reject("insect_selection_invalid:selection",
                    ["preface"])

    def test_unknown_duplicate_or_bad_action_rejected(self):
        for sid in ("not_a_section", 7):
            with self.subTest(sid=sid):
                self.reject("insect_selection_invalid:section_id", [{
                    "section_id": sid, "action": "use"}])
        dup = [{"section_id": "preface", "action": "use"},
               {"section_id": "preface", "action": "use"}]
        self.reject("insect_selection_invalid:section_id", dup)
        self.reject("insect_selection_invalid:action", [{
            "section_id": "preface", "action": "reuse"}])

    def test_observation_refs_must_be_list(self):
        self.reject("insect_selection_invalid:observation_refs", [{
            "section_id": "preface", "action": "use",
            "observation_refs": "F0:55"}])

    def test_source_refs(self):
        plan = self.plan([{
            "section_id": "farm_status", "action": "use",
            "source_refs": [{"id": "major-answer", "locator": "line 1"}]}])
        sel = plan["selected_sections"][0]
        self.assertEqual(sel["source_refs"],
                         [{"id": "major-answer", "locator": "line 1"}])
        self.assertEqual(sel["evidence_status"], "locator_linked")
        self.reject("insect_source_unregistered", [{
            "section_id": "farm_status", "action": "use",
            "source_refs": [{"id": "ghost", "locator": "line 1"}]}])
        self.reject("insect_source_unregistered", [{
            "section_id": "farm_status", "action": "use",
            "source_refs": [{"id": "major-answer", "locator": "  "}]}])
        self.reject("insect_selection_invalid:source_ref", [{
            "section_id": "farm_status", "action": "use",
            "source_refs": [{"id": "major-answer"}]}])
        self.reject("insect_selection_invalid:source_refs", [{
            "section_id": "farm_status", "action": "use",
            "source_refs": "major-answer"}])


class QuestionStateTests(_InsectCase):
    def _add_facts(self, facts, questions=None):
        project = copy.deepcopy(self.project)
        project["facts"] = dict(project["facts"])
        for fact in facts:
            project["facts"][fact["id"]] = fact
        if questions:
            project["questions"] = dict(project["questions"])
            project["questions"].update(questions)
        return project

    def test_superseded_only_is_not_reuse(self):
        project = self._add_facts(
            [_fact("f1", FIELD, verification="superseded")],
            {FIELD: {"attempts": 1}})
        plan = self.plan(project=project)
        state = plan["question_states"][FIELD]
        self.assertEqual(state["question_status"], "help")
        self.assertEqual(state["answer_states"], ["not_provided"])
        self.assertEqual(state["flags"], [])

    def test_superseded_plus_current_unknown(self):
        project = self._add_facts([
            _fact("f1", FIELD, verification="superseded"),
            _fact("f2", FIELD, answer_state="unknown",
                  verification="source_located"),
        ])
        state = self.plan(project=project)["question_states"][FIELD]
        self.assertNotEqual(state["question_status"], "reuse")
        self.assertEqual(state["answer_states"], ["unknown"])

    def test_disputed_and_multiple_active_flags(self):
        project = self._add_facts([
            _fact("f1", FIELD),
            _fact("f2", FIELD, verification="disputed"),
        ])
        state = self.plan(project=project)["question_states"][FIELD]
        self.assertEqual(state["question_status"], "reuse")
        self.assertEqual(state["answer_states"], ["provided"])
        self.assertEqual(state["flags"], ["multiple_active", "disputed"])

    def test_answer_values_never_emitted(self):
        project = self._add_facts([
            _fact("f1", FIELD, value="SECRET-ANSWER-W1"),
        ])
        blob = json.dumps(self.plan(project=project), ensure_ascii=False)
        self.assertNotIn("SECRET-ANSWER-W1", blob)
        self.assertEqual(
            json.loads(blob)["question_states"][FIELD]["answer_states"],
            ["provided"])

    def test_canonical_project_bytes_unchanged(self):
        project = self._add_facts([
            _fact("f1", FIELD, verification="superseded"),
            _fact("f2", FIELD),
        ])
        before = self._tree_bytes(self.root)
        canonical_before = copy.deepcopy(project)
        self.plan([{
            "section_id": "detailed_plan", "action": "use",
            "observation_refs": [F0_TABLE_21]}], project=project)
        self.assertEqual(project, canonical_before)
        self.assertEqual(self._tree_bytes(self.root), before)

    def test_foreign_field_facts_ignored(self):
        project = self._add_facts([
            _fact("f1", "specialty_crops.crop"),
        ])
        plan = self.plan(project=project)
        states = plan["question_states"]
        self.assertTrue(all(s["answer_states"] == ["not_provided"]
                            for s in states.values()))


class ModuleDeclarationTests(_InsectCase):
    def _module(self, *, supported_outputs=None, capabilities=None):
        module = self.mc.default_registry().resolve(MAJOR)
        kw = {}
        if supported_outputs is not None:
            kw["supported_outputs"] = supported_outputs
        if capabilities is not None:
            kw["capabilities"] = types.MappingProxyType(capabilities)
        return dataclasses.replace(module, **kw)

    def test_output_undeclared_rejected(self):
        module = self._module(supported_outputs=(
            "question_list", "evidence_review"))
        registry = self.mc.ModuleRegistry([module])
        self.reject("insect_output_unsupported:document_plan", [],
                    registry=registry)

    def test_document_capability_unsupported(self):
        module = self._module(capabilities={
            "question": "supported", "document": "unsupported",
            "evidence": "supported", "finance": "unsupported"})
        registry = self.mc.ModuleRegistry([module])
        self.reject("insect_capability_unsupported:document", [],
                    registry=registry)

    def test_specialty_binding_rejected(self):
        root = self.make_project()
        bind_major(root, "specialty_crops")
        project = self.core.load(root)
        with self.assertRaises(ValueError) as cm:
            self.doc.build_plan(self.registry, project, [])
        self.assertEqual(
            str(cm.exception),
            "insect plan requires industrial_insects binding")


class PlanContractTests(_InsectCase):
    FORBIDDEN_KEYS = {
        "calculated", "computed", "result", "total", "sum",
        "production", "revenue", "forecast", "authorization",
        "body", "content",
    }

    def _keys(self, value, out):
        if isinstance(value, dict):
            for key, sub in value.items():
                out.add(key)
                self._keys(sub, out)
        elif isinstance(value, (list, tuple)):
            for sub in value:
                self._keys(sub, out)
        return out

    def test_no_body_or_computed_or_authorization_keys(self):
        plan = self.plan([{
            "section_id": "detailed_plan", "action": "use",
            "observation_refs": [F0_TABLE_21]}])
        keys = self._keys(plan, set())
        self.assertTrue(keys.isdisjoint(self.FORBIDDEN_KEYS),
                        keys & self.FORBIDDEN_KEYS)

    def test_plan_has_no_generated_body_fields(self):
        plan = self.plan([{
            "section_id": "preface", "action": "use"}])
        sel = plan["selected_sections"][0]
        self.assertEqual(
            set(sel),
            {"section_id", "role", "action", "reason",
             "observation_refs", "role_fit_reason", "source_refs",
             "evidence_status"})


class LineKeysInPlanTests(_InsectCase):
    """B2 module 0.2.0: build_plan keeps its keys and adds the line
    instance view (declared ids/years only, never student values)."""

    NEW_KEYS = {
        "line_inventory", "line_retired", "plan_years", "instances",
        "line_questions", "related_project_facts", "next_line_id",
        "instance_issues",
    }

    def _with_line(self, *extra):
        project = copy.deepcopy(self.project)
        project["facts"] = dict(project["facts"])
        for fact in (
                _fact("inv", "industrial_insects.line_inventory",
                      value="l01", scope="project"),
                _fact("yrs", "industrial_insects.plan_years",
                      value="2027", scope="project"),
                _fact("lbl", "industrial_insects.line.l01.label",
                      value="SECRET-LINE-NAME", scope="line:l01"),
                _fact("sp", "industrial_insects.line.l01.species",
                      value="SECRET-LINE-SPECIES", scope="line:l01"),
                _fact("y27",
                      "industrial_insects.line.l01.year.2027."
                      "year_end_in_process",
                      value="SECRET-YEAR-VALUE", scope="line:l01"),
                *extra):
            project["facts"][fact["id"]] = fact
        return project

    def test_existing_keys_preserved_and_line_keys_added(self):
        plan = self.plan(project=self._with_line())
        for key in ("major_id", "module_version", "contract_version",
                    "project_revision", "status", "selected_sections",
                    "available_sections", "question_states",
                    "question_status_meaning", "answer_scope",
                    "source_ref_check", "finance_status",
                    "rendered_paper", "canonical_write"):
            with self.subTest(case=key):
                self.assertIn(key, plan)
        self.assertTrue(self.NEW_KEYS <= set(plan))
        self.assertEqual(plan["instances"], [{"id": "l01", "flags": []}])
        self.assertEqual(plan["line_inventory"]["question_status"],
                         "reuse")
        self.assertIn("l01", plan["line_questions"])
        self.assertIn("2027", plan["line_questions"]["l01"]["years"])
        self.assertEqual(plan["next_line_id"], "l02")
        self.assertEqual(plan["instance_issues"], [])
        # question_states still covers the declared schema, templates
        # included, and stays three keys per entry.
        self.assertEqual(
            set(plan["question_states"]),
            {q.field_id for q in self.mc.default_registry()
             .resolve(MAJOR).question_schema})
        for state in plan["question_states"].values():
            self.assertEqual(
                set(state), {"question_status", "answer_states", "flags"})

    def test_line_values_never_emitted(self):
        blob = json.dumps(self.plan(project=self._with_line()),
                          ensure_ascii=False)
        for secret in ("SECRET-LINE-NAME", "SECRET-LINE-SPECIES",
                       "SECRET-YEAR-VALUE"):
            with self.subTest(case=secret):
                self.assertNotIn(secret, blob)

    def test_line_keys_present_without_selection_or_lines(self):
        plan = self.plan()
        self.assertTrue(self.NEW_KEYS <= set(plan))
        self.assertEqual(plan["instances"], [])
        self.assertEqual(plan["line_questions"], {})
        self.assertEqual(plan["line_inventory"]["question_status"], "ask")
        self.assertEqual(plan["next_line_id"], "l01")
