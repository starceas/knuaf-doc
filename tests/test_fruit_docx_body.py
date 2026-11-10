"""S01 body/preliminary/appendix preservation through the real DOCX builder."""
from tests._harness import ContractCase, bind_major, runtime


class FruitBodyTests(ContractCase):
    def test_body_and_preliminary_content_survive_with_linked_contents(self):
        from docx.oxml.ns import qn
        root = self.make_project()
        bind_major(root, "fruit_trees")
        fm = runtime("gg_frontmatter")
        mc = runtime("gg_major_contract")
        builder = runtime("build_docx")
        profile = fm.normalize_school_profile({"major_id": "fruit_trees",
            "school_profile": {"title": "[과수 계획 제목]", "author": "[작성자]",
                "advisor": "[지도교수]", "submission_date": "2026년 12월",
                "graduation_date": "2027년 2월"}})
        headings = ["Ⅰ. 머리말", "Ⅱ. 영농환경분석", "Ⅲ. 영농계획", "Ⅳ. 맺음말",
                    "참고문헌", "학부모 동의서", "감사의 글", "부록(재무계획)"]
        body = ["요약", "합성 요약: 재무는 엑셀 확인 후 반영한다."]
        for i, heading in enumerate(headings):
            body += ["# " + heading, "합성 본문 구간 %d" % i]
        for i, role in enumerate(mc._FRUIT_APPENDIX_ROLES, 1):
            body += ["# %d. %s" % (i, role), "[확인 필요 — 엑셀 작성 후]"]
        body += ["표 1. 합성 항목", "| 항목 | 상태 |\n| --- | --- |\n| 자료 | 확인 필요 |",
                 "그림 1. 합성 도면", "[도면 확인 필요]"]
        doc = builder.convert("\n\n".join(fm.frontmatter_lines(profile) + body),
                              context=mc.output_context(root, "fruit_trees"))
        output = root / "fruit.docx"
        doc.save(output)
        doc = builder.Document(output)
        paragraphs = [p.text for p in doc.paragraphs]
        text = "\n".join(paragraphs)
        positions = [paragraphs.index(h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(text.index("합성 요약:"), text.index("합성 본문 구간 0"))
        for i in range(len(headings)):
            self.assertEqual(paragraphs.count("합성 본문 구간 %d" % i), 1)
        self.assertEqual(text.count("[확인 필요 — 엑셀 작성 후]"), 15)
        self.assertEqual(doc.tables[0].cell(1, 1).text, "확인 필요")
        fields = [e.get(qn("w:instr"), "")
                  for e in doc.element.iter(qn("w:fldSimple"))]
        self.assertTrue(any(f.split()[0] == "PAGEREF" for f in fields if f.split()))
        self.assertIn("표 1. 합성 항목", text)
        self.assertIn("그림 1. 합성 도면", text)
        bookmarks = {e.get(qn("w:name")) for e in doc.element.iter(qn("w:bookmarkStart"))}
        for instruction in fields:
            parts = instruction.split()
            if parts and parts[0] == "PAGEREF":
                self.assertIn(parts[1], bookmarks)
