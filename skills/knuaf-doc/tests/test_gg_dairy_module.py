import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import gg  # noqa: E402
import gg_core  # noqa: E402
import gg_document  # noqa: E402
import gg_major_contract as mc  # noqa: E402


class DairyModuleTests(unittest.TestCase):
    def test_registry_marker_plan_and_unsupported_outputs(self):
        registry = mc.default_registry()
        module = registry.resolve("dairy")
        self.assertEqual(module.module_version, "0.1.0")
        self.assertEqual(module.packs, ())
        self.assertEqual(mc.KNOWN_MAJOR_MARKERS["dairy"], ("낙농",))
        self.assertEqual(registry.major_ids[-1], "dairy")
        document = mc.route(registry, "dairy", "document").proposal
        self.assertEqual(len(document["sections"]), 28)
        self.assertEqual(len(document["mandated_sections"]), 28)
        sections = {item["section_id"]: item
                    for item in document["sections"]}
        self.assertIn("일반 목차·표 목차·그림 목차",
                      sections["lists"]["rationale"])
        for number in range(1, 13):
            rationale = sections["chapter_%02d" % number]["rationale"]
            self.assertIn("한우 대응 항목으로 치환", rationale)
            self.assertIn("references/dairy/school-toc.md", rationale)
        for output in ("paper", "export", "school_excel_workbook"):
            with self.assertRaises(mc.UnsupportedOutputError):
                mc.route(registry, "dairy", "document", output=output)
    def test_major_plan_reports_switch_and_filters_selected_questions(self):
        with tempfile.TemporaryDirectory() as temp:
            gg_core.init(temp)
            project_path = Path(temp) / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            (Path(temp) / "note.txt").write_text(
                "전공: 낙농\n", encoding="utf-8")
            project["sources"] = {
                "s1": {
                    "id": "s1", "revision": 1, "path": "note.txt",
                    "claims": {}, "claim_review": "synthetic"}}
            project["facts"] = {
                "major-fact": {
                    "id": "major-fact", "revision": 1,
                    "field_id": mc.COMMON_MAJOR_FIELD,
                    "kind": "reported_fact", "value": "dairy",
                    "unit": None, "value_type": "text", "period": None,
                    "scope": "project", "answer_state": "provided",
                    "verification": "claim_supported",
                    "source_refs": [{"id": "s1", "locator": "line 1",
                                     "revision": 1}],
                    "module_version": "0.1.0",
                },
                "livestock-fact": {
                    "id": "livestock-fact", "revision": 1,
                    "field_id": "dairy.livestock_type",
                    "kind": "reported_fact", "value": "hanwoo",
                    "unit": None, "value_type": "text", "period": None,
                    "scope": "project", "answer_state": "provided",
                    "verification": "claim_supported",
                    "source_refs": [{"id": "s1", "locator": "line 1",
                                     "revision": 1}],
                },
                "mode-fact": {
                    "id": "mode-fact", "revision": 1,
                    "field_id": "dairy.operation_mode",
                    "kind": "reported_fact", "value": "integrated",
                    "unit": None, "value_type": "text", "period": None,
                    "scope": "project", "answer_state": "provided",
                    "verification": "claim_supported",
                    "source_refs": [{"id": "s1", "locator": "line 1",
                                     "revision": 1}],
                },
            }
            project_path.write_text(
                json.dumps(project), encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result_code = gg.main(["major-plan", temp, "--major", "dairy"])
            self.assertEqual(result_code, 0, errors.getvalue() + output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result["dairy_switch"], "ready")
            question = result["proposals"]["question"]["proposal"]
            self.assertNotIn(
                "dairy.milk_collector",
                {field["field_id"] for field in question["fields"]})
            self.assertIn(
                "dairy.herd_stages",
                {field["field_id"] for field in question["fields"]})


    def test_switch_states_and_livestock_question_filter(self):
        def facts(livestock=None, mode=None):
            result = {}
            if livestock:
                result["livestock"] = {
                    "field_id": "dairy.livestock_type",
                    "answer_state": "provided", "value": livestock}
            if mode:
                result["mode"] = {
                    "field_id": "dairy.operation_mode",
                    "answer_state": "provided", "value": mode}
            return result

        self.assertEqual(mc.evaluate_dairy_switch({}), "livestock_type_required")
        self.assertEqual(mc.evaluate_dairy_switch(facts("hanwoo")),
                         "operation_mode_required")
        self.assertEqual(mc.evaluate_dairy_switch(facts("dairy_cattle", "breeding")),
                         "mismatch")
        self.assertEqual(mc.evaluate_dairy_switch(facts("dairy_cattle", "milking")),
                         "ready")
        self.assertEqual(mc.evaluate_dairy_switch(facts("hanwoo", "milking")),
                         "mismatch")
        for mode in ("breeding", "fattening", "integrated"):
            self.assertEqual(mc.evaluate_dairy_switch(facts("hanwoo", mode)),
                             "ready")
        proposal = mc.route(mc.default_registry(), "dairy", "question").proposal
        unresolved = mc.dairy_question_fields(proposal, facts())
        self.assertEqual(unresolved["held_fields"], [
            "dairy.milk_collector", "dairy.quota_plan",
            "dairy.milk_test_records", "dairy.herd_stages",
            "dairy.shipment_target", "dairy.sales_channel"])
        resolved = mc.dairy_question_fields(
            proposal, facts("hanwoo", "integrated"))
        targets = {item["target"] for item in resolved["fields"]}
        self.assertNotIn("dairy_cattle", targets)
        self.assertIn("hanwoo", targets)

    def test_summary_unicode_chapters_and_dairy_table_exemptions(self):
        text = "# 요 약\n# Ⅰ. 사업의 목적\n# Ⅱ. 투자분석결과\n# Ⅲ. 손익계산서\n# Ⅳ. 자금운용계획\n# 1. 머리말\n# 2. 농업환경분석\n"
        parsed = gg_document.parse(text, major_id="dairy")
        headings = [node for node in parsed if node["kind"] == "heading"]
        self.assertEqual([node["level"] for node in headings], [1, 2, 2, 2, 2, 1, 1])
        crop_parsed = gg_document.parse(text)
        crop_headings = [node for node in crop_parsed if node["kind"] == "heading"]
        self.assertEqual(crop_headings[1]["level"], 1)
        table_text = (
            "표 1. 기상 및 농장개요\n"
            "| 평균기온 | 농장명 |\n| --- | --- |\n| 값 | 값 |\n"
        )
        codes = {code for code, _ in gg_document.check(
            table_text, None, major_id="dairy")}
        self.assertNotIn("climate_fields", codes)
        self.assertNotIn("farm_overview_fields", codes)


if __name__ == "__main__":
    unittest.main()
