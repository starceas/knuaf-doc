"""Policy B output guard on the canonical/paper paths (G-B, 2026-09-25).

Every case ID refers to COMMON-MAJOR-GUARD-B.md.  Path x case matrix
(each cell is a test method or subTest below):

| entry point \\ case              | L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---------------------------------|----|----|----|----|----|----|----|
| gg.py paper (CLI)               | x  | x  | x  | x  | x  | x  | x  |
| gg_core.paper (direct)          | x  | x  | x  | x  | x  | x  | x  |
| gg_school_paper.paper (direct)  | x  | x  | x  | x  | x  | x  |    |
| gg_school_paper.py (standalone) | x  | x  | x  | x  | x  | x  |    |
| gg.py export / gg_core.export   | x  | x  | x  | x  | x  | x  |    |
| gg.py adopt-output / adopt      | x  | x  | x  | x  | x  | x  |    |
| merge_sections.py (merge)       | x  |    | x  |    |    |    |    |

L7 is exercised on the paper generator (the only path with a legacy
unmarked-spec history); export/adopt reuse the same guard call, pinned by
their L1–L6 rows.  merge only forwards ``--major`` into ``export`` (L2/L4–L6
are export's rows).  A registered peer module without the paper output is
refused on core.paper, export and adopt.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from tests._harness import (
    SCRIPTS, ContractCase, bind_major, fact_op, runtime, source_op, write_text,
)

SPECIALTY = "specialty_crops"
INSECTS = "industrial_insects"
# Registered majors are specialty_crops, industrial_insects, fruit_trees.
# UNREGISTERED keeps the unknown-major path; PROBE is a synthetic peer that
# declares only the outputs a test needs.
# A synthetic ID no real major will ever register (a real peer ID
# breaks this sample the day that major is added).
UNREGISTERED = "unregistered_probe_major"
PROBE = "probe_major"


def _spec(**extra):
    spec = {"author": "합성", "writing_year": 2026,
            "school_profile": {"mode": "school", "school": "합성대학교",
                               "department": "특용작물학과"}}
    spec.update(extra)
    return spec


def _cli(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True)


class _Base(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.sp = runtime("gg_school_paper")

    def _write_spec(self, root, spec, name="paper-spec.json"):
        write_text(root, name, json.dumps(spec, ensure_ascii=False))
        return name

    def _core_paper(self, root, spec, *, major_id=None, out="build/p.md"):
        name = self._write_spec(root, spec)
        return self.core.paper(
            root, name, (Path(root) / name).read_bytes(), out,
            major_id=major_id)

    def _core_held(self, reason, fn, *args, **kw):
        with self.assertRaises(self.core.OperationError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.result["reason"], reason,
                         caught.exception.result)
        self.assertEqual(caught.exception.result["commit_state"],
                         "not_committed")

    def _held_json(self, result, reason):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["reason"], reason, value)

    def _swap_binding(self, root, **changes):
        fact = dict(self.core.load(root)["facts"]["selected_major"])
        fact.update(changes)
        revision = self.core.load(root)["revision"]
        self.core.apply(root, {"request_id": "swap-binding",
                               "ops": [{"collection": "facts",
                                        "value": fact}]}, revision)

    def _probe_registry(self, outputs=("question_list", "document_plan")):
        mc = self.mc
        fruit = mc.declare_module(
            major_id=PROBE, module_version="0.0.1",
            capabilities={"question": "supported",
                          "document": "supported",
                          "evidence": "unsupported",
                          "finance": "unsupported"},
            question_schema=(), document_plan=(),
            evidence_applicability={}, finance_capabilities=(),
            validation_rules=(), supported_outputs=tuple(outputs))
        return mc.ModuleRegistry(mc.MODULES + (fruit,),
                                 pack_owner=mc.load_pack_owner())

    def _bind_probe(self, root, registry):
        """Bind the synthetic probe module and make it the default
        registry for this test (restored by addCleanup)."""
        self.addCleanup(setattr, self.mc, "default_registry",
                        self.mc.default_registry)
        self.mc.default_registry = lambda catalog_path=None: registry
        write_text(root, "major-answer.txt", "전공 선택: " + PROBE + "\n")
        fact = fact_op("selected_major", "common.major_id", PROBE,
                       "", scope="project", verification="claim_supported",
                       source_id="major-answer")
        fact["value"]["module_version"] = "0.0.1"
        src = source_op("major-answer.txt")
        src["value"]["id"] = "major-answer"
        revision = self.core.load(root)["revision"]
        self.core.apply(root, {"request_id": "fruit", "ops": [src, fact]},
                        revision)


class PaperGeneratorGuardTests(_Base):
    """A32-L1..L6 on gg.py paper, gg_core.paper, gg_school_paper.paper and
    the gg_school_paper.py standalone CLI."""

    def _all_held(self, root, spec, reason, *, major_id=None):
        name = self._write_spec(root, spec, "held-spec.json")
        before = self._tree_bytes(root)
        args = ["paper", root, "--input", name, "--out", "build/cli.md"]
        if major_id is not None:
            args += ["--major", major_id]
        self._held_json(_cli("gg.py", *args), reason)
        self._core_held(reason, self.core.paper, root, name,
                        (Path(root) / name).read_bytes(), "build/core.md",
                        major_id=major_id)
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.sp.paper(spec, context=self.mc.output_context(
                root, major_id))
        self.assertEqual(caught.exception.reason, reason)
        args = [root, "--input", name, "--out", "build/standalone.md"]
        if major_id is not None:
            args += ["--major", major_id]
        self._held_json(_cli("gg_school_paper.py", *args), reason)
        self.assertEqual(self._tree_bytes(root), before)

    def test_l1_unmarked_specs_are_held(self):
        """A32-L1: flat unmarked and specialty free-label specs are held on
        every paper entry point — no specialty inference."""
        root = self.make_project()
        for spec in ({"writing_year": 2026},
                     _spec(major="특용작물전공", title="인삼 재배 창업계획")):
            with self.subTest(spec=spec):
                self._all_held(root, spec, "major_id_required")

    def test_l2_binding_without_explicit_id_is_held(self):
        root = self.make_project()
        bind_major(root)
        self._all_held(root, _spec(), "major_id_required")

    def _bound(self):
        root = self.make_project()
        bind_major(root)
        return root

    def test_l3_explicit_specialty_with_binding_generates(self):
        """A32-L3: explicit ID (spec or --major) + matching binding keeps
        the existing generated output on every paper entry point.  One
        project per publication — a revision holds one managed paper."""
        root = self._bound()
        name = self._write_spec(root, _spec(major_id=SPECIALTY))
        cli = _cli("gg.py", "paper", root, "--input", name,
                   "--out", "build/cli.md")
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["status"], "generated")

        root = self._bound()
        plain = self._write_spec(root, _spec(), "plain-spec.json")
        via_flag = _cli("gg.py", "paper", root, "--input", plain,
                        "--out", "build/flag.md", "--major", SPECIALTY)
        self.assertEqual(via_flag.returncode, 0, via_flag.stdout)

        root = self._bound()
        value = self._core_paper(root, _spec(), major_id=SPECIALTY,
                                 out="build/core.md")
        self.assertEqual(value["status"], "generated")
        # Persistent record: the managed bundle carries the authorization.
        revision = self.core.load(root)["revision"]
        record = json.loads(
            (Path(root) / "build" / str(revision) / "paper"
             / "major_authorization.json").read_text(encoding="utf-8"))
        self.assertEqual(record, value["major_authorization"])
        self.assertEqual(record["project_revision"], revision)

        root = self._bound()
        text = self.sp.paper(_spec(major_id=SPECIALTY),
                             context=self.mc.output_context(root))
        self.assertIn("Ⅰ. 머리말", text)
        name = self._write_spec(root, _spec(major_id=SPECIALTY))
        standalone = _cli("gg_school_paper.py", root, "--input", name,
                          "--out", "build/standalone.md")
        self.assertEqual(standalone.returncode, 0, standalone.stdout)
        self.assertIn("Ⅰ. 머리말", (Path(root) / "build/standalone.md")
                      .read_text(encoding="utf-8"))

    def test_recovery_republishes_only_with_persisted_authorization(self):
        """Resume of a committed paper bundle (requested file missing)
        returns the bundle's persisted authorization; a bundle without it
        is not republished."""
        root = self._bound()
        name = self._write_spec(root, _spec(major_id=SPECIALTY))
        spec_bytes = (Path(root) / name).read_bytes()
        first = self.core.paper(root, name, spec_bytes, "build/r.md")
        (Path(root) / "build/r.md").unlink()
        again = self.core.paper(root, name, spec_bytes, "build/r.md")
        self.assertEqual(again["status"], "generated")
        self.assertEqual(again["major_authorization"],
                         first["major_authorization"])
        self.assertTrue((Path(root) / "build/r.md").is_file())
        revision = self.core.load(root)["revision"]
        (Path(root) / "build/r.md").unlink()
        # A pre-guard receipt (the file never listed) is refused.
        real_find = self.core._find_request

        def legacy_receipts(*args, **kw):
            found = real_find(*args, **kw)
            return [dict(r, files=[f for f in r.get("files", [])
                                   if f.get("path") !=
                                   "major_authorization.json"])
                    for r in found]

        with mock.patch.object(self.core, "_find_request", legacy_receipts):
            self._core_held("major_authorization_missing", self.core.paper,
                            root, name, spec_bytes, "build/r.md")
        self.assertFalse((Path(root) / "build/r.md").exists())
        (Path(root) / "build" / str(revision) / "paper"
         / "major_authorization.json").unlink()
        # Removing the file from a committed bundle is caught by the
        # publication integrity check first; a bundle whose receipt never
        # listed the file (pre-guard) reaches major_authorization_missing.
        # Either way nothing is republished.
        with self.assertRaises(self.core.OperationError) as caught:
            self.core.paper(root, name, spec_bytes, "build/r.md")
        self.assertIn(caught.exception.result["reason"],
                      {"major_authorization_missing",
                       "request_lookup_failed"})
        self.assertFalse((Path(root) / "build/r.md").exists())

    def test_l4_unregistered_conflicting_and_unsupported(self):
        """A32-L4: an unregistered ID, the registered fruit ID against a
        specialty binding, fruit/insect labels against a specialty ID,
        spec vs --major conflict, and an insect binding."""
        root = self.make_project()
        bind_major(root)
        unknown = _spec(major_id=UNREGISTERED)
        unknown["school_profile"]["department"] = "합성학과"
        self._all_held(root, unknown, "unknown_major")
        fruit = _spec(major_id="fruit_trees")
        fruit["school_profile"]["department"] = "과수학과"
        self._all_held(root, fruit, "major_binding_mismatch")
        fruit_label = _spec(major_id=SPECIALTY)
        fruit_label["school_profile"]["department"] = "과수학과"
        self._all_held(root, fruit_label, "major_marker_conflict")
        self._all_held(root, _spec(major_id=SPECIALTY),
                       "major_id_conflict", major_id=INSECTS)
        insect_root = self.make_project()
        bind_major(insect_root, INSECTS)
        insect = _spec(major_id=INSECTS)
        insect["school_profile"]["department"] = "산업곤충학과"
        self._all_held(insect_root, insect, "unsupported_output")

    def test_l4_registered_module_without_paper_is_refused(self):
        """The real registered fruit_trees module declares no paper."""
        root = self.make_project()
        bind_major(root, "fruit_trees")
        spec = _spec(major_id="fruit_trees")
        spec["school_profile"]["department"] = "과수학과"
        name = self._write_spec(root, spec)
        before = self._tree_bytes(root)
        self._core_held("unsupported_output", self.core.paper, root, name,
                        (Path(root) / name).read_bytes(), "build/f.md")
        self._core_held("unsupported_output", self.core.export, root,
                        "draft", major_id="fruit_trees")
        self.assertEqual(self._tree_bytes(root), before)

    def test_l5_invalid_ids_and_bindings_are_held(self):
        root = self.make_project()
        bind_major(root)
        self._all_held(root, _spec(major_id=None), "major_id_invalid")
        self._all_held(root, _spec(major_id=""), "major_id_invalid")
        self._swap_binding(root, module_version="0.9.0")
        self._all_held(root, _spec(major_id=SPECIALTY),
                       "major_binding_invalid")

    def test_l6_explicit_id_without_binding_is_held(self):
        root = self.make_project()
        self._all_held(root, _spec(major_id=SPECIALTY),
                       "major_binding_required")

    def test_direct_render_requires_context(self):
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.sp.paper(_spec(major_id=SPECIALTY))
        self.assertEqual(caught.exception.reason, "output_context_required")

    def test_binding_change_before_lock_recheck_is_not_published(self):
        """The pre-lock check passes, then the binding moves before the
        in-lock recheck: nothing is published."""
        root = self.make_project()
        bind_major(root)
        real = self.mc.authorize_output
        calls = []

        def pass_then_swap(*args, **kw):
            result = real(*args, **kw)
            if not calls:
                calls.append(1)
                self._swap_binding(root, value=INSECTS,
                                   module_version="0.1.0")
            return result

        with mock.patch.object(self.mc, "authorize_output", pass_then_swap):
            self._core_held("major_binding_mismatch", self._core_paper,
                            root, _spec(major_id=SPECIALTY),
                            out="build/raced.md")
        self.assertFalse((Path(root) / "build/raced.md").exists())
        self.assertFalse((Path(root) / "build").exists())


class PaperMigrationTests(_Base):
    """A32-L7: hold before migration, explicit selection + binding in a new
    revision, then the supported output with the same body meaning."""

    def _body(self, text):
        return text[text.index("Ⅰ. 머리말"):]

    def _unguarded_body(self, spec):
        # The pre-B rendering of the ORIGINAL spec, for comparison only.
        with mock.patch.object(self.sp.mc, "authorize_output",
                               lambda *a, **k: None):
            return self._body(self.sp.paper(dict(spec)))

    def _migrate(self, root, original, *, bind, add_id):
        name = self._write_spec(root, original, "original.json")
        original_bytes = (Path(root) / name).read_bytes()
        before_revision = self.core.load(root)["revision"]
        with self.assertRaises(self.core.OperationError):
            self.core.paper(root, name, original_bytes, "build/held.md")
        self.assertFalse((Path(root) / "build/held.md").exists())
        if bind:
            bind_major(root)
        migrated = dict(original, major_id=SPECIALTY) if add_id else original
        new = self._write_spec(root, migrated, "migrated.json")
        value = self.core.paper(root, new, (Path(root) / new).read_bytes(),
                                "build/migrated.md")
        self.assertEqual(value["status"], "generated")
        # Original input is preserved byte for byte.
        self.assertEqual((Path(root) / name).read_bytes(), original_bytes)
        body = self._body((Path(root) / "build/migrated.md")
                          .read_text(encoding="utf-8"))
        self.assertEqual(body, self._unguarded_body(original))
        p = self.core.load(root)
        majors = [f for f in p["facts"].values()
                  if f.get("field_id") == "common.major_id"]
        self.assertEqual(len(majors), 1)
        if bind:
            # A new revision: every gate row is judged at it, and the
            # review/approval rows remain open rather than carried over.
            self.assertGreater(p["revision"], before_revision)
            rows = self.core.gate(root, p)
            self.assertTrue(rows)
            self.assertEqual({r["input_revision"] for r in rows},
                             {p["revision"]})
            self.assertTrue(any(r["status"] == "blocked" and
                                "검토" in r["reason"] for r in rows))

    def test_l7_from_l1_unmarked_and_unbound(self):
        self._migrate(self.make_project(), _spec(), bind=True, add_id=True)

    def test_l7_from_l2_binding_only(self):
        root = self.make_project()
        bind_major(root)
        self._migrate(root, _spec(), bind=False, add_id=True)

    def test_l7_from_l6_id_only(self):
        self._migrate(self.make_project(), _spec(major_id=SPECIALTY),
                      bind=True, add_id=False)

    def test_l7_prior_review_and_revision_dependency(self):
        """A32-L7 dependency check: a review recorded before the migration
        is re-evaluated at the new revision.  Review freshness in this
        product is bound to the reviewed content (target refs +
        fingerprint), which the binding does not change, so the prior
        calculation review stays fresh and no gate row changes; the
        output authorization, however, is recomputed and recorded at the
        new revision, and a held pre-migration request never becomes an
        output."""
        from tests.test_p3_flow import _bind_economic_review
        root = self.make_project()
        before = _bind_economic_review(self.core, root)
        self.assertEqual(
            self.core.finance_review_freshness(root, before, "rev-calc")[0],
            "fresh")
        gate_before = {(r["check_id"], r["target"]): r["status"]
                       for r in self.core.gate(root, before)}
        name = self._write_spec(root, _spec(), "original.json")
        with self.assertRaises(self.core.OperationError):
            self.core.paper(root, name, (Path(root) / name).read_bytes(),
                            "build/held.md")
        revision = bind_major(root)
        after = self.core.load(root)
        self.assertGreater(revision, before["revision"])
        self.assertEqual(
            self.core.finance_review_freshness(root, after, "rev-calc")[0],
            "fresh")
        gate_after = {(r["check_id"], r["target"]): r["status"]
                      for r in self.core.gate(root, after)}
        self.assertEqual(gate_after, gate_before)
        value = self.core.paper(root, name, (Path(root) / name).read_bytes(),
                                "build/migrated.md", major_id=SPECIALTY)
        self.assertEqual(value["major_authorization"]["project_revision"],
                         revision)
        self.assertFalse((Path(root) / "build/held.md").exists())


    def test_l7_professor_approval_is_revision_bound(self):
        """A32-L7 with the owner's "keep current" decision: professor
        approval stays bound to the revision it was registered at (existing
        professor_approval_check), so a new binding revision requires
        re-approval; the output guard keeps authorizing under the same
        valid binding."""
        root = self.make_project()
        bind_major(root)
        (Path(root) / "o.md").write_bytes("# 검토본\n".encode("utf-8"))
        p = self.core.load(root)
        refs = self.core.sort_target_refs(self.core._export_refs(p))
        ov = {"id": "o", "path": "o.md", "format": "md",
              "file_hash": self.core.digest((Path(root) / "o.md")
                                            .read_bytes()),
              "target_refs": refs,
              "input_fingerprint": self.core.fingerprint(root, p, refs)}
        self.core.adopt_output(root, ov, p["revision"], "adopt",
                               major_id=SPECIALTY)
        p = self.core.load(root)
        write_text(root, "approval.txt", "교수 확인\n")
        trefs = self.core.sort_target_refs(
            [r for r in self.core._export_refs(p)
             if r["collection"] == "outputs"])
        approval = {
            "id": "prof", "kind": "professor", "status": "confirmed",
            "stale": False, "evidence_path": "approval.txt",
            "evidence_hash": self.core.digest(
                (Path(root) / "approval.txt").read_bytes()),
            "scope": "project_outputs", "target_refs": trefs,
            "human_confirmation": {"explicit": True, "confirmed_by": "교수"},
            "registered_revision": p["revision"] + 1,
            "input_revision": p["revision"],
            "input_fingerprint": self.core.fingerprint(root, p, trefs)}
        self.core.apply(root, {"request_id": "approve", "ops": [
            {"collection": "approvals", "value": approval}]}, p["revision"])
        approved = self.core.load(root)
        self.assertEqual(
            self.core.professor_approval_check(approved, root)["status"],
            "pass")
        # Revision dependence isolated: a source-only change outside the
        # approval's targets leaves the approved fingerprint and stale flag
        # unchanged, yet the approval no longer passes at the new revision.
        write_text(root, "memo.txt", "메모\n")
        memo = source_op("memo.txt")
        memo["value"]["id"] = "memo"
        self.core.apply(root, {"request_id": "memo", "ops": [memo]},
                        approved["revision"])
        bumped = self.core.load(root)
        kept = bumped["approvals"]["prof"]
        self.assertEqual(self.core.fingerprint(root, bumped, trefs),
                         kept["input_fingerprint"])
        self.assertFalse(kept.get("stale"))
        self.assertGreater(bumped["revision"], approved["revision"])
        self.assertNotEqual(
            self.core.professor_approval_check(bumped, root)["status"],
            "pass")
        approved = bumped
        # Re-record the same explicit selection from a new answer: a new
        # revision, same valid binding.
        write_text(root, "major-answer-2.txt", "전공 재확인: specialty_crops\n")
        src = source_op("major-answer-2.txt")
        src["value"]["id"] = "major-answer-2"
        fact = dict(approved["facts"]["selected_major"])
        fact["source_refs"] = [dict(fact["source_refs"][0],
                                    id="major-answer-2")]
        self.core.apply(root, {"request_id": "rebind", "ops": [
            src, {"collection": "facts", "value": fact}]},
            approved["revision"])
        after = self.core.load(root)
        self.assertNotEqual(
            self.core.professor_approval_check(after, root)["status"],
            "pass")
        auth = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_PAPER,
            self.mc.output_context(root, SPECIALTY))
        self.assertEqual(auth.project_revision, after["revision"])


class ExportAdoptGuardTests(_Base):
    """A32-L1..L6 on export (all kinds) and adopt-output, plus merge."""

    def _export_held(self, root, reason, major_id=None, kinds=None):
        before = self._tree_bytes(root)
        for kind in kinds or ("draft", "review", "submission_candidate"):
            with self.subTest(kind=kind, major_id=major_id):
                self._core_held(reason, self.core.export, root, kind,
                                major_id=major_id)
                args = ["export", root, "--kind", kind]
                if major_id is not None:
                    args += ["--major", major_id]
                self.assertNotEqual(_cli("gg.py", *args).returncode, 0)
        self.assertEqual(self._tree_bytes(root), before)

    def test_export_cases(self):
        root = self.make_project()
        self._export_held(root, "major_id_required")                  # L1
        self._export_held(root, "major_binding_required", SPECIALTY)  # L6
        bind_major(root)
        self._export_held(root, "major_id_required")                  # L2
        self._export_held(root, "unknown_major", UNREGISTERED)        # L4
        self._export_held(root, "major_binding_mismatch",
                          "fruit_trees")                              # L4
        self._export_held(root, "major_binding_mismatch", INSECTS)    # L4
        value = self.core.export(root, "draft", major_id=SPECIALTY)   # L3
        self.assertEqual(value["status"], "generated")
        manifest = json.loads((Path(value["path"]).parent / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["major_authorization"][0]["major_id"],
                         SPECIALTY)
        cli = _cli("gg.py", "export", root, "--kind", "review",
                   "--major", SPECIALTY)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self._swap_binding(root, verification="source_located")       # L5
        self._export_held(root, "major_binding_invalid", SPECIALTY,
                          kinds=("draft",))
        insect_root = self.make_project()
        bind_major(insect_root, INSECTS)
        self._export_held(insect_root, "unsupported_output", INSECTS,
                          kinds=("draft",))

    def test_merge_forwards_major(self):
        root = self.make_project()
        bind_major(root)
        before = self._tree_bytes(root)
        held = _cli("merge_sections.py", root)
        self.assertNotEqual(held.returncode, 0)
        self.assertEqual(json.loads(held.stdout)["reason"],
                         "major_id_required")
        self.assertEqual(self._tree_bytes(root), before)
        ok = _cli("merge_sections.py", root, "--major", SPECIALTY)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def _output_value(self, root, path, data):
        (Path(root) / path).write_bytes(data)
        p = self.core.load(root)
        refs = self.core.sort_target_refs(self.core._export_refs(p))
        return p, {
            "id": Path(path).stem, "path": path, "format": Path(path).suffix[1:],
            "file_hash": self.core.digest(data), "target_refs": refs,
            "input_fingerprint": self.core.fingerprint(root, p, refs),
        }

    def _adopt(self, root, path, data, *, major_id=None, companions=None,
               request_id="adopt"):
        p, ov = self._output_value(root, path, data)
        return self.core.adopt_output(root, ov, p["revision"], request_id,
                                      companion_files=companions,
                                      major_id=major_id)

    def test_adopt_cases(self):
        body = "# 검토본\n".encode("utf-8")
        root = self.make_project()
        (Path(root) / "o.md").write_bytes(body)
        before = self._tree_bytes(root)
        self._core_held("major_id_required", self._adopt, root, "o.md",
                        body)                                         # L1
        self.assertEqual(self._tree_bytes(root), before)
        self._core_held("major_binding_required", self._adopt, root,
                        "o.md", body, major_id=SPECIALTY)             # L6
        bind_major(root)
        self._core_held("major_id_required", self._adopt, root, "o.md",
                        body)                                         # L2
        self._core_held("unknown_major", self._adopt, root, "o.md", body,
                        major_id=UNREGISTERED)                        # L4
        self._core_held("major_binding_mismatch", self._adopt, root,
                        "o.md", body, major_id="fruit_trees")         # L4
        self._core_held("major_binding_mismatch", self._adopt, root,
                        "o.md", body, major_id=INSECTS)               # L4
        self._core_held("unknown_output", self._adopt, root, "o.dat",
                        body, major_id=SPECIALTY)
        value = self._adopt(root, "o.md", body, major_id=SPECIALTY)   # L3
        self.assertEqual(value["status"], "adopted")
        stored = self.core.load(root)["outputs"]["o"]
        self.assertEqual(stored["major_authorization"],
                         value["major_authorization"])
        self._swap_binding(root, module_version="0.9.0")              # L5
        (Path(root) / "o2.md").write_bytes(body)
        before = self._tree_bytes(root)
        self._core_held("major_binding_invalid", self._adopt, root,
                        "o2.md", body, major_id=SPECIALTY,
                        request_id="adopt-2")
        self.assertEqual(self._tree_bytes(root), before)

    def test_adopt_retry_of_stored_result_is_guarded(self):
        """A registered adoption whose source file is later removed: the
        same request retried without an ID (or under a moved binding) is
        held — returning the stored result is an output too."""
        root = self.make_project()
        bind_major(root)
        body = "# 검토본\n".encode("utf-8")
        p, ov = self._output_value(root, "o.md", body)
        first = self.core.adopt_output(root, ov, p["revision"], "adopt-r",
                                       major_id=SPECIALTY)
        self.assertEqual(first["status"], "adopted")
        self.assertEqual(first["major_authorization"][0]["major_id"],
                         SPECIALTY)
        (Path(root) / "o.md").unlink()
        before = self._tree_bytes(root)
        self._core_held("major_id_required", self.core.adopt_output, root,
                        ov, p["revision"], "adopt-r")
        self.assertEqual(self._tree_bytes(root), before)
        again = self.core.adopt_output(root, ov, p["revision"], "adopt-r",
                                       major_id=SPECIALTY)
        self.assertEqual(again["status"], "existing")
        # The persisted record is returned; the passing check rides along.
        self.assertEqual(again["major_authorization"],
                         first["major_authorization"])
        self.assertEqual(
            again["major_authorization_current"][0]["project_revision"],
            self.core.load(root)["revision"])
        # A stored adoption without a persisted authorization (made before
        # the guard) is not returned as an output.
        record = dict(self.core.load(root)["outputs"]["o"])
        record.pop("major_authorization")
        legacy = self.core.load(root)
        legacy["outputs"]["o"] = record
        (Path(root) / "project.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self._core_held("major_authorization_missing",
                        self.core.adopt_output, root, ov, p["revision"],
                        "adopt-r", major_id=SPECIALTY)
        self._swap_binding(root, value=INSECTS, module_version="0.1.0")
        self._core_held("major_binding_mismatch", self.core.adopt_output,
                        root, ov, p["revision"], "adopt-r",
                        major_id=SPECIALTY)
        self._core_held("unsupported_output", self.core.adopt_output,
                        root, ov, p["revision"], "adopt-r",
                        major_id=INSECTS)

    def test_adopt_cli_requires_major(self):
        root = self.make_project()
        bind_major(root)
        p, ov = self._output_value(root, "o.md", "# 검토본\n".encode())
        write_text(root, "adopt.json", json.dumps({"output": ov},
                                                  ensure_ascii=False))
        before = self._tree_bytes(root)
        held = _cli("gg.py", "adopt-output", root, "--input", "adopt.json",
                    "--expected-revision", p["revision"])
        self.assertNotEqual(held.returncode, 0)
        self.assertEqual(json.loads(held.stdout)["reason"],
                         "major_id_required")
        self.assertEqual(self._tree_bytes(root), before)
        ok = _cli("gg.py", "adopt-output", root, "--input", "adopt.json",
                  "--expected-revision", p["revision"], "--major", SPECIALTY)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_paper_only_module_cannot_adopt_workbook_companion(self):
        """Each published file needs its own kind: a module declaring only
        the paper output may adopt a paper file, but not with a workbook
        companion, and not a PDF of unknown origin."""
        from openpyxl import Workbook
        import io
        root = self.make_project()
        self._bind_probe(root, self._probe_registry(
            outputs=("question_list", "document_plan", "school_paper")))
        buf = io.BytesIO()
        Workbook().save(buf)
        (Path(root) / "재무.xlsx").write_bytes(buf.getvalue())
        companion = [{"path": "재무.xlsx",
                      "sha256": self.core.digest(buf.getvalue())}]
        self._core_held("unsupported_output", self._adopt, root, "o.md",
                        "# 본문\n".encode(), major_id=PROBE,
                        companions=companion)
        self._core_held("unsupported_output", self._adopt, root, "o.pdf",
                        b"%PDF-1.7\n%%EOF\n", major_id=PROBE,
                        request_id="adopt-pdf")
        value = self._adopt(root, "o.md", "# 본문\n".encode(),
                            major_id=PROBE, request_id="adopt-md")
        self.assertEqual(value["status"], "adopted")
        # Retrying the registered request after its source is removed is
        # judged on the managed copy (paper only) — not on both kinds.
        p = self.core.load(root)
        ov = dict(p["outputs"]["o"])
        registered = {k: ov[k] for k in ("id", "format", "file_hash",
                                         "target_refs", "input_fingerprint")}
        registered["path"] = "o.md"
        (Path(root) / "o.md").unlink()
        again = self.core.adopt_output(root, registered, p["revision"] - 1,
                                       "adopt-md", major_id=PROBE)
        self.assertEqual(again["status"], "existing")
