"""쉬운 말 조건표 허용 (형님 결정 4-A) — 허용 1건 + 우회 반례.

`단가 10% 하락 / 수량 10% 감소 / 둘 다` 같은 일상어 조건표는 본문 금칙
재무 용어 검사에 걸리지 않아야 한다. 다만 조건표가 금칙어 우회로가 되면
안 되므로, 표 머리·표 셀·캡션에 `민감도`·할인율·NPV·IRR·B/C를 넣은
경우는 지금처럼 잡혀야 한다. 정책 정본의 금칙 목록 자체도 바뀌지
않았는지 함께 잠근다.
"""
import json
import re
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
        # 경고군 통과만으로는 이 표가 일으키면 안 되는 경고(금칙어·상한·
        # 캡션 길이)를 놓친다. 표 자체가 원인인 id는 명시적으로 없어야 한다.
        # 나머지 id(짧은 시험 원고라 생기는 것)는 허용군 안에서만 허용한다.
        table_caused = {"forbidden_term", "caption_forbidden",
                        "term_limit", "caption_long"}
        assert not (found & table_caused), found & table_caused

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
        # 한계(사람 검토): `민`|`감도`처럼 두 셀에 쪼갠 표기는 기계가 잇지
        # 못한다(셀 경계는 합치지 않는다). 이 한계는 이번 변경 이전부터
        # 있던 것이며, 그래서 조건표는 사람 검토를 함께 받는다.
        # 검사기를 개선해 이 표기를 잡게 되면 이 시험을 뒤집는다
        # (잡히는 쪽이 옳다). 그때 thesis-level.md의 한계 문구도 함께 고친다.
        assert "forbidden_term" not in ids(check(table_with("민", "감도")))

    def test_caption_is_matched_verbatim_only_documented_human_limit(self):
        # 한계(사람 검토, 코드 읽기 기준): 캡션 노드는 term_parts가 비어
        # 본문 정규화 검사를 받지 않고(gg_document.py 캡션 노드), 캡션
        # 검사는 정책 JSON 금칙어 + 캡션 전용 목록을 대소문자만 무시한 원문 일치로 센다.
        # 그래서 정확한 금칙어는 잡히지만(아래 시험) 전각·영문 별칭은
        # 못 잡는다. 검사기를 개선하면 이 시험을 뒤집는다.
        for variant in ("ＮＰＶ", "sensitivity analysis"):
            with self.subTest(variant=variant):
                text = CONDITION_TABLE.replace(
                    "표 1. 단가·수량 변동에 따른 연 소득 (단위: 천원)",
                    "표 1. 단가·수량 " + variant + " (단위: 천원)")
                found = ids(check(text))
                assert "caption_forbidden" not in found
                # 본문 검사(forbidden_term)도 캡션 변형은 잡지 못한다.
                assert "forbidden_term" not in found

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
        # exemplar-quality.md는 견본에서 뽑아 해시로 고정한 발췌라
        # (builtin-sources.json) 이번 판에서 고치지 않으므로 대상에서 뺀다.
        for name in ("thesis-level.md",
                     "narrative-expansion.md", "finance-bible.md"):
            with self.subTest(doc=name):
                text = (REF / name).read_text(encoding="utf-8")
                assert "조건표만 예외" not in text
                # "대상이 아니다"류 표현은 쓰지 않는다(r2 권고 4).
                assert "금지의 대상이 아니" not in text
                assert "금지 대상이 아니" not in text
                # 다른 표현의 예외 선언도 막는다.
                assert not re.search(
                    r"조건표[^\n.]{0,20}예외(로 (한다|둔다|허용|본다|취급)|이다|다)",
                    text), name
                assert "§2 ③" in text
                # 양성 확인: 문서마다 표 안에서도 금칙어를 쓰지 않는다는
                # 취지 문구가 있어야 한다(thesis-level은 "표 안이라도"도).
                assert ("표 안에서도" in text or "표 안이라도" in text
                        or "표 머리·셀" in text), name

    def test_thesis_level_states_human_check_limits(self):
        # 기계가 못 잡는 것을 자동 검사처럼 읽히게 쓰지 않는다(r2 권고 2·3).
        level = (REF / "thesis-level.md").read_text(encoding="utf-8")
        assert "두 셀에 쪼갠" in level
        assert "캡션은 정규화와 영문 별칭 검사를 받지 않고" in level
        assert "정책 JSON 목록에 없어 기계가 막지 않는다" in level
        # 캡션은 "원문 그대로"가 아니라 대소문자 무시 원문 일치다(r3 권고 3).
        assert "대소문자만 무시한 원문 일치" in level
        assert "원문 그대로 일치" not in level

    def test_condition_table_has_no_invented_numeric_cap(self):
        # 결정 4에는 수치 상한이 없다(r2 중요 2). "3개 안팎·결과 1~2열"은
        # 권장이고, 고칠 수 있는 두 문서가 같은 표현이어야 한다.
        for name in ("thesis-level.md", "narrative-expansion.md"):
            with self.subTest(doc=name):
                text = (REF / name).read_text(encoding="utf-8")
                assert "5행" not in text
                assert "3개 안팎" in text
                assert "1~2열" in text
                assert "결과 1열의" not in text

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
        # "빌드 결과 쪽수"는 전체 쪽수(표지·목차·요약·참고문헌·부록 포함)로
        # 결정권자가 확정했다. 본문만 세는 해석으로 되돌아가지 않게 잠근다.
        assert "전체 쪽수" in level
        assert "표지·목차·요약·참고문헌·부록을 포함" in level
        assert "확인 대기" not in level
        # 풀어 쓰기(허용)와 반복(제안 안 함)의 구분.
        assert "풀어 쓰기" in level and "반복" in level
        # 안내 문구 아래에 70쪽이 전체 쪽수임을 분명히 하고(r3 권고 2),
        # 지금은 사람이 확인하며 세는 대상은 제출 최종 문서임을 잠근다.
        assert "문구의 \"70쪽\"은 본문만이 아니라 **전체 쪽수**" in level
        assert "지금은 사람이 확인한다" in level
        assert "학생이 제출하는 최종 문서" in level
        # 소절 단위 분량 목표 없음과 전체 쪽수 안내를 서로 가리킨다(r3 중요).
        narrative = (REF / "narrative-expansion.md").read_text(encoding="utf-8")
        assert "소절 단위의 분량 목표는 두지 않으며" in narrative
        assert "thesis-level.md](thesis-level.md) §2 ⑪" in narrative
        assert "분량 목표는 두지 않으며" not in narrative.replace(
            "소절 단위의 분량 목표는 두지 않으며", "")
        assert "소절 단위 분량 목표는 두지 않는다" in level
        assert "narrative-expansion.md" in level

    def test_generator_lead_description_is_exact(self):
        # r3 권고 1: 자리표시·lead_kind는 재무표 생성기만, 학교 골격은
        # 고정 문장이며 표 2 줄은 옛 꼴로 남은 알려진 차이, 소비 코드 없음.
        level = (REF / "thesis-level.md").read_text(encoding="utf-8")
        assert "자리표시와 `lead_kind`는 이 재무표 생성기만 쓴다" in level
        assert "표 2 앞 줄" in level and "알려진 차이" in level
        assert "`lead_kind`를 읽어 안내를 띄우는 코드는 아직 없다" in level

    def test_body_plan_names_the_one_sentence_form(self):
        # r3 권고 4: "한 형태"가 금액 서술 문장 꼴임을 분명히 한다.
        plan = (REF / "hort-env-systems" / "body-plan.md").read_text(
            encoding="utf-8")
        assert "금액 하나를 서술하는 문장" in plan
        assert "읽는 기준 문장과는 다른 것" in plan


if __name__ == "__main__":
    unittest.main()
