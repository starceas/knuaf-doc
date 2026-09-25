"""hort_env_systems peer module — declaration + guard coupling tests (§9).

The horticulture-environment module is a peer like industrial_insects:
it answers question/document/evidence proposals only.  Every finance
profile is declared unsupported and the module claims no paper or
workbook output, so the common policy-B guard refuses
``school_paper``/``school_excel_workbook``/``finance_calculation`` on a
bound project (D-H1), and the cover-marker list catches a request whose
cover names 원예환경 while its explicit ID is another major (D-H5).
"""
import dataclasses
import json
from pathlib import Path
from types import MappingProxyType

from tests._harness import (
    ContractCase, bind_major, fact_op, runtime, script_source, source_op,
    write_text,
)

SPECIALTY = "specialty_crops"
INSECTS = "industrial_insects"
HORT = "hort_env_systems"

# DESIGN §4: (suffix, meaning, unit, period, target) — "—" cells are None.
EXPECTED_FIELDS = (
    ("business_type", "사업 형태(생산·육묘·체험·가공 병행 여부)",
     None, None, "business"),
    ("startup_type", "창업 유형(신규 창업·승계)", None, None, "business"),
    ("crop_item", "대상 작목(품목)", None, None, "crop"),
    ("cultivar", "품종", None, None, "crop"),
    ("product_unit", "판매 계량 단위와 단위 규격(kg·판·본·상자 등)",
     None, None, "product"),
    ("region", "사업지 시·군", None, None, "site"),
    ("site_area", "부지 면적", "㎡", None, "site"),
    ("facility_type",
     "시설 형식(단동·연동·광폭·유리/PET/PC·식물공장 등)과 내재해형 등록 규격명",
     None, None, "facility"),
    ("facility_area", "시설(온실) 면적", "㎡", None, "facility"),
    ("cultivation_area", "실제 재배 면적", "㎡", None, "facility"),
    ("disaster_design_basis", "사업지 적설심·풍속 기준 확인 여부와 출처",
     None, "date", "facility"),
    ("cultivation_system",
     "재배 방식(토경·고형배지 수경·NFT·담액·분무경·고설 등)",
     None, None, "cultivation"),
    ("environment_control",
     "환경제어·에너지 설비 범위(난방·냉방·커튼·CO2·양액 등)",
     None, None, "facility"),
    ("water_energy_source", "용수 수원과 에너지원", None, None, "facility"),
    ("crop_cycle", "작기(정식~종료 월)와 연간 작기 수",
     None, None, "cultivation"),
    ("fiscal_year_mapping",
     "작기와 회계연도(표 기준 축) 대응·첫해 부분년 처리",
     None, None, "cultivation"),
    ("yield_basis", "단위면적당 생산량과 그 근거(조사값·견적·실측)",
     None, None, "production"),
    ("marketable_rate", "상품화율", "%", None, "production"),
    ("growth_assumption", "연도별 생산·가격 변화 가정과 근거",
     "%", None, "production"),
    ("channel", "판로·거래 단계", None, None, "market"),
    ("price_basis", "가격 근거(거래 단계·단위·조사 시점)",
     None, "date", "market"),
    ("facility_quote", "시설·설비 견적 근거", None, "date", "finance"),
    ("funding_plan", "자기자본·융자·보조 구성과 조건",
     None, None, "finance"),
    ("labor_plan", "자가·고용 노동 계획", None, None, "finance"),
    ("workbook_selection",
     "재무 참조 엑셀 선택(전공 교재 18시트 / 공용 17시트 / 기타)",
     None, None, "finance"),
)

