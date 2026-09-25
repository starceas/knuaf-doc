"""fruit_trees first bundle — read-only writing plan (gg_fruit_plan).

Acceptance IDs (research ACCEPTANCE-PLAN, -P = first-bundle part):
A02-P locators, A05 stale sources, A07 identity, A08-P/A11-P/A15-P
instances, A19-P conflict holds, A28-P plan review validity, A31-P flow,
design D-3 return contract, §12 survey evidence and dependencies.
All inputs are synthetic (tests/_fruit_fixtures.py).
"""
import json
import os
from pathlib import Path

from tests import _fruit_fixtures as ff
from tests._harness import ContractCase, bind_major, runtime


def _section(plan, sid):
    return next(s for s in plan["sections"] if s["section_id"] == sid)


def _req(plan, rid):
    return next(r for r in plan["requirements"] if r["id"] == rid)


def _conflict(plan, cid):
    return next(c for c in plan["conflicts"] if c["id"] == cid)


class PlanBindingTests(ContractCase):
    def test_unbound_and_other_major_are_held(self):
        root = self.make_project()
        plan = runtime("gg_fruit_plan").fruit_plan(root)
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "major_required")
        other = self.make_project()
        bind_major(other, "specialty_crops")
        plan = runtime("gg_fruit_plan").fruit_plan(other)
        self.assertEqual(plan["reason"], "major_not_fruit_trees")

    def test_plan_without_handoff_is_presented_with_holds(self):
        fp = ff.ready(self, handoff=False)
        plan = fp.plan()
        self.assertEqual(plan["status"], "presented")
        self.assertEqual(plan["handoff"]["state"], "not_registered")
        self.assertEqual(plan["completeness"], "incomplete")
        self.assertTrue(all(s["status"] == "held" for s in plan["sections"]))

    def test_return_contract_has_no_body_or_calculation(self):
        """D-3: plan/source information only; the project is untouched."""
        fp = ff.ready(self)
        fp.fact("b1", "fruit_trees.block.b1.label", "남쪽 구역",
                scope="block:b1")
        fp.fact("b1a", "fruit_trees.block.b1.area_m2", "3300",
                scope="block:b1", value_type="decimal", unit="㎡")
        before = self._tree_bytes(fp.root)
        plan = fp.plan()
        self.assertEqual(self._tree_bytes(fp.root), before)
        hits = []
        runtime("gg_major_contract")._computed_keys(plan, hits)
        self.assertEqual(hits, [])
        blob = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn("gg:draft", blob)
        for key in ("total_area", "production", "revenue", "yield"):
            self.assertNotIn('"%s"' % key, blob)
        self.assertEqual({o["reason"] for o in plan["output_availability"]},
                         {"unsupported_output"})

    def test_cli_is_read_only(self):
        import subprocess
        import sys
        fp = ff.ready(self)
        before = self._tree_bytes(fp.root)
        # The CLI runs in a fresh process: the synthetic reference set is
        # not patched there, so only the structure/exit code is checked.
        out = subprocess.run([sys.executable, "-B", str(
            Path(runtime("gg_fruit_plan").__file__)), str(fp.root)],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["status"], "presented")
        self.assertEqual(self._tree_bytes(fp.root), before)
        unbound = self.make_project()
        out = subprocess.run([sys.executable, "-B", str(
            Path(runtime("gg_fruit_plan").__file__)), str(unbound)],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)


class HandoffIdentityTests(ContractCase):
    """A05 / A07 — handoff and originals checked against registration."""

    def test_valid_handoff_complete(self):
        plan = ff.ready(self).plan()
        self.assertEqual(plan["handoff"]["state"], "valid")
        self.assertEqual(plan["completeness"], "complete")
        self.assertTrue(all(s["state"] == "ok"
                            for s in plan["template_sources"]))

    def test_original_bytes_changed_after_registration_is_stale(self):
        fp = ff.ready(self)
        (fp.root / "sources/originals/X02.xlsx").write_bytes(
            ff.workbook_bytes("changed"))
        plan = fp.plan()
        x02 = next(s for s in plan["template_sources"]
                   if s["ref_id"] == "X02")
        self.assertEqual(x02["state"], "stale_source")
        wb = plan["workbook_references"]
        row = next(r for r in wb["candidate_roles"] if r["file_id"] == "x02")
        self.assertFalse(row["usable"])
        self.assertEqual(row["link_state"], "stale_source")
        self.assertEqual(wb["common_set_status"], "incomplete")
        self.assertEqual(wb["reference_set"], ["x01"])

    def test_handoff_bytes_changed_is_stale(self):
        fp = ff.ready(self)
        path = fp.root / "sources/research/handoff.json"
        path.write_bytes(path.read_bytes() + b"\n")
        plan = fp.plan()
        self.assertEqual(plan["handoff"]["state"], "stale_source")

    def test_ref_sha_differs_from_registration_is_identity_mismatch(self):
        fp = ff.ready(self, handoff=False)
        h = ff.base_handoff(fp)
        h["template_source_refs"][0]["sha256"] = "0" * 64
        fp.register_handoff(runtime("gg_research_handoff").with_hash(h))
        plan = fp.plan()
        s01 = next(s for s in plan["template_sources"]
                   if s["ref_id"] == "S01")
        self.assertEqual(s01["state"], "identity_mismatch")
        self.assertEqual(_req(plan, "FRT-S02")["status"], "held")

    def test_invalid_handoff_rejected_and_canonical_unchanged(self):
        fp = ff.ready(self, handoff=False)
        h = ff.base_handoff(fp)
        h["major_id"] = "specialty_crops"
        h = runtime("gg_research_handoff").with_hash(h)
        fp.register_handoff(h)
        before = self._tree_bytes(fp.root)
        plan = fp.plan()
        self.assertEqual(plan["handoff"]["state"], "invalid")
        self.assertEqual(plan["handoff"]["error"]["code"], "major_mismatch")
        self.assertEqual(self._tree_bytes(fp.root), before)


