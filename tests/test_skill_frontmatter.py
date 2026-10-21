"""gg_frontmatter: the cover, title and approval pages a student sees first.

Migrated from the GUI contract suite (cb72ae1).  The module's own docstring
says it deliberately does not manufacture identities or dates; these tests
hold it to that.  A cover carrying an invented name, a guessed submission
date, or a graduation date copied from the submission date is worse than one
that says 확인 필요.
"""
import re
import unittest

from tests._harness import ContractCase, runtime


class FrontmatterTests(ContractCase):
    def setUp(self):
        self.fm = runtime("gg_frontmatter")

    def profile(self, **school):
        return self.fm.normalize_school_profile(
            {"school_profile": {"mode": "school", **school}})

    # --- the opt-in boundary ---------------------------------------------

    def test_a_spec_without_a_school_profile_keeps_the_legacy_path(self):
        self.assertEqual(
            {"enabled": False},
            self.fm.normalize_school_profile(
                {"title": "t", "student": "홍길동"}))

    def test_a_school_profile_turns_the_new_form_on(self):
        self.assertIs(True, self.profile(title="테스트")["enabled"])

    def test_a_non_object_spec_or_profile_is_refused(self):
        for bad in ("문자열", 5, [], True):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    self.fm.normalize_school_profile(bad)
                with self.assertRaises(TypeError):
                    self.fm.normalize_school_profile({"school_profile": bad})

    # --- unknown keys are refused, not ignored ---------------------------

    def test_an_unrecognised_profile_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "지원하지 않는 school_profile 키"):
            self.fm.normalize_school_profile(
                {"school_profile": {"mode": "school", "저자": "홍길동"}})

    def test_an_unrecognised_committee_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "committee 키"):
            self.profile(committee={"위원장": "김교수"})

    def test_committee_members_must_be_a_list(self):
        for members in ("홍길동", 5, {"a": 1}):
            with self.subTest(members=members):
                with self.assertRaisesRegex(TypeError, "members는 목록"):
                    self.profile(committee={"members": members})

    def test_a_non_object_committee_is_refused(self):
        with self.assertRaisesRegex(TypeError, "committee는 객체"):
            self.profile(committee="김교수")

    # --- the two dates never borrow from each other ----------------------

    def test_a_submission_date_does_not_become_a_graduation_date(self):
        p = self.profile(submission_date="2026년 12월")
        self.assertEqual("2026년 12월", p.get("submission_date"))
        self.assertIsNone(p.get("graduation_date"))

    def test_a_graduation_date_does_not_become_a_submission_date(self):
        p = self.profile(graduation_date="2027년 2월")
        self.assertEqual("2027년 2월", p.get("graduation_date"))
        self.assertIsNone(p.get("submission_date"))

    def test_both_dates_are_kept_when_both_are_given(self):
        p = self.profile(submission_date="2026년 12월",
                         graduation_date="2027년 2월")
        self.assertEqual("2026년 12월", p["submission_date"])
        self.assertEqual("2027년 2월", p["graduation_date"])

    # --- unknown identities are marked, never blanked --------------------

    def test_the_only_policy_is_to_mark_unknown_identities(self):
        self.assertEqual("mark", self.profile()["unknown_policy"])
        for refused in ("blank", "hide", "omit"):
            with self.subTest(refused=refused):
                with self.assertRaisesRegex(ValueError, "unknown_policy"):
                    self.profile(unknown_policy=refused)

    def test_display_marks_a_missing_value(self):
        self.assertEqual(self.fm.PENDING,
                         self.fm.display(None, self.profile()))
        self.assertEqual("[확인 필요]", self.fm.PENDING)
        self.assertEqual(self.fm.PENDING,
                         self.fm.display("", self.profile()))

    def test_display_keeps_a_supplied_value(self):
        self.assertEqual("홍길동", self.fm.display("홍길동", self.profile()))

    def test_a_signature_line_is_blank_on_purpose(self):
        self.assertEqual("", self.fm.display(None, self.profile(), blank=True))

    # --- whitespace is not a value ---------------------------------------

    def test_a_blank_field_is_treated_as_missing_not_as_text(self):
        for empty in ("", "   ", "\t", None):
            with self.subTest(empty=empty):
                p = self.profile(student=empty)
                self.assertEqual(self.fm.PENDING,
                                 self.fm.display(p.get("student"), p))

    def test_values_are_trimmed(self):
        self.assertEqual("홍길동", self.profile(author="  홍길동  ")["author"])

    def test_the_legacy_student_key_still_fills_the_author_slot(self):
        self.assertEqual("홍길동", self.profile(student="홍길동")["author"])
        self.assertEqual(
            "이몽룡",
            self.profile(author="이몽룡", student="홍길동")["author"])

    def test_the_committee_keeps_three_signature_slots(self):
        p = self.profile()
        self.assertIsNone(p["committee"]["chair"])
        self.assertEqual(3, len(p["committee"]["members"]))

    def test_supplied_committee_members_are_kept_in_order(self):
        p = self.profile(committee={
            "chair": "김교수",
            "members": ["이교수", "박교수", "최교수"]})
        self.assertEqual("김교수", p["committee"]["chair"])
        self.assertEqual(["이교수", "박교수", "최교수"],
                         p["committee"]["members"])

    def test_the_fixed_school_strings_are_not_overridable_by_omission(self):
        p = self.profile()
        self.assertEqual("한국농수산대학교", p["school"])
        self.assertEqual("농업전문학사 학위논문", p["degree"])
        self.assertEqual("영농창업계획", p["plan_label"])

    # --- the rendered forms ----------------------------------------------

    def test_the_three_official_forms_appear_before_the_contents(self):
        lines = self.fm.frontmatter_lines(self.profile(title="테스트 논문"))
        for marker in ("겉표지", "표제면", "인준서"):
            self.assertIn(marker, lines)
        self.assertLess(lines.index("겉표지"), lines.index("표제면"))
        self.assertLess(lines.index("표제면"), lines.index("인준서"))

    def test_the_school_name_is_fixed_and_the_rest_is_marked(self):
        lines = self.fm.frontmatter_lines(self.profile(title="테스트 논문"))
        self.assertIn("한국농수산대학교", lines)
        self.assertIn("테스트 논문", lines)
        self.assertGreaterEqual(lines.count(self.fm.PENDING), 3)

    def test_nothing_in_the_forms_is_invented(self):
        supplied = {"title": "테스트 논문", "student": "홍길동",
                    "advisor": "김교수"}
        lines = self.fm.frontmatter_lines(self.profile(**supplied))
        for line in lines:
            self.assertFalse(
                re.search(r"20\d{2}\s*년\s*\d{1,2}\s*월", line), line)

    def test_a_supplied_date_does_appear(self):
        lines = self.fm.frontmatter_lines(
            self.profile(title="t", submission_date="2026년 12월"))
        self.assertTrue(any("2026년 12월" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
