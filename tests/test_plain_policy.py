"""R-all-1 평문 논문 정책 — 모든 전공 공통 기본값과 majors 재정의.

기존 P1~P6 반례와 표·그림 출처·사진 정책을 모든 입력에 적용한다.
"""
import json
import random
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests._harness import (
    ContractCase,
    bind_major,
    runtime,
    section_op,
    source_op,
    write_text,
)

doc = runtime("gg_document")
core = runtime("gg_core")

SC = "specialty_crops"
OTHER_MAJOR = "industrial_insects"
ALL_MAJORS = (SC, "fruit_trees", OTHER_MAJOR, "hort_env_systems", None, "unknown_major")

_ERROR_IDS = {
    "forbidden_term",
    "front_matter_over",
    "caption_forbidden",
    "plain_policy_file",
    "object_credit",
    "image_ai_generated",
}
_WARNING_IDS = {
    "term_limit",
    "front_matter_long",
    "caption_long",
    "photo_placeholder",
}


def ids(issues):
    return {cid for cid, _ in issues}


def check(text, major_id=SC, root=None):
    return doc.check(text, root, major_id=major_id)


class PlainPolicyBody(ContractCase):
    def test_p1_minimal_text_has_no_count_warning(self):
        issues = check("산약 재배 계획\n")
        assert "object_count" not in ids(issues)
        assert "sourced_objects" in ids(issues)

    def test_p2_short_text_has_no_count_warning(self):
        # 학교 구조 검사가 건너뛰는 짧은 입력에도 정책 게이트가 돈다.
        issues = check("손익분기점은 3년차다.\n")
        assert "object_count" not in ids(issues)
        assert "sourced_objects" in ids(issues)

    def test_p3_forbidden_finance_terms(self):
        issues = check("NPV IRR B/C 할인율 민감도를 계산했다.\n")
        forbidden = [i for i in issues if i[0] == "forbidden_term"]
        assert len(forbidden) >= 5
        assert {"NPV", "IRR"} <= {
            t.split(": ")[-1] for _, t in forbidden
        }

    def test_benefit_cost_ratio_and_ratio_allowed(self):
        # 허용 비율 용어는 금칙에 걸리지 않아야 한다.
        issues = check("소득률과 순이익률, 부채비율을 함께 본다.\n")
        assert "forbidden_term" not in ids(issues)

    def test_bep_over_limit_warns(self):
        assert "term_limit" in ids(check("손익분기 " * 8))
        assert "term_limit" not in ids(check("손익분기 " * 5))

    def test_p4_long_front_matter(self):
        over = check("# Ⅰ. 머리말\n" + "가" * 2600 + "\n")
        assert "front_matter_over" in ids(over)
        long_ = check("# Ⅰ. 머리말\n" + "가" * 1900 + "\n")
        assert "front_matter_long" in ids(long_)
        assert "front_matter_over" not in ids(long_)
        short = check("# Ⅰ. 머리말\n" + "가" * 1100 + "\n")
        assert not ids(short) & {"front_matter_over", "front_matter_long"}

    def test_front_matter_exact_boundaries_and_raw_table_text(self):
        for length, expected in ((1800, None), (1801, "front_matter_long"),
                                 (2500, "front_matter_long"), (2501, "front_matter_over")):
            found = ids(check("# Ⅰ. 머리말\n" + "가 " * length))
            assert found & {"front_matter_long", "front_matter_over"} == (
                {expected} if expected else set())
        body = "\n가. 계획\n표 1. 계획\n|항목|값|\n|---|---|\n|가|1|\n출처: 본인 작성\n"
        expected = len("".join(body.split()))
        assert doc._front_matter_length(
            "# Ⅰ. 머리말\n" + body + "# Ⅱ. 현황\n" + "나" * 3000,
            "머리말",
        ) == expected

    def test_p5_hard_caption(self):
        text = (
            "표 1. 20년 NPV≥0 결합 승인 조건\n\n"
            "|a|b|\n|-|-|\n|1|2|\n"
        )
        assert "caption_forbidden" in ids(check(text))

    def test_caption_long_warns(self):
        text = "표 1. " + "가" * 40 + "\n\n|a|\n|-|\n|1|\n"
        found = ids(check(text))
        assert "caption_long" in found
        assert "caption_forbidden" not in found

    def test_chapter4_has_no_table_count_minimum(self):
        text = "# Ⅳ. 재무 계획\n\n계획을 설명한다.\n"
        assert "object_count" not in ids(check(text))
        assert ("sourced_objects", "출처 있는 표 0개·그림 0개") in check(text)

    def test_normal_document_has_no_policy_error(self):
        text = (
            "# Ⅰ. 머리말\n"
            "이 계획은 산약 재배의 경험을 정리한 것이다.\n\n"
            "표 1. 재배 일정\n\n|월|작업|\n|-|-|\n|3|정식|\n출처: 본인 작성\n"
        )
        assert not ids(check(text)) & _ERROR_IDS

    def test_photo_placeholder_warns(self):
        issues = check("정식 모습. [사진 필요: 정식 직후 밭 전경]\n")
        assert "photo_placeholder" in ids(issues)

    def _root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def test_ai_image_marker_rejected(self):
        issues = check(
            "![밭 전경 ai_generated](images/ai_generated_1.png)\n",
            root=self._root(),
        )
        assert "image_ai_generated" in ids(issues)

    def test_image_without_credit_is_error(self):
        issues = check(
            "![밭 전경](images/field.png)\n그림 1. 밭 전경\n",
            root=self._root(),
        )
        assert "object_credit" in ids(issues)
        assert "image_credit" not in ids(issues)
        for credit in ("출처: 본인 촬영", "출처 : 농촌진흥청"):
            credited = check(
                "![밭 전경](images/field.png)\n그림 1. 밭 전경\n" + credit,
                root=self._root(),
            )
            assert "object_credit" not in ids(credited)
            assert ("sourced_objects", "출처 있는 표 0개·그림 1개") in credited

    def test_caption_or_path_is_not_a_credit(self):
        issues = check(
            "![출처 본인 촬영](images/출처-field.png)\n그림 1. 본인 촬영 밭 전경\n",
            root=self._root(),
        )
        assert "object_credit" in ids(issues)



    def test_ai_caption_or_credit_is_rejected(self):
        for caption, credit in (("밭 전경", "출처: AI 생성"),
                                ("AI 생성 밭 전경", "출처: 본인 작성")):
            text = "![밭 전경](field.png)\n그림 1. " + caption + "\n" + credit
            assert "image_ai_generated" in ids(check(text, root=self._root()))
            assert "image_ai_generated" in ids(check(text, major_id=OTHER_MAJOR, root=self._root()))


