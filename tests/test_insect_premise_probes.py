"""industrial_insects premise probes follow the common interview format.

The common interview redesign (owner decision C, 2026-09-25) reads a
bound major's own ``premise-probes.md`` next to ``major-plan``.  This file
locks the insect document to that format without changing QuestionSpec:
every probe links an existing insect field_id (a missing field is only a
"신설 제안" note), carries the common bullets in order, and names no other
major.
"""
import re

from tests._harness import SCRIPTS, ContractCase, runtime

DOC = (SCRIPTS.parent / "references" / "industrial-insects"
       / "premise-probes.md")
BULLETS = ("연결 field_id", "무엇을 가정하나", "영향", "트리거 신호",
           "선행 정보", "질문 예", "모를 때 도움", "조사로 대체")
IMPACTS = ("사업 성립", "현금 시점", "최대 원가·수량", "서술")
# Probes asking a decision distinct from any existing field stay proposals,
# so an answer to a neighbouring field never hides them (final review 3).
PROPOSALS = {
    "industrial_insects.probe.feed_supply": "industrial_insects.feed_supply",
    "industrial_insects.probe.capacity": "industrial_insects.rearing_capacity",
    "industrial_insects.probe.stock_source": "industrial_insects.stock_source",
    "industrial_insects.probe.claims_ad": "industrial_insects.claims_check",
}


def _link(block):
    """('existing', field_id) or ('proposal', new_field_id)."""
    line = re.search(r"^- 연결 field_id: (.+)$", block, flags=re.M).group(1)
    m = re.match(r"신설 제안: (industrial_insects\.[a-z0-9_]+) — \S", line)
    if m:
        return ("proposal", m.group(1))
    m = re.match(r"(industrial_insects\.[a-z0-9_]+)(?:\s|$)", line)
    return ("existing", m.group(1)) if m else None


class InsectPremiseProbeTests(ContractCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")
        module = runtime("gg_major_contract").default_registry().resolve(
            "industrial_insects")
        self.module = module
        self.fields = {q.field_id for q in module.question_schema}
        parts = re.split(r"^## ", self.text, flags=re.M)
        self.header, self.probes = parts[0], parts[1:]

    def test_header_names_owner_version_and_module(self):
        self.assertTrue(self.header.startswith("# 산업곤충전공 핵심 전제 목록"))
        m = re.search(r"소유 전공: (\S+) · 문서 버전: (\d{4}-\d{2}-\d{2}\.\d+)"
                      r" · 기준 module_version: (\S+)", self.header)
        self.assertIsNotNone(m)
        self.assertEqual("industrial_insects", m.group(1))
        self.assertEqual(self.module.module_version, m.group(3))

    def test_every_probe_uses_the_common_bullets_and_existing_fields(self):
        self.assertGreaterEqual(len(self.probes), 1)
        ids = set()
        for block in self.probes:
            title = block.splitlines()[0]
            m = re.match(r"(industrial_insects\.probe\.[a-z0-9_]+) — \S", title)
            self.assertIsNotNone(m, title)
            self.assertNotIn(m.group(1), ids)
            ids.add(m.group(1))
            labels = re.findall(r"^- ([^:]+):", block, flags=re.M)
            self.assertEqual(list(BULLETS), labels, title)
            self.assertIsNotNone(_link(block), title)
            impact = re.search(r"^- 영향: (.+)$", block, flags=re.M).group(1)
            self.assertTrue(any(impact.startswith(i) for i in IMPACTS), title)
            sub = re.search(r"^- 조사로 대체: (\S+)", block, flags=re.M).group(1)
            self.assertIn(sub, ("예", "아니오", "부분"), title)

    def test_no_other_major_and_no_student_values(self):
        for word in ("specialty_crops", "fruit_trees", "특용작물", "과수",
                     "원예"):
            self.assertNotIn(word, self.text)
        self.assertIsNone(re.search(r"\d{8,}", self.text))

    def _by_id(self):
        return {block.splitlines()[0].split(" — ")[0]: _link(block)
                for block in self.probes}

    def test_links_are_one_decision_each(self):
        links = self._by_id()
        existing = [f for kind, f in links.values() if kind == "existing"]
        for field in existing:
            self.assertIn(field, self.fields)
        self.assertEqual(len(existing), len(set(existing)),
                         "two probes share one existing field")
        proposals = {pid: f for pid, (kind, f) in links.items()
                     if kind == "proposal"}
        self.assertEqual(PROPOSALS, proposals)
        for field in proposals.values():
            self.assertNotIn(field, self.fields)

    def test_neighbouring_answers_do_not_hide_split_probes(self):
        core = runtime("gg_core")
        answered = ("industrial_insects.facility_area",
                    "industrial_insects.stocking_input",
                    "industrial_insects.feed_or_substrate",
                    "industrial_insects.regulation_check")
        project = {"facts": {
            "f%d" % i: {"field_id": f, "answer_state": "provided",
                        "verification": "claim_supported"}
            for i, f in enumerate(answered)}, "questions": {}}
        links = self._by_id()
        for pid in PROPOSALS:
            kind, field = links[pid]
            self.assertEqual("proposal", kind)
            self.assertNotEqual("reuse", core.question(project, field), pid)
        for pid, (kind, field) in links.items():
            if kind == "existing" and field in answered:
                self.assertNotIn(pid, PROPOSALS)
