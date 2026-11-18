"""Stateless notice contract v7; synthetic canonical mutations only."""
import copy
import json
import unittest
from unittest.mock import patch

from tests._harness import (ContractCase, bind_major, claim_of, fact_op,
                            runtime, section_op, source_op, write_text)
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS


# Exhaustive legacy fail families observed in crosscheck_body at 85b0da3:
# no text; missing label; invalid metadata; invalid numeric value; mismatch
# (including the rival-amount suffix). Section conflicts have their own code.
FAIL_CASES = (
    (None, {}, "본문 텍스트 없음"),
    ("계획", {"매출액": {}}, "본문에 재무 항목 없음"),
    ("매출액", {"매출액": {}}, "유효하지 않은 메타데이터/기준값"),
    ("매출액", {"": {}}, "유효하지 않은 메타데이터/기준값"),
    ("매출액", {"매출액": {"value": "bad", "unit": "원", "period": "2027", "scope": "농장"}}, "유효하지 않은 기준값: bad"),
    ("농장 2027년 매출액은 2원이다.", {"매출액": {"value": "1", "unit": "원", "period": "2027", "scope": "농장"}}, "기간·단위·범위·출력 반올림 값 불일치"),
    ("농장 2027년 매출액은 1원, 2원이다.", {"매출액": {"value": "1", "unit": "원", "period": "2027", "scope": "농장"}}, "기간·단위·범위·출력 반올림 값 불일치 · 같은 문장에 다른 금액이 함께 있습니다. 그 금액이 전년·목표·일부 금액이면 문장을 나눠 쓰세요"),
)


