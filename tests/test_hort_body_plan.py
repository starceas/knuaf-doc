"""원예 본문 집필 순서표가 HT2 목차 계약·질문·근거·부록 시트를 빠짐없이 덮는지 검사한다.

`references/hort-env-systems/body-plan.md`는 사람이 읽는 작업 순서표다.
정본은 `profile.json`(HT2 절·부록)과 `gg_major_contract`(질문·근거 범위)이며,
표가 정본 노드를 빠뜨리거나 없는 field_id·use_scope·시트를 지어내면 실패한다.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
import gg_major_contract as mc  # noqa: E402

HORT = ROOT / "skills/knuaf-doc/references/hort-env-systems"
PLAN = HORT / "body-plan.md"
PROFILE = HORT / "profile.json"

# 절별 연결(질문·근거·시트)을 행마다 고정한다. 선언 집합의 부분집합 검사만
# 으로는 연결을 전부 "—"로 지워도(공집합) 통과하므로, 기존 필수 연결의
# 누락을 별도로 잠근다. 값이 바뀌면 정본과 이 표를 함께 고친다.
EXPECTED_LINKS = (
    ("ch1", "business_type, startup_type, crop_item", "industry_context", "—"),
    ("ch2", "—", "—", "—"),
    ("ch2_sec1", "region, site_area, facility_type, facility_area, cultivation_area, startup_type", "—", "—"),
    ("ch2_sec2", "crop_item, cultivar, product_unit, marketable_rate", "industry_context", "—"),
    ("ch2_sec3", "cultivation_system, crop_cycle, environment_control, water_energy_source", "technical_reference, facility_spec_registry", "—"),
    ("ch3", "—", "—", "—"),
    ("ch3_sec1", "labor_plan, funding_plan", "—", "1, 17"),
    ("ch3_sec2", "region, disaster_design_basis, channel, price_basis, growth_assumption", "disaster_design_standard, income_comparison, industry_context", "7"),
    ("ch3_sec3", "—", "industry_context", "3"),
    ("ch3_sec4", "—", "—", "—"),
    ("ch4", "—", "—", "—"),
    ("ch4_sec1", "crop_item, business_type", "—", "—"),
    ("ch4_sec2", "growth_assumption, fiscal_year_mapping", "income_comparison", "2"),
    ("ch4_sec3", "labor_plan, funding_plan, environment_control, channel", "—", "—"),
    ("ch5", "—", "—", "—"),
    ("ch5_sec1", "facility_quote, funding_plan, workbook_selection", "—", "4, 5"),
    ("ch5_sec2", "facility_type, facility_area, cultivation_area, disaster_design_basis, environment_control, water_energy_source, facility_quote", "disaster_design_standard, facility_spec_registry, technical_reference", "6"),
    ("ch5_sec3", "yield_basis, marketable_rate, crop_cycle, fiscal_year_mapping", "income_comparison", "8, 10"),
    ("ch5_sec4", "workbook_selection", "—", "9, 11, 12, 13, 14"),
    ("ch5_sec5", "channel, price_basis, product_unit", "industry_context", "7"),
    ("ch5_sec6", "—", "—", "—"),
    ("ch6", "—", "—", "—"),
    ("back_references", "—", "—", "—"),
    ("back_ack", "—", "—", "—"),
    ("back_consent", "—", "—", "—"),
)


def _tables(text):
    """Markdown 표를 (header, rows) 목록으로 돌려준다."""
    tables, header, rows = [], None, []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if header is None:
                header = cells
            else:
                rows.append(cells)
        elif header is not None:
            tables.append((header, rows))
            header, rows = None, []
    if header is not None:
        tables.append((header, rows))
    return tables


class BodyPlanTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PLAN.read_text(encoding="utf-8")
        cls.profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        cls.ht2 = next(p for p in cls.profile["precedents"]
                       if p["sha256"] == cls.profile["precedents"][1]["sha256"])
        cls.module = mc.HORT_ENV_SYSTEMS_MODULE
        cls.declared = {q.field_id for q in cls.module.question_schema}
        cls.scopes = set(cls.module.evidence_applicability["use_scopes"])
        cls.sheets = {m["no"]: m["sheet"]
                      for m in cls.profile["workbook_reference"]["appendix_map"]}
        cls.tables = _tables(cls.text)

    def _table(self, marker):
        for header, rows in self.tables:
            if marker in header:
                return header, rows
        self.fail("표를 찾지 못함: " + marker)

    def test_every_ht2_node_once_with_matching_page(self):
        header, rows = self._table("HT2 노드")
        self.assertEqual(header, ["단계", "HT2 노드", "HT2 인쇄쪽", "절",
                                  "질문", "근거", "시트"])
        items = [row[1] for row in rows]
        expected = [s["item"] for s in self.ht2["sections"]]
        self.assertEqual(sorted(items), sorted(expected))
        self.assertEqual(len(items), len(set(items)))
        pages = {s["item"]: s["printed_page"] for s in self.ht2["sections"]}
        titles = {s["item"]: s["title"] for s in self.ht2["sections"]}
        for row in rows:
            with self.subTest(node=row[1]):
                self.assertEqual(int(row[2]), pages[row[1]])
                self.assertEqual(row[3], titles[row[1]])

    def test_questions_and_scopes_are_declared(self):
        _, rows = self._table("HT2 노드")
        for row in rows:
            with self.subTest(node=row[1]):
                for token in (c.strip() for c in row[4].split(",")):
                    if token and token != "—":
                        self.assertIn("hort_env_systems." + token, self.declared)
                for token in (c.strip() for c in row[5].split(",")):
                    if token and token != "—":
                        self.assertIn(token, self.scopes)

    def test_sheet_references_exist(self):
        _, rows = self._table("HT2 노드")
        for row in rows:
            with self.subTest(node=row[1]):
                for token in (c.strip() for c in row[6].split(",")):
                    if token and token != "—":
                        self.assertIn(int(token), self.sheets)

    def test_every_connection_cell_is_pinned(self):
        _, rows = self._table("HT2 노드")
        self.assertEqual(
            {row[1]: (row[4], row[5], row[6]) for row in rows},
            {item: (questions, scopes, sheets)
             for item, questions, scopes, sheets in EXPECTED_LINKS})

    def test_appendix_table_covers_all_sheets(self):
        _, rows = self._table("HT2 제목")
        self.assertEqual([int(r[0]) for r in rows], sorted(self.sheets))
        for no, title, sheet in ((int(r[0]), r[1], r[2]) for r in rows):
            with self.subTest(appendix=no):
                self.assertEqual(sheet, self.sheets[no].strip())
                self.assertTrue(title.startswith("부록 %d." % no))
        expected = {a["title"].strip(): a["no"] for a in self.ht2["appendices"]}
        self.assertEqual({r[1]: int(r[0]) for r in rows}, expected)

    def test_financial_placeholders_marked(self):
        self.assertIn("[확인 필요 — 엑셀 작성 후]", self.text)

    def test_financial_scope_names_every_placeholder_target(self):
        # 본문 재무 제한 문단이 통째로 사라져도 부록 자리 표시 문장이 남아
        # 이전 시험은 통과했다. 대상 목록 자체를 고정한다.
        scope = next(line for line in self.text.splitlines()
                     if "대상:" in line)
        for token in ("요약", "Ⅲ-1-나", "Ⅳ-2", "Ⅴ-1", "Ⅴ-3", "Ⅴ-4", "부록 1–18"):
            self.assertIn(token, scope)

    def test_no_forbidden_terms_or_author_names(self):
        for term in self.module.forbidden_terms:
            self.assertNotIn(term, self.text)
        authors = {p["author"] for p in self.profile["precedents"]}
        for author in authors:
            self.assertNotIn(author, self.text)
        self.assertNotIn("작성자 실명", self.text)


if __name__ == "__main__":
    unittest.main()
