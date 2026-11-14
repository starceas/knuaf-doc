"""F1 C2/CA/CB: synthetic document defects and submission boundaries only."""
import json
import subprocess
import sys
import unicodedata
from unittest.mock import patch

from tests._harness import (
    ContractCase, SCRIPTS, bind_major, fact_op, runtime, section_op, source_op,
    write_text,
)

doc = runtime("gg_document")
core = runtime("gg_core")
F1_IDS = {"display_precision", "unit_conflict", "sample_identity",
          "sample_identity_unconfirmed", "sample_trace_review"}


def table(headers, values, before="", after=""):
    return (before + "\n표 1. 가상 계획\n|" + "|".join(headers) + "|\n|"
            + "|".join("---" for _ in headers) + "|\n|"
            + "|".join(values) + "|\n" + after)


def findings(text, base=None, **kwargs):
    return [(cid, reason) for cid, reason in doc.check(text, base, **kwargs)
            if cid in F1_IDS]


class TableUnits(ContractCase):
    def test_precision_only_money_cells_and_four_place_boundary(self):
        for unit in ("원", "천원", "만원", "백만원"):
            with self.subTest(unit=unit):
                result = findings(table(["수량(kg)", "금액(" + unit + ")"],
                                        ["12.3456", "157750.408636364"]))
                self.assertEqual([cid for cid, _ in result], ["display_precision"])
                self.assertIn("2열", result[0][1])
                self.assertFalse(findings(table(["금액(" + unit + ")"], ["157,750"])))
        for value, bad in (("12.345", False), ("12.3456", True),
                           ("-1,234.0000", True)):
            with self.subTest(value=value):
                self.assertEqual(bool(findings(table(["금액(원)"], [value]))), bad)
        self.assertFalse(findings("좌표 35.123456, 127.123456 및 계수 1.234567"))

    def test_common_unit_and_cell_column_precedence(self):
        self.assertEqual([cid for cid, _ in findings(table(
            ["값"], ["12.34567"], "(단위: 천원)"))], ["display_precision"])
        for header, value in (("수량(kg)", "12.34567"),
                              ("단가(원/kg)", "12.34567"),
                              ("비율(%)", "12.34567"),
                              ("값", "12.34567 kg")):
            with self.subTest(header=header, value=value):
                self.assertFalse(findings(table([header], [value], "(단위: 천원)")))
        self.assertEqual([cid for cid, _ in findings(table(
            ["수량(kg)"], ["12.34567원"], "(단위: 천원)"))], ["display_precision"])

    def test_markdown_emphasis_does_not_hide_cell_or_header_units(self):
        for header, value in (("금액(원)", "**12.34567**"),
                              ("금액", "**12.34567원**"),
                              ("금액", "*12.34567원*"),
                              ("금액", "__12.34567원__"),
                              ("**금액(원)**", "12.34567"),
                              ("**원**", "12.34567")):
            with self.subTest(header=header, value=value):
                self.assertEqual([cid for cid, _ in findings(table([header], [value]))],
                                 ["display_precision"])
        self.assertEqual([cid for cid, _ in findings(table(
            ["금액"], ["**12원**"], "(단위: 천원)"))], ["unit_conflict"])
        self.assertFalse(findings(table(["금액(원)"], ["**12.5**"])))
        self.assertFalse(findings(table(["금액(원)"], ["**ID-12.34567**"])))

    def test_identifiers_quotes_and_unknown_units_are_not_money_values(self):
        for value in ("ID-12.34567", "원문 12.34567", "12.34567%", "12.34567개"):
            with self.subTest(value=value):
                self.assertFalse(findings(table(["금액(원)"], [value])))
        self.assertFalse(findings(table(["해석불가"], ["12.34567"])))
        self.assertFalse(findings(table(["금액(원/두)"], ["12.34567"])))

    def test_column_and_common_conflicts_have_same_rule(self):
        for before, header in (("", "금액(천원)"), ("(단위: 천원)", "금액")):
            for note, bad in (("", True), ("주: 1천원=1,000원", False),
                              ("주: 1t=1,000kg", True)):
                with self.subTest(before=before, header=header, note=note):
                    result = findings(table([header], ["200,000원"], before, note))
                    self.assertEqual([cid for cid, _ in result],
                                     ["unit_conflict"] if bad else [])
        self.assertFalse(findings(table(["수량(kg)", "단가(원/kg)", "금액(천원)"],
                                        ["10", "2,000", "20"])))
        self.assertFalse(findings(table(["값(kg)"], ["20원"])))
        self.assertEqual([cid for cid, _ in findings(table(
            ["금액(천원)"], ["200,000 (원)"]))], ["unit_conflict"])
        self.assertEqual([cid for cid, _ in findings(table(
            ["값"], ["12.34567 (원)"]))], ["display_precision"])

    def test_conflict_dimensions_and_common_declarations(self):
        for inherited, explicit in (("kg", "g"), ("톤", "kg"),
                                    ("두", "마리"), ("㎡", "평"), ("ha", "㎡")):
            with self.subTest(inherited=inherited):
                self.assertEqual([cid for cid, _ in findings(table(
                    ["값(" + inherited + ")"], ["20" + explicit]))], ["unit_conflict"])
        self.assertEqual([cid for cid, _ in findings(table(
            ["값"], ["20"], "(단위: 천원)\n(단위: 원)"))], ["unit_conflict"])
        self.assertFalse(findings(table(["값"], ["20"], "(단위: ㎡)\n(단위: 천원)")))
        self.assertFalse(findings(table(["값"], ["20"], "(단위: 천원)\n(단위: 천원)")))

    def test_notes_and_declarations_do_not_leak_between_tables_or_sections(self):
        conflict = table(["금액(천원)"], ["200원"])
        separate_note = table(["무게(kg)"], ["1"], after="주: 1천원=1,000원")
        self.assertEqual([cid for cid, _ in findings(conflict + separate_note)],
                         ["unit_conflict"])
        self.assertEqual([cid for cid, _ in findings(
            conflict + "# 새 절\n주: 1천원=1,000원")], ["unit_conflict"])
        self.assertEqual([cid for cid, _ in findings(
            conflict + "주요 비용은 원 / 천원 기준이다.")], ["unit_conflict"])
        first = table(["값"], ["1"], "(단위: 원)")
        second = table(["값"], ["12.34567"]).replace("표 1.", "표 2.")
        self.assertFalse(findings(first + second))
        self.assertFalse(findings("(단위: 원)\n# 새 절\n" + second))

    def test_separate_table_unit_declarations_have_same_verdict_when_combined(self):
        first = "(단위: 천원)\n표 1. 가상 비용\n|항목|금액|\n|---|---|\n|A|200|\n"
        second = "(단위: 원)\n표 2. 가상 단가\n|항목|금액|\n|---|---|\n|B|300|\n"
        for text in (first, second, first + "\n" + second):
            with self.subTest(text=text):
                self.assertEqual(findings(text), [])

    def test_previous_table_note_preserves_separate_and_combined_unit_verdicts(self):
        first = "(단위: 천원)\n표 1. 가상 비용\n|항목|금액|\n|---|---|\n|A|200|\n"
        note = "주: 비용 기준(단위: 천원)\n"
        second = "(단위: 원)\n표 2. 가상 단가\n|항목|금액|\n|---|---|\n|B|300|\n"
        for text in (first + note, second, first + note + "\n" + second,
                     first + "\n" + second):
            with self.subTest(text=text):
                self.assertEqual(findings(text), [])

    def test_note_boundaries_do_not_supply_the_next_tables_display_unit(self):
        first = "(단위: 원)\n표 1. 가상 비용\n|항목|금액|\n|---|---|\n|A|200|\n"
        second = "표 2. 가상 측정\n|항목|값|\n|---|---|\n|B|12.34567|\n"
        for label in ("주:", "주1:", "비고:", "환산:", "※"):
            with self.subTest(label=label):
                note = label + " 비용 기준(단위: 원)\n"
                self.assertEqual(findings(first + note + "\n" + second), [])

    def test_real_trailing_unit_conflict_stays_with_its_table(self):
        first = ("(단위: 천원)\n표 1. 가상 비용\n|항목|금액|\n|---|---|\n|A|200|\n"
                 "주: 비용 기준(단위: 원)\n")
        second = "(단위: 원)\n표 2. 가상 단가\n|항목|금액|\n|---|---|\n|B|300|\n"
        for text, expected in ((first, ["unit_conflict"]), (second, []),
                               (first + "\n" + second, ["unit_conflict"])):
            with self.subTest(text=text):
                self.assertEqual([cid for cid, _ in findings(text)], expected)

    def test_conversion_note_with_unit_declaration_remains_local(self):
        first = ("표 1. 가상 비용\n|항목|금액(천원)|\n|---|---|\n|A|200,000원|\n")
        second = "(단위: 원)\n표 2. 가상 단가\n|항목|금액|\n|---|---|\n|B|300|\n"
        for note, expected in (
            ("주: 비용 기준(단위: 천원), 1천원=1,000원\n", []),
            ("주: 비용 기준(단위: 천원), 1t=1,000kg\n", ["unit_conflict"]),
        ):
            for text in (first + note, first + note + "\n" + second):
                with self.subTest(note=note, text=text):
                    self.assertEqual([cid for cid, _ in findings(text)], expected)

    def test_unit_declaration_between_uncaptioned_tables_is_not_a_certain_conflict(self):
        first = "|금액|\n|---|\n|200|\n"
        second = "|금액|\n|---|\n|300|\n"
        self.assertEqual(findings(
            "(단위: 천원)\n" + first + "\n(단위: 원)\n" + second), [])

    def test_trailing_table_unit_declarations_still_detect_real_conflicts(self):
        self.assertEqual([cid for cid, _ in findings(table(
            ["금액"], ["200원"], after="(단위: 천원)"))], ["unit_conflict"])
        self.assertEqual([cid for cid, _ in findings(table(
            ["금액"], ["200"], after="(단위: 천원)\n(단위: 원)"))], ["unit_conflict"])


