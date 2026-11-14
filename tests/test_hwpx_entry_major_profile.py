"""HWPX 진입점(`build_hwpx`)이 전공 서식 표를 읽는지 (F5).

`build_hwpx.py`는 `major_id == "food_crops"`를 여섯 자리에서 직접 비교하고
안내문·사람 마무리 목록·장 판정 정규식·구역 서식을 자기 상수로 들고 있었다.
이 단위는 그 자리를 `gg_major_format.profile_for` 조회로 바꿨다. 이 시험은
(1) 식량작물 값이 옛 상수와 **문자 하나까지** 같은지, (2) 프로필 값을 고치면
출력이 따라오는지(치환이 진짜인지), (3) 낙농은 게이트로 옛 경로를 타는지를 잰다.

가짜 원고·가짜 이름만 쓴다.
"""

from dataclasses import replace
from pathlib import Path
import re
import unittest
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime

# 앞머리 표지 7개가 모두 있는 가짜 원고. 값이 아니라 표지 이름만 쓴다.
FOOD_CROPS_FRONT_MD = ("겉표지\n\n표제면\n\n제출서\n\n인준서\n\n요약\n\n감사의 글\n\n"
                       "목차\n\n1. 머리말\n")
DAIRY_FRONT_MD = ("겉표지\n\n제출서\n\n인준서\n\n학부모 동의서\n\n요약\n\n감사의 글\n\n"
                  "목차\n\nⅠ. 머리말\n")


def section_xml(*texts):
    """장 제목 판정만 보는 최소 구역 XML(제목 스타일 `paraPrIDRef="1"`)."""
    paras = "".join(
        '<hp:p paraPrIDRef="1"><hp:run charPrIDRef="0"><hp:t>%s</hp:t></hp:run>'
        '</hp:p>' % text for text in texts)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            + paras + '</hs:sec>')


# 쪽번호 컨트롤이 정확히 하나 붙은 최소 구역(개수 재확인 경로용).
NUMBERED_SECTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0">'
    '<hp:ctrl><hp:pageNum pos="BOTTOM_CENTER" formatType="DIGIT" sideChar="-"/>'
    '</hp:ctrl><hp:t>본문이다.</hp:t></hp:run></hp:p>'
    '</hs:sec>')


class _StopAfterCapture(Exception):
    """`gg_hwpx.build` 대역이 인자를 잡은 뒤 빌드를 멈추는 표식."""


