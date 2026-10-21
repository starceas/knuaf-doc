"""WP-1: 검사 차단의 복구 경로와 오탐·공백 수정.

원장 G3-02·G3-03·G3-04·G3-05·G3-13① 과 G1-10·G1-11·G1-12를 닫는
시험이다. 모든 입력은 합성 자료이며 실제 학생 프로젝트를 쓰지 않는다.
"""
import json
from pathlib import Path
from unittest import mock

from tests._harness import (
    ContractCase,
    bind_major,
    fact_op,
    runtime,
    section_op,
    source_op,
    write_text,
)


class SourceRefreshTests(ContractCase):
    """G3-04/G1-12: 원자료 해시 재등록 경로."""

    def _project(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "원답변 첫 판\n")
        fact = fact_op("sales", "매출액", "1000", "천원",
                       value_type="decimal", period="2026년")
        core.apply(root, {"request_id": "wp1-source", "ops": [
            source_op(), fact]}, core.load(root)["revision"])
        return core, root

    def test_preview_changes_nothing_and_blocks_without_review(self):
        core, root = self._project()
        write_text(root, "answer.txt", "원답변 둘째 판\n")
        before = self._tree_bytes(root)
        preview = core.source_refresh(root, "answer")
        self.assertEqual("preview", preview["status"])
        self.assertTrue(preview["changed"])
        self.assertEqual(["sales"], preview["dependents"]["facts"])
        self.assertEqual(before, self._tree_bytes(root))
        with self.assertRaises(ValueError):
            core.source_refresh(root, "answer", apply_change=True)
        self.assertEqual(before, self._tree_bytes(root))

    def test_reviewed_apply_rehashes_and_closes_source_hash(self):
        core, root = self._project()
        write_text(root, "answer.txt", "원답변 둘째 판\n")
        p = core.load(root)
        self.assertIn(
            "source_hash",
            {r["check_id"] for r in core.checks(root, p)})
        value = core.source_refresh(
            root, "answer", reviewed=True, apply_change=True,
            expected_revision=p["revision"])
        self.assertEqual("refreshed", value["status"])
        refreshed = core.load(root)
        source = refreshed["sources"]["answer"]
        self.assertEqual(2, source["revision"])
        self.assertEqual(core.digest("원답변 둘째 판\n".encode()), source["hash"])
        self.assertEqual(
            2, refreshed["facts"]["sales"]["source_refs"][0]["revision"])
        self.assertNotIn(
            "source_hash",
            {r["check_id"] for r in core.checks(root, refreshed)})

    def test_unknown_source_and_stale_revision_are_rejected(self):
        core, root = self._project()
        write_text(root, "answer.txt", "원답변 둘째 판\n")
        with self.assertRaises(ValueError):
            core.source_refresh(root, "no-such-source")
        with self.assertRaises(ValueError):
            core.source_refresh(
                root, "answer", reviewed=True, apply_change=True,
                expected_revision=core.load(root)["revision"] + 5)


