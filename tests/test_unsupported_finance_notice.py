"""미지원 전공 재무 안내 — `DESIGN-W-N.md` §6 14항.

이 파일이 소유하는 것은 §6 표의 14항과, 안내 행 때문에 새로 생기는 단언뿐이다.
기존 알림 시험(`tests/test_hort_workbook_advice.py`)은 워크북 경로를 보고,
등록부·호출기·CLI 잠금은 `tests/test_sidecar_registry.py`가 본다.

**식량작물**: W-N 당시 식량작물은 재무 능력이 전부 `unsupported`라 이 안내
1행을 받았다. 엑셀 2판(PR #125)이 재무를 `supported`(학교 엑셀 빈 사본까지만,
``FILL_CLOSED_MAJORS``)로 바꾼 뒤로는 지원 쪽이다 — 미지원 안내 0행, 대신
빈 사본 안내(`finance_blank_copy_notice`) 1행. 그 행은 아래 `BlankCopy*` 시험이
같은 꼴·같은 배타 규칙으로 잠근다.

14항 중 ⑭(기존 3모듈 97/97·`test_p2_core` 81/81·스냅샷 39/39)은 다른 시험
묶음을 돌리는 일이라 여기 넣지 않는다. 그 항은 그 묶음을 직접 실행해 확인한다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS, _majors_in

NOTICES = ".knuaf-notices.json"
CHECK_ID = "finance_unsupported_notice"
# 설계 §6 표 12행은 18키라 적었지만, 실측 알림 dict는 18개이고 그 목록에
# ``status``가 들어 있다(설계 문면은 ``status``를 빠뜨리고 17개만 적었다).
# 여기서는 파일에 실제로 실린 키를 잠근다.
DOCUMENT_KEYS = {
    "check_id", "checker_version", "dismissible", "evidence",
    "input_revision", "items", "notice_identity", "owner", "project_id",
    "reason", "receipt_path", "record_refs", "redisplay_rule",
    "required_for", "severity", "status", "target", "title",
}
UNSUPPORTED = ("industrial_insects", "dairy")
SUPPORTED = ("specialty_crops", "fruit_trees", "hort_env_systems", "food_crops")
BLANK_COPY_ID = "finance_blank_copy_notice"
BLANK_COPY = ("food_crops",)
BLANK_COPY_TITLE = "지금은 빈 사본만 만들어 드려요. 숫자 확인은 교수님과 함께 하세요."
# 앱(notice.ts readNotice)은 이 셋 중 하나라도 비면 알림을 버린다.
APP_REQUIRED = ("title", "notice_identity", "project_id")


class UnsupportedFinanceCase(ContractCase):
    """전공 하나를 바인딩한 프로젝트를 세우는 공통 준비."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")

    def project(self, major_id=None):
        root = self.make_project()
        if major_id is not None:
            bind_major(root, major_id)
        return root

    def rows(self, root, check_id=CHECK_ID):
        project = self.core.load(root)
        return [row for row in self.core.checks(root, project)
                if row.get("check_id") == check_id]

    def document(self, root):
        return json.loads((Path(root) / NOTICES).read_text(encoding="utf-8"))