class HwpxEntryMajorProfileTests(ContractCase):
    def setUp(self):
        self.builder = runtime("build_hwpx")
        self.gmf = runtime("gg_major_format")

    def patched(self, **changes):
        """프로필 표를 건드리지 않고, 이 빌드만 다른 프로필을 보게 한다."""
        profile = replace(self.gmf.FOOD_CROPS_PROFILE, **changes)
        return mock.patch.object(
            self.builder, "hwpx_profile",
            lambda major_id: profile if major_id == "food_crops" else None)

    # --- 값이 옛 상수와 같다 ----------------------------------------------

    def test_food_crops_profile_equals_the_constants_the_entry_point_hardcoded(self):
        profile = self.gmf.profile_for("food_crops")
        # 안내문은 매니페스트 `notice`에 그대로 실린다 — 한 글자만 달라도
        # 기존 산출물의 매니페스트 의미가 바뀐다.
        self.assertEqual(self.builder.FOOD_CROPS_FINISH_NOTICE,
                         profile.finish_notice)
        # 사람 마무리 목록은 매니페스트 `human_finish_required` 배열 순서까지.
        self.assertEqual(tuple(self.builder.FOOD_CROPS_HUMAN_FINISH),
                         tuple(profile.human_finish))
        # 장 판정 정규식은 패턴·플래그 둘 다.
        self.assertEqual(self.builder._FOOD_CROPS_CHAPTER_RE.pattern,
                         profile.hwpx_chapter_re.pattern)
        self.assertEqual(self.builder._FOOD_CROPS_CHAPTER_RE.flags,
                         profile.hwpx_chapter_re.flags)
        # 구역 서식(여백·쪽번호) 3행.
        self.assertEqual(
            [dict(row["margin_mm"]) for row in
             self.builder.FOOD_CROPS_SECTION_FORMATS],
            [dict(row["margin_mm"]) for row in profile.section_formats])
        self.assertEqual(
            [dict(row["page_number"]) for row in
             self.builder.FOOD_CROPS_SECTION_FORMATS],
            [dict(row["page_number"]) for row in profile.section_formats])

    def _captured_build_kwargs(self, major):
        """`export`가 `gg_hwpx.build`에 넘긴 인자만 잡는다(빌드·kordoc 없이)."""
        captured = {}

        def capture(*args, **kw):
            captured.update(kw)
            raise _StopAfterCapture()

        root = self.make_project()
        bind_major(root, major)
        (Path(root) / "body.md").write_text("합성 본문.\n", encoding="utf-8")
        ctx = runtime("gg_major_contract").output_context(root, major)
        with mock.patch.object(self.builder.gg_hwpx, "build", capture):
            with self.assertRaises(_StopAfterCapture):
                self.builder.export(root, "body.md", "out.hwpx", context=ctx)
        return captured

    def test_export_hands_the_profile_section_formats_to_gg_hwpx(self):
        """`export`의 구역 서식 4자리(치환 3~6)가 프로필 조회를 타는지 잰다."""
        captured = self._captured_build_kwargs("food_crops")
        profile = self.gmf.profile_for("food_crops")
        self.assertIsNotNone(profile)
        # 같은 값이 아니라 **같은 표에서 온 객체**여야 한다(옛 상수를 그대로
        # 넘겨도 값은 같으므로 등가 비교로는 치환을 못 잡는다).
        self.assertIs(profile.section_formats, captured["section_formats"])
        # 쪽번호 재계산·후처리가 쓰는 본문 여백(셋째 구역)도 같은 표에서 온다.
        self.assertIs(profile.section_formats[2]["margin_mm"],
                      captured["margins_mm"])

    def test_export_leaves_other_majors_on_the_legacy_path(self):
        captured = self._captured_build_kwargs("specialty_crops")
        self.assertNotIn("section_formats", captured)
        self.assertNotIn("margins_mm", captured)

    # --- 치환이 진짜인가: 프로필을 고치면 출력이 따라온다 -------------------

    def test_profile_section_row_reads_pos_none_through_the_page_number_override(self):
        """프로필 행은 `MappingProxyType`이다 — `_page_number_pos`가 겉표지
        `pos="NONE"`을 읽어야 쪽번호 기대 개수가 0이 된다(F5 차단 원인)."""
        profile = self.gmf.profile_for("food_crops")
        self.assertIsNotNone(profile)
        cover, front, _body = profile.section_formats
        # 얼린 매핑이어야 이 시험이 의미 있다(평범한 dict면 옛 코드도 통과한다).
        self.assertNotIsInstance(cover, dict)
        gg_hwpx = self.builder.gg_hwpx
        self.assertEqual("NONE", gg_hwpx._page_number_pos(cover))
        # 겉표지(NONE): 컨트롤이 하나라도 있으면 기대 0과 어긋나 차단된다.
        with self.assertRaises(gg_hwpx.HwpxBlocked) as caught:
            gg_hwpx.inject_page_numbers(NUMBERED_SECTION, override=cover)
        self.assertEqual("page_number_control_count", caught.exception.reason)
        self.assertEqual(0, caught.exception.details["expected"])
        # 앞부분(BOTTOM_CENTER): 컨트롤 1개가 기대 — 그대로 통과(개수 재확인).
        self.assertEqual("BOTTOM_CENTER", gg_hwpx._page_number_pos(front))
        self.assertEqual(NUMBERED_SECTION,
                         gg_hwpx.inject_page_numbers(NUMBERED_SECTION,
                                                     override=front))

    def test_notice_and_finish_list_come_from_the_profile(self):
        with self.patched(finish_notice="합성 안내문",
                          human_finish=("합성 마무리",)):
            notice = self.builder.notice(True, "food_crops")
            summary = self.builder.school_frontmatter_summary(
                FOOD_CROPS_FRONT_MD, "food_crops")
        self.assertIn("합성 안내문", notice)
        self.assertNotIn(self.builder.FOOD_CROPS_FINISH_NOTICE, notice)
        self.assertEqual(["합성 마무리"], summary["human_finish_required"])

    def test_chapter_break_regex_comes_from_the_profile(self):
        # 프로필 정규식을 닫는 제목 하나만 잡게 바꾸면, 옛 식량작물 정규식이
        # 잡던 `1. 머리말`이 빠진다 — 치환이 살아 있다는 증거.
        narrower = re.compile(r"^맺음말\s*및\s*발전방향$")
        with self.patched(hwpx_chapter_re=narrower):
            _, chapters = self.builder.add_chapter_breaks(
                section_xml("1. 머리말", "맺음말 및 발전방향", "Ⅰ. 머리말"),
                major_id="food_crops")
        self.assertEqual(["맺음말 및 발전방향"], chapters)

    # --- 낙농은 게이트로 옛 경로 -------------------------------------------

    def test_dairy_gate_matches_the_other_builders(self):
        self.assertNotIn("dairy", self.builder.HWPX_PROFILE_MAJORS)
        self.assertIsNone(self.builder.hwpx_profile("dairy"))
        self.assertEqual(tuple(runtime("build_docx").DOCX_PROFILE_MAJORS),
                         tuple(self.builder.HWPX_PROFILE_MAJORS))

    def test_dairy_keeps_the_legacy_path_while_the_gate_is_shut(self):
        notice = self.builder.notice(True, "dairy")
        self.assertIn(self.builder.FINISH_NOTICE, notice)
        self.assertNotIn("위30/아래30/머리0/꼬리10", notice)
        self.assertEqual(
            list(self.builder.HUMAN_FINISH),
            self.builder.school_frontmatter_summary(
                DAIRY_FRONT_MD, "dairy")["human_finish_required"])
        # 낙농 프로필의 `hwpx_chapter_re`는 None이라 장 판정은 옛 로마 정규식.
        _, chapters = self.builder.add_chapter_breaks(
            section_xml("Ⅰ. 머리말", "1. 머리말"), major_id="dairy")
        self.assertEqual(["Ⅰ. 머리말"], chapters)

    def test_opening_the_gate_for_dairy_changes_the_notice(self):
        """게이트가 낙농을 막는 유일한 이유임을 보인다(치환이 살아 있다)."""
        with mock.patch.object(self.builder, "HWPX_PROFILE_MAJORS",
                               ("food_crops", "dairy")):
            notice = self.builder.notice(True, "dairy")
            summary = self.builder.school_frontmatter_summary(
                DAIRY_FRONT_MD, "dairy")
        dairy = self.gmf.profile_for("dairy")
        self.assertIn(dairy.finish_notice, notice)
        self.assertEqual(list(dairy.human_finish),
                         summary["human_finish_required"])


if __name__ == "__main__":
    unittest.main()
