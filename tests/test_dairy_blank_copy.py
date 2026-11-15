"""낙농 엑셀 2판 - 학교 28시트 재무 엑셀의 빈 사본.

지도(`references/dairy/corrections/dairy-clear-map.json`)는 2,903개 런으로
입력 칸 1,070개(학생 추정 1,053 + 고유 글 5 + 서술 12)를 지우고 5,463개를
남긴다. 남기는 5,463개는 표 구조 5,055개와 형님 결정(2026-11-13)으로 남긴
참조 기준값 408개다. 초안의 세 번째 action `decide` 408칸은 여기서
`preserve`로 접혀 action이 두 가지뿐이다.

이 시험은 학교 원본 대신 같은 28개 시트 이름·같은 지도 좌표를 가진 합성
통합문서를 쓰고, 참조 세트는 시험 안에서만 합성 지문으로 바꾼다. 실제 원본
실측은 `tests/local_real_sources.py`(KNUAF_REAL_DAIRY_XLSX)가 맡는다.

고정하는 것: 지도 좌표·역할·칸 수와 그 해시 두 개(합성 입력과 분리된 독립
상수), role 화이트리스트, clear 뒤 남는 값 0, preserve 값 불변, 수식·병합·숨김
시트·셀 스타일 보존, 원본 바이트 불변, 프로필 이름이 다르면
`unsupported_finance_profile`, 지문이 다르면 `workbook_structure_mismatch`,
원본이 바뀌면 `source version changed`, 값 채우기는 `fill_unsupported_for_major`,
번들 17시트는 `student_template_required`. Excel은 실행하지 않는다.
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

DAIRY = "dairy"
PROFILE = "dairy_hanwoo_28_sheet_v1"
SCHOOL_PROFILE = "school_17_sheet_v1"
MAP_PATH = (REPO_ROOT / "skills/knuaf-doc/references/dairy"
            / "corrections/dairy-clear-map.json")
DIV0_PATH = (REPO_ROOT / "skills/knuaf-doc/references/dairy"
             / "corrections/dairy-div0-expected.json")
EXPECTED_DIV0 = 29
# The 29 addresses, written here independently of the JSON so that moving one
# address (say 지표!E9 -> 지표!E10) fails even if every count still matches.
EXPECTED_DIV0_ADDRESSES = frozenset(
    [("16.투자분석★★★★", "E28")]
    + [("2.연차별 사육두수 확대계획", f"{col}{row}")
       for col in "EFGHIJ" for row in (23, 24, 25)]
    + [("지표", f"{col}{row}") for col in "EFGHI" for row in (9, 14)])
SHEET_COUNT = 28
CLEARED_CELLS = 1070
PRESERVED_CELLS = 5463
ENTRY_COUNT = 2903
ENTRY_CLEAR = 414
ENTRY_PRESERVE = 2489
STRUCTURE_CELLS = 5055
REFERENCE_CELLS = 408
# Reviewed coordinates, frozen here rather than derived from the map: sorted
# "\t".join((sheet, cell)) per line, sha256. A coordinate moved in the map -
# even with the same counts and no duplicates - fails this.
REVIEWED_COORDINATES_SHA256 = (
    "0a65cef7a7348e3b0953a18a05104ddf976cffdd0188ecadf9a08947491fee71")
# Reviewed classification, frozen the same way but with the verdict in it:
# sorted "\t".join((sheet, cell, action, role)) per line, sha256. Coordinates
# alone cannot see a swapped verdict - moving one cell's ``clear`` onto
# another's ``preserve`` keeps every coordinate and every count - so the
# decision at each cell is pinned here, not just its address.
REVIEWED_CLASSIFICATION_SHA256 = (
    "27bd317c2b89f9ab16cd4f90e21641d3f25d3c424680565c2fbb7df4e20bf954")
# Every role the map may use. A new role means the map was re-reviewed.
ROLES = {
    "input.student_estimate": ("clear", 1053),
    "input.student_identity": ("clear", 5),
    "input.student_text": ("clear", 12),
    "structure.label": ("preserve", 4458),
    "structure.period_year": ("preserve", 215),
    "structure.period_month": ("preserve", 343),
    "structure.calendar_days": ("preserve", 12),
    "structure.source_note": ("preserve", 27),
    "reference.feed_composition": ("preserve", 149),
    "reference.feed_product": ("preserve", 8),
    "reference.standard_value": ("preserve", 54),
    "reference.statistics": ("preserve", 42),
    "reference.market_price": ("preserve", 72),
    "reference.official_rate": ("preserve", 35),
    "reference.depreciation_life": ("preserve", 48),
}
# The 2026 E-column cells that start all six years. They are ``clear``: the
# student types the first year's numbers there, and every later sheet follows.
YEAR_ONE_STARTERS = ("E5", "E9", "E15", "E19", "E196", "E197", "E201",
                     "E202", "E206", "E207", "E230", "E232", "E234", "E236",
                     "E238", "E240", "E242", "E248")
# The 17 cells that hold this student's own name, breed, barn structure and
# other identifying text. Every one is ``clear``: leaving a classmate's
# sample farm in a new student's form would be the worst possible leak.
STUDENT_OWN_CELLS = {
    ("1.자산조사★★★★", "B3"): "input.student_identity",
    ("1.자산조사★★★★", "C7"): "input.student_identity",
    ("1.자산조사★★★★", "C8"): "input.student_identity",
    ("12.생산원가명세서", "C5"): "input.student_identity",
    ("14.대차대조표", "B4"): "input.student_identity",
    ("1.자산조사★★★★", "C15"): "input.student_text",
    ("1.자산조사★★★★", "C16"): "input.student_text",
    ("1.자산조사★★★★", "C17"): "input.student_text",
    ("1.자산조사★★★★", "C26"): "input.student_text",
    ("1.자산조사★★★★", "D26"): "input.student_text",
    ("1.자산조사★★★★", "E26"): "input.student_text",
    ("1.자산조사★★★★", "C27"): "input.student_text",
    ("1.자산조사★★★★", "D27"): "input.student_text",
    ("1.자산조사★★★★", "E27"): "input.student_text",
    ("1.자산조사★★★★", "C28"): "input.student_text",
    ("1.자산조사★★★★", "D28"): "input.student_text",
    ("1.자산조사★★★★", "E28"): "input.student_text",
}


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class DairyClearMapTests(ContractCase):
    def setUp(self):
        self.tpl = runtime("gg_excel_template")
        self.school = runtime("gg_school_excel")
        self.mc = runtime("gg_major_contract")
        self.map = _json(MAP_PATH)
        self.sheets = self.school.profile_structure(PROFILE)["sheets"]
        self.root = self.make_project()
        bind_major(self.root, DAIRY)
        self.context = self.mc.output_context(self.root, DAIRY)

    # -- synthetic 28-sheet workbook -------------------------------------
    def _expanded(self, entry):
        return self.tpl.expand_ref(entry.get("range") or entry["cell"])

    def synthetic_source(self, *, fingerprint_tamper=False):
        """Every mapped cell carries a value; formulas, merges and styles sit
        outside the map so the counts stay exact."""
        book = Workbook()
        book.remove(book.active)
        for name in self.sheets:
            book.create_sheet(name)
        # Off-map cells prove formulas, merges and hidden sheets survive.
        extra = book[self.sheets[0]]
        extra["AA200"] = "=COUNTA(AA199:AA199)"
        extra.merge_cells("AB200:AC200")
        extra["AB200"] = "합성 병합 표제"
        extra.sheet_state = "hidden"
        for entry in self.map["entries"]:
            sheet = book[entry["sheet"]]
            for cell in self._expanded(entry):
                if entry["action"] == "clear":
                    sheet[cell] = 1234
                elif entry["role"] in ("structure.period_year",
                                       "structure.period_month",
                                       "structure.calendar_days"):
                    sheet[cell] = 2026
                elif entry["role"] in ("structure.source_note",
                                       "input.student_identity",
                                       "input.student_text"):
                    sheet[cell] = "* 합성 출처 주석"
                else:
                    sheet[cell] = 1234
        # One styled clear cell proves style survives the value wipe.
        styled = next(e for e in self.map["entries"] if e["action"] == "clear")
        cell = book[styled["sheet"]][self._expanded(styled)[0]]
        cell.font = Font(bold=True, color="FF0000")
        cell.fill = PatternFill(fill_type="solid", fgColor="FFFF00")
        source = self.root / "synthetic-28.xlsx"
        book.save(source)
        book.close()

        registry = copy.deepcopy(_json(self.school.WORKBOOK_REFERENCE_SET))
        profile = next(p for p in registry["workbook_profiles"]
                       if p["profile"] == PROFILE)
        profile["sha256"] = self.tpl.sha256(source)
        fingerprints = self.school.formula_fingerprints(
            load_workbook(source, read_only=True))
        if fingerprint_tamper:
            fingerprints[self.sheets[0]] = hashlib.sha256(
                b"tampered").hexdigest()
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

    def _classification(self, entries):
        """Sorted (sheet, cell, action, role) lines for a map's entries."""
        rows = sorted((entry["sheet"], cell, entry["action"], entry["role"])
                      for entry in entries
                      for cell in self._expanded(entry))
        return rows, hashlib.sha256(
            "\n".join("\t".join(row) for row in rows).encode("utf-8")).hexdigest()

    # -- the map itself --------------------------------------------------
    def test_the_map_clears_inputs_and_keeps_the_forms_own_text(self):
        self.assertEqual(self.map["schema"], "gg-xlsx-template-map/v1")
        self.assertEqual(self.map["workbook_profile"], PROFILE)
        self.assertEqual(self.map["mapStatus"], "ok")
        self.role_at = {(entry["sheet"], cell): entry["role"]
                        for entry in self.map["entries"]
                        for cell in self._expanded(entry)}
        self.assertEqual(self.map["layout"],
                         {"variant": "unknown", "mapOrigin": "reviewed_custom"})
        self.assertEqual(self.map["source"]["sha256"],
                         self.school.profile_structure(PROFILE)["sha256"])
        self.assertEqual(len(self.map["entries"]), ENTRY_COUNT)
        self.assertEqual(
            sum(1 for e in self.map["entries"] if e["action"] == "clear"),
            ENTRY_CLEAR)
        self.assertEqual(
            sum(1 for e in self.map["entries"] if e["action"] == "preserve"),
            ENTRY_PRESERVE)

        counts = {"clear": 0, "preserve": 0}
        per_role = {}
        for entry in self.map["entries"]:
            self.assertIn(entry["action"], ("clear", "preserve"))
            self.assertTrue(entry["explicit"])
            self.assertTrue(entry["reason"].strip())
            self.assertNotIn("legacy", entry["reason"].lower())
            self.assertNotIn("reference", entry["reason"].lower())
            size = len(self._expanded(entry))
            counts[entry["action"]] += size
            per_role[entry["role"]] = per_role.get(entry["role"], 0) + size
        self.assertEqual(counts, {"clear": CLEARED_CELLS,
                                  "preserve": PRESERVED_CELLS})
        # The reviewed role whitelist, cell for cell: a new role, a moved
        # action or a resized run changes this table.
        self.assertEqual(per_role, {role: cells
                                    for role, (_, cells) in ROLES.items()})
        structure = sum(cells for role, (_, cells) in ROLES.items()
                        if role.startswith("structure."))
        reference = sum(cells for role, (_, cells) in ROLES.items()
                        if role.startswith("reference."))
        self.assertEqual((structure, reference),
                         (STRUCTURE_CELLS, REFERENCE_CELLS))

        # The reviewed coordinate set, frozen independently of this map.
        coordinates = sorted({(entry["sheet"], cell)
                              for entry in self.map["entries"]
                              for cell in self._expanded(entry)})
        listing = "\n".join("\t".join(pair) for pair in coordinates)
        self.assertEqual(hashlib.sha256(listing.encode("utf-8")).hexdigest(),
                         REVIEWED_COORDINATES_SHA256)

        cleared = {(e["sheet"], cell) for e in self.map["entries"]
                   if e["action"] == "clear" for cell in self._expanded(e)}
        for cell in YEAR_ONE_STARTERS:
            with self.subTest(cell=cell):
                self.assertIn(("2026", cell), cleared)
        for address, role in STUDENT_OWN_CELLS.items():
            with self.subTest(address=address):
                self.assertIn(address, cleared)
                self.assertEqual(self.role_at[address], role)
        self.assertEqual(
            sum(1 for role in self.role_at.values()
                if role.startswith("input.student_")), len(STUDENT_OWN_CELLS) + 1053)
        notes = [e for e in self.map["entries"]
                 if e["role"] == "structure.source_note"]
        self.assertTrue(notes and all(e["action"] == "preserve" for e in notes))
        years = [e for e in self.map["entries"]
                 if e["role"] == "structure.period_year"]
        self.assertTrue(years and all(e["action"] == "preserve" for e in years))
        # Q2 (형님 결정): market prices stay, and the reason says so, because
        # a kept price is a sample the student must replace with their own.
        prices = [e for e in self.map["entries"]
                  if e["role"] == "reference.market_price"]
        self.assertTrue(prices)
        self.assertTrue(all(e["action"] == "preserve" for e in prices))
        self.assertTrue(all("형님 결정" in e["reason"] for e in prices))

    def test_the_expected_div0_cells_still_match_the_form(self):
        """The 29 formulas predicted to show ``#DIV/0!`` on a blank copy are pinned.

        Measured by recalculating the original and the blank copy with one
        self-written evaluator, not Excel (agreeing with Excel's saved values
        on all 22,828 real formulas); 21 of the 50 transitive candidates stay
        finite. They are listed so a change to the map, the
        profile or the form that moves one of them fails here instead of
        surprising a student: the tool never rewrites these formulas.
        """
        doc = _json(DIV0_PATH)
        self.assertEqual(doc["schema"], "gg-dairy-div0-expected/v1")
        self.assertEqual(doc["workbook_profile"], PROFILE)
        self.assertEqual(doc["source_sha256"],
                         self.school.profile_structure(PROFILE)["sha256"])
        cells = doc["cells"]
        self.assertEqual(len(cells), EXPECTED_DIV0)
        self.assertEqual(len({(c["sheet"], c["cell"]) for c in cells}),
                         EXPECTED_DIV0)
        self.assertEqual(
            {sheet: sum(1 for c in cells if c["sheet"] == sheet)
             for sheet in {c["sheet"] for c in cells}},
            {"2.연차별 사육두수 확대계획": 18, "16.투자분석★★★★": 1,
             "지표": 10})
        self.assertEqual(len(EXPECTED_DIV0_ADDRESSES), EXPECTED_DIV0)
        self.assertEqual({(c["sheet"], c["cell"]) for c in cells},
                         EXPECTED_DIV0_ADDRESSES)
        self.assertEqual({c["kind"] for c in cells}, {"recalculated"})
        # Self-recalculation, not Excel: the label says so and the counts keep
        # the 22,828 real formulas apart from the one text cell "=".
        self.assertEqual(doc["method"], {
            "evaluator": "self_recalc_not_excel",
            "xml_formula_cells": 22828,
            "text_cells_starting_with_equals": 1,
            "recalc_cache_agreement": 22828,
            "transitive_candidates": 50, "measured": EXPECTED_DIV0})
        self.assertIn("엑셀 화면 확인은 아니다", doc["note"])
        # Every address exists on the form's own sheet list, and none of them
        # is a cell the map clears or preserves - they are formulas.
        names = [s["name"] if isinstance(s, dict) else s for s in self.sheets]
        mapped = {(e["sheet"], cell) for e in self.map["entries"]
                  for cell in self._expanded(e)}
        for entry in cells:
            with self.subTest(sheet=entry["sheet"], cell=entry["cell"]):
                self.assertIn(entry["sheet"], names)
                self.assertNotIn((entry["sheet"], entry["cell"]), mapped)

    def test_the_map_has_no_third_action_and_no_values(self):
        """The draft's ``decide`` 408 cells were answered, not carried over.

        ``blank_copy`` accepts only ``clear``/``preserve``; a surviving
        ``decide`` would be a map that cannot run, and any recorded value
        would put school sample numbers in the repository.
        """
        for entry in self.map["entries"]:
            self.assertNotIn(entry["action"], ("decide", "preserve+notify"))
            self.assertEqual(set(entry) - {"sheet", "range", "cell", "action",
                                           "role", "explicit", "reason"},
                             set())
        self.assertNotIn("decide_groups", self.map)
        serialized = MAP_PATH.read_text(encoding="utf-8")
        for word in ("oldValue", "\"value\"", "2026-01"):
            self.assertNotIn(word, serialized)

    def test_the_map_classification_is_frozen(self):
        """The verdict at each cell is pinned, not just its address.

        Swapping the ``clear`` at ``2026!E5`` with the ``preserve`` at
        ``2026!E4`` leaves every coordinate, every count and the coordinate
        hash identical. The classification hash covers the action and role as
        well, so that swap and any moved plan-year cell fail here.
        """
        rows, digest = self._classification(self.map["entries"])
        self.assertEqual(len(rows), CLEARED_CELLS + PRESERVED_CELLS)
        self.assertEqual(digest, REVIEWED_CLASSIFICATION_SHA256)

        # Swapping the verdicts of two one-cell runs on the same sheet keeps
        # every coordinate and every count, so only the classification hash
        # can catch it.
        swapped = copy.deepcopy(self.map["entries"])
        at_e5 = next(e for e in swapped
                     if (e["sheet"], e["range"]) == ("2026", "E5"))
        at_e3 = next(e for e in swapped
                     if (e["sheet"], e["range"]) == ("2026", "E3"))
        self.assertEqual((at_e5["action"], at_e3["action"]),
                         ("clear", "preserve"))
        self.assertEqual((at_e5["role"], at_e3["role"]),
                         ("input.student_estimate", "structure.label"))
        for field in ("action", "role"):
            at_e5[field], at_e3[field] = at_e3[field], at_e5[field]

        swapped_rows, swapped_digest = self._classification(swapped)
        self.assertEqual(
            sorted((sheet, cell) for sheet, cell, _, _ in swapped_rows),
            sorted((sheet, cell) for sheet, cell, _, _ in rows))
        self.assertEqual(
            {action: sum(1 for row in swapped_rows if row[2] == action)
             for action in ("clear", "preserve")},
            {"clear": CLEARED_CELLS, "preserve": PRESERVED_CELLS})
        self.assertNotEqual(swapped_digest, digest)

    # -- the writer ------------------------------------------------------
    def test_blank_copy_clears_1070_and_keeps_5463(self):
        source, map_path, _ = self.synthetic_source()
        original = source.read_bytes()
        before = load_workbook(source)
        styled_entry = next(e for e in self.map["entries"]
                            if e["action"] == "clear")
        styled_sheet, styled_cell = styled_entry["sheet"], \
            self._expanded(styled_entry)[0]
        style_before = before[styled_sheet][styled_cell].style_id
        before.close()

        out = self.root / "blank-28.xlsx"
        receipt = self.tpl.blank_copy(source, map_path, out,
                                      context=self.context)

        self.assertEqual(receipt["workbook_profile"], PROFILE)
        self.assertEqual(receipt["majorAuthorization"]["major_id"], DAIRY)
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
        # R-01: the copy says first that formula-embedded sample assumptions
        # and the 29 expected #DIV/0! cells remain - it is not a finished plan.
        self.assertEqual(receipt["notices"],
                         list(self.tpl.BLANK_COPY_NOTICES[PROFILE]))
        self.assertIn("DAIRY-CLEAR.md", receipt["notices"][0])
        self.assertIn("80,000", receipt["notices"][0])
        self.assertIn("29칸", receipt["notices"][1])
        self.assertIn("자체 재계산", receipt["notices"][1])
        self.assertNotIn("정상입니다", receipt["notices"][1])

        book = load_workbook(out)
        self.assertEqual(book.sheetnames, list(self.sheets))
        self.assertEqual(len(book.sheetnames), SHEET_COUNT)
        first = book[self.sheets[0]]
        self.assertEqual(first.sheet_state, "hidden")
        self.assertEqual(first["AB200"].value, "합성 병합 표제")
        self.assertEqual([r.coord for r in first.merged_cells.ranges],
                         ["AB200:AC200"])
        self.assertEqual(first["AA200"].value, "=COUNTA(AA199:AA199)")
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
                        self.assertIsNotNone(sheet[cell].value)
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

    def test_the_sixteen_sheet_map_cannot_clear_this_form(self):
        """A map for another school form is refused by its own declaration.

        The food-crops map is re-pointed at this synthetic 28-sheet workbook
        so the source-version check passes and the profile declaration is what
        the caller hits: a dairy student cannot be cleared with the 16-sheet
        plan, however well its addresses happen to line up.
        """
        path = (REPO_ROOT / "skills/knuaf-doc/references/food-crops"
                / "corrections/fc-clear-map.json")
        mapping = _json(path)
        self.assertNotEqual(mapping["workbook_profile"], PROFILE)
        source, _, synthetic = self.synthetic_source()
        mapping["source"] = synthetic["source"]
        foreign = self.root / "foreign-map.json"
        foreign.write_text(json.dumps(mapping, ensure_ascii=False),
                           encoding="utf-8")
        out = self.root / "cross.xlsx"
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.tpl.blank_copy(source, foreign, out, context=self.context)
        self.assertEqual(caught.exception.reason,
                         "unsupported_finance_profile")
        # The held error names the refused profile, so the student learns
        # which form's map was handed in instead of a bare refusal.
        self.assertEqual(caught.exception.detail.get("profile"),
                         mapping["workbook_profile"])
        self.assertEqual(caught.exception.detail.get("major_id"), DAIRY)
        self.assertFalse(out.exists())

    # -- the rest of the dairy contract stays closed ---------------------
    def test_a_dairy_map_is_refused_for_a_food_crops_student(self):
        """The 28-sheet profile is dairy's alone, in both directions.

        The other majors each keep their own profile; dairy's unlocked door
        must not open the 16-sheet form to a dairy student, nor give a
        food-crops student the 28-sheet one.
        """
        source, map_path, _ = self.synthetic_source()
        food_root = self.make_project()
        bind_major(food_root, "food_crops")
        food_context = self.mc.output_context(food_root, "food_crops")
        out = food_root / "dairy-map-food-student.xlsx"
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.tpl.blank_copy(source, map_path, out, context=food_context)
        self.assertEqual(caught.exception.reason,
                         "unsupported_finance_profile")
        self.assertEqual(caught.exception.detail.get("profile"), PROFILE)
        self.assertEqual(caught.exception.detail.get("major_id"), "food_crops")
        self.assertFalse(out.exists())

    def test_a_food_crops_map_is_refused_for_a_dairy_student(self):
        """The mirror case: dairy never borrows the 16-sheet plan."""
        source, map_path, _ = self.synthetic_source()
        # Point the dairy profile's declaration at the food-crops name so the
        # version and fingerprint checks pass and the profile check is what
        # the caller hits.
        mapping = _json(map_path)
        mapping["workbook_profile"] = "food_crops_16_sheet_v1"
        map_path.write_text(json.dumps(mapping, ensure_ascii=False),
                            encoding="utf-8")
        out = self.root / "food-map-dairy-student.xlsx"
        with self.assertRaises(self.mc.OutputHeldError) as caught:
            self.tpl.blank_copy(source, map_path, out, context=self.context)
        self.assertEqual(caught.exception.reason,
                         "unsupported_finance_profile")
        self.assertEqual(caught.exception.detail.get("profile"),
                         "food_crops_16_sheet_v1")
        self.assertEqual(caught.exception.detail.get("major_id"), DAIRY)
        self.assertFalse(out.exists())

    def test_filling_values_is_refused_for_dairy(self):
        source, _, _ = self.synthetic_source()
        fill = runtime("gg_excel_fill")
        with tempfile.TemporaryDirectory() as temp:
            fill_map = Path(temp) / "fill-map.json"
            fill_map.write_text(json.dumps({
                "schema": "gg-xlsx-fill-map/v1",
                "template": {"sha256": self.tpl.sha256(source)},
                "entries": [{"sheet": self.sheets[0], "cell": "AA200",
                             "role": "input", "period": "2030년",
                             "source_note": "합성", "editable": True}]},
                ensure_ascii=False), encoding="utf-8")
            values = Path(temp) / "values.json"
            values.write_text(json.dumps(
                {"schema": "gg-xlsx-fill-values/v1", "values": []}),
                encoding="utf-8")
            out = Path(temp) / "filled.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                fill.fill_copy(source, fill_map, values, out,
                               context=self.context)
            self.assertEqual(caught.exception.reason,
                             "fill_unsupported_for_major")
            self.assertFalse(out.exists())

    def test_the_bundled_seventeen_sheet_form_is_never_dairy_copy(self):
        """A verified "no school form" survey does not make the bundle one."""
        inventory = {"survey": {
            "scope_label": "dairy", "scope_id": "/synthetic/dairy",
            "status": "complete",
            "evidence": {"verified": True, "basis": "user_attested"}},
            "files": []}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "bundled.xlsx"
            with self.assertRaises(self.mc.OutputHeldError) as caught:
                self.tpl.create_workbook(None, out, context=self.context,
                                         template_ref="X01",
                                         reference_inventory=inventory)
            self.assertEqual(caught.exception.reason,
                             "student_template_required")
            self.assertIn(PROFILE, str(caught.exception))
            self.assertFalse(out.exists())

    def test_an_explicit_school_source_is_copied_byte_identical(self):
        source, _, _ = self.synthetic_source()
        out = self.root / "copy-28.xlsx"
        receipt = self.tpl.create_workbook(source, out, context=self.context)
        self.assertEqual(receipt["status"], "template_copy")
        self.assertEqual(receipt["source"]["kind"], "student")
        self.assertEqual(out.read_bytes(), source.read_bytes())

    def test_the_other_dairy_finance_capabilities_stay_unsupported(self):
        module = self.mc.default_registry().resolve(DAIRY)
        by_profile = {cap.profile: cap.status
                      for cap in module.finance_capabilities}
        self.assertEqual(by_profile[PROFILE], "supported")
        self.assertEqual(by_profile["dairy_cattle_milk_cash"], "unsupported")
        self.assertEqual(by_profile["hanwoo_herd_cash"], "unsupported")


