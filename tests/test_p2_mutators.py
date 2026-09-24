"""P2 lane A tests — mutator discipline (API §1-§2, SPEC §3/§6-§7).

Every mutator must assert a real held capability before any product
write, clean only its own staging paths, preserve managed publications
when a later step fails, and report commit evidence instead of guessing.
"""
import json
import os
from pathlib import Path
import unittest

from tests._harness import (
    ContractCase,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    write_text,
)


def _seed(root):
    write_text(root, "answer.txt", "농장명: 행복농장\n")
    write_text(root, "intro.md", "농장명은 행복농장이다.\n")
    fact = fact_op("farm_name", "farm_name", "행복농장", None, claim_id="c1")
    ops = [
        source_op(claims=claim_of(fact["value"])),
        fact,
        section_op(
            "intro", "머리말", "intro.md",
            claims=[section_claim(fact["value"], "농장명은 행복농장이다")]),
    ]
    return runtime("gg_core").apply(
        root, {"request_id": "seed", "ops": ops}, 0)


def _spec(root):
    spec = root / "paper-spec.json"
    spec.write_text(json.dumps(
        {"author": "합성", "writing_year": 2026, "years": 5,
         "school_profile": {"mode": "school"}},
        ensure_ascii=False), encoding="utf-8")
    return spec


def _residue(root):
    return sorted(
        p.name for p in Path(root).rglob("*")
        if p.name.startswith((".gg-tmp", ".gg-export-", ".gg-paper-",
                              ".gg-import-", ".gg-staging")))


