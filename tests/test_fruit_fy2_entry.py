"""과수 FY2 — 과수 전용 정정 진입점 하나를 잠근다.

정정은 학교 원본을 고치지 않는다. clear/fill을 마친 사본에서 `9.경비계획`
21칸만 바꾸고, 그 사본은 `fruit_16_sheet_v1_fy1` 지문으로만 통과한다. 시험은
여덟을 잠근다: 정정 명세 21칸·해시 동결, 참조 세트의 정정 프로필 연결, 공개
`patch_copy`가 넓어지지 않았음, 진입점의 명세 경로 미수용, 입력·출력 지문의
상호 배제, 셀별 원문 거부와 정본 무변경, 영수증의 21칸·명세 해시 기록, 그리고
fill→정정 사슬에서만 허용되는 프로필 전환(위조 영수증 거부).

과수 실제 원본은 저장소에 없다. 아래 합성 16시트 통합문서는 정정 명세의
수식 21개만 옮겨 담고, 참조 세트는 시험 안에서만 그 지문으로 바꾼다.
Excel 실행이 아니며 실제 재계산은 저장소 밖에서 한다.
"""
import hashlib
import inspect
import json
import tempfile
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook

from tests._harness import ContractCase, REPO_ROOT, bind_major, runtime


REF = REPO_ROOT / "skills/knuaf-doc/references/fruit-trees"
SPEC_PATH = REF / "corrections/fy1-spec.json"
MAP_PATH = REF / "fruit-finance-fact-map.fy1.json"
BASE_MAP_PATH = REF / "fruit-finance-fact-map.json"
PROFILE = "fruit_16_sheet_v1_fy1"
BASE_PROFILE = "fruit_16_sheet_v1"
SHEET = "9.경비계획"