class LocatorTests(ContractCase):
    """A02-P — extraction never marks a requirement as met."""

    def _plan_with(self, locators):
        fp = ff.ready(self, handoff=False)
        h = ff.base_handoff(fp)
        req = next(r for r in h["school_requirement_refs"]
                   if r["id"] == "FRT-S13")
        req["locators"] = locators
        fp.register_handoff(runtime("gg_research_handoff").with_hash(h))
        return fp.plan()

    def test_parser_omission_detected(self):
        plan = self._plan_with([{"kind": "xml", "ref": "section1:p4",
                                 "heading": "Ⅲ-4 병해충 및 방제력"}])
        self.assertEqual(_req(plan, "FRT-S13")["status"],
                         "parser_omission_detected")

    def test_same_heading_wrong_position_is_mismatch(self):
        plan = self._plan_with([
            {"kind": "xml", "ref": "section1:p3",
             "heading": "Ⅲ-4 병해충 및 방제력"},
            {"kind": "parsed", "ref": "parsed:p4",
             "heading": "Ⅲ-4 병해충 및 방제력"}])
        req = _req(plan, "FRT-S13")
        self.assertEqual(req["status"], "locator_mismatch")
        self.assertEqual(req["locators"][0]["verification"], "mismatch")
        self.assertIn("locator_mismatch", [h["kind"] for h in
                                           _section(plan, "ch3_pest")["holds"]])

    def test_heading_disagreement_is_mismatch(self):
        plan = self._plan_with([
            {"kind": "xml", "ref": "section1:p4",
             "heading": "Ⅲ-4 병해충 및 방제력"},
            {"kind": "parsed", "ref": "parsed:p4", "heading": "Ⅲ-3 재배작형"}])
        self.assertEqual(_req(plan, "FRT-S13")["status"], "locator_mismatch")

    def test_correct_position_verified_and_no_met_state(self):
        plan = ff.ready(self).plan()
        req = _req(plan, "FRT-S13")
        self.assertEqual(req["locators"][0]["verification"], "verified")
        self.assertEqual(req["locators"][1]["verification"], "unverified")
        states = {r["status"] for r in plan["requirements"]}
        self.assertNotIn("met", states)

    def test_text_after_tab_inside_run_is_read(self):
        """Real S01 TOC lines put the next heading after an hp:tab."""
        plan = self._plan_with([
            {"kind": "xml", "ref": "section1:p6", "heading": "4. 병해충관리"},
            {"kind": "parsed", "ref": "parsed:p6", "heading": "4. 병해충관리"}])
        req = _req(plan, "FRT-S13")
        self.assertEqual(req["locators"][0]["verification"], "verified")
        self.assertEqual(req["status"], "planned")

    def test_unregistered_or_stale_hwpx_is_unverified(self):
        fp = ff.ready(self)
        (fp.root / "sources/originals/S01.hwpx").write_bytes(
            ff.hwpx_bytes(["다른 문단"] * 5))
        plan = fp.plan()
        req = _req(plan, "FRT-S13")
        self.assertEqual(req["locators"][0]["verification"], "unverified")
        self.assertEqual(req["status"], "held")


