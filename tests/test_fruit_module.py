"""fruit_trees module declaration and its link to the common guard B.

A01-P (S01 plan, no specialty inheritance), A30 (no example precedents),
finance unsupported, and the fruit link of A32/A46: the real registered
module is refused by capability on every deliverable path while plan and
reading stay open.  The full path x case matrix lives in
tests/test_major_guard_*.py (G-B, inherited); this file checks the fruit
connection only.
"""
import json
import subprocess
import sys

from tests import _fruit_fixtures as ff
from tests._harness import SCRIPTS, ContractCase, bind_major, runtime, \
    write_text

S01_NODES = (
    ["front", "ch1_intro", "ch2_location", "ch2_climate", "ch2_soil",
     "ch2_industry", "ch2_model_farm", "ch2_swot", "ch2_policy",
     "ch3_manager", "ch3_cultivar", "ch3_cultivation", "ch3_pest",
     "ch3_production", "ch3_marketing", "ch3_investment", "ch4_closing",
     "back_parent_consent", "back_ack"]
    + ["appendix_%02d" % i for i in range(1, 16)])


def _cli(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True)


class DeclarationTests(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.module = self.mc.default_registry().resolve("fruit_trees")

    def test_registered_with_plan_only_outputs(self):
        m = self.module
        self.assertEqual(m.module_version, "0.1.0")
        self.assertEqual(m.capabilities["finance"], "unsupported")
        self.assertEqual(set(m.supported_outputs),
                         {"question_list", "document_plan",
                          "evidence_review"})
        self.assertTrue(all(c.status == "unsupported"
                            for c in m.finance_capabilities))
        self.assertEqual(m.packs, ())
        self.assertEqual(m.imports, ())

    def test_s01_document_plan_without_specialty_inheritance(self):
        """A01-P."""
        nodes = [n.section_id for n in self.module.document_plan]
        self.assertEqual(nodes, S01_NODES)
        specialty = {n.section_id for n in self.mc.default_registry()
                     .resolve("specialty_crops").document_plan}
        self.assertEqual(set(nodes) & (specialty - {"farm_status"}), set())
        self.assertTrue(all(n.required and not n.selectable
                            for n in self.module.document_plan))
        ids = {e for n in self.module.document_plan for e in n.evidence_ids}
        self.assertEqual(ids, {"FRT-S%02d" % i for i in range(1, 18)})

    def test_no_example_precedents(self):
        """A30 — the student example is not a module precedent."""
        self.assertEqual(self.module.precedents, ())
        doc = self.mc.route(self.mc.default_registry(), "fruit_trees",
                            "document").to_dict()
        self.assertEqual(doc["proposal"]["precedents"], [])
        self.assertEqual(doc["proposal"]["claims"], [])

    def test_proposals_validate_and_stay_uncomputed(self):
        props = self.mc.capability_proposals(self.mc.default_registry(),
                                             "fruit_trees")
        self.assertEqual({k: v.status for k, v in props.items()},
                         {"question": "supported", "document": "supported",
                          "evidence": "supported",
                          "finance": "unsupported"})
        blob = json.dumps({k: v.to_dict() for k, v in props.items()},
                          ensure_ascii=False)
        for term in ("곤충", "사육", "특용작물"):
            self.assertNotIn(term, blob)

    def test_instance_templates_are_namespaced(self):
        fields = [q.field_id for q in self.module.question_schema]
        self.assertIn("fruit_trees.cohort.{id}.tree_count", fields)
        self.assertIn("fruit_trees.block.{id}.cultivation_form", fields)
        self.assertTrue(all(f.startswith("fruit_trees.") for f in fields))


class GuardLinkTests(ContractCase):
    """Fruit link of A32-L4 / A46-T1 on the real module."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.core = runtime("gg_core")

    def _held(self, reason, output, root, spec=None, major=None):
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.mc.authorize_output(output, self.mc.output_context(
                root, major), spec=spec)
        self.assertEqual(caught.exception.reason, reason)

    def test_bound_fruit_every_output_refused_by_capability(self):
        root = self.make_project()
        bind_major(root, "fruit_trees")
        for output in self.mc.OUTPUT_REQUIREMENTS:
            with self.subTest(output=output):
                self._held("unsupported_output", output, root,
                           major="fruit_trees")

    def test_unbound_and_mismatched_fruit_requests(self):
        root = self.make_project()
        self._held("major_binding_required", "school_paper", root,
                   major="fruit_trees")
        bind_major(root, "specialty_crops")
        self._held("major_binding_mismatch", "school_paper", root,
                   major="fruit_trees")

    def test_fruit_label_conflicts_with_other_major(self):
        root = self.make_project()
        bind_major(root, "specialty_crops")
        spec = {"major_id": "specialty_crops",
                "school_profile": {"department": "과수학과"}}
        self._held("major_marker_conflict", "school_paper", root, spec=spec)

    def test_paper_cli_held_and_plan_open(self):
        root = self.make_project()
        bind_major(root, "fruit_trees")
        spec = {"author": "합성", "writing_year": 2026,
                "major_id": "fruit_trees",
                "school_profile": {"mode": "school", "school": "합성대학교",
                                   "department": "과수학과"}}
        write_text(root, "spec.json", json.dumps(spec, ensure_ascii=False))
        before = self._tree_bytes(root)
        out = _cli("gg.py", "paper", root, "--input", "spec.json",
                   "--out", "build/p.md", "--major", "fruit_trees")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("unsupported_output", out.stdout + out.stderr)
        self.assertEqual(self._tree_bytes(root), before)
        plan = _cli("gg.py", "major-plan", root)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["binding"]["major_id"],
                         "fruit_trees")

    def test_plan_reports_same_holds_as_guard(self):
        fp = ff.ready(self)
        availability = {o["output"]: o["reason"]
                        for o in fp.plan()["output_availability"]}
        self.assertEqual(set(availability.values()), {"unsupported_output"})
        self.assertEqual(set(availability), {
            "school_paper", "school_excel_workbook", "finance_calculation"})
