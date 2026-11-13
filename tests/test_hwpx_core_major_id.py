"""한글 변환 본체(`gg_hwpx`)가 전공 ID를 넘겨받는지 (F6).

`gg_hwpx.py`에는 `"food_crops"`가 글자로 박힌 자리가 둘 있었다 —
`_front_markers()`의 앞머리 구분 이름 조회와 `build()`의 `parse`·
`school_frontmatter_plan` 호출. 이 단위는 그 둘을 넘겨받은 전공 ID로 바꿨다.
이 시험은 (1) 식량작물이면 옛 값(문자 그대로)과 같은 마커가 나오는지,
(2) 다른 전공 ID를 넘기면 그 전공 표를 읽는지, (3) `transform_hwpx`가 그 ID를
`split_sections_by_plan`까지 넘기는지를 잰다.

가짜 원고·가짜 이름만 쓴다.
"""

import io
import unittest
import zipfile

from tests._harness import ContractCase, runtime

# F6 이전 코드에 박혀 있던 값. 식량작물 표에서 온 그대로다.
LEGACY_FOOD_CROPS_MARKERS = ("겉표지", "표제면", "제출서", "인준서", "요약",
                             "감사의 글", "목차")
# 낙농 구분 이름. 식량작물과 달리 `학부모 동의서`가 있고 `표제면`이 없다.
DAIRY_MARKERS = ("겉표지", "제출서", "인준서", "학부모 동의서", "요약",
                 "감사의 글", "목차")

SECTION_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
    '<hp:p><hp:run><hp:t>본문이다.</hp:t></hp:run></hp:p>'
    '</hs:sec>')


def nodes_of(*texts):
    """`gg_document.parse`가 내는 노드 모양(글자만 있으면 된다)."""
    return [{"text": text, "kind": "paragraph"} for text in texts]


def minimal_hwpx():
    """구역 하나짜리 최소 HWPX ZIP(mimetype 무압축 + section0 + header)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/hwp+zip")
        archive.writestr("Contents/section0.xml", SECTION_XML)
        archive.writestr(
            "Contents/header.xml",
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"/>')
    return buffer.getvalue()


class HwpxCoreMajorIdTests(ContractCase):
    def setUp(self):
        self.gh = runtime("gg_hwpx")

    def test_food_crops_markers_match_legacy_literal(self):
        # 넘겨받은 ID가 식량작물이면 옛 하드코딩과 문자 하나까지 같아야 한다.
        self.assertEqual(LEGACY_FOOD_CROPS_MARKERS,
                         self.gh._front_markers("food_crops"))
        nodes = nodes_of("겉표지", "본문 첫 문단", "요약", "목차")
        self.assertEqual({"겉표지", "요약", "목차"},
                         self.gh._front_matter_markers(nodes, "food_crops"))

    def test_other_major_reads_its_own_markers(self):
        # 다른 전공 ID를 넘기면 그 전공 표에서 읽는다(하드코딩 아님).
        self.assertEqual(DAIRY_MARKERS, self.gh._front_markers("dairy"))
        nodes = nodes_of("학부모 동의서", "표제면")
        self.assertEqual({"학부모 동의서"},
                         self.gh._front_matter_markers(nodes, "dairy"))
        self.assertEqual({"표제면"},
                         self.gh._front_matter_markers(nodes, "food_crops"))

    def test_split_uses_passed_major_id_when_plan_missing(self):
        # 계획이 없을 때 유예/거절을 가르는 기준도 넘겨받은 ID다. 낙농에만 있는
        # 구분 이름 하나만 있는 원고는 식량작물 기준으로는 '앞머리 없음'이다.
        nodes = nodes_of("학부모 동의서", "본문 첫 문단")
        sections, info = self.gh.split_sections_by_plan(
            SECTION_XML, nodes=nodes, plan=None,
            section_formats=self.gh.FOOD_CROPS_SECTION_FORMATS,
            major_id="food_crops")
        self.assertIsNone(sections)
        self.assertEqual("not_applicable", info["status"])
        with self.assertRaises(self.gh.HwpxBlocked) as caught:
            self.gh.split_sections_by_plan(
                SECTION_XML, nodes=nodes, plan=None,
                section_formats=self.gh.FOOD_CROPS_SECTION_FORMATS,
                major_id="dairy")
        self.assertEqual("section_split_front_matter_partial",
                         caught.exception.reason)
        self.assertEqual(set(DAIRY_MARKERS) - {"학부모 동의서"},
                         set(caught.exception.details["missing"]))

    def test_transform_hwpx_forwards_major_id(self):
        # `build()`가 넘긴 ID가 분할 단계까지 닿는지(인자 흐름) 잰다.
        data = minimal_hwpx()
        nodes = nodes_of("학부모 동의서", "본문 첫 문단")
        _, applied = self.gh.transform_hwpx(
            data, font=None, margins_mm=None, page_numbers=False,
            section_formats=self.gh.FOOD_CROPS_SECTION_FORMATS,
            nodes=nodes, plan=None, major_id="food_crops")
        self.assertEqual("not_applicable", applied["sections"])
        with self.assertRaises(self.gh.HwpxBlocked) as caught:
            self.gh.transform_hwpx(
                data, font=None, margins_mm=None, page_numbers=False,
                section_formats=self.gh.FOOD_CROPS_SECTION_FORMATS,
                nodes=nodes, plan=None, major_id="dairy")
        self.assertEqual("section_split_front_matter_partial",
                         caught.exception.reason)


if __name__ == "__main__":
    unittest.main()
