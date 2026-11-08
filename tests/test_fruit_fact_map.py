"""과수 재무 사실 지도가 등록용(`gg-xlsx-fact-map/v1`)임을 잠근다.

지도는 본문 Ⅴ장 재무 핵심 금액 70행(7계열×10년)과 각 값이 오는 16시트 셀·기간·
단위를 적는다. 시험은 네 가지를 잠근다: 검토된 (fact_id, sheet, cell, period,
field_id) 70행 동결표, 행마다 제 캐시를 읽는 실제 등록, 오류 셀(`#DIV/0!`)·비율
셀을 등록하지 않는 어댑터 동작, 그리고 해시·프로필 바인딩 거부.

native 영수증은 합성 fixture이며 Excel 실행이 아니다. 실제 재계산은 저장소 밖
에서 한다. `fruit-finance-fact-map.draft.json`은 대체된 옛 초안으로 남는다.
"""
import copy
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import Workbook

from tests._harness import ContractCase, bind_major, runtime


ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "skills/knuaf-doc/references/fruit-trees"
MAP_PATH = REF / "fruit-finance-fact-map.json"
DRAFT_PATH = REF / "fruit-finance-fact-map.draft.json"
PROFILE = "fruit_16_sheet_v1"
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}

# 본문에서 실제로 쓰는 항목 이름. 기계용 식별자(sales.revenue)를 쓰면 본문 대조가
# "매출액"을 못 찾아 blocked가 되므로, field_id는 본문 표기여야 한다.
LABELS = {
    "매출액": "revenue",
    "당기순이익": "netprofit",
    "생산원가": "prodcost",
    "영업이익": "opprofit",
    "수지균형": "annual-balance",
    "누적수지균형": "cum-balance",
    "소득": "income",
}

# 계열마다 시작 열이 다르다(손익계획·생산원가는 C, 현금흐름은 D, 소득분석은 E).
FIRST_COLUMN = {"매출액": "C", "당기순이익": "C", "생산원가": "C",
                "영업이익": "C", "수지균형": "D", "누적수지균형": "D",
                "소득": "E"}

# 검토된 70행을 파일과 독립적으로 동결한다. 파일이 스스로 일관돼도 행이 바뀌면
# 이 표와 어긋나 실패한다.
def _column(letter, offset):
    return chr(ord(letter) + offset)


EXPECTED_ROWS = tuple(
    (f"fruit-body-{LABELS[label]}-{year}", label, sheet,
     f"{_column(FIRST_COLUMN[label], year - 2027)}{row}", str(year))
    for label, sheet, row in (
        ("매출액", "12.손익계획", 6),
        ("당기순이익", "12.손익계획", 34),
        ("생산원가", "11.생산원가", 29),
        ("영업이익", "12.손익계획", 19),
        ("수지균형", "14.현금흐름", 21),
        ("누적수지균형", "14.현금흐름", 22),
        ("소득", "15.소득분석", 26),
    )
    for year in range(2027, 2037)
)

# 행마다 다른 캐시값: 두 행이 바뀌면 두 값이 함께 바뀐다.
EXPECTED_VALUES = {row[0]: 1000 + 7 * index for index, row in enumerate(EXPECTED_ROWS)}


def _map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _inside(span, cell):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?",
                         span)
    low, first, high, last = match.groups()
    high = high or low
    last = last or first
    column, row = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell).groups()
    return (min(low, high) <= column <= max(low, high)
            and int(first) <= int(row) <= int(last))