class PlainPolicyReviewFixes(ContractCase):
    def test_labelled_summary_headings_do_not_hide_the_first_body_chapter(self):
        summary = "요약\nI. 사업의 개요\n요약 내용\nII. 투자분석\n요약 내용\n"
        for major in ALL_MAJORS:
            for heading in ("Ⅰ. 머리말", "Ⅰ 서론", "1. 서론"):
                with self.subTest(major=major, heading=heading):
                    text = summary + heading + "\n" + "가" * 2501 + "\nⅡ. 계획\n나"
                    assert "front_matter_over" in ids(check(text, major_id=major))
                    if heading != "1. 서론":
                        assert doc._front_matter_length(text, ["머리말", "서론"]) == 2501

    def test_real_first_chapter_titles_and_exact_boundaries_for_every_major(self):
        titles = ("Ⅰ. 머리말", "I. 머리말", "Ⅰ 머리말", "1. 머리말",
                  "Ⅰ. 서론", "I. 서론", "Ⅰ 서론", "1. 서론")
        boundaries = ((1800, set()), (1801, {"front_matter_long"}),
                      (2500, {"front_matter_long"}), (2501, {"front_matter_over"}))
        for major in ALL_MAJORS:
            for title in titles:
                for prefix in ("", "# "):
                    for length, expected in boundaries:
                        with self.subTest(major=major, title=prefix + title, length=length):
                            out = ids(check(prefix + title + "\n" + "가 " * length,
                                            major_id=major))
                            self.assertEqual(out & {"front_matter_long", "front_matter_over"},
                                             expected)

    def test_first_chapter_stops_at_next_chapter_and_retains_subheadings(self):
        for first, second in (("Ⅰ 서론", "Ⅱ 현황"), ("I. 머리말", "II. 현황"),
                              ("1. 서론", "2. 현황")):
            content = "가 " * 20 + "\n## 1. 연구목적\n나. 계획\n|항목|값|\n|---|---|\n|가|1|\n"
            text = first + "\n" + content + second + "\n" + "나" * 3000
            self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]),
                             len("".join(content.split())))
        # Arabic subsection titles in a Roman-numbered first chapter are not chapter ends.
        content = "가\n1. 연구목적\n나\n2. 연구방법\n다\n"
        self.assertEqual(doc._front_matter_length("Ⅰ 서론\n" + content + "Ⅱ 현황\n라",
                                                 ["머리말", "서론"]),
                         len("".join(content.split())))

    def test_later_or_lower_level_intro_is_not_the_first_chapter(self):
        for title in ("Ⅰ. 본론\nⅡ. 서론", "1. 개요\n2. 서론",
                      "## Ⅰ. 서론", "Ⅰ. 서론적 검토"):
            self.assertIsNone(doc._front_matter_length(title + "\n" + "가" * 2501,
                                                       ["머리말", "서론"]))

    def test_ascii_terms_ignore_case_but_do_not_match_inside_latin_words(self):
        for major in ALL_MAJORS:
            with self.subTest(major=major):
                out = check("npv nPv npv를 irr Irr은 bcr BcR은 b/c B/c가", major_id=major)
                counts = {reason.split(": ")[-1]: int(reason.split(" ")[4].split("회")[0])
                          for cid, reason in out if cid == "forbidden_term"}
                self.assertEqual(counts, {"NPV": 3, "IRR": 2, "BCR": 2, "B/C": 2})
                self.assertNotIn("forbidden_term", ids(check(
                    "irrigation preNPVpost BCRValue b/catalog npv_value IRR2", major_id=major)))
                for term in ("npv", "iRr", "bCr", "b/c"):
                    text = "표 1. " + term + "\n|값|\n|---|\n|1|\n출처: 본인 작성"
                    self.assertIn("caption_forbidden", ids(check(text, major_id=major)))
                self.assertNotIn("caption_forbidden", ids(check(
                    "표 1. irrigation\n|값|\n|---|\n|1|\n출처: 본인 작성", major_id=major)))

    def test_limited_term_counts_printed_body_cells_and_captions_once(self):
        def text(extra):
            body = "손익분기 손익분기" + (" 손익분기" if extra == "body" else "")
            cells = "손익분기 손익분기" + (" 손익분기" if extra == "cell" else "")
            tables = ("표 1. 손익분기점\n|항목|값|\n|---|---|\n|" + cells
                      + "|1|\n출처: 본인 작성\n"
                      "표 2. 손익분기점\n|값|\n|---|\n|2|\n출처: 본인 작성")
            if extra == "caption":
                tables += "\n표 3. 손익분기점\n|값|\n|---|\n|3|\n출처: 본인 작성"
            return body + "\n" + tables
        for major in ALL_MAJORS:
            self.assertNotIn("term_limit", ids(check(text(None), major_id=major)))
            for extra in ("body", "cell", "caption"):
                with self.subTest(major=major, extra=extra):
                    rows = [(cid, reason) for cid, reason in check(text(extra), major_id=major)
                            if cid == "term_limit"]
                    self.assertEqual(rows, [("term_limit", "제한 용어 손익분기 7회(상한 6회)")])

    def test_seven_caption_only_mentions_warn(self):
        text = "\n".join("표 %d. 손익분기점\n|값|\n|---|\n|1|\n출처: 본인 작성" % i
                         for i in range(1, 8))
        self.assertIn(("term_limit", "제한 용어 손익분기 7회(상한 6회)"), check(text))

    def _registered(self, major, **meta):
        root = self.make_project()
        if major is not None:
            bind_major(root, major)
        raw = "학생의 직접 견학 원답변\n"
        write_text(root, "answer.txt", raw)
        op = source_op()
        op["value"].update(meta)
        core.apply(root, {"request_id": "f-author-survey", "ops": [op]},
                   core.load(root)["revision"])
        self.assertEqual((root / "answer.txt").read_text(), raw)
        return root

    def test_author_survey_interviews_allowed_with_either_documented_marker(self):
        for major in ALL_MAJORS[:-1]:
            for marker in ({"source_type": "author_survey", "source": "사용자 견학"},
                           {"source": "작성자 직접 조사"}):
                with self.subTest(major=major, marker=marker):
                    root = self._registered(major, kind="interview", verified="user-stated",
                                            grade=5, **marker)
                    project = core.load(root)
                    self.assertEqual(project["sources"]["answer"]["verified"], "user-stated")
                    for key, value in marker.items():
                        self.assertEqual(project["sources"]["answer"][key], value)
                    rows = [r for r in core.checks(root, project) if r["check_id"] in
                            {"source_kind_forbidden", "source_grade_forbidden"}]
                    self.assertEqual(rows, [])

    def test_external_interviews_and_partial_or_article_markers_still_rejected(self):
        rejected = ({}, {"verified": "user-stated", "source": "사용자 견학"},
                    {"source_type": "external_interview"},
                    {"source": "기사에 나온 작성자 직접 조사"},
                    {"source_type": "author_survey", "kind": "news"},
                    {"source": "작성자 직접 조사", "kind": "blog"})
        for major in ALL_MAJORS[:-1]:
            for marker in rejected:
                with self.subTest(major=major, marker=marker):
                    root = self._registered(major, **dict({"kind": "interview"}, **marker))
                    rows = [r for r in core.checks(root, core.load(root))
                            if r["check_id"] == "source_kind_forbidden"]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["status"], rows[0]["severity"]), ("fail", "error"))

    def test_author_marker_does_not_exempt_forbidden_grade(self):
        for grade in (3, 4, "3", "4"):
            root = self._registered(SC, kind="interview", source_type="author_survey", grade=grade)
            out = core.checks(root, core.load(root))
            self.assertNotIn("source_kind_forbidden", {r["check_id"] for r in out})
            self.assertIn("source_grade_forbidden", {r["check_id"] for r in out})