class FinanceHoleCards(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")
        self.root = self.make_project()

    def apply(self, ops):
        rev = self.core.load(self.root)["revision"]
        return self.core.apply(self.root, {"request_id": "card:%d" % rev, "ops": ops}, rev)

    def figure(self, text="합성농장 2027년 매출액은 2,000천원이다.\n", **changes):
        f = fact_op("sales", "매출액", "1000", "천원", value_type="decimal",
                    period="2027년", scope="합성농장", claim_id="c1")
        f["value"].update(changes)
        write_text(self.root, "answer.txt", "합성 원답변\n")
        write_text(self.root, "body.md", text)
        return self.apply([source_op(claims=claim_of(f["value"])), f,
                           section_op("body", "계획", "body.md")])

    def cards(self):
        return [r for r in self.notices.notice_rows(self.root)
                if r.get("choice_policy") == "none"]

    def document(self):
        return json.loads((self.root / ".knuaf-notices.json").read_text(encoding="utf-8"))

    def assert_contract(self, card, deficit=False):
        self.assertEqual(not deficit, card["blocking"])
        self.assertFalse(card["dismissible"])
        self.assertEqual("talk", card["fix_mode"])
        self.assertEqual(["fix", "proceed", "later"] if deficit else ["fix", "later"],
                         [c["value"] for c in card["choices"]])
        self.assertRegex(card["card_key"], r"^[0-9a-f]{1,64}$")
        for absent in ("state", "choice", "card_version", "canon_revision"):
            self.assertNotIn(absent, card)
        # Existing app required fields and old title/items/dismissible surface.
        for key in ("title", "notice_identity", "project_id"):
            self.assertTrue(card[key])
        self.assertTrue(all(isinstance(i["what"], str) for i in card["items"]))
        self.assertEqual("", card["items"][0]["changed"])
        visible = json.dumps({k: card[k] for k in
                              ("title", "items", "choices", "talk_message")}, ensure_ascii=False)
        for term in FORBIDDEN_STUDENT_TERMS:
            self.assertNotIn(term, visible)

    def test_legacy_fail_families_are_neutral_without_invented_numbers(self):
        scanner = runtime("gg_school_excel")
        for text, values, reason in FAIL_CASES:
            with self.subTest(reason=reason):
                issues = scanner.crosscheck_body(text, values)
                self.assertEqual(reason, issues[0]["reason"])
                title = self.core._card_text("finance_hole_figure", reason)
                self.assertNotIn("달라", title)
                self.assertNotRegex(title, r"\d")
        self.assertEqual("본문의 금액 정보를 함께 확인해 주세요",
                         self.core._card_text("finance_hole_figure", "새로운 fail"))

    def test_figure_contract_and_apply_removes_stale_card(self):
        p = self.figure()
        self.assertTrue(any(r["check_id"] == "body_finance_crosscheck" and r["status"] == "fail"
                            for r in self.core.checks(self.root, p)))
        card, = self.cards()
        self.assert_contract(card)
        self.assertEqual("", card["items"][0]["current"])
        self.assertEqual(1, len(self.document()["notices"]))
        # Public apply re-registers the corrected body and rewrites sidecars.
        write_text(self.root, "body.md", "합성농장 2027년 매출액은 1,000천원이다.\n")
        self.apply([section_op("body", "계획", "body.md")])
        self.assertEqual([], self.cards())
        self.assertEqual([], self.document()["notices"])

    def test_missing_and_blocked_do_not_become_cards(self):
        self.assertEqual([], self.cards())
        p = self.figure()
        section = copy.deepcopy(p["sections"]["body"])
        section["claims"] = []
        self.apply([{"collection": "sections", "value": section}])
        self.assertEqual([], self.cards())

    def test_same_fact_legacy_failure_suppresses_section_duplicate(self):
        self.figure("합성농장 2027년 매출액은 1,000천원이다.\n")
        write_text(self.root, "two.md", "합성농장 2027년 매출액은 2,000천원이다.\n")
        p = self.apply([section_op("two", "비용", "two.md", order=2)])
        sources = self.core._finance_card_sources(self.root, p)
        self.assertEqual(1, len(sources))
        self.assertEqual("sales", sources[0][1]["fact_id"])
        self.assertEqual(1, len(self.cards()))

    def test_section_conflict_keeps_actual_amounts_and_unique_keys(self):
        self.figure("합성농장 2027년 매출액은 1,000천원이다.\n합성농장 2027년 영업이익은 300천원이다.\n")
        write_text(self.root, "two.md", "합성농장 2027년 영업이익은 400천원이다.\n")
        p = self.apply([section_op("two", "비용", "two.md", order=2)])
        card, = self.cards()
        self.assert_contract(card)
        self.assertIn("서로 달라요", card["title"])
        self.assertIn("300천원", card["items"][0]["current"])
        self.assertIn("400천원", card["items"][0]["current"])
        sources = self.core._finance_card_sources(self.root, p)
        self.assertEqual("BODY_SECTION_AMOUNT_CONFLICT", sources[0][1]["code"])
        # Even a future scanner pair with the same fact ID cannot collide:
        # distinct visible evidence is part of fold identity.
        legacy = {"fact_id": "same", "location": "same", "reason": FAIL_CASES[-2][2]}
        conflict = dict(sources[0][1], fact_id="same")
        with patch.object(self.core, "_finance_card_sources", return_value=[
                ("finance_hole_figure", legacy), ("finance_hole_figure", conflict)]):
            pair = self.notices._finance_cards(self.root, p)
        self.assertEqual(2, len({c["card_key"] for c in pair}))

    def test_key_tracks_visible_material_not_revision(self):
        self.figure()
        card, = self.cards()
        anchor = "fact:sales"
        key = self.core._card_key(card, card["kind"], anchor)
        changed = copy.deepcopy(card)
        changed["revision"] = 999
        self.assertEqual(key, self.core._card_key(changed, card["kind"], anchor))
        changed["items"][0]["current"] = "2030년 0원"
        self.assertNotEqual(key, self.core._card_key(changed, card["kind"], anchor))
        self.assertNotEqual(key, self.core._card_key(card, "other_kind", anchor))

    def test_deficit_zero_and_negative_then_acceptance_removes_card(self):
        # Reuse fixture methods, not its test suite or mocked gate decisions.
        from tests.test_finance_gate import BodyFinanceGateTests
        helper = BodyFinanceGateTests()
        for value in ("-17100000", "0"):
            self.root = self.make_project()
            helper.core, helper.root = self.core, self.root
            bind_major(self.root)
            helper.body()
            helper.record("deficit_pending", value)
            card, = self.cards()
            self.assert_contract(card, deficit=True)
            self.assertIn(value + "원", card["title"])
            self.assertIn("2030년", card["title"])
        helper.answer()
        self.assertEqual([], self.cards())
        self.assertEqual([], [c for c in self.document()["notices"] if c.get("choice_policy") == "none"])

    def test_once_coexists_and_full_checks_is_not_reexecuted(self):
        self.figure()
        once = {"title": "기존 알림", "items": [{"what": "확인해 주세요"}],
                "dismissible": True, "choice_policy": "once"}
        with patch.object(self.core, "checks", side_effect=AssertionError("full checks")), \
                patch.object(self.core, "_workbook_notice_row_for_project", return_value=once):
            self.assertEqual("written", self.notices.write_notices(self.root)["status"])
        card, old = self.document()["notices"]
        self.assert_contract(card)
        self.assertEqual("once", old["choice_policy"])
        self.assertTrue(old["dismissible"])


if __name__ == "__main__":
    unittest.main()
