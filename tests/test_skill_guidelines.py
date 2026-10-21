"""gg_guidelines: fail-closed coverage of the school's writing requirements.

Migrated from the GUI contract suite (cb72ae1).  Every path here is about one
property: you cannot reach ``guideline_ready`` by supplying a document the
project cannot still verify.  A quote sitting in an inventory is metadata,
not proof about the current manuscript.
"""
import json
import unittest

from tests._harness import ContractCase, runtime


class GuidelineTests(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.gl = runtime("gg_guidelines")

    def load(self, project):
        return self.core.load(project)

    def add_school_source(self, project, kind="official_pdf"):
        """Register a school-rule source so _school_source() sees one."""
        path = project / "sources" / "school.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 school rules\n")
        p = self.load(project)
        p["sources"]["src-school"] = {
            "id": "src-school", "revision": p["revision"],
            "path": "sources/school.pdf", "kind": kind,
            "hash": self.core.digest(path.read_bytes()), "claims": {},
        }
        return p, path

    def write_inventory(self, project, requirements, *, schema=None,
                        pages=26, source_id="src-school", source_hash=None):
        inv = {
            "schema": schema or self.gl.SCHEMA,
            "source": {"id": source_id, "sha256": source_hash,
                       "physical_page_count": pages},
            "requirements": requirements,
            "conflicts": [],
        }
        path = project / "sources" / "guidelines" / "inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
        return path

    def _profile(self, p, inv_path, inv_hash, requirement_ids):
        return {
            "id": "guideline_profile", "profile_id": "knuaf-2020",
            "source_refs": [{"id": "src-school",
                             "revision": p["revision"],
                             "locator": "p.1-5"}],
            "inventory": {"path": str(inv_path), "sha256": inv_hash},
            "requirement_ids": requirement_ids,
        }

    # --- nothing configured -----------------------------------------------

    def test_an_empty_project_is_not_configured_rather_than_passing(self):
        project = self.make_project()
        r = self.gl.check(project, self.load(project))
        self.assertEqual("not_configured", r["status"])
        self.assertIsNone(r["guideline_ready"])
        self.assertEqual([], r["blockers"])

    def test_a_school_source_without_a_profile_blocks(self):
        project = self.make_project()
        p, _ = self.add_school_source(project)
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIs(False, r["guideline_ready"])
        self.assertIn("guideline_profile", r["blockers"])

    def test_a_malformed_profile_blocks_instead_of_being_skipped(self):
        profiles = [
            "not-a-dict",
            {"id": "wrong_id"},
            {"id": "guideline_profile"},
            {"id": "guideline_profile", "profile_id": "", "source_refs": [],
             "inventory": {}, "requirement_ids": []},
            {"id": "guideline_profile", "profile_id": "x",
             "source_refs": [{"id": "s"}], "inventory": {},
             "requirement_ids": ["A", "A"]},
        ]
        for profile in profiles:
            with self.subTest(profile=profile):
                project = self.make_project()
                p = self.load(project)
                p["rules"]["guideline_profile"] = profile
                r = self.gl.check(project, p)
                self.assertEqual("blocked", r["status"])
                self.assertIs(False, r["guideline_ready"])

    # --- the inventory must still be the one that was reviewed ------------

    def test_a_tampered_inventory_blocks_on_the_hash(self):
        project = self.make_project()
        p, src = self.add_school_source(project)
        inv = self.write_inventory(
            project, [], source_hash=self.core.digest(src.read_bytes()))
        p["rules"]["guideline_profile"] = self._profile(
            p, "sources/guidelines/inventory.json",
            self.core.digest(inv.read_bytes()), [])
        before = self.gl.check(project, p)["blockers"]
        self.assertNotIn("inventory_hash", before)
        inv.write_text(
            inv.read_text(encoding="utf-8").replace(
                '"conflicts": []', '"conflicts": [1]'),
            encoding="utf-8")
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIn("inventory_hash", r["blockers"])
        self.assertIs(False, r["guideline_ready"])

    def test_a_foreign_schema_blocks(self):
        project = self.make_project()
        p, src = self.add_school_source(project)
        inv = self.write_inventory(
            project, [], schema="something-else/v9",
            source_hash=self.core.digest(src.read_bytes()))
        p["rules"]["guideline_profile"] = self._profile(
            p, "sources/guidelines/inventory.json",
            self.core.digest(inv.read_bytes()), [])
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIn("inventory_schema", r["blockers"])
        self.assertNotIn("inventory_hash", r["blockers"])

    def test_an_unreadable_inventory_blocks_rather_than_raising(self):
        project = self.make_project()
        p, src = self.add_school_source(project)
        path = project / "sources" / "guidelines" / "inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        p["rules"]["guideline_profile"] = self._profile(
            p, "sources/guidelines/inventory.json",
            self.core.digest(path.read_bytes()), [])
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIs(False, r["guideline_ready"])

    def test_an_inventory_outside_the_working_folder_is_refused(self):
        project = self.make_project()
        p, _ = self.add_school_source(project)
        p["rules"]["guideline_profile"] = self._profile(
            p, "../탈출.json", "0" * 64, [])
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIs(False, r["guideline_ready"])

    def test_requirements_missing_from_the_inventory_block(self):
        project = self.make_project()
        p, src = self.add_school_source(project)
        inv = self.write_inventory(
            project, [], source_hash=self.core.digest(src.read_bytes()))
        p["rules"]["guideline_profile"] = self._profile(
            p, "sources/guidelines/inventory.json",
            self.core.digest(inv.read_bytes()), ["OG-001"])
        r = self.gl.check(project, p)
        self.assertEqual("blocked", r["status"])
        self.assertIs(False, r["guideline_ready"])

    # --- the report shape the app and gg.py rely on -----------------------

    def test_every_report_carries_the_schema_and_readiness_fields(self):
        project = self.make_project()
        r = self.gl.check(project, self.load(project))
        self.assertEqual("gg-guideline-report/v1", r["schema"])
        self.assertGreaterEqual(
            set(r),
            {"status", "guideline_ready", "profile_id", "inventory", "items",
             "blockers", "pending_user_finish", "reason"})

    def test_ready_is_never_true_while_any_blocker_stands(self):
        project = self.make_project()
        p, _ = self.add_school_source(project)
        r = self.gl.check(project, p)
        self.assertTrue(r["blockers"])
        self.assertIsNot(True, r["guideline_ready"])


if __name__ == "__main__":
    unittest.main()
