"""Synthetic tests for the common major contract (DECISION-20260924).

No real workspaces, packs, sources, or student data — a synthetic
``ModuleRegistry`` and fabricated proposals exercise the contract: the
six gg_core states are preserved, majors are peers with no cross-major
dependency, a missing major fails closed (never defaulting to
specialty_crops), modules cannot write the canonical record directly,
and the industrial_insects path performs no calculation, emits no crop
paper wording, and claims no workbook.
"""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
PACKS_DIR = TESTS.parent / "references" / "benchmark-packs"

sys.path.insert(0, str(SCRIPTS))
import gg_core  # noqa: E402
import gg_major_contract as mc  # noqa: E402

SPECIALTY_PACK = "mafra.specialty.production.2024"
PACK_OWNER = {SPECIALTY_PACK: "specialty_crops"}

SIX_STATES = {
    "not_provided",
    "explicit_none",
    "unknown",
    "provided",
    "not_applicable",
    "withheld",
}

CROP_WORDING = (
    "특용작물",
    "본포",
    "토양관리",
    "재배기술",
    "관수",
    "농산물 가공",
)


def registry():
    return mc.ModuleRegistry(mc.MODULES, pack_owner=dict(PACK_OWNER))


class TestStatesAndDeclarations(unittest.TestCase):
    def test_six_states_preserved(self):
        self.assertEqual(mc.ANSWER_STATES, gg_core.STATES)
        self.assertEqual(mc.ANSWER_STATES, SIX_STATES)
        self.assertEqual(len(mc.ANSWER_STATES), 6)

    def test_normalize_answer_state(self):
        for s in SIX_STATES:
            self.assertEqual(mc.normalize_answer_state(s), s)
        for bad in (None, 0, "0", "missing", "provided "):
            with self.assertRaises(ValueError):
                mc.normalize_answer_state(bad)

    def test_peer_declarations(self):
        reg = registry()
        sc = reg.resolve("specialty_crops")
        ii = reg.resolve("industrial_insects")
        self.assertIsNot(sc, ii)
        self.assertEqual(
            sc.contract_version, ii.contract_version, mc.CONTRACT_VERSION
        )
        self.assertEqual(sc.major_id, "specialty_crops")
        self.assertEqual(ii.major_id, "industrial_insects")
        self.assertEqual(sc.field_namespace, "specialty_crops")
        self.assertEqual(ii.field_namespace, "industrial_insects")
        for module in (sc, ii):
            self.assertEqual(module.imports, ())
            self.assertEqual(module.pack_refs, ())

    def test_field_namespace_enforced(self):
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={"question": "supported"},
                question_schema=(
                    mc.QuestionSpec(
                        field_id="specialty_crops.crop_item",
                        meaning="foreign namespace",
                    ),
                ),
            )

    def test_contract_version_enforced(self):
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                contract_version="knuaf-major-contract/0",
                capabilities={},
            )

    def test_empty_slot_preserved(self):
        ii = registry().resolve("industrial_insects")
        self.assertEqual(ii.packs, ())