class ClaimRecoveryTests(ContractCase):
    """G3-04/G1-11: claim_missing 복구 서식이 실제로 차단을 닫는다."""

    def _blocked_project(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출 1,000천원\n")
        fact = fact_op("sales", "매출액", "1000", "천원",
                       value_type="decimal", period="2026년", claim_id="c1")
        core.apply(root, {"request_id": "wp1-claim", "ops": [
            source_op(claims={}), fact]}, core.load(root)["revision"])
        return core, root

    def test_blocked_row_carries_runnable_recovery(self):
        core, root = self._blocked_project()
        p = core.load(root)
        row = next(r for r in core.checks(root, p)
                   if r["check_id"] == "claim_missing")
        self.assertEqual("blocked", row["status"])
        recovery = row["recovery"]
        self.assertEqual("claim_missing", recovery["code"])
        self.assertTrue(recovery["steps"])
        self.assertEqual(1, len(recovery["argv"]))
        self.assertIn("apply", recovery["argv"][0])
        claim = recovery["change"]["ops"][0]["value"]["claims"]["c-sales"]
        self.assertEqual("1000", claim["value"])
        self.assertEqual("매출액", claim["field_id"])

    def test_applying_the_recovery_change_closes_the_block(self):
        core, root = self._blocked_project()
        p = core.load(root)
        recovery = next(
            r for r in core.checks(root, p)
            if r["check_id"] == "claim_missing")["recovery"]
        change = recovery["change"]
        change["ops"][0]["value"]["claims"]["c-sales"]["quote"] = "매출 1,000천원"
        path = write_text(root, ".gg-work/recovery/claim.json",
                          json.dumps(change, ensure_ascii=False))
        core.apply(root, json.loads(path.read_text(encoding="utf-8")),
                   p["revision"])
        self.assertNotIn(
            "claim_missing",
            {r["check_id"] for r in core.checks(root, core.load(root))})

    def test_shared_source_refs_follow_the_recovered_revision(self):
        # 검토자 반례: 누락 claim 1개 + 같은 출처의 정상 claim 1개.
        # 복구 change를 그대로 적용하면 정상 사실까지 개정이 어긋나면 안 된다.
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출 1,000천원; 재배면적 100㎡\n")
        missing = fact_op("sales", "매출액", "1000", "천원",
                          value_type="decimal", period="2026년",
                          claim_id="missing")
        area = fact_op("area", "재배면적", "100", "㎡",
                       value_type="decimal", period="2026년",
                       claim_id="area")
        claim = {k: area["value"][k] for k in (
            "field_id", "value", "unit", "period", "scope",
            "answer_state", "kind")}
        core.apply(root, {"request_id": "wp1-shared", "ops": [
            source_op(claims={"area": claim}), missing, area]},
            core.load(root)["revision"])
        p = core.load(root)
        change = next(
            r for r in core.checks(root, p)
            if r["check_id"] == "claim_missing")["recovery"]["change"]
        source = next(o for o in change["ops"]
                      if o["collection"] == "sources")
        source["value"]["claims"]["c-sales"]["quote"] = "매출 1,000천원"
        core.apply(root, change, p["revision"])
        after = core.load(root)
        self.assertEqual(2, after["sources"]["answer"]["revision"])
        for fid in ("sales", "area"):
            self.assertEqual(
                2, after["facts"][fid]["source_refs"][0]["revision"])
        self.assertEqual(
            [],
            [r["check_id"] for r in core.checks(root, after)
             if r["check_id"] in ("claim_missing", "source_revision")])

    def test_recovery_rebases_derived_inputs(self):
        # 같은 출처를 쓰는 계산 사실의 입력 개정도 함께 맞춘다.
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출 1,000천원; 재배면적 100㎡\n")
        missing = fact_op("sales", "매출액", "1000", "천원",
                          value_type="decimal", period="2026년",
                          claim_id="missing")
        area = fact_op("area", "재배면적", "100", "㎡",
                       value_type="decimal", period="2026년",
                       claim_id="area")
        derived = {"collection": "facts", "value": {
            "id": "per_area", "field_id": "매출액/면적", "kind": "derived",
            "value": "10", "unit": "천원/㎡", "value_type": "decimal",
            "period": "2026년", "scope": "farm", "answer_state": "provided",
            "verification": "claim_supported",
            "source_refs": [
                {"id": "answer", "locator": "line 1", "revision": 1}],
            "input_refs": [{"id": "sales", "revision": 1},
                           {"id": "area", "revision": 1}],
            "formula": {"op": "divide", "unit_reason": "매출을 면적으로 나눔"}}}
        claim = {k: area["value"][k] for k in (
            "field_id", "value", "unit", "period", "scope",
            "answer_state", "kind")}
        core.apply(root, {"request_id": "wp1-derived", "ops": [
            source_op(claims={"area": claim}), missing, area, derived]},
            core.load(root)["revision"])
        p = core.load(root)
        change = next(
            r for r in core.checks(root, p)
            if r["check_id"] == "claim_missing")["recovery"]["change"]
        source = next(o for o in change["ops"]
                      if o["collection"] == "sources")
        source["value"]["claims"]["c-sales"]["quote"] = "매출 1,000천원"
        core.apply(root, change, p["revision"])
        after = core.load(root)
        self.assertEqual(
            [2, 2],
            [r["revision"] for r in after["facts"]["per_area"]["input_refs"]])
        self.assertEqual(
            [],
            [r["check_id"] for r in core.checks(root, after)
             if r["check_id"] in
             ("claim_missing", "source_revision", "calculation")])

    def _section_project(self, body):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출 1,000천원; 재배면적 100㎡\n")
        missing = fact_op("sales", "매출액", "1000", "천원",
                          value_type="decimal", period="2026년",
                          claim_id="missing")
        area = fact_op("area", "재배면적", "100", "㎡",
                       value_type="decimal", period="2026년",
                       claim_id="area")
        claim = {k: area["value"][k] for k in (
            "field_id", "value", "unit", "period", "scope",
            "answer_state", "kind")}
        write_text(root, "sections/intro.md", body)
        core.apply(root, {"request_id": "wp1-section-recovery", "ops": [
            source_op(claims={"area": claim}), missing, area,
            section_op("intro", "Ⅰ. 머리말", "sections/intro.md",
                       depends_on=[{"collection": "facts", "id": "sales",
                                    "revision": 1}])]},
            core.load(root)["revision"])
        return core, root

    def _apply_recovery(self, core, root):
        p = core.load(root)
        change = next(
            r for r in core.checks(root, p)
            if r["check_id"] == "claim_missing")["recovery"]["change"]
        next(o for o in change["ops"]
             if o["collection"] == "sources")["value"]["claims"]["c-sales"][
                 "quote"] = "매출 1,000천원"
        core.apply(root, change, p["revision"])
        return core.load(root)

    def test_unregistered_body_change_is_not_adopted_by_claim_recovery(self):
        # 검토자 반례: 참조 개정만 맞추는 복구가 등록하지 않은 본문 변경을
        # 재해시해 draft_changed 오류를 지우면 안 된다.
        core, root = self._section_project("# Ⅰ. 머리말\n\n기존 등록 본문.\n")
        write_text(root, "sections/intro.md", "# Ⅰ. 머리말\n\n등록하지 않은 수정.\n")
        p = core.load(root)
        registered = p["sections"]["intro"]["draft_hash"]
        self.assertIn("draft_changed",
                      {r["check_id"] for r in core.checks(root, p)})
        after = self._apply_recovery(core, root)
        self.assertEqual(registered, after["sections"]["intro"]["draft_hash"])
        self.assertEqual(1, after["sections"]["intro"]["revision"])
        self.assertNotEqual(
            registered, core.digest(core.draft(
                (root / "sections/intro.md").read_text(encoding="utf-8"))))
        ids = {r["check_id"] for r in core.checks(root, after)}
        self.assertIn("draft_changed", ids)
        self.assertNotIn("claim_missing", ids)
        self.assertNotIn("source_revision", ids)

    def test_unchanged_body_section_is_rebased_by_claim_recovery(self):
        core, root = self._section_project("# Ⅰ. 머리말\n\n기존 등록 본문.\n")
        after = self._apply_recovery(core, root)
        self.assertEqual(2, after["sections"]["intro"]["revision"])
        self.assertEqual(
            2, after["sections"]["intro"]["depends_on"][0]["revision"])
        ids = {r["check_id"] for r in core.checks(root, after)}
        self.assertNotIn("draft_changed", ids)
        self.assertNotIn("dependency_revision", ids)

    def test_recovery_names_sections_left_for_separate_registration(self):
        core, root = self._section_project("# Ⅰ. 머리말\n\n기존 등록 본문.\n")
        write_text(root, "sections/intro.md", "# Ⅰ. 머리말\n\n등록하지 않은 수정.\n")
        row = next(r for r in core.checks(root, core.load(root))
                   if r["check_id"] == "claim_missing")
        self.assertTrue(any("intro" in s for s in row["recovery"]["steps"]))
        self.assertIn("자동으로 재등록하지 않는다",
                      row["recovery"]["notice"])

    def test_school_and_guideline_recovery_are_attached(self):
        core = runtime("gg_core")
        root = self.make_project()
        p = core.load(root)
        row = next(r for r in core.checks(root, p)
                   if r["check_id"] == "school_profile")
        self.assertEqual("school_profile", row["recovery"]["code"])
        self.assertIn("내장 학교 지침", row["recovery"]["steps"][0])
        fake = {"status": "ready", "guideline_ready": False,
                "reason": "지침 항목별 검증 근거 누락", "blockers": [],
                "inventory": {}, "items": []}
        with mock.patch("gg_guidelines.check", return_value=fake):
            report = core.checks(root, p)
        row = next(r for r in report if r["check_id"] == "guideline_profile")
        self.assertEqual("blocked", row["status"])
        self.assertEqual("guideline_profile", row["recovery"]["code"])


class BodyRegistryTests(ContractCase):
    """G3-02/G1-10: 본문 파일이 있는데 등록되지 않은 상태."""

    def test_unregistered_body_file_is_reported_with_recovery(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sections/01-개요.md", "# Ⅰ. 머리말\n\n본문이다.\n")
        p = core.load(root)
        self.assertEqual({}, p["sections"])
        rows = [r for r in core.checks(root, p)
                if r["check_id"] == "body_registry"]
        self.assertEqual(1, len(rows))
        self.assertEqual("warning", rows[0]["severity"])
        self.assertIn("01-개요.md", rows[0]["recovery"]["summary"])

    def test_registered_body_file_clears_the_gap(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sections/01-개요.md", "# Ⅰ. 머리말\n\n본문이다.\n")
        core.apply(root, {"request_id": "wp1-section", "ops": [
            section_op("intro", "Ⅰ. 머리말", "sections/01-개요.md")]},
            core.load(root)["revision"])
        rows = [r for r in core.checks(root, core.load(root))
                if r["check_id"] == "body_registry"]
        self.assertEqual([], rows)

    def test_extra_unregistered_file_is_still_reported(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "sections/01-개요.md", "# Ⅰ. 머리말\n\n본문이다.\n")
        core.apply(root, {"request_id": "wp1-section-2", "ops": [
            section_op("intro", "Ⅰ. 머리말", "sections/01-개요.md")]},
            core.load(root)["revision"])
        write_text(root, "sections/02-초안.md", "# Ⅱ. 초안\n\n메모.\n")
        rows = [r for r in core.checks(root, core.load(root))
                if r["check_id"] == "body_registry"]
        self.assertEqual(1, len(rows))
        self.assertIn("02-초안.md", rows[0]["recovery"]["summary"])

    def test_root_level_body_does_not_flag_preserved_materials(self):
        # 검토자 반례: 본문이 루트에 있으면 인터뷰 원답변·학교 자료까지
        # 미등록 본문으로 판정해 논문 절로 승격하라고 안내했다.
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "manuscript.md", "# Ⅰ. 머리말\n\n본문이다.\n")
        write_text(root, "00_interview_log.md", "# 인터뷰 기록\n\n원답변 보존본.\n")
        write_text(root, "sources/guidelines/official-toc.md", "# 학교 목차\n")
        core.apply(root, {"request_id": "wp1-root-body", "ops": [
            section_op("intro", "Ⅰ. 머리말", "manuscript.md")]},
            core.load(root)["revision"])
        rows = [r for r in core.checks(root, core.load(root))
                if r["check_id"] == "body_registry"]
        self.assertEqual([], rows)


class BodyFinanceScopeTests(ContractCase):
    """G3-03: 비교 대상이 없어 아무것도 안 본 상태를 드러낸다."""

    def test_no_monetary_fact_reports_not_checked(self):
        core = runtime("gg_core")
        root = self.make_project()
        rows = [r for r in core.checks(root, core.load(root))
                if r["check_id"] == "body_finance_crosscheck_scope"]
        self.assertEqual(1, len(rows))
        self.assertEqual(("info", "not_checked"),
                         (rows[0]["severity"], rows[0]["status"]))
        self.assertIn("대조할 금액 사실이 없음", rows[0]["reason"])

    def test_monetary_fact_removes_the_not_checked_row(self):
        core = runtime("gg_core")
        root = self.make_project()
        write_text(root, "answer.txt", "매출 1,000천원\n")
        fact = fact_op("sales", "매출액", "1000", "천원",
                       value_type="decimal", period="2026년", claim_id="c1")
        core.apply(root, {"request_id": "wp1-finance", "ops": [
            source_op(claims={"c1": {
                k: fact["value"][k] for k in (
                    "field_id", "value", "unit", "period", "scope",
                    "answer_state", "kind")}}), fact]},
            core.load(root)["revision"])
        rows = [r for r in core.checks(root, core.load(root))
                if r["check_id"] == "body_finance_crosscheck_scope"]
        self.assertEqual([], rows)


class BibliographyJoinTests(ContractCase):
    """G3-05: 연도 접미와 URL·조회일 숫자를 발행연도로 오인하지 않는다."""

    HEAD = "# Ⅵ. 참고문헌\n\n"

    def _ids(self, body):
        doc = runtime("gg_document")
        return [cid for cid, _ in
                doc.check(self.HEAD + body, None, major_id="specialty_crops")]

    def test_missing_publication_year_with_url_number_is_not_a_finding(self):
        self.assertNotIn(
            "bibliography_join",
            self._ids(
                "농촌진흥청. [발행연도 미상]. 「잎들깨 수경재배 요령」. 농사로. "
                "https://example.go.kr/detail?no=1941 (조회 2026-10-19).\n"))

    def test_edition_suffix_is_not_a_second_author(self):
        self.assertNotIn(
            "bibliography_join",
            self._ids(
                "한국농수산식품유통공사. 2026b. 「가락시장 깻잎 100속 "
                "경락가격」. KAMIS. https://www.kamis.co.kr "
                "(조회 2026-10-17).\n"))

    def test_real_multi_author_without_separator_still_fails(self):
        self.assertIn(
            "bibliography_join",
            self._ids("김정인 이명희. 2026. 「가락시장 깻잎」. KAMIS.\n"))

    def test_separator_and_year_inside_title_are_not_findings(self):
        self.assertNotIn(
            "bibliography_join",
            self._ids("김정인ㆍ이명희. 2026. 「가락시장 깻잎」. KAMIS.\n"))
        self.assertNotIn(
            "bibliography_join",
            self._ids("한국농촌경제연구원. 「2020년 농업전망」. 2026.\n"))


class PhotoGapTests(ContractCase):
    """G3-13①: 사진이 필요한 본문인데 자리가 0개인 상태."""

    def _ids(self, text):
        doc = runtime("gg_document")
        return [cid for cid, _ in doc.check(text, None,
                                            major_id="specialty_crops")]

    def test_site_heading_without_any_photo_is_reported(self):
        doc = runtime("gg_document")
        self.assertIn("photo_missing", doc.POLICY_ADVISORY_CHECKS)
        self.assertIn(
            "photo_missing",
            self._ids("# Ⅱ. 외부환경분석\n\n## 가. 농장 개요\n\n"
                      "예정지 전경과 진입로를 서술한다.\n"))

    def test_placeholder_or_inserted_figure_clears_the_gap(self):
        self.assertNotIn(
            "photo_missing",
            self._ids("# Ⅱ. 외부환경분석\n\n## 가. 농장 개요\n\n"
                      "[사진 필요: PHOTO-001 | 예정지 전경 | 원경 | "
                      "그림 2. 전경 | 80% | Ⅱ.1]\n"))
        self.assertNotIn(
            "photo_missing",
            self._ids("# Ⅱ. 외부환경분석\n\n## 가. 농장 개요\n\n"
                      "![밭](field.png)\n그림 1. 밭 전경\n출처: 본인 촬영\n"))

    def test_unrelated_body_is_not_reported(self):
        self.assertNotIn(
            "photo_missing",
            self._ids("# Ⅰ. 머리말\n\n창업 동기를 적는다.\n"))

    def test_figure_in_another_section_does_not_clear_the_gap(self):
        # 검토자 반례: 다른 절(기후)의 그림 하나가 예정지 사진 누락을 꺼서는
        # 안 된다. 사진이 필요한 절 안의 그림만 누락을 해소한다.
        self.assertIn(
            "photo_missing",
            self._ids(
                "# Ⅱ. 외부환경분석\n\n## 가. 농장 개요\n\n"
                "예정지 필지와 배수 여건을 설명한다.\n\n"
                "## 나. 기후\n\n![기온](temperature.png)\n"
                "그림 1. 월평균 기온\n출처: 기상청\n"))
