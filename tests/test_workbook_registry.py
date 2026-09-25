"""Common reference-workbook decision (D3a; A03, A06, A20-P, A40).

Pure decision table over a caller-verified inventory, plus the shipped
X01/X02 identity data.  Every branch returns the same keys.
"""
import json
from pathlib import Path

from tests import _fruit_fixtures as ff
from tests._harness import REPO_ROOT, ContractCase, runtime

KEYS = {"major_id", "inventory_status", "reference_set", "comparison_refs",
        "common_set_status", "selection_status", "selection_reason",
        "authority_status", "candidate_roles", "source_revisions",
        "completeness", "survey_basis"}
X01 = "a" * 64
X02 = "b" * 64
MEMBERS = {"X01": {"sha256": X01, "sheets": ["s1", "s2 "], "label": "x01"},
           "X02": {"sha256": X02, "sheets": ["s1", "s2 "], "label": "x02"}}


def common(fid, sha, sheets=("s1", "s2 "), **over):
    f = {"file_id": fid, "label": fid, "origin": "common_root",
         "relative_id": None, "sha256": sha, "readable": True,
         "sheets": list(sheets), "declared_role": "professor_reference",
         "role_authority": {"kind": "none", "ref": None},
         "link_state": "ok"}
    f.update(over)
    return f


def major(fid, **over):
    f = {"file_id": fid, "label": fid, "origin": "major_folder",
         "relative_id": fid + ".xlsx", "sha256": "c" * 64, "readable": True,
         "sheets": ["x"], "declared_role": "student_example",
         "role_authority": {"kind": "user_statement", "ref": "ans"},
         "link_state": "ok"}
    f.update(over)
    return f


def inv(files, status="complete", verified=True):
    return {"survey": {"scope_label": "fruit", "scope_id": "/s/fruit",
                       "status": status,
                       "evidence": {"verified": verified,
                                    "basis": "user_attested"}},
            "files": files}


SEL_NONE = {"state": "absent", "file_id": None, "fact_id": None}


class DecisionTableTests(ContractCase):
    def setUp(self):
        self.wr = runtime("gg_workbook_registry")

    def resolve(self, files, selection=SEL_NONE, **kw):
        out = self.wr.resolve_workbook_references(
            "fruit_trees", inv(files, **kw), selection,
            reference_set=MEMBERS)
        self.assertEqual(set(out), KEYS)
        return out

    def test_unsurveyed_or_unverified_is_not_absent(self):
        both = [common("x01", X01), common("x02", X02)]
        for kw in ({"status": "partial"}, {"status": "unreadable"},
                   {"status": "complete", "verified": False}):
            out = self.resolve(both, **kw)
            self.assertEqual(out["inventory_status"], "not_surveyed")
            self.assertEqual(out["reference_set"], [])
            self.assertEqual(out["comparison_refs"], ["x01", "x02"])

    def test_confirmed_absence_uses_both_common(self):
        out = self.resolve([common("x01", X01), common("x02", X02)])
        self.assertEqual(out["inventory_status"], "confirmed_absent")
        self.assertEqual(out["reference_set"], ["x01", "x02"])
        self.assertEqual(out["completeness"], "complete")
        self.assertEqual(out["authority_status"], "policy")

    def test_one_common_missing_or_mismatched(self):
        out = self.resolve([common("x01", X01)])
        self.assertEqual(out["common_set_status"], "incomplete")
        self.assertEqual(out["completeness"], "incomplete")
        out = self.resolve([common("x01", X01),
                            common("x02", X02, sheets=("s1", "s2"))])
        self.assertEqual(out["common_set_status"], "mismatch")
        self.assertEqual(out["reference_set"], ["x01"])
        out = self.resolve([common("x01", X01),
                            common("x02", X02, link_state="stale_source")])
        self.assertEqual(out["reference_set"], ["x01"])

    def test_common_completeness_independent_of_major_files(self):
        out = self.resolve([common("x01", X01), major("a")])
        self.assertEqual(out["inventory_status"], "present")
        self.assertEqual(out["common_set_status"], "incomplete")

    def test_major_file_selection_branches(self):
        files = [common("x01", X01), common("x02", X02), major("a"),
                 major("b", declared_role="unclassified",
                       role_authority={"kind": "none", "ref": None}),
                 major("bad", readable=False)]
        cases = (
            ({"state": "absent", "file_id": None}, "selection_required"),
            ({"state": "none", "file_id": None}, "selection_required"),
            ({"state": "conflict", "file_id": None}, "selection_conflict"),
            ({"state": "selected", "file_id": "zzz"}, "selection_invalid"),
            ({"state": "selected", "file_id": "x01"}, "selection_invalid"),
            ({"state": "selected", "file_id": "bad"}, "selected_unusable"),
            ({"state": "selected", "file_id": "a"}, "user_selected"),
        )
        for selection, expected in cases:
            with self.subTest(selection=selection):
                out = self.resolve(files, selection)
                self.assertEqual(out["selection_status"], expected)
                self.assertEqual(out["comparison_refs"], ["x01", "x02"])
        out = self.resolve(files, {"state": "selected", "file_id": "a"})
        self.assertEqual(out["reference_set"], ["a"])
        self.assertEqual(out["authority_status"], "declared")
        out = self.resolve(files, {"state": "selected", "file_id": "b"})
        self.assertEqual(out["authority_status"], "unverified")

    def test_roles_never_inferred_from_names(self):
        f = major("강동현_교수_양식", declared_role="unclassified",
                  role_authority={"kind": "none", "ref": None})
        out = self.resolve([f])
        role = out["candidate_roles"][0]
        self.assertEqual(role["declared_role"], "unclassified")
        self.assertEqual(role["authority"], "unverified")

    def test_malformed_inventory_raises(self):
        with self.assertRaises(self.wr.InventoryError):
            self.resolve([major("a"), major("a")])
        with self.assertRaises(self.wr.InventoryError):
            self.resolve([major("a", relative_id=None)])
        with self.assertRaises(self.wr.InventoryError):
            self.wr.resolve_workbook_references(
                "", inv([]), SEL_NONE, reference_set=MEMBERS)


