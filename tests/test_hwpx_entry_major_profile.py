"""HWPX 진입점(`build_hwpx`)이 전공 서식 표를 읽는지 (F5).

`build_hwpx.py`는 `major_id == "food_crops"`를 여섯 자리에서 직접 비교하고
안내문·사람 마무리 목록·장 판정 정규식·구역 서식을 자기 상수로 들고 있었다.
이 단위는 그 자리를 `gg_major_format.profile_for` 조회로 바꿨다. 이 시험은
(1) 식량작물 값이 옛 상수와 **문자 하나까지** 같은지, (2) 프로필 값을 고치면
출력이 따라오는지(치환이 진짜인지), (3) 낙농은 게이트로 옛 경로를 타는지를 잰다.

가짜 원고·가짜 이름만 쓴다.
"""

from dataclasses import replace
import io
from pathlib import Path
import re
import unittest
from unittest import mock
import zipfile

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


# 가짜 header.xml — 문단 모양 3개: 본문 NONE(id 0)·제목 OUTLINE(id 1)·
# `1)` NONE(id 7). 모양은 `impl/f9c/sample/Contents/header.xml`(식량작물
# 실측본)을 줄여 옮긴 것이고, 값은 그 사본 그대로다.
HEADER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
    '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
    ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" version="1.4">\n'
    '  <hh:paraProperties itemCnt="3">\n'
    '    <hh:paraPr id="0" tabPrIDRef="0" condense="0" textDir="AUTO">\n'
    '      <hh:align horizontal="JUSTIFY" vertical="BASELINE"/>\n'
    '      <hh:heading type="NONE" idRef="0" level="0"/>\n'
    '      <hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
    '<hc:prev value="0" unit="HWPUNIT"/><hc:next value="0" unit="HWPUNIT"/>'
    '</hh:margin>\n'
    '    </hh:paraPr>\n'
    '    <hh:paraPr id="1" tabPrIDRef="0" condense="0" textDir="AUTO">\n'
    '      <hh:align horizontal="LEFT" vertical="BASELINE"/>\n'
    '      <hh:heading type="OUTLINE" idRef="0" level="0"/>\n'
    '      <hh:margin><hc:intent value="0" unit="HWPUNIT"/>'
    '<hc:prev value="800" unit="HWPUNIT"/><hc:next value="200" unit="HWPUNIT"/>'
    '</hh:margin>\n'
    '    </hh:paraPr>\n'
    '    <hh:paraPr id="7" tabPrIDRef="0" condense="0" textDir="AUTO">\n'
    '      <hh:align horizontal="LEFT" vertical="BASELINE"/>\n'
    '      <hh:heading type="NONE" idRef="0" level="0"/>\n'
    '      <hh:margin><hc:intent value="600" unit="HWPUNIT"/>'
    '<hc:prev value="0" unit="HWPUNIT"/><hc:next value="0" unit="HWPUNIT"/>'
    '</hh:margin>\n'
    '    </hh:paraPr>\n'
    '  </hh:paraProperties>\n'
    '</hh:head>\n')

# 후처리가 건드릴 것이 없는 최소 구역(표 없음·장 제목 없음).
HEADER_ZIP_SECTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    '<hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>본문이다.</hp:t>'
    '</hp:run></hp:p></hs:sec>')


