"""P2 lane A tests — core/CLI contract (API §1-§10, SPEC §3-§8).

Covers the A-owned acceptance items: commit-state/exit mapping, init and
ledger rules, adopt_output as the sole output-registration path, export/
paper/import through the receipt namespace, observation v2, and the
output_publication check gate.  Synthetics only.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests._harness import (
    ContractCase,
    SCRIPTS,
    bind_major,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    write_text,
)


def _env():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _cli(root, *args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "gg.py"), *args],
        capture_output=True, text=True, env=_env(), cwd=cwd or root)


def _seed(root):
    """Minimal project: one source+fact+section triple at revision 1."""
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


def _output_value(core, root, path, fmt, data, p=None):
    p = p or core.load(root)
    refs = core.sort_target_refs(core._export_refs(p))
    return {
        "id": Path(path).stem,
        "path": path,
        "format": fmt,
        "file_hash": core.digest(data),
        "target_refs": refs,
        "input_fingerprint": core.fingerprint(root, p, refs),
    }


class CommitStateCliTests(ContractCase):
    """API §1: commit_state -> exit code mapping must never collapse."""

    def test_not_committed_maps_exit2(self):
        root = self.make_project()
        proc = _cli(
            root, "apply", str(root),
            "--change", "missing.json", "--expected-revision", "0")
        self.assertEqual(2, proc.returncode, proc.stderr + proc.stdout)

    def test_cli_apply_exit2_keeps_structured_reason(self):
        core = runtime("gg_core")
        root = self.make_project()
        change = root / "change.json"
        change.write_text(json.dumps(
            {"request_id": "x", "ops": []}), encoding="utf-8")
        proc = _cli(
            root, "apply", str(root),
            "--change", "change.json", "--expected-revision", "9")
        self.assertEqual(2, proc.returncode, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual("revision_conflict", payload["reason"])
        self.assertEqual("not_committed", payload["commit_state"])

    def test_generic_cli_error_never_fakes_cleanup_states(self):
        # A plain failure (unreadable change file) is exit 2 — it must not
        # be presented as committed_cleanup_pending (3) or indeterminate(4).
        root = self.make_project()
        proc = _cli(
            root, "apply", str(root),
            "--change", "no/such/change.json", "--expected-revision", "0")
        self.assertEqual(2, proc.returncode)
        self.assertNotIn(proc.returncode, (3, 4))


class InitContractTests(ContractCase):
    def test_existing_canonical_never_overwritten(self):
        core = runtime("gg_core")
        root = self.make_project()
        before = (root / "project.json").read_bytes()
        with self.assertRaises(ValueError):
            core.init(root)
        self.assertEqual(before, (root / "project.json").read_bytes())

    def test_ready_v2_without_canonical_is_resumable(self):
        # SPEC §3: a workspace with only the v2 protocol installed (the
        # pre-canonical-write state) resumes init under the guard.
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        (root / "project.json").unlink()
        probe = gg_lock.probe_lock(root)
        self.assertEqual("ready", probe["structure"])
        p = core.init(root)
        self.assertEqual(0, p["revision"])
        self.assertTrue((root / "project.json").is_file())

    def test_lock_installed_before_first_canonical_write(self):
        # If the canonical commit fails, the protocol dir must already be
        # there — init never writes product bytes without the protocol.
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-p2init-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "ws"
        core.init(root)
        self.assertTrue((root / ".gg-lock" / "protocol.json").is_file())
        self.assertEqual(
            "ready", gg_lock.probe_lock(root)["structure"])


class LedgerContractTests(ContractCase):
    def test_malformed_ledger_rejects_before_write(self):
        # API §8: a corrupt requests entry is malformed_request_ledger,
        # never 'a different request' — checked before any product write.
        core = runtime("gg_core")
        root = self.make_project()
        p = core.load(root)
        p["requests"]["corrupt"] = {"hash": 12345}
        (root / "project.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        before = (root / "project.json").read_bytes()
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": "r2",
                 "ops": [{"collection": "tasks", "value": {"id": "t"}}]},
                0)
        self.assertEqual("malformed_request_ledger",
                         cm.exception.result["reason"])
        self.assertEqual(before, (root / "project.json").read_bytes())

    def test_apply_requires_request_id(self):
        core = runtime("gg_core")
        root = self.make_project()
        for bad in (
            {"ops": []},
            {"request_id": "", "ops": []},
            {"request_id": 7, "ops": []},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(core.OperationError) as cm:
                    core.apply(root, bad, 0)
                self.assertIn(
                    cm.exception.result["reason"],
                    {"request_id_required", "invalid_change"})

    def test_generic_apply_rejects_receipt_namespace_collision(self):
        # A generic apply reusing a publication request_id is a conflict —
        # the canonical ledger and receipt namespace share one id space.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        value = core.export(root, "draft", major_id="specialty_crops")
        rid = value["request_id"]
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": rid,
                 "ops": [{"collection": "tasks", "value": {"id": "t"}}]},
                core.load(root)["revision"])
        self.assertEqual("request_id_conflict",
                         cm.exception.result["reason"])


class AdoptOutputContractTests(ContractCase):
    def _adopt(self, core, root, *, name="out.md", data=b"BYTES",
               fmt="md", request_id="adopt-1", companions=None,
               mutate=None):
        (root / name).write_bytes(data)
        p = core.load(root)
        ov = _output_value(core, root, name, fmt, data, p=p)
        ov["id"] = "wb1"
        if mutate:
            mutate(ov)
        return core.adopt_output(
            root, ov, p["revision"], request_id,
            companion_files=companions or [], major_id="specialty_crops")

    def test_adopt_publishes_managed_copy_and_ledger_kind(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        value = self._adopt(core, root)
        self.assertEqual("adopted", value["status"])
        managed = Path(value["path"])
        self.assertTrue(managed.is_file())
        self.assertIn(".gg-artifacts/", value["path"])
        p = core.load(root)
        entry = p["requests"]["adopt-1"]
        self.assertEqual("adopt_output", entry["kind"])
        self.assertEqual(
            set(entry), {"hash", "revision", "kind", "publication_ref"})
        rec = p["outputs"]["wb1"]
        self.assertEqual(managed.name, Path(rec["path"]).name)
        self.assertEqual(value["publication_ref"], rec["publication_ref"])
        # Original bytes are never touched by adoption.
        self.assertEqual(b"BYTES", (root / "out.md").read_bytes())

    def test_adopt_dedup_returns_existing(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "out.md").write_bytes(b"BYTES")
        p = core.load(root)
        ov = _output_value(core, root, "out.md", "md", b"BYTES", p=p)
        ov["id"] = "wb1"
        # A retry is the identical request — same output_value AND the
        # same expected_revision; anything else is a conflict by design.
        first = core.adopt_output(
            root, ov, p["revision"], "adopt-1", major_id="specialty_crops")
        second = core.adopt_output(
            root, ov, p["revision"], "adopt-1", major_id="specialty_crops")
        self.assertEqual("adopted", first["status"])
        self.assertEqual("existing", second["status"])
        self.assertEqual(first["publication_ref"],
                         second["publication_ref"])
        with self.assertRaises(core.OperationError) as cm:
            core.adopt_output(
                root, dict(ov, format="docx"), p["revision"], "adopt-1",
                major_id="specialty_crops")
        self.assertEqual("request_id_conflict",
                         cm.exception.result["reason"])

    def test_adopt_rejects_server_derived_keys(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        for key in ("publication_ref", "revision", "stale"):
            with self.subTest(key=key):
                with self.assertRaises(core.OperationError) as cm:
                    self._adopt(
                        core, root, request_id="srv-" + key,
                        mutate=lambda ov, k=key: ov.__setitem__(k, "x"))
                self.assertEqual("invalid_output_metadata",
                                 cm.exception.result["reason"])

    def test_adopt_rejects_wrong_hash_and_fingerprint(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        with self.assertRaises(core.OperationError) as cm:
            self._adopt(core, root, request_id="bad-h",
                    mutate=lambda ov: ov.__setitem__("file_hash", "0" * 64))
        self.assertEqual("output_hash_mismatch",
                         cm.exception.result["reason"])
        with self.assertRaises(core.OperationError) as cm:
            self._adopt(
                core, root, request_id="bad-fp",
                mutate=lambda ov: ov.__setitem__(
                    "input_fingerprint", "0" * 64))
        self.assertEqual("input_fingerprint_mismatch",
                         cm.exception.result["reason"])

    def test_adopt_validates_companion_bytes(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "side.md").write_text("side", encoding="utf-8")
        with self.assertRaises(core.OperationError) as cm:
            self._adopt(
                core, root, request_id="bad-comp",
                companions=[{"path": "side.md", "sha256": "0" * 64}])
        self.assertEqual("companion_mismatch",
                         cm.exception.result["reason"])
        good = self._adopt(
            core, root, request_id="good-comp",
            companions=[
                {"path": "side.md",
                 "sha256": core.digest("side".encode())}])
        self.assertEqual("adopted", good["status"])

    def test_generic_apply_cannot_register_output(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        p = core.load(root)
        refs = core.sort_target_refs(core._export_refs(p))
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": "bypass",
                 "ops": [{"collection": "outputs", "value": {
                     "id": "sneak", "path": "x.md", "format": "md",
                     "file_hash": "0" * 64, "target_refs": refs,
                     "input_fingerprint": "0" * 64}}]},
                p["revision"])
        self.assertEqual("adopt_output_required",
                         cm.exception.result["reason"])

    def test_meta_only_supersede_still_allowed(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)

        def ov_for(path, data, oid):
            # Claims over direct inputs only — an output whose refs
            # include another output goes stale when that output is
            # superseded, which is the designed behaviour.
            p = core.load(root)
            refs = core.sort_target_refs(
                [r for r in core._export_refs(p)
                 if r["collection"] != "outputs"])
            return {
                "id": oid, "path": path, "format": "md",
                "file_hash": core.digest(data), "target_refs": refs,
                "input_fingerprint": core.fingerprint(root, p, refs),
            }

        (root / "old.md").write_bytes(b"OLD")
        p = core.load(root)
        core.adopt_output(
            root, ov_for("old.md", b"OLD", "wb0"), p["revision"],
            "adopt-old", major_id="specialty_crops")
        (root / "new.md").write_bytes(b"NEW")
        p = core.load(root)
        core.adopt_output(
            root, ov_for("new.md", b"NEW", "wb1"), p["revision"],
            "adopt-new", major_id="specialty_crops")
        p = core.load(root)
        old_rec = dict(p["outputs"]["wb0"])
        old_rec["superseded_by"] = "wb1"
        p2 = core.apply(
            root,
            {"request_id": "link",
             "ops": [{"collection": "outputs", "value": old_rec}]},
            p["revision"])
        self.assertEqual("wb1", p2["outputs"]["wb0"]["superseded_by"])
        lineage = core.output_lineage(p2)
        self.assertEqual("history", lineage["wb0"]["state"])
        self.assertEqual("wb1", lineage["wb0"]["terminal"])


class ExportContractTests(ContractCase):
    def test_export_publishes_bundle_and_requested_file(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        rev = core.load(root)["revision"]
        value = core.export(root, "review", major_id="specialty_crops")
        self.assertEqual("generated", value["status"])
        requested = Path(value["path"])
        self.assertTrue(requested.is_file())
        bundle = root / "build" / str(rev) / "review"
        self.assertTrue((bundle / ".publication.json").is_file())
        self.assertIn("검토용.md", {e["path"] for e in
                                  json.loads((bundle / ".publication.json")
                                             .read_bytes())["files"]})
        # dedup: same inputs replay the publication, no second bundle.
        again = core.export(root, "review", major_id="specialty_crops")
        self.assertEqual("existing", again["status"])
        self.assertEqual(value["path"], again["path"])

    def test_export_foreign_destination_preserved(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        rev = core.load(root)["revision"]
        foreign = root / "build" / str(rev) / "review"
        foreign.mkdir(parents=True)
        (foreign / "user-file.txt").write_text("keep me")
        with self.assertRaises(core.OperationError) as cm:
            core.export(root, "review", major_id="specialty_crops")
        result = cm.exception.result
        self.assertIn(result["commit_state"],
                      ("not_committed", "indeterminate"))
        self.assertEqual(
            "keep me", (foreign / "user-file.txt").read_text())

    def test_export_kind_gate_blocks_submission_candidate(self):
        # Submission candidates require the gate to pass — an empty
        # project is not submittable.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        with self.assertRaises(core.OperationError) as cm:
            core.export(
                root, "submission_candidate", major_id="specialty_crops")
        self.assertEqual("not_committed",
                         cm.exception.result["commit_state"])


class PaperContractTests(ContractCase):
    def _spec(self, root):
        spec = root / "paper-spec.json"
        spec.write_text(json.dumps(
            {"author": "합성", "writing_year": 2026, "years": 5,
             "school_profile": {"mode": "school", "school": "합성대학교",
                                "department": "특용작물학과"}},
            ensure_ascii=False), encoding="utf-8")
        return spec

    def test_paper_publishes_requested_and_managed(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        spec = self._spec(root)
        value = core.paper(
            root, "paper-spec.json", spec.read_bytes(),
            "build/paper-out.md", major_id="specialty_crops")
        self.assertEqual("generated", value["status"])
        self.assertTrue((root / "build/paper-out.md").is_file())
        rev = core.load(root)["revision"]
        self.assertTrue(
            (root / "build" / str(rev) / "paper" / "paper.md").is_file())
        # identical request replays: no second bundle, same status path.
        again = core.paper(
            root, "paper-spec.json", spec.read_bytes(),
            "build/paper-out.md", major_id="specialty_crops")
        self.assertEqual("existing", again["status"])

    def test_paper_resume_republishes_only_requested(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        spec = self._spec(root)
        value = core.paper(
            root, "paper-spec.json", spec.read_bytes(),
            "build/paper-out.md", major_id="specialty_crops")
        (root / "build/paper-out.md").unlink()
        resumed = core.paper(
            root, "paper-spec.json", spec.read_bytes(),
            "build/paper-out.md", major_id="specialty_crops")
        # The managed bundle is replayed, never republished; the missing
        # requested file is regenerated from it in this call.
        self.assertEqual("generated", resumed["status"])
        self.assertTrue((root / "build/paper-out.md").is_file())
        self.assertEqual(value["request_id"], resumed["request_id"])


class ImportContractTests(ContractCase):
    def _legacy(self, prefix="knuaf-p2src-"):
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        (src / "sections").mkdir(parents=True)
        (src / "sections" / "01-개요.md").write_text(
            "# 개요\n본문\n", encoding="utf-8")
        return src, Path(tmp.name) / "dest"

    def test_import_requires_offline_confirmation_without_protocol(self):
        core = runtime("gg_core")
        src, dest = self._legacy()
        with self.assertRaises(core.OperationError) as cm:
            core.migrate(src, dest)
        self.assertEqual("offline_confirmation_required",
                         cm.exception.result["reason"])
        self.assertFalse(dest.exists())

    def test_import_publishes_workspace_and_retries_identically(self):
        core = runtime("gg_core")
        src, dest = self._legacy()
        p = core.migrate(src, dest, offline_confirmed=True)
        self.assertTrue((dest / "project.json").is_file())
        self.assertTrue((dest / ".gg-lock" / "protocol.json").is_file())
        self.assertTrue(
            (dest / ".gg-import-publication.json").is_file())
        # Source keeps a stub receipt; destination never overwritten.
        self.assertTrue(
            (src / ".gg-import-publication.json").is_file())
        again = core.migrate(src, dest, offline_confirmed=True)
        self.assertEqual(p["revision"], again["revision"])
        # Foreign destination is preserved, never repaired or emptied.
        foreign = Path(src.parent) / "foreign"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("keep")
        with self.assertRaises(core.OperationError) as cm:
            core.migrate(src, foreign, offline_confirmed=True)
        self.assertIn(cm.exception.result["reason"],
                      ("import_destination_conflict",
                       "import_receipt_conflict"))
        self.assertEqual("keep", (foreign / "keep.txt").read_text())

    def test_import_refuses_nested_and_new_format_sources(self):
        core = runtime("gg_core")
        src, dest = self._legacy()
        nested = src / "inner"
        with self.assertRaises(core.OperationError) as cm:
            core.migrate(src, nested, offline_confirmed=True)
        self.assertEqual("nested_workspaces",
                         cm.exception.result["reason"])
        root = self.make_project()
        with self.assertRaises(core.OperationError) as cm:
            core.migrate(root, dest, offline_confirmed=True)
        self.assertEqual("already_new_format",
                         cm.exception.result["reason"])


class ObservationContractTests(ContractCase):
    def _observation(self, core, root):
        (root / "report.md").write_text("report", encoding="utf-8")
        p = core.load(root)
        refs = core.sort_target_refs(core._export_refs(p))
        return {
            "author_session": "s-author",
            "reviewer_session": "s-reviewer",
            "author_id": "author",
            "reviewer_id": "reviewer",
            "review_kinds": ["사실성"],
            "target_refs": refs,
            "input_fingerprint": core.fingerprint(root, p, refs),
            "report_path": "report.md",
            "report_hash": core.digest(b"report"),
            "input_revision": p["revision"],
        }

    def test_observation_v2_published_immutably(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        obs = self._observation(core, root)
        value = core.ingest_review_observation(root, obs, "observer-1")
        stored = Path(root) / value["path"]
        self.assertTrue(stored.is_file())
        self.assertIn(".gg-observations", value["path"])
        record = json.loads(stored.read_text(encoding="utf-8"))
        self.assertEqual("gg-review-observation/2", record["schema"])
        self.assertEqual("observer-1", record["observed_by"])
        for key in ("workspace_id", "protocol_sha256", "report_path",
                    "report_hash", "input_revision"):
            self.assertIn(key, record)
        # identical observation is idempotent, same path.
        again = core.ingest_review_observation(root, obs, "observer-1")
        self.assertEqual(value["path"], again["path"])

    def test_observation_rejects_unknown_and_party_keys(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        obs = self._observation(core, root)
        obs["workspace_id"] = "forged"
        with self.assertRaises(core.OperationError) as cm:
            core.ingest_review_observation(root, obs, "observer-1")
        self.assertEqual("observation_unknown_keys",
                         cm.exception.result["reason"])
        obs = self._observation(core, root)
        with self.assertRaises(core.OperationError) as cm:
            core.ingest_review_observation(root, obs, "s-author")
        self.assertEqual("observer_is_party",
                         cm.exception.result["reason"])
        obs["reviewer_id"] = "author"
        with self.assertRaises(core.OperationError) as cm:
            core.ingest_review_observation(root, obs, "observer-1")
        self.assertEqual("self_review_observation",
                         cm.exception.result["reason"])

    def test_observation_rejection_creates_no_receipt_dir(self):
        # P10: a rejected observation must not even create
        # .gg-observations — and canonical revision stays put.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        revision = core.load(root)["revision"]
        obs = self._observation(core, root)
        obs["input_revision"] = revision + 5
        with self.assertRaises(core.OperationError):
            core.ingest_review_observation(root, obs, "observer-1")
        self.assertFalse((root / ".gg-observations").exists())
        self.assertEqual(revision, core.load(root)["revision"])

    def _bound_observation(self, core, root, report_rel,
                           review_kinds=("content",)):
        """Observation input whose review_kinds covers the bound
        review's kind (the shared helper pins '사실성')."""
        obs = self._observation(core, root)
        obs["report_path"] = report_rel
        obs["report_hash"] = core.digest(
            (root / report_rel).read_bytes())
        obs["review_kinds"] = list(review_kinds)
        return obs

    def _bound_review(self, core, root, receipt, report_rel,
                      input_revision="omit"):
        """A schema2-bound review value wired to a real observation
        receipt — the shape the public apply flow stores."""
        p = core.load(root)
        refs = core.sort_target_refs(core._export_refs(p))
        value = {
            "id": "rev-content",
            "review_kind": "content",
            "author_id": "author",
            "reviewer_id": "reviewer",
            "target_refs": refs,
            "input_fingerprint": core.fingerprint(root, p, refs),
            "findings": [],
            "disposition": "resolved",
            "status": "pass",
            "path": report_rel,
            "coverage": {"intro": ["농장명은 행복농장이다"]},
            "provenance_path": receipt["path"],
            "provenance_hash": receipt["hash"],
        }
        if input_revision != "omit":
            value["input_revision"] = input_revision
        return value

    def _apply_review(self, core, root, value, request_id="bind-review"):
        p = core.load(root)
        return core.apply(
            root,
            {"request_id": request_id,
             "ops": [{"collection": "reviews", "value": value}]},
            p["revision"],
        )

    def _assert_canonical_untouched(self, core, root, before_bytes, before_rev):
        self.assertEqual(before_bytes,
                         (root / "project.json").read_bytes())
        p = core.load(root)
        self.assertEqual(before_rev, p["revision"])
        self.assertNotIn("rev-content", p["reviews"])
        self.assertNotIn("bind-review", p["requests"])

    def test_bound_review_observed_revision_lands(self):
        # API110/146/148/159: observe at R -> apply bound review carrying
        # input_revision R -> stored review keeps R while the canonical
        # commit is R+1 -> schema2 verification is valid -> gate counts it.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        obs = self._bound_observation(core, root, "report.md")
        receipt = core.ingest_review_observation(root, obs, "observer-1")
        R = core.load(root)["revision"]
        value = self._bound_review(core, root, receipt, "report.md",
                                   input_revision=R)
        p2 = self._apply_review(core, root, value)
        self.assertEqual(R + 1, p2["revision"])
        stored = p2["reviews"]["rev-content"]
        self.assertEqual(R, stored["input_revision"])
        self.assertEqual(receipt["hash"], stored["provenance_hash"])
        state, reason = core.review_observation_state(root, stored)
        self.assertEqual("valid", state, reason)
        blocked = {c["check_id"] for c in core.gate(root, p2)}
        self.assertNotIn("review_content", blocked)
        self.assertNotIn("review_observation", blocked)

    def test_bound_review_requires_exact_revision(self):
        # Missing/bool/string/registration-revision/stale input_revision
        # must all fail before canonical/history/ledger writes.
        core = runtime("gg_core")
        for supplied in ("omit", True, "1", "future", "stale", "rplus1"):
            with self.subTest(supplied=supplied):
                root = self.make_project()
                _seed(root)
                (root / "report.md").write_text("report", encoding="utf-8")
                receipt = core.ingest_review_observation(
                    root, self._bound_observation(core, root, "report.md"), "observer-1")
                R = core.load(root)["revision"]
                revision = {"future": R + 9, "stale": R - 1,
                            "rplus1": R + 1}.get(supplied, supplied)
                value = self._bound_review(
                    core, root, receipt, "report.md",
                    input_revision=revision)
                before = (root / "project.json").read_bytes()
                with self.assertRaises(core.OperationError) as cm:
                    self._apply_review(core, root, value)
                self.assertEqual("not_committed",
                                 cm.exception.result["commit_state"])
                self.assertEqual("invalid_change",
                                 cm.exception.result["reason"])
                self._assert_canonical_untouched(core, root, before, R)

    def test_bound_review_rejects_same_byte_other_path(self):
        # API148: identical bytes at a different relative path are not the
        # observed report — rejected at commit and at verification.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        # The observation helper writes report.md itself; the copy gets
        # the same bytes so only the path differs.
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"),
            "observer-1")
        write_text(root, "report-copy.md",
                   (root / "report.md").read_text(encoding="utf-8"))
        R = core.load(root)["revision"]
        before = (root / "project.json").read_bytes()
        wrong_path = self._bound_review(
            core, root, receipt, "report-copy.md", input_revision=R)
        with self.assertRaises(core.OperationError) as cm:
            self._apply_review(core, root, wrong_path)
        self.assertEqual("not_committed",
                         cm.exception.result["commit_state"])
        self._assert_canonical_untouched(core, root, before, R)
        # Verification layer independently rejects the same mismatch for a
        # review record that somehow carries the wrong path claim.
        wrong_path["report_hash"] = core.digest(
            (root / "report-copy.md").read_bytes())
        state, reason = core.review_observation_state(root, wrong_path)
        self.assertEqual("invalid", state)
        self.assertIn("경로", reason)

    def test_bound_review_rejects_broken_provenance(self):
        # Missing/forged provenance cannot borrow a real receipt.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"), "observer-1")
        R = core.load(root)["revision"]
        before = (root / "project.json").read_bytes()
        variants = []
        no_hash = self._bound_review(core, root, receipt, "report.md",
                                     input_revision=R)
        del no_hash["provenance_hash"]
        variants.append(no_hash)
        wrong_hash = self._bound_review(core, root, receipt, "report.md",
                                        input_revision=R)
        wrong_hash["provenance_hash"] = "0" * 64
        variants.append(wrong_hash)
        outside = self._bound_review(core, root, receipt, "report.md",
                                     input_revision=R)
        outside["provenance_path"] = "report.md"
        variants.append(outside)
        for value in variants:
            with self.assertRaises(core.OperationError) as cm:
                self._apply_review(core, root, value)
            self.assertEqual("not_committed",
                             cm.exception.result["commit_state"])
        self._assert_canonical_untouched(core, root, before, R)

    def test_bound_review_rejects_field_forgery(self):
        # A receipt for a different author/refs/fingerprint cannot be
        # borrowed by editing the review fields.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"), "observer-1")
        R = core.load(root)["revision"]
        before = (root / "project.json").read_bytes()
        bad_author = self._bound_review(core, root, receipt, "report.md",
                                        input_revision=R)
        bad_author["author_id"] = "mallory"
        bad_refs = self._bound_review(core, root, receipt, "report.md",
                                      input_revision=R)
        bad_refs["target_refs"] = [
            {"collection": "sections", "id": "intro"}]
        bad_refs["input_fingerprint"] = core.fingerprint(
            root, core.load(root), bad_refs["target_refs"])
        for value in (bad_author, bad_refs):
            with self.assertRaises(core.OperationError) as cm:
                self._apply_review(core, root, value)
            self.assertEqual("not_committed",
                             cm.exception.result["commit_state"])
        self._assert_canonical_untouched(core, root, before, R)

    def test_bound_review_replay_and_later_revision(self):
        # Idempotent replay is byte-stable; a later legitimate revision
        # does not rewrite the past observation receipt.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"), "observer-1")
        R = core.load(root)["revision"]
        value = self._bound_review(core, root, receipt, "report.md",
                                   input_revision=R)
        p2 = self._apply_review(core, root, value)
        obs_bytes = (root / receipt["path"]).read_bytes()
        canon_after = (root / "project.json").read_bytes()
        again = self._apply_review(core, root, value)
        self.assertEqual(p2["revision"], again["revision"])
        self.assertEqual(canon_after, (root / "project.json").read_bytes())
        # Later unrelated revision: receipt bytes and validity preserved.
        write_text(root, "extra.txt", "extra\n")
        core.apply(
            root,
            {"request_id": "later",
             "ops": [{"collection": "sources", "value": {
                 "id": "extra", "path": "extra.txt",
                 "claims": {}, "claim_review": "x"}}]},
            p2["revision"])
        p3 = core.load(root)
        self.assertEqual(R + 2, p3["revision"])
        self.assertEqual(obs_bytes, (root / receipt["path"]).read_bytes())
        state, _ = core.review_observation_state(
            root, p3["reviews"]["rev-content"])
        self.assertEqual("valid", state)

    def test_unobserved_review_keeps_legacy_convention(self):
        # Schema-1/unobserved semantics preserved: a review without
        # provenance still gets the registration revision, never bound.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        p = core.load(root)
        R = p["revision"]
        refs = core.sort_target_refs(core._export_refs(p))
        value = {
            "id": "rev-content",
            "review_kind": "content",
            "author_id": "author",
            "reviewer_id": "reviewer",
            "target_refs": refs,
            "input_fingerprint": core.fingerprint(root, p, refs),
            "findings": [],
            "disposition": "resolved",
            "status": "pass",
            "path": "report.md",
            "coverage": {"intro": ["농장명은 행복농장이다"]},
        }
        p2 = self._apply_review(core, root, value)
        stored = p2["reviews"]["rev-content"]
        self.assertEqual(R + 1, stored["input_revision"])
        self.assertEqual(
            "legacy_unobserved",
            core.review_observation_state(root, stored)[0])

    def test_same_request_target_mutation_cannot_bypass(self):
        # Mutating a reviewed target inside the same request must not
        # re-bind the review to the mutated input: reject with canonical
        # bytes/history/ledger untouched.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"), "observer-1")
        R = core.load(root)["revision"]
        before = (root / "project.json").read_bytes()
        review = self._bound_review(core, root, receipt, "report.md",
                                    input_revision=R)
        write_text(root, "answer.txt", "농장명: 다른농장\n")
        mutation = {"collection": "sources", "value": {
            "id": "answer", "path": "answer.txt",
            "claims": {"c1": {
                "field_id": "farm_name", "value": "다른농장", "unit": None,
                "period": None, "scope": "farm",
                "answer_state": "provided", "kind": "reported_fact"}},
            "claim_review": "synthetic-review"}}
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": "bind-review",
                 "ops": [mutation,
                         {"collection": "reviews", "value": review}]},
                R)
        self.assertEqual("not_committed",
                         cm.exception.result["commit_state"])
        self._assert_canonical_untouched(core, root, before, R)

    def test_cross_workspace_receipt_rejected_at_apply(self):
        # P2-R7 defect closure: a genuine observation receipt produced in
        # workspace A must not be borrowable by copying its immutable
        # bytes into workspace B's own .gg-observations store and then
        # applying a bound review there — apply-time must check
        # workspace_id/protocol_sha256 BEFORE any canonical write, not
        # only in the post-hoc verifier. Canonical/history/ledger stay
        # byte-unchanged on rejection.
        core = runtime("gg_core")
        root_a = self.make_project()
        _seed(root_a)
        root_b = self.make_project()
        _seed(root_b)
        (root_a / "report.md").write_text("report", encoding="utf-8")
        (root_b / "report.md").write_text("report", encoding="utf-8")
        receipt_a = core.ingest_review_observation(
            root_a, self._bound_observation(core, root_a, "report.md"),
            "observer-1")
        obs_bytes = (root_a / receipt_a["path"]).read_bytes()
        dest = root_b / receipt_a["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(obs_bytes)
        self.assertNotEqual(
            json.loads(obs_bytes)["workspace_id"],
            json.loads((root_b / ".gg-lock" / "protocol.json")
                       .read_bytes())["workspace_id"])
        R = core.load(root_b)["revision"]
        before = (root_b / "project.json").read_bytes()
        # Snapshot the full on-disk tree (history/migration snapshots
        # included, not just canonical project.json) so rejection is
        # proven to leave every persisted artifact byte-unchanged, per
        # the original P2-R7 finding's history-invariance requirement.
        tree_before = self._tree_bytes(root_b)
        value = self._bound_review(core, root_b, receipt_a, "report.md",
                                   input_revision=R)
        with self.assertRaises(core.OperationError) as cm:
            self._apply_review(core, root_b, value)
        self.assertEqual("not_committed",
                         cm.exception.result["commit_state"])
        self.assertEqual("invalid_change",
                         cm.exception.result["reason"])
        self._assert_canonical_untouched(core, root_b, before, R)
        self.assertEqual(tree_before, self._tree_bytes(root_b))
        # Post-hoc verifier independently agrees on a review record that
        # somehow carries the foreign-workspace receipt binding.
        stored_shape = dict(value)
        state, reason = core.review_observation_state(root_b, stored_shape)
        self.assertEqual("invalid", state)
        self.assertIn("바인딩", reason)

    def test_same_workspace_receipt_still_applies(self):
        # Positive control for the binding check above: an observation
        # receipt applied in ITS OWN workspace (the ordinary case) must
        # keep working exactly as before.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"),
            "observer-1")
        R = core.load(root)["revision"]
        value = self._bound_review(core, root, receipt, "report.md",
                                   input_revision=R)
        p2 = self._apply_review(core, root, value)
        self.assertEqual(R + 1, p2["revision"])
        state, reason = core.review_observation_state(
            root, p2["reviews"]["rev-content"])
        self.assertEqual("valid", state, reason)

    def test_trusted_recovery_lineage_receipt_still_applies(self):
        # A receipt whose recorded protocol endpoint is the PRIOR endpoint
        # of a trusted same_workspace_repair recovery chain must still be
        # accepted — recovery is a legitimate, contract-defined path from
        # an old endpoint to the current one, distinct from copying a
        # receipt into an unrelated foreign workspace.
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        _seed(root)
        (root / "report.md").write_text("report", encoding="utf-8")
        receipt = core.ingest_review_observation(
            root, self._bound_observation(core, root, "report.md"),
            "observer-1")
        # Force an offline recovery that installs a NEW protocol
        # while preserving same_workspace_repair lineage back to the old
        # endpoint the receipt above was minted under. Damage the guard
        # (same trigger as test_p2_recovery.py) so the control is
        # actually eligible for repair rather than already_installed.
        gg_fs = runtime("gg_fs")
        guard = root / ".gg-lock" / "guard"
        old_guard_id = gg_fs.identity(guard, kind="file")
        replacement = guard.with_name("guard-replacement")
        # Allocate before replacing so the old inode cannot be reused.
        replacement.write_bytes(b"X")
        os.replace(replacement, guard)
        self.assertFalse(gg_fs.same_identity(
            old_guard_id, gg_fs.identity(guard, kind="file")))
        self.assertEqual("guard_replaced",
                         gg_lock.inspect_lock(root)["reason"])
        result = gg_lock.upgrade_offline(root, offline_confirmed=True)
        self.assertEqual("completed", result["status"])
        R = core.load(root)["revision"]
        value = self._bound_review(core, root, receipt, "report.md",
                                   input_revision=R)
        p2 = self._apply_review(core, root, value)
        self.assertEqual(R + 1, p2["revision"])
        state, reason = core.review_observation_state(
            root, p2["reviews"]["rev-content"])
        self.assertEqual("valid", state, reason)