class ReferenceIdentityTests(ContractCase):
    """A20-P — shipped X01/X02 identity keeps the exact sheet names."""

    def test_shipped_reference_set(self):
        wr = runtime("gg_workbook_registry")
        members = wr.load_reference_set()
        self.assertEqual(set(members), {"X01", "X02"})
        for m in members.values():
            self.assertEqual(len(m["sha256"]), 64)
            self.assertIn("10. 감가상각비계획 ", m["sheets"])
            self.assertIn(" 5. 판매계획", m["sheets"])
        text = Path(wr.REFERENCE_SET_PATH).read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn(":\\\\", text)

    def test_trailing_space_is_identity(self):
        wr = runtime("gg_workbook_registry")
        out = wr.resolve_workbook_references(
            "fruit_trees", inv([common("x01", X01, sheets=("s1", "s2")),
                                common("x02", X02)]),
            SEL_NONE, reference_set=MEMBERS)
        self.assertEqual(out["candidate_roles"][0]["identity_state"],
                         "common_mismatch")

    def test_scan_reads_identity_and_never_completes_survey(self):
        import tempfile
        wr = runtime("gg_workbook_registry")
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "a.xlsx"
            good.write_bytes(ff.workbook_bytes("a"))
            bad = Path(tmp) / "b.xlsx"
            bad.write_bytes(b"not a workbook")
            before = {p.name: p.read_bytes() for p in Path(tmp).iterdir()}
            out = wr.scan([good, bad], scope_root=tmp)
            self.assertEqual({p.name: p.read_bytes()
                              for p in Path(tmp).iterdir()}, before)
        self.assertEqual(out["survey"]["status"], "partial")
        rows = {f["file_id"]: f for f in out["files"]}
        self.assertEqual(rows["a.xlsx"]["sheets"], ff.SHEETS)
        self.assertFalse(rows["b.xlsx"]["readable"])
        self.assertEqual(rows["a.xlsx"]["declared_role"], "unclassified")


class PackagingTests(ContractCase):
    """A06 — no originals, extracts or local paths in the shipped tree."""

    def test_no_original_documents_shipped(self):
        skill = REPO_ROOT / "skills" / "knuaf-doc"
        banned = {".xlsx", ".xlsm", ".xls", ".hwpx", ".hwp", ".pdf"}
        found = [p.relative_to(REPO_ROOT).as_posix()
                 for p in skill.rglob("*") if p.suffix.lower() in banned]
        self.assertEqual(found, [])

    def test_fruit_reference_text_has_no_local_paths(self):
        skill = REPO_ROOT / "skills" / "knuaf-doc"
        for rel in ("references/fruit-trees/README.md",
                    "references/workbook-reference-set.json",
                    "scripts/gg_fruit_plan.py",
                    "scripts/gg_workbook_registry.py",
                    "scripts/gg_research_handoff.py"):
            text = (skill / rel).read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertNotIn("/Users/", text)
                self.assertNotIn("Desktop", text)
                json.dumps(text)
