"""v0.4.9: 엑셀 점검 '안내만'(차단 0) + 알림 계약 + 알림 파일/선택."""
import json
from pathlib import Path
from unittest import mock

from tests._harness import ContractCase, bind_major, runtime, section_op, write_text


class NoticeDataTests(ContractCase):
    """설계 §4.2 표의 종류별 수를 데이터에서 세어 단언한다(N16·N17)."""

    def setUp(self):
        self.hort = runtime("gg_hort_workbook")

    def test_typo_and_method_counts_match_the_spec_table(self):
        counts = self.hort.notice_counts()
        self.assertEqual([7, 18], counts["typo"])
        self.assertEqual([9, 67], counts["method"])

    def test_group_counts_and_cells_add_up(self):
        groups = self.hort.notice_groups()
        self.assertEqual(16, len(groups))
        self.assertEqual(16, len({g["key"] for g in groups}))
        self.assertEqual(85, sum(len(g["cells"]) for g in groups))
        self.assertEqual(85, len(self.hort.notice_group_cells(groups)))

    def test_each_group_kind_has_the_spec_cell_count(self):
        expected = {
            "invest_subtotal_2027": 4, "invest_subtotal_2028_2031": 4,
            "invest_total_missing": 1, "invest_item_cells_cleared": 6,
            "sales_commercialization_rate": 4, "production_area": 5,
            "production_avg_price": 5, "material_textbook_growth_removed": 24,
            "depreciation_grant_net": 15, "balance_last_year_profit": 1,
            "balance_opening_capital": 1, "balance_last_year_land": 1,
            "balance_loan_by_year": 5, "balance_equity_paid_in": 5,
            "cashflow_facility_equipment": 2, "income_double_conversion": 2,
        }
        got = {g["key"]: len(g["cells"]) for g in self.hort.notice_groups()}
        self.assertEqual(expected, got)

    def test_groups_exist_in_the_public_transform_spec(self):
        spec = self.hort.contracts()[self.hort.FILES[0]]
        cells = {(e["sheet"], e["cell"]) for e in spec["entries"]}
        missing = []
        for group in self.hort.notice_groups():
            for coord in group["cells"]:
                sheet, _, cell = coord.rpartition("!")
                if (sheet, cell) not in cells:
                    missing.append(coord)
        self.assertEqual([], missing)

    def test_items_are_one_line_per_group(self):
        items = self.hort.notice_items(self.hort.notice_groups(), source="transform")
        self.assertEqual(16, len(items))
        self.assertTrue(all(item["what"].strip() for item in items))

    def test_title_drops_empty_categories(self):
        self.assertIsNone(self.hort.notice_title(0, 0))
        typo_only = self.hort.notice_title(18, 0)
        method_only = self.hort.notice_title(0, 67)
        both = self.hort.notice_title(18, 67)
        self.assertIn("18", typo_only)
        self.assertNotIn("계산 방식", typo_only)
        self.assertIn("67", method_only)
        self.assertNotIn("수식 오류", method_only)
        self.assertIn("18", both)
        self.assertIn("67", both)

    def test_title_has_no_internal_terms(self):
        text = self.hort.notice_title(18, 67)
        for banned in ("!", "묶음", "typo", "method", "check_id", "registry"):
            self.assertNotIn(banned, text)
        self.assertNotIn("\n", text)

    def test_identity_ignores_choice(self):
        """선택은 식별값을 바꾸지 않는다 — 바뀌면 앱이 말풍선을 다시 띄운다."""
        import inspect
        params = inspect.signature(self.hort.notice_identity).parameters
        self.assertNotIn("choice", params)
        groups = self.hort.notice_groups()
        same = dict(project_id="p1", kind="transform", profile="x",
                    form_identity="f", groups=groups)
        self.assertEqual(self.hort.notice_identity(**same),
                         self.hort.notice_identity(**same))

    def test_identity_is_stable_and_project_scoped(self):
        groups = self.hort.notice_groups()
        a = self.hort.notice_identity(project_id="p1", kind="transform",
                                      profile="x", form_identity="f", groups=groups)
        b = self.hort.notice_identity(project_id="p1", kind="transform",
                                      profile="x", form_identity="f", groups=groups)
        c = self.hort.notice_identity(project_id="p2", kind="transform",
                                      profile="x", form_identity="f", groups=groups)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_fruit_counts(self):
        counts = self.hort.notice_counts(fruit=True)
        self.assertEqual([2, 21], counts["typo"])
        self.assertEqual([0, 0], counts["method"])