def header_zip(header_xml, section=HEADER_ZIP_SECTION):
    """header.xml + 구역 하나짜리 가짜 HWPX ZIP(mimetype 무압축)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, b"application/hwp+zip")
        archive.writestr("Contents/header.xml", header_xml)
        archive.writestr("Contents/section0.xml", section)
    return buffer.getvalue()


def read_members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info.filename)
                for info in archive.infolist()}


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

    # --- F9c: 제목 문단 위아래 여백(header.xml) -----------------------------

    def test_heading_margins_touch_only_the_outline_paragraph_shape(self):
        """`type="OUTLINE"` 모양만 바뀌고 본문(`type="NONE"`)은 그대로다."""
        updated, changed = self.builder.set_heading_margins(
            HEADER_XML, 1920, 1920)
        self.assertEqual(["1"], changed)
        self.assertIn(
            '<hc:prev value="1920" unit="HWPUNIT"/>'
            '<hc:next value="1920" unit="HWPUNIT"/>', updated)
        # 본문 모양(id 0·7)의 여백은 손대지 않는다 — 값이 0인 채로 남는다.
        self.assertEqual(
            2, updated.count('<hc:prev value="0" unit="HWPUNIT"/>'))
        # 제목 모양의 두 값만 바뀌고 나머지 글자는 하나도 안 바뀐다.
        self.assertEqual(
            HEADER_XML.replace('value="800"', 'value="1920"')
                      .replace('value="200"', 'value="1920"'),
            updated)

    def test_heading_margins_keep_the_header_when_values_are_zero(self):
        """값이 0이면 header.xml 문자열이 입력 그대로다(바이트 불변)."""
        self.assertEqual((HEADER_XML, []),
                         self.builder.set_heading_margins(HEADER_XML, 0, 0))

    def test_apply_layout_leaves_header_bytes_unchanged(self):
        """프로필 없음(게이트 밖) 또는 프로필 제목 여백 0이면 header.xml 불변."""
        original = header_zip(HEADER_XML)
        # 식량작물은 프로필 제목 여백이 0이라 불변이고, 낙농·특용작물은 게이트
        # 밖이라 프로필 자체가 없다. 셋 다 header.xml이 손대지 않은 채다.
        for major in ("food_crops", "dairy", "specialty_crops"):
            with self.subTest(major=major):
                updated, report = self.builder.apply_layout(
                    original, major_id=major)
                # header.xml 멤버 바이트가 한 개도 달라선 안 된다.
                self.assertEqual(read_members(original), read_members(updated))
                # 보고 내용도 그대로여야 매니페스트가 안 바뀐다(키 없음).
                self.assertNotIn("heading_margins", report)

    def test_apply_layout_writes_the_profile_heading_margins(self):
        """프로필 값이 0보다 크면 header.xml의 제목 여백만 그 값이 된다."""
        with self.patched(heading_prev_hwpunit=1920,
                          heading_next_hwpunit=1920):
            updated, report = self.builder.apply_layout(
                header_zip(HEADER_XML), major_id="food_crops")
        members = read_members(updated)
        self.assertIn('<hc:prev value="1920" unit="HWPUNIT"/>',
                      members["Contents/header.xml"].decode("utf-8"))
        self.assertEqual(["1"], report["heading_margins"])
        # 구역 XML은 손대지 않는다(표 폭·장 쪽 나눔이 없을 때).
        self.assertEqual(HEADER_ZIP_SECTION,
                         members["Contents/section0.xml"].decode("utf-8"))

    # --- F9d: 장 새 쪽을 노드 목록으로 판정 --------------------------------

    def test_chapter_breaks_follow_the_node_text_list(self):
        """`chapter_texts`가 있으면 정규식 대신 글자 일치로만 쪽 나눔을 준다.

        `Ⅱ. 셋째`만 목록에 있으므로 로마 번호 장인 `Ⅰ. 머리말`도, 아라비아
        번호 장을 닮은 `1. 둘째`도 손대지 않는다(정규식이었다면 둘 다 잡힌다).
        """
        xml = section_xml("Ⅰ. 머리말", "1. 둘째", "Ⅱ. 셋째")
        updated, chapters = self.builder.add_chapter_breaks(
            xml, major_id="dairy", chapter_texts=["Ⅱ. 셋째"])
        self.assertEqual(["Ⅱ. 셋째"], chapters)
        self.assertEqual(1, updated.count('pageBreak="1"'))
        self.assertEqual(
            xml.replace('<hp:p paraPrIDRef="1"><hp:run charPrIDRef="0">'
                        '<hp:t>Ⅱ. 셋째</hp:t>',
                        '<hp:p paraPrIDRef="1" pageBreak="1">'
                        '<hp:run charPrIDRef="0"><hp:t>Ⅱ. 셋째</hp:t>'),
            updated)

    def test_chapter_texts_ignore_the_paragraph_shape_number(self):
        """목록 방식은 `paraPrIDRef`를 보지 않는다 — 실제 kordoc 출력 회귀.

        실측: kordoc `--plain`은 낙농 원고의 장 제목 문단에 `#` 제목이 있으면
        `paraPrIDRef="1"`, 없으면 `"7"`(아라비아 장), 로마 번호 장이면 `"0"`을
        붙인다. 모양 번호를 고정하면 실제 낙농 HWPX에 쪽 나눔이 0개 들어간다
        (F9e가 실제 kordoc으로 빌드해 확인). 목록은 그 원고의 1단계 제목에서
        나왔으므로 글자만 맞으면 된다. 게이트 밖에서도 이 함수는 그대로
        도는 갈래라, 이 시험은 이 묶음에서도 통과해야 한다(함정 방지선).
        """
        for shape in ("0", "7"):
            with self.subTest(paraPrIDRef=shape):
                xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                       '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                       '<hp:p paraPrIDRef="%s"><hp:run charPrIDRef="0">'
                       '<hp:t>2. 농업환경분석</hp:t></hp:run></hp:p></hs:sec>' % shape)
                updated, chapters = self.builder.add_chapter_breaks(
                    xml, major_id="dairy", chapter_texts=["2. 농업환경분석"])
                self.assertEqual(["2. 농업환경분석"], chapters)
                self.assertEqual(1, updated.count('pageBreak="1"'))
                self.assertIn('<hp:p paraPrIDRef="%s" pageBreak="1">' % shape,
                              updated)

    def test_chapter_texts_none_keeps_the_legacy_regex(self):
        """목록을 안 넘기면(식량작물·게이트 밖 전공) 옛 정규식 결과 그대로."""
        xml = section_xml("1. 머리말", "1) 소제목", "참고문헌", "Ⅰ. 머리말")
        legacy, legacy_chapters = self.builder.add_chapter_breaks(
            xml, major_id="food_crops")
        explicit, explicit_chapters = self.builder.add_chapter_breaks(
            xml, major_id="food_crops", chapter_texts=None)
        self.assertEqual((legacy, legacy_chapters),
                         (explicit, explicit_chapters))
        # `1)` 소제목은 아라비아 장 번호가 아니라 여전히 빠지고, `1.`·무번호
        # 제목만 잡힌다.
        self.assertEqual(["1. 머리말", "참고문헌"], legacy_chapters)

    def test_chapter_break_texts_drops_the_first_body_heading(self):
        """도우미는 앞부분 뒤쪽 1단계 제목을 모으고 첫째를 뺀다(`Ⅰ.`·`1.` 둘 다)."""
        md = ("겉표지\n\n제출서\n\n인준서\n\n학부모 동의서\n\n요약\n\n감사의 글\n\n"
              "목차\n\nⅠ. 머리말\n\nⅡ. 낙농 현황\n\nⅢ. 경영 계획\n\nⅣ. 결론\n")
        self.assertEqual(
            ["Ⅱ. 낙농 현황", "Ⅲ. 경영 계획", "Ⅳ. 결론"],
            self.builder.chapter_break_texts(md, "dairy"))
        # 앞부분 계획이 없는 원고는 전체에서 모으고 첫째만 뺀다.
        self.assertEqual(
            ["Ⅰ. 둘째"],
            self.builder.chapter_break_texts("1. 머리말\n\nⅠ. 둘째\n",
                                             "specialty_crops"))

    def test_apply_layout_passes_the_text_list_to_the_section(self):
        """`apply_layout`이 목록을 구역 변환까지 넘기는지(중간 배선)."""
        original = header_zip(HEADER_XML, section=section_xml(
            "Ⅰ. 머리말", "Ⅱ. 낙농 현황", "Ⅲ. 경영 계획"))
        updated, report = self.builder.apply_layout(
            original, major_id="dairy", chapter_texts=["Ⅲ. 경영 계획"])
        section = read_members(updated)["Contents/section0.xml"].decode("utf-8")
        self.assertEqual(["Ⅲ. 경영 계획"], report["chapter_breaks"])
        self.assertEqual(1, section.count('pageBreak="1"'))
        self.assertIn('<hp:t>Ⅲ. 경영 계획</hp:t>', section)

    def test_export_computes_chapter_texts_only_for_node_profiles(self):
        """`export`는 `hwpx_chapter == "nodes"`일 때만 목록을 만들어 넘긴다."""
        def captured(major):
            root = self.make_project()
            bind_major(root, major)
            (Path(root) / "body.md").write_text(
                "Ⅰ. 머리말\n\nⅡ. 낙농 현황\n\nⅢ. 경영 계획\n", encoding="utf-8")
            ctx = runtime("gg_major_contract").output_context(root, major)
            seen = {}

            def fake_build(*args, **kw):
                Path(args[1]).write_bytes(b"")
                return {}

            def fake_layout(*args, **kw):
                seen.update(kw)
                raise _StopAfterCapture()

            profile = self.gmf.profile_for(major)
            with mock.patch.object(self.builder, "hwpx_profile",
                                   lambda major_id: profile), \
                 mock.patch.object(self.builder.gg_hwpx, "build", fake_build), \
                 mock.patch.object(self.builder, "apply_layout", fake_layout):
                with self.assertRaises(_StopAfterCapture):
                    self.builder.export(root, "body.md", "out.hwpx", context=ctx)
            return seen["chapter_texts"]

        # 낙농(노드 방식): 본문 1단계 제목에서 첫째를 뺀 목록이 넘어간다.
        self.assertEqual(["Ⅱ. 낙농 현황", "Ⅲ. 경영 계획"], captured("dairy"))
        # 식량작물(정규식 방식): None — 옛 정규식 경로 그대로.
        self.assertIsNone(captured("food_crops"))


if __name__ == "__main__":
    unittest.main()
