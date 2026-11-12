"""G: student input preservation through the deployed CLI and save APIs.

These live in root tests/ because it is the default validation discovery
root, independently of the separate skill-test gate change.
"""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from tests._harness import (
    ContractCase, SCRIPTS, bind_major, fact_op, runtime, source_op, write_text,
)


def cli(root, script, *args):
    script_args = ([args[0], str(root), *args[1:]] if script == "gg.py"
                   else [str(root), *args])
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *script_args],
        capture_output=True, text=True, encoding="utf-8", cwd=root)


build_hwpx = runtime("build_hwpx")


class DocxPreservationTests(ContractCase):
    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root)
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")

    def school_markdown(self, official=False):
        spec = {"writing_year": 2026, "start_year": 2027,
                "title": "합성 영농계획"}
        if official:
            spec["school_profile"] = {
                "mode": "school", "layout": "forms_1_to_4",
                "school": "합성대학교", "department": "특용작물학과"}
        return runtime("gg_school_paper").paper(spec, context=self.context)

    def build(self, output="build/paper.docx"):
        return cli(self.root, "build_docx.py", "--in", "body.md",
                   "--out", output, "--major", "specialty_crops")

    def test_legacy_prose_between_contents_and_body_is_refused_before_save(self):
        text = self.school_markdown().replace(
            "목차\n", "목차\n\n초록\n\n보존해야 하는 합성 초록 문단.\n", 1)
        write_text(self.root, "body.md", text)
        before = self._tree_bytes(self.root)
        proc = self.build()
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("blocked", json.loads(proc.stdout)["status"])
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_legacy_tables_and_unmatched_headings_are_not_silently_dropped(self):
        for content in (
            "|항목|값|\n|---|---|\n|합성 계획|보존 대상|",
            "# Ⅰ. 새로 쓴 머리말 제목",
        ):
            with self.subTest(content=content):
                text = self.school_markdown().replace(
                    "목차\n", "목차\n\n" + content + "\n\n", 1)
                write_text(self.root, "body.md", text)
                before = self._tree_bytes(self.root)
                proc = self.build()
                self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual("blocked", json.loads(proc.stdout)["status"])
                self.assertEqual(before, self._tree_bytes(self.root))

    def test_official_summary_prose_remains_in_the_saved_document(self):
        summary = "원문과 대조한 합성 요약 문단."
        text = self.school_markdown(official=True).replace(
            "목차\n", "목차\n요약\n" + summary + "\n표 목차\n", 1)
        write_text(self.root, "body.md", text)
        proc = self.build()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        with zipfile.ZipFile(self.root / "build/paper.docx") as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn(summary, xml)
        self.assertIn("Ⅰ. 머리말", xml)
        self.assertIn("목    차", xml)

    def test_existing_output_or_manifest_preserves_every_existing_byte(self):
        write_text(self.root, "body.md", "# Ⅰ. 머리말\n\n합성 본문.\n")
        for collision in ("output", "manifest", "both"):
            with self.subTest(collision=collision):
                out = self.root / ("build/" + collision + ".docx")
                manifest = out.with_suffix(".manifest.json")
                out.parent.mkdir(exist_ok=True)
                if collision in ("output", "both"):
                    out.write_bytes(b"existing student document\x00")
                if collision in ("manifest", "both"):
                    manifest.write_bytes(b"existing student manifest\x00")
                before = self._tree_bytes(self.root)
                proc = self.build(out.relative_to(self.root).as_posix())
                self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual("blocked", json.loads(proc.stdout)["status"])
                self.assertEqual(before, self._tree_bytes(self.root))

    def test_dangling_manifest_link_is_an_existing_path_not_an_empty_slot(self):
        write_text(self.root, "body.md", "# Ⅰ. 머리말\n\n합성 본문.\n")
        out = self.root / "build/paper.docx"
        out.parent.mkdir()
        manifest = out.with_suffix(".manifest.json")
        missing = self.root / "missing-manifest.json"
        manifest.symlink_to(missing)
        proc = self.build()
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertFalse(out.exists())
        self.assertTrue(manifest.is_symlink())
        self.assertFalse(missing.exists())