class PlainPolicyF2Fixes(ContractCase):
    def test_summary_ends_at_first_body_chapter_even_with_later_intro(self):
        for major in ALL_MAJORS[:-1]:
            for length in (1800, 1801, 2500, 2501):
                for subheading in ("1. 서론", "## 1. 서론"):
                    with self.subTest(major=major, length=length, subheading=subheading):
                        text = ("# 요약\n사업 요약\n# Ⅰ. 영농계획\n본문의 서론을 살펴본다.\n"
                                + subheading + "\n" + "가" * length + "\n# Ⅱ. 현황\n나")
                        self.assertIsNone(doc._front_matter_length(text, ["머리말", "서론"]))
                        self.assertFalse(ids(check(text, major_id=major)) &
                                         {"front_matter_long", "front_matter_over"})
                        child = next(n for n in doc.parse(text) if n.get("text") == "1. 서론")
                        self.assertEqual((child["kind"], child["level"]), ("heading", 2))
            for prefix in ("", "# 요약\n사업 요약\n", "# 요약\n## I. 사업 요약\n내용\n"):
                text = prefix + "# Ⅰ. 영농계획\n서론을 살펴본다.\n# 1. 서론\n" + "가" * 2501
                self.assertIsNone(doc._front_matter_length(text, ["머리말", "서론"]))

    def test_prose_and_equal_level_subsections_preserve_exact_length_boundaries(self):
        cases = (("# Ⅰ. 머리말", "가\nI study agriculture.\n", "# Ⅱ. 계획"),
                 ("# 1. 서론", "가\n1. 연구목적\n", "# 2. 현황"),
                 ("# 1. 서론", "가\n## 1. 연구목적\n", "# 2. 현황"))
        for major in ALL_MAJORS[:-1]:
            for heading, prefix, end in cases:
                for length, expected in ((1800, set()), (1801, {"front_matter_long"}),
                                         (2500, {"front_matter_long"}), (2501, {"front_matter_over"})):
                    with self.subTest(major=major, heading=heading, prefix=prefix, length=length):
                        content = prefix + "나" * (length - len("".join(prefix.split())))
                        text = heading + "\n" + content + "\n" + end + "\n" + "다" * 3000
                        self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]), length)
                        self.assertEqual(ids(check(text, major_id=major)) &
                                         {"front_matter_long", "front_matter_over"}, expected)
                        if "I study" in prefix:
                            node = next(n for n in doc.parse(text) if n["text"] == "I study agriculture.")
                            self.assertEqual(node["kind"], "paragraph")
                        else:
                            node = next(n for n in doc.parse(text) if n["text"] == "1. 연구목적")
                            self.assertEqual((node["kind"], node["level"]), ("heading", 2))

    def test_raw_spans_include_tables_and_stop_at_actual_level_one(self):
        content = "가\n## 1. 연구목적\n표 1. 계획\n|항목|값|\n|---|---|\n|가|1|\n출처: 본인 작성\n"
        for end in ("# Ⅱ. 현황", "# 2. 현황", "# 현황"):
            text = "# Ⅰ. 머리말\n" + content + end + "\n" + "나" * 3000
            self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]),
                             len("".join(content.split())))
            nodes = doc.parse(text, with_spans=True)
            self.assertEqual([{k: v for k, v in n.items() if k not in {"start_line", "end_line"}}
                              for n in nodes], doc.parse(text))
            table = next(n for n in nodes if n["kind"] == "table")
            raw = "\n".join(core.draft(text).splitlines()[table["start_line"]:table["end_line"]])
            self.assertIn("|---|---|", raw)

    def test_plain_number_fallback_rejects_english_pronoun_as_a_chapter(self):
        content = "가\nI study agriculture.\n1. 연구목적\n2. 연구방법\n" + "나" * 2501 + "\n"
        text = "Ⅰ 머리말\n" + content + "Ⅱ 계획\n다"
        self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]),
                         len("".join(content.split())))
        for raw in ("요약\n본문 요약\nⅠ 영농계획\n1. 서론\n", "Ⅰ 영농계획\n서론을 살펴본다.\n"):
            self.assertIsNone(doc._front_matter_length(raw + "가" * 2501, ["머리말", "서론"]))

    def test_interview_display_contract_rejects_explicit_type_conflicts(self):
        raw = "신문 기자가 타인 농장주를 인터뷰한 합성 기사. 학생 본인 조사가 아니다.\n"
        types = ("external_interview", "external_news", "external_author_survey",
                 "external_", "news", "blog", "interview", "sns", "cafe", "shop", "press")
        for major in ALL_MAJORS[:-1]:
            for source_type in types:
                with self.subTest(major=major, source_type=source_type):
                    root = self.make_project()
                    if major:
                        bind_major(root, major)
                    write_text(root, "answer.txt", raw)
                    op = source_op()
                    op["value"].update(kind="interview", source="작성자 직접 조사",
                                       source_type=source_type)
                    core.apply(root, {"request_id": "f2-conflict", "ops": [op]}, core.load(root)["revision"])
                    project = core.load(root)
                    rows = [r for r in core.checks(root, project) if r["check_id"] == "source_kind_forbidden"]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["status"], rows[0]["severity"]), ("fail", "error"))
                    self.assertEqual((root / "answer.txt").read_text(), raw)
                    self.assertEqual(project["sources"]["answer"]["hash"], core.digest(raw.encode()))

    def test_marked_disguise_is_not_automatically_classified_from_raw_content(self):
        # The F2 disposition accepts a metadata trust contract, not truth inference.
        raw = "신문 기자가 타인 농장주를 인터뷰한 합성 기사. 학생 본인 조사가 아니다.\n"
        for major in ALL_MAJORS[:-1]:
            root = self.make_project()
            if major:
                bind_major(root, major)
            write_text(root, "answer.txt", raw)
            op = source_op()
            op["value"].update(kind="interview", source="타인 기사형 인터뷰", source_type="author_survey")
            core.apply(root, {"request_id": "f2-trust-boundary", "ops": [op]}, core.load(root)["revision"])
            rows = [r for r in core.checks(root, core.load(root)) if r["check_id"] in
                    {"source_kind_forbidden", "source_grade_forbidden"}]
            self.assertEqual(rows, [])
            self.assertEqual((root / "answer.txt").read_text(), raw)