class TestBindingAndRouting(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_explicit_binding(self):
        b = mc.bind_major(self.reg, "industrial_insects")
        self.assertEqual(b.major_id, "industrial_insects")
        self.assertEqual(b.module_version, "0.2.0")
        self.assertEqual(b.contract_version, mc.CONTRACT_VERSION)
        self.assertEqual(b.basis, "explicit_selection")
        self.assertIsNone(b.binding_evidence)

    def test_binding_basis_closed_set(self):
        for bad in ("guessed", "", None, {"a": 1}):
            with self.assertRaises(mc.BindingInvalidError) as cm:
                mc.bind_major(self.reg, "specialty_crops", basis=bad)
            self.assertEqual(cm.exception.reason, "binding_invalid")

    def test_legacy_binding_requires_evidence(self):
        with self.assertRaises(mc.BindingInvalidError):
            mc.bind_major(self.reg, "specialty_crops",
                          basis="legacy_record")
        with self.assertRaises(mc.BindingInvalidError):
            mc.bind_major(self.reg, "specialty_crops",
                          basis="legacy_record", evidence="")
        with self.assertRaises(mc.BindingInvalidError):
            mc.bind_major(self.reg, "specialty_crops",
                          basis="legacy_record", evidence=42)
        b = mc.bind_major(
            self.reg,
            "specialty_crops",
            basis="legacy_record",
            evidence="project:old/fields.json#major",
        )
        self.assertEqual(b.basis, "legacy_record")
        self.assertEqual(
            b.binding_evidence, "project:old/fields.json#major"
        )

    def test_missing_major_fails_closed(self):
        for bad in (None, "", 0, 17):
            with self.assertRaises(mc.MissingMajorError) as cm:
                mc.bind_major(self.reg, bad)
            self.assertEqual(cm.exception.reason, "major_required")
            with self.assertRaises(mc.MissingMajorError):
                mc.route(self.reg, bad, "question")

    def test_no_global_specialty_default(self):
        self.assertFalse(hasattr(mc, "DEFAULT_MAJOR"))
        with self.assertRaises(mc.MissingMajorError):
            mc.route(self.reg, None, "document")
        held = mc.common_only_plan()
        self.assertEqual(held["status"], "held")
        self.assertTrue(held["common_only"])
        self.assertEqual(set(held["held"]), set(mc.CAPABILITY_KINDS))
        self.assertNotIn("specialty_crops", json.dumps(held))

    def test_unknown_major_fails_closed(self):
        with self.assertRaises(mc.UnknownMajorError) as cm:
            mc.bind_major(self.reg, "orchids")
        self.assertEqual(cm.exception.reason, "unknown_major")
        with self.assertRaises(mc.UnknownMajorError):
            mc.route(self.reg, "orchids", "document")

    def test_capability_proposals_exposed(self):
        props = mc.capability_proposals(self.reg, "industrial_insects")
        self.assertEqual(set(props), set(mc.CAPABILITY_KINDS))
        q = props["question"].proposal
        self.assertEqual(sorted(q["answer_states"]), sorted(SIX_STATES))
        for f in q["fields"]:
            self.assertTrue(
                f["field_id"].startswith("industrial_insects."),
                f["field_id"],
            )
        d = props["document"].proposal
        self.assertTrue(d["sections"])
        for s in d["sections"]:
            self.assertIn("section_id", s)
            self.assertIn("role", s)
        self.assertEqual(
            {p["ref"] for p in d["precedents"]},
            {"F0", "E1", "E2", "E3", "E4"},
        )
        self.assertEqual(d["mandated_sections"], ())
        e = props["evidence"].proposal
        self.assertEqual(set(e["axes"]), set(mc.EVIDENCE_AXES))
        self.assertIn("link_only", e["axes"]["acquisition"])
        self.assertIn("verified_observation", e["axes"]["observation"])
        f = props["finance"].proposal
        self.assertEqual(
            set(f["envelope_fields"]), set(mc.FINANCE_ENVELOPE_FIELDS)
        )
        for p in props.values():
            self.assertFalse(p.canonical_write)
            self.assertEqual(p.contract_version, mc.CONTRACT_VERSION)

    def test_unknown_capability_fails_closed(self):
        with self.assertRaises(mc.UnsupportedOutputError):
            mc.route(self.reg, "specialty_crops", "telemetry")


class TestDependencyIsolation(unittest.TestCase):
    def test_cross_major_import_rejected(self):
        with self.assertRaises(mc.CrossMajorDependencyError) as cm:
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={},
                imports=("specialty_crops",),
            )
        self.assertEqual(cm.exception.reason, "cross_major_dependency")

    def test_foreign_pack_reference_rejected(self):
        bad = mc.declare_module(
            major_id="industrial_insects",
            module_version="9.9",
            capabilities={},
            pack_refs=(SPECIALTY_PACK,),
        )
        with self.assertRaises(mc.CrossMajorDependencyError):
            mc.ModuleRegistry((bad,), pack_owner=dict(PACK_OWNER))

    def test_foreign_pack_claim_rejected(self):
        bad = mc.declare_module(
            major_id="industrial_insects",
            module_version="9.9",
            capabilities={},
            packs=(SPECIALTY_PACK,),
        )
        with self.assertRaises(mc.CrossMajorDependencyError):
            mc.ModuleRegistry((bad,), pack_owner=dict(PACK_OWNER))

    def test_common_pack_reference_allowed_but_not_applicable(self):
        common = mc.declare_module(
            major_id="industrial_insects",
            module_version="9.9",
            capabilities={},
            pack_refs=("rda.econ.2025",),
        )
        reg = mc.ModuleRegistry(
            (common,), pack_owner={SPECIALTY_PACK: "specialty_crops",
                                   "rda.econ.2025": None}
        )
        self.assertEqual(reg.resolve("industrial_insects"), common)

    def test_unknown_pack_id_fails_closed(self):
        """An absent pack id is unknown — never treated as common."""
        for field_kw in ({"packs": ("ghost.pack",)},
                         {"pack_refs": ("ghost.pack",)}):
            mod = mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={},
                **field_kw,
            )
            with self.assertRaises(mc.DeclarationInvalidError):
                mc.ModuleRegistry((mod,), pack_owner=dict(PACK_OWNER))
        # A registry with no catalog at all cannot prove ownership of a
        # declared pack — register fails closed rather than trusting it.
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.ModuleRegistry(mc.MODULES)