class RowPresenceTests(UnsupportedFinanceCase):
    """①②③ 판별 — 누가 안내를 받고, 누가 안 받으며, 막지는 않는가."""

    def test_unsupported_majors_get_exactly_one_row(self):
        """① 미지원 2전공 1행·지원 4전공 0행(식량작물은 #125부터 지원 쪽)."""
        for major_id in UNSUPPORTED:
            with self.subTest(major=major_id):
                self.assertEqual(1, len(self.rows(self.project(major_id))))
        for major_id in SUPPORTED:
            with self.subTest(major=major_id):
                self.assertEqual(0, len(self.rows(self.project(major_id))))

    def test_unbound_project_gets_no_row(self):
        """② 전공 미확인은 `major_guard` 몫 — 여기서 겹쳐 만들지 않는다."""
        self.assertEqual(0, len(self.rows(self.project(None))))

    def test_row_is_informational_and_blocks_nothing(self):
        """③ 안내 행은 게이트에 실리되 아무것도 막지 않는다."""
        root = self.project("industrial_insects")
        rows = self.rows(root)
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertIs(False, self.core.blocks_skill_candidate(row))
        gate_ids = [item.get("check_id") for item in
                    self.core.gate(root, self.core.load(root))]
        self.assertIn(CHECK_ID, gate_ids)
        blocking = [item for item in self.core.gate(root, self.core.load(root))
                    if self.core.blocks_skill_candidate(item)]
        self.assertNotIn(CHECK_ID,
                         [item.get("check_id") for item in blocking])
        # 종료코드는 패치 전과 같다 — `status`는 0, `check`의 1은 기존
        # `school_profile` 몫이고 우리 행이 원인이 아니다.
        self.assertEqual(0, self._cli("status", root))
        self.assertEqual(1, self._cli("check", root))

    def _cli(self, command, root):
        import subprocess
        script = (Path(__file__).resolve().parent.parent
                  / "skills" / "knuaf-doc" / "scripts" / "gg.py")
        finished = subprocess.run(
            [sys.executable, "-B", str(script), command, str(root)],
            capture_output=True, text=True, encoding="utf-8")
        return finished.returncode


class RowShapeTests(UnsupportedFinanceCase):
    """④⑫ 행·문서 꼴."""

    def test_row_fields_are_the_designed_ones(self):
        """④ 학생에게는 안내, 앱에게는 표준 필드."""
        row = self.rows(self.project("industrial_insects"))[0]
        self.assertEqual("info", row["severity"])
        self.assertEqual([], row["required_for"])
        self.assertEqual("not_checked", row["status"])
        self.assertEqual("finance", row["target"])
        self.assertEqual("skill", row["owner"])
        self.assertEqual([], row["items"])
        self.assertIsNone(row["receipt_path"])
        self.assertEqual([], row["record_refs"])
        self.assertIs(True, row["dismissible"])
        self.assertEqual("identity_changed", row["redisplay_rule"])

    def test_notice_document_matches_the_notices_schema(self):
        """⑫ 최상위 4키·알림 18키·64-hex 식별값."""
        root = self.project("industrial_insects")
        self.assertEqual("written", self.notices.write_notices(root)["status"])
        document = self.document(root)
        self.assertEqual({"schema", "written_at", "project_id", "notices"},
                         set(document))
        self.assertEqual("knuaf.notices/1", document["schema"])
        self.assertEqual(1, len(document["notices"]))
        notice = document["notices"][0]
        self.assertEqual(DOCUMENT_KEYS, set(notice))
        self.assertEqual(CHECK_ID, notice["check_id"])
        self.assertIsNone(notice["receipt_path"])
        self.assertEqual([], notice["record_refs"])
        self.assertRegex(notice["notice_identity"], r"^[0-9a-f]{64}$")
        self.assertNotIn("\n", notice["title"])


class StudentWordingTests(UnsupportedFinanceCase):
    """⑤⑥ 저장·선택·문구."""

    def test_write_keeps_one_row_and_no_temp_files(self):
        """⑤ 파일 1행·`items` 빈 배열·임시파일 잔여 0."""
        root = self.project("dairy")
        result = self.notices.write_notices(root)
        self.assertEqual("written", result["status"])
        self.assertEqual(1, result["notices"])
        self.assertEqual([], self.document(root)["notices"][0]["items"])
        self.assertEqual([], sorted(Path(root).glob(".knuaf-notices-*.tmp")))

    def test_choice_is_not_offered_and_title_is_student_safe(self):
        """⑥ `choices` 없음 → `choice_not_offered`; 문구 lint 통과."""
        root = self.project("industrial_insects")
        self.notices.write_notices(root)
        notice = self.document(root)["notices"][0]
        self.assertNotIn("choices", notice)
        self.assertEqual({"status": "rejected", "reason": "choice_not_offered"},
                         self.notices.choose(root, notice["notice_identity"],
                                             "footnote"))

        title = notice["title"]
        self.assertNotIn("\n", title)
        self.assertNotIn("\t", title)
        self.assertLessEqual(len(title), 50, title)
        lowered = title.lower()
        self.assertEqual([], [term for term in FORBIDDEN_STUDENT_TERMS
                              if term.lower() in lowered])
        self.assertEqual([], _majors_in(title))
        self.assertNotIn("대차", title)
        self.assertNotIn("문제 없", title)

    def test_repeat_is_stable_and_major_change_moves_identity(self):
        """⑦ 같은 전공 반복 → 같은 행, 전공 변경 → 다른 식별값."""
        root = self.project("industrial_insects")
        first = json.dumps(self.rows(root)[0], sort_keys=True,
                           ensure_ascii=False)
        second = json.dumps(self.rows(root)[0], sort_keys=True,
                            ensure_ascii=False)
        self.assertEqual(first, second)
        before = self.rows(root)[0]["notice_identity"]
        bind_major(root, "dairy")
        after = self.rows(root)[0]["notice_identity"]
        self.assertNotEqual(before, after)