class PlainPolicyF3Properties(ContractCase):
    @staticmethod
    def suffixes():
        # Fixed seed: same generated corpus in pytest and unittest discovery.
        atoms = ("## 1. 개요\n나", "1. 개요\n나", "### 개요\n나", "2 . 계획\n다",
                 "I study agriculture.", "I . 계획", "Ⅰ. 계획", "# 1. 서론\n새 본문",
                 "요약", "초록", "Abstract", "요약을 닮은 문구", "\n\n",
                 "# 요약\n## I . 개요", "1. Abstract", "- 1. 서론", "나. 계획",
                 "표 1. 계획\n|항목|값|\n|---|---|\n|가|1|\n출처: 본인 작성",
                 "[사진 필요: 예정지]", "\t", "## Ⅰ . 서론", "본문의 서론을 살펴본다.", "# 1٠ . 현황")
        rng = random.Random(20261007)
        return ("", *atoms, *("\n".join(rng.choices(atoms, k=rng.randint(2, 6)))
                               for _ in range(48)))

    @staticmethod
    def content(length):
        # Oracle is the known raw interval C, not a reconstructed node tree.
        core = ("가\nI study agriculture.\n1 . 연구목적\n나. 계획\n표 1. 계획\n"
                "|항목|값|\n|---|---|\n|가|1|\n출처: 본인 작성\n")
        return core + "나" * (length - len("".join(core.split()))) + "\n"

    def test_generated_suffix_preserves_length_and_all_prefix_nodes(self):
        summary = "요약\nI . 사업의 개요\n내용\nII . 투자분석\n내용\n"
        pairs = ((summary, "Ⅰ. 머리말", "Ⅱ. 현황"),
                 (summary, "I . 서론", "II . 현황"),
                 (summary, "1 . 서론", "Ⅱ . 현황"),
                 ("# Abstract\n## I . 개요\n내용\n", "# 1. 서론", "# 현황"),
                 ("", "1 . 머리말", "2 . 현황"),
                 ("", "# Ⅰ. 머리말", "# 2. 현황"))
        for preface, first, end in pairs:
            for length in (1800, 1801, 2500, 2501):
                prefix = preface + first + "\n" + self.content(length) + end + "\n"
                before = doc.parse(prefix, with_spans=True)
                self.assertEqual((before[-1]["kind"], before[-1]["level"]), ("heading", 1))
                for suffix in self.suffixes():
                    with self.subTest(first=first, end=end, length=length, suffix=suffix):
                        text = prefix + suffix
                        self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]), length)
                        self.assertEqual(doc.parse(text, with_spans=True)[:len(before)], before)
                # Arbitrary invalid suffix syntax cannot change L either. Full
                # document rendering still rejects unsupported Markdown.
                self.assertEqual(doc._front_matter_length(prefix + "```bad\n<script>",
                                                          ["머리말", "서론"]), length)
                expected = set() if length <= 1800 else {
                    "front_matter_over" if length > 2500 else "front_matter_long"}
                for major in ALL_MAJORS[:-1]:
                    for suffix in ("1. 개요\n나", "## 1. 개요\n나", "### 개요\n나"):
                        with self.subTest(major=major, first=first, length=length, suffix=suffix):
                            self.assertEqual(ids(check(prefix + suffix, major_id=major)) &
                                             {"front_matter_long", "front_matter_over"}, expected)

    def test_number_and_summary_notations_share_the_same_body_decision(self):
        summary_titles = ("요약", "초록", "Abstract", "ABSTRACT", "# 요약", "# 초록",
                          "# Abstract", "I . 요약", "Ⅰ. 초록", "1 . Abstract", "# 1 . 초록")
        sections = ("I. 사업의 개요\n내용\nII. 투자분석\n내용\n",
                    "I . 사업의 개요\n내용\nII . 투자분석\n내용\n",
                    "I． 사업의 개요\n내용\nII ． 투자분석\n내용\n",
                    "## I . 사업의 개요\n내용\n### II . 투자분석\n내용\n",
                    "## Ⅰ . 사업의 개요\n내용\n### Ⅱ . 투자분석\n내용\n")
        for title in summary_titles:
            for section in sections:
                for first, end in (("I. 서론", "II. 현황"), ("I . 서론", "II . 현황"),
                                   ("Ⅰ. 서론", "Ⅱ. 현황"), ("Ⅰ . 서론", "Ⅱ . 현황"),
                                   ("Ⅰ 서론", "Ⅱ 현황"), ("1. 서론", "2. 현황"),
                                   ("1 . 서론", "2 . 현황"), ("# I . 서론", "# II . 현황")):
                    with self.subTest(summary=title, section=section, first=first):
                        text = title + "\n" + section + first + "\n" + self.content(2501) + end + "\n"
                        self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]), 2501)
                        nodes = doc.parse(text)
                        child = next(n for n in nodes if n.get("text") == "1 . 연구목적")
                        self.assertEqual((child["kind"], child["level"]), ("heading", 2))
                        self.assertEqual((nodes[-1]["kind"], nodes[-1]["level"]), ("heading", 1))

    def test_first_non_intro_consumes_summary_once_for_generated_suffixes(self):
        for summary in ("", "# 요약\n내용\n", "초록\nI . 개요\nII . 분석\n",
                        "1 . Abstract\n## I . 개요\n내용\n"):
            prefix = summary + "# Ⅰ. 영농계획\n본문의 서론을 살펴본다.\nⅡ. 현황\n"
            for suffix in self.suffixes():
                with self.subTest(summary=summary, suffix=suffix):
                    self.assertIsNone(doc._front_matter_length(prefix + suffix +
                        "\n# 요약\n# 1. 서론\n" + "가" * 2501, ["머리말", "서론"]))
            for subheading in ("1. 서론", "## 1. 서론"):
                for major in ALL_MAJORS[:-1]:
                    self.assertFalse(ids(check(prefix + subheading + "\n" + "가" * 2501,
                                               major_id=major)) &
                                     {"front_matter_long", "front_matter_over"})

    def test_main_heading_spacing_is_identical_in_parser_and_length_gate(self):
        for numeral in ("I", "Ⅰ", "1"):
            for dot in (".", "．"):
                for before in ("", " ", "\t"):
                    for after in ("", " ", "\t"):
                        heading = numeral + before + dot + after + "서론"
                        with self.subTest(heading=heading):
                            text = heading + "\n" + "가" * 2501 + "\n# 현황\n나"
                            node = doc.parse(text)[0]
                            self.assertEqual((node["kind"], node["level"]), ("heading", 1))
                            self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]), 2501)
        # Every decimal numeral matched by the common grammar must take
        # the numeric branch, including Unicode tails after an ASCII digit.
        for numeral, number in (("1٠", 10), ("1２", 12)):
            for dot in (".", "．"):
                for before in ("", " "):
                    for after in ("", " "):
                        heading = numeral + before + dot + after + "현황"
                        with self.subTest(decimal=heading):
                            self.assertEqual(doc._numbered_heading(heading),
                                             ("arabic", number, "현황", False))
                            text = "Ⅰ . 서론\n" + "가" * 2501 + "\n# " + heading + "\n나"
                            self.assertEqual(doc._front_matter_length(text, ["머리말", "서론"]), 2501)
                            self.assertEqual(doc.parse(text)[-2]["level"], 1)
        for prose in ("I study agriculture.", "I .", "1.2 자료", "I 서론"):
            self.assertIsNone(doc._front_matter_length(prose + "\n" + "가" * 2501,
                                                       ["머리말", "서론"]))