class IdentityFixture(ContractCase):
    def identity_project(self, student="학생B", *, owner="sample", authors=None,
                         extra_facts=()):
        root = self.make_project()
        write_text(root, "answer.txt", "가상 작성자·학생 응답\n")
        source = source_op()
        source["value"].update(file_owner=owner,
                               author_names=["견본A"] if authors is None else authors)
        ops = [source]
        if student is not None:
            ops.append(fact_op("student", "common.student_name", student, "",
                               scope="project"))
        ops.extend(extra_facts)
        core.apply(root, {"request_id": "identity-fixture", "ops": ops},
                   core.load(root)["revision"])
        return root

    def save_body(self, root, text):
        path = write_text(root, "sections/01-cover.md", text)
        core.apply(root, {"request_id": "body", "ops": [
            section_op("cover", "표지", "sections/01-cover.md")]},
            core.load(root)["revision"])
        return path


class SampleIdentity(IdentityFixture):
    def test_cover_mismatch_homonym_and_unknown_student(self):
        text = "# 표지\n성명: 견본A"
        for student, expected in (("학생B", "sample_identity"),
                                  ("견본A", None),
                                  (None, "sample_identity_unconfirmed")):
            with self.subTest(student=student):
                root = self.identity_project(student)
                self.assertEqual([cid for cid, _ in findings(
                    text, root, project_root=root)], [expected] if expected else [])

    def test_only_front_student_roles_are_errors(self):
        root = self.identity_project()
        for heading, label, expected in (
            ("표지", "성 명", "sample_identity"),
            ("제출지", "학생", "sample_identity"),
            ("제출지", "작성자", "sample_identity"),
            ("인준지", "심사위원 성명", "sample_trace_review"),
            ("표지", "지도교수 성명", "sample_trace_review"),
            ("표지", "학과장", "sample_trace_review"),
            ("Ⅰ. 본문", "작성자", "sample_trace_review"),
            ("알 수 없는 역할", "성명", "sample_trace_review"),
        ):
            with self.subTest(heading=heading, label=label):
                self.assertEqual([cid for cid, _ in findings(
                    "# " + heading + "\n" + label + ": 견본A",
                    root, project_root=root)], [expected])

    def test_unicode_and_whitespace_name_normalization(self):
        name = "가상다"
        root = self.identity_project("학생B", authors=[name])
        for value in ("가 상 다", unicodedata.normalize("NFD", name)):
            with self.subTest(value=value):
                self.assertEqual([cid for cid, _ in findings(
                    "# 표지\n성명: " + value, root, project_root=root)],
                    ["sample_identity"])
        self.assertEqual([cid for cid, _ in findings(
            "# 표지\n성명: 가\n상 다", root, project_root=root)], ["sample_identity"])
        root = self.identity_project("가 상 다", authors=[name])
        self.assertFalse(findings("# 표지\n성명: " + name, root, project_root=root))

    def test_soft_wrapped_student_name_is_not_a_shorter_sample_identity(self):
        for text in ("# 표지\n성명: 가상나", "# 표지\n성명: 가상\n나"):
            with self.subTest(text=text):
                root = self.identity_project("가상나", authors=["가상"])
                path = self.save_body(root, text)
                self.assertEqual([cid for cid, _ in findings(
                    text, root, project_root=root)], ["sample_trace_review"])
                rows = [row for row in core.checks(root, core.load(root))
                        if row["check_id"] in F1_IDS]
                self.assertEqual([(row["check_id"], row["severity"]) for row in rows],
                                 [("sample_trace_review", "warning")])
                self.assertTrue(core.completion(root, core.load(root), rows)["skill_ready"])
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "lint_format.py"), str(path), "--json"],
                    text=True, encoding="utf-8", capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                cli = [row for row in json.loads(result.stdout) if row["check_id"] in F1_IDS]
                self.assertEqual([(row["check_id"], row["severity"]) for row in cli],
                                 [("sample_trace_review", "warning")])

    def test_name_continuation_stops_at_other_fields_blank_lines_and_hard_breaks(self):
        root = self.identity_project("가상나", authors=["가상"])
        for text in ("# 표지\n성명: 가상\n학번: 123",
                     "# 표지\n성명: 가상\n\n나",
                     "# 표지\n성명: 가상  \n나"):
            with self.subTest(text=text):
                self.assertEqual([cid for cid, _ in findings(
                    text, root, project_root=root)], ["sample_identity"])

    def test_only_active_unambiguous_student_facts_confirm_identity(self):
        for verification, expected in (("superseded", "sample_identity"),
                                        ("source_located", "sample_identity_unconfirmed")):
            old = fact_op("old-student", "common.student_name", "학생C", "",
                          verification=verification, scope="project")
            root = self.identity_project(extra_facts=[old])
            self.assertEqual([cid for cid, _ in findings(
                "성명: 견본A", root, project_root=root)], [expected])
        missing = fact_op("missing", "common.student_name", None, "",
                          answer_state="not_provided", scope="project")
        root = self.identity_project(None, extra_facts=[missing])
        self.assertEqual([cid for cid, _ in findings(
            "성명: 견본A", root, project_root=root)], ["sample_identity_unconfirmed"])

    def test_only_other_and_sample_well_formed_author_lists_are_used(self):
        for owner, authors, expected in (
            ("self", ["견본A"], []), ("unknown", ["견본A"], []),
            ("other", ["견본A"], ["sample_identity"]),
            ("sample", "견본A", []), ("sample", [123], []),
            ("sample", [], []), ("sample", [""], []),
        ):
            with self.subTest(owner=owner, authors=authors):
                root = self.identity_project(owner=owner, authors=authors)
                self.assertEqual([cid for cid, _ in findings(
                    "성명: 견본A", root, project_root=root)], expected)
        root = self.identity_project(authors=["12345"])
        self.assertFalse(findings("성명: 12345", root, project_root=root))

    def test_body_table_and_unknown_roles_warn_with_locations_but_references_do_not(self):
        root = self.identity_project()
        text = ("# Ⅰ. 본문\n견본A의 가상 연구를 검토한다.\n"
                "|항목|값|\n|---|---|\n|관계자|견본A|\n"
                "출처: 견본A\n# 참고문헌\n견본A. 2026. 가상 연구.\n"
                "# 부록\n견본A의 가상 기록.")
        result = findings(text, root, project_root=root)
        self.assertEqual([cid for cid, _ in result], ["sample_trace_review"])
        self.assertIn("2행", result[0][1])
        self.assertIn("2열", result[0][1])
        self.assertIn("10행", result[0][1])
        self.assertNotIn("8행", result[0][1])
        self.assertFalse(findings("# 참고문헌\n견본A. 2026.", root, project_root=root))
        self.assertFalse(findings("출처: 견본A", root, project_root=root))
        self.assertFalse(findings("표 1. 가상 계획 (출처: 견본A)", root, project_root=root))
        self.assertFalse(findings("가상 수치 12345", root, project_root=root))

    def test_project_context_is_optional_and_not_inferred_from_image_base(self):
        root = self.identity_project()
        self.assertFalse(findings("성명: 견본A", root))
        self.assertFalse(findings("성명: 견본A", None))
        self.assertFalse(findings("성명: 견본A", None, project_root=root / "outside"))
        self.assertEqual([cid for cid, _ in findings(
            "성명: 견본A", root / "sections", project_root=root)], ["sample_identity"])

    def test_cli_folder_and_single_section_use_same_project_record(self):
        root = self.identity_project()
        path = self.save_body(root, "# 표지\n성명: 견본A")
        for target in (root, path):
            with self.subTest(target=target.name):
                completed = subprocess.run(
                    [sys.executable, str(SCRIPTS / "lint_format.py"), str(target), "--json"],
                    text=True, encoding="utf-8", capture_output=True, check=False)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                rows = [row for row in json.loads(completed.stdout)
                        if row["check_id"] in F1_IDS]
                self.assertEqual([(row["check_id"], row["severity"], row["owner"])
                                  for row in rows], [("sample_identity", "error", "skill")])


