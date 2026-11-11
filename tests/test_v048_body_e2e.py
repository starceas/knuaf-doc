"""§7-1 원예·과수·특용작물 종단 간 통합 시험 (v0.4.8 e2e).

학생 실제 경로인 ``core.checks``(``gg.py check``가 부르는 함수,
``skills/knuaf-doc/scripts/gg_core.py:checks``)에서
``_finance_check_items → crosscheck_body``로 이어지는 v1 경로가,
등록된 사실과 본문 절 파일을 실제로 대조하는지.

케이스 (설계 §7-1):
- 원예: 표 전개 정상값 / ``<농장명>의 2027년 <항목>은 <값>천원이다.``
  정상값 / 값 1 틀림 / 필수 항목 본문 빠짐
- 과수: ``계획 2027 매출액은 …`` 정상값 / 값 1 틀림 / 항목 빠짐
- 특용작물: 등록된 프로젝트 재실행 (§1-1 시험 4) — 변경 전 기대값
  (``body_finance_crosscheck`` fail 0·not_checked 없음) 유지
- 공통: claims 일부 지정 → ``body_finance_crosscheck`` blocked 1행,
  reason에 ``FORBIDDEN_STUDENT_TERMS`` 0건

이 시험은 ``checks`` 결과 행만 대조한다. 성공(FAIL 0건)인 대조는
행을 내지 않으므로, "아무 일도 일어나지 않음"과 구분하기 위해
``_finance_check_items``의 items가 비어 있지 않은지, 그리고
``crosscheck_body``를 직접 호출한 결과와 checks 행이 같은 의미인지
함께 단언한다. 과수 사실이 대조 대상(eligible)에 안 들어
"대조 안 함(not_checked)"이 나오면 기대값을 맞추지 않는다 —
그 자체가 결함이므로, 그런 경우 이 시험은 FAIL로 떨어진다.
"""
import json
import unittest
from pathlib import Path

from tests._harness import (
    ContractCase,
    bind_major,
    claim_of,
    fact_op,
    runtime,
    section_claim,
    section_op,
    source_op,
    write_text,
)
from tests.test_fruit_fact_map import _Chain as _FruitChain
from tests.test_hort_fact_map import _ChainCase as _HortChainCase
from tests.test_hort_fact_map import EXPECTED_VALUES as _HORT_VALUES
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS


def _body_rows(checks_output):
    """``checks`` 결과에서 본문 재무 대조 행만 골라낸다."""
    return [
        r for r in checks_output
        if r["check_id"] in (
            "body_finance_crosscheck",
            "body_finance_crosscheck_scope",
            "body_finance_crosscheck_v2",
        )
    ]


class _E2ECase(ContractCase):
    """본문 절을 등록하고 v1 경로로 ``core.checks``를 돌리는 공통 기지."""

    def add_body(self, core, root, body_text):
        write_text(root, "body1.md", body_text)
        project = core.load(root)
        core.apply(
            root,
            {"request_id": "body",
             "ops": [section_op("body", "재무", "body1.md")]},
            project["revision"],
        )

    def check_body_rows(self, core, root):
        project = core.load(root)
        rows = _body_rows(core.checks(root, project))
        v2_rows = [r for r in rows
                   if r["check_id"] == "body_finance_crosscheck_v2"]
        # 절에 claims 목록이 없으면 v2 경로는 돌지 않는다 — 돌았으면
        # 이 시험이 어느 경로를 본 건지 고정할 수 없다.
        self.assertEqual([], v2_rows,
                         "v2 경로가 돌면 안 된다(절 claims 없음)")
        return project, rows

    def assert_pass(
            self, core, root, *, items, crosscheck_body_calls):
        """등록 사실이 대조 대상으로 들어오고 본문이 PASS인 경우."""
        self.assertTrue(items, "등록 사실이 대조 대상에 안 들었다 — 결함")
        # direct crosscheck must also see it as passing (two views agree)
        self.assertEqual([], crosscheck_body_calls)
        _, rows = self.check_body_rows(core, root)
        self.assertEqual(
            [], [r for r in rows if r["check_id"]
                 in ("body_finance_crosscheck",
                     "body_finance_crosscheck_scope")],
            "정상 본문인데 대조 행이 나왔다: %r" % (rows,))

    def assert_fail_one(self, core, root, *, fact_id):
        """값 틀림/항목 빠짐 → 해당 사실 ID로 fail 정확히 1행."""
        _, rows = self.check_body_rows(core, root)
        fails = [r for r in rows
                 if r["check_id"] == "body_finance_crosscheck"
                 and r.get("status") == "fail"]
        self.assertEqual(1, len(fails),
                         "fail 정확히 1행 기대, got: %r" % (rows,))
        self.assertEqual(fact_id, fails[0]["target"])
        self.assertEqual(
            [], [r for r in rows
                 if r["check_id"] == "body_finance_crosscheck_scope"],
            "not_checked 행이 있으면 '대조 안 함'이라 결함")


