"""식량작물 엑셀 2판 — N3 `clear` 지도가 16시트 사본을 만드는 방식.

지도(`references/food-crops/corrections/fc-clear-map.json`)는 159개 런으로
393칸을 지우고 82칸(계획연도 40 + 출처 주석 42)을 남긴다. 이 시험은 학교
원본 대신 같은 16개 시트 이름·같은 지도 좌표를 가진 합성 통합문서를 쓰고,
참조 세트는 시험 안에서만 합성 지문으로 바꾼다. 실제 원본 실측은
`tests/local_real_sources.py`(KNUAF_REAL_FOOD_CROPS_XLSX)가 맡는다.

고정하는 것: 지도 좌표·역할·칸 수, clear 뒤 남는 값 0, preserve 값 불변,
수식·병합·숨김 시트·셀 스타일 보존, 원본 바이트 불변, 프로필 이름이 다르면
`unsupported_finance_profile`, 지문이 다르면 `workbook_structure_mismatch`.
Excel은 실행하지 않는다.
"""

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from tests._harness import ContractCase, REPO_ROOT, bind_major, runtime

FOOD = "food_crops"
PROFILE = "food_crops_16_sheet_v1"
SCHOOL_PROFILE = "school_17_sheet_v1"
MAP_PATH = (REPO_ROOT / "skills/knuaf-doc/references/food-crops"
            / "corrections/fc-clear-map.json")