class ConflictHoldTests(ContractCase):
    """A19-P and §12.3 — conflict holds are partial, never whole-plan."""

    def _plan(self, mutate):
        fp = ff.ready(self, handoff=False)
        h = ff.base_handoff(fp)
        mutate(h)
        fp.register_handoff(runtime("gg_research_handoff").with_hash(h))
        return fp.plan()

    def test_unresolved_c06_holds_pest_section_only(self):
        plan = ff.ready(self).plan()
        pest = _section(plan, "ch3_pest")
        self.assertEqual(pest["status"], "held")
        self.assertIn("XR-02", pest["holds"][0]["xr_refs"])
        self.assertEqual(_section(plan, "ch2_soil")["status"], "planned")

    def test_missing_c06_record_still_holds_by_default_map(self):
        def drop(h):
            h["known_conflicts"] = [c for c in h["known_conflicts"]
                                    if c["id"] != "C06"]
        plan = self._plan(drop)
        self.assertEqual(plan["handoff"]["state"], "valid")
        self.assertEqual(plan["completeness"], "incomplete")
        self.assertIn("C06", plan["missing"]["conflicts"])
        self.assertEqual(_conflict(plan, "C06")["effective_status"],
                         "missing")
        self.assertEqual(_section(plan, "ch3_pest")["status"], "held")

    def test_unknown_conflict_reference_holds_requirement(self):
        def ref(h):
            req = next(r for r in h["school_requirement_refs"]
                       if r["id"] == "FRT-S05")
            req["conflict_ids"] = ["C99"]
        plan = self._plan(ref)
        kinds = [x["kind"] for x in _section(plan, "ch2_soil")["holds"]]
        self.assertIn("unknown_conflict_ref", kinds)

    def test_decision_with_verified_source_releases_hold(self):
        def decide(h):
            c = next(c for c in h["known_conflicts"] if c["id"] == "C08")
            c["status"] = "decided"
            c["decision_ref"] = {"source_ref": "S01", "locator": "section1:p4"}
            c6 = next(c for c in h["known_conflicts"] if c["id"] == "C06")
            c6["status"] = "not_applicable"
            c6["decision_ref"] = {"source_ref": "S01",
                                  "locator": "section1:p4"}
        plan = self._plan(decide)
        self.assertFalse(_conflict(plan, "C06")["held"])
        self.assertNotIn("conflict", [x["kind"] for x in
                                      _section(plan, "ch3_pest")["holds"]])

    def test_decision_with_unverified_source_stays_held(self):
        def decide(h):
            c = next(c for c in h["known_conflicts"] if c["id"] == "C06")
            c["status"] = "decided"
            c["decision_ref"] = {"source_ref": "S99", "locator": "x"}
        plan = self._plan(decide)
        self.assertEqual(_conflict(plan, "C06")["effective_status"],
                         "decision_unverified")
        self.assertEqual(_section(plan, "ch3_pest")["status"], "held")


class InstanceTests(ContractCase):
    """A08-P / A11-P / A15-P — per-instance questions and inheritance."""

    def _orchard(self):
        fp = ff.ready(self)
        f = fp.fact
        f("farm-crop", "fruit_trees.crop_species", "사과")
        f("farm-form", "fruit_trees.cultivation_form", "노지")
        f("b1", "fruit_trees.block.b1.label", "노지 구역", scope="block:b1")
        f("b1h", "fruit_trees.block.b1.heating", "무가온", scope="block:b1")
        f("b2", "fruit_trees.block.b2.label", "시설 구역", scope="block:b2")
        f("b2f", "fruit_trees.block.b2.cultivation_form", "시설",
          scope="block:b2")
        f("b2h", "fruit_trees.block.b2.heating", "가온", scope="block:b2")
        f("ca", "fruit_trees.cohort.a.label", "승계 성목", scope="cohort:a")
        f("ca-o", "fruit_trees.cohort.a.origin", "승계", scope="cohort:a")
        f("ca-b", "fruit_trees.cohort.a.block_ref", "b1", scope="cohort:a")
        f("ca-n", "fruit_trees.cohort.a.tree_count", "0", scope="cohort:a",
          value_type="decimal", unit="주")
        f("cb", "fruit_trees.cohort.b.label", "신규 식재", scope="cohort:b")
        f("cb-o", "fruit_trees.cohort.b.origin", "신규식재", scope="cohort:b")
        f("cb-b", "fruit_trees.cohort.b.block_ref", "b2", scope="cohort:b")
        f("cb-c", "fruit_trees.cohort.b.crop_species", None, scope="cohort:b",
          answer_state="explicit_none")
        return fp

    def test_questions_are_per_instance(self):
        plan = self._orchard().plan()
        q = {x["field_id"]: x["action"] for x in plan["questions"]}
        self.assertEqual(q["fruit_trees.cohort.a.tree_count"], "reuse")
        self.assertEqual(q["fruit_trees.cohort.b.tree_count"], "ask")
        self.assertEqual(q["fruit_trees.cohort.b.origin"], "reuse")

    def test_question_attempts_count_per_instance(self):
        import subprocess
        import sys
        fp = self._orchard()
        gg = Path(runtime("gg_core").__file__).with_name("gg.py")
        for _ in range(2):
            subprocess.run([sys.executable, "-B", str(gg), "question",
                            str(fp.root), "--field",
                            "fruit_trees.cohort.b.tree_count"],
                           capture_output=True, text=True, check=True)
        plan = fp.plan()
        q = {x["field_id"]: x["action"] for x in plan["questions"]}
        self.assertEqual(q["fruit_trees.cohort.b.tree_count"], "deferred")
        self.assertEqual(q["fruit_trees.cohort.a.tree_count"], "reuse")
        self.assertEqual(q["fruit_trees.cohort.a.planting_year"], "ask")

    def test_states_and_zero_are_preserved(self):
        plan = self._orchard().plan()
        a = plan["instances"]["cohort"]["a"]["fields"]
        b = plan["instances"]["cohort"]["b"]["fields"]
        self.assertEqual(a["tree_count"]["value"], "0")
        self.assertEqual(b["tree_count"]["answer_state"], "no_fact")
        self.assertIsNone(b["tree_count"]["value"])
        self.assertEqual(b["crop_species"]["answer_state"], "explicit_none")

    def test_inheritance_and_combinations(self):
        plan = self._orchard().plan()
        a = plan["instances"]["cohort"]["a"]
        b = plan["instances"]["cohort"]["b"]
        self.assertEqual(a["combination"], ["사과", None, "노지", "무가온"])
        self.assertEqual(a["effective"]["crop_species"]["inherited_from"],
                         "farm")
        self.assertEqual(a["effective"]["cultivation_form"]["inherited_from"],
                         "farm")
        # explicit none on the cohort stops inheritance from the farm
        self.assertEqual(b["effective"]["crop_species"]["answer_state"],
                         "explicit_none")
        self.assertEqual(b["combination"], [None, None, "시설", "가온"])

    def test_integrity_issues(self):
        fp = self._orchard()
        fp.fact("cc-b", "fruit_trees.cohort.c.block_ref", "b9",
                scope="cohort:c")
        fp.fact("cd", "fruit_trees.cohort.d.label", "d", scope="cohort:d")
        fp.fact("cd-b", "fruit_trees.cohort.d.block_ref", "b9",
                scope="cohort:d")
        fp.fact("cd-o", "fruit_trees.cohort.d.origin", "기타",
                scope="cohort:d")
        fp.fact("bad", "fruit_trees.cohort.Bad.label", "x", scope="cohort:x")
        fp.fact("scope", "fruit_trees.cohort.d.cultivar", "후지",
                scope="farm")
        fp.fact("dup", "fruit_trees.cohort.d.rootstock", "M9",
                scope="cohort:d")
        fp.fact("dup2", "fruit_trees.cohort.d.rootstock", "M26",
                scope="cohort:d")
        issues = {(i["kind"], i.get("id") or i.get("cohort")
                   or i.get("field_id") or i.get("fact_id"))
                  for i in fp.plan()["instance_issues"]}
        self.assertIn(("undeclared_instance", "c"), issues)
        self.assertIn(("dangling_ref", "d"), issues)
        self.assertIn(("value_not_allowed", "d"), issues)
        self.assertIn(("invalid_field_id", "fruit_trees.cohort.Bad.label"), issues)
        self.assertIn(("scope_mismatch", "scope"), issues)
        self.assertIn(("duplicate_answer", "fruit_trees.cohort.d.rootstock"),
                      issues)


