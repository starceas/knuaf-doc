"""통합: WP-3 ``review_plan`` → ``check``의 ``finance_plan_review`` 행.

HANDOFF-WP3-to-WP1의 처분을 잠근다.

- 통합문서 경로·해시가 맞는 ``.xlsx`` 원자료가 있으면 검사가 실제로 돈다.
- ``blocked``는 통과가 아니라 미수행이며 제출 후보를 막는다.
- ``pending``은 경고라 막지 않는다.
- 원자료가 없거나 해시가 낡으면 이 검사는 행을 만들지 않는다(기존
  ``source_hash``/``source_missing`` 검사가 그 상태를 소유한다).
"""

import sys
from pathlib import Path

from tests._harness import ContractCase, bind_major, runtime, write_text

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "knuaf-doc" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CHECK_ID = "finance_plan_review"

def _workbook(path, *, profit=(-10, -5, 0, 5, 10), sales=(100, 200, 300, 400, 500),
              cash=(0, 0, 0, 0, 0)):
    from openpyxl import Workbook

    years = (2027, 2028, 2029, 2030, 2031)
    wb = Workbook()
    assets = wb.active
    assets.title = "1. 기초재무상태조사"
    assets["B6"] = "(1) 토지"
    assets["B36"] = "(6) 현금 및 준현금"
    assets["E39"] = 1000

    plan = wb.create_sheet("12 손익계획")
    plan["B4"] = "과목"
    for offset, year in enumerate(years):
        plan.cell(4, 3 + offset, "%d년" % year)
    plan["B6"] = "I. 매출액"
    plan["B26"] = "X. 당기순이익"
    for offset in range(len(years)):
        plan.cell(6, 3 + offset, sales[offset])
        plan.cell(26, 3 + offset, profit[offset])

    flow = wb.create_sheet("14. 현금흐름계획")
    flow["B5"] = "구  분"
    for offset, year in enumerate(years):
        flow.cell(6, 4 + offset, "%d년" % year)
    flow["B24"] = "누적수지균형(D)\n(D=전년도D+금년도C)"
    for offset in range(len(years)):
        flow.cell(24, 4 + offset, cash[offset])

    wb.save(path)
    wb.close()

class FinancePlanReviewTests(ContractCase):
    def _project(self, *, workbook=True, mutate=None, stale=False,
                 profit=(-10, -5, 0, 5, 10)):
        core = runtime("gg_core")
        root = self.make_project()
        bind_major(root)
        if workbook:
            path = write_text(root, "finance/plan.xlsx", "")
            _workbook(path, profit=profit)
            if mutate:
                mutate(path)
            source = {"collection": "sources", "value": {
                "id": "plan", "path": "finance/plan.xlsx"}}
            core.apply(root, {"request_id": "integ-plan-source",
                              "ops": [source]}, core.load(root)["revision"])
            if stale:
                path.write_bytes(path.read_bytes() + b"")
                path.touch()
                write_text(root, "finance/plan.xlsx", "변조된 바이트")
        return core, root

    def _rows(self, report):
        return [r for r in report if r["check_id"] == CHECK_ID]

    def _codes(self, rows):
        return {r["evidence"][0].get("code") for r in rows if r.get("evidence")}

    def test_deficit_plan_reports_a_failing_row_that_blocks(self):
        core, root = self._project(profit=(-30, -20, -10, -5, 0))
        p = core.load(root)
        rows = self._rows(core.checks(root, p))
        self.assertTrue(rows, "finance_plan_review 행이 없음")
        self.assertTrue(all(r["target"] == "finance" for r in rows))
        deficit = [r for r in rows
                   if r["evidence"][0].get("code") == "PLAN_ALL_YEARS_DEFICIT"]
        self.assertEqual(1, len(deficit))
        self.assertEqual("fail", deficit[0]["status"])
        self.assertEqual("error", deficit[0]["severity"])
        self.assertTrue(core.blocks_skill_candidate(deficit[0]))

    def test_unreadable_profit_is_blocked_not_a_pass(self):
        core, root = self._project(
            mutate=lambda path: self._drop_profit_cache(path))
        p = core.load(root)
        rows = self._rows(core.checks(root, p))
        blocked = [r for r in rows if r["status"] == "blocked"]
        self.assertEqual(
            {"PLAN_NET_PROFIT_UNREADABLE"},
            {r["evidence"][0]["code"] for r in blocked})
        self.assertTrue(all(core.blocks_skill_candidate(r) for r in blocked))
        self.assertTrue(all(r.get("recovery") for r in blocked))
        # 미수행을 통과로 소비하지 않는다: 같은 검사에 pass 행이 없다.
        self.assertNotIn("pass", {r["status"] for r in rows})

    def test_undeclared_opening_assets_warns_without_blocking(self):
        core, root = self._project()
        p = core.load(root)
        rows = [r for r in self._rows(core.checks(root, p))
                if r["evidence"][0].get("code") == "OPENING_ASSETS_UNDECLARED"]
        self.assertEqual(1, len(rows))
        self.assertEqual("pending", rows[0]["status"])
        self.assertEqual("warning", rows[0]["severity"])
        self.assertFalse(core.blocks_skill_candidate(rows[0]))

    def test_no_current_workbook_source_makes_no_row(self):
        core, root = self._project(stale=True)
        p = core.load(root)
        self.assertEqual([], self._rows(core.checks(root, p)))
        self.assertIn("source_hash",
                      {r["check_id"] for r in core.checks(root, p)})

    def test_project_without_workbook_keeps_checks_unchanged(self):
        core, root = self._project(workbook=False)
        p = core.load(root)
        self.assertEqual([], self._rows(core.checks(root, p)))

    @staticmethod
    def _drop_profit_cache(path):
        import openpyxl

        workbook = openpyxl.load_workbook(path)
        sheet = workbook["12 손익계획"]
        for offset in range(5):
            sheet.cell(26, 3 + offset, "=B6*0.1")
        workbook.save(path)
        workbook.close()