# R-01: the formula-embedded assumptions DAIRY-CLEAR.md section 2 lists.
# The real-source check proves each address holds a formula with a numeric
# literal; this pins that the student table names exactly these addresses.
FORMULA_CONSTANT_ADDRESSES = (
    ("1.자산조사★★★★", "C56"), ("1.자산조사★★★★", "C57"),
    ("1.자산조사★★★★", "D56"), ("1.자산조사★★★★", "D57"),
    ("1.자산조사★★★★", "E7"), ("1.자산조사★★★★", "D12"),
    ("10.감가상각비★★★", "D28"),
    ("11.원리금상환계획", "F5"), ("11.원리금상환계획", "F25"),
    ("11.원리금상환계획", "F41"), ("11.원리금상환계획", "F86"),
    ("5.종부검정혈통등록비", "D9"), ("5.종부검정혈통등록비", "G10"),
    ("5.종부검정혈통등록비", "D52"),
    ("12.생산원가명세서", "F15"), ("12.생산원가명세서", "E28"),
    ("13.손익계산서", "C14"),
    ("6.노동력이용계획", "G45"), ("7.인건비계산", "F52"),
    ("2026", "E190"), ("2026", "Q246"),
    ("16-1.투자분석자료★★★", "H13"),
)


class DairyFormulaConstantTableTests(ContractCase):

    def test_the_student_table_names_every_pinned_address(self):
        text = (REPO_ROOT / "skills/knuaf-doc/references/dairy/corrections"
                / "DAIRY-CLEAR.md").read_text(encoding="utf-8")
        section = text.split("### 2. 수식 안에 박힌 값", 1)[1].split("### 3.", 1)[0]
        rows = [line for line in section.splitlines()
                if line.startswith("| ") and "`" in line]
        self.assertEqual(len(rows), 20)
        for sheet, cell in FORMULA_CONSTANT_ADDRESSES:
            with self.subTest(sheet=sheet, cell=cell):
                short = sheet.rstrip("★")
                self.assertTrue(any(short.split(".")[-1][:4] in row
                                    and f"`{cell}" in row for row in rows)
                                or any(f"`{cell}`" in row and sheet[:4] in row
                                       for row in rows))
        self.assertEqual(len(FORMULA_CONSTANT_ADDRESSES), 22)