class AdviceRowTests(ContractCase):
    """checks() 안내 행이 계약대로 만들어지는가."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.root = self.make_project()

    def _rows(self):
        return self.core.checks(self.root, self.core.load(self.root))

    def _has(self, rows, cid):
        return [r for r in rows if r.get("check_id") == cid]

    def _project_with_output(self, profile, receipt_kind="template_receipt",
                             sha=None, oid="wb1"):
        """채택 산출물 레코드를 **메모리 정본**에 넣고 그 project를 돌려준다.

        발행·채택 경로를 부르지 않는다(그 코드는 이번 판에서 무수정).
        기존 시험도 ``project["outputs"]``를 직접 채워 ``checks``에 넘긴다.
        """
        write_text(self.root, "out/workbook.xlsx", "synthetic")
        write_text(self.root, "out/receipt.json", json.dumps({"a": 1}))
        data = (Path(self.root) / "out/receipt.json").read_bytes()
        project = self.core.load(self.root)
        project.setdefault("outputs", {})
        project["outputs"][oid] = {
            "id": oid,
            "path": "out/workbook.xlsx",
            "format": "xlsx",
            "file_hash": "0" * 64,
            "target_refs": [],
            "input_fingerprint": "0" * 64,
            "revision": 1,
            "workbook_profile": profile,
            receipt_kind: {
                "path": "out/receipt.json",
                "sha256": sha if sha is not None else self.core.digest(data),
            },
        }
        return project

    def _rows(self, project=None):
        project = project if project is not None else self.core.load(self.root)
        return self.core.checks(self.root, project)

    def _has(self, rows, cid):
        return [r for r in rows if r.get("check_id") == cid]

    def test_no_advice_for_other_majors(self):
        bind_major(self.root, "specialty_crops")
        self.assertEqual([], self._has(self._rows(), "finance_workbook_notice"))

    def test_not_checked_row_when_body_exists_but_no_workbook(self):
        bind_major(self.root, "hort_env_systems")
        write_text(self.root, "sections/body.md", "# 검토\n합성.\n")
        p = self.core.load(self.root)
        self.core.apply(self.root, {"request_id": "body", "ops": [
            section_op("body", "검토", "sections/body.md")]}, p["revision"])
        rows = self._has(self._rows(), "finance_workbook_not_checked")
        self.assertEqual(1, len(rows))
        self.assertEqual("info", rows[0]["severity"])

    def test_notice_row_for_horticulture(self):
        bind_major(self.root, "hort_env_systems")
        project = self._project_with_output("hort_18_sheet_reconstructed_v1")
        rows = self._has(self._rows(project), "finance_workbook_notice")
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("info", row["severity"])
        self.assertEqual("check_needed", row["status"])
        self.assertFalse(self.core.blocks_skill_candidate(row))
        self.assertEqual(16, len(row["items"]))
        self.assertEqual(18, row["typo_count"])
        self.assertEqual(67, row["method_count"])
        self.assertEqual([{"collection": "outputs", "id": "wb1"}],
                         row["record_refs"])
        self.assertEqual(2, len(row["choices"]))
        self.assertIsNone(row["choice"])
        self.assertTrue(row["choice_hint"])
        self.assertEqual(64, len(row["notice_identity"]))

    def test_notice_row_for_fruit_receipt_only(self):
        """template_receipt 만 가진 정상 과수 채택본도 링크가 채워진다(N8b)."""
        bind_major(self.root, "fruit_trees")
        project = self._project_with_output("fruit_16_sheet_v1_fy1")
        rows = self._has(self._rows(project), "finance_workbook_notice")
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("out/receipt.json", row["receipt_path"])
        self.assertIsNotNone(row["receipt_path"])
        self.assertEqual(2, len(row["items"]))
        self.assertEqual(0, row["method_count"])
        self.assertNotIn("choices", row)

    def test_receipt_path_blank_when_hash_mismatch(self):
        bind_major(self.root, "hort_env_systems")
        project = self._project_with_output("hort_18_sheet_reconstructed_v1",
                                            sha="f" * 64)
        rows = self._has(self._rows(project), "finance_workbook_notice")
        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0]["receipt_path"])
        self.assertEqual(16, len(rows[0]["items"]))


class NoticesFileTests(ContractCase):
    """알림 파일(.knuaf-notices.json) 계약."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")
        self.root = self.make_project()
        bind_major(self.root, "hort_env_systems")

    def _write(self):
        info = self.notices.write_notices(self.root)
        path = Path(self.root) / ".knuaf-notices.json"
        return info, path, json.loads(path.read_text(encoding="utf-8"))

    def test_file_written_with_schema_and_empty_list(self):
        info, path, doc = self._write()
        self.assertEqual("written", info["status"])
        self.assertTrue(path.is_file())
        self.assertEqual("knuaf.notices/1", doc["schema"])
        self.assertIsInstance(doc["notices"], list)
        self.assertEqual([], doc["notices"])
        self.assertEqual(info["project_id"], doc["project_id"])
        self.assertIn("written_at", doc)

    def test_no_temp_file_left_behind(self):
        self._write()
        leftovers = [p.name for p in Path(self.root).glob(".knuaf-notices-*.tmp")]
        self.assertEqual([], leftovers)

    def test_items_capped_and_extra_line(self):
        row = {"check_id": "finance_workbook_notice", "title": "가" * 3,
               "items": [{"key": "g%d" % i, "kind": "typo", "what": "x",
                          "cells": [], "source": "transform",
                          "in_school_form": True} for i in range(51)],
               "receipt_path": None, "record_refs": []}
        clean = self.notices._sanitize_row(row)
        self.assertEqual(51, len(clean["items"]))
        self.assertEqual("외 1곳", clean["items"][-1]["what"])
        self.assertEqual(50, len(clean["items"]) - 1)

    def test_title_is_one_line(self):
        clean = self.notices._sanitize_row({"title": "가\n나\t다", "items": []})
        self.assertNotIn("\n", clean["title"])
        self.assertEqual("가 나 다", clean["title"])

    def test_absolute_receipt_path_dropped(self):
        clean = self.notices._sanitize_row(
            {"title": "t", "items": [], "receipt_path": "/Users/x/receipt.json"})
        self.assertIsNone(clean["receipt_path"])

    def test_relative_receipt_path_kept(self):
        clean = self.notices._sanitize_row(
            {"title": "t", "items": [], "receipt_path": "out/receipt.json"})
        self.assertEqual("out/receipt.json", clean["receipt_path"])

    def test_record_refs_are_structured_not_paths(self):
        clean = self.notices._sanitize_row({
            "title": "t", "items": [],
            "record_refs": [{"collection": "outputs", "id": "wb1"},
                            "out/receipt.json", {"collection": "outputs"}]})
        self.assertEqual([{"collection": "outputs", "id": "wb1"}],
                         clean["record_refs"])

    def test_choose_rejects_unknown_value(self):
        result = self.notices.choose(self.root, "x" * 64, "nope")
        self.assertEqual("rejected", result["status"])
        self.assertEqual("choice_value_invalid", result["reason"])

    def test_choose_rejects_unknown_identity(self):
        result = self.notices.choose(self.root, "x" * 64, "footnote")
        self.assertEqual("rejected", result["status"])
        self.assertIn(result["reason"],
                      {"notice_identity_unknown", "choice_not_offered"})

    def _project_with_notice(self):
        """알림이 뜨는 정본을 메모리에 만들고 파일에 반영한다."""
        write_text(self.root, "out/workbook.xlsx", "synthetic")
        write_text(self.root, "out/receipt.json", json.dumps({"a": 1}))
        data = (Path(self.root) / "out/receipt.json").read_bytes()
        project = self.core.load(self.root)
        project.setdefault("outputs", {})
        project["outputs"]["wb1"] = {
            "id": "wb1", "path": "out/workbook.xlsx", "format": "xlsx",
            "file_hash": "0" * 64, "target_refs": [], "input_fingerprint": "0" * 64,
            "revision": 1, "workbook_profile": "hort_18_sheet_reconstructed_v1",
            "template_receipt": {"path": "out/receipt.json",
                                 "sha256": self.core.digest(data)},
        }
        return project

    def test_choose_records_choice_and_rewrites_file(self):
        project = self._project_with_notice()
        rows = [r for r in self.core.checks(self.root, project)
                if r.get("check_id") == "finance_workbook_notice"]
        self.assertEqual(1, len(rows))
        identity = rows[0]["notice_identity"]
        with mock.patch.object(self.notices, "notice_rows", return_value=rows):
            result = self.notices.choose(self.root, identity, "footnote")
        self.assertEqual("chosen", result["status"])
        self.assertEqual("footnote", result["choice"])

        saved = self.core.load(self.root)
        fact = saved["facts"].get("finance.workbook.accounting_notice.choice")
        self.assertIsNotNone(fact)
        self.assertEqual("footnote", fact["value"])
        self.assertIn("finance.workbook.accounting_notice.choice", saved["questions"])

    def test_identity_unchanged_before_and_after_choice(self):
        """같은 프로젝트에서 선택 전·후 알림 식별값이 같다(엔드투엔드)."""
        project = self._project_with_notice()
        before = [r for r in self.core.checks(self.root, project)
                  if r.get("check_id") == "finance_workbook_notice"]
        self.assertEqual(1, len(before))
        identity_before = before[0]["notice_identity"]
        with mock.patch.object(self.notices, "notice_rows", return_value=before):
            result = self.notices.choose(self.root, identity_before, "footnote")
        self.assertEqual("chosen", result["status"])
        # 디스크 정본에는 산출물이 없다(테스트가 메모리에만 넣었다).
        # 선택이 기록된 뒤의 정본에 같은 산출물을 다시 얹어 비교한다.
        reloaded = self._project_with_notice()
        after = [r for r in self.core.checks(self.root, reloaded)
                 if r.get("check_id") == "finance_workbook_notice"]
        self.assertEqual(1, len(after))
        self.assertEqual(identity_before, after[0]["notice_identity"])
        self.assertEqual("footnote", after[0]["choice"])

    def test_choose_twice_uses_registered_source_ref(self):
        """재선택은 source_ref 계약(등록된 source id)을 채워야 통과한다."""
        project = self._project_with_notice()
        rows = [r for r in self.core.checks(self.root, project)
                if r.get("check_id") == "finance_workbook_notice"]
        identity = rows[0]["notice_identity"]
        with mock.patch.object(self.notices, "notice_rows", return_value=rows):
            first = self.notices.choose(self.root, identity, "direct_tell")
            self.assertEqual("chosen", first["status"])
            second = self.notices.choose(self.root, identity, "footnote")
        self.assertEqual("chosen", second["status"])
        saved = self.core.load(self.root)
        self.assertEqual(
            "footnote",
            saved["facts"]["finance.workbook.accounting_notice.choice"]["value"])
        question = saved["questions"]["finance.workbook.accounting_notice.choice"]
        self.assertEqual(2, question["decision_revision"])
        self.assertEqual("user_change", question["resume_reason"])
        self.assertIn(question["source_ref"]["id"], saved["sources"])