class SchemaOneInvalidTests(ContractCase):
    """L11-invalid: on schema-1 input every invalid path — CAS, op shape,
    unknown collection — must leave canonical bytes, revision, requests,
    history and the migration inventory untouched (S01 preflight covers
    IDs; the rest of validation must be just as side-effect free)."""

    def _schema1_project(self):
        root = self.make_project()
        pj = root / "project.json"
        p = json.loads(pj.read_text(encoding="utf-8"))
        p["schema_version"] = 1
        pj.write_text(json.dumps(p, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        return root

    def test_schema1_bad_cas_leaves_canonical_untouched(self):
        core = runtime("gg_core")
        root = self._schema1_project()
        before = (root / "project.json").read_bytes()
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": "r",
                 "ops": [{"collection": "tasks",
                          "value": {"id": "t"}}]},
                99)
        self.assertEqual("revision_conflict",
                         cm.exception.result["reason"])
        self.assertEqual(before, (root / "project.json").read_bytes())
        self.assertEqual(1, core.load(root)["schema_version"])

    def test_schema1_invalid_ops_leave_canonical_untouched(self):
        core = runtime("gg_core")
        for change in (
            {"request_id": "r",
             "ops": [{"collection": "bogus_collection",
                      "value": {"id": "x"}}]},
            {"request_id": "r", "ops": "not-a-list"},
            {"request_id": "r",
             "ops": [{"collection": "facts", "value": "not-a-dict"}]},
        ):
            with self.subTest(change=change):
                root = self._schema1_project()
                before = (root / "project.json").read_bytes()
                with self.assertRaises(Exception):
                    core.apply(root, change, 0)
                self.assertEqual(before,
                                 (root / "project.json").read_bytes())
                self.assertEqual(1, core.load(root)["schema_version"])


