"""industrial_insects premise probes follow the common interview format.

The common interview (owner decision C, 2026-09-25) reads a bound
major's own ``premise-probes.md`` next to ``major-plan``.  This file locks
the insect document (module 0.2.0) to that format: every probe links one
declared insect field (a line template counts), probes linked to a line
template name their instance source, no field carries two probes, no
"신설 제안" remains, and no other major is named.
"""
import re

from tests._harness import SCRIPTS, ContractCase, runtime

DOC = (SCRIPTS.parent / "references" / "industrial-insects"
       / "premise-probes.md")
BULLETS = ("연결 field_id", "무엇을 가정하나", "영향", "트리거 신호",
           "선행 정보", "질문 예", "모를 때 도움", "조사로 대체")
IMPACTS = ("사업 성립", "현금 시점", "최대 원가·수량", "서술")
TEMPLATE = "industrial_insects.line.{id}."


def _link(block):
    return re.search(r"^- 연결 field_id: (\S+)\s*$", block,
                     flags=re.M).group(1)


class InsectPremiseProbeTests(ContractCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")
        self.module = runtime("gg_major_contract").default_registry(
            ).resolve("industrial_insects")
        self.fields = {q.field_id for q in self.module.question_schema}
        parts = re.split(r"^## ", self.text, flags=re.M)
        self.header, self.probes = parts[0], parts[1:]

    def test_header_names_owner_version_and_module(self):
        self.assertTrue(self.header.startswith("# 산업곤충전공 핵심 전제 목록"))
        m = re.search(r"소유 전공: (\S+) · 문서 버전: (\d{4}-\d{2}-\d{2}\.\d+)"
                      r" · 기준 module_version: (\S+)", self.header)
        self.assertIsNotNone(m)
        self.assertEqual("industrial_insects", m.group(1))
        self.assertEqual(self.module.module_version, m.group(3))

    def test_every_probe_uses_the_common_bullets(self):
        self.assertGreaterEqual(len(self.probes), 1)
        ids = set()
        for block in self.probes:
            title = block.splitlines()[0]
            with self.subTest(case=title):
                m = re.match(r"(industrial_insects\.probe\.[a-z0-9_]+) — \S",
                             title)
                self.assertIsNotNone(m)
                self.assertNotIn(m.group(1), ids)
                ids.add(m.group(1))
                labels = [label for label in
                          re.findall(r"^- ([^:]+):", block, flags=re.M)
                          if label != "인스턴스 출처"]
                self.assertEqual(list(BULLETS), labels)
                impact = re.search(r"^- 영향: (.+)$", block,
                                   flags=re.M).group(1)
                self.assertTrue(any(impact.startswith(i) for i in IMPACTS))
                sub = re.search(r"^- 조사로 대체: (\S+)", block,
                                flags=re.M).group(1)
                self.assertIn(sub, ("예", "아니오", "부분"))

    def test_links_are_declared_fields_one_probe_each(self):
        links = [_link(block) for block in self.probes]
        for field in links:
            with self.subTest(case=field):
                self.assertIn(field, self.fields)
        self.assertEqual(len(links), len(set(links)),
                         "two probes share one field")
        self.assertNotIn("신설 제안", self.text)

    def test_template_probes_name_their_instance_source(self):
        for block in self.probes:
            field = _link(block)
            has_source = re.search(r"^- 인스턴스 출처: .*insect-plan", block,
                                   flags=re.M) is not None
            with self.subTest(case=field):
                self.assertEqual(field.startswith(TEMPLATE), has_source)

    def test_probe_mapping_matches_design_table(self):
        """Design B2 §5 + §11 + §12: probe id -> linked field, exactly."""
        expected = {
            "product_lines": "industrial_insects.line_inventory",
            "plan_years": "industrial_insects.plan_years",
            "feed_supply": TEMPLATE + "feed_supply",
            "survival": TEMPLATE + "survival_rate",
            "survival_basis": TEMPLATE + "survival_basis",
            "capacity_boxes": TEMPLATE + "rearing_boxes",
            "capacity_tiers": TEMPLATE + "box_tiers",
            "capacity_density": TEMPLATE + "density_per_box",
            "cycle_year": TEMPLATE + "cycles_per_year",
            "first_sale": TEMPLATE + "first_sale_month",
            "first_year": TEMPLATE + "first_year_sold_cycles",
            "year_end_wip": TEMPLATE + "year.{yyyy}.year_end_in_process",
            "stock_source": TEMPLATE + "stock_source",
            "use_law": TEMPLATE + "purpose",
            "processing_permit": "industrial_insects.regulation_check",
            "claims_ad": "industrial_insects.claims_check",
            "price_basis": TEMPLATE + "price_basis",
            "unit_price": TEMPLATE + "unit_price",
        }
        actual = {}
        for block in self.probes:
            pid = re.match(r"industrial_insects\.probe\.([a-z0-9_]+) — ",
                           block.splitlines()[0]).group(1)
            actual[pid] = _link(block)
        self.assertEqual(expected, actual)

    def test_capacity_and_survival_are_separate_decisions(self):
        links = {_link(block) for block in self.probes}
        for name in ("rearing_boxes", "box_tiers", "density_per_box",
                     "survival_rate", "survival_basis", "unit_price",
                     "price_basis", "first_year_sold_cycles"):
            self.assertIn(TEMPLATE + name, links)
        self.assertIn(TEMPLATE + "year.{yyyy}.year_end_in_process", links)

    def test_project_summary_answers_do_not_hide_line_questions(self):
        core = runtime("gg_core")
        project = {"facts": {
            "f1": {"field_id": "industrial_insects.species",
                   "answer_state": "provided",
                   "verification": "claim_supported"},
            "f2": {"field_id": "industrial_insects.facility_area",
                   "answer_state": "provided",
                   "verification": "claim_supported"}},
            "questions": {}}
        self.assertEqual("reuse",
                         core.question(project, "industrial_insects.species"))
        for name in ("species", "rearing_boxes"):
            with self.subTest(case=name):
                self.assertEqual("ask", core.question(
                    project, "industrial_insects.line.l01." + name))

    def test_no_other_major_and_no_student_values(self):
        for word in ("specialty_crops", "fruit_trees", "특용작물", "과수",
                     "원예"):
            self.assertNotIn(word, self.text)
        self.assertIsNone(re.search(r"\d{8,}", self.text))