class MajorNoticeIsolationTests(ContractCase):
    """F1: 식량작물 안내문·사람 확인 항목이 다른 전공 산출물로 새지 않는다.

    1판 문자열을 그대로 못 박는다. 공용 상수를 고치면 다른 전공 매니페스트가
    같이 움직이므로, 여기서는 스크립트 상수를 인용하지 않고 원문을 적는다.
    """

    # 1판 build_docx 매니페스트 notice.
    DOCX_NOTICE = (
        "저수준 미검증 출력. 목차 필드 갱신·전체 페이지·학교 원문·학명·그림·표·"
        "머리행 검토 필요. 한글에서 글꼴 신명조·여백(위20/아래15/머리15/꼬리15/"
        "좌30/우30/제본0 mm)·페이지 번호를 확인한다. 이 세 가지는 스킬 작성 완료를 "
        "막지 않으며 자동 통과하지 않는다. file_hash는 빌드 직후 파일 기준이다. "
        "쪽 나눔·여백 등 후처리로 파일을 다시 저장했다면 --rehash로 매니페스트 "
        "해시를 갱신한다."
    )
    # 1판 build_hwpx notice(verify=True).
    HWPX_NOTICE = (
        "저수준 미검증 출력. kordoc validate(구조)와 HWPX 재파싱 표 수치 대조를 "
        "통과했다. 한글 프로그램에서 열어 (1) 글꼴 신명조 (2) 여백 "
        "위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm (3) 쪽번호를 확인한다. "
        "표지·제출면·인준서의 쪽 나눔·가운데 정렬과 목차의 실제 쪽번호는 사람이 "
        "마무리한다. 이 항목들은 자동 통과하지 않는다."
    )
    # 1판 HUMAN_FINISH 5개.
    HUMAN_FINISH = (
        "표지·제출면·인준서의 쪽 나눔과 가운데 정렬",
        "목차의 실제 쪽번호",
        "표·그림 목차(있으면)",
        "표 폭이 본문 폭(150 mm)인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
        "장(Ⅰ~Ⅵ) 시작이 새 쪽인지 확인 — 자동으로 쪽 나눔을 넣었지만 한글에서 확인한다",
    )
    FOOD_CROPS_FINISH = (
        "요약이 Ⅰ부터, 본문이 1부터인지 확인 — 자동으로 맞췄지만 한글에서 확인한다",
    )
    FRONT_MATTER_MD = ("겉표지\n\n표제면\n\n제출서\n\n인준서\n\n요약\n\n감사의 글\n\n"
                       "목차\n\n1. 머리말\n")

    def build(self, major):
        root = self.make_project()
        bind_major(root, major)
        write_text(root, "body.md", "# 1. 머리말\n\n합성 본문.\n")
        out = "build/" + major + ".docx"
        proc = cli(root, "build_docx.py", "--in", "body.md",
                   "--out", out, "--major", major)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return json.loads((root / out).with_suffix(".manifest.json")
                          .read_text(encoding="utf-8"))

    def test_food_crops_notice_does_not_leak_to_other_majors(self):
        for major in ("specialty_crops", "fruit_trees", "hort_env_systems"):
            with self.subTest(major=major, surface="docx-manifest"):
                self.assertEqual(self.DOCX_NOTICE, self.build(major)["notice"])
            with self.subTest(major=major, surface="hwpx-notice"):
                self.assertEqual(self.HWPX_NOTICE, build_hwpx.notice(True, major))
            with self.subTest(major=major, surface="hwpx-human-finish"):
                summary = build_hwpx.school_frontmatter_summary(
                    self.FRONT_MATTER_MD, major)
                self.assertEqual(list(self.HUMAN_FINISH),
                                 summary["human_finish_required"])
        # 산업곤충은 앞머리 계획이 없어 요약이 None이다 — 그래도 안내문은 1판 그대로.
        self.assertEqual(self.HWPX_NOTICE, build_hwpx.notice(True, "industrial_insects"))
        self.assertIsNone(build_hwpx.school_frontmatter_summary(
            self.FRONT_MATTER_MD, "industrial_insects"))

        # 식량작물만 새 문구를 고르고, 1판 여백 문구는 남지 않는다.
        self.assertIn("위30/아래30/머리0/꼬리10", self.build("food_crops")["notice"])
        food_crops_notice = build_hwpx.notice(True, "food_crops")
        self.assertIn("요약은 Ⅰ부터, 본문은 1부터", food_crops_notice)
        self.assertNotIn("위20/아래15", food_crops_notice)
        self.assertNotIn("위20/아래15", self.build("food_crops")["notice"])
        summary = build_hwpx.school_frontmatter_summary(
            self.FRONT_MATTER_MD, "food_crops")
        self.assertEqual(list(self.HUMAN_FINISH) + list(self.FOOD_CROPS_FINISH),
                         summary["human_finish_required"])