class PlainPolicyScope(ContractCase):
    """전공 여부와 관계없이 같은 기본 정책을 적용한다."""

    BAD_BODY = (
        "# Ⅰ. 머리말\n"
        "NPV IRR B/C 할인율 민감도를 계산했다.\n" + "가" * 2600 + "\n\n"
        "표 1. 20년 NPV≥0 결합 승인 조건\n|a|\n|-|\n|1|\n"
        "![밭](field.png)\n그림 1. 밭 전경\n"
        "[사진 필요: 정식 직후 밭]\n"
    )

    def test_every_major_rejects_same_body_and_uncredited_objects(self):
        reference = doc.check(self.BAD_BODY, None, major_id=SC)
        for major in ALL_MAJORS:
            with self.subTest(major=major):
                out = doc.check(self.BAD_BODY, None, major_id=major)
                assert out == reference
                assert {"forbidden_term", "front_matter_over", "caption_forbidden",
                        "object_credit", "photo_placeholder", "sourced_objects"} <= ids(out)
                missing = [reason for cid, reason in out if cid == "object_credit"]
                assert len(missing) == 2
                assert any("표" in reason for reason in missing)
                assert any("그림" in reason for reason in missing)
                assert all("미적용" not in reason for _, reason in out)

    def test_projects_reject_same_article_body_and_uncredited_objects(self):
        for major in ALL_MAJORS[:-1]:
            with self.subTest(major=major):
                root = self.make_project()
                if major is not None:
                    bind_major(root, major)
                write_text(root, "answer.txt", "답변 원문\n")
                write_text(root, "sections/s1.md", self.BAD_BODY)
                op = source_op()
                op["value"]["kind"] = "news"
                sec = section_op("s1", "Ⅰ. 머리말", "sections/s1.md")
                core.apply(root, {"request_id": "all-majors", "ops": [op, sec]},
                           core.load(root)["revision"])
                out = core.checks(root, core.load(root))
                for cid in ("source_kind_forbidden", "forbidden_term",
                            "front_matter_over", "object_credit"):
                    rows = [row for row in out if row["check_id"] == cid]
                    assert rows
                    assert all((row["severity"], row["status"]) == ("error", "fail")
                               for row in rows)
                assert all("미적용" not in row["reason"] for row in out)

    def test_normal_body_passes_for_all_majors(self):
        for major in ALL_MAJORS:
            with self.subTest(major=major):
                assert doc.check("간단한 재배 계획이다.\n", None, major_id=major) == [
                    ("sourced_objects", "출처 있는 표 0개·그림 0개")]

    def test_no_major_project_applies_default_policy(self):
        root = self.make_project()
        rows = [row for row in core.checks(root, core.load(root))
                if row["check_id"] == "sourced_objects"]
        assert len(rows) == 1
        assert (rows[0]["severity"], rows[0]["status"]) == ("info", "pass")
        assert "forbidden_term" in ids(doc.check("NPV를 계산했다.", str(root)))

    def test_check_resolves_major_from_project_root(self):
        root = self.make_project()
        bind_major(root, SC)
        assert "forbidden_term" in ids(doc.check("NPV를 계산했다.\n", str(root)))