class IdentityTests(UnsupportedFinanceCase):
    """⑧⑨ 두 생산 경로가 같은 행을 낸다."""

    def test_identity_import_failure_matches_normal_path(self):
        """⑧ 대체 계산 == 정상 계산. 옛 dict 요리법은 다른 값(음성 통제)."""
        for major_id in ("industrial_insects", "dairy"):
            with self.subTest(major=major_id):
                root = self.project(major_id)
                normal = self.rows(root)[0]["notice_identity"]
                with mock.patch.dict(sys.modules, {"gg_hort_workbook": None}):
                    row = self.core._unsupported_finance_notice_row_for_project(
                        root, self.core.load(root))
                self.assertEqual(normal, row["notice_identity"])
                # 재료는 전공 ID다 — 구분자를 빠뜨린 옛 계산은 다른 값이어야
                # 음성 통제가 성립한다.
                payload = {"project_id": self.core._advice_project_id(root),
                           "kind": "unsupported_finance", "profile": None,
                           "form_identity": major_id, "patched": []}
                self.assertNotEqual(normal, self.core.digest(payload))

    def test_thin_path_equals_checks_row(self):
        """⑨ 알림 파일 경로 == `checks()` 행(바이트 동일)."""
        for major_id in UNSUPPORTED:
            with self.subTest(major=major_id):
                root = self.project(major_id)
                project = self.core.load(root)
                thin = self.core._unsupported_finance_notice_row_for_project(
                    root, project)
                full = self.rows(root)[0]
                self.assertEqual(
                    json.dumps(full, sort_keys=True, ensure_ascii=False),
                    json.dumps(thin, sort_keys=True, ensure_ascii=False))


class ExclusivityTests(UnsupportedFinanceCase):
    """⑩⑪ 두 알림은 배타이고, 전공을 바꾸면 파일이 비워진다."""

    def test_unsupported_and_workbook_notices_never_coexist(self):
        """⑩ 미지원 전공은 워크북 0행, 지원 전공은 미지원 0행."""
        for major_id in UNSUPPORTED:
            with self.subTest(major=major_id):
                root = self.project(major_id)
                project = self.core.load(root)
                self.assertIsNone(
                    self.core._workbook_notice_row_for_project(root, project))
                self.assertEqual([CHECK_ID], [
                    row["check_id"] for row in self.notices.notice_rows(root)])
        for major_id in SUPPORTED:
            with self.subTest(major=major_id):
                root = self.project(major_id)
                self.assertEqual([], [
                    row for row in self.notices.notice_rows(root)
                    if row["check_id"] == CHECK_ID])

    def test_rebinding_to_supported_major_empties_the_file(self):
        """⑪ 1행 → 지원 전공 재바인딩 → 0행."""
        root = self.project("industrial_insects")
        self.assertEqual(1, self.notices.write_notices(root)["notices"])
        bind_major(root, "specialty_crops")
        self.assertEqual(0, self.notices.write_notices(root)["notices"])
        self.assertEqual([], self.document(root)["notices"])