# DESIGN §5 order (education_service observed in HT1 and HT2; cost_plan
# in HT2 only; experience_processing/succession from 2차 관찰).
EXPECTED_SECTIONS = (
    ("preface", ("HT1", "HT2")),
    ("farm_status", ("HT1", "HT2")),
    ("environment_analysis", ("HT1", "HT2")),
    ("vision_goals", ("HT1", "HT2")),
    ("investment_repayment", ("HT1", "HT2")),
    ("facility_plan", ("HT1", "HT2")),
    ("production_plan", ("HT1", "HT2")),
    ("sales_marketing", ("HT1", "HT2")),
    ("cost_plan", ("HT2",)),
    ("education_service", ("HT1", "HT2")),
    ("experience_processing", ()),
    ("succession", ()),
    ("closing", ("HT1", "HT2")),
    ("references", ("HT1", "HT2")),
    ("appendix_workbook_tables", ("HT1", "HT2")),
)


def _strings(value, skip_fields=()):
    """Every string inside a declaration/proposal structure.

    ``skip_fields`` names dataclass attributes whose contents are
    excluded (e.g. ``forbidden_terms`` itself, which lists the terms)."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, (dict, MappingProxyType)):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
        return
    if isinstance(value, (tuple, list, frozenset)):
        for item in value:
            yield from _strings(item)
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            if f.name in skip_fields:
                continue
            yield from _strings(getattr(value, f.name))


def _keys(value):
    """Every mapping key anywhere inside a structure."""
    if isinstance(value, (dict, MappingProxyType)):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _keys(v)
    elif isinstance(value, (tuple, list, frozenset)):
        for item in value:
            yield from _keys(item)


def _computed_hits(value, hits):
    """Mirror of the contract's computed-key scan for finance payloads."""
    if isinstance(value, (dict, MappingProxyType)):
        items = value.items()
    elif isinstance(value, (tuple, list)):
        items = enumerate(value)
    else:
        return
    for k, v in items:
        if isinstance(k, str) and k in ("calculated", "computed", "result"):
            if v:
                hits.append(k)
        _computed_hits(v, hits)