class WorkbookSelectionTests(ContractCase):
    """§5/§12.1 — the current selection comes from the canonical record."""

    def _with_major_files(self, **kw):
        fp = ff.ready(self, handoff=False)
        fp.register("wb-a", "sources/major/a.xlsx", ff.workbook_bytes("a"))
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "student_example",
             "role_authority": {"kind": "user_statement", "ref": "ans"},
             "link": {"kind": "project_source", "source_id": "wb-a"}},
            {"file_id": "b", "label": "b.xlsx", "origin": "major_folder",
             "relative_id": "b.xlsx", "declared_role": "unclassified",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "none"}}]
        fp.register_handoff(ff.base_handoff(fp, files=files, **kw))
        return fp

    def test_selection_required_then_user_selected(self):
        fp = self._with_major_files(research_selection="a")
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["selection_status"], "selection_required")
        self.assertTrue(wb["selection_drift"])
        fp.fact("sel", "fruit_trees.workbook.selection", "a")
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["selection_status"], "user_selected")
        self.assertEqual(wb["reference_set"], ["a"])
        self.assertFalse(wb["selection_drift"])

    def test_unlinked_selection_is_unusable(self):
        fp = self._with_major_files()
        fp.fact("sel", "fruit_trees.workbook.selection", "b")
        self.assertEqual(fp.plan()["workbook_references"]["selection_status"],
                         "selected_unusable")

    def test_canonical_change_wins_over_handoff(self):
        fp = self._with_major_files(research_selection="b")
        fp.fact("sel", "fruit_trees.workbook.selection", "a")
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["reference_set"], ["a"])
        self.assertTrue(wb["selection_drift"])