class BlankCopyFinanceTests(UnsupportedFinanceCase):
    """빈 사본까지만 지원하는 전공(식량작물) — 미지원 행 대신 빈 사본 행 1개."""

    def test_blank_copy_major_gets_blank_row_and_no_unsupported_row(self):
        """식량작물: 미지원 0행 + 빈 사본 1행. 다른 5전공: 빈 사본 0행."""
        for major_id in BLANK_COPY:
            with self.subTest(major=major_id):
                root = self.project(major_id)
                self.assertEqual(0, len(self.rows(root)))
                self.assertEqual(1, len(self.rows(root, BLANK_COPY_ID)))
        for major_id in UNSUPPORTED + tuple(
                m for m in SUPPORTED if m not in BLANK_COPY):
            with self.subTest(major=major_id):
                self.assertEqual(
                    0, len(self.rows(self.project(major_id), BLANK_COPY_ID)))
        self.assertEqual(0, len(self.rows(self.project(None), BLANK_COPY_ID)))

    def test_blank_row_shape_and_app_required_fields(self):
        """행 꼴은 미지원 행과 같고, 앱 필수 3필드가 비어 있지 않다."""
        root = self.project("food_crops")
        row = self.rows(root, BLANK_COPY_ID)[0]
        self.assertEqual(BLANK_COPY_TITLE, row["title"])
        self.assertEqual("info", row["severity"])
        self.assertEqual([], row["required_for"])
        self.assertEqual("not_checked", row["status"])
        self.assertEqual("finance", row["target"])
        self.assertEqual([], row["items"])
        self.assertIsNone(row["receipt_path"])
        self.assertEqual([], row["record_refs"])
        self.assertIs(True, row["dismissible"])
        self.assertEqual("identity_changed", row["redisplay_rule"])
        self.assertIs(False, self.core.blocks_skill_candidate(row))
        for field in APP_REQUIRED:
            with self.subTest(field=field):
                self.assertIsInstance(row.get(field), str)
                self.assertTrue(row[field].strip())

    def test_blank_notice_file_has_one_row_with_app_required_fields(self):
        """알림 파일: 빈 사본 1행, 18키, 앱 필수 3필드 비어 있지 않음."""
        root = self.project("food_crops")
        self.assertEqual("written", self.notices.write_notices(root)["status"])
        notices = self.document(root)["notices"]
        self.assertEqual([BLANK_COPY_ID], [n["check_id"] for n in notices])
        notice = notices[0]
        self.assertEqual(DOCUMENT_KEYS, set(notice))
        self.assertRegex(notice["notice_identity"], r"^[0-9a-f]{64}$")
        for field in APP_REQUIRED:
            with self.subTest(field=field):
                self.assertIsInstance(notice.get(field), str)
                self.assertTrue(notice[field].strip())
        title = notice["title"]
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 50, title)
        lowered = title.lower()
        self.assertEqual([], [term for term in FORBIDDEN_STUDENT_TERMS
                              if term.lower() in lowered])
        self.assertEqual([], _majors_in(title))

    def test_blank_thin_path_equals_checks_row(self):
        """알림 파일 경로 == `checks()` 행(바이트 동일)."""
        root = self.project("food_crops")
        thin = self.core._blank_copy_finance_notice_row_for_project(
            root, self.core.load(root))
        full = self.rows(root, BLANK_COPY_ID)[0]
        self.assertEqual(
            json.dumps(full, sort_keys=True, ensure_ascii=False),
            json.dumps(thin, sort_keys=True, ensure_ascii=False))

    def test_blank_identity_import_failure_matches_normal_path(self):
        """대체 계산 == 정상 계산, 그리고 미지원 행 식별값과는 다르다."""
        root = self.project("food_crops")
        normal = self.rows(root, BLANK_COPY_ID)[0]["notice_identity"]
        with mock.patch.dict(sys.modules, {"gg_hort_workbook": None}):
            row = self.core._blank_copy_finance_notice_row_for_project(
                root, self.core.load(root))
        self.assertEqual(normal, row["notice_identity"])
        from gg_hort_workbook import notice_identity
        other_kind = notice_identity(
            project_id=self.core._advice_project_id(root),
            kind="unsupported_finance", profile=None,
            form_identity="food_crops", groups=[])
        self.assertNotEqual(normal, other_kind)

    def test_blank_and_other_notices_never_coexist(self):
        """식량작물 알림 행은 빈 사본 1개뿐, 지원 전공 재바인딩 → 0행."""
        root = self.project("food_crops")
        project = self.core.load(root)
        self.assertIsNone(
            self.core._workbook_notice_row_for_project(root, project))
        self.assertIsNone(
            self.core._unsupported_finance_notice_row_for_project(root, project))
        self.assertEqual([BLANK_COPY_ID], [
            row["check_id"] for row in self.notices.notice_rows(root)])
        self.assertEqual(1, self.notices.write_notices(root)["notices"])
        bind_major(root, "specialty_crops")
        self.assertEqual(0, self.notices.write_notices(root)["notices"])
        self.assertEqual([], self.document(root)["notices"])