class DeclarationTests(ContractCase):
    """§9 선언 row: registration, resolve, and exact field values."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.registry = self.mc.default_registry()
        self.module = self.registry.resolve(HORT)

    def test_registered_and_resolves_with_exact_declaration_values(self):
        mc, module = self.mc, self.module
        self.assertIs(module, mc.HORT_ENV_SYSTEMS_MODULE)
        self.assertIn(HORT, self.registry.major_ids)
        self.assertEqual(module.major_id, HORT)
        self.assertEqual(module.field_namespace, HORT)
        self.assertEqual(module.module_version, "0.1.0")
        self.assertEqual(module.contract_version, mc.CONTRACT_VERSION)
        self.assertEqual(dict(module.capabilities), {
            "question": "supported",
            "document": "supported",
            "evidence": "supported",
            "finance": "unsupported",
        })
        self.assertEqual(module.supported_outputs,
                         ("question_list", "document_plan",
                          "evidence_review"))
        self.assertEqual(
            module.forbidden_terms,
            ("곤충", "사육", "종충", "동애등에", "귀뚜라미", "특용작물"))
        self.assertEqual(
            module.validation_rules,
            ("namespace_fields", "no_foreign_pack_refs",
             "forbidden_terms_absent", "no_workbook_output",
             "no_hort_calculation"))
        # Equal-rank roster: HT1/HT2 share one kind; packs stay empty.
        self.assertEqual(
            [(p.ref, p.kind) for p in module.precedents],
            [("HT1", "example_observed"), ("HT2", "example_observed")])
        self.assertEqual(module.packs, ())
        self.assertEqual(module.pack_refs, ())
        self.assertEqual(module.imports, ())
        # Evidence applicability: six scopes, unverified common policy,
        # five required axes.
        self.assertEqual(dict(module.evidence_applicability), {
            "use_scopes": (
                "disaster_design_standard", "facility_spec_registry",
                "technical_reference", "income_comparison",
                "useful_life_reference", "industry_context",
            ),
            "common_pack_policy": "unverified",
            "required_axes": (
                "acquisition", "observation", "rights", "conflict",
                "applicability",
            ),
        })
        # All three finance profiles are declared unsupported with a
        # reason — the module computes nothing (D-H1).
        self.assertEqual(
            [c.profile for c in module.finance_capabilities],
            ["hort_18_sheet_workbook_v1", "single_annual_cash_v1",
             "multi_cycle_facility_cash"])
        for cap in module.finance_capabilities:
            with self.subTest(profile=cap.profile):
                self.assertEqual(cap.status, "unsupported")
                self.assertTrue(cap.reason)

    def test_question_schema_is_25_namespaced_unique_fields(self):
        fields = self.module.question_schema
        self.assertEqual(len(fields), 25)
        self.assertEqual(
            [(q.field_id, q.meaning, q.unit, q.period, q.target)
             for q in fields],
            [("hort_env_systems." + suffix, meaning, unit, period, target)
             for suffix, meaning, unit, period, target in EXPECTED_FIELDS],
        )
        self.assertEqual(len({q.field_id for q in fields}), len(fields))
        for q in fields:
            self.assertTrue(q.field_id.startswith("hort_env_systems."))

    def test_document_plan_is_15_selectable_optional_nodes(self):
        nodes = self.module.document_plan
        self.assertEqual(
            [(n.section_id, n.precedent_refs) for n in nodes],
            list(EXPECTED_SECTIONS))
        roster = {p.ref for p in self.module.precedents}
        for n in nodes:
            with self.subTest(section=n.section_id):
                self.assertTrue(n.selectable)
                self.assertFalse(n.required)
                self.assertEqual(n.role, n.section_id)
                self.assertTrue(n.rationale)
                self.assertLessEqual(set(n.precedent_refs), roster)
                if n.precedent_refs:
                    self.assertIsNone(n.transform_reason)
                else:
                    self.assertIsNotNone(n.transform_reason)

    def test_declaration_strings_carry_no_forbidden_terms(self):
        """Every string in the declaration is clean — validate_proposal
        scans payloads as substrings, so the sources must be clean too."""
        for term in self.module.forbidden_terms:
            for text in _strings(self.module, skip_fields=("forbidden_terms",)):
                with self.subTest(term=term, text=text):
                    self.assertNotIn(term, text)

    def test_no_peer_module_references_in_declaration_block(self):
        """The HORT block in gg_major_contract.py cites no other major's
        module identifier or field namespace (peer rule, §9 선언 row)."""
        source = script_source("gg_major_contract.py")
        start = source.index("HORT_ENV_SYSTEMS_MODULE = declare_module(")
        end = source.index("\nMODULES =")
        block = source[start:end]
        for foreign in (
            "SPECIALTY_CROPS_MODULE", "INDUSTRIAL_INSECTS_MODULE",
            "specialty_crops.", "industrial_insects.",
        ):
            self.assertNotIn(foreign, block)


class ProposalTests(ContractCase):
    """§9 선언 row: all four capability proposals validate cleanly and
    carry no forbidden term, workbook claim, or computed value."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.registry = self.mc.default_registry()
        self.module = self.registry.resolve(HORT)
        self.proposals = self.mc.capability_proposals(self.registry, HORT)

    def _payload(self, capability):
        return self.proposals[capability].to_dict()["proposal"]

    def test_four_proposals_validate_and_statuses_match(self):
        self.assertEqual(set(self.proposals),
                         set(self.mc.CAPABILITY_KINDS))
        for kind, proposal in self.proposals.items():
            with self.subTest(capability=kind):
                self.assertIsInstance(proposal, self.mc.ModuleProposal)
                self.assertEqual(proposal.major_id, HORT)
                self.assertFalse(proposal.canonical_write)
                expected = (
                    "unsupported" if kind == "finance" else "supported")
                self.assertEqual(proposal.status, expected)

    def test_no_forbidden_term_in_any_proposal(self):
        for kind, proposal in self.proposals.items():
            blob = json.dumps(proposal.to_dict()["proposal"],
                              ensure_ascii=False)
            for term in self.module.forbidden_terms:
                with self.subTest(capability=kind, term=term):
                    self.assertNotIn(term, blob)

    def test_question_proposal_lists_the_25_fields(self):
        payload = self._payload("question")
        self.assertEqual(len(payload["fields"]), 25)
        self.assertEqual(
            payload["answer_states"], sorted(self.mc.ANSWER_STATES))
        for field in payload["fields"]:
            self.assertTrue(
                field["field_id"].startswith("hort_env_systems."))

    def test_document_proposal_has_no_workbook_claim(self):
        payload = self._payload("document")
        self.assertEqual(len(payload["sections"]), 15)
        self.assertEqual(payload["mandated_sections"], [])
        self.assertEqual(payload["precedents"], [
            {"ref": "HT1", "kind": "example_observed"},
            {"ref": "HT2", "kind": "example_observed"},
        ])
        keys = set(_keys(payload))
        self.assertNotIn("workbook", keys)
        self.assertNotIn("sheets", keys)

    def test_finance_proposal_has_no_computed_values(self):
        payload = self._payload("finance")
        hits = []
        _computed_hits(payload, hits)
        self.assertEqual(hits, [])
        self.assertEqual(
            [c["status"] for c in payload["capabilities"]],
            ["unsupported"] * 3)
        self.assertFalse(payload["blocks_document"])

    def test_evidence_proposal_carries_module_rules(self):
        payload = self._payload("evidence")
        # The thawed proposal holds lists; compare against a
        # JSON-normalized view of the declaration.
        expected = json.loads(
            json.dumps(dict(self.module.evidence_applicability)))
        self.assertEqual(payload["applicability"], expected)
        self.assertEqual(set(payload["axes"]),
                         set(self.mc.EVIDENCE_AXES))