class HortEndToEndTests(_E2ECase, _HortChainCase):
    """원예: farm_name 사실 + 합성 엑셀/영수증 → 등록 → 본문 대조.

    ``_HortChainCase``의 FARM_NAME은 합성 농장명 ``합성농장``.
    """

    FIELD_ID = "총투자비 합계"
    FACT_ID = "hort-invest-total"

    def _prepare(self, body_text):
        mapping = self.single_row()
        change = self.build(mapping)
        self.core.apply(self.root, change,
                        self.core.load(self.root)["revision"])
        project = self.core.load(self.root)
        items, blocked = self.core._finance_check_items(project)
        self.assertIsNone(blocked)
        self.assertTrue(items, "원예 사실이 대조 대상에 안 들었다 — 결함")
        self.add_body(self.core, self.root, body_text)
        return items

    def _table(self, value, *, include_item=True):
        row = ("| 총투자비 합계 | %s |\n" % value) if include_item else ""
        return (
            "합성농장의 2027년 주요 재무 값은 표 1에 나타내었다.\n\n"
            "표 1. 2027년 주요 재무 값\n\n"
            "| 항목 | 2027년 |\n|---|---|\n"
            + row +
            "\n주) 단위: 천원.\n"
        )

    def _crosscheck_direct(self, items):
        body = Path(self.root, "body1.md").read_text(encoding="utf-8")
        from gg_school_excel import crosscheck_body
        return crosscheck_body(body, items)

    def test_table_body_with_registered_amount_passes(self):
        value = _HORT_VALUES[self.FACT_ID]
        items = self._prepare(self._table(value))
        project = self.core.load(self.root)
        # crosscheck_body는 보문 절 파일 본문을 직접 읽은 값을 본다
        # (title이 merged()로 붙기 전 원문 아님) — direct 호출은 파일이라
        # 같은 결과를 내야 한다.
        self.assertEqual([], self._crosscheck_direct(items))
        self.assert_pass(self.core, self.root, items=items,
                         crosscheck_body_calls=[])
        # project 재로드해도 대조 대상이 그대로
        items2, _ = self.core._finance_check_items(
            self.core.load(self.root))
        self.assertEqual([self.FACT_ID],
                         [i["fact_id"] for i in items2])

    def test_possessive_sentence_with_registered_amount_passes(self):
        """ⓑ+ⓐ 합쳐야 통과하는 사례: ``<농장명>의 2027년 …``."""
        value = _HORT_VALUES[self.FACT_ID]
        body = ("합성농장%s 2027년 총투자비 합계는 %s천원이다.\n"
                % ("의", value))
        items = self._prepare(body)
        project = self.core.load(self.root)
        self.assertEqual([], self._crosscheck_direct(items))
        self.assert_pass(self.core, self.root, items=items,
                         crosscheck_body_calls=[])

    def test_table_body_with_amount_off_by_one_fails(self):
        value = _HORT_VALUES[self.FACT_ID]
        items = self._prepare(self._table(value + 1))
        self.assertTrue(self._crosscheck_direct(items))
        self.assert_fail_one(self.core, self.root, fact_id=self.FACT_ID)

    def test_table_body_missing_required_item_fails(self):
        items = self._prepare(self._table(0, include_item=False))
        self.assertTrue(self._crosscheck_direct(items))
        self.assert_fail_one(self.core, self.root, fact_id=self.FACT_ID)