class FinanceSavePreservationTests(ContractCase):
    SPEC = {
        "profile": "single_annual_cash_v1", "crops": ["합성작목"],
        "accounting_basis": "cash_pre_tax_no_inventory",
        "source_refs": ["synthetic:보존시험"], "unit": "원", "quantity_unit": "kg",
        "land": "10000000", "facility": "20000000", "equity": "25000000",
        "loan": "10000000", "loan_rate": "0.02", "discount_rate": "0.05",
        "salvage": "2000000", "start_year": 2027, "years": 2, "life": 2,
        "grace": 0, "term": 2, "repayment": "equal_principal",
        "investment_basis": "school_farm_new_business", "owner_labor_in_costs": False,
        "periods": [
            {"year": 2027, "quantity": "1000", "sold": "1000", "loss": "0",
             "price": "15000", "variable_cost": "3000", "fixed_cost": "2000000",
             "household": "3000000"},
            {"year": 2028, "quantity": "1200", "sold": "1200", "loss": "0",
             "price": "15000", "variable_cost": "3000", "fixed_cost": "2000000",
             "household": "3000000"},
        ],
    }

    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root)
        self.finance = runtime("gg_finance")
        self.context = runtime("gg_major_contract").output_context(
            self.root, "specialty_crops")

    def save(self, out):
        return self.finance.workbook(copy.deepcopy(self.SPEC), out, context=self.context)

    def test_orphan_manifest_blocks_api_and_cli_without_touching_the_tree(self):
        out = self.root / "build/finance.xlsx"
        out.parent.mkdir()
        out.with_suffix(".manifest.json").write_bytes(b"student manifest\x00")
        write_text(self.root, "spec.json", json.dumps(self.SPEC, ensure_ascii=False))
        before = self._tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self.save(out)
        self.assertEqual(before, self._tree_bytes(self.root))
        proc = cli(self.root, "build_excel_finance_template.py", "--input", "spec.json",
                   "--out", "build/finance.xlsx", "--major", "specialty_crops")
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("blocked", json.loads(proc.stdout)["status"])
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_first_save_binds_the_manifest_then_refuses_to_replace_the_pair(self):
        out = self.root / "build/finance.xlsx"
        self.assertEqual("calculated", self.save(out)["status"])
        manifest = json.loads(out.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime("gg_core").digest(out.read_bytes()), manifest["file_hash"])
        before = self._tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self.save(out)
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_existing_workbook_alone_is_preserved_without_creating_a_manifest(self):
        out = self.root / "build/finance.xlsx"
        out.parent.mkdir()
        out.write_bytes(b"student workbook without a manifest")
        before = self._tree_bytes(self.root)
        with self.assertRaises(ValueError):
            self.save(out)
        self.assertEqual(before, self._tree_bytes(self.root))

    def test_dangling_manifest_link_is_not_overwritten(self):
        out = self.root / "build/finance.xlsx"
        out.parent.mkdir()
        manifest = out.with_suffix(".manifest.json")
        missing = self.root / "missing-manifest.json"
        manifest.symlink_to(missing)
        with self.assertRaises(ValueError):
            self.save(out)
        self.assertFalse(out.exists())
        self.assertTrue(manifest.is_symlink())
        self.assertFalse(missing.exists())


class MalformedCheckStateTests(ContractCase):
    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root)
        self.core = runtime("gg_core")
        write_text(self.root, "answer.txt", "합성 원답변\n")
        fact = fact_op("answer", "문체", "학부생 화법", None, claim_id="answer")
        claim = {k: fact["value"][k] for k in (
            "field_id", "value", "unit", "period", "scope", "answer_state", "kind")}
        self.core.apply(self.root, {"request_id": "preservation-input", "ops": [
            source_op(claims={"answer": claim}), fact]}, self.core.load(self.root)["revision"])
        self.valid = self.core.load(self.root)

    def persist(self, project):
        write_text(self.root, "project.json", json.dumps(project, ensure_ascii=False))

    def check_cli(self, *args):
        before = self._tree_bytes(self.root)
        proc = cli(self.root, "gg.py", *args)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(before, self._tree_bytes(self.root))
        return proc, payload

    def test_non_object_claims_and_views_return_blocked_check_rows(self):
        for bad in ([], None, "oops", 0):
            for fields in (("claims",), ("views",), ("claims", "views")):
                with self.subTest(bad=bad, fields=fields):
                    project = copy.deepcopy(self.valid)
                    expected = set()
                    if "claims" in fields:
                        project["sources"]["answer"]["claims"] = bad
                        expected.add("source_claims_invalid")
                    if "views" in fields:
                        project["views"] = bad
                        expected.add("view_invalid")
                    self.persist(project)
                    proc, rows = self.check_cli("check")
                    self.assertEqual(1, proc.returncode)
                    blocked = {r["check_id"] for r in rows
                               if r["status"] == "blocked" and r["severity"] == "error"}
                    self.assertTrue(expected <= blocked)

    def test_non_object_members_also_block_instead_of_crashing(self):
        project = copy.deepcopy(self.valid)
        project["sources"]["answer"]["claims"] = {"answer": []}
        project["views"] = {"view.md": []}
        self.persist(project)
        proc, rows = self.check_cli("check")
        self.assertEqual(1, proc.returncode)
        self.assertTrue({"source_claims_invalid", "view_invalid"} <=
                        {r["check_id"] for r in rows if r["status"] == "blocked"})

    def test_status_with_a_malformed_finance_gate_source_is_still_structured(self):
        project = copy.deepcopy(self.valid)
        project["facts"]["answer"]["field_id"] = self.core.FINANCE_GATE_STATE
        project["facts"]["answer"]["verification"] = "claim_supported"
        project["sources"]["answer"]["claims"] = []
        self.persist(project)
        proc, payload = self.check_cli("status")
        self.assertEqual(0, proc.returncode)
        self.assertIn("source_claims_invalid", {r["check_id"] for r in payload["checks"]})
        self.assertFalse(payload["skill_ready"])

    def test_valid_records_still_report_an_edited_view(self):
        project = copy.deepcopy(self.valid)
        project["views"] = {"view.md": {"hash": self.core.digest(b"registered view")}}
        write_text(self.root, "view.md", "manually edited view")
        self.persist(project)
        _, rows = self.check_cli("check")
        self.assertIn("view_edited", {r["check_id"] for r in rows})
        self.assertFalse({"source_claims_invalid", "view_invalid", "claim_mismatch"} &
                         {r["check_id"] for r in rows})