def self_mock_checks(case, project):
    """(미사용) 이전 시도 흔적."""
    return None

class NoticeAutoWriteTests(ContractCase):
    """채택 직후 알림 파일 자동 쓰기 (A안)."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.notices = runtime("gg_notices")
        self.root = self.make_project()
        bind_major(self.root, "hort_env_systems")

    def _adoptable_fruit(self, root=None):
        """과수 16시트 합성 양식 + native 영수증으로 **실제 채택 가능한** 메타.

        과수 채택이므로 이 시험 전용 프로젝트를 **과수로 바인딩**해 쓴다.
        """
        from openpyxl import Workbook
        from tests.test_finance_profile_gate import register_synthetic_form
        if root is None:
            root = self.make_project()
            bind_major(root, "fruit_trees")
            self.fruit_root = root
        school = runtime("gg_school_excel")
        book = Workbook()
        sheets = school.profile_structure("fruit_16_sheet_v1")["sheets"]
        book.active.title = sheets[0]
        book.active["B1"] = "=A1*2"
        for title in sheets[1:]:
            book.create_sheet(title)
        register_synthetic_form(self, book)
        book.active["A1"] = 40
        book.active["B1"] = "=A1*2"
        write_text(root, "body.md", "# synthetic\nstudent plan\n")
        self.core.apply(root, {"request_id": "body", "ops": [
            section_op("body", "synthetic", "body.md")]},
            self.core.load(root)["revision"])
        path = Path(root) / "fruit.xlsx"
        book.save(path)
        digest = self.core.digest(path.read_bytes())
        manifest = {"status": "converted", "engine": "Microsoft Excel",
                    "published_office_sha256": digest,
                    "validation": {"workbook_profile": "fruit_16_sheet_v1",
                                   "finance_profile": "fruit_16_sheet_v1",
                                   "structure_issues": [], "school_issues": [],
                                   "excel": {"valid": True, "sha256": digest}}}
        raw = json.dumps(manifest).encode()
        write_text(root, "native.json", raw.decode())
        pointer = {"path": "native.json", "sha256": self.core.digest(raw)}
        project = self.core.load(root)
        refs = self.core.sort_target_refs(self.core._export_refs(project))
        value = {"id": "fruit", "path": path.name, "format": "xlsx",
                 "file_hash": digest, "target_refs": refs,
                 "input_fingerprint": self.core.fingerprint(root, project, refs),
                 "native_manifest": pointer}
        return project, value, [pointer]

    def _store_fruit_output(self):
        """메모리 정본에 과수 산출물을 얹는다(채택 경로 미호출)."""
        project = self.core.load(self.fruit_root)
        project.setdefault("outputs", {})["fruit"] = {
            "id": "fruit", "path": "fruit.xlsx", "format": "xlsx",
            "file_hash": "0" * 64, "target_refs": [],
            "input_fingerprint": "0" * 64, "revision": 1,
            "workbook_profile": "fruit_16_sheet_v1",
            "native_manifest": {"path": "native.json", "sha256": "0" * 64},
        }
        return project

    def test_thin_path_equals_full_checks_byte_for_byte(self):
        """얇은 경로가 전체 checks와 같은 알림 행을 낸다(바이트 비교)."""
        project, _value, _c = self._adoptable_fruit()
        project = self._store_fruit_output()
        full = [r for r in self.core.checks(self.fruit_root, project)
                if r.get("check_id") == "finance_workbook_notice"]
        thin = self.core._workbook_notice_row_for_project(self.fruit_root, project)
        self.assertEqual(1, len(full))
        self.assertIsNotNone(thin)
        self.assertEqual(
            json.dumps(full[0], ensure_ascii=False, sort_keys=True),
            json.dumps(thin, ensure_ascii=False, sort_keys=True))

    def test_write_notices_matches_checks_rows(self):
        """write_notices 결과가 checks()의 알림 행과 같다."""
        self._adoptable_fruit()
        project = self._store_fruit_output()
        from unittest import mock
        with mock.patch.object(self.core, "load", return_value=project):
            doc = self.notices.notices_document(
                self.fruit_root, self.notices.notice_rows(self.fruit_root))
        full = [r for r in self.core.checks(self.fruit_root, project)
                if r.get("check_id") == "finance_workbook_notice"]
        self.assertEqual(1, len(full))
        self.assertEqual(full[0]["notice_identity"],
                         doc["notices"][0]["notice_identity"])
        self.assertEqual(full[0]["title"], doc["notices"][0]["title"])

    def test_adoption_writes_notice_file(self):
        """(가) 채택 → 알림 파일 생성 → 계약 꼴 일치."""
        project, value, companions = self._adoptable_fruit()
        result = self.core.adopt_output(
            self.fruit_root, value, project["revision"], "req-1",
            companion_files=companions, major_id="fruit_trees")
        self.assertEqual("adopted", result["status"])
        self.assertEqual("written", result["notices_write"]["status"])
        path = Path(self.fruit_root) / ".knuaf-notices.json"
        self.assertTrue(path.is_file())
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("knuaf.notices/1", doc["schema"])
        self.assertEqual({"schema", "written_at", "project_id", "notices"}, set(doc))
        self.assertEqual(1, len(doc["notices"]))
        notice = doc["notices"][0]
        for key in ("check_id", "title", "items", "notice_identity",
                    "receipt_path", "record_refs", "typo_count",
                    "method_count", "dismissible", "redisplay_rule",
                    "project_id"):
            self.assertIn(key, notice)
        self.assertEqual("finance_workbook_notice", notice["check_id"])
        self.assertIsInstance(notice["title"], str)
        self.assertNotIn("\n", notice["title"])
        self.assertEqual(2, len(notice["items"]))       # 과수 fy1 = 2묶음
        self.assertEqual(21, notice["typo_count"])
        self.assertEqual(0, notice["method_count"])
        self.assertNotIn("choices", notice)             # 과수는 선택 없음
        self.assertEqual([{"collection": "outputs", "id": "fruit"}],
                         notice["record_refs"])
        self.assertIsNotNone(notice["receipt_path"])
        self.assertFalse(notice["receipt_path"].startswith("/"))
        self.assertFalse(self.core.blocks_skill_candidate(
            {"status": "check_needed", "severity": "info", "owner": "skill"}))

    def test_adoption_without_notice_target_writes_empty_list(self):
        """(나) 알림 대상이 아닌 채택 → notices 빈 배열, 파일 꼴은 계약대로."""
        other = self.make_project()
        bind_major(other, "specialty_crops")
        # 실제 xlsx 바이트여야 한다 — adopt_output은 파일 내용으로 형식을
        # 판별하므로 텍스트를 .xlsx로 두면 unknown_output으로 거절된다.
        from openpyxl import Workbook
        (Path(other) / "out").mkdir(parents=True, exist_ok=True)
        book = Workbook()
        book.active["A1"] = "v"
        book.save(Path(other) / "out/w.xlsx")
        project = self.core.load(other)
        refs = self.core.sort_target_refs(self.core._export_refs(project))
        value = {
            "id": "wb2", "path": "out/w.xlsx", "format": "xlsx",
            "file_hash": self.core.digest(
                (Path(other) / "out/w.xlsx").read_bytes()),
            "target_refs": refs,
            # 64자 sha256이어야 한다(_check_sha256) — 그리고 실제 입력
            # 지문과 같아야 input_fingerprint_mismatch에 걸리지 않는다.
            "input_fingerprint": self.core.fingerprint(other, project, refs),
        }
        result = self.core.adopt_output(
            other, value, project["revision"], "req-2",
            major_id="specialty_crops")
        self.assertEqual("adopted", result["status"])
        self.assertEqual("written", result["notices_write"]["status"])
        doc = json.loads((Path(other) / ".knuaf-notices.json").read_text(encoding="utf-8"))
        self.assertEqual("knuaf.notices/1", doc["schema"])
        self.assertEqual([], doc["notices"])

    def test_write_failure_keeps_adoption_and_reports(self):
        """(다) 쓰기 실패 → 접수 성공 + 내부 필드로 표시(조용히 삼키지 않음)."""
        project, value, companions = self._adoptable_fruit()
        from unittest import mock
        with mock.patch.object(self.notices, "write_notices",
                               side_effect=OSError("disk full")):
            result = self.core.adopt_output(
                self.fruit_root, value, project["revision"], "req-3",
                companion_files=companions, major_id="fruit_trees")
        self.assertEqual("adopted", result["status"])
        write = result["notices_write"]
        self.assertEqual("failed", write["status"])
        self.assertEqual("OSError", write["error"])
        self.assertIn("disk full", write["detail"])
        self.assertEqual("fruit", self.core.load(self.fruit_root)["outputs"]["fruit"]["id"])
