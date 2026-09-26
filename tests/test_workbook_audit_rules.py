"""Synthetic rule positives and negatives, perturbation and result guards."""
import copy
import json
from pathlib import Path
import unittest

from tests.test_workbook_audit_evaluator import audit_module


NAMES = ["1. Opening", "3. Invest", "4. Debt", "8. Labor", "9 .Costs", "10. Depreciation",
         "11. Cost", "12 P&L", "13. Balance", "14. Cash", "15. Income"]


def synthetic(formulas=None, values=None):
    a = audit_module()
    b = a.Workbook()
    b.sheets = NAMES[:]
    for (n, addr), text in (formulas or {}).items():
        sheet = a.sheet_number(b, n)
        b.formulas[sheet, addr] = a.FORMULA(text, None, None)
    for (n, addr), value in (values or {}).items():
        b.values[a.sheet_number(b, n), addr] = value
    return a, b


def blocks():
    return {
      "variants": {"X01": {"later_loan": "H25"}},
      "periods": {"11": {"columns": "CD", "first_year": 2026}},
      "regions": {"1": [{"range":"E9:F58", "role":"input"}],
                  "11": [{"range":"C16:D38", "role":"calculation"}]},
      "year_pattern_rows": {"11": [25]},
      "subtotals": [], "constant_siblings": {},
      "continuity_links": [{"sheet":"4","target":"C43","source":"E42",
                            "meaning":"loan carry"}],
      "base_date_cell": {"sheet":"1", "cell":"B2"},
      "first_balance_year_cell": {"sheet":"13", "cell":"C5"},
    }


def candidates(a, b, config):
    entries, _ = a.inventory(b)
    return a.analyze_rules(b, config, entries)


