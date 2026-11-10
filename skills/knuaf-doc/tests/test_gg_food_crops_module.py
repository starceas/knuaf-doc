"""Food-crops (식량작물전공) module contract.

Covers DESIGN-v1 §12-1's refusal table (school workbook unsupported at
every entry point), the four branch combinations plus the undecided /
conflict / crop-count-mismatch states (§11-2), the 12-node document plan
order, and the advisory notices (plan-year shift, startup form, non-food
crop).
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gg  # noqa: E402
import gg_core  # noqa: E402
import gg_major_contract as mc  # noqa: E402

MAJOR = "food_crops"


def _fact(field_id, value, *, superseded=False, answer_state="provided"):
    record = {
        "field_id": "%s.%s" % (MAJOR, field_id),
        "answer_state": answer_state,
        "value": value,
    }
    if superseded:
        record["verification"] = "superseded"
    return record


def _branch(entry_mode=None, cropping_system=None, crops=None):
    facts = {}
    for key, value in (("entry_mode", entry_mode),
                       ("cropping_system", cropping_system),
                       ("crops", crops)):
        if value is not None:
            facts[key] = _fact(key, value)
    return facts


class FoodCropsRefusalTests(unittest.TestCase):
    """§12-1: no school workbook, decided by omission from the outputs."""

    def test_route_rejects_school_excel_workbook_for_every_capability(self):
        registry = mc.default_registry()
        for capability in mc.CAPABILITY_KINDS:
            with self.subTest(capability=capability):
                with self.assertRaises(mc.UnsupportedOutputError):
                    mc.route(registry, MAJOR, capability,
                             output="school_excel_workbook")

    def test_document_proposal_with_workbook_claim_rejected(self):
        module = mc.default_registry().resolve(MAJOR)
        for claim in ({"workbook": "school_17_sheet_v1"},
                      {"sheets": ["생산계획"]}):
            with self.subTest(claim=claim):
                proposal = mc.ModuleProposal(
                    major_id=MAJOR,
                    module_version=module.module_version,
                    contract_version=mc.CONTRACT_VERSION,
                    capability="document",
                    status="supported",
                    proposal=mc._freeze(claim),
                )
                with self.assertRaises(mc.ProposalInvalidError):
                    mc.validate_proposal(module, proposal)

    def test_finance_capability_is_unsupported(self):
        module = mc.default_registry().resolve(MAJOR)
        self.assertEqual(module.capabilities["finance"], "unsupported")
        self.assertEqual(
            [(cap.profile, cap.status) for cap in module.finance_capabilities],
            [("food_crops_16_sheet_v1", "unsupported")])
        proposal = mc.route(mc.default_registry(), MAJOR, "finance")
        self.assertEqual(proposal.status, "unsupported")
        self.assertEqual(
            [cap["status"] for cap in proposal.proposal["capabilities"]],
            ["unsupported"])
        self.assertFalse(proposal.proposal["blocks_document"])

    def test_cli_workbook_request_refused_and_no_file_written(self):
        with tempfile.TemporaryDirectory() as temp:
            root = _bound_project(temp)
            (Path(temp) / "spec.json").write_text(
                json.dumps({"profile": "school_17_sheet_v1"}),
                encoding="utf-8")
            before = sorted(
                p.relative_to(temp).as_posix()
                for p in Path(temp).rglob("*") if p.is_file())
            result = subprocess.run(
                [sys.executable, "-B",
                 str(SCRIPTS / "build_excel_finance_template.py"), temp,
                 "--input", "spec.json", "--out", "build/x.xlsx",
                 "--major", MAJOR],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2,
                             result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["reason"],
                             "unsupported_output")
            self.assertFalse((Path(temp) / "build").exists())
            after = sorted(
                p.relative_to(temp).as_posix()
                for p in Path(temp).rglob("*") if p.is_file())
            self.assertEqual(before, after)

    def test_major_plan_reports_finance_unsupported(self):
        result = _major_plan()
        self.assertEqual(
            result["proposals"]["finance"]["status"], "unsupported")
        self.assertEqual(
            {cap["status"]
             for cap in result["proposals"]["finance"]["proposal"][
                 "capabilities"]},
            {"unsupported"})
        self.assertNotIn("school_excel_workbook", json.dumps(result))
        module = mc.default_registry().resolve(MAJOR)
        self.assertNotIn("school_excel_workbook", module.supported_outputs)
        self.assertEqual(module.workbook_profiles,
                         ("food_crops_16_sheet_v1",))
        self.assertIn("school_paper", module.supported_outputs)


class FoodCropsDeclarationTests(unittest.TestCase):
    def test_registry_marker_and_twelve_node_plan_order(self):
        registry = mc.default_registry()
        self.assertEqual(registry.major_ids[-1], MAJOR)
        self.assertEqual(mc.KNOWN_MAJOR_MARKERS[MAJOR], ("식량작물",))
        module = registry.resolve(MAJOR)
        self.assertEqual(module.module_version, "0.1.0")
        self.assertEqual(module.packs, ())
        self.assertEqual(len(module.question_schema), 28)
        self.assertEqual(len({q.field_id for q in module.question_schema}), 28)
        document = mc.route(registry, MAJOR, "document").proposal
        self.assertEqual(
            [item["section_id"] for item in document["sections"]],
            ["cover", "approval", "summary_1_5", "acknowledgements", "lists",
             "chapter_01", "chapter_02", "chapter_03", "chapter_04",
             "conclusion", "references", "parent_consent"])
        self.assertEqual(len(document["mandated_sections"]), 12)
        self.assertEqual(document["sections"][-1]["section_id"],
                         "parent_consent")
        for item in document["sections"]:
            self.assertFalse(item["selectable"])
            self.assertTrue(item["required"])

    def test_question_targets_are_only_branch_gates(self):
        module = mc.default_registry().resolve(MAJOR)
        targets = {q.field_id: q.target for q in module.question_schema}
        gated = {field_id: target for field_id, target in targets.items()
                 if target is not None}
        self.assertEqual(gated, {
            MAJOR + ".past_results": "succession",
            MAJOR + ".second_crop_schedule": "double",
            MAJOR + ".farm_founding_date": "startup",
            MAJOR + ".land_acquisition": "startup",
            MAJOR + ".startup_fund": "startup",
        })
        for field_id in (MAJOR + ".existing_assets",
                         MAJOR + ".parent_support"):
            self.assertIn(field_id, targets)
            self.assertIsNone(targets[field_id])

    def test_question_units_follow_the_question_id_table(self):
        units = {q.field_id: q.unit
                 for q in mc.default_registry().resolve(MAJOR).question_schema}
        self.assertEqual(units[MAJOR + ".plan_period"], "년")
        self.assertEqual(units[MAJOR + ".land_owned"], "m2")
        self.assertEqual(units[MAJOR + ".land_leased"], "m2")
        self.assertEqual(units[MAJOR + ".market_distance"], "km")
        self.assertIsNone(units[MAJOR + ".crops"])


class FoodCropsSwitchTests(unittest.TestCase):
    def test_four_branch_combinations_reach_ready(self):
        cases = (
            ("succession", "single", ["벼"], "succession", False),
            ("succession", "double", ["벼", "보리"], "succession", True),
            ("startup", "single", ["감자"], "startup", False),
            ("startup", "double", ["콩", "밀"], "startup", True),
        )
        proposal = mc.route(mc.default_registry(), MAJOR,
                            "question").proposal
        for mode, system, crops, expected_mode, double in cases:
            with self.subTest(mode=mode, system=system):
                facts = _branch(mode, system, crops)
                self.assertEqual(
                    mc.evaluate_food_crops_switch(facts), "ready")
                result = mc.food_crops_question_fields(proposal, facts)
                targets = {item["target"] for item in result["fields"]}
                self.assertIn(expected_mode, targets)
                self.assertEqual("double" in targets, double)
                self.assertEqual(result["held_fields"], [
                    item["field_id"] for item in proposal["fields"]
                    if item["target"] is not None
                    and item["target"] not in targets])

    def test_undecided_conflict_and_crop_count_mismatch_hold_branch_fields(self):
        proposal = mc.route(mc.default_registry(), MAJOR,
                            "question").proposal
        self.assertEqual(mc.evaluate_food_crops_switch({}),
                         "entry_mode_required")
        self.assertEqual(
            mc.evaluate_food_crops_switch(_branch("succession")),
            "cropping_system_required")
        self.assertEqual(
            mc.evaluate_food_crops_switch(_branch("startup", "single")),
            "crops_required")
        # 단작인데 2작목 / 이모작인데 1작목 — 갈래 미확정과 같게 다룬다.
        for mode, system, crops in (("succession", "single", ["벼", "보리"]),
                                    ("startup", "double", ["벼"]),
                                    ("succession", "double",
                                     ["벼", "보리", "콩"])):
            with self.subTest(crops=crops):
                facts = _branch(mode, system, crops)
                self.assertEqual(mc.evaluate_food_crops_switch(facts),
                                 "crop_count_mismatch")
                held = mc.food_crops_question_fields(proposal, facts)
                self.assertFalse({item["target"]
                                  for item in held["fields"]}
                                 & {"succession", "startup", "double"})
                self.assertIn(MAJOR + ".past_results", held["held_fields"])
        conflict = {
            "a": _fact("entry_mode", "succession"),
            "b": _fact("entry_mode", "startup"),
            "c": _fact("cropping_system", "single"),
            "d": _fact("crops", ["벼"]),
        }
        self.assertEqual(mc.evaluate_food_crops_switch(conflict),
                         "answer_conflict")
        held = mc.food_crops_question_fields(proposal, conflict)
        self.assertFalse({item["target"] for item in held["fields"]}
                         & {"succession", "startup", "double"})

    def test_superseded_and_unprovided_answers_never_decide_a_branch(self):
        facts = _branch("succession", "single", ["벼"])
        facts["old"] = _fact("entry_mode", "startup", superseded=True)
        self.assertEqual(mc.evaluate_food_crops_switch(facts), "ready")
        # 같은 값의 중복 기록은 충돌이 아니다.
        facts["again"] = _fact("entry_mode", "succession")
        self.assertEqual(mc.evaluate_food_crops_switch(facts), "ready")
        only_old = {"old": _fact("entry_mode", "startup", superseded=True)}
        self.assertEqual(mc.evaluate_food_crops_switch(only_old),
                         "entry_mode_required")
        empty_slot = {"a": _fact("entry_mode", "startup",
                                 answer_state="empty_slot")}
        self.assertEqual(mc.evaluate_food_crops_switch(empty_slot),
                         "entry_mode_required")

    def test_crops_answer_accepts_text_and_list_shapes(self):
        for crops in (["벼", "보리"], ["벼"], ["감자"]):
            with self.subTest(crops=crops):
                system = "double" if len(crops) == 2 else "single"
                self.assertEqual(
                    mc.evaluate_food_crops_switch(
                        _branch("startup", system, crops)), "ready")
        self.assertEqual(
            mc.evaluate_food_crops_switch(
                _branch("startup", "double", "벼, 보리")), "ready")
        self.assertEqual(
            mc.evaluate_food_crops_switch(
                _branch("startup", "double", "벼·보리")), "ready")


class FoodCropsNoticeTests(unittest.TestCase):
    def test_major_plan_carries_switch_and_notices(self):
        result = _major_plan(
            _record("mode", "entry_mode", "startup"),
            _record("system", "cropping_system", "single"),
            _record("crops", "crops", ["감자"]))
        self.assertEqual(result["food_crops_switch"], "ready")
        self.assertEqual([notice["id"] for notice in
                          result["food_crops_notices"]],
                         ["food_crops_startup_form"])
        question = result["proposals"]["question"]["proposal"]
        fields = {item["field_id"] for item in question["fields"]}
        self.assertIn(MAJOR + ".startup_fund", fields)
        self.assertNotIn(MAJOR + ".past_results", fields)
        self.assertIn(MAJOR + ".past_results", question["held_fields"])

    def test_major_plan_holds_branch_fields_until_branch_is_settled(self):
        result = _major_plan()
        self.assertEqual(result["food_crops_switch"],
                         "entry_mode_required")
        question = result["proposals"]["question"]["proposal"]
        self.assertFalse({item["target"] for item in question["fields"]}
                         & {"succession", "startup", "double"})
        self.assertIn(MAJOR + ".past_results", question["held_fields"])
        self.assertIn("food_crops_switch_pending",
                      [notice["id"] for notice in
                       result["food_crops_notices"]])

    def test_crop_count_mismatch_notice_names_the_count(self):
        notices = mc.food_crops_notices(
            _branch("succession", "single", ["벼", "보리"]))
        ids = [notice["id"] for notice in notices]
        self.assertIn("food_crops_crop_count_mismatch", ids)
        self.assertIn("food_crops_switch_pending", ids)
        message = next(notice["message"] for notice in notices
                       if notice["id"] == "food_crops_crop_count_mismatch")
        self.assertIn("작목 2개", message)
        self.assertIn("단작은 1작목", message)

    def test_plan_year_shift_notice_uses_no_format_example_year(self):
        def year_notices(value):
            return [notice["id"] for notice in mc.food_crops_notices({
                **_branch("succession", "single", ["벼"]),
                "year": _fact("plan_year", value)})
                    if notice["id"] == "food_crops_plan_year_shift"]

        self.assertEqual(year_notices("2027년"), [])
        self.assertEqual(year_notices("2030년"),
                         ["food_crops_plan_year_shift"])
        self.assertEqual(year_notices("계획 기간 5년"), [])
        self.assertEqual(year_notices(True), [])
        # 여러 해가 살아 있으면 하나를 고르지 않는다.
        facts = {**_branch("succession", "single", ["벼"]),
                 "a": _fact("plan_year", "2030년"),
                 "b": _fact("plan_year", "2031년")}
        self.assertNotIn("food_crops_plan_year_shift",
                         [notice["id"]
                          for notice in mc.food_crops_notices(facts)])
        message = next(notice["message"]
                       for notice in mc.food_crops_notices(
                           {**_branch("succession", "single", ["벼"]),
                            "y": _fact("plan_year", "2030년")})
                       if notice["id"] == "food_crops_plan_year_shift")
        self.assertIn("시트마다 기준연도가 다름", message)
        self.assertIn("규칙집은 엑셀을 고치지 않음", message)
        self.assertNotIn("2027", message)

    def test_crop_outside_food_crops_only_advises(self):
        facts = _branch("startup", "single", ["딸기"])
        self.assertEqual(mc.evaluate_food_crops_switch(facts), "ready")
        notices = mc.food_crops_notices(facts)
        self.assertIn("food_crops_non_food_crop",
                      [notice["id"] for notice in notices])
        for crop in ("벼", "보리", "콩", "감자", "조", "옥수수"):
            with self.subTest(crop=crop):
                self.assertNotIn("food_crops_non_food_crop", [
                    notice["id"] for notice in mc.food_crops_notices(
                        _branch("startup", "single", [crop]))])


def _record(record_id, field, value, *, superseded=False):
    record = {
        "id": record_id, "revision": 1,
        "field_id": "%s.%s" % (MAJOR, field), "kind": "reported_fact",
        "value": value, "unit": None, "value_type": "text",
        "period": None, "scope": "project",
        "answer_state": "provided",
        "verification": "claim_supported",
        "source_refs": [{"id": "s1", "locator": "line 1", "revision": 1}],
    }
    if superseded:
        record["verification"] = "superseded"
    return record


def _bound_project(temp):
    gg_core.init(temp)
    project_path = Path(temp) / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    (Path(temp) / "note.txt").write_text("전공: 식량작물\n", encoding="utf-8")
    project["sources"] = {"s1": {
        "id": "s1", "revision": 1, "path": "note.txt",
        "claims": {}, "claim_review": "synthetic"}}
    fact = {
        "id": "major-fact", "revision": 1,
        "field_id": mc.COMMON_MAJOR_FIELD, "kind": "reported_fact",
        "value": MAJOR, "unit": None, "value_type": "text",
        "period": None, "scope": "project",
        "answer_state": "provided", "verification": "claim_supported",
        "source_refs": [{"id": "s1", "locator": "line 1", "revision": 1}],
        "module_version": "0.1.0",
    }
    project["facts"] = {"major-fact": fact}
    fact["source_refs"][0]["claim_id"] = "major"
    project["sources"]["s1"]["claims"]["major"] = {
        key: fact[key] for key in (
            "field_id", "value", "unit", "period", "scope",
            "answer_state", "kind")}
    project_path.write_text(json.dumps(project), encoding="utf-8")
    return temp


def _major_plan(*records):
    with tempfile.TemporaryDirectory() as temp:
        _bound_project(temp)
        if records:
            project_path = Path(temp) / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["facts"].update(
                {record["id"]: record for record in records})
            for record in records:
                claim = project["sources"]["s1"]["claims"]
                claim[record["id"]] = {
                    key: record[key] for key in (
                        "field_id", "value", "unit", "period", "scope",
                        "answer_state", "kind")}
                record["source_refs"][0]["claim_id"] = record["id"]
            project_path.write_text(json.dumps(project), encoding="utf-8")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            code = gg.main(["major-plan", temp, "--major", MAJOR])
        assert code == 0, errors.getvalue() + output.getvalue()
        return json.loads(output.getvalue())


if __name__ == "__main__":
    unittest.main()