class SubmissionBoundary(IdentityFixture):
    def test_each_error_blocks_actual_candidate_but_allows_review_publication(self):
        for expected, text in (
            ("display_precision", table(["금액(원)"], ["12.34567"])),
            ("unit_conflict", table(["금액(천원)"], ["200원"])),
            ("sample_identity", "# 표지\n성명: 견본A"),
        ):
            with self.subTest(expected=expected):
                root = self.identity_project()
                bind_major(root)
                self.save_body(root, text)
                # Real checks consume the canonical body/source/fact records.
                # Isolate unrelated school/output prerequisites at gate,
                # not document findings, severity, completion or export.
                rows = [row for row in core.checks(root, core.load(root))
                        if row["check_id"] in F1_IDS]
                self.assertEqual([(row["check_id"], row["severity"], row["owner"])
                                  for row in rows], [(expected, "error", "skill")])
                self.assertFalse(core.completion(root, core.load(root), rows)["skill_ready"])
                with patch.object(core, "gate", return_value=rows):
                    with self.assertRaises(core.OperationError) as raised:
                        core.export(root, "submission_candidate", major_id="specialty_crops")
                    self.assertEqual(raised.exception.result["reason"],
                                     "submission_candidate_blocked")
                    review = core.export(root, "review", major_id="specialty_crops")
                self.assertIn(text.strip(), core.local(root, review["path"]).read_text(encoding="utf-8"))

    def test_identity_warnings_allow_actual_candidate_publication(self):
        for student, expected, text in (
            (None, "sample_identity_unconfirmed", "# 표지\n성명: 견본A"),
            ("학생B", "sample_trace_review", "# Ⅰ. 본문\n견본A의 가상 연구"),
        ):
            with self.subTest(expected=expected):
                root = self.identity_project(student)
                bind_major(root)
                self.save_body(root, text)
                rows = [row for row in core.checks(root, core.load(root))
                        if row["check_id"] in F1_IDS]
                self.assertEqual([(row["check_id"], row["severity"], row["owner"])
                                  for row in rows], [(expected, "warning", "skill")])
                self.assertTrue(core.completion(root, core.load(root), rows)["skill_ready"])
                with patch.object(core, "gate", return_value=rows):
                    candidate = core.export(root, "submission_candidate", major_id="specialty_crops")
                self.assertIn(text.strip(), core.local(root, candidate["path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest
    unittest.main()