class TestOutputIsolation(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_no_crop_paper_in_insect_path(self):
        prop = mc.route(self.reg, "industrial_insects", "document")
        blob = json.dumps(prop.to_dict(), ensure_ascii=False)
        for term in CROP_WORDING:
            self.assertNotIn(term, blob)

    def test_crop_term_in_insect_proposal_rejected(self):
        prop = mc.route(self.reg, "industrial_insects", "document")
        forged = replace(
            prop,
            proposal=MappingProxyType(
                {"sections": [{"section_id": "x", "role": "재배기술"}]}
            ),
        )
        module = self.reg.resolve("industrial_insects")
        with self.assertRaises(mc.ProposalInvalidError):
            mc.validate_proposal(module, forged)

    def test_no_workbook_claim_in_insects(self):
        ii = self.reg.resolve("industrial_insects")
        self.assertNotIn("school_excel_workbook", ii.supported_outputs)
        with self.assertRaises(mc.UnsupportedOutputError) as cm:
            mc.route(
                self.reg,
                "industrial_insects",
                "document",
                output="school_excel_workbook",
            )
        self.assertEqual(cm.exception.reason, "unsupported_output")
        prop = mc.route(self.reg, "industrial_insects", "document")
        payload = prop.to_dict()["proposal"]
        self.assertNotIn("workbook", payload)
        self.assertNotIn("sheets", payload)

    def test_no_paper_draft_claim_for_insects(self):
        """No insect document generator exists — the contract exposes
        the document *plan* only, never a rendered-paper output."""
        ii = self.reg.resolve("industrial_insects")
        self.assertNotIn("paper_draft", ii.supported_outputs)
        self.assertNotIn("school_paper", ii.supported_outputs)
        with self.assertRaises(mc.UnsupportedOutputError):
            mc.route(self.reg, "industrial_insects", "document",
                     output="paper_draft")
        # …while the document plan itself is still exposed.
        prop = mc.route(self.reg, "industrial_insects", "document",
                        output="document_plan")
        self.assertEqual(prop.status, "supported")
        self.assertTrue(prop.to_dict()["proposal"]["sections"])

    def test_specialty_workbook_output_routes(self):
        prop = mc.route(
            self.reg,
            "specialty_crops",
            "document",
            output="school_excel_workbook",
        )
        self.assertEqual(prop.status, "supported")

    def test_unsupported_output_fails_closed(self):
        with self.assertRaises(mc.UnsupportedOutputError):
            mc.route(
                self.reg,
                "specialty_crops",
                "document",
                output="telemetry_feed",
            )


class TestFinance(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_insect_calculation_unsupported(self):
        prop = mc.route(self.reg, "industrial_insects", "finance")
        self.assertEqual(prop.status, "unsupported")
        payload = prop.to_dict()["proposal"]
        self.assertFalse(payload["blocks_document"])
        self.assertTrue(
            all(
                c["status"] == "unsupported" for c in payload["capabilities"]
            )
        )
        self.assertNotIn('"calculated": true', json.dumps(payload))
        with self.assertRaises(mc.UnsupportedOutputError):
            mc.route(
                self.reg,
                "industrial_insects",
                "finance",
                output="finance_calculation",
            )

    def test_unsupported_finance_may_not_emit_values(self):
        prop = mc.route(self.reg, "industrial_insects", "finance")
        forged = replace(
            prop,
            proposal=MappingProxyType(
                {"capabilities": [], "calculated": True}
            ),
        )
        module = self.reg.resolve("industrial_insects")
        with self.assertRaises(mc.ProposalInvalidError):
            mc.validate_proposal(module, forged)

    def test_specialty_finance_supported(self):
        prop = mc.route(self.reg, "specialty_crops", "finance")
        self.assertEqual(prop.status, "supported")
        caps = prop.to_dict()["proposal"]["capabilities"]
        by_profile = {c["profile"]: c["status"] for c in caps}
        self.assertEqual(
            by_profile["single_annual_cash_v1"], "supported"
        )
        self.assertEqual(by_profile["composite_multi_crop"], "unsupported")


class TestCanonicalWrites(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_proposal_never_writes_canonical(self):
        for major in ("specialty_crops", "industrial_insects"):
            for kind in mc.CAPABILITY_KINDS:
                prop = mc.route(self.reg, major, kind)
                self.assertFalse(prop.canonical_write)
                self.assertFalse(prop.to_dict()["canonical_write"])

    def test_forged_canonical_write_rejected(self):
        prop = mc.route(self.reg, "specialty_crops", "document")
        forged = replace(prop, canonical_write=True)
        module = self.reg.resolve("specialty_crops")
        with self.assertRaises(mc.ProposalInvalidError):
            mc.validate_proposal(module, forged)

    def test_canonical_view_is_deep_read_only(self):
        view = mc.canonical_view(
            {"facts": {"f1": {"state": "provided"}}, "rows": [1, {"x": 2}]}
        )
        with self.assertRaises(TypeError):
            view["facts"] = {}
        with self.assertRaises(TypeError):
            view["facts"]["f1"]["state"] = "withheld"
        with self.assertRaises(TypeError):
            view["rows"][1]["x"] = 9
        with self.assertRaises(AttributeError):
            view["rows"].append(3)

    def test_route_hands_module_read_only_canonical(self):
        prop = mc.route(
            self.reg,
            "industrial_insects",
            "question",
            canonical={"facts": {"f1": {"state": "provided"}}},
        )
        self.assertIsNotNone(prop.canonical)
        self.assertTrue(prop.to_dict()["canonical_present"])
        with self.assertRaises(TypeError):
            prop.canonical["facts"]["f1"]["state"] = "withheld"
        # No canonical handed over -> proposal records none.
        bare = mc.route(self.reg, "industrial_insects", "question")
        self.assertIsNone(bare.canonical)
        self.assertFalse(bare.to_dict()["canonical_present"])

    def test_mutable_canonical_handoff_rejected(self):
        prop = mc.route(self.reg, "specialty_crops", "document")
        forged = replace(prop, canonical={"facts": {}})
        module = self.reg.resolve("specialty_crops")
        with self.assertRaises(mc.ProposalInvalidError):
            mc.validate_proposal(module, forged)

    def test_proposal_payload_is_frozen(self):
        prop = mc.route(self.reg, "industrial_insects", "question")
        with self.assertRaises(TypeError):
            prop.proposal["fields"] = []


class TestEvidenceAxes(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.ii = self.reg.resolve("industrial_insects")
        self.good = dict(
            acquisition="bytes_held",
            observation="verified_observation",
            rights="redistribution_confirmed",
            conflict="none",
            applicability="applicable",
            approval="unapproved",
        )

    def axes(self, **kw):
        args = dict(self.good)
        args.update(kw)
        return mc.evidence_axes(**args)

    def test_all_clear_is_approval_candidate(self):
        axes = self.axes()
        self.assertEqual(mc.promotion_blockers(axes), ())
        self.assertTrue(mc.approval_candidate(axes))

    def test_each_axis_blocks(self):
        cases = {
            "acquisition": "link_only",
            "observation": "quarantined",
            "rights": "unconfirmed",
            "conflict": "unresolved",
            "applicability": "unverified",
        }
        for axis, bad in cases.items():
            axes = self.axes(**{axis: bad})
            self.assertIn(axis, mc.promotion_blockers(axes))
            self.assertFalse(mc.approval_candidate(axes))

    def test_source_conflict_is_conflict_axis_not_observation(self):
        axes = self.axes(conflict="source_conflict")
        self.assertEqual(axes["conflict"], "unresolved")
        self.assertEqual(axes["observation"], "verified_observation")
        self.assertIn("conflict", mc.promotion_blockers(axes))

    def test_audit_key_confers_no_bypass(self):
        key = {
            "pack_id": SPECIALTY_PACK,
            "records_file_sha256": "ab" * 32,
            "physical_jsonl_line_1based": 1,
            "raw_line_sha256": "cd" * 32,
        }
        axes = self.axes(observation="quarantined")
        self.assertEqual(
            mc.promotion_blockers(axes), mc.promotion_blockers(axes, audit_key=key)
        )
        self.assertFalse(mc.approval_candidate(axes, audit_key=key))

    def test_invalid_axis_value_rejected(self):
        with self.assertRaises(ValueError):
            self.axes(observation="probably_fine")

    def test_applicability(self):
        self.assertEqual(
            mc.evaluate_applicability(
                self.ii, {"major_id": "specialty_crops"}
            ),
            "inapplicable",
        )
        self.assertEqual(
            mc.evaluate_applicability(
                self.ii,
                {"major_id": None, "use_scope": "industry_context"},
            ),
            "unverified",
        )
        self.assertEqual(
            mc.evaluate_applicability(
                self.ii,
                {
                    "major_id": "industrial_insects",
                    "use_scope": "industry_context",
                },
            ),
            "applicable",
        )
        self.assertEqual(
            mc.evaluate_applicability(
                self.ii,
                {
                    "major_id": "industrial_insects",
                    "use_scope": "yield_per_farm",
                },
            ),
            "unverified",
        )


class TestProjectBinding(unittest.TestCase):
    """binding_from_project: the project->router link, read-only."""

    def setUp(self):
        self.reg = registry()

    def project(self, facts=None, sources=None):
        return {"facts": facts or {}, "sources": sources or {}}

    def major_fact(self, fid="f-major", value="specialty_crops", *,
                   answer_state="provided", verification="claim_supported",
                   source_refs=({"id": "s1", "locator": "line 1"},),
                   module_version="1.0.0"):
        f = {
            "field_id": mc.COMMON_MAJOR_FIELD,
            "kind": "reported_fact",
            "value": value,
            "unit": None,
            "value_type": "text",
            "period": None,
            "scope": "project",
            "answer_state": answer_state,
            "verification": verification,
            "source_refs": list(source_refs),
        }
        if module_version is not None:
            f["module_version"] = module_version
        return fid, f

    def test_binds_from_confirmed_fact(self):
        fid, fact = self.major_fact()
        p = self.project(facts={fid: fact}, sources={"s1": {"id": "s1"}})
        b = mc.binding_from_project(self.reg, p)
        self.assertEqual(b.major_id, "specialty_crops")
        self.assertEqual(b.basis, "legacy_record")
        self.assertEqual(b.binding_evidence, "facts:f-major")
        self.assertEqual(b.module_version, "1.0.0")

    def test_old_project_without_fact_never_infers_specialty(self):
        for p in (
            self.project(),
            self.project(facts={"f": self.major_fact(
                answer_state="not_provided")[1]}),
            self.project(facts={"f": self.major_fact(
                answer_state="withheld")[1]}),
            self.project(facts={"f": self.major_fact(
                answer_state="unknown")[1]}),
        ):
            with self.assertRaises(mc.MissingMajorError) as cm:
                mc.binding_from_project(self.reg, p)
            self.assertEqual(cm.exception.reason, "major_required")

    def test_duplicate_and_conflicting_facts_rejected(self):
        fid1, same1 = self.major_fact(fid="f1")
        fid2, same2 = self.major_fact(fid="f2")
        p = self.project(facts={fid1: same1, fid2: same2},
                         sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError) as cm:
            mc.binding_from_project(self.reg, p)
        self.assertIn("duplicate", str(cm.exception))
        fid3, other = self.major_fact(fid="f3",
                                      value="industrial_insects",
                                      module_version="0.1.0")
        p = self.project(facts={fid1: same1, fid3: other},
                         sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError) as cm:
            mc.binding_from_project(self.reg, p)
        self.assertIn("conflicting", str(cm.exception))

    def test_unconfirmed_verification_rejected(self):
        """Only claim_supported binds — an unverified crop hint must not
        activate the specialty adapter."""
        for v in ("unreviewed", "source_located", "disputed",
                  "superseded"):
            fid, fact = self.major_fact(verification=v)
            p = self.project(facts={fid: fact}, sources={"s1": {}})
            with self.assertRaises(mc.BindingInvalidError):
                mc.binding_from_project(self.reg, p)
        fid, fact = self.major_fact()
        del fact["verification"]
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError):
            mc.binding_from_project(self.reg, p)

    def test_source_refs_must_resolve(self):
        fid, fact = self.major_fact(source_refs=())
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError):
            mc.binding_from_project(self.reg, p)
        fid, fact = self.major_fact(
            source_refs=({"id": "ghost", "locator": "x"},))
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError) as cm:
            mc.binding_from_project(self.reg, p)
        self.assertEqual(cm.exception.detail["missing"], ["ghost"])

    def test_module_version_required_and_matching(self):
        fid, fact = self.major_fact(module_version=None)
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError):
            mc.binding_from_project(self.reg, p)
        fid, fact = self.major_fact(module_version="9.9")
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.BindingInvalidError) as cm:
            mc.binding_from_project(self.reg, p)
        self.assertEqual(cm.exception.detail["recorded"], "9.9")
        self.assertEqual(cm.exception.detail["registered"], "1.0.0")

    def test_unregistered_major_value_rejected(self):
        fid, fact = self.major_fact(value="orchids")
        p = self.project(facts={fid: fact}, sources={"s1": {}})
        with self.assertRaises(mc.UnknownMajorError):
            mc.binding_from_project(self.reg, p)

    def test_non_string_or_empty_value_rejected(self):
        for bad in (None, 7, ""):
            fid, fact = self.major_fact(value=bad)
            p = self.project(facts={fid: fact}, sources={"s1": {}})
            with self.assertRaises(mc.BindingInvalidError):
                mc.binding_from_project(self.reg, p)

    def test_read_only_leaves_project_unchanged(self):
        fid, fact = self.major_fact()
        p = self.project(facts={fid: fact}, sources={"s1": {"id": "s1"}})
        before = json.dumps(p, sort_keys=True, ensure_ascii=False)
        mc.binding_from_project(self.reg, p)
        self.assertEqual(
            before, json.dumps(p, sort_keys=True, ensure_ascii=False)
        )

    def test_real_gg_core_roundtrip(self):
        """The common.major_id fact (with module_version) is accepted by
        the existing gg_core canonical path — init/apply/load — and the
        loaded project binds through the contract."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ws"
            root.mkdir()
            gg_core.init(root)
            (root / "note.txt").write_text(
                "전공: 특용작물\n", encoding="utf-8")
            ops = [
                {"collection": "sources", "value": {
                    "id": "s1", "path": "note.txt",
                    "claims": {}, "claim_review": "synthetic"}},
                {"collection": "facts", "value": {
                    "id": "f-major", "field_id": mc.COMMON_MAJOR_FIELD,
                    "kind": "reported_fact", "value": "specialty_crops",
                    "unit": None, "value_type": "text", "period": None,
                    "scope": "project", "answer_state": "provided",
                    "verification": "claim_supported",
                    "source_refs": [{"id": "s1", "locator": "line 1",
                                     "revision": 1}],
                    "module_version": "1.0.0"}},
            ]
            gg_core.apply(root, {"request_id": "bind-1", "ops": ops}, 0)
            p = gg_core.load(root)
            b = mc.binding_from_project(self.reg, p)
            self.assertEqual(b.major_id, "specialty_crops")
            self.assertEqual(b.basis, "legacy_record")
            self.assertEqual(b.binding_evidence, "facts:f-major")


class TestPrecedents(unittest.TestCase):
    """F0 and E1–E4 are equal-rank example_observed precedents —
    no exemplar monopoly, no mandated outline."""

    def setUp(self):
        self.reg = registry()
        self.ii = self.reg.resolve("industrial_insects")
        self.sc = self.reg.resolve("specialty_crops")

    def test_equal_precedent_roster(self):
        self.assertEqual(
            {p.ref for p in self.ii.precedents},
            {"F0", "E1", "E2", "E3", "E4"},
        )
        self.assertEqual(
            {p.kind for p in self.ii.precedents}, {"example_observed"}
        )

    def test_no_f0_monopoly(self):
        prop = mc.route(self.reg, "industrial_insects", "document")
        payload = prop.to_dict()["proposal"]
        self.assertGreater(len(payload["precedents"]), 1)
        # No node cites F0 (or any single exemplar) as its sole basis —
        # nodes are informed by the equal roster, not owned by F0.
        for s in payload["sections"]:
            self.assertNotEqual(tuple(s["precedent_refs"]), ("F0",))
            self.assertTrue(
                set(s["precedent_refs"]).issubset(
                    {p["ref"] for p in payload["precedents"]}
                )
            )
            self.assertTrue(s["selectable"])

    def test_no_forced_section_count(self):
        prop = mc.route(self.reg, "industrial_insects", "document")
        payload = prop.to_dict()["proposal"]
        self.assertEqual(payload["mandated_sections"], [])
        for s in payload["sections"]:
            self.assertFalse(s["required"])
        # Contrast: the specialty path keeps its fixed legacy outline.
        sc_prop = mc.route(self.reg, "specialty_crops", "document")
        sc_payload = sc_prop.to_dict()["proposal"]
        self.assertEqual(
            len(sc_payload["mandated_sections"]),
            len(sc_payload["sections"]),
        )

    def test_node_rationale_and_transform_slot(self):
        prop = mc.route(self.reg, "industrial_insects", "document")
        for s in prop.to_dict()["proposal"]["sections"]:
            self.assertIsInstance(s["rationale"], str)
            self.assertIn("transform_reason", s)

    def test_undeclared_precedent_ref_rejected(self):
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={},
                precedents=(mc.PrecedentSpec("F0"),),
                document_plan=(
                    mc.DocumentNodeSpec(
                        "x", "role", precedent_refs=("E9",)
                    ),
                ),
            )

    def test_mixed_precedent_rank_rejected(self):
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={},
                precedents=(
                    mc.PrecedentSpec("F0"),
                    mc.PrecedentSpec("E1", kind="mandatory_template"),
                ),
            )

    def test_duplicate_precedent_rejected(self):
        with self.assertRaises(mc.DeclarationInvalidError):
            mc.declare_module(
                major_id="industrial_insects",
                module_version="9.9",
                capabilities={},
                precedents=(
                    mc.PrecedentSpec("F0"),
                    mc.PrecedentSpec("F0"),
                ),
            )


class TestShippedCatalog(unittest.TestCase):
    """Read-only wiring check against the real catalog."""

    def test_default_registry_binds_real_pack_ownership(self):
        catalog = PACKS_DIR / "catalog.json"
        if not catalog.exists():
            self.skipTest("catalog.json not shipped")
        reg = mc.default_registry()
        sc = reg.resolve("specialty_crops")
        self.assertIn(SPECIALTY_PACK, sc.packs)
        ii = reg.resolve("industrial_insects")
        self.assertEqual(ii.packs, ())
        fr = reg.resolve("fruit_trees")
        self.assertEqual(fr.packs, ())
        self.assertEqual(reg.major_ids,
                         ("specialty_crops", "industrial_insects",
                          "fruit_trees", "hort_env_systems"))


if __name__ == "__main__":
    unittest.main()
