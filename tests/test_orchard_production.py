"""Synthetic acceptance checks for read-only orchard quantity reconciliation."""

import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from tests._fruit_fixtures import ready
from tests._harness import ContractCase, SCRIPTS, runtime, write_text, bind_major


class OrchardTests(ContractCase):
    def setUp(self):
        self.fp = ready(self)
        self.mod = runtime("gg_orchard_production")
        self.n = 0

    def add(self, group, iid, field, value, *, unit="", state="provided",
            year=None):
        self.n += 1
        fid = "v%d" % self.n
        field_id = "fruit_trees.%s.%s.%s" % (group, iid, field)
        scope = "%s:%s" % (group, iid)
        decimal = bool(unit and unit not in ("년", "m"))
        if year is None:
            self.fp.fact(fid, field_id, value, scope=scope, answer_state=state,
                         value_type="decimal" if decimal else "text", unit=unit)
        else:
            path = "answers/%s.txt" % fid
            write_text(self.fp.root, path, "%s %s %s" % (field_id, year, value))
            src = {"collection": "sources", "value": {"id": "ans-" + fid,
                   "path": path, "claims": {}, "claim_review": "synthetic-review"}}
            fact = {"id": fid, "field_id": field_id, "kind": "reported_fact",
                    "value": value if state == "provided" else None,
                    "unit": unit, "value_type": "decimal" if decimal else "text",
                    "period": str(year), "scope": scope, "answer_state": state,
                    "verification": "source_located",
                    "source_refs": [{"id": "ans-" + fid, "locator": "line 1",
                                     "revision": 1}]}
            if state in ("explicit_none", "withheld", "not_applicable"):
                fact["reason"] = "synthetic reason"
            self.fp.apply([src, {"collection": "facts", "value": fact}],
                          "annual:" + fid)
        return fid

    def block(self, bid="b", area="1000", form="노지"):
        self.add("block", bid, "label", bid)
        self.add("block", bid, "area_m2", area, unit="㎡")
        self.add("block", bid, "cultivation_form", form)
        self.add("block", bid, "heating", "무가온")

    def cohort(self, cid="c", bid="b", *, area="1000", trees="100",
               crop="사과", cultivar="A", basis="per_tree",
               active="2027", until="2027"):
        self.add("cohort", cid, "label", cid)
        self.add("cohort", cid, "block_ref", bid)
        self.add("cohort", cid, "area_m2", area, unit="㎡")
        if trees is not None:
            self.add("cohort", cid, "tree_count", trees, unit="주")
        self.add("cohort", cid, "crop_species", crop)
        if cultivar is not None:
            self.add("cohort", cid, "cultivar", cultivar)
        self.add("cohort", cid, "yield_basis", basis)
        if active:
            self.add("cohort", cid, "active_from_year", active, unit="년")
        if until:
            self.add("cohort", cid, "active_to_year", until, unit="년")

    def yield_tree(self, cid="c", year="2027", count="10", each="1"):
        self.add("cohort", cid, "bearing_trees", count, year=year, unit="주")
        self.add("cohort", cid, "yield_kg_per_tree", each, year=year,
                 unit="kg/주")

    def batch(self, bid, *, origin="harvest", cid="c", year="2027",
              opening=None, qty="10", crop="사과", cultivar="A", grade="상"):
        self.add("batch", bid, "label", bid)
        self.add("batch", bid, "origin", origin)
        if origin == "harvest":
            self.add("batch", bid, "cohort_ref", cid)
        if origin == "opening":
            self.add("batch", bid, "crop_species", crop)
            self.add("batch", bid, "cultivar", cultivar)
            self.add("batch", bid, "opening_year", opening or year, unit="년")
        if origin in ("harvest", "opening"):
            self.add("batch", bid, "harvest_year", year, unit="년")
            if qty is not None:
                self.add("batch", bid, "quantity_kg", qty, unit="kg")
        self.add("batch", bid, "grade", grade)

    def move(self, mid, src, kind, qty="1", year="2027", dst=None,
             unit="kg", state="provided"):
        self.add("move", mid, "label", mid)
        self.add("move", mid, "batch_ref", src)
        self.add("move", mid, "kind", kind)
        self.add("move", mid, "year", year, unit="년")
        if qty is not None:
            self.add("move", mid, "quantity_kg", qty, unit=unit, state=state)
        if dst:
            self.add("move", mid, "target_batch", dst)

    def output(self):
        return self.mod.orchard_production(self.fp.root)

    def test_e1_read_only_boundaries_and_cli(self):
        before = self._tree_bytes(self.fp.root)
        out = self.output()
        self.assertEqual(out["status"], "presented")
        self.assertEqual((out["derived"], out["persisted"]), (True, False))
        cli = subprocess.run([sys.executable, str(SCRIPTS / "gg_orchard_production.py"),
                              str(self.fp.root)], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), out)
        self.assertEqual(before, self._tree_bytes(self.fp.root))
        forbidden = ("price", "revenue", "cost", "amount", "sales")
        self.assertFalse(any(k in json.dumps(out).lower() for k in forbidden))
        mc = runtime("gg_major_contract")
        for kind in ("school_paper", "school_excel_workbook", "finance_calculation"):
            with self.assertRaises(mc.OutputHeldError) as caught:
                mc.authorize_output(kind, mc.output_context(self.fp.root,
                                    "fruit_trees"))
            self.assertEqual(caught.exception.reason, "unsupported_output")

    def test_e1_unbound_and_other_major(self):
        for major in (None, "specialty_crops"):
            with self.subTest(major=major):
                root = self.make_project()
                if major:
                    bind_major(root, major)
                before = self._tree_bytes(root)
                value = self.mod.orchard_production(root)
                self.assertEqual(value["status"], "held")
                cli = subprocess.run([sys.executable, str(SCRIPTS /
                    "gg_orchard_production.py"), str(root)],
                    capture_output=True, text=True)
                self.assertEqual(cli.returncode, 2)
                self.assertEqual(json.loads(cli.stdout), value)
                self.assertEqual(before, self._tree_bytes(root))

    def test_a08_e1_and_a11_e1_zero_missing_and_explicit_none(self):
        self.block(area="3000")
        for cid, active in (("new", "2027"), ("old", "2028"),
                            ("renew", "2029")):
            self.cohort(cid, area="1000", active=active, until=active)
        self.yield_tree("new", count="0")
        self.yield_tree("old", year="2028", count="10")
        self.add("cohort", "renew", "yield_kg_per_tree", "1", year="2029",
                 unit="kg/주")
        out = self.output()
        self.assertEqual(out["cohorts"]["new"]["years"]["2027"]["production"],
                         {"exact": "0"})
        self.assertEqual(out["cohorts"]["old"]["years"]["2028"]["production"],
                         {"exact": "10"})
        rec = out["cohorts"]["renew"]["years"]["2029"]
        self.assertEqual(rec["state"], "not_computable")
        self.assertIn("bearing_trees", rec["missing"])
        self.assertNotIn("production", rec)
        self.add("cohort", "renew", "bearing_trees", None, year="2029",
                 unit="주", state="explicit_none")
        rec = self.output()["cohorts"]["renew"]["years"]["2029"]
        self.assertEqual(rec["reason"], "explicit_none")

    def test_a09_e1_a10_e1_and_numeric_validation(self):
        self.block()
        self.cohort(basis="per_area")
        self.add("cohort", "c", "bearing_area_m2", "500", year="2027",
                 unit="㎡")
        self.add("cohort", "c", "yield_kg_per_10a", "20", year="2027",
                 unit="kg/10a")
        self.add("cohort", "c", "yield_area_basis", "total_area")
        rec = self.output()["cohorts"]["c"]["years"]["2027"]
        self.assertEqual((rec["state"], rec["reason"]),
                         ("held", "area_basis_mismatch"))
        self.move("bad", "missing", "sale", "-5")
        out = self.output()
        self.assertTrue(any(v["kind"] == "invalid_value" and
                            v["owner"] == "bad" for v in out["violations"]))

    def test_a08_e1_sequential_renewal_and_block_limit(self):
        self.block()
        self.cohort("old", active="2027", until="2027", basis="per_area")
        self.cohort("new", active="2028", until="2028", basis="per_area")
        for cid, year in (("old", "2027"), ("new", "2028")):
            self.add("cohort", cid, "bearing_area_m2", "1000", year=year,
                     unit="㎡")
            self.add("cohort", cid, "yield_kg_per_10a", "10", year=year,
                     unit="kg/10a")
            self.add("cohort", cid, "yield_area_basis", "bearing_area")
        out = self.output()
        self.assertFalse(any(v["kind"] == "block_area_exceeded"
                             for v in out["violations"]))
        self.assertEqual(out["cohorts"]["new"]["years"]["2028"]["state"],
                         "computed")

    def test_a16_fraction_theoretical_and_evidence(self):
        self.block()
        self.cohort(trees="100")
        self.add("cohort", "c", "spacing_m", "3x3", unit="m")
        out = self.output()
        self.assertEqual(out["cohorts"]["c"]["theoretical_trees"],
                         {"exact": "1000/9", "display": "111.11",
                          "precision": "0.01 half-even"})
        self.assertTrue(any(w["kind"] == "evidence_required" for w in out["warnings"]))
        facts = self.fp.core.load(self.fp.root)["facts"].values()
        self.assertEqual([f["value"] for f in facts if f["field_id"] ==
                          "fruit_trees.cohort.c.tree_count"], ["100"])

    def test_a12_y_opening_2028_and_recovery(self):
        self.batch("opening", origin="opening", year="2026", opening="2028",
                   qty="4")
        out = self.output()
        self.assertEqual(list(out["batches"]["opening"]["balances"]), ["2028"])
        self.assertEqual(out["batches"]["opening"]["balances"]["2028"],
                         {"state": "computed", "kg": {"exact": "4"}})
        self.move("early", "opening", "sale", "1", year="2027")
        out = self.output()
        self.assertTrue(any(v.get("reason") == "before_opening" for v in
                            out["violations"]))
        self.move("sell", "opening", "sale", "5", year="2028")
        self.batch("later", origin="opening", year="2029", opening="2029",
                   qty="2")
        self.move("restore", "later", "mix_in", "2", year="2029", dst="opening")
        out = self.output()
        self.assertEqual(out["batches"]["opening"]["balances"]["2028"]["state"],
                         "negative")
        self.assertEqual(out["batches"]["opening"]["balances"]["2029"]["state"],
                         "negative")  # invalid target cannot restore opening
        self.assertEqual(out["completeness"], "partial")

    def test_a12_e1_negative_then_recovery_by_arrival(self):
        self.batch("a", origin="opening", year="2027", qty="4")
        self.batch("b", origin="opening", year="2027", qty="3")
        self.batch("mixed", origin="mix", grade="혼합")
        self.move("in", "a", "mix_in", "4", dst="mixed")
        self.move("sell", "mixed", "sale", "5")
        self.move("recover", "b", "mix_in", "2", year="2028", dst="mixed")
        out = self.output()
        balances = out["batches"]["mixed"]["balances"]
        self.assertEqual(balances["2027"]["state"], "negative")
        self.assertEqual(balances["2028"], {"state": "computed",
                                             "kg": {"exact": "1"}})
        self.assertEqual(out["completeness"], "partial")
        self.assertCountEqual(out["inputs"]["mixed"], [
            {"batch": "a", "quantity": {"exact": "4"}},
            {"batch": "b", "quantity": {"exact": "2"}}])

    def test_a12_e1_indeterminate_from_missing_move(self):
        self.batch("a", origin="opening", year="2027", qty="4")
        self.move("m", "a", "sale", None)
        out = self.output()
        self.assertEqual(out["batches"]["a"]["balances"]["2027"],
                         {"state": "indeterminate", "cause": "m"})

    def test_a15_e1_group_separation_and_exclusion(self):
        self.block("field", area="2000", form="노지")
        self.block("house", area="2000", form="시설")
        self.cohort("c1", "field", area="1000")
        self.cohort("c2", "house", area="1000")
        self.yield_tree("c1")
        self.add("cohort", "c2", "yield_kg_per_tree", "1", year="2027",
                 unit="kg/주")
        out = self.output()
        self.assertEqual(len(out["groups"]), 2)
        self.assertEqual([g["combination"][2] for g in out["groups"]],
                         ["노지", "시설"])
        second = out["groups"][1]
        self.assertFalse(second["complete"])
        self.assertEqual(second["known"], {"exact": "0"})
        self.assertEqual(second["excluded"][0]["cohort"], "c2")

    def test_h_window_open_active_and_missing_range(self):
        self.block()
        self.cohort(active=None, until=None)
        self.add("cohort", "c", "active_from_year", "2027", unit="년")
        out = self.output()
        self.assertEqual(out["not_computable"], [{"cohort": "c",
                          "state": "not_computable", "missing": ["year_range"]}])
        self.fp.fact("start", "fruit_trees.business_start_year", "2027",
                     unit="년")
        out = self.output()
        self.assertEqual(out["plan_window"], {"start_year": "2027",
                                               "end_year": "2036"})
        self.assertEqual(len(out["cohorts"]["c"]["years"]), 10)

    def test_a12_e1_move_table_and_mix_lineage(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.batch("harvest")
        self.batch("opening", origin="opening", qty="10")
        self.batch("rg", origin="regrade", grade="중")
        self.batch("mixed", origin="mix", grade="혼합")
        self.batch("mixed2", origin="mix", grade="혼합")
        self.move("h_sale", "harvest", "sale", "1")
        self.move("o_loss", "opening", "loss", "1")
        self.move("h_rg", "harvest", "regrade", "2", dst="rg")
        self.move("rg_use", "rg", "own_use", "1")
        self.move("rg_mix", "rg", "mix_in", "1", dst="mixed")
        self.move("o_mix", "opening", "mix_in", "1", dst="mixed")
        self.move("mix_sale", "mixed", "sale", "1")
        self.move("mix_rg", "mixed", "regrade", "1", dst="rg")
        self.move("mix_mix", "mixed", "mix_in", "1", dst="mixed2")
        out = self.output()
        reasons = {v.get("move"): v.get("reason") for v in out["violations"]
                   if v.get("kind") == "invalid_move"}
        self.assertNotIn("mix_rg", reasons)
        self.assertNotIn("mix_mix", reasons)
        held = {v.get("move"): v.get("reason") for v in out["violations"]
                if v["kind"] == "held_move"}
        self.assertEqual(held["mix_rg"], "unsupported_move")
        self.assertEqual(held["mix_mix"], "unsupported_move")
        self.assertCountEqual(out["inputs"]["mixed"], [
            {"batch": "rg", "quantity": {"exact": "1"}},
            {"batch": "opening", "quantity": {"exact": "1"}}])
        self.assertEqual(out["batches"]["rg"]["balances"]["2027"]["kg"],
                         {"exact": "0"})

    def test_a12_e1_self_cycle_attribute_mismatch_and_invalid_qty(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.batch("h")
        self.batch("a", origin="regrade", grade="중")
        self.add("batch", "a", "cohort_ref", "c")
        self.batch("b", origin="regrade", grade="하")
        self.batch("other", origin="opening", crop="배", cultivar="B")
        self.move("seed", "h", "regrade", "2", dst="a")
        self.move("self", "a", "regrade", "1", dst="a")
        self.move("cycle1", "a", "regrade", "1", dst="b")
        self.move("cycle2", "b", "regrade", "1", dst="a")
        self.move("mismatch", "other", "regrade", "1", dst="a")
        self.move("negative", "h", "sale", "-5")
        out = self.output()
        reasons = {v["move"]: v["reason"] for v in out["violations"]
                   if v["kind"] == "invalid_move"}
        self.assertEqual(reasons["self"], "self")
        self.assertEqual(reasons["cycle1"], "cycle")
        self.assertEqual(reasons["cycle2"], "cycle")
        self.assertIn("mismatch", reasons, out["violations"])
        self.assertEqual(reasons["mismatch"], "attribute_mismatch")
        self.assertEqual(reasons["seed"], "attribute_mismatch")
        self.assertEqual(reasons["negative"], "invalid_value")
        self.assertEqual(out["batches"]["h"]["balances"]["2027"]["kg"],
                         {"exact": "10"})

    def test_a12_e1_overallocation_and_per_batch_not_farm_total(self):
        self.block()
        self.cohort()
        self.yield_tree(count="20")
        self.batch("a", qty="10")
        self.batch("b", qty="10")
        self.move("a_sale", "a", "sale", "15")
        self.move("b_sale", "b", "sale", "5")
        out = self.output()
        self.assertEqual(out["batches"]["a"]["balances"]["2027"]["state"],
                         "negative")
        self.assertEqual(out["batches"]["b"]["balances"]["2027"]["kg"],
                         {"exact": "5"})
        self.assertEqual(out["allocations"][0]["allocation_state"], "balanced")
        self.batch("extra", qty="1")
        self.assertTrue(any(v["kind"] == "overallocated" for v in
                            self.output()["violations"]))

    def test_a10_e1_wrong_units_and_invalid_yearly_period(self):
        self.block()
        self.cohort(basis="per_area")
        self.add("cohort", "c", "yield_area_basis", "bearing_area")
        self.add("cohort", "c", "bearing_area_m2", "0.1", year="2027",
                 unit="ha")
        self.add("cohort", "c", "yield_kg_per_10a", "10", year="2027",
                 unit="kg/10a")
        out = self.output()
        rec = out["cohorts"]["c"]["years"]["2027"]
        self.assertEqual(rec["state"], "held")
        self.assertNotIn("production", rec)
        self.assertTrue(any(v["kind"] == "unit_mismatch" for v in out["violations"]))
        self.batch("a", origin="opening", year="2027", qty="4")
        self.move("box", "a", "sale", "1", unit="상자")
        out = self.output()
        self.assertEqual(out["batches"]["a"]["balances"]["2027"]["kg"],
                         {"exact": "4"})

    def test_h_outside_window_and_active_period_violation(self):
        self.fp.fact("start", "fruit_trees.business_start_year", "2027",
                     unit="년")
        self.fp.fact("end", "fruit_trees.plan_end_year", "2028", unit="년")
        self.block()
        self.cohort(active="2027", until="2027")
        self.yield_tree(year="2029")
        out = self.output()
        self.assertTrue(any(w["kind"] == "outside_plan_window" and
                            w["year"] == "2029" for w in out["warnings"]))
        self.assertEqual(out["cohorts"]["c"]["years"]["2029"]["state"],
                         "held")

    def test_a15_e1_unknown_combination_remains_separate(self):
        self.block(area="2000")
        self.cohort("a")
        self.cohort("b", cultivar=None)
        self.yield_tree("a")
        self.yield_tree("b")
        out = self.output()
        self.assertEqual(len(out["groups"]), 2)
        self.assertEqual(out["groups"][0]["known"], {"exact": "10"})
        self.assertIsNone(out["groups"][1]["combination"])
        self.assertEqual(out["groups"][1]["cohort"], "b")

    def test_a08_e1_missing_limit_is_unverifiable_and_excluded(self):
        self.block()
        self.cohort(trees=None)
        self.yield_tree()
        out = self.output()
        rec = out["cohorts"]["c"]["years"]["2027"]
        self.assertEqual(rec["state"], "unverifiable")
        self.assertEqual(rec["unverified"], {"exact": "10"})
        self.assertNotIn("production", rec)
        self.assertEqual(out["groups"][0]["known"], {"exact": "0"})
        self.assertFalse(out["groups"][0]["complete"])

    def test_annual_duplicate_blocks_dependent_cohort(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.add("cohort", "c", "bearing_trees", "10", year="2027", unit="주")
        out = self.output()
        self.assertTrue(any(v["kind"] == "duplicate_answer" for v in
                            out["violations"]))
        rec = out["cohorts"]["c"]["years"]["2027"]
        self.assertEqual(rec["state"], "held")
        self.assertNotIn("production", rec)

    def test_a12_y_next_year_sale_and_before_harvest(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.batch("h")
        self.move("early", "h", "sale", "1", year="2026")
        self.move("next", "h", "sale", "3", year="2028")
        out = self.output()
        self.assertTrue(any(v.get("reason") == "before_harvest" for v in
                            out["violations"]))
        self.assertEqual(out["batches"]["h"]["balances"]["2028"],
                         {"state": "computed", "kg": {"exact": "7"}})

    def test_scope_mismatch_holds_involved_instance(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.fp.fact("wrong_scope", "fruit_trees.cohort.c.layout_note",
                     "synthetic", scope="farm")
        out = self.output()
        self.assertTrue(any(v["kind"] == "scope_mismatch" for v in
                            out["violations"]))
        self.assertEqual(out["cohorts"]["c"]["years"]["2027"]["state"],
                         "held")

    def test_regrade_conflicting_roots_do_not_choose_first(self):
        self.batch("a", origin="opening", crop="사과", cultivar="A", grade="상")
        self.batch("b", origin="opening", crop="배", cultivar="B", grade="상")
        self.batch("rg", origin="regrade", grade="중")
        self.move("from_a", "a", "regrade", "1", dst="rg")
        self.move("from_b", "b", "regrade", "1", dst="rg")
        out = self.output()
        rejected = {v["move"] for v in out["violations"] if
                    v["kind"] == "invalid_move" and
                    v["reason"] == "attribute_mismatch"}
        self.assertEqual(rejected, {"from_a", "from_b"})
        self.assertEqual(out["batches"]["a"]["balances"]["2027"]["kg"],
                         {"exact": "10"})

    def test_r1_invalid_static_field_recorded_once(self):
        self.block()
        self.cohort(until="20X7")
        for year in ("2027", "2028", "2029"):
            self.yield_tree(year=year)
        out = self.output()
        repeated = [v for v in out["violations"] if v == {
            "kind": "invalid_value", "owner": "c", "field": "active_to_year"}]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(out["issue_count"], len(out["violations"]) +
                         len(out["not_computable"]))
        keys = [json.dumps(v, sort_keys=True, ensure_ascii=False)
                for v in out["violations"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_r2_regrade_two_harvest_batches_same_origin_kind(self):
        self.block()
        self.cohort()
        self.yield_tree(count="20")
        self.batch("a", grade="특")
        self.batch("b", grade="상")
        self.batch("rg", origin="regrade", grade="중")
        self.move("a_to_rg", "a", "regrade", "1", dst="rg")
        self.move("b_to_rg", "b", "regrade", "1", dst="rg")
        out = self.output()
        self.assertFalse(any(v.get("move") in ("a_to_rg", "b_to_rg")
                             for v in out["violations"]))
        self.assertEqual(out["batches"]["rg"]["balances"]["2027"],
                         {"state": "computed", "kg": {"exact": "2"}})

    def test_r3_chained_regrade_conflict_independent_of_ids(self):
        for first, second in (("m3", "m4"), ("z9", "a0")):
            with self.subTest(ids=(first, second)):
                self.fp = ready(self)
                self.n = 0
                self.batch("apple", origin="opening", crop="사과", cultivar="A",
                           grade="특")
                self.batch("pear", origin="opening", crop="배", cultivar="B",
                           grade="특")
                self.batch("r1", origin="regrade", grade="상")
                self.batch("r2", origin="regrade", grade="상")
                self.batch("r3", origin="regrade", grade="중")
                self.move("m1", "apple", "regrade", "1", dst="r1")
                self.move("m2", "pear", "regrade", "1", dst="r2")
                self.move(first, "r1", "regrade", "1", dst="r3")
                self.move(second, "r2", "regrade", "1", dst="r3")
                out = self.output()
                rejected = {v["move"] for v in out["violations"] if
                            v["kind"] == "invalid_move" and
                            v["reason"] == "attribute_mismatch"}
                self.assertEqual(rejected, {first, second})
                self.assertEqual(out["batches"]["r3"]["balances"], {})

    def test_r4_duplicate_block_holds_dependent_cohort(self):
        self.block()
        self.add("block", "b", "area_m2", "1000", unit="㎡")
        self.cohort()
        self.yield_tree()
        out = self.output()
        rec = out["cohorts"]["c"]["years"]["2027"]
        self.assertEqual(rec["state"], "held")
        self.assertEqual(rec["reason"], "instance_issue")
        self.assertNotIn("production", rec)
        self.assertNotIn("unverified", rec)
        self.assertFalse(any(v["kind"] == "block_area_exceeded"
                             for v in out["violations"]))

    def test_final1_before_arrival_and_no_arrival(self):
        self.batch("source", origin="opening", year="2028", opening="2028",
                   qty="10", grade="상")
        self.batch("rg", origin="regrade", grade="중")
        self.move("arrive", "source", "regrade", "10", year="2028", dst="rg")
        self.move("early", "rg", "sale", "5", year="2027")
        self.batch("empty_mix", origin="mix")
        self.move("empty_sale", "empty_mix", "sale", "1", year="2027")
        out = self.output()
        reasons = {v["move"]: v["reason"] for v in out["violations"]
                   if v["kind"] == "invalid_move"}
        self.assertEqual(reasons["early"], "before_arrival")
        self.assertEqual(reasons["empty_sale"], "no_arrival")
        self.assertEqual(out["batches"]["rg"]["balances"]["2028"],
                         {"state": "computed", "kg": {"exact": "10"}})
        self.assertEqual(out["completeness"], "partial")

    def test_final2_different_cohorts_never_merge_by_regrade(self):
        for specified in (False, True):
            with self.subTest(target_cohort_ref=specified):
                self.fp = ready(self)
                self.n = 0
                self.block(area="2000")
                for cid in ("c1", "c2"):
                    self.cohort(cid, area="1000")
                    self.yield_tree(cid)
                self.batch("a", cid="c1", grade="특")
                self.batch("b", cid="c2", grade="상")
                self.batch("rg", origin="regrade", grade="중")
                if specified:
                    self.add("batch", "rg", "cohort_ref", "c1")
                self.move("a_in", "a", "regrade", "1", dst="rg")
                self.move("b_in", "b", "regrade", "1", dst="rg")
                out = self.output()
                rejected = {v["move"] for v in out["violations"] if
                            v["kind"] == "invalid_move" and
                            v["reason"] == "attribute_mismatch"}
                self.assertEqual(rejected, {"a_in", "b_in"})
                self.assertEqual(out["batches"]["rg"]["balances"], {})
                self.assertEqual(out["completeness"], "partial")

    def test_final3_held_harvest_batch_propagates_to_stock_and_allocation(self):
        self.block()
        self.cohort()
        self.yield_tree()
        self.add("batch", "h", "label", "h")
        self.add("batch", "h", "origin", "harvest")
        self.add("batch", "h", "cohort_ref", "c")
        self.add("batch", "h", "harvest_year", "2027", unit="년")
        self.add("batch", "h", "quantity_kg", "10", unit="kg")
        self.fp.fact("bad_grade", "fruit_trees.batch.h.grade", "상",
                     scope="farm")
        self.move("sell", "h", "sale", "1")
        out = self.output()
        self.assertEqual(out["batches"]["h"]["state"], "held")
        self.assertEqual(out["batches"]["h"]["balances"]["2027"],
                         {"state": "indeterminate", "cause": "batch:h"})
        self.assertEqual(out["allocations"][0]["state"], "unverifiable")
        self.assertEqual(out["allocations"][0]["reason"], "batch_held")
        self.assertNotIn("allocated", out["allocations"][0])
        self.assertTrue(any(v.get("move") == "sell" and
                            v["kind"] == "held_move" for v in out["violations"]))
        self.assertEqual(out["completeness"], "partial")

    def test_final4_explicit_unknown_end_is_not_open_period(self):
        for state in ("unknown", "explicit_none"):
            with self.subTest(state=state):
                self.fp = ready(self)
                self.n = 0
                self.fp.fact("start", "fruit_trees.business_start_year", "2027",
                             unit="년")
                self.block()
                self.cohort(until=None)
                self.add("cohort", "c", "active_to_year", None, state=state,
                         unit="년")
                self.yield_tree(year="2028")
                out = self.output()
                years = out["cohorts"]["c"]["years"]
                self.assertEqual(list(years), ["2028"])
                self.assertEqual(years["2028"]["state"], "unverifiable")
                self.assertEqual(years["2028"]["reason"],
                                 "active_period_" + state)
                self.assertEqual(years["2028"]["unverified"], {"exact": "10"})
                self.assertNotIn("production", years["2028"])
                self.assertEqual(out["groups"][0]["known"], {"exact": "0"})
                self.assertEqual(out["groups"][0]["excluded"][0]["cohort"], "c")
                self.assertEqual(out["completeness"], "partial")

    def test_final5_missing_opening_attributes_and_bad_cohort_type(self):
        self.add("batch", "opening", "label", "opening")
        self.add("batch", "opening", "origin", "opening")
        out = self.output()
        self.assertEqual(out["batches"]["opening"]["state"], "held")
        missing = [v for v in out["violations"] if v["kind"] ==
                   "missing_attribute" and v["owner"] == "opening"]
        self.assertEqual(missing[0]["fields"], ["crop_species", "cultivar",
                         "harvest_year", "opening_year", "grade", "quantity_kg"])
        self.assertEqual(out["completeness"], "partial")
        self.block()
        self.cohort(crop=["사과"])
        self.yield_tree()
        out = self.output()
        self.assertTrue(any(v["kind"] == "invalid_value" and
                            v["field"] == "crop_species" for v in out["violations"]))
        unresolved = [g for g in out["groups"] if g["combination"] is None]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["cohort"], "c")

    def test_final6_long_terminating_decimal_exact_and_balanced(self):
        q = "0." + str(5 ** 100).rjust(100, "0")
        self.block()
        self.cohort()
        self.yield_tree(count="1", each=q)
        self.batch("h", qty=q)
        out = self.output()
        production = out["cohorts"]["c"]["years"]["2027"]["production"]
        self.assertEqual(production, {"exact": q})
        self.assertEqual(out["allocations"][0]["allocation_state"], "balanced")
        self.assertEqual(Fraction(production["exact"]), Fraction(q))
        self.assertEqual(Fraction(out["allocations"][0]["allocated"]["exact"]),
                         Fraction(q))
        exact = self.mod.wrapped(Fraction(1, 2 ** 200))["exact"]
        self.assertEqual(Fraction(exact), Fraction(1, 2 ** 200))

    def test_final5_bad_batch_text_and_channel_hold_dependents(self):
        self.batch("bad", origin="opening", crop=["사과"])
        self.batch("good", origin="opening")
        self.move("bad_channel", "good", "sale", "1")
        self.add("move", "bad_channel", "channel", ["온라인"])
        out = self.output()
        self.assertEqual(out["batches"]["bad"]["state"], "held")
        self.assertTrue(any(v["kind"] == "invalid_value" and
                            v.get("owner") == "bad" and
                            v.get("field") == "crop_species" for v in
                            out["violations"]))
        self.assertTrue(any(v["kind"] == "held_move" and
                            v.get("move") == "bad_channel" for v in
                            out["violations"]))
        self.assertEqual(out["batches"]["good"]["balances"]["2027"]["kg"],
                         {"exact": "10"})

    def test_review3_time_invalid_arrival_cannot_poison_valid_regrade(self):
        for apple_in, early, pear_in in (
                ("a_in", "b_early", "c_in"),
                ("z_in", "a_early", "m_in")):
            with self.subTest(ids=(apple_in, early, pear_in)):
                self.fp = ready(self)
                self.n = 0
                self.batch("apple", origin="opening", crop="사과",
                           cultivar="A", year="2027", qty="10", grade="특")
                self.batch("pear", origin="opening", crop="배",
                           cultivar="B", year="2027", qty="5", grade="특")
                self.batch("r1", origin="regrade", grade="상")
                self.batch("r2", origin="regrade", grade="중")
                self.move(apple_in, "apple", "regrade", "10",
                          year="2028", dst="r1")
                self.move(early, "r1", "regrade", "5",
                          year="2027", dst="r2")
                self.move(pear_in, "pear", "regrade", "5",
                          year="2027", dst="r2")
                out = self.output()
                reasons = {v["move"]: v["reason"] for v in out["violations"]
                           if v["kind"] == "invalid_move"}
                self.assertEqual(reasons.get(early), "before_arrival")
                self.assertNotIn(pear_in, reasons)
                self.assertEqual(out["batches"]["r2"]["balances"]["2027"],
                                 {"state": "computed", "kg": {"exact": "5"}})
                self.assertEqual(out["completeness"], "partial")

    def test_review3_attribute_rejection_moves_first_arrival_later(self):
        self.batch("early", origin="opening", year="2027", qty="5",
                   grade="중")
        self.batch("late", origin="opening", year="2028", qty="5",
                   grade="상")
        self.batch("rg", origin="regrade", grade="중")
        self.move("early_in", "early", "regrade", "5",
                  year="2027", dst="rg")
        self.move("late_in", "late", "regrade", "5",
                  year="2028", dst="rg")
        self.move("sale", "rg", "sale", "1", year="2027")
        out = self.output()
        reasons = {v["move"]: v["reason"] for v in out["violations"]
                   if v["kind"] == "invalid_move"}
        self.assertEqual(reasons["early_in"], "grade_mismatch")
        self.assertEqual(reasons["sale"], "before_arrival")
        self.assertNotIn("late_in", reasons)
        self.assertEqual(out["batches"]["rg"]["balances"]["2028"],
                         {"state": "computed", "kg": {"exact": "5"}})

    def test_review4_restored_arrival_revalidates_sale(self):
        for ids, reverse in (
                (("a_bad", "b_apple", "c_early", "d_pear", "e_sale"), False),
                (("z_bad", "a_apple", "y_early", "b_pear", "c_sale"), True)):
            with self.subTest(ids=ids, reverse=reverse):
                self.fp = ready(self)
                self.n = 0
                self.batch("apple", origin="opening", crop="사과", cultivar="A",
                           year="2026", opening="2027", qty="10", grade="특")
                self.batch("pear", origin="opening", crop="배", cultivar="B",
                           year="2026", opening="2027", qty="10", grade="상")
                self.batch("r1", origin="regrade", grade="상")
                self.batch("r2", origin="regrade", grade="중")
                actions = (
                    (ids[0], "pear", "regrade", "5", "2027", "r1"),
                    (ids[1], "apple", "regrade", "10", "2028", "r1"),
                    (ids[2], "r1", "regrade", "5", "2027", "r2"),
                    (ids[3], "pear", "regrade", "5", "2027", "r2"),
                    (ids[4], "r2", "sale", "1", "2027", None),
                )
                for mid, src, kind, qty, year, dst in (
                        reversed(actions) if reverse else actions):
                    self.move(mid, src, kind, qty, year=year, dst=dst)
                out = self.output()
                reasons = {v["move"]: v["reason"] for v in out["violations"]
                           if v["kind"] == "invalid_move"}
                self.assertEqual(reasons, {ids[0]: "grade_mismatch",
                                           ids[2]: "before_arrival"})
                self.assertEqual(out["batches"]["r2"]["balances"]["2027"],
                                 {"state": "computed", "kg": {"exact": "4"}})
                self.assertEqual(out["completeness"], "partial")

    def test_review4_unresolved_dependency_is_held_at_iteration_limit(self):
        self.batch("source", origin="opening")
        self.batch("rg", origin="regrade", grade="중")
        self.move("in", "source", "regrade", "5", dst="rg")
        self.move("out", "rg", "sale", "1")
        self.move("independent", "source", "sale", "1")
        self.batch("other_rg", origin="regrade", grade="특")
        self.move("other_in", "source", "regrade", "3", dst="other_rg")
        self.move("other_out", "other_rg", "sale", "1")
        calls = 0

        def oscillating_time(moves, meta, candidates, reasons):
            nonlocal calls
            calls += 1
            reason = "no_arrival" if calls % 2 else "before_arrival"
            reasons["out"] = reason
            next(m for m in moves if m["id"] == "out").update(
                state="invalid", reason=reason)
            return True

        with patch.object(self.mod, "_temporal_filter", side_effect=oscillating_time):
            out = self.output()
        held = {v["move"]: v["reason"] for v in out["violations"]
                if v["kind"] == "held_move"}
        self.assertEqual(held, {"in": "unresolved_dependency",
                                "out": "unresolved_dependency"})
        self.assertGreater(calls, 2)
        self.assertEqual(out["batches"]["source"]["balances"]["2027"],
                         {"state": "computed", "kg": {"exact": "6"}})
        self.assertEqual(out["batches"]["other_rg"]["balances"]["2027"],
                         {"state": "computed", "kg": {"exact": "2"}})