def _inject_caches(path, cells):
    """xlsx 한 파일에 숫자·오류 캐시를 직접 써 넣는다(값 재계산 아님)."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    workbook = ET.fromstring(members["xl/workbook.xml"])
    rels = ET.fromstring(members["xl/_rels/workbook.xml.rels"])
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    sheets = {}
    for sheet in workbook.find(f"{{{ns}}}sheets"):
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[rid].lstrip("/")
        sheets[sheet.get("name")] = target if target.startswith("xl/") else "xl/" + target
    for (sheet, ref), (value, kind) in cells.items():
        member = sheets[sheet]
        xml = ET.fromstring(members[member])
        data = xml.find(f"{{{ns}}}sheetData")
        row_number = int(re.search(r"[0-9]+", ref).group())
        row = next((candidate for candidate in data.findall(f"{{{ns}}}row")
                    if int(candidate.get("r")) == row_number), None)
        if row is None:
            row = ET.SubElement(data, f"{{{ns}}}row", {"r": str(row_number)})
        for candidate in list(row.findall(f"{{{ns}}}c")):
            if candidate.get("r") == ref:
                row.remove(candidate)
        attributes = {"r": ref}
        if kind is not None:
            attributes["t"] = kind
        cell = ET.SubElement(row, f"{{{ns}}}c", attributes)
        ET.SubElement(cell, f"{{{ns}}}v").text = str(value)
        rows = row.findall(f"{{{ns}}}c")
        for candidate in rows:
            row.remove(candidate)
        rows.sort(key=lambda item: (int(re.sub(r"[A-Z]", "", item.get("r"))),
                                    item.get("r")))
        for candidate in rows:
            row.append(candidate)
        members[member] = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


class MapFileTests(ContractCase):
    def setUp(self):
        self.map = _map()
        self.facts = runtime("gg_excel_facts")
        self.school = runtime("gg_school_excel")

    def test_map_is_the_common_registration_schema_with_unbound_hashes(self):
        self.assertEqual(self.map["schema"], self.facts.MAP_SCHEMA)
        self.assertEqual(self.map["workbook_profile"], PROFILE)
        self.assertEqual(self.map["status"], "rows_reviewed_binding_pending_native_run")
        # 해시는 native 실행이 채운다. 저장소 사본은 빈칸이어야 등록이 거부된다.
        self.assertEqual(self.map["workbook"], {"path": "", "sha256": ""})
        self.assertEqual(self.map["receipt"], {"path": "", "sha256": ""})
        self.assertTrue(any("build_registration" in line
                            for line in self.map["binding"]))

    def test_map_is_exactly_the_reviewed_row_set(self):
        shipped = {(row["fact_id"], row["field_id"], row["sheet"], row["cell"],
                    row["period"])
                   for row in self.map["cells"]}
        self.assertEqual(set(EXPECTED_ROWS), shipped)
        self.assertEqual(len(self.map["cells"]), 70)

    def test_rows_name_real_sheets_and_body_labels(self):
        roster = self.school.profile_structure(PROFILE)["sheets"]
        cells = self.map["cells"]
        self.assertEqual(len({row["fact_id"] for row in cells}), len(cells))
        self.assertEqual(len({(row["sheet"], row["cell"]) for row in cells}), len(cells))
        for row in cells:
            with self.subTest(row=row["fact_id"]):
                self.assertTrue(CELL.fullmatch(row["cell"]))
                self.assertIn(row["sheet"], roster)
                self.assertEqual(row["unit"], "천원")
                self.assertEqual(row["scope"], "계획")
                self.assertIn(row["field_id"], LABELS)
                self.assertTrue(row["body_label"].strip())

    def test_reviewed_cells_never_inside_an_excluded_series(self):
        excluded = {(row["sheet"], row["range"]) for row in self.map["excluded_series"]}
        self.assertIn(("13.대차대조", "C4:M35"), excluded)
        self.assertIn(("11.생산원가", "C36:L36"), excluded)
        for row in self.map["cells"]:
            for span in self.map["excluded_series"]:
                if span["sheet"] != row["sheet"]:
                    continue
                with self.subTest(row=row["fact_id"], span=span["range"]):
                    self.assertFalse(_inside(span["range"], row["cell"]))

    def test_pending_notice_carries_the_three_decided_sentences(self):
        notice = self.map["held_notice"]
        self.assertEqual(len(notice), 3)
        self.assertIn("합성 시험 사본", notice[0])
        self.assertIn("fruit-finance-input-map.json", notice[1])
        self.assertIn("수식을 고치지 않고", notice[2])

    def test_draft_stays_a_different_schema_and_is_marked_replaced(self):
        draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        self.assertNotEqual(draft["schema"], self.facts.MAP_SCHEMA)
        self.assertNotIn("workbook", draft)
        self.assertEqual(draft["status"], "superseded_by_registration_map")


class _Chain(ContractCase):
    """합성 native 사슬: 16시트 통합문서 + 영수증. Excel 실행 아님."""

    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.school = runtime("gg_school_excel")
        self.root = self.make_project()
        bind_major(self.root, "fruit_trees")
        self.context = self.mc.output_context(self.root, "fruit_trees")
        self.roster = self.school.profile_structure(PROFILE)["sheets"]
        self.workbook = self.root / "recalc.xlsx"
        book = Workbook()
        book.remove(book.active)
        for name in self.roster:
            book.create_sheet(name)
        book.save(self.workbook)
        book.close()
        # 등록 지도는 실제 과수 양식의 시트 목록·수식 지문을 요구한다. 합성
        # 통합문서는 수식이 없으므로 그 지문을 이 시험 안에서만 등록한다.
        registry = json.loads(self.school.WORKBOOK_REFERENCE_SET.read_text())
        profile = next(entry for entry in registry["workbook_profiles"]
                       if entry["profile"] == PROFILE)
        from openpyxl import load_workbook
        check = load_workbook(self.workbook)
        fingerprints = self.school.formula_fingerprints(check)
        check.close()
        profile["formula_fingerprints"] = fingerprints
        path = self.root / "reference.json"
        path.write_text(json.dumps(registry, ensure_ascii=False))
        from unittest import mock
        patch = mock.patch.object(self.school, "WORKBOOK_REFERENCE_SET", path)
        patch.start()
        self.addCleanup(patch.stop)
        self.receipt = self.root / "native.json"
        self.native = {
            "status": "converted", "engine": "Microsoft Excel",
            "engine_result": "GG_OFFICE_SESSION|closed|saved|synthetic",
            "major_authorization": [self.mc.authorize_output(
                self.mc.OUTPUT_SCHOOL_WORKBOOK, self.context).to_dict()],
            "office_session": dict(SESSION),
            "validation": {"workbook_profile": PROFILE, "finance_profile": PROFILE,
                           "structure_issues": [], "school_issues": [],
                           "excel": {"valid": True, "sha256": ""}},
        }

    def issue_native(self, caches):
        _inject_caches(self.workbook, caches)
        actual = self.core.digest(self.workbook.read_bytes())
        self.native["published_office_sha256"] = actual
        self.native["validation"]["excel"]["sha256"] = actual
        self.receipt.write_text(json.dumps(self.native, ensure_ascii=False))
        return {"workbook": actual,
                "receipt": self.core.digest(self.receipt.read_bytes())}

    def caches(self, values):
        return {(row[2], row[3]): (values.get(row[0], 0), None)
                for row in EXPECTED_ROWS}

    def bound(self, hashes, **overrides):
        mapping = _map()
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": hashes["workbook"]}
        mapping["receipt"] = {"path": "native.json", "sha256": hashes["receipt"]}
        mapping.update(overrides)
        return mapping

    def build(self, mapping):
        return self.facts.build_registration(
            self.root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan", context=self.context)

    def registered(self, change):
        return {op["value"]["id"]: (op["value"]["excel_provenance"]["sheet"],
                                    op["value"]["excel_provenance"]["cell"],
                                    op["value"]["value"])
                for op in change["ops"] if op["collection"] == "facts"}

    def held(self, mapping, reason):
        before = self._tree_bytes(self.root)
        with self.assertRaisesRegex(self.facts.RegistrationHeld, reason):
            self.build(mapping)
        self.assertEqual(before, self._tree_bytes(self.root))


class RegistrationTests(_Chain):
    def test_every_reviewed_row_reads_its_own_cell(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        change = self.build(self.bound(hashes))
        got = self.registered(change)
        want = {fact_id: (sheet, cell, str(EXPECTED_VALUES[fact_id]))
                for fact_id, _label, sheet, cell, _period in EXPECTED_ROWS}
        self.assertEqual(want, got)
        self.assertEqual(len(change["ops"]), 71)

    def test_registered_facts_carry_the_body_label_and_measure(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        change = self.build(self.bound(hashes))
        facts = {op["value"]["id"]: op["value"]
                 for op in change["ops"] if op["collection"] == "facts"}
        fact = facts["fruit-body-prodcost-2027"]
        self.assertEqual(fact["field_id"], "생산원가")
        self.assertEqual(fact["period"], "2027")
        self.assertEqual(fact["scope"], "계획")
        self.assertEqual(fact["measure"], {"kind": "monetary_total",
                                           "currency": "KRW", "unit": "천원"})
        self.assertEqual(fact["excel_provenance"]["cell"], "C29")

    def test_swapped_rows_register_the_value_of_the_cell_the_map_names(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        mapping = self.bound(hashes)
        rows = mapping["cells"]
        first = next(index for index, row in enumerate(rows)
                     if row["fact_id"] == "fruit-body-prodcost-2027")
        second = next(index for index, row in enumerate(rows)
                      if row["fact_id"] == "fruit-body-prodcost-2028")
        rows[first]["cell"], rows[second]["cell"] = (rows[second]["cell"],
                                                     rows[first]["cell"])
        got = self.registered(self.build(mapping))
        self.assertEqual(got["fruit-body-prodcost-2027"][2],
                         str(EXPECTED_VALUES["fruit-body-prodcost-2028"]))
        self.assertNotEqual(got["fruit-body-prodcost-2027"][2],
                            str(EXPECTED_VALUES["fruit-body-prodcost-2027"]))

    def test_error_cache_cells_are_never_registered(self):
        # 12.손익계획!C6은 빈 입력에서 #DIV/0! 로 남는다(WC2 ⑤). Excel은 이를
        # t="e" + "#DIV/0!" 캐시로 저장하므로 어댑터는 값을 만들지 않고 거부해야
        # 한다. 지도 행이 있다는 사실이 오류 셀 등록을 허용하지 않는다.
        caches = self.caches(EXPECTED_VALUES)
        caches[("12.손익계획", "C6")] = ("#DIV/0!", "e")
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == "fruit-body-revenue-2027"]
        self.held(mapping, "numeric_cache_required")

    def test_text_cache_is_never_read_as_an_amount(self):
        caches = self.caches(EXPECTED_VALUES)
        caches[("14.현금흐름", "D21")] = ("계획", "str")
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == "fruit-body-annual-balance-2027"]
        self.held(mapping, "numeric_cache_required")


class BodyCrosscheckTests(_Chain):
    """등록된 사실이 본문 문장과 실제로 대조되는지(소비자 동작)."""

    def register_one(self, value):
        caches = self.caches({})
        caches[("11.생산원가", "C29")] = (value, None)
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == "fruit-body-prodcost-2027"]
        change = self.build(mapping)
        self.core.apply(self.root, change, self.core.load(self.root)["revision"])
        return self.core.load(self.root)

    def test_body_sentence_with_the_registered_amount_passes(self):
        project = self.register_one("14473.44")
        items, blocked = self.core._finance_check_items(project)
        self.assertIsNone(blocked)
        self.assertEqual(self.school.crosscheck_body(
            "계획 2027 생산원가는 14,473.44천원이다.", items), [])

    def test_body_sentence_with_another_amount_fails(self):
        project = self.register_one("14473.44")
        items, _ = self.core._finance_check_items(project)
        self.assertTrue(self.school.crosscheck_body(
            "계획 2027 생산원가는 15,000천원이다.", items))


class BindingTests(_Chain):
    def test_shipped_map_is_held_until_a_run_binds_its_hashes(self):
        self.issue_native(self.caches(EXPECTED_VALUES))
        self.held(_map(), "fact_map_hash_mismatch")

    def test_draft_schema_is_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        self.held(self.bound(hashes, schema="gg-xlsx-fact-map-draft/v1"),
                  "fact_map_schema_invalid")

    def test_empty_cells_are_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        self.held(self.bound(hashes, cells=[]), "cell_mapping_required")

    def test_foreign_profile_label_is_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        self.held(self.bound(hashes, workbook_profile="school_17_sheet_v1"),
                  "fact_map_profile_mismatch")

    def test_wrong_workbook_hash_is_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        mapping = self.bound(hashes)
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": "f" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_wrong_receipt_hash_is_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        mapping = self.bound(hashes)
        mapping["receipt"] = {"path": "native.json", "sha256": "e" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_workbook_bytes_changed_after_binding_are_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        mapping = self.bound(hashes)
        self.workbook.write_bytes(self.workbook.read_bytes() + b"\0")
        self.held(mapping, "fact_map_hash_mismatch")

    def test_workbook_with_unregistered_sheets_is_held(self):
        hashes = self.issue_native(self.caches(EXPECTED_VALUES))
        mapping = self.bound(hashes)
        book = Workbook()
        book.active.title = "1.재무상태"
        book.save(self.workbook)
        book.close()
        actual = self.core.digest(self.workbook.read_bytes())
        self.native["published_office_sha256"] = actual
        self.native["validation"]["excel"]["sha256"] = actual
        self.receipt.write_text(json.dumps(self.native, ensure_ascii=False))
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": actual}
        mapping["receipt"] = {"path": "native.json",
                              "sha256": self.core.digest(self.receipt.read_bytes())}
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(mapping)
        self.assertEqual(held.exception.reason, "finance_profile_evidence_invalid")
        self.assertEqual(before, self._tree_bytes(self.root))


if __name__ == "__main__":
    sys.exit(__import__("unittest").main())