# 셀, 바꾸기 전(수식 또는 값), 바꾼 뒤(수식). J열은 감가상각 참조와 U6/U7이
# 블록마다 다르므로 21칸을 그대로 적는다.
CELLS = (
    ("B12", 2025, "='12.손익계획'!C4"),
    ("J15", "=SUM('10.감가상각'!L7)*$U$6*(1+$U$10)^(B12-2020)",
     "=SUM('10.감가상각'!L7)*$U$6*(1+$U$10)^(B12-$B$12)"),
    ("J16", "=SUM('10.감가상각'!H7)*$U$7*(1+$U$10)^(B12-2020)",
     "=SUM('10.감가상각'!H7)*$U$7*(1+$U$10)^(B12-$B$12)"),
    ("J34", "=SUM('10.감가상각'!L18)*$U$6*(1+$U$10)^(B31-2020)",
     "=SUM('10.감가상각'!L18)*$U$6*(1+$U$10)^(B31-$B$12)"),
    ("J35", "=SUM('10.감가상각'!H18)*$U$7*(1+$U$10)^(B31-2020)",
     "=SUM('10.감가상각'!H18)*$U$7*(1+$U$10)^(B31-$B$12)"),
    ("J53", "=SUM('10.감가상각'!L29)*$U$6*(1+$U$10)^(B50-2020)",
     "=SUM('10.감가상각'!L29)*$U$6*(1+$U$10)^(B50-$B$12)"),
    ("J54", "=SUM('10.감가상각'!H29)*$U$7*(1+$U$10)^(B50-2020)",
     "=SUM('10.감가상각'!H29)*$U$7*(1+$U$10)^(B50-$B$12)"),
    ("J72", "=SUM('10.감가상각'!L40)*$U$6*(1+$U$10)^(B69-2020)",
     "=SUM('10.감가상각'!L40)*$U$6*(1+$U$10)^(B69-$B$12)"),
    ("J73", "=SUM('10.감가상각'!H40)*$U$7*(1+$U$10)^(B69-2020)",
     "=SUM('10.감가상각'!H40)*$U$7*(1+$U$10)^(B69-$B$12)"),
    ("J91", "=SUM('10.감가상각'!L51)*$U$6*(1+$U$10)^(B88-2020)",
     "=SUM('10.감가상각'!L51)*$U$6*(1+$U$10)^(B88-$B$12)"),
    ("J92", "=SUM('10.감가상각'!H51)*$U$7*(1+$U$10)^(B88-2020)",
     "=SUM('10.감가상각'!H51)*$U$7*(1+$U$10)^(B88-$B$12)"),
    ("J110", "=SUM('10.감가상각'!L62)*$U$6*(1+$U$10)^(B107-2020)",
     "=SUM('10.감가상각'!L62)*$U$6*(1+$U$10)^(B107-$B$12)"),
    ("J111", "=SUM('10.감가상각'!H62)*$U$7*(1+$U$10)^(B107-2020)",
     "=SUM('10.감가상각'!H62)*$U$7*(1+$U$10)^(B107-$B$12)"),
    ("J129", "=SUM('10.감가상각'!L73)*$U$6*(1+$U$10)^(B126-2020)",
     "=SUM('10.감가상각'!L73)*$U$6*(1+$U$10)^(B126-$B$12)"),
    ("J130", "=SUM('10.감가상각'!H73)*$U$7*(1+$U$10)^(B126-2020)",
     "=SUM('10.감가상각'!H73)*$U$7*(1+$U$10)^(B126-$B$12)"),
    ("J148", "=SUM('10.감가상각'!L84)*$U$6*(1+$U$10)^(B145-2020)",
     "=SUM('10.감가상각'!L84)*$U$6*(1+$U$10)^(B145-$B$12)"),
    ("J149", "=SUM('10.감가상각'!H84)*$U$7*(1+$U$10)^(B145-2020)",
     "=SUM('10.감가상각'!H84)*$U$7*(1+$U$10)^(B145-$B$12)"),
    ("J167", "=SUM('10.감가상각'!L95)*$U$6*(1+$U$10)^(B164-2020)",
     "=SUM('10.감가상각'!L95)*$U$6*(1+$U$10)^(B164-$B$12)"),
    ("J168", "=SUM('10.감가상각'!H95)*$U$7*(1+$U$10)^(B164-2020)",
     "=SUM('10.감가상각'!H95)*$U$7*(1+$U$10)^(B164-$B$12)"),
    ("J186", "=SUM('10.감가상각'!L106)*$U$6*(1+$U$10)^(B183-2020)",
     "=SUM('10.감가상각'!L106)*$U$6*(1+$U$10)^(B183-$B$12)"),
    ("J187", "=SUM('10.감가상각'!H106)*$U$7*(1+$U$10)^(B183-2020)",
     "=SUM('10.감가상각'!H106)*$U$7*(1+$U$10)^(B183-$B$12)"),
)

REPORTED_SPEC_SHA = (
    "5e55ad3b5bbc9c8d57307156bd9ca56937517c7410f6ae735fae5ac1770f0c44")
BASE_SHA = "5f2896dd0c47d9b90a1ec3da30128ea0a9aa47f72aa79156a57bfd7bfd271374"
BASE_SHEET_FINGERPRINT = (
    "24f8af4c953793510fb7245c5dedec090e94508620a8bd3fb93e447ade6be2a9")
CORRECTED_SHEET_FINGERPRINT = (
    "88f8aaa8e400de4006801bbdd6a45aae87bfeb6207261971a3e10c64b58d98fd")