class SurveyEvidenceTests(ContractCase):
    """§12.5 — a scan receipt proves a survey only when it is the real
    producer's receipt for this exact root and its workbook entries match
    the inventory one-to-one."""

    def _scan(self, fp, ext, files):
        si = runtime("gg_source_intake")
        reuse = runtime("gg_reuse")
        gg_lock = runtime("gg_lock")
        for rel, data in files.items():
            path = ext / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        registry = reuse.load_registry(
            Path(si.__file__).resolve().parents[1] / "references"
            / "builtin-sources.json")
        res = si.run_source_scan(
            ext, root=fp.root, classification_version="p5-classifier/1",
            registry=registry, key_catalog={},
            scan_policy={"classification_version": "p5-classifier/1",
                         "include": [], "exclude": [],
                         "always_hash": ["**/*.xlsx", "*.xlsx"],
                         "document_extensions": ["md"]})
        with gg_lock.Lock(fp.root) as cap:
            receipt = si.write_scan_receipt(fp.root, res, capability=cap)
        rel = receipt.relative_to(fp.root).as_posix()
        fp.register("receipt", rel, receipt.read_bytes())
        diag = receipt.with_name(res["scan_id"] + ".diagnostics.json")
        fp.register("diag", diag.relative_to(fp.root).as_posix(),
                    diag.read_bytes())
        return res

    def _plan(self, scope_id, files_on_disk, inventory_files):
        fp = ff.ready(self, handoff=False)
        ext = Path(scope_id)
        self._scan(fp, ext, files_on_disk)
        evidence = {"kind": "source_scan_receipt",
                    "receipt_source_id": "receipt",
                    "diagnostics_source_id": "diag"}
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"]
        files = files + inventory_files
        fp.register_handoff(ff.base_handoff(
            fp, scope_id=os.path.normpath(str(ext)), evidence=evidence,
            files=files))
        return fp.plan()["workbook_references"]

    def _tmp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_empty_folder_receipt_confirms_absence(self):
        base = self._tmp()
        (base / "fruit").mkdir()
        wb = self._plan(base / "fruit", {"memo.md": b"x"}, [])
        self.assertEqual(wb["survey"]["status"], "complete")
        self.assertEqual(wb["inventory_status"], "confirmed_absent")

    def test_same_folder_name_elsewhere_is_rejected(self):
        base = self._tmp()
        (base / "farmA" / "fruit").mkdir(parents=True)
        (base / "farmB" / "fruit").mkdir(parents=True)
        fp = ff.ready(self, handoff=False)
        self._scan(fp, base / "farmA" / "fruit", {"memo.md": b"x"})
        evidence = {"kind": "source_scan_receipt",
                    "receipt_source_id": "receipt",
                    "diagnostics_source_id": "diag"}
        fp.register_handoff(ff.base_handoff(
            fp, scope_id=str(base / "farmB" / "fruit"), evidence=evidence))
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["survey"]["evidence"]["reason"], "receipt_scope")
        self.assertEqual(wb["inventory_status"], "not_surveyed")

    def test_duplicate_bytes_candidate_missing_from_inventory(self):
        base = self._tmp()
        same = ff.workbook_bytes("same")
        wb = self._plan(base / "fruit", {"a.xlsx": same, "b.xlsx": same}, [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "unclassified",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "none"}}])
        self.assertEqual(wb["survey"]["status"], "partial")
        self.assertEqual(wb["survey"]["evidence"]["reason"],
                         "receipt_inventory_mismatch")

    def test_forged_receipt_self_hash_is_rejected(self):
        base = self._tmp()
        (base / "fruit").mkdir()
        fp = ff.ready(self, handoff=False)
        self._scan(fp, base / "fruit", {"memo.md": b"x"})
        rec = fp.record("receipt")
        path = fp.root / rec["path"]
        sealed = json.loads(path.read_bytes())
        sealed["scan_state"] = "forged"
        fp.register("receipt", rec["path"],
                    json.dumps(sealed).encode("utf-8"))
        evidence = {"kind": "source_scan_receipt",
                    "receipt_source_id": "receipt",
                    "diagnostics_source_id": "diag"}
        fp.register_handoff(ff.base_handoff(
            fp, scope_id=str(base / "fruit"), evidence=evidence))
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["survey"]["evidence"]["reason"],
                         "receipt_self_hash")

    def test_attestation_without_scope_is_partial(self):
        fp = ff.ready(self, handoff=False)
        fp.register("attest", "sources/research/attest.txt",
                    "XLSX 없음\n".encode("utf-8"))
        fp.register_handoff(ff.base_handoff(fp))
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["survey"]["status"], "partial")
        self.assertEqual(wb["inventory_status"], "not_surveyed")


