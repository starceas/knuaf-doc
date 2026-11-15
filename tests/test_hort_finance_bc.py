"""BC closure checks on public contracts and fail-closed plan/verify gates."""
from collections import Counter
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
import gg_hort_workbook as h


def _facts(values):
    """Minimal project-shaped facts payload for a `_facts` gate."""
    return {"facts": {str(i): {
        "id": str(i), "field_id": "hort_env_systems.fin." + key,
        "kind": "reported_fact", "value": value, "unit": None,
        "value_type": "text", "period": None, "scope": "farm",
        "answer_state": "provided", "verification": "unreviewed",
        "source_refs": [{"id": "s1", "locator": "synthetic", "revision": 1}],
        "revision": 1} for i, (key, value) in enumerate(values.items())}}


# --- Land allocation oracle (W-L) -------------------------------------------
# Pure arithmetic over the design's combination table. The row split is the
# reference; `_cur` is the shipped-land formula, `_p4` the reviewer proposal,
# `_r3` the parcel-wise formula that this release ships.

LAND_KINDS = ("논", "밭", "", "과수원")
LAND_OK = ("논", "밭")
LAND_INVESTS = {
    "없음": [],
    "논 1줄(50000, 2027)": [("논", 50000, 2027)],
    "밭 1줄(50000, 2027)": [("밭", 50000, 2027)],
    "논+밭 2줄(50000/30000, 2027)": [("논", 50000, 2027), ("밭", 30000, 2027)],
    "논 1줄(50000, 2029)": [("논", 50000, 2029)],
    "시설 1줄(land_class=논)": [("시설", 50000, 2027)],
    "토지 1줄 land_class 빈칸": [("", 50000, 2027)],
}
LAND_YEARS = (2026, 2027, 2028, 2029, 2030)


def _land_truth(b8, e8, b9, e9, inv, year):
    row = {"논": 0, "밭": 0}
    for kind, value in ((b8, e8), (b9, e9)):
        if kind in row and value:
            row[kind] += value
    for land, amount, start in inv:
        if land in row and year >= start:
            row[land] += amount
    return row["논"], row["밭"]


def _land_cur(b8, e8, b9, e9, inv, year):
    return (e8 + sum(u for l, u, s in inv if l == b8 and year >= s),
            e9 + sum(u for l, u, s in inv if l == "밭" and year >= s))


def _land_p4(b8, e8, b9, e9, inv, year):
    return ((e8 if b8 != "밭" else 0)
            + sum(u for l, u, s in inv if l == "논" and year >= s),
            e9 + (e8 if b8 == "밭" else 0)
            + sum(u for l, u, s in inv if l == "밭" and year >= s))


def _land_r3(b8, e8, b9, e9, inv, year):
    return ((e8 if b8 == "논" else 0) + (e9 if b9 == "논" else 0)
            + sum(u for l, u, s in inv if l == "논" and year >= s),
            (e8 if b8 == "밭" else 0) + (e9 if b9 == "밭" else 0)
            + sum(u for l, u, s in inv if l == "밭" and year >= s))


def _land_blocked_r3(b8, e8, b9, e9, inv):
    """The shipped stored-params gate: an out-of-list kind is refused even at
    amount 0; a blank kind is refused only when an amount is present."""
    why = []
    if b8 and b8 not in LAND_OK:
        why.append("기초 필지 종류")
    elif e8 and b8 not in LAND_OK:
        why.append("기초 필지 종류")
    if b9 and b9 not in LAND_OK:
        why.append("두 번째 필지 종류")
    elif e9 and b9 not in LAND_OK:
        why.append("두 번째 필지 종류")
    for land, amount, _ in inv:
        # '시설' rows are equipment (asset_class), not land rows.
        if land == "시설":
            continue
        if land and land not in LAND_OK:
            why.append("투자 줄 땅 구분")
        elif amount and not land:
            why.append("투자 줄 땅 구분")
    return why


def _land_blocked_r2(b8, e8, b9, e9, inv):
    """The r2 table's looser gate: amount 0 waived the kind check."""
    why = []
    if e8 and b8 not in LAND_OK:
        why.append("기초 필지 종류")
    if e9 and b9 not in LAND_OK:
        why.append("두 번째 필지 종류")
    for land, amount, _ in inv:
        if amount and land not in (*LAND_OK, "시설"):
            why.append("투자 줄 땅 구분")
    return why


class BasisClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = ROOT / "skills/knuaf-doc/references/hort-env-systems"
        cls.m = json.loads((p / "finance-transform.json").read_text())
        cls.p = json.loads((p / "finance-params.json").read_text())
        cls.r = json.loads((p / "finance-verify-classes.json").read_text())

    def test_fixed_counts_and_removed_contract(self):
        self.assertEqual((self.m["entry_count"], self.p["param_count"]), (4107, 1380))
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

    def test_land_combination_table_gate_move_216_to_150(self):
        """The stored-params gate now refuses 66 combinations the r2 table
        accepted (`과수원` with amount 0), so the combination table holds on
        150 instead of 216. Allocation errors drop from 82/55 (current formula)
        to 0/0 (parcel-wise r3)."""
        reasons = Counter()
        blocked = valid = 0
        wrong = {name: [0, 0] for name in ("현재", "P4", "r3")}
        total_bad = 0
        r2_accepts = []
        newly_blocked = []
        for b8 in LAND_KINDS:
            for e8 in (0, 200000):
                for b9 in LAND_KINDS:
                    for e9 in (0, 100000):
                        for name, inv in LAND_INVESTS.items():
                            case = (b8, e8, b9, e9, name)
                            why = _land_blocked_r3(b8, e8, b9, e9, inv)
                            if why:
                                blocked += 1
                                reasons.update(why)
                                # Accepted by the looser r2 gate: the delta.
                                if not _land_blocked_r2(b8, e8, b9, e9, inv):
                                    r2_accepts.append(case)
                                    newly_blocked.append(case)
                                continue
                            r2_accepts.append(case)
                            valid += 1
                            # A case is row-wrong when any year's (논, 밭) split
                            # is wrong, and total-wrong when any year's sum is.
                            for key, fn in (("현재", _land_cur), ("P4", _land_p4),
                                            ("r3", _land_r3)):
                                got = [fn(b8, e8, b9, e9, inv, y) for y in LAND_YEARS]
                                want = [_land_truth(b8, e8, b9, e9, inv, y)
                                        for y in LAND_YEARS]
                                if got != want:
                                    wrong[key][0] += 1
                                if [sum(g) for g in got] != [sum(w) for w in want]:
                                    wrong[key][1] += 1
                            if any(sum(_land_r3(b8, e8, b9, e9, inv, y))
                                   != sum(_land_truth(b8, e8, b9, e9, inv, y))
                                   for y in LAND_YEARS):
                                total_bad += 1
        self.assertEqual(blocked + valid, 448)
        self.assertEqual(blocked, 298)
        self.assertEqual(valid, 150)
        self.assertEqual(reasons, {"기초 필지 종류": 168, "두 번째 필지 종류": 168,
                                   "투자 줄 땅 구분": 64})
        self.assertEqual(wrong["현재"], [82, 55])
        self.assertEqual(wrong["P4"], [30, 0])
        self.assertEqual(wrong["r3"], [0, 0])
        self.assertEqual(total_bad, 0)
        # The r2 table had 216 valid combinations; 66 of them are now refused.
        self.assertEqual(len(r2_accepts), 216)
        self.assertEqual(len(newly_blocked), 66)
        # Every combination r2 waived is one where only the amount was 0.
        self.assertTrue(all(_land_blocked_r3(b8, e8, b9, e9, LAND_INVESTS[n])
                            for b8, e8, b9, e9, n in newly_blocked))
        self.assertTrue(all(not (e8 if b8 not in LAND_OK else 0)
                            and not (e9 if b9 not in LAND_OK else 0)
                            for b8, e8, b9, e9, _ in newly_blocked))

    def test_land_second_parcel_gate(self):
        """`1!B9` (second parcel kind): same allowed values as the first parcel
        and required once the extra amount or area is present."""
        keys = ("opening.land_extra_kind", "opening.land_extra_value_krw1000",
                "opening.land_extra_area_m2")
        params = {k: self.p["parameters"][k] for k in keys}
        kind, amount, area = keys
        # Required: any one of the three alone is incomplete.
        for values in ({amount: 500}, {kind: "논"},
                       {kind: "논", amount: 500}):
            with self.subTest(values=values), \
                    self.assertRaisesRegex(h.Held, "required_fact_missing"):
                h._facts(_facts(values), params)
        # Value gate: exact membership, no stripping, even for a wrong value.
        for bad in ("과수원", "논 "):
            with self.subTest(bad=bad), \
                    self.assertRaisesRegex(h.Held, "fact_value_invalid"):
                h._facts(_facts({kind: bad, amount: 500, area: 100}), params)
        # Negative amounts are a range failure, ahead of the value gate.
        for key in (amount, area):
            with self.subTest(key=key), \
                    self.assertRaisesRegex(h.Held, "fact_range_invalid"):
                h._facts(_facts({kind: "논", amount: 500, area: 100,
                                 key: -1}), params)
        values, _, _ = h._facts(_facts({kind: "논", amount: 500, area: 100}), params)
        self.assertEqual({k: str(v) for k, v in values.items()},
                         {kind: "논", amount: "500", area: "100"})

    def test_land_kind_gate_and_absence(self):
        """First parcel: an out-of-list kind is refused even at amount 0, an
        empty kind is a type failure, and `confirmed_absent` waives both the
        kind and the two values."""
        keys = ("opening.land_kind", "opening.land_value_krw1000",
                "opening.land_area_m2", "category_absence.land")
        params = {k: self.p["parameters"][k] for k in keys}
        kind, value, area, absent = keys
        with self.assertRaisesRegex(h.Held, "required_fact_missing"):
            h._facts(_facts({kind: "논"}), params)
        with self.assertRaisesRegex(h.Held, "fact_type_invalid"):
            h._facts(_facts({kind: ""}), params)
        with self.assertRaisesRegex(h.Held, "fact_value_invalid"):
            h._facts(_facts({kind: "과수원", value: 0}), params)
        with self.assertRaisesRegex(h.Held, "required_fact_missing"):
            h._facts(_facts({kind: "밭", value: 100}), params)
        values, _, _ = h._facts(_facts({kind: "밭", value: 100, area: 50}), params)
        self.assertEqual({k: str(v) for k, v in values.items()},
                         {kind: "밭", value: "100", area: "50"})
        values, _, _ = h._facts(_facts({absent: "confirmed_absent"}), params)
        self.assertEqual({k: str(v) for k, v in values.items()},
                         {absent: "confirmed_absent", value: "0", area: "None"})

    def test_overhead_slot_rate_list_method_condition(self):
        """`method_is` now accepts the stored list form, so the overhead slot's
        rate/unit price is required under the asset-rate methods. The gate is
        stricter than the old string-only comparison: the list-form keys were
        silently unconstrained before."""
        keys = ("cost.overhead.y1.slot2.area_kind",
                "cost.overhead.y1.slot2.rate_or_unit_price",
                "cost.overhead.y1.slot2.payment_month")
        params = {k: self.p["parameters"][k] for k in keys}
        area, rate, month = keys
        spec = self.p["parameters"][rate]
        self.assertEqual(spec["method_tag"], "asset_rate")
        self.assertEqual(spec["required_if"]["all"][1],
                         {"method_is": ["area_unit", "asset_rate", "tax_base_rate"]})
        self.assertEqual(h._facts(_facts({}), params)[0], {})
        with self.assertRaisesRegex(h.Held, "required_fact_missing"):
            h._facts(_facts({area: "시설", month: 3}), params)
        with self.assertRaisesRegex(h.Held, "fact_range_invalid"):
            h._facts(_facts({area: "시설", rate: 2, month: 3}), params)
        # A fully formed slot still stops on its evidence row. pE's fixture
        # swap (../scratch-F/full_params.json) replaces this arm's expected code.
        with self.assertRaisesRegex(
                h.Held, "evidence_source_missing:cost.overhead.y1.slot2.rate_or_unit_price"):
            h._facts(_facts({area: "시설", rate: "0.5", month: 3}), params)


if __name__ == "__main__":
    unittest.main()