SHEET_COUNT = 16
CLEARED_CELLS = 393
PRESERVED_CELLS = 82
YEAR_CELLS = 40
NOTE_CELLS = 42


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class FoodCropsClearMapTests(ContractCase):
    def setUp(self):
        self.tpl = runtime("gg_excel_template")
        self.school = runtime("gg_school_excel")
        self.mc = runtime("gg_major_contract")
        self.map = _json(MAP_PATH)
        self.sheets = self.school.profile_structure(PROFILE)["sheets"]
        self.root = self.make_project()
        bind_major(self.root, FOOD)
        self.context = self.mc.output_context(self.root, FOOD)

    # -- synthetic 16-sheet workbook -------------------------------------
    def _expanded(self, entry):
        return self.tpl.expand_ref(entry.get("range") or entry["cell"])

    def synthetic_source(self, *, fingerprint_tamper=False):
        """Every mapped cell carries a value; formulas, merges and styles sit
        outside the map so the counts stay exact."""
        book = Workbook()
        book.remove(book.active)
        for name in self.sheets:
            book.create_sheet(name)
        book["목록"]["A1"] = "합성 목록"
        book["목록"]["A2"] = "=COUNTA(A1:A1)"
        book["목록"].merge_cells("C1:D1")
        book["목록"]["C1"] = "합성 병합 표제"
        book["목록"].sheet_state = "hidden"
        for entry in self.map["entries"]:
            sheet = book[entry["sheet"]]
            for cell in self._expanded(entry):
                sheet[cell] = 2026 if entry["role"] == "structure.period_year" \
                    else ("* 합성 출처 주석" if entry["role"] == "structure.source_note"
                          else 1234)
        # One styled clear cell proves style survives the value wipe.
        styled = self.map["entries"][0]
        first = sheet = book[styled["sheet"]]
        cell = first[self._expanded(styled)[0]]
        cell.font = Font(bold=True, color="FF0000")
        cell.fill = PatternFill(fill_type="solid", fgColor="FFFF00")
        source = self.root / "synthetic-16.xlsx"
        book.save(source)
        book.close()

        registry = copy.deepcopy(_json(self.school.WORKBOOK_REFERENCE_SET))
        profile = next(p for p in registry["workbook_profiles"]
                       if p["profile"] == PROFILE)
        profile["sha256"] = self.tpl.sha256(source)
        fingerprints = self.school.formula_fingerprints(load_workbook(source, read_only=True))
        if fingerprint_tamper:
            fingerprints[self.sheets[0]] = hashlib.sha256(b"tampered").hexdigest()
        profile["formula_fingerprints"] = fingerprints
        registry_path = self.root / "synthetic-reference.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        patch = mock.patch.object(self.school, "WORKBOOK_REFERENCE_SET",
                                  registry_path)
        patch.start()
        self.addCleanup(patch.stop)

        mapping = copy.deepcopy(self.map)
        mapping["source"] = {"sha256": self.tpl.sha256(source),
                             "size": source.stat().st_size}
        map_path = self.root / "synthetic-map.json"
        map_path.write_text(json.dumps(mapping, ensure_ascii=False),
                            encoding="utf-8")
        return source, map_path, mapping

    # -- the map itself --------------------------------------------------
    def test_the_map_clears_inputs_and_keeps_the_forms_own_text(self):
        self.assertEqual(self.map["schema"], "gg-xlsx-template-map/v1")
        self.assertEqual(self.map["workbook_profile"], PROFILE)
        self.assertEqual(self.map["mapStatus"], "ok")
        self.assertEqual(self.map["layout"],
                         {"variant": "unknown", "mapOrigin": "reviewed_custom"})
        self.assertEqual(self.map["source"]["sha256"],
                         self.school.profile_structure(PROFILE)["sha256"])
        self.assertEqual(len(self.map["entries"]), 159)
        counts = {"clear": {}, "preserve": {}}
        for entry in self.map["entries"]:
            self.assertIn(entry["action"], ("clear", "preserve"))
            self.assertTrue(entry["explicit"])
            self.assertTrue(entry["reason"].strip())
            self.assertNotIn("legacy", entry["reason"].lower())
            self.assertNotIn("reference", entry["reason"].lower())
            bucket = counts[entry["action"]]
            bucket[entry["role"]] = bucket.get(entry["role"], 0) \
                + len(self._expanded(entry))
        self.assertEqual(counts["clear"], {"input.student_estimate": CLEARED_CELLS})
        self.assertEqual(counts["preserve"], {"structure.period_year": YEAR_CELLS,
                                              "structure.source_note": NOTE_CELLS})
        # A preserved note is the form's own instruction, not prose to keep.
        notes = [e for e in self.map["entries"]
                 if e["role"] == "structure.source_note"]
        self.assertTrue(all(e["action"] == "preserve" for e in notes))
        years = [e for e in self.map["entries"]
                 if e["role"] == "structure.period_year"]
        self.assertTrue(all(e["action"] == "preserve" for e in years))

    # -- the writer ------------------------------------------------------
    def test_blank_copy_clears_393_and_keeps_82(self):
        source, map_path, _ = self.synthetic_source()
        original = source.read_bytes()
        before = load_workbook(source)
        styled_sheet, styled_cell = self.map["entries"][0]["sheet"], \
            self._expanded(self.map["entries"][0])[0]
        style_before = before[styled_sheet][styled_cell].style_id
        before.close()

        out = self.root / "blank-16.xlsx"
        receipt = self.tpl.blank_copy(source, map_path, out, context=self.context)

        self.assertEqual(receipt["workbook_profile"], PROFILE)
        self.assertEqual(receipt["majorAuthorization"]["major_id"], FOOD)
        self.assertEqual(len(receipt["cleared"]), CLEARED_CELLS)
        self.assertEqual(len(receipt["preserved"]), PRESERVED_CELLS)
        self.assertEqual(receipt["formulaCellsProtected"], 0)
        self.assertEqual(receipt["mergedCellsProtected"], 0)
        self.assertEqual(receipt["unclassifiedNumericCells"], 0)
        self.assertEqual(receipt["unclassifiedTextCells"], 0)
        self.assertEqual(receipt["missingCells"], [])
        self.assertEqual(receipt["classification"], {
            "clearMapped": CLEARED_CELLS, "preserveMapped": PRESERVED_CELLS,
            "unclassifiedNumeric": 0, "unclassifiedText": 0,
            "unclassifiedValuesRemain": False})
        self.assertEqual(receipt["output"]["status"], "blank_template")
        self.assertIn("cached_formula_values_invalidated",
                      receipt["output"]["calculationStatus"])
        self.assertEqual(source.read_bytes(), original)

        book = load_workbook(out)
        self.assertEqual(book.sheetnames, list(self.sheets))
        self.assertEqual(book["목록"].sheet_state, "hidden")
        self.assertEqual(book["목록"]["C1"].value, "합성 병합 표제")
        self.assertEqual([r.coord for r in book["목록"].merged_cells.ranges],
                         ["C1:D1"])
        self.assertEqual(book["목록"]["A2"].value, "=COUNTA(A1:A1)")
        self.assertEqual(book[styled_sheet][styled_cell].style_id, style_before)
        self.assertTrue(book[styled_sheet][styled_cell].font.bold)
        for entry in self.map["entries"]:
            sheet = book[entry["sheet"]]
            for cell in self._expanded(entry):
                with self.subTest(sheet=entry["sheet"], cell=cell,
                                  action=entry["action"]):
                    if entry["action"] == "clear":
                        self.assertIsNone(sheet[cell].value)
                    else:
                        expected = (2026 if entry["role"] == "structure.period_year"
                                    else "* 합성 출처 주석")
                        self.assertEqual(sheet[cell].value, expected)
        book.close()

    def test_other_profile_declarations_are_refused_before_any_write(self):
        source, map_path, mapping = self.synthetic_source()
        mapping["workbook_profile"] = SCHOOL_PROFILE
        map_path.write_text(json.dumps(mapping, ensure_ascii=False),
                            encoding="utf-8")
        out = self.root / "wrong-profile.xlsx"
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.tpl.blank_copy(source, map_path, out, context=self.context)
        self.assertEqual(caught.exception.reason, "unsupported_finance_profile")
        self.assertFalse(out.exists())

    def test_a_tampered_formula_fingerprint_is_held(self):
        source, map_path, _ = self.synthetic_source(fingerprint_tamper=True)
        out = self.root / "tampered.xlsx"
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.tpl.blank_copy(source, map_path, out, context=self.context)
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")
        self.assertIn("fingerprint", str(caught.exception))
        self.assertFalse(out.exists())

    def test_a_changed_source_is_never_cleared(self):
        source, map_path, mapping = self.synthetic_source()
        mapping["source"]["sha256"] = "0" * 64
        map_path.write_text(json.dumps(mapping, ensure_ascii=False),
                            encoding="utf-8")
        out = self.root / "stale.xlsx"
        with self.assertRaisesRegex(RuntimeError, "source version changed"):
            self.tpl.blank_copy(source, map_path, out, context=self.context)
        self.assertFalse(out.exists())
