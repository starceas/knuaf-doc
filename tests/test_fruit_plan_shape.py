"""The fruit plan's read-only instance and question handoff contract."""

import json
import re
import subprocess
import sys
from pathlib import Path

from tests import _fruit_fixtures as ff
from tests._harness import ContractCase, runtime, write_text


GROUPS = ("block", "cohort", "batch", "move")
YEARLY = ("bearing_trees", "yield_kg_per_tree", "bearing_area_m2",
          "yield_kg_per_10a")
ACTIONS = {"reuse", "ask", "help", "deferred"}


class FruitPlanShapeTests(ContractCase):
    def _declared(self):
        fp = ff.ready(self)
        for group, iid in (("block", "b1"), ("cohort", "c1"),
                           ("batch", "lot1"), ("move", "m1")):
            fp.fact("label-" + group,
                    f"fruit_trees.{group}.{iid}.label", group,
                    scope=f"{group}:{iid}")
        fp.fact("block-ref", "fruit_trees.cohort.c1.block_ref", "b1",
                scope="cohort:c1")
        fp.fact("unknown-owner", "fruit_trees.block.b1.ownership", None,
                scope="block:b1", answer_state="unknown")
        # A value on an undeclared instance must never create a question.
        fp.fact("orphan", "fruit_trees.cohort.orphan.origin", "승계",
                scope="cohort:orphan")
        return fp

    def _year_fact(self, fp, fid, name, period, value):
        field_id = f"fruit_trees.cohort.c1.{name}"
        write_text(fp.root, f"answers/{fid}.txt", f"{field_id}: {value}\n")
        fp.apply([
            {"collection": "sources", "value": {
                "id": "ans-" + fid, "path": f"answers/{fid}.txt",
                "claims": {}, "claim_review": "synthetic-review"}},
            {"collection": "facts", "value": {
                "id": fid, "field_id": field_id, "kind": "reported_fact",
                "value": value, "unit": "주" if name == "bearing_trees" else "kg",
                "value_type": "decimal", "period": period,
                "scope": "cohort:c1", "answer_state": "provided",
                "verification": "source_located",
                "source_refs": [{"id": "ans-" + fid,
                                 "locator": "line 1", "revision": 1}]}}
        ], "fact:" + fid)

    def test_instance_groups_and_answers(self):
        plan = self._declared().plan()
        instances = plan["instances"]
        self.assertEqual(set(instances), set(GROUPS))
        for group, iid in (("block", "b1"), ("cohort", "c1"),
                           ("batch", "lot1"), ("move", "m1")):
            self.assertEqual(set(instances[group]), {iid})
            node = instances[group][iid]
            self.assertIsInstance(node, dict)
            self.assertIsInstance(node["fields"], dict)
            for name, answer in node["fields"].items():
                if group == "cohort" and name in YEARLY:
                    self.assertEqual(set(answer), {"by_period", "invalid_periods"})
                    self.assertEqual(answer["by_period"], {})
                    self.assertEqual(answer["invalid_periods"], [])
                else:
                    self.assertTrue({"answer_state", "value", "fact_ids"}
                                    <= set(answer), (group, name, answer))
                    self.assertIsInstance(answer["fact_ids"], list)
                    if answer["answer_state"] != "provided":
                        self.assertIsNone(answer["value"])
        owner = instances["block"]["b1"]["fields"]["ownership"]
        self.assertEqual(owner["answer_state"], "unknown")
        self.assertIsNone(owner["value"])
        self.assertEqual(owner["fact_ids"], ["unknown-owner"])

    def test_yearly_answers_and_period_scoped_duplicates(self):
        fp = self._declared()
        for name in YEARLY:
            self._year_fact(fp, name + "-28", name, "2028", "1")
            self._year_fact(fp, name + "-29", name, "2029", "2")
            self._year_fact(fp, name + "-bad", name, "20X9", "3")
        self._year_fact(fp, "bearing-28-again", "bearing_trees", "2028", "4")
        plan = fp.plan()
        fields = plan["instances"]["cohort"]["c1"]["fields"]
        for name in YEARLY:
            answer = fields[name]
            self.assertEqual(set(answer), {"by_period", "invalid_periods"})
            self.assertEqual(set(answer["by_period"]), {"2028", "2029"})
            self.assertEqual(answer["invalid_periods"], [name + "-bad"])
            for year, item in answer["by_period"].items():
                self.assertRegex(year, r"^\d{4}$")
                self.assertTrue({"answer_state", "value", "fact_ids"}
                                <= set(item))
                self.assertIsInstance(item["fact_ids"], list)
                if item["answer_state"] != "provided":
                    self.assertIsNone(item["value"])
            self.assertEqual(answer["by_period"]["2029"]["value"], "2")
        self.assertEqual(fields["bearing_trees"]["by_period"]["2028"]
                         ["answer_state"], "duplicate_answer")
        # A duplicate keeps the unit key: the shared unit, or None on a
        # unit conflict (the value is None either way).
        self.assertEqual(fields["bearing_trees"]["by_period"]["2028"]
                         ["unit"], "주")
        duplicates = [issue for issue in plan["instance_issues"]
                      if issue["kind"] == "duplicate_answer" and
                      issue["field_id"] == "fruit_trees.cohort.c1.bearing_trees"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["period"], "2028")
        self.assertFalse(any(i["kind"] == "duplicate_answer" and
                             i.get("period") == "2029"
                             for i in plan["instance_issues"]))

    def test_duplicate_unit_conflict_is_none(self):
        fp = self._declared()
        self._year_fact(fp, "k1", "yield_kg_per_tree", "2028", "1")
        self._year_fact(fp, "k2", "yield_kg_per_tree", "2028", "2")
        # Rewrite k2's unit so the two duplicates disagree.
        fact = dict(runtime("gg_core").load(fp.root)["facts"]["k2"], unit="g")
        fact.pop("revision", None)
        fp.apply([{"collection": "facts", "value": fact}], "fact:k2-unit")
        item = (fp.plan()["instances"]["cohort"]["c1"]["fields"]
                ["yield_kg_per_tree"]["by_period"]["2028"])
        self.assertEqual(item["answer_state"], "duplicate_answer")
        self.assertIsNone(item["unit"])
        self.assertIsNone(item["value"])
        self.assertEqual(sorted(item["fact_ids"]), ["k1", "k2"])

    def test_duplicate_with_non_string_unit_does_not_crash(self):
        fp = self._declared()
        self._year_fact(fp, "t1", "bearing_trees", "2027", "1")
        self._year_fact(fp, "t2", "bearing_trees", "2027", "2")
        fact = dict(runtime("gg_core").load(fp.root)["facts"]["t1"],
                    unit=["주"])
        fact.pop("revision", None)
        fp.apply([{"collection": "facts", "value": fact}], "fact:t1-unit")
        item = (fp.plan()["instances"]["cohort"]["c1"]["fields"]
                ["bearing_trees"]["by_period"]["2027"])
        self.assertEqual(item["answer_state"], "duplicate_answer")
        self.assertIsNone(item["unit"])
        self.assertEqual(sorted(item["fact_ids"]), ["t1", "t2"])
        # The production check reads the same plan and must not stop.
        out = runtime("gg_orchard_production").orchard_production(fp.root)
        self.assertEqual(out["status"], "presented")

    def test_questions_use_concrete_declared_fields(self):
        plan = self._declared().plan()
        declared = {f"fruit_trees.{group}.{iid}.{name}"
                    for group, nodes in plan["instances"].items()
                    for iid, node in nodes.items()
                    for name in node["fields"]}
        questions = plan["questions"]
        self.assertIsInstance(questions, list)
        self.assertEqual({q["field_id"] for q in questions}, declared)
        self.assertEqual(len(questions), len(declared))
        for question in questions:
            self.assertEqual(set(question), {"field_id", "action"})
            self.assertIn(question["action"], ACTIONS)
            self.assertNotIn("{id}", question["field_id"])
            self.assertTrue(re.fullmatch(
                r"fruit_trees\.(block|cohort|batch|move)\.[a-z0-9_]+\.[a-z0-9_]+",
                question["field_id"]))

    def test_unbound_project_is_held(self):
        root = self.make_project()
        self.assertEqual(runtime("gg_fruit_plan").fruit_plan(root)["status"],
                         "held")
        script = Path(runtime("gg_fruit_plan").__file__)
        out = subprocess.run([sys.executable, "-B", str(script), str(root)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertEqual(json.loads(out.stdout)["status"], "held")