class CrossKindCollisionTests(ContractCase):
    """P17: the canonical ledger and the receipt namespace share one
    request-id space — same id across kinds is always a conflict."""

    def test_adopt_rejects_apply_request_id(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "out.md").write_bytes(b"OUT")
        p = core.load(root)
        ov = _output_value(core, root, "out.md", "md", b"OUT", p=p)
        with self.assertRaises(core.OperationError) as cm:
            core.adopt_output(
                root, ov, p["revision"], "seed",
                major_id="specialty_crops")
        self.assertEqual("request_id_conflict",
                         cm.exception.result["reason"])

    def test_apply_rejects_adopt_ledger_entry(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "out.md").write_bytes(b"OUT")
        p = core.load(root)
        ov = _output_value(core, root, "out.md", "md", b"OUT", p=p)
        core.adopt_output(
            root, ov, p["revision"], "took-this-id",
            major_id="specialty_crops")
        with self.assertRaises(core.OperationError) as cm:
            core.apply(
                root,
                {"request_id": "took-this-id",
                 "ops": [{"collection": "tasks",
                          "value": {"id": "t2"}}]},
                core.load(root)["revision"])
        self.assertEqual("request_id_conflict",
                         cm.exception.result["reason"])

    def test_adopt_rejects_export_receipt_id(self):
        # A caller-chosen adopt request_id colliding with a committed
        # export receipt is a cross-namespace conflict.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        export_id = core.export(
            root, "draft", major_id="specialty_crops")["request_id"]
        (root / "out.md").write_bytes(b"OUT")
        p = core.load(root)
        ov = _output_value(core, root, "out.md", "md", b"OUT", p=p)
        with self.assertRaises(core.OperationError) as cm:
            core.adopt_output(
                root, ov, p["revision"], export_id,
                major_id="specialty_crops")
        self.assertEqual("request_id_conflict",
                         cm.exception.result["reason"])

    def test_ledger_kind_never_backfilled(self):
        # Old two-key apply entries stay two-key; the adopt entry sits
        # beside them with its own kind — no rewriting of history.
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "out.md").write_bytes(b"OUT")
        p = core.load(root)
        ov = _output_value(core, root, "out.md", "md", b"OUT", p=p)
        core.adopt_output(
            root, ov, p["revision"], "adopt-x", major_id="specialty_crops")
        p2 = core.load(root)
        self.assertEqual({"hash", "revision"}, set(p2["requests"]["seed"]))
        self.assertEqual(
            {"hash", "revision", "kind", "publication_ref"},
            set(p2["requests"]["adopt-x"]))