HELD_SPANS = {
    "9.경비계획!J15:J16", "9.경비계획!J34:J35", "9.경비계획!J53:J54",
    "9.경비계획!J72:J73", "9.경비계획!J91:J92", "9.경비계획!J110:J111",
    "9.경비계획!J129:J130", "9.경비계획!J148:J149",
    "9.경비계획!J167:J168", "9.경비계획!J186:J187"}


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fingerprints(book):
    return {sheet.title: hashlib.sha256(json.dumps(sorted(
        (cell.coordinate, cell.value) for row in sheet for cell in row
        if cell.data_type == "f"), ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest() for sheet in book}


class SpecTests(ContractCase):
    def setUp(self):
        self.spec = _json(SPEC_PATH)
        self.school = runtime("gg_school_excel")
        self.registry = _json(self.school.WORKBOOK_REFERENCE_SET)

    def _entry(self, profile):
        return next(row for row in self.registry["workbook_profiles"]
                    if row["profile"] == profile)

    def test_the_spec_is_the_pinned_one_and_holds_the_twenty_one_cells(self):
        self.assertEqual(
            hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest(), REPORTED_SPEC_SHA)
        shipped = {row["cell"]: row for row in self.spec["patches"]}
        self.assertEqual(len(self.spec["patches"]), len(CELLS))
        self.assertEqual(set(shipped), {cell for cell, _, _ in CELLS})
        self.assertEqual([row["cell"] for row in self.spec["patches"]],
                         [cell for cell, _, _ in CELLS])
        for cell, before, after in CELLS:
            with self.subTest(cell=cell):
                row = shipped[cell]
                self.assertEqual(row["new_formula"], after)
                if isinstance(before, int):
                    self.assertEqual(row["class"], "value_to_formula")
                    self.assertEqual(row["expected_value"], before)
                    self.assertNotIn("expected_formula", row)
                else:
                    self.assertEqual(row["class"], "formula_repoint")
                    self.assertEqual(row["expected_formula"], before)
                    self.assertNotIn("expected_value", row)
                    self.assertNotIn("2020", after)
                    self.assertIn("$B$12", after)

    def test_the_spec_names_its_base_workbook_and_profile_pair(self):
        self.assertEqual(self.spec["schema"], "gg-xlsx-formula-patch-map/v1")
        self.assertEqual(self.spec["workbook_profile"], PROFILE)
        self.assertEqual(self.spec["base_profile"], BASE_PROFILE)
        self.assertEqual(self.spec["source"]["binding"], "cells")
        self.assertEqual(self.spec["source"]["sha256"], BASE_SHA)
        for row in self.spec["patches"]:
            self.assertEqual(row["sheet"], SHEET)
            self.assertTrue(row["reason"].strip())
            self.assertTrue(row["evidence_ref"]["locator"].strip())

    def test_the_reference_set_links_the_correction_to_the_original_form(self):
        base, corrected = self._entry(BASE_PROFILE), self._entry(PROFILE)
        self.assertEqual(corrected["base_profile"], BASE_PROFILE)
        self.assertEqual(corrected["sha256"], base["sha256"])
        self.assertEqual(corrected["sheets"], base["sheets"])
        self.assertEqual(corrected["correction_spec"], {
            "path": "references/fruit-trees/corrections/fy1-spec.json",
            "sha256": REPORTED_SPEC_SHA,
            "cells": len(CELLS)})
        differing = {name for name in base["formula_fingerprints"]
                     if base["formula_fingerprints"][name]
                     != corrected["formula_fingerprints"][name]}
        self.assertEqual(differing, {SHEET})
        self.assertEqual(base["formula_fingerprints"][SHEET],
                         BASE_SHEET_FINGERPRINT)
        self.assertEqual(corrected["formula_fingerprints"][SHEET],
                         CORRECTED_SHEET_FINGERPRINT)
        self.assertEqual(base["sha256"], BASE_SHA)

    def test_the_contract_declares_the_corrected_profile_after_its_base(self):
        mc = runtime("gg_major_contract")
        module = mc.default_registry().resolve("fruit_trees")
        authorization = mc.OutputAuthorization(
            output=mc.OUTPUT_SCHOOL_WORKBOOK, major_id="fruit_trees",
            module_version=module.module_version,
            contract_version=mc.CONTRACT_VERSION, project_revision=1,
            binding_evidence="synthetic")
        self.assertEqual(mc.workbook_profiles_for(authorization),
                         (BASE_PROFILE, PROFILE))
        supported = {cap.profile for cap in module.finance_capabilities
                     if cap.status == "supported"}
        self.assertEqual(supported, {BASE_PROFILE, PROFILE})
        # 원본 양식의 선언은 그대로 남는다(정정은 새 프로필로만 추가).
        self.assertEqual(module.workbook_profiles[0], BASE_PROFILE)

    def test_the_public_patch_entry_cannot_carry_a_fruit_profile(self):
        fp = runtime("gg_excel_formula_patch")
        mc = runtime("gg_major_contract")
        # The public entry keeps the 17-sheet default for a major whose own
        # form is not the specialty 17-sheet one; fruit uses its own module.
        root = self.make_project()
        bind_major(root, "fruit_trees")
        with self.assertRaisesRegex(mc.OutputHeldError,
                                    "unsupported_finance_profile"):
            fp.patch_copy(SPEC_PATH, SPEC_PATH,
                          Path(tempfile.gettempdir()) / "fy2-not-written.xlsx",
                          context=mc.output_context(root, "fruit_trees"))

    def test_the_entry_point_takes_no_spec_path(self):
        module = runtime("gg_fruit_correction")
        parameters = list(inspect.signature(module.apply_fy1).parameters)
        self.assertEqual(parameters[:3], ["source", "out", "receipt"])
        self.assertNotIn("map_path", parameters)
        self.assertNotIn("spec", parameters)
        self.assertEqual(module.SPEC_SHA256, REPORTED_SPEC_SHA)
        self.assertEqual(module.SPEC_PATH.resolve(), SPEC_PATH.resolve())
        self.assertEqual(module.BASE_SHA256, BASE_SHA)
        self.assertEqual(module.CELL_COUNT, len(CELLS))


class FactMapTests(ContractCase):
    def setUp(self):
        self.map = _json(MAP_PATH)
        self.base = _json(BASE_MAP_PATH)

    def test_the_corrected_map_releases_every_held_row_and_nothing_else(self):
        self.assertEqual(self.map["workbook_profile"], PROFILE)
        self.assertEqual(self.map["base_profile"], BASE_PROFILE)
        self.assertEqual(self.map["schema"], "gg-xlsx-fact-map/v1")
        self.assertEqual(self.map["held_cells"], [])
        base_rows = {row["fact_id"]: row for row in self.base["cells"]}
        held = {row["fact_id"]: row for row in self.base["held_cells"]}
        shipped = {row["fact_id"]: row for row in self.map["cells"]}
        self.assertEqual(len(shipped), 70)
        self.assertEqual(len(base_rows), 10)
        self.assertEqual(len(held), 60)
        # 이미 등록 경로에 있던 10행은 한 글자도 바뀌지 않는다.
        for fact_id, row in base_rows.items():
            self.assertEqual(shipped[fact_id], row)
        # 해제된 60행은 주소·기간·단위가 같고 보류 사유만 사라진다.
        for fact_id, row in held.items():
            released = shipped[fact_id]
            for key in ("field_id", "sheet", "cell", "period", "scope",
                        "unit", "body_label"):
                self.assertEqual(released[key], row[key], key)
            self.assertNotIn("held_by", released)
            self.assertNotIn("reason", released)
        self.assertEqual(set(shipped), set(base_rows) | set(held))

    def test_every_released_row_is_held_by_the_corrected_spans_only(self):
        corrected = {SHEET + "!" + cell for cell, _, _ in CELLS[1:]}
        self.assertEqual(len(corrected), 20)
        spans = {tuple(row["held_by"]) for row in self.base["held_cells"]}
        self.assertEqual({s for span in spans for s in span}, HELD_SPANS)
        for span in HELD_SPANS:
            sheet, cells = span.split("!")
            first, last = cells.split(":")
            self.assertEqual(sheet, SHEET)
            self.assertIn(sheet + "!" + first, corrected)
            self.assertIn(sheet + "!" + last, corrected)
        # 행마다: 보류 사유가 정정된 20칸으로만 풀리는지, 다른 시트·다른 사유가
        # 섞여 있지 않은지 확인한다. 독립 사유가 하나라도 남으면 그 행은
        # 계속 보류여야 한다(여기서는 전부 경비계획 물가지수뿐임을 고정).
        for row in self.base["held_cells"]:
            with self.subTest(fact_id=row["fact_id"]):
                self.assertTrue(row["held_by"])
                for token in row["held_by"]:
                    sheet, cells = token.split("!")
                    self.assertEqual(sheet, SHEET)
                    first, last = cells.split(":")
                    column = first[0]
                    self.assertEqual(last[0], column)
                    for number in range(int(first[1:]), int(last[1:]) + 1):
                        self.assertIn(f"{sheet}!{column}{number}", corrected)
                # 사유 문구도 2020 고정 지수 하나만 말해야 한다.
                self.assertIn("2020", row["reason"])
                for other in ("13.대차대조", "11.생산원가", "5.판매계획"):
                    self.assertNotIn(other, row["reason"])
                self.assertEqual(row["reason"].count("종속"), 1)

    def test_only_the_two_unrelated_exclusions_remain(self):
        remaining = [("13.대차대조", "C4:M35"), ("11.생산원가", "C36:L36")]
        self.assertEqual(
            [(row["sheet"], row["range"]) for row in self.map["excluded_series"]],
            remaining)
        # 원본 지도에서는 경비계획 10쌍도 제외 구간이었다. 정정이 그 10쌍을
        # 바꾸므로 fy1 지도는 그 구간만 해제하고 나머지 둘은 그대로 둔다.
        base = [(row["sheet"], row["range"]) for row in self.base["excluded_series"]]
        self.assertEqual(set(base) - set(remaining), {
            (SHEET, "J15:J16"), (SHEET, "J34:J35"), (SHEET, "J53:J54"),
            (SHEET, "J72:J73"), (SHEET, "J91:J92"), (SHEET, "J110:J111"),
            (SHEET, "J129:J130"), (SHEET, "J148:J149"),
            (SHEET, "J167:J168"), (SHEET, "J186:J187")})
        self.assertEqual(len(base) - len(remaining), 10)

    def test_the_student_notice_keeps_the_coefficients_and_marks_the_axis(self):
        corrections = self.map["corrections"]
        self.assertEqual(corrections["cells"], len(CELLS))
        self.assertEqual(corrections["spec"]["sha256"], REPORTED_SPEC_SHA)
        self.assertEqual(corrections["spec"]["path"],
                         "references/fruit-trees/corrections/fy1-spec.json")
        joined = " ".join(corrections["notice"])
        # 첫해에 1이 되는 것은 물가 상승률 계수의 지수 효과뿐이다.
        self.assertIn("$U$6", joined)
        self.assertIn("$U$7", joined)
        self.assertIn("그대로 곱해집니다", joined)
        for wrong in ("쓰이지 않", "쓰지 않", "효과가 없다"):
            self.assertNotIn(wrong, joined)
        self.assertIn("(1+U10)^0", corrections["applies"])
        review = (REF / "finance-map-review.md").read_text(encoding="utf-8")
        self.assertIn("추정", review)
        self.assertIn("$U$6", review)
        self.assertIn("$U$7", review)


class _SyntheticFruit(ContractCase):
    """정정 명세의 수식 21개만 담은 합성 16시트 통합문서(Excel 아님)."""

    def setUp(self):
        self.school = runtime("gg_school_excel")
        self.mc = runtime("gg_major_contract")
        self.entry = runtime("gg_fruit_correction")
        self.spec = _json(SPEC_PATH)
        tmp = tempfile.TemporaryDirectory(prefix="fy2-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.make_project()
        bind_major(self.root, "fruit_trees")
        self.context = self.mc.output_context(self.root, "fruit_trees")
        self.names = self.school.profile_structure(BASE_PROFILE)["sheets"]
        # 같은 21칸을 독립으로 두 번 만든다: 정정 전(원본 모양)과 정정 후.
        # A1/B1은 fill이 값을 바꿀 자리이고 정정 대상이 아니다.
        self.before_path = self.tmp / "before.xlsx"
        self.after_path = self.tmp / "after.xlsx"
        self._write(self.before_path, {cell: value for cell, value, _ in CELLS})
        self._write(self.after_path, {cell: value for cell, _, value in CELLS})
        self._register()

    def _write(self, path, formulas):
        book = Workbook()
        book.remove(book.active)
        for name in self.names:
            book.create_sheet(name)
        book["1.재무상태"]["A1"] = 1
        book["1.재무상태"]["B1"] = 2
        for cell, value in formulas.items():
            book[SHEET][cell] = value
        book.save(path)
        book.close()

    def _register(self):
        """원본 프로필은 '정정 전' 지문, fy1은 '정정 후' 지문으로 둔다."""
        base = load_workbook(self.before_path)
        corrected = load_workbook(self.after_path)
        data = _json(self.school.WORKBOOK_REFERENCE_SET)
        base_entry = next(row for row in data["workbook_profiles"]
                          if row["profile"] == BASE_PROFILE)
        profile_entry = next(row for row in data["workbook_profiles"]
                             if row["profile"] == PROFILE)
        base_entry["formula_fingerprints"] = _fingerprints(base)
        profile_entry["formula_fingerprints"] = _fingerprints(corrected)
        base.close()
        corrected.close()
        self.registry_path = self.tmp / "reference.json"
        self.registry_path.write_text(json.dumps(data), encoding="utf-8")
        patcher = mock.patch.object(self.school, "WORKBOOK_REFERENCE_SET",
                                    self.registry_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def corrected(self, out_name="corrected.xlsx", receipt_name="receipt.json"):
        out = self.tmp / out_name
        receipt = self.tmp / receipt_name
        result = self.entry.apply_fy1(self.before_path, out, receipt,
                                      context=self.context)
        return out, receipt, result


class EntryTests(_SyntheticFruit):
    def test_a_corrected_copy_carries_every_new_formula_and_both_profiles(self):
        before = self.before_path.read_bytes()
        out, receipt_path, receipt = self.corrected()
        self.assertEqual(len(receipt["patched"]), len(CELLS))
        self.assertEqual(receipt["workbook_profile"], PROFILE)
        self.assertEqual(receipt["base_profile"], BASE_PROFILE)
        self.assertEqual(receipt["output"]["status"], "patched")
        self.assertEqual(self.before_path.read_bytes(), before)
        self.assertEqual(_json(receipt_path), receipt)
        book = load_workbook(out)
        self.addCleanup(book.close)
        self.assertEqual(book[SHEET]["B12"].value, "='12.손익계획'!C4")
        for cell, _, after in CELLS[1:]:
            self.assertEqual(book[SHEET][cell].value, after)

    def test_the_receipt_records_the_registered_spec_and_all_twenty_one_cells(self):
        _, _, receipt = self.corrected()
        self.assertEqual(receipt["corrections"]["spec"], {
            "path": "references/fruit-trees/corrections/fy1-spec.json",
            "sha256": REPORTED_SPEC_SHA})
        self.assertEqual(receipt["corrections"]["cells"], len(CELLS))
        self.assertEqual(
            [(row["sheet"], row["cell"])
             for row in receipt["corrections"]["patched"]],
            [(SHEET, cell) for cell, _, _ in CELLS])
        # 엔진 입력부(`map`)는 해시로 묶인 실물 명세 파일을 가리킨다.
        self.assertEqual(receipt["map"], {
            "path": str(SPEC_PATH.resolve()), "sha256": REPORTED_SPEC_SHA})
        self.assertEqual(
            {(row["sheet"], row["cell"], row["new_formula"])
             for row in receipt["patched"]},
            {(SHEET, cell, after.lstrip("=")) for cell, _, after in CELLS})
        operations = [row["operation"] for row in receipt["patched"]]
        self.assertEqual(operations.count("value_to_formula"), 1)
        self.assertEqual(operations.count("formula_replace"), 20)

    def test_the_corrected_copy_passes_only_the_corrected_fingerprint(self):
        out, _, _ = self.corrected()
        self.school.require_profile_structure(out, PROFILE)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.school.require_profile_structure(out, BASE_PROFILE)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.school.require_profile_structure(self.before_path, PROFILE)
        self.school.require_profile_structure(self.before_path, BASE_PROFILE)

    def test_one_wrong_cell_is_refused_and_no_output_or_receipt_survives(self):
        # 수식 한 칸이 어긋나면 시트 지문이, 값 칸(B12)이 어긋나면 셀별 원문
        # 대조가 먼저 거부한다(지문은 값을 보지 않는다). 둘 다 산출물 없음.
        before = self.before_path.read_bytes()
        for name, mutate, replacement, expected in (
            ("formula", "J34",
             "=SUM('10.감가상각'!L18)*$U$6*(1+$U$10)^(B31-2021)", "fingerprint"),
            ("value", "B12", 2026, "expected value mismatch"),
        ):
            with self.subTest(cell=mutate):
                book = load_workbook(self.before_path)
                book[SHEET][mutate] = replacement
                mutant = self.tmp / f"mutant-{name}.xlsx"
                book.save(mutant)
                book.close()
                out = self.tmp / f"refused-{name}.xlsx"
                receipt = self.tmp / f"refused-{name}.json"
                with self.assertRaisesRegex(ValueError, expected):
                    self.entry.apply_fy1(mutant, out, receipt, context=self.context)
                self.assertFalse(out.exists())
                self.assertFalse(receipt.exists())
        self.assertEqual(self.before_path.read_bytes(), before)

    def test_a_tampered_spec_is_refused_before_any_write(self):
        changed = _json(SPEC_PATH)
        changed["patches"][4]["new_formula"] = "=1+2"
        tampered = self.tmp / "changed-spec.json"
        tampered.write_text(json.dumps(changed, ensure_ascii=False),
                            encoding="utf-8")
        out = self.tmp / "held.xlsx"
        receipt = self.tmp / "held.json"
        with mock.patch.object(self.entry, "SPEC_PATH", tampered):
            with self.assertRaisesRegex(ValueError, "registered spec"):
                self.entry.apply_fy1(self.before_path, out, receipt,
                                     context=self.context)
        self.assertFalse(out.exists())
        self.assertFalse(receipt.exists())

    def test_refuses_to_overwrite_an_existing_output(self):
        out = self.tmp / "taken.xlsx"
        out.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.entry.apply_fy1(self.before_path, out, self.tmp / "r.json",
                                 context=self.context)
        self.assertEqual(out.read_bytes(), b"existing")

    def test_a_copy_without_the_original_form_is_refused(self):
        other = self.tmp / "other.xlsx"
        book = Workbook()
        book.active.title = self.names[0]
        for name in self.names[1:]:
            book.create_sheet(name)
        book.save(other)
        book.close()
        out = self.tmp / "other-out.xlsx"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.entry.apply_fy1(other, out, self.tmp / "other.json",
                                 context=self.context)
        self.assertFalse(out.exists())

    def test_the_cli_has_no_map_or_spec_argument(self):
        source = inspect.getsource(runtime("gg_fruit_correction").cli)
        self.assertIn('"apply-fy1"', source)
        self.assertNotIn('"--map"', source)
        self.assertNotIn('"--spec"', source)


class LineageTests(_SyntheticFruit):
    """fill(원본 프로필) → 정정(fy1) → 사슬 프로필 전환."""

    def _fill(self):
        fill = runtime("gg_excel_fill")
        mapping = self.tmp / "fill-map.json"
        mapping.write_text(json.dumps({
            "schema": fill.MAP_SCHEMA, "workbook_profile": BASE_PROFILE,
            "template": {"sha256": runtime("gg_excel_template").sha256(
                self.before_path)},
            "entries": [{"sheet": "1.재무상태", "cell": "A1",
                         "role": "farm.area", "period": "2027",
                         "editable": True, "source_note": "합성 시험값"}]},
            ensure_ascii=False), encoding="utf-8")
        values = self.tmp / "values.json"
        values.write_text(json.dumps({"schema": fill.VALUES_SCHEMA, "values": [
            {"sheet": "1.재무상태", "cell": "A1", "value": 40,
             "value_type": "integer", "answer_state": "provided",
             "evidence_ref": {"source_id": "fy2-synthetic", "revision": 1,
                              "locator": "A1", "origin": "synthetic"}}]},
            ensure_ascii=False), encoding="utf-8")
        filled = self.tmp / "filled.xlsx"
        receipt = fill.fill_copy(self.before_path, mapping, values, filled,
                                 context=self.context)
        path = self.tmp / "fill-receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return filled, path

    def _lineage(self):
        filled, fill_receipt = self._fill()
        corrected = self.tmp / "lineage-corrected.xlsx"
        patch_receipt = self.tmp / "lineage-patch.json"
        self.entry.apply_fy1(filled, corrected, patch_receipt,
                             context=self.context)
        return corrected, fill_receipt, patch_receipt

    def test_the_correction_sits_between_fill_and_native_and_moves_the_profile(self):
        corrected, fill_receipt, patch_receipt = self._lineage()
        office = runtime("gg_office")
        binding = office.template_receipt_binding(corrected, patch_receipt)
        self.assertEqual(binding["workbook_profile"], PROFILE)
        authorization = self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context)
        verified, profile = office._preflight_excel_profile(
            corrected, [authorization], self.context,
            template_receipt=patch_receipt)
        self.assertEqual(profile, PROFILE)
        self.assertEqual(verified["workbook_profile"], PROFILE)
        lineage = office.verify_template_lineage(
            corrected, [fill_receipt, patch_receipt])
        self.assertEqual(lineage["status"], "pass")
        self.assertEqual(lineage["workbook_profile"], PROFILE)
        self.assertEqual([row["workbook_profile"] for row in lineage["receipts"]],
                         [BASE_PROFILE, PROFILE])
        self.assertEqual([row["operation_status"] for row in lineage["receipts"]],
                         ["filled", "patched"])

    def test_a_tampered_receipt_cell_list_is_refused(self):
        corrected, fill_receipt, patch_receipt = self._lineage()
        receipt = _json(patch_receipt)
        receipt["patched"] = receipt["patched"][:-1]
        forged = self.tmp / "truncated.json"
        forged.write_text(json.dumps(receipt, ensure_ascii=False),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "workbook profile changed"):
            runtime("gg_office").verify_template_lineage(
                corrected, [fill_receipt, forged])

    def test_a_receipt_without_the_registered_correction_block_is_refused(self):
        corrected, fill_receipt, patch_receipt = self._lineage()
        receipt = _json(patch_receipt)
        receipt.pop("corrections")
        forged = self.tmp / "no-corrections.json"
        forged.write_text(json.dumps(receipt, ensure_ascii=False),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "workbook profile changed"):
            runtime("gg_office").verify_template_lineage(
                corrected, [fill_receipt, forged])

    def test_a_tampered_map_hash_is_refused(self):
        corrected, fill_receipt, patch_receipt = self._lineage()
        receipt = _json(patch_receipt)
        receipt["map"] = dict(receipt["map"], sha256="0" * 64)
        forged = self.tmp / "bad-map-sha.json"
        forged.write_text(json.dumps(receipt, ensure_ascii=False),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lineage artifact hash mismatch"):
            runtime("gg_office").verify_template_lineage(
                corrected, [fill_receipt, forged])

    def test_a_receipt_from_another_correction_spec_is_refused(self):
        corrected, fill_receipt, patch_receipt = self._lineage()
        receipt = _json(patch_receipt)
        # 정정 21칸 대신 다른 칸 목록을 주장하면 등록 명세와 다르다.
        receipt["corrections"] = dict(receipt["corrections"], cells=20)
        forged = self.tmp / "other-cells.json"
        forged.write_text(json.dumps(receipt, ensure_ascii=False),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "workbook profile changed"):
            runtime("gg_office").verify_template_lineage(
                corrected, [fill_receipt, forged])


if __name__ == "__main__":
    import unittest
    unittest.main()