class FruitEndToEndTests(_E2ECase, _FruitChain):
    """과수: 계획 2027 매출액 — 본문 자동 대조가 실제로 도는지."""

    FACT_ID = "fruit-body-revenue-2027"

    def _register(self, value):
        caches = self.caches({})
        caches[("12.손익계획", "C6")] = (value, None)
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == self.FACT_ID]
        change = self.build(mapping)
        self.core.apply(self.root, change,
                        self.core.load(self.root)["revision"])
        project = self.core.load(self.root)
        items, blocked = self.core._finance_check_items(project)
        self.assertIsNone(blocked)
        self.assertTrue(items, "과수 사실이 대조 대상에 안 들었다 — 결함")
        return items

    def _crosscheck_direct(self, items):
        body = Path(self.root, "body1.md").read_text(encoding="utf-8")
        from gg_school_excel import crosscheck_body
        return crosscheck_body(body, items)

    def test_body_sentence_with_registered_amount_passes(self):
        items = self._register("14473.44")
        self.add_body(self.core, self.root,
                      "계획 2027 매출액은 14,473.44천원이다.\n")
        self.assertEqual([], self._crosscheck_direct(items))
        self.assert_pass(self.core, self.root, items=items,
                         crosscheck_body_calls=[])

    def test_body_sentence_with_year_suffix_passes(self):
        """W-A ⓑ: ``년`` 접미를 받아 기간 ``2027`` 과 대조한다."""
        items = self._register("14473.44")
        self.add_body(self.core, self.root,
                      "계획 2027년 매출액은 14,473.44천원이다.\n")
        self.assertEqual([], self._crosscheck_direct(items))
        self.assert_pass(self.core, self.root, items=items,
                         crosscheck_body_calls=[])

    def test_body_sentence_with_amount_off_by_one_fails(self):
        items = self._register("14473.44")
        self.add_body(self.core, self.root,
                      "계획 2027 매출액은 15,000천원이다.\n")
        self.assertTrue(self._crosscheck_direct(items))
        self.assert_fail_one(self.core, self.root, fact_id=self.FACT_ID)

    def test_body_sentence_missing_item_fails(self):
        items = self._register("14473.44")
        self.add_body(self.core, self.root,
                      "계획 2027년 다른 항목은 14,473.44천원이다.\n")
        self.assertTrue(self._crosscheck_direct(items))
        self.assert_fail_one(self.core, self.root, fact_id=self.FACT_ID)


