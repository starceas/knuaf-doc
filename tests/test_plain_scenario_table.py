"""쉬운 말 조건표 허용 (형님 결정 4-A) — 허용 1건 + 우회 반례.

`단가 10% 하락 / 수량 10% 감소 / 둘 다` 같은 일상어 조건표는 본문 금칙
재무 용어 검사에 걸리지 않아야 한다. 다만 조건표가 금칙어 우회로가 되면
안 되므로, 표 머리·표 셀·캡션에 `민감도`·할인율·NPV·IRR·B/C를 넣은
경우는 지금처럼 잡혀야 한다. 정책 정본의 금칙 목록 자체도 바뀌지
않았는지 함께 잠근다.
"""
import json
from pathlib import Path
import unittest

from tests._harness import ContractCase, runtime

doc = runtime("gg_document")

SC = "specialty_crops"
POLICY = (Path(__file__).resolve().parent.parent
          / "skills" / "knuaf-doc" / "references" / "plain-thesis-policy.json")
REF = POLICY.parent

CONDITION_TABLE = (
    "# Ⅳ. 재무 계획\n"
    "\n"
    "단가가 10% 내려가면 연 소득이 얼마나 줄어드는가를 비교한다(표 1).\n"
    "\n"
    "표 1. 단가·수량 변동에 따른 연 소득 (단위: 천원)\n"
    "\n"
    "| 구분 | 연 소득 |\n"
    "|---|---|\n"
    "| 단가 10% 하락 | 12,000 |\n"
    "| 수량 10% 감소 | 11,400 |\n"
    "| 둘 다 | 10,800 |\n"
    "\n"
    "단가와 수량이 함께 줄 때 감소 폭이 가장 크다.\n"
)


def ids(issues):
    return {cid for cid, _ in issues}


def check(text):
    return doc.check(text, None, major_id=SC)


def table_with(header, cell):
    return (
        "# Ⅳ. 재무 계획\n"
        "\n"
        "단가가 내려갈 때 연 소득을 비교한다(표 1).\n"
        "\n"
        "표 1. 단가 변동에 따른 연 소득\n"
        "\n"
        "| " + header + " | 연 소득 |\n"
        "|---|---|\n"
        "| " + cell + " | 12,000 |\n"
    )


class ConditionTableAllowed(ContractCase):
    def test_plain_condition_table_passes(self):
        found = ids(check(CONDITION_TABLE))
        # 허용 표가 다른 이유로 막히지 않는지 전체 이슈 집합으로 본다
        # (금칙어·캡션 금칙어 부재만 보면 다른 차단 오류를 놓친다).
        allowed = (doc.WARNING_CHECKS | doc.POLICY_ADVISORY_CHECKS
                   | doc.INFO_CHECKS)
        assert found <= allowed, found

    def test_condition_words_alone_are_not_terms(self):
        found = ids(check(
            "단가 10% 하락과 수량 10% 감소, 둘 다일 때 연 소득을 적는다.\n"))
        allowed = (doc.WARNING_CHECKS | doc.POLICY_ADVISORY_CHECKS
                   | doc.INFO_CHECKS)
        assert found <= allowed, found

    def test_bep_cap_still_counts_table_cells(self):
        # 조건표 셀도 손익분기 6회 상한에 그대로 합산된다.
        rows = "\n".join("| 손익분기 %d년 | 1 |" % n for n in range(7))
        text = (
            "# Ⅳ. 재무 계획\n"
            "\n"
            "연차별 손익분기 시점을 정리한다(표 1).\n"
            "\n"
            "표 1. 연차별 손익분기\n"
            "\n"
            "| 구분 | 값 |\n"
            "|---|---|\n" + rows + "\n"
        )
        assert "term_limit" in ids(check(text))