class PlainPolicySources(ContractCase):
    def _project_with_source(self, major=SC, **extra):
        root = self.make_project()
        if major is not None:
            bind_major(root, major)
        write_text(root, "answer.txt", "답변 원문\n")
        op = source_op()
        op["value"].update(extra)
        rev = core.load(root)["revision"]
        core.apply(
            root,
            {"request_id": "w3-src", "ops": [op]},
            rev,
        )
        return root

    def _source_ids(self, root):
        p = core.load(root)
        out = core.checks(root, p)
        return {r["check_id"] for r in out}, out

    def test_p6_forbidden_source_kind(self):
        for kind in ("news", "blog", "interview", "sns", "cafe", "shop", "press"):
            root = self._project_with_source(kind=kind)
            found, out = self._source_ids(root)
            assert "source_kind_forbidden" in found, kind
            row = [r for r in out if r["check_id"] == "source_kind_forbidden"][0]
            assert row["severity"] == "error"

    def test_p6_forbidden_grade(self):
        for grade in (3, 4, "3", "4"):
            root = self._project_with_source(grade=grade)
            found, _ = self._source_ids(root)
            assert "source_grade_forbidden" in found, grade

    def test_p6_allowed_grade(self):
        root = self._project_with_source(grade=1, kind="official")
        found, _ = self._source_ids(root)
        assert "source_kind_forbidden" not in found
        assert "source_grade_forbidden" not in found

    def test_legacy_source_untouched(self):
        # kind/grade가 없는 기존 기록은 계속 유효해야 한다.
        root = self._project_with_source()
        found, _ = self._source_ids(root)
        assert "source_kind_forbidden" not in found
        assert "source_grade_forbidden" not in found

    def test_ai_image_source_rejected(self):
        root = self._project_with_source(kind="ai_generated")
        found, _ = self._source_ids(root)
        assert "image_ai_generated" in found
        assert "source_kind_forbidden" not in found

    def test_source_policy_applies_to_all_majors(self):
        for major in ALL_MAJORS[:-1]:
            with self.subTest(major=major):
                root = self._project_with_source(major=major, kind="news", grade=4)
                found, _ = self._source_ids(root)
                assert {"source_kind_forbidden", "source_grade_forbidden"} <= found
                root = self._project_with_source(major=major, kind="ai_generated")
                found, _ = self._source_ids(root)
                assert "image_ai_generated" in found