class CommitDrivenWriteTests(UnsupportedFinanceCase):
    """⑬ 앱이 아니라 커밋이 파일을 다시 쓴다."""

    def test_commit_rewrites_the_file(self):
        """전공 바인딩·비전공 커밋 모두 파일을 갱신한다."""
        root = self.project("industrial_insects")
        document = self.document(root)         # init·바인딩 커밋이 이미 씀
        self.assertEqual(1, len(document["notices"]))
        self.assertEqual(CHECK_ID, document["notices"][0]["check_id"])
        first_written = document["written_at"]

        revision = self.core.load(root)["revision"]
        self.core.apply(root, {
            "request_id": "unrelated-rule",
            "ops": [{"collection": "rules", "value": {
                "id": "sidecar_commit", "flag": True, "source_refs": []}}]},
            revision)
        changed = self.document(root)
        self.assertNotEqual(first_written, changed["written_at"])
        self.assertEqual(CHECK_ID, changed["notices"][0]["check_id"])

        bind_major(root, "specialty_crops")
        self.assertEqual([], self.document(root)["notices"])

    def test_writer_failure_keeps_commit_and_removes_file(self):
        """writer가 `failed`를 돌려주면 커밋은 남고 파일은 사라진다."""
        root = self.project("industrial_insects")
        target = Path(root) / NOTICES
        self.assertTrue(target.is_file())
        before = self.core.load(root)["revision"]
        stderr = self._commit_with(
            root, mock.patch.object(
                self.notices, "write_notices",
                return_value={"status": "failed", "path": NOTICES,
                              "error": "synthetic", "notices": None}))
        self.assertEqual(before + 1, stderr[0])
        self.assertEqual(1, len(stderr[1]))
        self.assertFalse(target.exists())

    def test_writer_exception_keeps_commit_and_removes_file(self):
        """writer가 던져도 같다 — 예외는 한 줄로 접힌다."""
        root = self.project("industrial_insects")
        target = Path(root) / NOTICES
        before = self.core.load(root)["revision"]
        revision, lines = self._commit_with(
            root, mock.patch.object(self.notices, "write_notices",
                                    side_effect=RuntimeError("boom")))
        self.assertEqual(before + 1, revision)
        self.assertFalse(target.exists())
        self.assertEqual(1, len(lines))
        self.assertIn("RuntimeError: boom", lines[0])

    def _commit_with(self, root, patcher):
        """호출기 하나를 목킹한 채 비전공 커밋 1회. (revision, stderr 줄)"""
        import contextlib
        import io
        stream = io.StringIO()
        revision = self.core.load(root)["revision"]
        with patcher:
            with contextlib.redirect_stderr(stream):
                project = self.core.apply(root, {
                    "request_id": "sidecar-writer-failure",
                    "ops": [{"collection": "rules", "value": {
                        "id": "sidecar_failure", "flag": True,
                        "source_refs": []}}]}, revision)
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        return project["revision"], lines


if __name__ == "__main__":
    unittest.main()
