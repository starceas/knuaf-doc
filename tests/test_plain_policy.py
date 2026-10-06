"""R-all-1 평문 논문 정책 — 모든 전공 공통 기본값과 majors 재정의.

기존 P1~P6 반례와 표·그림 출처·사진 정책을 모든 입력에 적용한다.
"""
import json
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
            ("front_matter", "title_keyword", None),
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
