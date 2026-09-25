"""knuaf-research-handoff/v1 strict parsing (A07, A30, design §8.1/§12.3).

Structural faults reject the whole handoff; reference gaps are reported
for the caller to hold on.  The self-hash is internal consistency only.
"""
import json

from tests import _fruit_fixtures as ff
from tests._harness import ContractCase, runtime


class HandoffTests(ContractCase):
    def setUp(self):
        self.hm = runtime("gg_research_handoff")
        fp = ff.ready(self, handoff=False)
        self.good = ff.base_handoff(fp)

    def _fails(self, handoff_or_bytes, code, *, rehash=True):
        data = handoff_or_bytes
        if isinstance(data, dict):
            if rehash:
                data = self.hm.with_hash(data)
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(self.hm.HandoffError) as caught:
            self.hm.load_and_validate(data, major_id="fruit_trees")
        self.assertEqual(caught.exception.code, code)

    def _copy(self):
        return json.loads(json.dumps(self.good))

    def test_good_handoff_has_no_gaps(self):
        out = self.hm.load_and_validate(
            json.dumps(self.good, ensure_ascii=False).encode("utf-8"),
            major_id="fruit_trees")
        self.assertEqual(out["gaps"], [])

    def test_json_level_rejections(self):
        raw = json.dumps(self.good, ensure_ascii=False)
        dup = raw.replace('"schema":', '"schema": "x", "schema":', 1)
        self._fails(dup.encode("utf-8"), "duplicate_key")
        nan = raw.replace('"source_revision": 1', '"source_revision": NaN', 1)
        self._fails(nan.encode("utf-8"), "non_finite_number")
        self._fails(b"\xff\xfe", "not_utf8")
        self._fails(b"{", "json_invalid")

    def test_closed_keys_top_and_nested(self):
        h = self._copy()
        h["example_farm_values"] = {"yield": 1000}
        self._fails(h, "unknown_key")
        h = self._copy()
        h["known_conflicts"][0]["note"] = "x"
        self._fails(h, "unknown_key")
        h = self._copy()
        del h["period_model"]
        self._fails(h, "missing_key")

    def test_self_hash_and_major(self):
        h = self._copy()
        h["unresolved_items"][0]["summary"] = "바뀜"
        self._fails(h, "self_hash_mismatch", rehash=False)
        h = self._copy()
        h["major_id"] = "specialty_crops"
        self._fails(h, "major_mismatch")

    def test_ids_formats_and_uniqueness(self):
        h = self._copy()
        h["school_requirement_refs"][1]["id"] = h[
            "school_requirement_refs"][0]["id"]
        self._fails(h, "duplicate_id")
        h = self._copy()
        h["known_conflicts"][0]["id"] = "X1"
        self._fails(h, "id_format")
        h = self._copy()
        h["known_conflicts"][0]["xr_refs"] = ["XR-2"]
        self._fails(h, "id_format")
        h = self._copy()
        h["period_model"]["school_display_years"] = [2027, 2029]
        self._fails(h, "years_not_consecutive")
        h = self._copy()
        h["template_source_refs"][0]["source_revision"] = True
        self._fails(h, "type_error")

    def test_hold_scope_and_decision_ref_rules(self):
        h = self._copy()
        h["row_mappings"][0]["conflict_ids"] = []
        self._fails(h, "unresolved_without_hold")
        h = self._copy()
        h["known_conflicts"][0]["status"] = "decided"
        self._fails(h, "decision_ref_required")

    def test_reference_gaps_are_not_structural(self):
        h = self._copy()
        h["known_conflicts"] = [c for c in h["known_conflicts"]
                                if c["id"] != "C06"]
        req = next(r for r in h["school_requirement_refs"]
                   if r["id"] == "FRT-S05")
        req["source_ref"] = "S99"
        role = h["appendix_roles"][0]
        role["workbook_sheets"]["X01"] = ["10. 감가상각비계획"]  # space lost
        out = self.hm.validate(self.hm.with_hash(h), major_id="fruit_trees",
                               reference_sheets={"X01": ff.SHEETS})
        kinds = {(g["kind"], g["ref"]) for g in out["gaps"]}
        self.assertIn(("dangling_conflict_ref", "C06"), kinds)
        self.assertIn(("dangling_source_ref", "S99"), kinds)
        self.assertIn(("unknown_sheet", "X01:10. 감가상각비계획"), kinds)

    def test_xr_refs_are_preserved(self):
        out = self.hm.validate(self._copy(), major_id="fruit_trees")
        c06 = next(c for c in out["handoff"]["known_conflicts"]
                   if c["id"] == "C06")
        self.assertEqual(c06["xr_refs"], ["XR-02"])
        self.assertEqual(out["handoff"]["row_mappings"][0]["xr_refs"],
                         ["XR-04"])

    def test_evidence_kind_keys_are_closed(self):
        h = self._copy()
        h["workbook_reference_set"]["survey"]["evidence"] = {
            "kind": "source_scan_receipt", "receipt_source_id": "r"}
        self._fails(h, "missing_key")
        h = self._copy()
        h["workbook_reference_set"]["survey"]["evidence"] = {
            "kind": "none", "attestation_source_id": "a"}
        self._fails(h, "unknown_key")
