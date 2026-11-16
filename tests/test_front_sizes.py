"""F12 앞부분 크기·굵기 회귀: 합성 원고만 쓰고 실제 XML/문단 서식을 검사한다."""
import unittest
from dataclasses import replace
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime
from tests import test_build_hwpx as hw


class FrontSizeTests(ContractCase):
    def manuscript(self, major):
        fm = runtime("gg_frontmatter")
        profile = fm.normalize_school_profile({
            "major_id": major,
            "school_profile": {
                "title": "합성 영농계획", "author": "가상학생",
                "advisor": "가상교수", "submission_date": "2026년 12월",
                "graduation_date": "2027년 2월"}})
        lines = fm.frontmatter_lines(profile, artifacts={
            "parent_consent": "합성 동의 내용\n\n※ 합성 동의 주석",
            "summary": "1. 농장 개요\n\n합성 요약 내용",
            "thanks": "합성 감사 내용"})
        return "\n\n".join(lines + ["1. 머리말", "합성 본문 내용"])

    def docx(self, major):
        root = self.make_project()
        bind_major(root, major)
        return runtime("build_docx").convert(
            self.manuscript(major),
            context=runtime("gg_major_contract").output_context(root, major))

    def hwpx(self, major):
        gd = runtime("gg_document")
        nodes = gd.parse(self.manuscript(major), major_id=major)
        plan = gd.school_frontmatter_plan(nodes, major)
        profile = runtime("gg_major_format").profile_for(major)
        section = hw.hwpx_section([node["text"] for node in nodes])
        data, _ = hw.gh.transform_hwpx(
            hw.food_crops_zip(section), section_formats=profile.section_formats,
            nodes=nodes, plan=plan, major_id=major)
        members = hw.read_zip(data)
        shapes = hw.charpr_shapes(members["Contents/header.xml"].decode("utf-8"))
        rows = []
        for i in range(3):
            for p in hw.paragraphs_of(members["Contents/section%d.xml" % i].decode("utf-8")):
                rows.append(("".join(p.itertext()),
                             shapes[hw.first_run(p).get("charPrIDRef")]))
        return rows

    def test_dairy_docx_titles_cover_and_consent(self):
        doc = self.docx("dairy")
        for text in ("요약", "감사의 글", "학부모 동의서", "목    차", "표 목차", "그림 목차"):
            # 목차의 항목은 12pt이므로 실제 제목만 고른다.
            titles = [p for p in doc.paragraphs if p.text == text
                      and p.runs and p.runs[0].font.size is not None
                      and p.runs[0].font.size.pt == 22]
            self.assertEqual(1, len(titles), text)
            self.assertTrue(titles[0].runs[0].bold, text)
        for text, shape in (("농업전문학사 학위논문", (18, False)),
                            ("합성 동의 내용", (12, False)),
                            ("※ 합성 동의 주석", (10, False)),
                            ("합성 요약 내용", (12, False))):
            paragraphs = [p for p in doc.paragraphs if p.text == text]
            self.assertTrue(paragraphs, text)
            for p in paragraphs:
                self.assertEqual(shape, (p.runs[0].font.size.pt, bool(p.runs[0].bold)), text)

    def test_dairy_hwpx_titles_cover_and_consent_boundaries(self):
        rows = self.hwpx("dairy")
        for text in ("요약", "감사의 글", "학부모 동의서", "목차", "표 목차", "그림 목차"):
            self.assertEqual([(22, True)], [shape for label, shape in rows if label == text], text)
        for text, shape in (("농업전문학사 학위논문", (18, False)),
                            ("지도교수 가상교수", (18, False)),
                            ("합성 동의 내용", (12, False)),
                            ("※ 합성 동의 주석", (10, False)),
                            ("합성 요약 내용", (12, False)),
                            ("합성 본문 내용", (12, False))):
            found = [value for label, value in rows if label == text]
            self.assertTrue(found, text)
            self.assertEqual([shape] * len(found), found, text)

    def test_food_crops_list_titles_keep_normal_weight(self):
        doc = self.docx("food_crops")
        for text in ("표 목차", "그림 목차"):
            titles = [p for p in doc.paragraphs if p.text == text
                      and p.runs and p.runs[0].font.size.pt == 20]
            self.assertEqual(1, len(titles), text)
            self.assertFalse(titles[0].runs[0].bold, text)
        rows = self.hwpx("food_crops")
        for text in ("표 목차", "그림 목차"):
            self.assertEqual([(20, False)], [shape for label, shape in rows if label == text], text)
        self.assertEqual([(18, True)] * 3,
                         [shape for label, shape in rows if label == "농업전문학사 학위논문"])

    def test_docx_list_title_size_and_weight_follow_profile(self):
        gmf = runtime("gg_major_format")
        titles = dict(gmf.DAIRY_PROFILE.front_titles)
        titles["표 목차"] = (18, False)
        titles["그림 목차"] = (16, False)
        changed = replace(gmf.DAIRY_PROFILE, front_titles=gmf._freeze(titles))
        with mock.patch.object(runtime("build_docx"), "docx_profile", return_value=changed):
            doc = self.docx("dairy")
        for text, pt in (("표 목차", 18), ("그림 목차", 16)):
            title = next(p for p in doc.paragraphs if p.text == text
                         and p.runs[0].font.size.pt == pt)
            self.assertFalse(title.runs[0].bold)


if __name__ == "__main__":
    unittest.main()