class CapabilityEnforcementTests(ContractCase):
    def test_forged_capability_rejected_before_any_write(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        before = (root / "project.json").read_bytes()
        forged = gg_lock.Capability()  # never issued by Lock.__enter__
        for call in (
            lambda: core._apply_locked(
                root, {"request_id": "f", "ops": []}, 0,
                capability=forged),
            lambda: core._init_locked(root, forged),
            lambda: core.export_locked(root, "draft", capability=forged),
        ):
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
        self.assertEqual(before, (root / "project.json").read_bytes())

    def test_cross_root_capability_rejected(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root_a = self.make_project()
        root_b = self.make_project()
        cap = gg_lock.Lock(root_a)
        cap.__enter__()
        try:
            with self.assertRaises(ValueError):
                core._apply_locked(
                    root_b, {"request_id": "f", "ops": []}, 0,
                    capability=cap)
        finally:
            cap.__exit__(None, None, None)

    def test_released_capability_rejected(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        cap = gg_lock.Lock(root)
        cap.__enter__()
        cap.__exit__(None, None, None)
        with self.assertRaises(ValueError):
            core._apply_locked(
                root, {"request_id": "f", "ops": []}, 0, capability=cap)

    def test_adoption_context_registry_is_internal(self):
        # An arbitrary dict must never satisfy the adoption argument —
        # only A-registered contexts from the same acquisition count.
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        before = (root / "project.json").read_bytes()
        cap = gg_lock.Lock(root)
        cap.__enter__()
        try:
            with self.assertRaises(ValueError):
                core._apply_locked(
                    root,
                    {"request_id": "fake",
                     "ops": [{"collection": "outputs", "value": {
                         "id": "x", "path": "x.md", "format": "md",
                         "file_hash": "0" * 64, "target_refs": [],
                         "input_fingerprint": "0" * 64}}]},
                    0,
                    capability=cap,
                    adoption={"publication_ref": {"fake": True}})
        finally:
            cap.__exit__(None, None, None)
        self.assertEqual(before, (root / "project.json").read_bytes())


class StagingCleanupTests(ContractCase):
    def test_export_leaves_no_staging_residue(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        core.export(root, "review")
        self.assertEqual([], _residue(root))

    def test_paper_leaves_no_staging_residue(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        spec = _spec(root)
        core.paper(root, "paper-spec.json", spec.read_bytes(),
                   "build/paper-out.md")
        self.assertEqual([], _residue(root))

    def test_failed_mutator_leaves_no_residue(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        spec = _spec(root)
        with self.assertRaises(core.OperationError):
            # invalid spec bytes fail before any staging write
            core.paper(root, "paper-spec.json", b"{not json",
                       "build/paper-out.md")
        with self.assertRaises(core.OperationError):
            core.apply(
                root,
                {"request_id": "bad",
                 "ops": [{"collection": "facts", "value": {"id": "x"}}]},
                core.load(root)["revision"])
        self.assertEqual([], _residue(root))

    def test_import_staging_temp_removed(self):
        import tempfile
        core = runtime("gg_core")
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-p2imp-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        (src / "sections").mkdir(parents=True)
        (src / "sections" / "01-a.md").write_text("# a\nx\n",
                                                encoding="utf-8")
        dest = Path(tmp.name) / "dest"
        core.migrate(src, dest, offline_confirmed=True)
        leftovers = [
            p.name for p in Path(tmp.name).iterdir()
            if p.name.startswith(".gg-import-")]
        self.assertEqual([], leftovers)


class ManagedPreservedTests(ContractCase):
    def test_paper_requested_blocked_preserves_managed_bundle(self):
        # publish_directory commits the managed bundle; when the
        # requested path can never be created (parent is a regular file)
        # the result is committed_cleanup_pending and the bundle + the
        # user's foreign object are both preserved — never deleted.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        spec = _spec(root)
        (root / "build").mkdir(exist_ok=True)
        (root / "build" / "blocked").write_text("foreign file")
        with self.assertRaises(core.OperationError) as cm:
            core.paper(
                root, "paper-spec.json", spec.read_bytes(),
                "build/blocked/paper.md")
        result = cm.exception.result
        self.assertEqual("committed_cleanup_pending",
                         result["commit_state"])
        self.assertEqual("requested_path_blocked", result["reason"])
        rev = core.load(root)["revision"]
        managed = root / "build" / str(rev) / "paper" / "paper.md"
        self.assertTrue(managed.is_file())
        self.assertTrue(
            (root / "build" / str(rev) / "paper"
             / ".publication.json").is_file())
        self.assertEqual("foreign file",
                         (root / "build" / "blocked").read_text())
        # Resume must not republish the bundle — only the requested path.
        (root / "build" / "blocked").unlink()
        (root / "build" / "blocked").mkdir()
        resumed = core.paper(
            root, "paper-spec.json", spec.read_bytes(),
            "build/blocked/paper.md")
        self.assertEqual("generated", resumed["status"])
        self.assertTrue((root / "build" / "blocked" / "paper.md").is_file())


class CommitStateEvidenceTests(ContractCase):
    def test_unconfirmed_release_is_indeterminate_not_cleanup_pending(self):
        # LockCleanupError with release_confirmed=False cannot prove the
        # release — even a verified canonical commit downgrades to
        # indeterminate, never a self-assured 'committed'.
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        write_text(root, "answer.txt", "x\n")
        change = {
            "request_id": "rel-fail",
            "ops": [{"collection": "sources", "value": {
                "id": "s", "path": "answer.txt",
                "claims": {}, "claim_review": "x"}}],
        }

        real_lock = gg_lock.Lock

        class ReleasingFails(real_lock):
            def __exit__(self, *exc):
                raise gg_lock.LockCleanupError(
                    [RuntimeError("synthetic release failure")],
                    release_confirmed=False,
                    prior_error=None)

        original = gg_lock.Lock
        gg_lock.Lock = ReleasingFails
        try:
            with self.assertRaises(core.OperationError) as cm:
                core.apply(root, change, 0)
        finally:
            gg_lock.Lock = original
        self.assertEqual("indeterminate",
                         cm.exception.result["commit_state"])
        self.assertTrue(cm.exception.result["cleanup_errors"])

    def test_apply_return_preserves_p1_shape(self):
        # The normal return keeps the project dict — callers on the P1
        # contract do not see a new envelope.
        core = runtime("gg_core")
        root = self.make_project()
        p = _seed(root)
        self.assertEqual(1, p["revision"])
        self.assertIn("requests", p)


if __name__ == "__main__":
    unittest.main()