class GuardCouplingTests(ContractCase):
    """§9 가드 결합 row: a bound horticulture project is refused every
    output the module does not declare — and nothing is written."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")

    def _ctx(self, root, major_id=None):
        return self.mc.output_context(root, major_id)

    def _bound_project(self):
        root = self.make_project()
        bind_major(root, HORT)
        # A hort-namespaced answer recorded through the common path.
        write_text(root, "answer.txt", "대상 작목: 완숙토마토\n")
        src = source_op("answer.txt")
        fact = fact_op("crop", "hort_env_systems.crop_item",
                       "완숙토마토", "")
        revision = self.core.load(root)["revision"]
        self.core.apply(root, {"request_id": "hort-answer",
                               "ops": [src, fact]}, revision)
        return root

    def test_bound_project_refuses_paper_workbook_and_finance(self):
        """D-H1: school_paper, school_excel_workbook and
        finance_calculation are all held as unsupported_output; the
        refusal leaves every persisted byte unchanged."""
        root = self._bound_project()
        before = self._tree_bytes(root)
        spec = {"major_id": HORT}
        for output in self.mc.OUTPUT_REQUIREMENTS:
            with self.subTest(output=output):
                with self.assertRaises(self.mc.OutputHeldError) as caught:
                    self.mc.authorize_output(
                        output, self._ctx(root), spec=spec)
                self.assertEqual(caught.exception.reason,
                                 "unsupported_output",
                                 caught.exception.detail)
        self.assertEqual(self._tree_bytes(root), before)

    def test_route_refuses_school_paper_output(self):
        registry = self.mc.default_registry()
        for capability in self.mc.CAPABILITY_KINDS:
            with self.subTest(capability=capability):
                with self.assertRaises(self.mc.UnsupportedOutputError):
                    self.mc.route(registry, HORT, capability,
                                  output="school_paper")

    def test_conflicting_cover_label_is_held_by_guard(self):
        """D-H5: a cover naming another major while the explicit ID is
        hort_env_systems is refused before any output decision."""
        root = self._bound_project()
        before = self._tree_bytes(root)
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.mc.authorize_output(
                self.mc.OUTPUT_SCHOOL_PAPER, self._ctx(root),
                spec={"major_id": HORT,
                      "school_profile": {"department": "특용작물학과"}})
        self.assertEqual(caught.exception.reason, "major_marker_conflict")
        self.assertEqual(self._tree_bytes(root), before)


class CoverMarkerTests(ContractCase):
    """§9 표지 row: "원예환경" labels map only to hort_env_systems, and a
    bare "원예과" is not a horticulture-environment marker."""

    def setUp(self):
        self.mc = runtime("gg_major_contract")

    def test_hort_cover_conflicts_with_other_explicit_ids(self):
        mc = self.mc
        self.assertEqual(
            mc.marker_conflicts(
                {"major": "원예환경시스템전공"}, SPECIALTY),
            [("spec.major", "hort_env_systems")])
        self.assertEqual(
            mc.marker_conflicts(
                {"major": "원예환경시스템학과"}, INSECTS),
            [("spec.major", "hort_env_systems")])

    def test_hort_cover_does_not_conflict_with_hort_id(self):
        mc = self.mc
        for spec in (
            {"major": "원예환경시스템전공"},
            {"major": "원예환경"},
            {"school_profile": {"major": "원예환경시스템학과"}},
        ):
            with self.subTest(spec=spec):
                self.assertEqual(mc.marker_conflicts(spec, HORT), [])

    def test_plain_horticulture_labels_are_not_markers(self):
        mc = self.mc
        for spec in (
            {"department": "원예과"},
            {"major": "원예과"},
            {"school_profile": {"department": "원예학과"}},
        ):
            with self.subTest(spec=spec):
                self.assertEqual(mc.marker_conflicts(spec, HORT), [])
                # A bare "원예" label must not read as the hort marker for
                # another major either — only "원예환경" names this major.
                self.assertNotIn(
                    HORT,
                    [m for _, m in mc.marker_conflicts(spec, "specialty_crops")])



class PremiseProbeDocumentTests(ContractCase):
    """The hort premise-probe list (common interview format, C안) links
    only to declared hort field_ids; unlinked probes are marked as a
    proposal and never silently add a QuestionSpec."""

    def test_probe_links_resolve_to_declared_fields(self):
        import re
        mc = runtime("gg_major_contract")
        declared = {q.field_id for q in mc.HORT_ENV_SYSTEMS_MODULE.question_schema}
        doc = (
            Path(mc.__file__).resolve().parents[1]
            / "references" / "hort-env-systems" / "premise-probes.md"
        ).read_text(encoding="utf-8")
        self.assertIn("소유 전공: hort_env_systems", doc)
        self.assertIn("기준 module_version: %s"
                      % mc.HORT_ENV_SYSTEMS_MODULE.module_version, doc)
        probes = re.findall(r"^## (hort_env_systems\.probe\.[a-z_]+) — ", doc, re.M)
        self.assertGreater(len(probes), 0)
        self.assertEqual(len(probes), len(set(probes)))
        links = re.findall(r"^- 연결 field_id: (.+)$", doc, re.M)
        self.assertEqual(len(links), len(probes))
        for line in links:
            with self.subTest(line=line):
                if line.startswith("신설 제안:"):
                    proposed = re.findall(r"hort_env_systems\.[a-z_]+", line)
                    self.assertTrue(proposed)
                    self.assertFalse(set(proposed) & declared)
                    continue
                ids = [x.strip() for x in line.split(",")]
                self.assertTrue(ids)
                self.assertLessEqual(set(ids), declared)
        for term in mc.HORT_ENV_SYSTEMS_MODULE.forbidden_terms:
            self.assertNotIn(term, doc)


if __name__ == "__main__":
    import unittest
    unittest.main()