class PlainPolicyFile(ContractCase):
    def _swap_policy(self, target):
        original = doc.POLICY_PATH
        doc.POLICY_PATH = Path(target)
        self.addCleanup(setattr, doc, "POLICY_PATH", original)

    def test_missing_policy_fails_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._swap_policy(Path(tmp.name) / "none.json")
        with self.assertRaises(ValueError):
            doc.load_plain_policy()
        issues = check("짧은 문서\n")
        assert "plain_policy_file" in ids(issues)
        root = self.make_project()
        found = {r["check_id"] for r in core.checks(root, core.load(root))}
        assert "plain_policy" in found

    def test_malformed_policy_fails_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = Path(tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self._swap_policy(bad)
        with self.assertRaises(ValueError):
            doc.load_plain_policy()
        assert "plain_policy_file" in ids(check("문서\n"))

    def test_missing_keys_fail_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = Path(tmp.name) / "shallow.json"
        bad.write_text(json.dumps({"caption": {}}), encoding="utf-8")
        self._swap_policy(bad)
        with self.assertRaises(ValueError):
            doc.load_plain_policy()

    def test_real_policy_loads(self):
        policy = doc.load_plain_policy()
        assert "NPV" in policy["forbidden_body_terms"]
        assert policy["sources"]["forbidden_grades"] == [3, 4]
        assert "applies_to_majors" not in policy
        assert "counts" not in policy
        assert policy["majors"] == {SC: {}}
        assert "objects" in policy

    def test_invalid_nested_fields_and_deprecated_counts_fail_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        original = doc.load_plain_policy()
        changes = [
            ("objects", "scan_lines", True),
            ("objects", "credit_labels", [7]),
            ("images", "ai_markers", "ai_generated"),
            ("sources", "forbidden_grades", ["3"]),
            ("front_matter", "title_keywords", None),
            ("front_matter", "title_keywords", []),
            ("front_matter", "title_keywords", [None]),
        ]
        for group, key, value in changes:
            data = json.loads(json.dumps(original))
            data[group][key] = value
            path = Path(tmp.name) / "bad.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                doc.load_plain_policy(path)
        for data in (
            dict(original, counts={"tables_min": 60}),
            dict(original, majors={SC: {"counts": {"chapter4_tables_min": 25}}}),
            dict(original, majors={SC: {"objects": {"scan_lines": 0}}}),
        ):
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                doc.load_plain_policy(path)

    def test_major_overrides_do_not_spill_into_other_majors(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = doc.load_plain_policy()
        data["majors"][SC] = {"caption": {"warn_chars": 5}}
        path = Path(tmp.name) / "override.json"
        path.write_text(json.dumps(data))
        self._swap_policy(path)
        text = "표 1. 가" + "나" * 10 + "\n|항목|\n|---|\n|값|\n출처: 본인 작성"
        assert "caption_long" in ids(check(text))
        for major in ALL_MAJORS[1:]:
            assert "caption_long" not in ids(check(text, major_id=major))
            assert "object_count" not in ids(check(text, major_id=major))

    def test_major_override_reaches_source_checks(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = doc.load_plain_policy()
        data["majors"][SC] = {"sources": {
            "forbidden_kinds": data["sources"]["forbidden_kinds"] + ["custom_kind"]}}
        path = Path(tmp.name) / "override.json"
        path.write_text(json.dumps(data))
        self._swap_policy(path)
        for major in (SC, OTHER_MAJOR, None):
            root = self.make_project()
            if major is not None:
                bind_major(root, major)
            write_text(root, "answer.txt", "답변 원문\n")
            op = source_op()
            op["value"]["kind"] = "custom_kind"
            core.apply(root, {"request_id": "override", "ops": [op]},
                       core.load(root)["revision"])
            found = {row["check_id"] for row in core.checks(root, core.load(root))}
            assert ("source_kind_forbidden" in found) == (major == SC)

    def test_invalid_policy_fails_for_all_majors(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._swap_policy(Path(tmp.name) / "missing.json")
        for major in ALL_MAJORS:
            assert "plain_policy_file" in ids(doc.check("NPV", None, major_id=major))


class PlainPolicyObjectCredits(ContractCase):
    TABLE = "표 1. 재배 계획\n|항목|값|\n|---|---|\n|면적|1|\n"

    def test_table_credit_before_or_after_and_self_authored(self):
        for credit in ("출처: 본인 작성", "출처 : 농촌진흥청", "* 출처 : 본인작성",
                       "**출처: 본인 작성**", "출처：통계청"):
            for text in (credit + "\n" + self.TABLE, self.TABLE + credit):
                out = check(text)
                assert "object_credit" not in ids(out), text
                assert ("sourced_objects", "출처 있는 표 1개·그림 0개") in out

    def test_absent_table_source_is_error(self):
        for credit in ("", "출처:", "출처: 확인 불가", "출처: [확인 필요]",
                       "출처: N/A", "자료: 본인 작성", "이 표의 출처는 없다."):
            out = check(self.TABLE + credit)
            assert "object_credit" in ids(out), credit
            assert "object_credit" not in doc.WARNING_CHECKS
            assert "object_credit" not in doc.INFO_CHECKS

    def test_credit_not_reused_between_adjacent_objects(self):
        second = self.TABLE.replace("표 1.", "표 2.")
        out = check(self.TABLE + "출처: 본인 작성\n" + second)
        errors = [r for cid, r in out if cid == "object_credit"]
        assert len(errors) == 1 and "표 2." in errors[0]
        assert ("sourced_objects", "출처 있는 표 1개·그림 0개") in out
        out = check(self.TABLE + "출처: 본인 작성\n" + second + "출처: 통계청")
        assert "object_credit" not in ids(out)
        assert ("sourced_objects", "출처 있는 표 2개·그림 0개") in out

    def test_credit_does_not_cross_heading_or_table(self):
        for text in (self.TABLE + "# 새 절\n출처: 본인 작성",
                     self.TABLE + "\n".join(["설명"] * 5) + "\n출처: 본인 작성",
                     self.TABLE + "그림 1. 위치\n출처: 본인 촬영"):
            out = check(text)
            assert any(cid == "object_credit" and "표 1." in reason for cid, reason in out)

    def test_uncaptioned_objects_still_require_credit(self):
        table = self.TABLE.split("\n", 1)[1]
        out = check(table)
        assert "object_credit" in ids(out)
        assert "object_credit" not in ids(check(table + "출처: 본인 작성"))
        assert "object_credit" in ids(check("![밭](field.png)", root=self.make_project()))

    def test_placeholder_is_warning_without_source_error_or_count(self):
        out = check("그림 1의 정식 모습을 넣을 예정이다.\n그림 1. 정식 모습\n"
                    "[사진 필요: P1 | 밭 | 전경 | 정식 | 반쪽 | Ⅲ장]\n")
        assert "photo_placeholder" in ids(out)
        assert "object_credit" not in ids(out)
        assert "missing_object" not in ids(out)
        assert ("sourced_objects", "출처 있는 표 0개·그림 0개") in out
        out = check(self.TABLE + "[사진 필요: 밭 전경]")
        assert "object_credit" in ids(out)  # 사진 자리가 표 출처를 면제하지 않는다.

    def test_real_normal_objects_pass_existing_and_policy_checks(self):
        root = self.make_project()
        (root / "field.png").write_bytes(b"local image fixture")
        out = check("표 1과 그림 1을 참고한다.\n" + self.TABLE
                    + "출처: 본인 작성\n![밭](field.png)\n그림 1. 밭 전경\n출처: 본인 촬영",
                    root=root)
        assert out == [("sourced_objects", "출처 있는 표 1개·그림 1개")]

    def test_core_missing_credit_is_error_and_count_is_info(self):
        root = self.make_project()
        bind_major(root, SC)
        write_text(root, "sections/s1.md", self.TABLE)
        sec = section_op("s1", "Ⅰ. 머리말", "sections/s1.md")
        core.apply(root, {"request_id": "credit", "ops": [sec]}, core.load(root)["revision"])
        out = core.checks(root, core.load(root))
        row = next(r for r in out if r["check_id"] == "object_credit")
        assert (row["severity"], row["status"]) == ("error", "fail")

    def test_format_cli_default_policy_and_info_exit_code(self):
        commands = runtime("gg_commands")
        root = self.make_project()
        path = write_text(root, "plain.md", "NPV를 계산했다.")
        for args, expected_code, expected_id in (
            ([], 1, "forbidden_term"),
            (["--major", OTHER_MAJOR], 1, "forbidden_term"),
            (["--major", "unknown_major"], 1, "forbidden_term"),
            (["--major", SC], 1, "forbidden_term"),
        ):
            output = io.StringIO()
            with patch.object(sys, "argv", ["lint_plaintext.py", str(path)] + args), contextlib.redirect_stdout(output):
                code = commands.main("format")
            rows = json.loads(output.getvalue())
            assert code == expected_code
            assert expected_id in {r["check_id"] for r in rows}
        path.write_text("간단한 계획이다.")
        output = io.StringIO()
        with patch.object(sys, "argv", ["lint_plaintext.py", str(path), "--major", SC]), contextlib.redirect_stdout(output):
            assert commands.main("format") == 0
        row = next(r for r in json.loads(output.getvalue()) if r["check_id"] == "sourced_objects")
        assert (row["severity"], row["status"]) == ("info", "pass")


    def test_format_cli_explicit_major_for_unbound_project(self):
        commands = runtime("gg_commands")
        root = self.make_project()
        output = io.StringIO()
        with patch.object(sys, "argv", ["lint_plaintext.py", str(root), "--major", SC]), contextlib.redirect_stdout(output):
            commands.main("format")
        found = {r["check_id"] for r in json.loads(output.getvalue())}
        assert "sourced_objects" in found

    def test_format_cli_project_major_cannot_be_overridden(self):
        commands = runtime("gg_commands")
        root = self.make_project()
        bind_major(root, SC)
        path = write_text(root, "plain.md", "NPV를 계산했다.")
        for target in (path, write_text(root, "sections/s1.md", "NPV를 계산했다.")):
            output = io.StringIO()
            with patch.object(sys, "argv", ["lint_plaintext.py", str(target), "--major", OTHER_MAJOR]), contextlib.redirect_stdout(output):
                assert commands.main("format") == 1
            found = {r["check_id"] for r in json.loads(output.getvalue())}
            assert "forbidden_term" in found


class PlainPolicySeverity(ContractCase):
    def test_sourced_object_count_is_nonblocking_info(self):
        root = self.make_project()
        bind_major(root, SC)
        out = core.checks(root, core.load(root))
        rows = [r for r in out if r["check_id"] == "sourced_objects"]
        assert len(rows) == 1
        assert rows[0]["severity"] == "info"
        assert rows[0]["status"] == "pass"
        assert rows[0]["reason"] == "출처 있는 표 0개·그림 0개"
        assert not rows[0]["required_for"]
        assert "object_count" not in {r["check_id"] for r in out}

    def test_warning_check_ids_match_document(self):
        # 코어가 warning으로 내리는 ID 집합은 문서 모듈이 정의한다.
        assert doc.WARNING_CHECKS == _WARNING_IDS


if __name__ == "__main__":
    unittest.main()
