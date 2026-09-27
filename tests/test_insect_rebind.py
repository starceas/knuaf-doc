"""industrial_insects module 0.2.0 migration (B2 design §2.8).

0.2.0 adds the product-line and plan-year question templates.  A project
bound at 0.1.0 is held by the common binding check; it moves forward by
updating the *same* ``common.major_id`` fact to a new revision (a second
provided fact would be ``duplicate_major_facts``).  Answers and question
counters stay untouched, and every deliverable stays refused.
"""
import json
import subprocess
import sys

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime, \
    write_text


def _gg(*args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *map(str, args)],
        capture_output=True, text=True)


class InsectRebindTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.version = self.mc.INDUSTRIAL_INSECTS_MODULE.module_version

    def _set_binding(self, root, **changes):
        fact = dict(self.core.load(root)["facts"]["selected_major"])
        fact.update(changes)
        self.core.apply(root, {"request_id": "rebind:%s" % changes,
                               "ops": [{"collection": "facts",
                                        "value": fact}]},
                        self.core.load(root)["revision"])

    def test_current_version_is_0_2_0(self):
        self.assertEqual("0.2.0", self.version)

    def test_old_binding_is_held_and_same_fact_rebind_restores_plan(self):
        root = self.make_project()
        bind_major(root, "industrial_insects")
        self._set_binding(root, module_version="0.1.0")
        registry = self.mc.default_registry()
        with self.assertRaises(self.mc.MajorContractError) as held:
            self.mc.binding_from_project(registry, self.core.load(root))
        self.assertEqual("binding_invalid", held.exception.reason)

        before = self.core.load(root)
        questions = json.dumps(before.get("questions"), sort_keys=True)
        others = {k: v for k, v in before["facts"].items()
                  if k != "selected_major"}
        self._set_binding(root, module_version=self.version)
        after = self.core.load(root)
        binding = self.mc.binding_from_project(registry, after)
        self.assertEqual(("industrial_insects", self.version),
                         (binding.major_id, binding.module_version))
        self.assertEqual(questions,
                         json.dumps(after.get("questions"), sort_keys=True))
        self.assertEqual(others, {k: v for k, v in after["facts"].items()
                                  if k != "selected_major"})
        self.assertGreater(after["facts"]["selected_major"]["revision"],
                           before["facts"]["selected_major"]["revision"])

    def test_second_binding_fact_is_duplicate(self):
        root = self.make_project()
        bind_major(root, "industrial_insects")
        p = self.core.load(root)
        fact = dict(p["facts"]["selected_major"])
        fact["id"] = "selected_major_again"
        fact.pop("revision", None)
        self.core.apply(root, {"request_id": "dup", "ops": [
            {"collection": "facts", "value": fact}]}, p["revision"])
        with self.assertRaises(self.mc.MajorContractError) as held:
            self.mc.binding_from_project(self.mc.default_registry(),
                                         self.core.load(root))
        self.assertIn("duplicate_major_facts", str(held.exception))

    def test_deliverables_still_refused_at_current_version(self):
        mc = self.mc
        root = self.make_project()
        bind_major(root, "industrial_insects")
        ctx = mc.output_context(root, "industrial_insects")
        for output in (mc.OUTPUT_SCHOOL_PAPER, mc.OUTPUT_SCHOOL_WORKBOOK,
                       mc.OUTPUT_FINANCE_CALCULATION):
            with self.subTest(case=output):
                with self.assertRaises(mc.OutputHeldError) as held:
                    mc.authorize_output(output, ctx)
                self.assertEqual("unsupported_output", held.exception.reason)

    def test_plan_without_selection_lists_lines_only(self):
        root = self.make_project()
        bind_major(root, "industrial_insects")
        before = self._tree_bytes(root)
        proc = _gg("insect-plan", root)
        self.assertEqual(before, self._tree_bytes(root))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual("needs_selection", value["status"])
        self.assertIn("line_inventory", value)
        self.assertEqual([], value["instances"])