def _retired_ledger_write_instructions(text):
    """Find new write destinations, storage-table targets and authoring forms.

    A filename in a read-only preservation note is not a new write instruction.
    """
    retired = r"00_interview\.md"
    writes = r"(?:기록|저장|작성|추가|갱신)(?:한다|하고|하라|하세요|해|할|해야)"
    findings = []
    for block in re.split(r"\n\s*\n", text):
        plain = block.replace("`", "")
        if not re.search(retired, plain):
            continue
        lines = plain.splitlines()
        if (lines[0].lstrip().startswith("|")
                and re.search(r"저장\s*(?:위치|대상)|정본\s*기록", lines[0])):
            findings.extend(line for line in lines if re.search(retired, line))
        findings.extend(re.findall(
            rf"(?m)^#{{1,6}}\s+[^\n]*{retired}\s+(?:작성\s*)?서식[^\n]*",
            plain))
        paragraph = " ".join(plain.split())
        findings.extend(re.findall(
            rf"{retired}\s*(?:파일\s*)?(?:에|으로|로)\s*[^.!?]*?{writes}",
            paragraph))
    return findings


class PolishingCanonicalExampleTests(ContractCase):
    def _checked_guidance(self):
        texts = {
            name: (SCRIPTS.parent / "references" / name).read_text(encoding="utf-8")
            for name in ("korean-polishing.md", "interview-guide.md")
        }
        for name, text in texts.items():
            self.assertEqual([], _retired_ledger_write_instructions(text),
                             f"{name}: retired ledger is a new write target")
        return texts["korean-polishing.md"]

    def test_documented_style_choice_survives_a_canonical_roundtrip_without_a_legacy_ledger(self):
        guidance = self._checked_guidance()
        answer_match = re.search(r"```text\n(.*?)```", guidance, re.S)
        change_match = re.search(r"```json\n(.*?)```", guidance, re.S)
        self.assertIsNotNone(answer_match, "polishing guidance needs an executable source example")
        self.assertIsNotNone(change_match, "polishing guidance needs an executable canonical change")
        answer = answer_match.group(1)
        change = json.loads(change_match.group(1))
        root = self.make_project()
        core = runtime("gg_core")
        source_path = write_text(root, "interview/style-answer.txt", answer)
        source = source_op("interview/style-answer.txt")
        source["value"]["id"] = "style-answer"
        core.apply(root, {"request_id": "style-source", "ops": [source]}, 0)
        change_path = write_text(root, ".gg-work/style-choice.json", json.dumps(change, ensure_ascii=False))
        proc = cli(root, "gg.py", "apply", "--change", str(change_path), "--expected-revision", "1")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        project = core.load(root)
        choice = project["facts"]["writing-style"]
        self.assertEqual("학부생 화법", choice["value"])
        self.assertEqual("source_located", choice["verification"])
        self.assertEqual("style-answer", choice["source_refs"][0]["id"])
        self.assertEqual(answer, source_path.read_text(encoding="utf-8"))
        self.assertFalse((root / "00_interview.md").exists())
        self.assertIn("claim_missing", {r["check_id"] for r in core.checks(root, project)})