class TargetRefsOracleTests(ContractCase):
    """P17 detail oracles (API §9): colon IDs, NFC collision, collection
    membership and ledger malformedness."""

    def test_colon_id_and_collection_boundaries(self):
        core = runtime("gg_core")
        refs = core.sort_target_refs(
            [{"collection": "facts", "id": "price:purchase"},
             {"collection": "facts", "id": "price"}])
        self.assertEqual(
            [{"collection": "facts", "id": "price"},
             {"collection": "facts", "id": "price:purchase"}], refs)
        # Same id under different collections is a different ref.
        refs = core.sort_target_refs(
            [{"collection": "facts", "id": "x"},
             {"collection": "sources", "id": "x"}])
        self.assertEqual(2, len(refs))
        # "facts:x" is not a collection — never split a colon id.
        with self.assertRaises(ValueError):
            core.sort_target_refs([{"collection": "facts:x", "id": "y"}])
        # String refs and extra keys are rejected, not normalized.
        with self.assertRaises(ValueError):
            core.sort_target_refs(["facts:price"])
        with self.assertRaises(ValueError):
            core.sort_target_refs(
                [{"collection": "facts", "id": "a", "extra": 1}])

    def test_nfc_nfd_duplicate_ref_rejected(self):
        core = runtime("gg_core")
        with self.assertRaises(ValueError):
            core.sort_target_refs(
                [{"collection": "facts", "id": "가"},
                 {"collection": "facts",
                  "id": "가"}])  # NFD of the same syllable
        # An exact duplicate is rejected too.
        with self.assertRaises(ValueError):
            core.sort_target_refs(
                [{"collection": "facts", "id": "a"},
                 {"collection": "facts", "id": "a"}])

    def test_malformed_ledger_variants_rejected(self):
        core = runtime("gg_core")
        bad_entries = {
            "null_kind": {"hash": "0" * 64, "revision": 1, "kind": None,
                          "publication_ref": {}},
            "unknown_kind": {"hash": "0" * 64, "revision": 1,
                             "kind": "export", "publication_ref": {}},
            "extra_key": {"hash": "0" * 64, "revision": 1, "x": 1},
            "bool_revision": {"hash": "0" * 64, "revision": True},
            "negative_revision": {"hash": "0" * 64, "revision": -1},
            "future_revision": {"hash": "0" * 64, "revision": 99},
            "bad_sha": {"hash": "zz", "revision": 1},
            "missing_revision": {"hash": "0" * 64},
        }
        for name, entry in bad_entries.items():
            with self.subTest(entry=name):
                root = self.make_project()
                _seed(root)
                p = core.load(root)
                p["requests"]["bad"] = entry
                (root / "project.json").write_text(
                    json.dumps(p, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                before = (root / "project.json").read_bytes()
                with self.assertRaises(core.OperationError) as cm:
                    core.apply(
                        root,
                        {"request_id": "r2",
                         "ops": [{"collection": "tasks",
                                  "value": {"id": "t"}}]},
                        p["revision"])
                self.assertEqual("malformed_request_ledger",
                                 cm.exception.result["reason"])
                self.assertEqual(before,
                                 (root / "project.json").read_bytes())


class ReadOnlyAndDoctorTests(ContractCase):
    """L07-readonly + L11-doctor: read paths change no bytes and never
    grab the guard; doctor probing is explicit and observation-only."""

    def _tree_snapshot(self, root):
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    def test_status_check_doctor_make_no_writes(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        before = self._tree_snapshot(root)
        p = core.load(root)
        core.checks(root, p)
        core.gate(root, p)
        core.next_tasks(p)
        core.completion(root, p, core.gate(root, p))
        core.question(p, "crop")
        proc = _cli(root, "doctor", str(root))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(before, self._tree_snapshot(root))

    def test_doctor_probe_is_explicit_and_observation_only(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        # Default inspect: no acquire/release cycle is performed.
        st = gg_lock.inspect_lock(root)
        self.assertEqual("ready", st["structure"])
        # Explicit probe: one acquire/release, reported as observation.
        probed = gg_lock.probe_lock(root)
        self.assertEqual("free", probed["lock_state"])
        self.assertEqual("observation_only_not_a_write_permit",
                         probed["reason"])
        proc = _cli(root, "doctor", str(root), "--probe")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn('"free"', proc.stdout)


class LegacyLockTests(ContractCase):
    """L06-legacy: a pre-v2 lock surface (owner record, no protocol/guard)
    is diagnosed as legacy — writes refuse, unlock deletes nothing."""

    def test_legacy_lock_refuses_writes_and_unlock_preserves(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        lockdir = root / ".gg-lock"
        for child in lockdir.iterdir():
            child.unlink()
        (lockdir / "owner.json").write_text(
            '{"pid": 1}', encoding="utf-8")
        self.assertEqual("legacy",
                         gg_lock.inspect_lock(root)["structure"])
        before = (root / "project.json").read_bytes()
        with self.assertRaises(gg_lock.LockError):
            core.apply(
                root,
                {"request_id": "r",
                 "ops": [{"collection": "tasks",
                          "value": {"id": "t"}}]},
                0)
        self.assertEqual(before, (root / "project.json").read_bytes())
        with self.assertRaises(gg_lock.LockError):
            gg_lock.cleanup_owner(root)
        # Nothing was deleted — owner evidence and directory intact.
        self.assertTrue((lockdir / "owner.json").is_file())


class CommitBoundaryTests(ContractCase):
    """L03/L05: canonical bytes are complete-old or complete-new; orphan
    history snapshots are reported; confirmed-commit + cleanup failure is
    exit 3, never flattened."""

    def test_commit_then_confirmed_release_failure_is_cleanup_pending(self):
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

        class ReleaseUncertain(real_lock):
            def __exit__(self, *exc):
                super().__exit__(*exc)
                raise gg_lock.LockCleanupError(
                    [RuntimeError("synthetic owner cleanup failure")],
                    release_confirmed=True,
                    prior_error=None)

        gg_lock.Lock = ReleaseUncertain
        try:
            with self.assertRaises(core.OperationError) as cm:
                core.apply(root, change, 0)
        finally:
            gg_lock.Lock = real_lock
        self.assertEqual("committed_cleanup_pending",
                         cm.exception.result["commit_state"])
        self.assertTrue(cm.exception.result["cleanup_errors"])
        # The commit itself really landed.
        self.assertIn("s", core.load(root)["sources"])

    def test_orphan_history_snapshot_reported_not_committed(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        real_atomic = core.atomic

        def flaky_atomic(path, data):
            if Path(path).name == "project.json":
                raise OSError("synthetic canonical write failure")
            return real_atomic(path, data)

        core.atomic = flaky_atomic
        try:
            with self.assertRaises(core.OperationError) as cm:
                core.apply(
                    root,
                    {"request_id": "r-orphan",
                     "ops": [{"collection": "tasks",
                              "value": {"id": "t"}}]},
                    1)
        finally:
            core.atomic = real_atomic
        result = cm.exception.result
        self.assertEqual("not_committed", result["commit_state"])
        self.assertEqual(1, core.load(root)["revision"])
        # The completed-but-orphaned snapshot is reported, not hidden.
        preserved = result.get("preserved_paths") or []
        self.assertTrue(
            any("revision-1.json" in p for p in preserved),
            result)
        self.assertTrue(
            (root / "migration" / "revision-1.json").is_file())

    def test_commit_failure_leaves_complete_old_canonical(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        before = (root / "project.json").read_bytes()
        real_atomic = core.atomic
        calls = []

        def fail_first_commit(path, data):
            calls.append(Path(path).name)
            if Path(path).name == "project.json":
                raise OSError("synthetic")
            return real_atomic(path, data)

        core.atomic = fail_first_commit
        try:
            with self.assertRaises(core.OperationError) as cm:
                core.apply(
                    root,
                    {"request_id": "r-fail",
                     "ops": [{"collection": "tasks",
                              "value": {"id": "t"}}]},
                    1)
        finally:
            core.atomic = real_atomic
        self.assertEqual("not_committed", cm.exception.result["commit_state"])
        # Canonical bytes are the complete old ones, not a torn write.
        self.assertEqual(before, (root / "project.json").read_bytes())


class CapabilityBoundaryTests(ContractCase):
    """L01-capability extras: foreign-PID and direct *_locked calls are
    refused before the first product-path write."""

    def test_foreign_pid_capability_rejected(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        with gg_lock.Lock(root) as cap:
            # Acquiring the guard legitimately rewrites owner.json (evidence
            # record).  Snapshot after acquisition so the assertion isolates
            # product-path writes only.
            before = self._tree_bytes(root)
            cap.pid = os.getpid() + 4242
            with self.assertRaises(gg_lock.LockError) as cm:
                core._apply_locked(
                    root,
                    {"request_id": "r", "ops": []},
                    0, capability=cap)
        self.assertIn("pid_mismatch", str(cm.exception))
        after = self._tree_bytes(root)
        after.pop(".gg-lock/owner.json", None)
        before.pop(".gg-lock/owner.json", None)
        self.assertEqual(before, after)

    def test_direct_locked_call_with_forged_capability(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        root = self.make_project()
        before = self._tree_bytes(root)
        for forged in (
            {"token": "x"},
            object(),
            gg_lock.Capability(),  # constructed, never registered
        ):
            with self.subTest(forged=type(forged).__name__):
                with self.assertRaises(gg_lock.LockError):
                    core._apply_locked(
                        root,
                        {"request_id": "r", "ops": []},
                        0, capability=forged)
                with self.assertRaises(gg_lock.LockError):
                    core.export_locked(root, "draft", capability=forged)
        self.assertEqual(before, self._tree_bytes(root))

    def _tree_bytes(self, root):
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
        }


class ImportPayloadTests(ContractCase):
    """P12 extras: the destination gets a fresh workspace identity —
    source control artifacts never ride along as payload."""

    def _legacy_source(self):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-legacy-")
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        (src / "interview.json").write_text(
            json.dumps({"answers": {"q1": "무"}}), encoding="utf-8")
        (src / "notes.md").write_text("메모\n", encoding="utf-8")
        return src

    def test_dest_workspace_identity_is_fresh(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        src = self._legacy_source()
        dest = src.parent / "dest"
        core.migrate(src, dest, offline_confirmed=True)
        dest_proto = json.loads(
            (dest / ".gg-lock" / "protocol.json").read_text(
                encoding="utf-8"))
        self.assertTrue(dest_proto["workspace_id"])
        # The destination carries its own receipt; nothing of a source
        # control surface exists because the legacy source had none.
        self.assertTrue(
            (dest / ".gg-import-publication.json").is_file())
        self.assertFalse((dest / ".gg-lock" / "owner.json").exists())
        # Exact retry returns the recorded result without re-publishing.
        again = core.migrate(src, dest, offline_confirmed=True)
        self.assertEqual(
            json.loads((dest / "project.json").read_text(
                encoding="utf-8"))["revision"],
            again["revision"])

    def test_v2_source_control_never_in_payload(self):
        core = runtime("gg_core")
        gg_lock = runtime("gg_lock")
        src = self.make_project()
        _seed(src)
        src_proto = json.loads(
            (src / ".gg-lock" / "protocol.json").read_text(
                encoding="utf-8"))
        dest = src.parent / "dest-copy"
        # A v2-format source is refused as already_new_format — the
        # destination must not be created at all.
        with self.assertRaises(core.OperationError) as cm:
            core.migrate(src, dest)
        self.assertEqual("already_new_format",
                         cm.exception.result["reason"])
        self.assertFalse(dest.exists())


class OutputPublicationGateTests(ContractCase):
    def test_output_without_publication_fails_closed(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        # Craft an output record without a publication_ref by editing the
        # canonical offline — the gate must report it, not pass on bytes.
        p = core.load(root)
        p["outputs"]["legacy"] = {
            "id": "legacy", "path": "legacy.md", "format": "md",
            "file_hash": "0" * 64, "target_refs": [],
            "input_fingerprint": "0" * 64, "revision": 1}
        (root / "project.json").write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = [
            r for r in core.checks(root, core.load(root))
            if r["check_id"] == "output_publication"]
        self.assertEqual(1, len(rows))
        self.assertEqual("legacy", rows[0]["target"])
        self.assertNotEqual("pass", rows[0]["status"])

    def test_adopted_output_passes_publication_gate(self):
        core = runtime("gg_core")
        root = self.make_project()
        _seed(root)
        bind_major(root)
        (root / "ok.md").write_bytes(b"OK")
        p = core.load(root)
        ov = _output_value(core, root, "ok.md", "md", b"OK", p=p)
        core.adopt_output(
            root, ov, p["revision"], "adopt-gate",
            major_id="specialty_crops")
        rows = [
            r for r in core.checks(root, core.load(root))
            if r["check_id"] == "output_publication"]
        self.assertEqual([], rows)


if __name__ == "__main__":
    unittest.main()
