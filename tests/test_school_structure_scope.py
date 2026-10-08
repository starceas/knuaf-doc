"""X1-03: major-specific outline contracts and submission blockers."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests._harness import ContractCase, bind_major, runtime, section_op, write_text

doc, core, mc = (runtime(name) for name in ("gg_document", "gg_core", "gg_major_contract"))

FRUIT = "# Ⅱ. 영농환경분석\n## 4. SWOT분석\n농장의 강점과 보완할 점을 살핀다.\n"
HORT1 = "# Ⅲ. 영농환경분석\n## 4. SWOT 분석\n농장의 강점과 보완할 점을 살핀다.\n"
HORT2 = HORT1.replace("영농환경분석", "영농환경 분석")
SPECIALTY = (
    "# Ⅰ. 머리말\n형태 방법 조건 전망 애로 비전\n"
    "# Ⅱ. 외부환경\n환경을 살핀다.\n"
    "# Ⅲ. 영농계획\n## 2. 경영계획\n### 마. SWOT 분석\n강점 약점 기회 위협\n"
    "# Ⅳ. 재무 계획\n계획을 살핀다.\n"
    "# Ⅴ. 맺음말\n결론 전략 보완\n"
    "# Ⅵ. 참고문헌\n농촌진흥청. 2026. 자료.\n# 감사의 글\n감사합니다.\n"
)


def school_rows(text, major):
    return [(cid, reason) for cid, reason in doc.check(text, None, major_id=major)
            if cid.startswith("school_")]


class SchoolStructureScopeTests(ContractCase):
    def test_s01_ht1_ht2_normal_sections(self):
        for major, text in (("fruit_trees", FRUIT), ("hort_env_systems", HORT1),
                            ("hort_env_systems", HORT2)):
            with self.subTest(major=major, text=text):
                self.assertEqual(school_rows(text, major), [])

    def test_wrong_chapter_or_section_is_still_blocked(self):
        for major, text in (("fruit_trees", FRUIT), ("hort_env_systems", HORT1)):
            for bad in (text.replace("4.", "2."), text.replace("Ⅱ.", "Ⅲ.")
                        if major == "fruit_trees" else text.replace("Ⅲ.", "Ⅱ."),
                        text + "# Ⅳ. 계획\n## 4. SWOT 분석\n다른 위치\n"):
                with self.subTest(major=major, text=bad):
                    self.assertIn("school_swot", {cid for cid, _ in school_rows(bad, major)})

    def test_specialty_structure_behavior_is_preserved(self):
        self.assertNotIn("school_swot", {cid for cid, _ in school_rows(SPECIALTY, "specialty_crops")})
        for bad in (SPECIALTY.replace("마. SWOT", "라. SWOT"),
                    SPECIALTY.replace("2. 경영계획", "4. SWOT 분석"),
                    SPECIALTY.replace("Ⅲ. 영농계획", "Ⅱ. 영농계획"), FRUIT):
            self.assertIn("school_swot", {cid for cid, _ in school_rows(bad, "specialty_crops")})
        self.assertIn("school_chapters", {cid for cid, _ in school_rows(
            SPECIALTY.replace("# Ⅳ. 재무 계획", "# 다른 장"), "specialty_crops")})

    def test_no_outline_contract_does_not_inherit_specialty(self):
        for major in (None, "unknown_major", "industrial_insects", "food_crops", "forest"):
            for text in (FRUIT, HORT1, SPECIALTY.replace("마. SWOT", "라. SWOT")):
                with self.subTest(major=major):
                    self.assertEqual(school_rows(text, major), [])

    def test_peer_full_text_does_not_require_specialty_six_chapters(self):
        for major in ("fruit_trees", "hort_env_systems"):
            text = SPECIALTY.replace("SWOT", "역량")
            text = text.replace("# Ⅳ. 재무 계획", "# Ⅳ. 맺음말·참고문헌")
            text = text[:text.index("# Ⅴ.")]
            self.assertEqual(school_rows(text, major), [])

    def test_notation_equivalence_and_toc_exclusion(self):
        for major, text in (("fruit_trees", FRUIT), ("hort_env_systems", HORT1)):
            for variant in (text.replace("Ⅱ.", "II.").replace("Ⅲ.", "III."),
                            text.replace(".", "．"), text.replace("4.", "4 .")):
                self.assertEqual(school_rows(variant, major), [])
            wrapped = "\n".join("# " + x for x in ("겉표지", "표제면", "제출서", "인준서", "목차"))
            wrapped += "\n# Ⅰ. 머리말\n## 2. SWOT 분석\n# 감사의 글\n" + text
            self.assertEqual(school_rows(wrapped, major), [])

    def test_fruit_contract_comes_from_registry(self):
        registry = mc.default_registry()
        module = registry.resolve("fruit_trees")
        altered = replace(module, document_plan=tuple(
            replace(n, rationale="Ⅱ-5 당면과제와 SWOT 대응") if n.role == "swot" else n
            for n in module.document_plan))
        class Registry:
            def resolve(self, major):
                return altered if major == "fruit_trees" else registry.resolve(major)
        with patch.object(mc, "default_registry", return_value=Registry()):
            self.assertIn("school_swot", {cid for cid, _ in school_rows(FRUIT, "fruit_trees")})
            self.assertEqual(school_rows(FRUIT.replace("4.", "5."), "fruit_trees"), [])

    def test_hort_contract_comes_from_declared_precedent_profile(self):
        profile = json.loads(doc.HORT_PROFILE_PATH.read_text(encoding="utf-8"))
        for precedent in profile["precedents"]:
            for section in precedent["sections"]:
                if "SWOT" in section["title"]:
                    section["item"] = "ch3_sec5"
                    section["title"] = "5. SWOT 분석"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            with patch.object(doc, "HORT_PROFILE_PATH", path):
                self.assertIn("school_swot", {cid for cid, _ in school_rows(HORT1, "hort_env_systems")})
                self.assertEqual(school_rows(HORT1.replace("4.", "5."), "hort_env_systems"), [])

    def test_missing_specific_outline_rule_is_skipped(self):
        registry = mc.default_registry()
        module = registry.resolve("fruit_trees")
        altered = replace(module, document_plan=tuple(n for n in module.document_plan if n.role != "swot"))
        with patch.object(registry, "resolve", return_value=altered), patch.object(mc, "default_registry", return_value=registry):
            self.assertEqual(school_rows(FRUIT.replace("4.", "2."), "fruit_trees"), [])

    def test_declared_profile_read_failure_is_reported(self):
        with patch.object(doc, "HORT_PROFILE_PATH", Path("/nonexistent/knuaf/profile.json")):
            self.assertIn("school_structure_policy", {cid for cid, _ in school_rows(HORT1, "hort_env_systems")})

    def test_canonical_submission_candidate_checks(self):
        for major, text in (("fruit_trees", FRUIT), ("hort_env_systems", HORT2),
                            ("specialty_crops", FRUIT)):
            with self.subTest(major=major):
                root = self.make_project()
                bind_major(root, major)
                path = "sections/swot.md"
                write_text(root, path, text)
                core.apply(root, {"request_id": "swot", "ops": [
                    section_op("swot", "계획", path)]}, core.load(root)["revision"])
                rows = [r for r in core.checks(root, core.load(root)) if r["check_id"] == "school_swot"]
                if major == "specialty_crops":
                    self.assertTrue(rows)
                else:
                    self.assertEqual(rows, [])
                    write_text(root, path, text.replace("4.", "2."))
                    rows = [r for r in core.checks(root, core.load(root)) if r["check_id"] == "school_swot"]
                    self.assertTrue(rows)
                for row in rows:
                    self.assertEqual((row["severity"], row["status"]), ("error", "fail"))
                    self.assertIn("submission_candidate", row["required_for"])