class ConditionTableIsNotABypass(ContractCase):
    def test_forbidden_term_in_header_still_blocked(self):
        found = ids(check(table_with("민감도", "단가 10% 하락")))
        assert "forbidden_term" in found

    def test_forbidden_term_in_row_cell_still_blocked(self):
        found = ids(check(table_with("구분", "할인율 적용")))
        assert "forbidden_term" in found

    def test_acronyms_in_row_cell_still_blocked(self):
        found = ids(check(table_with("구분", "NPV 120")))
        assert "forbidden_term" in found

    def test_english_alias_in_row_cell_still_blocked(self):
        found = ids(check(table_with("구분", "sensitivity analysis")))
        assert "forbidden_term" in found

    def test_fullwidth_acronym_in_row_cell_still_blocked(self):
        # 셀 안 표기도 본문과 같은 NFKC 정규화를 거친다.
        found = ids(check(table_with("구분", "ＮＰＶ 120")))
        assert "forbidden_term" in found

    def test_korean_terms_in_row_cell_still_blocked(self):
        # 정책 JSON의 한글 금칙어 4종도 표 셀에서 그대로 잡힌다.
        for term in ("순현재가치", "내부수익률", "편익비용", "비용편익"):
            with self.subTest(term=term):
                found = ids(check(table_with("구분", term + " 적용")))
                assert "forbidden_term" in found, term

    def test_split_across_cells_is_a_documented_human_limit(self):
        # 한계(사람 검토): `민`|`감도`처럼 두 셀에 쪼갠 표기와 캡션의 변형
        # 표기는 기계가 잇지 못한다(셀 경계는 합치지 않는다). 셀 안의
        # 표기만 본문과 같은 정규화로 읽는다. 이 한계는 이번 변경 이전부터
        # 있던 것이며, 그래서 조건표는 사람 검토를 함께 받는다.
        assert "forbidden_term" not in ids(check(table_with("민", "감도")))

    def test_forbidden_term_in_caption_still_blocked(self):
        text = CONDITION_TABLE.replace(
            "표 1. 단가·수량 변동에 따른 연 소득 (단위: 천원)",
            "표 1. 단가·수량 민감도 (단위: 천원)")
        assert "caption_forbidden" in ids(check(text))


class PolicyAndDocKeepTheBan(ContractCase):
    def test_policy_still_forbids_the_terms(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        forbidden = set(policy["forbidden_body_terms"])
        assert {"민감도", "할인율", "NPV", "IRR", "B/C"} <= forbidden
        assert policy["limited_terms"]["손익분기"] == 6

    def test_thesis_level_states_allowance_and_ban(self):
        level = (REF / "thesis-level.md").read_text(encoding="utf-8")
        start = level.index("**쉬운 말 조건표 허용")
        block = level[start:level.index("**학교 지침의 투자분석 요구**", start)]
        # 승인 문구(허용 예시)와 금지 사실만 잠근다. 이유 설명 문장은
        # 정정될 수 있으므로 통째로 고정하지 않는다(검토 r1 약점 3).
        assert '"단가 10% 하락"' in block
        assert "금칙" in block
        assert "forbidden_body_terms" in block

    def test_four_docs_never_call_the_table_an_exception(self):
        # 차단 1의 모순(조건표는 금칙어 예외)이 다시 들어오지 않게 한다.
        for name in ("thesis-level.md", "exemplar-quality.md",
                     "narrative-expansion.md", "finance-bible.md"):
            with self.subTest(doc=name):
                text = (REF / name).read_text(encoding="utf-8")
                assert "조건표만 예외" not in text
                assert "조건표는 이 금지의 대상이 아니라" not in text or (
                    "그 표 안에서도" in text)
                assert "§2 ③" in text

    def test_thesis_level_names_the_policy_list_not_a_short_list(self):
        level = (REF / "thesis-level.md").read_text(encoding="utf-8")
        assert "forbidden_body_terms" in level
        assert "forbidden_body_term_aliases" in level
        # 목록을 다시 적을 때 빠졌던 한글 용어 4종이 문서에 남아 있는지.
        for term in ("순현재가치", "내부수익률", "편익비용", "비용편익"):
            assert term in level, term

    def test_page_target_is_guidance_not_school_rule(self):
        level = (REF / "thesis-level.md").read_text(encoding="utf-8")
        # 학생에게 보이는 안내 문구는 그대로 복제한다(ORDER-D4 부속 2).
        assert (
            "> 최소 70쪽(이보다 적으면 졸업논문 제출 수준이 안 돼요). 위로는 정해진 "
            "끝이 없어요 — 장마다 할 말을 다 쓰면 보통 80~100쪽, 많으면 130쪽이 "
            "되기도 해요. 쪽수를 채우려고 늘리지는 마세요."
        ) in level
        # 학교 규정이 아니라는 부정 규칙 자체를 잠근다.
        assert '"학교 기준"이라고 쓰지 않는다' in level
        assert "확인된 하한 없음" in level
        assert "70쪽 미만일 때만" in level
        assert "130쪽을 넘어도 경고를 내지 않는다" in level


if __name__ == "__main__":
    unittest.main()