class PlanReviewTests(ContractCase):
    """A28-P / §6 / §12.2 / §12.5 — plan review validity on three axes."""

    def _review(self, fp, plan, *, rid="rv", observe=False):
        core = runtime("gg_core")
        report = fp.root / "reviews" / (rid + ".md")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("계획 검토: 합성\n", encoding="utf-8")
        value = {"id": rid, "review_kind": "plan", "author_id": "writer",
                 "reviewer_id": "reviewer",
                 "target_refs": plan["plan_target_refs"],
                 "input_fingerprint": plan["plan_input_fingerprint"],
                 "findings": [], "disposition": "resolved",
                 "status": "pass", "path": "reviews/%s.md" % rid,
                 "coverage": {"plan": "all"}}
        if observe:
            obs = {"schema": "gg-review-observation/1",
                   "review_kinds": ["plan"], "observed_by": "observer",
                   "author_session": "s-author",
                   "reviewer_session": "s-reviewer",
                   "author_id": "writer", "reviewer_id": "reviewer",
                   "target_refs": plan["plan_target_refs"],
                   "input_fingerprint": plan["plan_input_fingerprint"],
                   "report_hash": core.digest(report.read_bytes())}
            data = json.dumps(obs, ensure_ascii=False).encode("utf-8")
            digest = core.digest(data)
            obs_path = fp.root / ".gg-observations" / (digest + ".json")
            obs_path.parent.mkdir(exist_ok=True)
            obs_path.write_bytes(data)
            value["provenance_path"] = ".gg-observations/%s.json" % digest
            value["provenance_hash"] = digest
        fp.apply([{"collection": "reviews", "value": value}], "review:" + rid)
        return value

    def _current(self, fp):
        plan = fp.plan()
        return plan["current_plan_review"], plan["plan_reviews"][0]

    def test_fresh_then_fact_change_is_stale(self):
        fp = ff.ready(self)
        fp.fact("farm-crop", "fruit_trees.crop_species", "사과")
        self._review(fp, fp.plan())
        self.assertEqual(self._current(fp)[0], "fresh")
        fp.fact("farm-crop", "fruit_trees.crop_species", "배",
                request_id="fact:farm-crop:2")
        state, review = self._current(fp)
        self.assertEqual(state, "none")
        self.assertEqual(review["input_state"], "stale")

    def test_original_bytes_change_is_stale(self):
        fp = ff.ready(self)
        self._review(fp, fp.plan())
        (fp.root / "sources/originals/X01.xlsx").write_bytes(
            ff.workbook_bytes("tampered"))
        self.assertEqual(self._current(fp)[1]["input_state"], "stale")

    def test_new_fact_changes_scope(self):
        fp = ff.ready(self)
        self._review(fp, fp.plan())
        fp.fact("farm-crop", "fruit_trees.crop_species", "사과")
        self.assertEqual(self._current(fp)[1]["input_state"],
                         "scope_changed")

    def test_linked_workbook_and_evidence_sources_are_dependencies(self):
        fp = ff.ready(self, handoff=False)
        fp.register("wb-a", "sources/major/a.xlsx", ff.workbook_bytes("a"))
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "current_work",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "project_source", "source_id": "wb-a"}}]
        fp.register_handoff(ff.base_handoff(fp, files=files))
        plan = fp.plan()
        ids = {r["id"] for r in plan["plan_target_refs"]}
        self.assertTrue({"wb-a", "attest", "handoff", "x01", "x02",
                         "s01"} <= ids)
        self._review(fp, plan)
        (fp.root / "sources/major/a.xlsx").write_bytes(ff.workbook_bytes("z"))
        self.assertEqual(self._current(fp)[1]["input_state"], "stale")
        (fp.root / "sources/major/a.xlsx").write_bytes(ff.workbook_bytes("a"))
        fp.register("attest", "sources/research/attest.txt",
                    b"/synthetic/major/fruit changed\n")
        self.assertEqual(self._current(fp)[1]["input_state"], "stale")

    def test_unregistered_link_is_unresolvable(self):
        fp = ff.ready(self, handoff=False)
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "g", "label": "g.xlsx", "origin": "major_folder",
             "relative_id": "g.xlsx", "declared_role": "current_work",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "project_source", "source_id": "ghost"}}]
        fp.register_handoff(ff.base_handoff(fp, files=files))
        plan = fp.plan()
        self.assertEqual(plan["unresolvable_links"], ["ghost"])
        self._review(fp, plan)
        self.assertEqual(fp.plan()["current_plan_review"], "none")

    def test_binding_only_rebind_keeps_review_current(self):
        """L7 owner decision: a binding change alone keeps content review."""
        fp = ff.ready(self)
        self._review(fp, fp.plan())
        bind_major(fp.root, "fruit_trees", request_id="rebind")
        state, review = self._current(fp)
        self.assertEqual(state, "fresh")
        self.assertTrue(review["counts_as_current"])

    def test_report_deleted_or_changed_is_not_current(self):
        fp = ff.ready(self)
        self._review(fp, fp.plan())
        report = fp.root / "reviews" / "rv.md"
        report.write_text("변조\n", encoding="utf-8")
        state, review = self._current(fp)
        self.assertEqual(review["input_state"], "fresh")
        self.assertEqual(review["evidence_state"], "report_hash_mismatch")
        self.assertEqual(state, "none")
        report.unlink()
        self.assertEqual(self._current(fp)[1]["evidence_state"],
                         "report_missing")

    def test_observation_damage_is_not_current(self):
        fp = ff.ready(self)
        value = self._review(fp, fp.plan(), observe=True)
        self.assertEqual(self._current(fp)[0], "fresh")
        (fp.root / value["provenance_path"]).write_bytes(b"{}")
        state, review = self._current(fp)
        self.assertEqual(review["evidence_state"], "observation_invalid")
        self.assertEqual(state, "none")

    def test_failed_outcome_is_not_current(self):
        fp = ff.ready(self)
        value = self._review(fp, fp.plan())
        value = dict(value, status="fail", disposition="open")
        value.pop("revision", None)
        fp.apply([{"collection": "reviews", "value": value}], "review:fail")
        self.assertEqual(self._current(fp)[1]["outcome"], "not_pass")