class RuleTests(unittest.TestCase):
    def assert_rule(self, rule, yes, no, config=None):
        config = config or blocks()
        a, b = synthetic(*yes)
        self.assertTrue(any(x["rule_id"] == rule for x in candidates(a,b,config)), rule)
        a, b = synthetic(*no)
        self.assertFalse(any(x["rule_id"] == rule for x in candidates(a,b,config)), rule)

    def test_r1_year_pattern(self):
        f = {("11","C25"):"SUM(C26:C27)", ("11","D25"):"SUM(C26:C27)"}
        good = dict(f, **{})
        good[("11","D25")] = "SUM(D26:D27)"
        self.assert_rule("R1", (f,{}), (good,{}))

    def test_r1_repeated_absolute_annual_dependency_and_fixed_exception(self):
        a,b = synthetic({("11","C25"):"SUM($C$26:$C$27)",
                         ("11","D25"):"SUM($C$26:$C$27)"},{})
        self.assertTrue(any(x["rule_id"] == "R1" and "D25" in x["cells"]
                            for x in candidates(a,b,blocks())))
        config = blocks()
        config["intentional_fixed_refs"] = {"11":[{"sheet":"11","cell":"B2"}]}
        a,b = synthetic({("11","C25"):"$B$2",("11","D25"):"$B$2"},
                        {("11","B2"):0.1})
        self.assertFalse(any(x["rule_id"] == "R1" for x in candidates(a,b,config)))

    def test_r1_real_misc_fee_pattern_is_detected_generically(self):
        a,b = synthetic({("11","C25"):"'9 .Costs'!$C29",
                         ("11","D25"):"'9 .Costs'!$C29"},{})
        self.assertTrue(any(x["rule_id"] == "R1" and "D25" in x["cells"]
                            for x in candidates(a,b,blocks())))

    def test_r2_blank_calculation_reference(self):
        f = {("11","C35"):"C31+C38"}
        self.assert_rule("R2", (f,{("11","C31"):2}),
                         (f,{("11","C31"):2,("11","C38"):3}))
        a = audit_module()
        self.assertTrue(a.in_rectangle("D19", "D19"))
        self.assertFalse(a.in_rectangle("D18", "D19"))

    def test_r3_dependency_subtotal_membership(self):
        config = blocks()
        config["subtotals"] = [{"sheet":"11","row":16,"members":[17,28],"columns":"C"}]
        self.assert_rule("R3", ({("11","C16"):"C17"},{}),
                         ({("11","C16"):"C17+C28"},{}), config)

    def test_r4_constant_sibling(self):
        config = blocks(); config["constant_siblings"] = {"10":["H20"]}
        self.assert_rule("R4", ({},{("10","H20"):2}),
                         ({("10","H20"):"H8"},{("10","H8"):2}), config)

    def test_r5_unreachable_opening_input(self):
        self.assert_rule("R5", ({},{("1","E16"):7}),
                         ({("11","C35"):"'1. Opening'!E16"},{("1","E16"):7}))

    def test_r6_closing_to_opening(self):
        self.assert_rule("R6", ({("4","C43"):"C42"},{}),
                         ({("4","C43"):"E42"},{}))

    def test_r7_cash_link(self):
        config = blocks()
        f = {("12","C12"):"1"}
        a,b = synthetic(f,{})
        self.assertTrue(any(x["rule_id"] == "R7" and "C12" in x["cells"]
                            for x in candidates(a,b,config)))
        f[("14","D18")] = "'12 P&L'!C12"
        a,b = synthetic(f,{})
        self.assertFalse(any(x["rule_id"] == "R7" and x["sheet"] == a.sheet_number(b,12)
                             and "C12" in x["cells"] for x in candidates(a,b,config)))

    def test_r8_period_mismatch(self):
        labels = {("1","B2"):"Opening 2026년", ("13","C5"):"2025년"}
        aligned = dict(labels); aligned[("1","B2")] = "Opening 2025년"
        self.assert_rule("R8", ({},labels), ({},aligned))

    def test_perturbation_determinism(self):
        a,b = synthetic({("11","C16"):"C17+C28"},{("11","C17"):2,("11","C28"):4})
        key = (a.sheet_number(b,11),"C28")
        output = (a.sheet_number(b,11),"C16")
        first = a.perturb(b,key,3,[output])
        self.assertEqual(first,a.perturb(b,key,3,[output]))
        self.assertEqual(first["outputs"][0]["delta"],3)
        self.assertEqual(b.values[key],4)

    def test_opening_liability_probe_discriminates_in_memory_correction(self):
        formulas = {("13","C25"):"'1. Opening'!E58",("13","C28"):"C22",
                    ("13","D25"):"'4. Debt'!F8"}
        values = {("1","E58"):10,("13","C22"):100,("4","F8"):20}
        a,b = synthetic(formulas,values)
        # The in-memory formula uses the real sheet name, so align the fixture.
        b.sheets[b.sheets.index("4. Debt")] = "4. 원리금상환계획"
        b.values[("4. 원리금상환계획","F8")] = b.values.pop(("4. Debt","F8"))
        balance = a.sheet_number(b,13)
        b.formulas[balance,"D25"] = a.FORMULA("'4. 원리금상환계획'!F8",None,None)
        probe = a.opening_liability_probe(b)
        self.assertEqual(probe["comparison"],
                         {"original":{"opening_equity":0,"opening_imbalance":-7,"forecast_debt":0},
                          "corrected_in_memory":{"opening_equity":-7,"opening_imbalance":0,"forecast_debt":7}})
        self.assertEqual(b.formulas[balance,"D25"].text,"'4. 원리금상환계획'!F8")

    def test_impact_closure_all_cells_range_plus_shared_and_cross_sheet(self):
        formulas = {
            ("11","D25"):"SUM(D26:D27)",
            ("11","D16"):"D17+D20+D25+D21+D29+D30",
            ("11","D31"):"D16+D5",
            ("12","D11"):"'11. Cost'!D16",
            ("13","E34"):"'12 P&L'!D11",
            ("14","E22"):"'12 P&L'!D11",
            ("15","F14"):"'11. Cost'!D31",
        }
        a,b = synthetic(formulas,{("11","D26"):2,("11","D27"):3})
        cost = a.sheet_number(b,11)
        # D25 stands in for the effective text of a shared follower.
        b.formulas[cost,"D25"] = a.FORMULA("SUM(D26:D27)","C25",None)
        entries,_ = a.inventory(b)
        deps = a.graph(b,entries)
        impact = a.impact_for(deps,cost,["D25","D26"])
        self.assertEqual(impact["dependent_cell_count"],6)
        self.assertEqual(impact["category_counts"],
                         {"production_cost":2,"P&L":1,"balance":1,"cash":1,"other":1})
        self.assertIn({"sheet":cost,"cell":"D16","category":"production_cost"},
                      impact["dependent_cells"])
        # The range member alone must reach the shared follower and the chain.
        self.assertEqual(a.impact_for(deps,cost,["D26"])["dependent_cell_count"],7)

    def test_pinned_xr02_impact_requires_real_check(self):
        root = Path(__file__).resolve().parents[1]
        for ref in ("x01","x02"):
            path = root / "skills/knuaf-doc/references/common-workbooks/formula-audit" / (ref+".json")
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(audit_module().validate_xr02_impact(doc))

    def test_both_source_xr_maps_and_unresolved_timing_dispositions(self):
        a = audit_module()
        self.assertEqual(set(a.XR_FILE_CELLS), {"X01","X02"})
        for ref in ("X01","X02"):
            self.assertEqual(set(a.XR_FILE_CELLS[ref]), set(range(1,23)))
            path = (Path(__file__).resolve().parents[1] /
                    "skills/knuaf-doc/references/common-workbooks/formula-audit" /
                    (ref.lower()+".json"))
            findings = {f["id"]:f for f in json.loads(path.read_text(encoding="utf-8"))["findings"]}
            for index in range(1,23):
                finding = findings[f"XR-{index:02d}"]
                self.assertEqual(finding["cells"], a.XR_FILE_CELLS[ref][index])
                self.assertEqual([x["cell"] for x in finding["cell_formulas"]], finding["cells"])
            for index in (12,13,14,15):
                finding = findings[f"XR-{index:02d}"]
                self.assertEqual(finding["verdict"],"open")
                self.assertEqual(finding["engine_disposition"],"undecided")
                self.assertEqual(finding["exception_decision"],"unresolved")
                self.assertTrue(finding["needs_professor_interpretation"])
                self.assertTrue(finding["open_reason"])
                self.assertTrue(finding["unresolved_dependencies"])
        self.assertEqual(a.XR_FILE_CELLS["X01"][11],["D17"])
        self.assertEqual(a.XR_FILE_CELLS["X02"][11],["D18"])
        self.assertTrue(all(cell.startswith(("J","K")) for cell in a.XR_FILE_CELLS["X02"][8]))

    def test_completeness_and_content_policy(self):
        a = audit_module()
        finding = {"id":"XR-01", "sheet":"1. Opening", "cells":["C1"],
                   "formula":None,"cell_formulas":[{"cell":"C1","formula":None,"cell_kind":"blank"}],
                   "expected_relation":"defined", "period":["2026"],
                   "meaning":{"text":"ratio", "semantic_authority":"inferred"},
                   "normal_exception":None, "normal_exception_reason":"unresolved",
                   "exception_decision":"unresolved",
                   "impact":{"dependent_cell_count":0,"categories":[],
                             "category_counts":{"production_cost":0,"P&L":0,"balance":0,
                                                "cash":0,"other":0},"dependent_cells":[]},
                   "verdict":"open", "evidence":[{"rule_id":"R1","provenance":"workbook_derived"}],
                   "correction_proposal":None,"needs_professor_interpretation":True,
                   "engine_disposition":"undecided","assumptions":[],
                   "tested_conditions":[],"unresolved_dependencies":[],"open_reason":"unknown"}
        self.assertTrue(a.required_fields_check(finding))
        broken = copy.deepcopy(finding); del broken["meaning"]
        with self.assertRaises(ValueError): a.required_fields_check(broken)
        broken = copy.deepcopy(finding); broken["verdict"] = "defect"
        with self.assertRaises(ValueError): a.required_fields_check(broken)
        doc = {"schema":"knuaf-workbook-formula-audit/v1", "inventory":[],
               "findings":[dict(finding,id=f"XR-{i:02d}") for i in range(1,23)]}
        self.assertTrue(a.content_policy_check(doc))
        doc["cached_value"] = 12
        with self.assertRaises(ValueError): a.content_policy_check(doc)
        del doc["cached_value"]
        del doc["findings"][-1]
        with self.assertRaises(ValueError): a.content_policy_check(doc)


if __name__ == "__main__":
    unittest.main()
