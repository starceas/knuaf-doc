"""Public CLI integration for the opt-in major writing contract.

The synthetic projects use the existing guarded gg_core.apply transaction.
No student document, workbook, or external source is involved.
"""

import json
import subprocess
import sys

from tests._harness import ContractCase, SCRIPTS, fact_op, runtime, source_op, write_text


class CommonContractFlowTests(ContractCase):
    def _run(self, root, command, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "gg.py"), command, str(root), *args],
            capture_output=True, text=True,
        )

    def _bind(self, root, major_id, module_version):
        write_text(root, "answer.txt", "전공 선택: " + major_id + "\n")
        selected = fact_op(
            "selected_major", "common.major_id", major_id, None,
            verification="claim_supported",
        )
        selected["value"]["module_version"] = module_version
        core = runtime("gg_core")
        core.apply(
            root,
            {"request_id": "major-selection", "ops": [source_op(), selected]},
            0,
        )

    def test_unbound_project_holds_major_work_without_writes(self):
        root = self.make_project()
        before = self._tree_bytes(root)
        result = self._run(root, "major-plan")
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "held")
        self.assertTrue(value["common_only"])
        self.assertEqual(self._tree_bytes(root), before)

    def test_specialty_binding_retains_raw_lookup_and_blocks_unapproved_use(self):
        root = self.make_project()
        self._bind(root, "specialty_crops", "1.0.0")
        before = self._tree_bytes(root)

        plan = self._run(root, "major-plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        body = json.loads(plan.stdout)
        self.assertEqual(body["binding"]["major_id"], "specialty_crops")
        self.assertEqual(body["binding"]["module_version"], "1.0.0")
        self.assertEqual(body["proposals"]["finance"]["status"], "supported")

        query = self._run(
            root, "rda-candidates", "--crop", "유지작물계", "--region", "전국",
            "--rda-kind", "production_stat", "--year", "2024",
            "--unit", "kg", "--use-scope", "plan_input",
        )
        self.assertEqual(query.returncode, 0, query.stderr)
        candidates = json.loads(query.stdout)
        self.assertEqual(candidates["major_id"], "specialty_crops")
        self.assertEqual(candidates["counts"]["candidates"], 1)
        self.assertEqual(candidates["counts"]["approved"], 0)
        self.assertNotEqual(candidates["status"], "approved")

        raw = self._run(
            root, "rda-lookup", "--crop", "유지작물계", "--region", "전국",
            "--major", "특용작물", "--rda-kind", "production_stat",
            "--year", "2024",
        )
        self.assertEqual(raw.returncode, 0, raw.stderr)
        self.assertEqual(json.loads(raw.stdout)["status"], "ambiguous")
        self.assertEqual(self._tree_bytes(root), before)

    def test_insect_binding_has_plan_and_empty_pack_without_crop_output(self):
        root = self.make_project()
        self._bind(root, "industrial_insects", "0.1.0")
        plan = self._run(root, "major-plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        body = json.loads(plan.stdout)
        self.assertEqual(body["proposals"]["finance"]["status"], "unsupported")
        document = body["proposals"]["document"]["proposal"]
        self.assertTrue(document["sections"])
        self.assertNotIn("school_excel_workbook", str(document))
        self.assertNotIn("특용작물", str(document))

        query = self._run(
            root, "rda-candidates", "--crop", "쌍별귀뚜라미",
            "--region", "전국", "--rda-kind", "production_stat",
            "--year", "2024", "--form", "생체", "--unit", "kg",
            "--use-scope", "plan_input",
        )
        self.assertEqual(query.returncode, 0, query.stderr)
        candidates = json.loads(query.stdout)
        self.assertEqual(candidates["status"], "not_found")
        self.assertEqual(candidates["observation"]["reason"], "major_pack_empty")

        before = self._tree_bytes(root)
        paper = self._run(root, "paper", "--input", "unused.json")
        self.assertEqual(paper.returncode, 2)
        self.assertEqual(json.loads(paper.stdout)["status"], "blocked")
        self.assertEqual(self._tree_bytes(root), before)

        core = runtime("gg_core")
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "unused.json", b'{"writing_year":2026}',
                       "build/insect.md")
        self.assertEqual(caught.exception.result["reason"], "unsupported_major")
        self.assertEqual(self._tree_bytes(root), before)

    def test_explicit_insect_spec_is_blocked_even_without_project_binding(self):
        root = self.make_project()
        spec = {"writing_year": 2026, "title": "합성 산업곤충",
                "major_id": "industrial_insects"}
        spec_bytes = json.dumps(spec, ensure_ascii=False).encode()
        write_text(root, "spec.json", spec_bytes.decode())
        before = self._tree_bytes(root)

        cli = self._run(root, "paper", "--input", "spec.json")
        self.assertNotEqual(cli.returncode, 0)
        self.assertEqual(json.loads(cli.stdout)["reason"], "unsupported_major")
        self.assertEqual(self._tree_bytes(root), before)

        core = runtime("gg_core")
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "spec.json", spec_bytes, "build/insect.md")
        self.assertEqual(caught.exception.result["reason"], "unsupported_major")
        self.assertEqual(self._tree_bytes(root), before)

        school_paper = runtime("gg_school_paper")
        with self.assertRaises(ValueError):
            school_paper.paper(spec)
        with self.assertRaises(ValueError):
            school_paper.paper({"writing_year": 2026, "major": "산업곤충"})
        with self.assertRaises(ValueError):
            school_paper.paper({"writing_year": 2026, "major": "곤충"})
        with self.assertRaises(ValueError):
            school_paper.paper({"writing_year": 2026,
                                "school_profile": {
                                    "department": "산업곤충학과"}})
        self.assertIn("Ⅰ. 머리말", school_paper.paper(
            {"writing_year": 2026, "title": "합성 특용작물"}))

    def test_unbound_insect_department_does_not_render_crop_body(self):
        root = self.make_project()
        spec = {"writing_year": 2026,
                "school_profile": {"school": "한국농수산대학교",
                                   "department": "산업 곤충학과"}}
        write_text(root, "insect-dept.json", json.dumps(spec, ensure_ascii=False))
        before = self._tree_bytes(root)
        result = self._run(root, "paper", "--input", "insect-dept.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "unsupported_major")
        self.assertEqual(self._tree_bytes(root), before)

    def test_new_major_marker_requires_binding_and_school_profile(self):
        root = self.make_project()
        marked = {"writing_year": 2026, "major_id": "specialty_crops",
                  "school_profile": {"school": "한국농수산대학교",
                                     "department": "특용작물학과"}}
        write_text(root, "marked.json", json.dumps(marked, ensure_ascii=False))
        before = self._tree_bytes(root)
        unbound = self._run(root, "paper", "--input", "marked.json")
        self.assertNotEqual(unbound.returncode, 0)
        self.assertEqual(json.loads(unbound.stdout)["reason"],
                         "major_binding_required")
        self.assertEqual(self._tree_bytes(root), before)

        self._bind(root, "specialty_crops", "1.0.0")
        core = runtime("gg_core")
        no_profile = {"writing_year": 2026, "major_id": "specialty_crops"}
        bound_before = self._tree_bytes(root)
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "no-profile.json",
                       json.dumps(no_profile).encode(), "build/no-profile.md")
        self.assertEqual(caught.exception.result["reason"],
                         "school_profile_required")
        self.assertEqual(self._tree_bytes(root), bound_before)

        empty_profile = {"writing_year": 2026,
                         "major_id": "specialty_crops",
                         "school_profile": {}}
        with self.assertRaises(core.OperationError) as caught:
            core.paper(root, "empty-profile.json",
                       json.dumps(empty_profile).encode(), "build/empty.md")
        self.assertEqual(caught.exception.result["reason"],
                         "school_profile_required")
        self.assertEqual(self._tree_bytes(root), bound_before)


if __name__ == "__main__":
    import unittest
    unittest.main()