class FlowTests(ContractCase):
    """A31-P — select sources, partial answers, change, plan again."""

    def test_partial_then_updated_plan_preserves_answers(self):
        fp = ff.ready(self)
        fp.fact("ca", "fruit_trees.cohort.a.label", "A", scope="cohort:a")
        first = fp.plan()
        self.assertEqual(first["status"], "presented")
        facts_before = dict(fp.core.load(fp.root)["facts"])
        fp.fact("ca-n", "fruit_trees.cohort.a.tree_count", "120",
                scope="cohort:a", value_type="decimal", unit="주")
        second = fp.plan()
        self.assertGreater(second["project_revision"],
                           first["project_revision"])
        self.assertNotEqual(second["plan_input_fingerprint"],
                            first["plan_input_fingerprint"])
        facts_after = fp.core.load(fp.root)["facts"]
        for fid, fact in facts_before.items():
            self.assertEqual(facts_after[fid]["value"], fact["value"])
        q = {x["field_id"]: x["action"] for x in second["questions"]}
        self.assertEqual(q["fruit_trees.cohort.a.tree_count"], "reuse")


class FinalReviewRegressionTests(ContractCase):
    """Astra final review 1 findings 1–8: holds propagate and malformed
    inputs become states, never a crashed plan."""

    def _plan(self, mutate, fp=None):
        fp = fp or ff.ready(self, handoff=False)
        h = ff.base_handoff(fp)
        mutate(h)
        fp.register_handoff(runtime("gg_research_handoff").with_hash(h))
        return fp.plan()

    def test_1_stale_workbook_holds_roles_and_mappings(self):
        fp = ff.ready(self)
        (fp.root / "sources/originals/X02.xlsx").write_bytes(
            ff.workbook_bytes("changed"))
        plan = fp.plan()
        kinds = [x["kind"] for x in _section(plan, "appendix_01")["holds"]]
        self.assertIn("role_source", kinds)
        self.assertEqual(plan["row_mappings"][0]["status"], "held")
        self.assertEqual(plan["row_mappings"][0]["source_state"],
                         "stale_source")

    def test_1_unknown_sheet_holds_role(self):
        def sheet(h):
            h["appendix_roles"][2]["workbook_sheets"]["X01"] = ["없는 시트"]
        plan = self._plan(sheet)
        kinds = [x["kind"] for x in _section(plan, "appendix_03")["holds"]]
        self.assertIn("unknown_sheet", kinds)
        self.assertEqual(plan["completeness"], "incomplete")

    def test_2_invalid_conflict_target_keeps_default_hold(self):
        def typo(h):
            c6 = next(c for c in h["known_conflicts"] if c["id"] == "C06")
            c6["affected_sections"] = ["ch3_pest_typo"]
            c8 = next(c for c in h["known_conflicts"] if c["id"] == "C08")
            c8["status"] = "decided"
            c8["decision_ref"] = {"source_ref": "S01",
                                  "locator": "section1:p4"}
        plan = self._plan(typo)
        self.assertEqual(_section(plan, "ch3_pest")["status"], "held")
        self.assertEqual(plan["completeness"], "incomplete")

    def test_3_undefined_conflict_ref_holds_requirement(self):
        def ref(h):
            req = next(r for r in h["school_requirement_refs"]
                       if r["id"] == "FRT-S05")
            req["conflict_ids"] = ["C99"]
        plan = self._plan(ref)
        self.assertEqual(_req(plan, "FRT-S05")["status"], "held")
        self.assertEqual(plan["completeness"], "incomplete")

    def test_4_receipt_needs_both_hashes(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ext = Path(tmp.name) / "fruit"
        ext.mkdir()
        fp = ff.ready(self, handoff=False)
        SurveyEvidenceTests._scan(self, fp, ext,
                                  {"a.xlsx": ff.workbook_bytes("a")})
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "unclassified",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "none"}}]
        fp.register_handoff(ff.base_handoff(
            fp, scope_id=str(ext), files=files,
            evidence={"kind": "source_scan_receipt",
                      "receipt_source_id": "receipt",
                      "diagnostics_source_id": "diag"}))
        wb = fp.plan()["workbook_references"]
        self.assertEqual(wb["survey"]["status"], "partial")
        self.assertEqual(wb["inventory_status"], "not_surveyed")

    def test_5_two_selection_facts_conflict_even_if_equal(self):
        fp = ff.ready(self, handoff=False)
        fp.register("wb-a", "sources/major/a.xlsx", ff.workbook_bytes("a"))
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "current_work",
             "role_authority": {"kind": "user_statement", "ref": "ans"},
             "link": {"kind": "project_source", "source_id": "wb-a"}}]
        fp.register_handoff(ff.base_handoff(fp, files=files))
        fp.fact("sel1", "fruit_trees.workbook.selection", "a")
        fp.fact("sel2", "fruit_trees.workbook.selection", "a")
        self.assertEqual(fp.plan()["workbook_references"]["selection_status"],
                         "selection_conflict")

    def test_5_non_string_selection_is_invalid(self):
        fp = ff.ready(self, handoff=False)
        fp.register("wb-a", "sources/major/a.xlsx", ff.workbook_bytes("a"))
        files = ff.base_handoff(fp)["workbook_reference_set"]["files"] + [
            {"file_id": "a", "label": "a.xlsx", "origin": "major_folder",
             "relative_id": "a.xlsx", "declared_role": "current_work",
             "role_authority": {"kind": "none", "ref": None},
             "link": {"kind": "project_source", "source_id": "wb-a"}}]
        fp.register_handoff(ff.base_handoff(fp, files=files))
        fp.fact("sel", "fruit_trees.workbook.selection", ["a"])
        self.assertEqual(fp.plan()["workbook_references"]["selection_status"],
                         "selection_invalid")

    def test_6_bad_id_type_is_invalid_handoff_not_crash(self):
        def bad(h):
            h["row_mappings"][0]["mapping_id"] = []
        plan = self._plan(bad)
        self.assertEqual(plan["status"], "presented")
        self.assertEqual(plan["handoff"]["state"], "invalid")
        self.assertEqual(plan["handoff"]["error"]["code"], "type_error")
        def bad_item(h):
            h["unresolved_items"][0]["id"] = 7
        plan = self._plan(bad_item)
        self.assertEqual(plan["handoff"]["error"]["code"], "type_error")

    def test_7_malformed_diagnostics_is_partial(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ext = Path(tmp.name) / "fruit"
        ext.mkdir()
        fp = ff.ready(self, handoff=False)
        SurveyEvidenceTests._scan(self, fp, ext, {"memo.md": b"x"})
        rec = fp.record("diag")
        fp.register("diag", rec["path"], b"[]")
        fp.register_handoff(ff.base_handoff(
            fp, scope_id=str(ext),
            evidence={"kind": "source_scan_receipt",
                      "receipt_source_id": "receipt",
                      "diagnostics_source_id": "diag"}))
        plan = fp.plan()
        wb = plan["workbook_references"]
        self.assertEqual(plan["status"], "presented")
        self.assertEqual(wb["survey"]["evidence"]["reason"],
                         "receipt_unparsable")
        self.assertEqual(wb["survey"]["status"], "partial")

    def test_8_non_string_block_ref_is_an_issue(self):
        fp = ff.ready(self)
        fp.fact("cz", "fruit_trees.cohort.z.label", "z", scope="cohort:z")
        fp.fact("cz-b", "fruit_trees.cohort.z.block_ref", ["b1"],
                scope="cohort:z")
        fp.fact("cz-o", "fruit_trees.cohort.z.origin", ["승계"],
                scope="cohort:z")
        plan = fp.plan()
        kinds = {i["kind"] for i in plan["instance_issues"]}
        self.assertIn("invalid_ref_value", kinds)
        self.assertIn("value_not_allowed", kinds)
        self.assertEqual(plan["status"], "presented")

    def test_9_malformed_receipt_entry_is_not_absence(self):
        """Final review 2 finding 9: a self-consistent receipt whose file
        list holds a malformed entry never proves an empty folder."""
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ext = Path(tmp.name) / "fruit"
        ext.mkdir()
        fp = ff.ready(self, handoff=False)
        res = SurveyEvidenceTests._scan(self, fp, ext, {"memo.md": b"x"})
        si = runtime("gg_source_intake")
        for files in ([None], [{"relative_id": 7, "type": "file",
                                "content_sha256": None}]):
            sealed = {k: v for k, v in res["sealed"].items()
                      if k != "scan_id"}
            sealed["files"] = files
            scan_id = si._sha256_text(si._canonical_json(sealed))
            sealed["scan_id"] = scan_id
            intake = "sources/intake/"
            fp.register("receipt", intake + scan_id + ".json",
                        json.dumps(sealed).encode("utf-8"))
            diag = json.loads((fp.root / fp.record("diag")["path"])
                              .read_bytes())
            fp.register("diag", intake + scan_id + ".diagnostics.json",
                        json.dumps(diag).encode("utf-8"))
            fp.register_handoff(ff.base_handoff(
                fp, scope_id=str(ext),
                evidence={"kind": "source_scan_receipt",
                          "receipt_source_id": "receipt",
                          "diagnostics_source_id": "diag"}),
                source_id="handoff")
            wb = fp.plan()["workbook_references"]
            with self.subTest(files=files):
                self.assertEqual(wb["survey"]["evidence"]["reason"],
                                 "receipt_unparsable")
                self.assertEqual(wb["inventory_status"], "not_surveyed")