class SpecialtyRerunTests(_E2ECase):
    """특용작물: 등록된 프로젝트 재실행 (§1-1-4).

    특용작물 A26은 ⓑ 정규식을 거치지 않는다(자체 워크북 정규식).
    ``checks`` 경로의 ``body_finance_crosscheck`` 기대값(정상 본문
    FAIL 0·not_checked 없음)이 W-A·W-B 변경 전과 같은지 고정한다.
    """

    def _register(self):
        core = runtime("gg_core")
        mc = runtime("gg_major_contract")
        facts = runtime("gg_excel_facts")
        root = self.make_project()
        bind_major(root)
        context = mc.output_context(root, "specialty_crops")
        workbook = root / "recalculated.xlsx"
        from openpyxl import Workbook
        book = Workbook()
        book.active.title = "계획"
        book.active["A1"] = "=10*25"
        book.active["B1"] = "=20*25"
        book.save(workbook)
        book.close()
        # 네이티브 캐시 250을 A1에 심는다(test_excel_fact_registration
        # 의 cache 방식과 같다)
        import zipfile
        from xml.etree import ElementTree as ET
        ns = ("http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        with zipfile.ZipFile(workbook) as archive:
            parts = {name: archive.read(name)
                     for name in archive.namelist()}
        xml = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        node = xml.find(f".//{{{ns}}}c[@r='A1']")
        node.attrib.pop("t", None)
        cache = node.find(f"{{{ns}}}v")
        if cache is not None:
            node.remove(cache)
        ET.SubElement(node, f"{{{ns}}}v").text = "250"
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(xml)
        with zipfile.ZipFile(workbook, "w") as archive:
            for path, raw in parts.items():
                archive.writestr(path, raw)
        profile = "school_17_sheet_v1"
        digest = core.digest(workbook.read_bytes())
        receipt = {
            "status": "converted", "engine": "Microsoft Excel",
            "engine_result": "GG_OFFICE_SESSION|closed|saved|synthetic",
            "major_authorization": [mc.authorize_output(
                mc.OUTPUT_SCHOOL_WORKBOOK, context).to_dict()],
            "office_session": {"app": "excel", "reason": "closed_confirmed",
                               "cleanup": "closed", "save_state": "saved"},
            "published_office_sha256": digest,
            "validation": {"workbook_profile": profile,
                           "finance_profile": profile,
                           "structure_issues": [], "school_issues": [],
                           "excel": {"valid": True, "sha256": digest}}}
        (root / "native.json").write_text(json.dumps(receipt),
                                          encoding="utf-8")
        receipt_digest = core.digest(
            (root / "native.json").read_bytes())
        mapping = {"schema": facts.MAP_SCHEMA,
                   "workbook_profile": profile,
                   "cells": [{"fact_id": "sales", "field_id": "매출액",
                               "sheet": "계획", "cell": "A1",
                               "period": "2026년", "unit": "천원",
                               "scope": "합성농장"}],
                   "workbook": {"sha256": digest},
                   "receipt": {"sha256": receipt_digest}}
        change = facts.build_registration(
            root, "recalculated.xlsx", "native.json", mapping,
            source_id="excel-plan", context=context)
        core.apply(root, change, core.load(root)["revision"])
        project = core.load(root)
        items, blocked = core._finance_check_items(project)
        self.assertIsNone(blocked)
        self.assertTrue(items, "특용작물 사실이 대조 대상에 안 들었다 — 결함")
        return core, root, items

    def _crosscheck_direct(self, root, items):
        body = Path(root, "body1.md").read_text(encoding="utf-8")
        from gg_school_excel import crosscheck_body
        return crosscheck_body(body, items)

    def test_registered_body_matches_without_fail(self):
        core, root, items = self._register()
        self.add_body(core, root, "합성농장 2026년 매출액은 250 천원이다.\n")
        self.assertEqual([], self._crosscheck_direct(root, items))
        self.assert_pass(core, root, items=items,
                         crosscheck_body_calls=[])

    def test_registered_body_one_off_fails(self):
        core, root, items = self._register()
        self.add_body(core, root, "합성농장 2026년 매출액은 251 천원이다.\n")
        self.assertTrue(self._crosscheck_direct(root, items))
        self.assert_fail_one(core, root, fact_id="sales")


class ClaimsPartialBlockedEndToEndTests(_E2ECase):
    """§2: claims 일부 지정 → ``checks`` blocked 1행 + 학생 말 위생."""

    def test_partial_claims_block_reason_carries_no_forbidden_terms(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "합성 원답변\n")

        def money(fid, value, field_id):
            fact = fact_op(fid, field_id, value, "천원",
                           value_type="decimal", period="2026년",
                           scope="농장 전체", claim_id="c-" + fid)
            source = source_op(claims=claim_of(fact["value"],
                                               claim_id="c-" + fid))
            source["value"]["id"] = fid + "-answer"
            return source, fact

        ops = []
        f1 = None
        for fid, value, field_id in (("m1", "1000", "매출액"),
                                     ("m2", "2000", "이익")):
            source, fact = money(fid, value, field_id)
            write_text(root, fid + "-answer.txt", fid + "\n")
            fact["value"]["source_refs"][0]["id"] = fid + "-answer"
            ops += [source, fact]
            if fid == "m1":
                f1 = fact["value"]
        write_text(root, "body1.md",
                   "농장 전체 2026년 매출액은 1,000천원이다.\n")
        # claims를 m1 하나만 — m2는 확인 대상에서 빠졌다고 차단해야 한다
        ops.append(section_op(
            "body", "재무", "body1.md",
            claims=[section_claim(f1, "매출액은 1,000천원")]))
        p = core.apply(root, {"request_id": "claims-partial", "ops": ops}, 0)

        rows = core.checks(root, p)
        bio = [r for r in rows if r["check_id"] == "body_finance_crosscheck"]
        self.assertEqual(1, len(bio), "blocked 정확히 1행 기대: %r" % (rows,))
        self.assertEqual("blocked", bio[0]["status"])
        reason = bio[0]["reason"]
        lowered = reason.lower()
        for term in FORBIDDEN_STUDENT_TERMS:
            self.assertNotIn(
                term.lower(), lowered,
                "학생에게 보이는 blocked 이유에 내부 용어 %r" % term)
        # 개수 N·M·K가 실제 건수와 같다 (e2e: m1· m2 중 1개만)
        self.assertIn("2개 중 1개", reason)
        self.assertIn("빠진 금액 1개", reason)
        # blocked인데 v2 없는 것은 아니다 — claims가 있다 → v2도 돈다.
        # v2 행 자체(claim-plan-missing 포함)는 본 케이스의 단언 대상이
        # 아니고, v1 blocked 행만 고정한다.


if __name__ == "__main__":
    unittest.main()
