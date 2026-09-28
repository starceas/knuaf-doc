"""BC closure checks on public contracts and fail-closed plan/verify gates."""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
import gg_hort_workbook as h


class BasisClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = ROOT / "skills/knuaf-doc/references/hort-env-systems"
        cls.m = json.loads((p / "finance-transform.json").read_text())
        cls.p = json.loads((p / "finance-params.json").read_text())
        cls.r = json.loads((p / "finance-verify-classes.json").read_text())

    def test_fixed_counts_and_removed_contract(self):
        self.assertEqual((self.m["entry_count"], self.p["param_count"]), (4103, 1379))
        keys = self.p["parameters"]
        self.assertFalse(any(k.startswith("cost.material.") and k.endswith((".area_kind", ".use_ratio"))
                             or k.startswith("cost.overhead.") and k.endswith(".coverage_ratio") for k in keys))
        entries = {(e["sheet"], e["cell"]): e for e in self.m["entries"]}
        self.assertFalse(any("교수 확인 전 초안" in str(e) for e in entries.values()))
        for sh, cells in (("5. 원리금상환계획", ["K8", "K9", *[f"L{i}" for i in range(8, 14)]]),
                          (" 7. 판매계획", ["B126", "C126"]),
                          ("1. 자산조사", ["B2"]), ("18. 현금흐름계획", ["B2"])):
            for cell in cells:
                self.assertNotIn((sh, cell), entries)
        for e in self.m["entries"]:
            f = e.get("after_formula") or ""
            same_sheet = re.sub(r"'[^']+'!\$?[A-Z]+\$?\d+", "", f)
            if e["sheet"] == " 7. 판매계획":
                self.assertNotIn("$C$126", f)
            if e["sheet"] == "9. 영농자재구매계획":
                self.assertFalse(re.search(r"(?<![A-Za-z0-9])\$?[VY]\$?(?:2[0-9]|3[0-9]|4[0-9]|5[0-3])(?!\d)", same_sheet))
            if e["sheet"] == "12 .경비계획":
                self.assertFalse(re.search(r"(?<![A-Za-z0-9])\$?T\$?(?:[1-7][0-9]|8[01])(?!\d)", same_sheet))

    def test_material_basis_required_and_positive(self):
        key = "cost.material.y1.item1.basis_m2"
        spec = self.p["parameters"][key]
        self.assertEqual(spec["required_if"], {"group_active": "cost.material.y1.item1"})
        with self.assertRaisesRegex(h.Held, "material_basis_invalid"):
            h._check_material_basis({key: 0})
        # Astra IMPL-1 non-blocking 2: negative, bool and non-numeric W also hold.
        for bad in (-1, True, False, "1000", None):
            with self.subTest(bad=bad), self.assertRaisesRegex(h.Held, "material_basis_invalid"):
                h._check_material_basis({key: bad})
        h._check_material_basis({key: 1000})

    def test_sales_unit_gate(self):
        good = {"sales.quantity_unit": "kg", "sales.price_per_unit": "kg",
                "sales.currency": "원", "sales.package_spec": "10kg"}
        h._check_sales_units(good)
        with self.assertRaisesRegex(h.Held, "sales_unit_mismatch"):
            h._check_sales_units({**good, "sales.price_per_unit": "box"})

    def test_plan_facts_missing_zero_and_unit_mismatch(self):
        all_params = self.p["parameters"]

        def facts(values):
            return {"facts": {str(i): {
                "id": str(i), "field_id": "hort_env_systems.fin." + key,
                "kind": "reported_fact", "value": value, "unit": None,
                "value_type": "text", "period": None, "scope": "farm",
                "answer_state": "provided", "verification": "unreviewed",
                "source_refs": [{"id": "s1", "locator": "synthetic", "revision": 1}],
                "revision": 1} for i, (key, value) in enumerate(values.items())}}

        prefix = "cost.material.y1.item1."
        params = {k: all_params[k] for k in (prefix + "name", prefix + "basis_qty",
                                               prefix + "basis_m2")}
        values = {prefix + "name": "synthetic", prefix + "basis_qty": 10}
        with self.assertRaisesRegex(h.Held, "required_fact_missing"):
            h._facts(facts(values), params)
        with self.assertRaisesRegex(h.Held, "material_basis_invalid"):
            h._facts(facts({**values, prefix + "basis_m2": 0}), params)
        h._facts(facts({**values, prefix + "basis_m2": 1000}), params)

        sales = {k: all_params[k] for k in ("sales.quantity_unit", "sales.price_per_unit",
                                            "sales.currency", "sales.package_spec")}
        good = {"sales.quantity_unit": "kg", "sales.price_per_unit": "kg",
                "sales.currency": "원", "sales.package_spec": "10kg"}
        h._facts(facts(good), sales)
        with self.assertRaisesRegex(h.Held, "sales_unit_mismatch"):
            h._facts(facts({**good, "sales.price_per_unit": "box"}), sales)

    def test_loan_identity_blank_year_and_boundary(self):
        c = {"1. 자산조사": {"E53": (None, 7, False, False)},
             "5. 원리금상환계획": {"B43": (None, 2026, False, False),
                                "C43": (None, 10, False, False),
                                "E43": (None, 3, False, False),
                                "B44": (None, "", False, False),
                                "B45": (None, 2027, False, False)},
             "4.투자계획": {},}
        for slot in range(1, 6):
            _, rows = h._loan_slot_rows(slot)
            for r in rows:
                c["5. 원리금상환계획"].setdefault(f"B{r}", ("str", "", True, True))
        for k in range(5):
            c["5. 원리금상환계획"][f"C{8+k}"] = (None, 0, False, False)
            c["4.투자계획"][f"F{8+k}"] = (None, 0, False, False)
        h._verify_loan_identities(c, 2027)
        blank = c["5. 원리금상환계획"].pop("B44")
        with self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
            h._verify_loan_identities(c, 2027)
        c["5. 원리금상환계획"]["B44"] = blank
        c["1. 자산조사"]["E53"] = (None, 8, False, False)
        with self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
            h._verify_loan_identities(c, 2027)
        c["1. 자산조사"]["E53"] = (None, 7, False, False)
        h._verify_loan_identities(c, 2027)
        # Astra IMPL-1 non-blocking 2: each funding year must hold on a
        # mismatch, a blank amount and an error cache, on either side.
        for k in range(5):
            for sheet, ref in (("5. 원리금상환계획", f"C{8+k}"), ("4.투자계획", f"F{8+k}")):
                good = c[sheet][ref]
                for bad in ((None, 1, False, False), (None, None, False, False),
                            ("e", "#N/A", False, False)):
                    c[sheet][ref] = bad
                    with self.subTest(sheet=sheet, ref=ref, bad=bad), \
                            self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
                        h._verify_loan_identities(c, 2027)
                del c[sheet][ref]
                with self.subTest(sheet=sheet, ref=ref, bad="absent"), \
                        self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
                    h._verify_loan_identities(c, 2027)
                c[sheet][ref] = good
        h._verify_loan_identities(c, 2027)


if __name__ == "__main__":
    unittest.main()
