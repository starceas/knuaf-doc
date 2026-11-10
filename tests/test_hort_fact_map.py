"""The horticulture cell map is one common gg-xlsx-fact-map/v1 file.

The map lists the body Ⅴ chapter's core monetary figures and the 18-sheet cell,
period and unit each one comes from.  These tests lock four things: the exact
twenty (fact_id, sheet, cell) pairs and the value each one registers, the map
review gate that refuses a ratio cell or an excluded cell even when the
workbook carries a numeric cache there, byte-exact hash binding of workbook and
receipt, and the dedicated horticulture receipt chain.

The native manifest used here is a synthetic fixture, not Excel execution; the
real recalculation is exercised outside the suite.
"""
import json
import re
import sys
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from tests._harness import ContractCase, claim_of, fact_op, runtime, source_op, write_text

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "skills/knuaf-doc/references/hort-env-systems"
MAP_PATH = REF / "finance-fact-map.json"
PARAMS_PATH = REF / "finance-params.json"
CELL = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$")
SESSION = {"app": "excel", "reason": "closed_confirmed",
           "cleanup": "closed", "save_state": "saved"}

# The reviewed map, frozen independently of the file under test: a swapped or
# dropped row must fail even though the file itself stays self-consistent.
EXPECTED_ROWS = (
    ("hort-invest-total", "4.투자계획", "C13", "천원"),
    ("hort-invest-year1", "4.투자계획", "C8", "천원"),
    ("hort-loan-principal-2027", "5. 원리금상환계획", "D8", "천원"),
    ("hort-loan-interest-2027", "5. 원리금상환계획", "E8", "천원"),
    ("hort-facility-construction", "6.시설계획", "C76", "천원"),
    ("hort-sales-2027", " 7. 판매계획", "O25", "천원"),
    ("hort-sales-2028", " 7. 판매계획", "O34", "천원"),
    ("hort-sales-2029", " 7. 판매계획", "O43", "천원"),
    ("hort-sales-2030", " 7. 판매계획", "O52", "천원"),
    ("hort-sales-2031", " 7. 판매계획", "O61", "천원"),
    ("hort-material-2027", "9. 영농자재구매계획", "G15", "천원"),
    ("hort-labor-2027", "11. 인건비계획", "I17", "천원"),
    ("hort-overhead-2027", "12 .경비계획", "C26", "천원"),
    ("hort-depreciation-2027", "13. 감가상각비계획 ", "E12", "천원"),
    ("hort-expense-2027", "14. 생산원가계획", "C12", "천원"),
    ("hort-production-cost-2027", "14. 생산원가계획", "C25", "천원"),
    ("hort-revenue-2027", "15. 손익계획", "C6", "천원"),
    ("hort-income-2027", "16. 수익성분석", "G23", "천원"),
    ("hort-net-income-2027", "16. 수익성분석", "G24", "천원"),
    ("hort-assets-opening", "17. 추정대차대조표", "C30", "천원"),
)

# One distinct cache value per row, so a swapped pair changes two values.
EXPECTED_VALUES = {row[0]: 1000 + 7 * index
                   for index, row in enumerate(EXPECTED_ROWS)}


def _map():
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def _params():
    return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["parameters"]


def _inject_caches(path, cells):
    """Write plain numeric caches (no ``t`` attribute) into an xlsx in place."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    workbook = ET.fromstring(members["xl/workbook.xml"])
    rels = ET.fromstring(members["xl/_rels/workbook.xml.rels"])
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    sheets = {}
    for sheet in workbook.find(f"{{{namespace}}}sheets"):
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets[rid].lstrip("/")
        name = target if target.startswith("xl/") else "xl/" + target
        sheets[sheet.get("name")] = name
    for (sheet, ref), value in cells.items():
        member = sheets[sheet]
        xml = ET.fromstring(members[member])
        data = xml.find(f"{{{namespace}}}sheetData")
        row_number = int(re.search(r"[0-9]+", ref).group())
        row = None
        for candidate in data.findall(f"{{{namespace}}}row"):
            if int(candidate.get("r")) == row_number:
                row = candidate
                break
        if row is None:
            row = ET.SubElement(data, f"{{{namespace}}}row", {"r": str(row_number)})
        for candidate in list(row.findall(f"{{{namespace}}}c")):
            if candidate.get("r") == ref:
                row.remove(candidate)
        cell = ET.SubElement(row, f"{{{namespace}}}c", {"r": ref})
        ET.SubElement(cell, f"{{{namespace}}}v").text = str(value)
        rows = row.findall(f"{{{namespace}}}c")
        for position, candidate in enumerate(rows):
            row.remove(candidate)
        rows.sort(key=lambda item: (int(re.sub(r"[A-Z]", "", item.get("r"))),
                                    item.get("r")))
        for candidate in rows:
            row.append(candidate)
        members[member] = ET.tostring(xml, encoding="utf-8",
                                      xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


class MapFileTests(ContractCase):
    def setUp(self):
        self.map = _map()
        self.facts = runtime("gg_excel_facts")
        self.hort = runtime("gg_hort_workbook")

    def test_rows_name_real_sheets_cells_and_body_nodes(self):
        self.assertEqual(self.map["schema"], self.facts.MAP_SCHEMA)
        profile = json.loads((REF / "profile.json").read_text(encoding="utf-8"))
        roster = profile["workbook_reference"]["sheets"]
        nodes = {section["item"] for section in profile["precedents"][1]["sections"]}
        cells = self.map["cells"]
        self.assertEqual(len(cells), 20)
        self.assertEqual(len({row["fact_id"] for row in cells}), len(cells))
        self.assertEqual(len({(row["sheet"], row["cell"]) for row in cells}),
                         len(cells))
        for row in cells:
            with self.subTest(row=row["fact_id"]):
                self.assertTrue(CELL.fullmatch(row["cell"]))
                self.assertIn(row["sheet"], roster)
                self.assertIn(row["unit"], {"원", "천원"})
                self.assertIn(row["node"], nodes)
                self.assertTrue(row["body_label"].strip())
                self.assertTrue(re.fullmatch(r"\d{4}", row["period"]))

    def test_field_ids_are_the_body_labels_not_machine_identifiers(self):
        # The body crosscheck looks the row up by field_id in prose, so a
        # machine identifier (finance.invest_total) can never be found: the
        # fact would be reported blocked instead of checked.  The fruit map
        # already ships body labels; horticulture follows it.
        school = runtime("gg_school_excel")
        for row in self.map["cells"]:
            with self.subTest(row=row["fact_id"]):
                self.assertEqual(row["field_id"], row["body_label"])
                self.assertIsNone(
                    school._MACHINE_FIELD_ID.match(row["field_id"]))

    def test_map_is_exactly_the_reviewed_row_set(self):
        shipped = {(row["fact_id"], row["sheet"], row["cell"], row["unit"])
                   for row in self.map["cells"]}
        self.assertEqual(set(EXPECTED_ROWS), shipped)

    def test_map_passes_the_review_gate(self):
        rows = self.hort.check_fact_map(self.map, _params())
        self.assertEqual(len(rows), len(EXPECTED_ROWS))

    def test_units_stay_monetary_and_ratios_are_excluded(self):
        excluded = {(row["sheet"], row["range"])
                    for row in self.map["excluded_series"]}
        self.assertIn(("16. 수익성분석", "G27:K27"), excluded)
        self.assertIn(("14. 생산원가계획", "C28:G28"), excluded)
        for row in self.map["cells"]:
            with self.subTest(row=row["fact_id"]):
                self.assertEqual(row["unit"], "천원")

    def test_reviewed_cells_never_inside_an_excluded_series(self):
        for row in self.map["cells"]:
            for span in self.map["excluded_series"]:
                if span["sheet"] != row["sheet"]:
                    continue
                with self.subTest(row=row["fact_id"], span=span["range"]):
                    self.assertFalse(_inside(span["range"], row["cell"]))


def _inside(span, cell):
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?",
                         span)
    low, first, high, last = match.groups()
    high = high or low
    last = last or first
    column, row = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell).groups()
    return (min(low, high) <= column <= max(low, high)
            and int(first) <= int(row) <= int(last))


class _ChainCase(ContractCase):
    """Synthetic native chain shared by the registration and binding cases."""

    # (value, answer_state) of the synthetic ``farm_name`` fact, registered
    # before the transform so every later step sees an ordinary project.
    FARM_NAME = ("합성농장", "provided")

    def setUp(self):
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.hort = runtime("gg_hort_workbook")
        self.root = self.make_project()
        self.chain = self.prepare_chain(self.root, plant=True)

    def prepare_chain(self, root, *, plant=False, source=None):
        """Fresh transform/native chain over ``root``; returns the chain.

        A recovery run needs a second, independent folder, so the whole
        fixture (canonical copy, farm name, transform, native workbook) is
        built per root rather than baked into setUp.  ``source`` re-runs the
        chain over an existing workbook — recovery registers the student's
        own file, not a freshly generated stand-in.
        """
        from tests.test_hort_finance_pipeline import Chain, _docs, _native_workbook
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        if source is not None:
            chain.source = Path(source)
            chain.sha = self.hort.file_digest(chain.source)
            chain.docs = _docs(chain.roster[0], chain.sha)
            for target, value in (("SOURCE_SHA", chain.sha),
                                  ("contracts", lambda: chain.docs)):
                patcher = mock.patch.object(self.hort, target, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        (root / "project.json").write_bytes(
            (chain.root / "project.json").read_bytes())
        chain.root = root
        if self.FARM_NAME is not None:
            self.register_farm_name(root, *self.FARM_NAME)
        if plant:
            self.plant_before_transform()
        chain._run_to_transform()
        _native_workbook(chain.native, chain.roster,
                         chain.docs[self.hort.FILES[1]]["parameters"],
                         chain.facts)
        return chain

    def register_farm_name(self, root, value, answer_state="provided"):
        """Register the project's ``farm_name`` through the ordinary transaction."""
        write_text(root, "farm-answer.txt", "농장명\n")
        fact = fact_op("farm_name", "farm_name", value, "",
                       answer_state=answer_state, source_id="farm-answer")
        source = source_op("farm-answer.txt")
        source["value"]["id"] = "farm-answer"
        self.core.apply(root, {"request_id": "farm-name", "ops": [source, fact]},
                        self.core.load(root)["revision"])

    def plant_fact(self, root, fid, field_id, value, *, scope, unit="천원"):
        """Plant a previous-registration-shaped fact before the transform.

        Registering after a real transform makes the hort receipt stale, so a
        pre-existing fact can only be created before that run.
        """
        write_text(root, fid + "-answer.txt", fid + "\n")
        fact = fact_op(fid, field_id, value, unit, scope=scope,
                       source_id=fid + "-answer", claim_id="c")
        source = source_op(fid + "-answer.txt",
                           claims=claim_of(fact["value"], "c"))
        source["value"]["id"] = fid + "-answer"
        self.core.apply(root, {"request_id": "plant:" + fid,
                               "ops": [source, fact]},
                        self.core.load(root)["revision"])

    def plant_before_transform(self):
        """Hook for cases that need a fact to exist before the native run."""

    def reviewed_caches(self, overrides=None):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]]
                  for row in EXPECTED_ROWS}
        caches.update(overrides or {})
        return caches

    def single_row(self, fact_id="hort-invest-total", overrides=None):
        """Stage the native run and return a mapping holding one row."""
        hashes = self.issue_native(self.reviewed_caches(overrides))
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == fact_id]
        return mapping

    def fact_state(self):
        project = self.core.load(self.root)
        return {fid: {key: fact.get(key) for key in
                      ("value", "field_id", "scope", "revision")}
                for fid, fact in project["facts"].items()}

    def issue_native(self, caches, chain=None):
        chain = chain or self.chain
        _inject_caches(chain.native, caches)
        chain._issue_native()
        return self.stage(chain=chain)

    def stage(self, record=None, *, chain=None):
        chain = chain or self.chain
        root = chain.root
        for source, name in ((chain.native, "recalc.xlsx"),
                             (chain.pdf, "recalc.pdf")):
            (root / name).write_bytes(source.read_bytes())
        if record is None:
            record = json.loads(chain.native_rec.read_text(encoding="utf-8"))
        record["published_office"] = str((root / "recalc.xlsx").resolve())
        record["published_pdf"] = str((root / "recalc.pdf").resolve())
        record["engine_result"] = "GG_OFFICE_SESSION|closed|saved|synthetic"
        record["office_session"] = dict(SESSION)
        receipt = root / "native.json"
        receipt.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                      indent=2), encoding="utf-8")
        return {"workbook": self.core.digest((root / "recalc.xlsx").read_bytes()),
                "receipt": self.core.digest(receipt.read_bytes())}

    def bound(self, hashes, **overrides):
        mapping = _map()
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": hashes["workbook"]}
        mapping["receipt"] = {"path": "native.json", "sha256": hashes["receipt"]}
        mapping.update(overrides)
        return mapping

    def build(self, mapping, *, root=None, major="hort_env_systems"):
        root = root or self.root
        return self.facts.build_registration(
            root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan",
            context=self.mc.output_context(root, major))

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


class RegistrationTests(_ChainCase):
    """A real registration: the exact cell each reviewed row reads."""

    def test_every_reviewed_row_reads_its_own_cell(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        change = self.build(self.bound(hashes))
        got = self.registered(change)
        want = {fact_id: (sheet, cell, str(value))
                for fact_id, sheet, cell, _ in EXPECTED_ROWS
                for value in (EXPECTED_VALUES[fact_id],)}
        self.assertEqual(want, got)
        self.assertEqual(len(change["ops"]), 21)

    def test_registered_scope_is_the_student_farm_name(self):
        # The map keeps the placeholder ``student_farm``; the adapter replaces
        # it with the project's own farm name when it registers the fact.
        change = self.build(self.single_row())
        self.assertEqual({op["value"]["scope"] for op in change["ops"]
                          if op["collection"] == "facts"}, {self.FARM_NAME[0]})
        self.core.apply(self.root, change, self.core.load(self.root)["revision"])
        project = self.core.load(self.root)
        self.assertEqual(project["facts"]["hort-invest-total"]["scope"],
                         self.FARM_NAME[0])

    def test_swapped_rows_do_not_register_the_reviewed_values(self):
        # The same cache set, but the two investment rows exchanged in the map:
        # structure still passes, so only the value binding can catch it.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        rows = mapping["cells"]
        first = next(index for index, row in enumerate(rows)
                     if row["fact_id"] == "hort-invest-total")
        second = next(index for index, row in enumerate(rows)
                      if row["fact_id"] == "hort-invest-year1")
        rows[first]["cell"], rows[second]["cell"] = (
            rows[second]["cell"], rows[first]["cell"])
        got = self.registered(self.build(mapping))
        self.assertEqual(got["hort-invest-total"][2], str(EXPECTED_VALUES["hort-invest-year1"]))
        self.assertNotEqual(got["hort-invest-total"][2],
                            str(EXPECTED_VALUES["hort-invest-total"]))


class FarmNameBoundaryTests(_ChainCase):
    """The farm name is required only for the horticulture placeholder."""

    FARM_NAME = None

    def test_absent_farm_name_is_held_before_any_op(self):
        self.held(self.single_row(), "farm_name_required")


class BlankFarmNameTests(_ChainCase):
    """A provided-but-blank name is no name: the scope may not be whitespace."""

    FARM_NAME = None

    def plant_before_transform(self):
        self.register_farm_name(self.root, "   ")

    def test_blank_farm_name_is_held(self):
        self.held(self.single_row(), "farm_name_required")


class NotProvidedFarmNameTests(_ChainCase):
    """A fact that records "not provided" carries no usable name either."""

    FARM_NAME = None

    def plant_before_transform(self):
        self.register_farm_name(self.root, None, "not_provided")

    def test_farm_name_fact_that_is_not_provided_is_held(self):
        self.assertEqual(
            self.core.load(self.root)["facts"]["farm_name"]["answer_state"],
            "not_provided")
        self.held(self.single_row(), "farm_name_required")


class OtherMajorPlaceholderTests(ContractCase):
    """A ``student_farm`` row in another major's map stays literal."""

    def setUp(self):
        from openpyxl import Workbook
        self.core = runtime("gg_core")
        self.mc = runtime("gg_major_contract")
        self.facts = runtime("gg_excel_facts")
        self.root = self.make_project()
        from tests._harness import bind_major
        bind_major(self.root)  # specialty_crops
        self.workbook = self.root / "recalc.xlsx"
        book = Workbook()
        book.active.title = "계획"
        book.active["A1"] = 250  # already-cached value; no engine in CI
        book.save(self.workbook)
        book.close()
        self.profile = "school_17_sheet_v1"
        actual = self.core.digest(self.workbook.read_bytes())
        self.native = {
            "status": "converted", "engine": "Microsoft Excel",
            "engine_result": "GG_OFFICE_SESSION|closed|saved|synthetic",
            "major_authorization": [self.mc.authorize_output(
                self.mc.OUTPUT_SCHOOL_WORKBOOK,
                self.mc.output_context(self.root, "specialty_crops")).to_dict()],
            "office_session": dict(SESSION),
            "published_office_sha256": actual,
            "validation": {"workbook_profile": self.profile,
                           "finance_profile": self.profile,
                           "structure_issues": [], "school_issues": [],
                           "excel": {"valid": True, "sha256": actual}}}
        receipt = self.root / "native.json"
        receipt.write_text(json.dumps(self.native, ensure_ascii=False))

    def build(self, scope):
        actual = self.core.digest(self.workbook.read_bytes())
        receipt = self.root / "native.json"
        mapping = {
            "schema": self.facts.MAP_SCHEMA, "workbook_profile": self.profile,
            "workbook": {"sha256": actual},
            "receipt": {"sha256": self.core.digest(receipt.read_bytes())},
            "cells": [{"fact_id": "sales", "field_id": "매출액", "sheet": "계획",
                       "cell": "A1", "period": "2027", "unit": "천원",
                       "scope": scope}]}
        return self.facts.build_registration(
            self.root, "recalc.xlsx", "native.json", mapping,
            source_id="excel-plan",
            context=self.mc.output_context(self.root, "specialty_crops"))

    def test_student_farm_placeholder_is_not_replaced_for_specialty_crops(self):
        # Substitution is gated on the horticulture major, so the same literal
        # row in another major's map registers exactly as before.
        change = self.build("student_farm")
        self.assertEqual(change["ops"][1]["value"]["scope"], "student_farm")

    def test_absent_farm_name_does_not_block_another_major(self):
        # No farm name exists in this project at all; registration must not
        # start demanding one merely because the map uses the placeholder.
        self.assertNotIn("farm_name", self.core.load(self.root)["facts"])
        self.build("student_farm")

    def test_legacy_shape_outside_horticulture_keeps_the_ordinary_reason(self):
        # The dedicated reason tells a horticulture student their old
        # registration cannot simply be redone.  Another major has no such
        # history, so a changed amount stays an ordinary conflict.
        write_text(self.root, "old.txt", "매출액\n")
        fact = fact_op("sales", "finance.sales", "100", "천원", scope="계획",
                       source_id="old", claim_id="c")
        source = source_op("old.txt", claims=claim_of(fact["value"], "c"))
        source["value"]["id"] = "old"
        self.core.apply(self.root, {"request_id": "plant:old",
                                    "ops": [source, fact]},
                        self.core.load(self.root)["revision"])
        # The native receipt records the grant for the revision it was issued
        # against; the planted fact moved that revision, so re-issue the same
        # synthetic grant and receipt before the run under test.
        self.native["major_authorization"] = [self.mc.authorize_output(
            self.mc.OUTPUT_SCHOOL_WORKBOOK,
            self.mc.output_context(self.root, "specialty_crops")).to_dict()]
        (self.root / "native.json").write_text(
            json.dumps(self.native, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(self.facts.RegistrationHeld,
                                    "existing_fact_conflict"):
            self.build("student_farm")


class SpecialCharacterFarmNameTests(_ChainCase):
    """A regex-special farm name is data, never a pattern."""

    FARM_NAME = ("별빛(主)농장.+*?", "provided")

    def setUp(self):
        super().setUp()
        self.school = runtime("gg_school_excel")
        self.change = self.build(self.single_row())
        self.core.apply(self.root, self.change,
                        self.core.load(self.root)["revision"])
        self.project = self.core.load(self.root)
        self.items, blocked = self.core._finance_check_items(self.project)
        self.assertIsNone(blocked)

    def body(self, farm):
        return (f"{farm}의 2027년 주요 재무 값은 표 1에 나타내었다.\n\n"
                "표 1. 2027년 주요 재무 값\n\n"
                "| 항목 | 2027년 |\n|---|---|\n"
                f"| 총투자비 합계 | {EXPECTED_VALUES['hort-invest-total']} |\n\n"
                "주) 단위: 천원.\n")

    def test_scope_is_stored_verbatim(self):
        self.assertEqual(self.project["facts"]["hort-invest-total"]["scope"],
                         self.FARM_NAME[0])
        self.assertEqual(self.items[0]["scope"], self.FARM_NAME[0])

    def test_table_with_the_exact_farm_name_passes(self):
        self.assertEqual(self.school.crosscheck_body(self.body(self.FARM_NAME[0]),
                                                     self.items), [])

    def test_regex_metacharacters_are_not_matched_as_patterns(self):
        changed = self.body(self.FARM_NAME[0]).replace(
            self.FARM_NAME[0], "별빛X主X농장.+*?")
        self.assertTrue(self.school.crosscheck_body(changed, self.items))
        # The parentheses removed but the rest kept: an unescaped pattern
        # would still match, so this pins re.escape, not just inequality.
        stripped = self.body(self.FARM_NAME[0]).replace(
            self.FARM_NAME[0], "별빛主농장.+*?")
        self.assertTrue(self.school.crosscheck_body(stripped, self.items))


class ConsumerTests(_ChainCase):
    """The registered fact is what the body crosscheck reads."""

    def register(self, **overrides):
        mapping = self.single_row(overrides=overrides)
        self.core.apply(self.root, self.build(mapping),
                        self.core.load(self.root)["revision"])
        items, blocked = self.core._finance_check_items(self.core.load(self.root))
        self.assertIsNone(blocked)
        return items

    def test_table_body_with_the_registered_amount_passes(self):
        items = self.register()
        text = ("합성농장의 2027년 주요 재무 값은 표 1에 나타내었다.\n\n"
                "표 1. 2027년 주요 재무 값\n\n"
                "| 항목 | 2027년 |\n|---|---|\n"
                f"| 총투자비 합계 | {EXPECTED_VALUES['hort-invest-total']} |\n\n"
                "주) 단위: 천원.\n")
        self.assertEqual(runtime("gg_school_excel").crosscheck_body(text, items), [])

    def test_table_body_with_another_amount_fails(self):
        items = self.register()
        text = ("합성농장의 2027년 주요 재무 값은 표 1에 나타내었다.\n\n"
                "표 1. 2027년 주요 재무 값\n\n"
                "| 항목 | 2027년 |\n|---|---|\n"
                f"| 총투자비 합계 | {EXPECTED_VALUES['hort-invest-total'] + 1} |\n\n"
                "주) 단위: 천원.\n")
        self.assertTrue(runtime("gg_school_excel").crosscheck_body(text, items))


class RepeatedLabelConsumerTests(_ChainCase):
    """Five rows share the label 판매금액; only the period tells them apart.

    Registering two years at once proves the body crosscheck keys the claim on
    ``fact_id``/period rather than the display label: a table holding both
    years passes, and exchanging the two amounts fails both years.
    """

    YEARS = ("hort-sales-2027", "hort-sales-2028")

    def register(self):
        hashes = self.issue_native(self.reviewed_caches())
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] in self.YEARS]
        self.core.apply(self.root, self.build(mapping),
                        self.core.load(self.root)["revision"])
        items, blocked = self.core._finance_check_items(self.core.load(self.root))
        self.assertIsNone(blocked)
        self.assertEqual({item["fact_id"] for item in items}, set(self.YEARS))
        return items

    def table(self, first, second):
        return ("합성농장의 판매계획은 표 1에 나타내었다. 단위: 천원.\n\n"
                "표 1. 판매계획\n\n"
                "| 항목 | 2027년 | 2028년 |\n|---|---|---|\n"
                f"| 판매금액 | {first} | {second} |\n\n")

    def test_two_years_of_the_same_label_pass_with_their_own_amounts(self):
        items = self.register()
        text = self.table(EXPECTED_VALUES[self.YEARS[0]],
                          EXPECTED_VALUES[self.YEARS[1]])
        self.assertEqual(runtime("gg_school_excel").crosscheck_body(text, items), [])

    def test_exchanged_years_fail_even_though_both_amounts_are_present(self):
        items = self.register()
        text = self.table(EXPECTED_VALUES[self.YEARS[1]],
                          EXPECTED_VALUES[self.YEARS[0]])
        issues = runtime("gg_school_excel").crosscheck_body(text, items)
        self.assertEqual({issue["fact_id"] for issue in issues}, set(self.YEARS))


class RepeatedClaimInClauseTests(_ChainCase):
    """One clause restates the same claim twice (W-D3 @Roh 중요①).

    Every claim in the clause must be checked: a first correct amount must
    not silence a second, different one, while restating the same amount
    still passes.
    """

    FACT = "hort-sales-2027"

    def register(self):
        mapping = self.single_row(fact_id=self.FACT)
        self.core.apply(self.root, self.build(mapping),
                        self.core.load(self.root)["revision"])
        items, blocked = self.core._finance_check_items(self.core.load(self.root))
        self.assertIsNone(blocked)
        return [item for item in items if item["fact_id"] == self.FACT]

    def prose(self, first, second):
        return (f"합성농장의 2027년 판매금액은 {first:,}천원이고 "
                f"합성농장의 2027년 판매금액은 {second:,}천원이다.\n")

    def test_second_different_amount_in_same_clause_fails(self):
        items = self.register()
        good = EXPECTED_VALUES[self.FACT]
        text = self.prose(good, good + 10_000)
        issues = runtime("gg_school_excel").crosscheck_body(text, items)
        self.assertEqual({issue["fact_id"] for issue in issues}, {self.FACT})

    def test_same_amount_restated_in_same_clause_passes(self):
        items = self.register()
        good = EXPECTED_VALUES[self.FACT]
        text = self.prose(good, good)
        self.assertEqual(runtime("gg_school_excel").crosscheck_body(text, items),
                         [])


class LegacyConflictTests(_ChainCase):
    """A v0.4.7 registration is told apart from an ordinary value change."""

    def plant_before_transform(self):
        self.plant_fact(self.root, "hort-invest-total", "finance.invest_total",
                        "999", scope="student_farm")

    def test_legacy_shape_conflict_has_its_own_reason(self):
        project = self.root / "project.json"
        digest = self.core.digest(project.read_bytes())
        facts = self.fact_state()
        self.held(self.single_row(), "legacy_hort_fact_conflict")
        self.assertEqual(self.core.digest(project.read_bytes()), digest)
        self.assertEqual(self.fact_state(), facts)


class LegacyConflictWithoutFarmNameTests(_ChainCase):
    """A v0.4.7 folder has no farm name; the legacy reason still wins.

    v0.4.7 never wrote a ``farm_name`` fact, so the farm-name demand would
    otherwise be the only thing reported and would send the student to add a
    name that cannot resolve the real conflict.
    """

    FARM_NAME = None

    def plant_before_transform(self):
        self.plant_fact(self.root, "hort-invest-total", "finance.invest_total",
                        "999", scope="student_farm")

    def test_legacy_reason_precedes_the_farm_name_demand(self):
        self.held(self.single_row(), "legacy_hort_fact_conflict")


class LegacyPlaceholderScopeTests(_ChainCase):
    """The placeholder alone marks a v0.4.7 fact, even with the body label.

    A partial migration could have renamed ``field_id`` and kept the literal
    ``student_farm`` scope; without a farm name the demand would otherwise
    hide the conflict again.
    """

    FARM_NAME = None

    def plant_before_transform(self):
        self.plant_fact(self.root, "hort-invest-total", "총투자비 합계",
                        "999", scope="student_farm")

    def test_placeholder_scope_alone_is_a_legacy_conflict(self):
        self.held(self.single_row(), "legacy_hort_fact_conflict")


class LegacyConflictAfterReceiptTests(_ChainCase):
    """The real second registration: the stale receipt must not hide it.

    The v0.4.7 fact is applied after the native run, which is the order a
    student actually produces; the old transform receipt is then stale.  The
    refusal must still be the conflict, and it must be the adapter's own
    hold rather than the receipt chain's ``hort_transform_receipt_invalid``.
    """

    def register_legacy_fact(self):
        write_text(self.root, "legacy.txt", "총투자비\n")
        fact = fact_op("hort-invest-total", "finance.invest_total", "999",
                       "천원", scope="student_farm", source_id="legacy",
                       claim_id="c")
        source = source_op("legacy.txt", claims=claim_of(fact["value"], "c"))
        source["value"]["id"] = "legacy"
        self.core.apply(self.root, {"request_id": "legacy-registration",
                                    "ops": [source, fact]},
                        self.core.load(self.root)["revision"])

    def test_conflict_precedes_the_stale_receipt(self):
        self.register_legacy_fact()
        mapping = self.single_row()
        before = self._tree_bytes(self.root)
        with self.assertRaises(self.facts.RegistrationHeld) as held:
            self.build(mapping)
        self.assertEqual(held.exception.reason, "legacy_hort_fact_conflict")
        self.assertEqual(before, self._tree_bytes(self.root))


class ExistingConflictTests(_ChainCase):
    """A v0.4.8-shaped fact with a different value keeps the old reason.

    The previous fact is planted before the transform so the receipt stays
    valid; the shape planted here is what the first v0.4.8 run writes — body
    label plus the student's farm name — so only the amount differs and the
    value comparison, not the legacy scan, must refuse it.  A real second
    registration in the same folder is covered by
    ``LegacyConflictAfterReceiptTests``.
    """

    def plant_before_transform(self):
        self.plant_fact(self.root, "hort-invest-total", "총투자비 합계",
                        "999", scope="합성농장")

    def test_value_change_keeps_existing_fact_conflict(self):
        self.held(self.single_row(), "existing_fact_conflict")


class RecoveryTests(_ChainCase):
    """Option B: register in a fresh folder, leaving the old one untouched.

    The starting point is the real blocked case — a folder that already holds
    a v0.4.7 registration — so the fresh run is proved against an actual
    refusal, and the original folder is compared byte for byte afterwards.
    """

    def plant_before_transform(self):
        self.plant_fact(self.root, "hort-invest-total", "finance.invest_total",
                        "999", scope="student_farm")

    def setUp(self):
        super().setUp()
        self.old_state = self.fact_state()
        self.old_project = self.core.digest(
            (self.root / "project.json").read_bytes())

    def test_a_fresh_folder_registers_the_same_workbook(self):
        # The original folder really is blocked, and the refusal itself left
        # every artifact byte-unchanged.
        self.held(self.single_row(), "legacy_hort_fact_conflict")
        blocked_tree = self._tree_bytes(self.root)
        self.assertEqual(self.core.digest(
            (self.root / "project.json").read_bytes()), self.old_project)

        fresh = self.make_project()
        # The student's own workbook, byte for byte — not a new stand-in.
        student = fresh / "student.xlsx"
        student.write_bytes(self.chain.source.read_bytes())
        chain = self.prepare_chain(fresh, plant=False, source=student)
        hashes = self.issue_native(self.reviewed_caches(), chain=chain)
        mapping = self.bound(hashes)
        mapping["cells"] = [row for row in mapping["cells"]
                            if row["fact_id"] == "hort-invest-total"]
        self.core.apply(fresh, self.build(mapping, root=fresh),
                        self.core.load(fresh)["revision"])
        fact = self.core.load(fresh)["facts"]["hort-invest-total"]
        self.assertEqual((fact["scope"], fact["value"]),
                         (self.FARM_NAME[0],
                          str(EXPECTED_VALUES["hort-invest-total"])))
        # The original folder is exactly as it was before recovery.
        self.assertEqual(self.fact_state(), self.old_state)
        self.assertEqual(self._tree_bytes(self.root), blocked_tree)


class MapGateTests(ContractCase):
    """The review gate refuses what the adapter alone cannot see."""

    def setUp(self):
        self.hort = runtime("gg_hort_workbook")
        self.params = _params()

    def gate(self, mapping):
        return self.hort.check_fact_map(mapping, self.params)

    def test_ratio_cell_disguised_as_money_is_refused(self):
        # 16!G27 is 농가수익률 (0%): a numeric cache 0.25 exists there, and the
        # row claims 천원, so only the map's unit/exclusion review can stop it.
        mapping = _map()
        for row in mapping["cells"]:
            if row["fact_id"] == "hort-net-income-2027":
                row["cell"] = "G27"
        with self.assertRaisesRegex(self.hort.Held, "fact_map_excluded_cell"):
            self.gate(mapping)

    def test_registry_ratio_cell_disguised_as_money_is_refused(self):
        # 7!O75 is sales.y1.marketability_rate (RATIO in the registry); the map
        # row says 천원 while a numeric cache 0.25 sits in the cell.
        mapping = _map()
        mapping["cells"] = [{"fact_id": "hort-marketability", "field_id": "finance.rate",
                             "sheet": " 7. 판매계획", "cell": "O75", "period": "2027",
                             "scope": "student_farm", "unit": "천원",
                             "body_label": "상품화율", "node": "ch5_sec3"},
                            *mapping["cells"]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_unit_mismatch"):
            self.gate(mapping)

    def test_duplicate_row_is_refused(self):
        mapping = _map()
        mapping["cells"] = [*mapping["cells"],
                            dict(mapping["cells"][0], fact_id="hort-duplicate")]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_row_conflict"):
            self.gate(mapping)

    def test_unknown_sheet_or_cell_is_refused(self):
        mapping = _map()
        mapping["cells"] = [dict(mapping["cells"][0], sheet="19. 없는시트"),
                            *mapping["cells"][1:]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_sheet_unknown"):
            self.gate(mapping)
        mapping = _map()
        mapping["cells"] = [dict(mapping["cells"][0], cell="C13:D13"),
                            *mapping["cells"][1:]]
        with self.assertRaisesRegex(self.hort.Held, "fact_map_cell_invalid"):
            self.gate(mapping)


class BindingTests(_ChainCase):
    """Hash binding and the horticulture receipt chain, not just the map shape."""

    def test_shipped_map_is_held_until_a_run_binds_its_hashes(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        self.held(_map(), "fact_map_hash_mismatch")

    def test_foreign_schema_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, schema="gg-xlsx-fact-map-draft/v1"),
                  "fact_map_schema_invalid")

    def test_missing_cells_are_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, cells=[]), "cell_mapping_required")

    def test_foreign_profile_label_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        self.held(self.bound(hashes, workbook_profile="school_17_sheet_v1"),
                  "fact_map_profile_mismatch")

    def test_registration_refuses_a_ratio_cell_with_a_numeric_cache(self):
        # 16!G27 is 농가수익률; a real 0.25 cache sits there, the row claims
        # 천원 and the hashes are rebound, so only the map gate inside the
        # registration path can refuse it.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        caches[("16. 수익성분석", "G27")] = 0.25
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        next(row for row in mapping["cells"]
             if row["fact_id"] == "hort-net-income-2027")["cell"] = "G27"
        self.held(mapping, "fact_map_excluded_cell")

    def test_registration_refuses_a_registry_ratio_cell(self):
        # 5!F39 is loan.slot1.annual_rate (RATIO in the registry); the row
        # claims 천원 and a numeric cache 0.015 sits in the cell.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        caches[("5. 원리금상환계획", "F39")] = 0.015
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["cells"] = [{"fact_id": "hort-loan-rate", "field_id": "finance.rate",
                             "sheet": "5. 원리금상환계획", "cell": "F39", "period": "2027",
                             "scope": "student_farm", "unit": "천원"},
                            *mapping["cells"]]
        self.held(mapping, "fact_map_unit_mismatch")

    def test_wrong_workbook_hash_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["workbook"] = {"path": "recalc.xlsx", "sha256": "f" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_wrong_receipt_hash_is_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        mapping["receipt"] = {"path": "native.json", "sha256": "e" * 64}
        self.held(mapping, "fact_map_hash_mismatch")

    def test_workbook_bytes_changed_after_binding_are_held(self):
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        hashes = self.issue_native(caches)
        mapping = self.bound(hashes)
        (self.root / "recalc.xlsx").write_bytes(
            (self.root / "recalc.xlsx").read_bytes() + b"\0")
        self.held(mapping, "fact_map_hash_mismatch")

    def test_receipt_without_horticulture_chain_is_held(self):
        # General Excel save/close proof stays; the hort transform binding is
        # gone.  Re-hash the manifest and rebind the map so the hash gate is
        # not what refuses it.
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        record = json.loads((self.root / "native.json").read_text(encoding="utf-8"))
        record.pop("validation")
        hashes = self.stage(record)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(self.bound(hashes))
        self.assertEqual(held.exception.reason, "hort_transform_receipt_invalid")

    def test_foreign_project_chain_is_held(self):
        from tests.test_hort_finance_pipeline import Chain, _native_workbook
        caches = {(row[1], row[2]): EXPECTED_VALUES[row[0]] for row in EXPECTED_ROWS}
        self.issue_native(caches)
        other = Chain(methodName="test_full_chain_plan_transform_native_verify")
        other.setUp()
        self.addCleanup(other.doCleanups)
        other._run_to_transform()
        record = json.loads((self.root / "native.json").read_text(encoding="utf-8"))
        binding = dict(record["validation"]["template_receipt"])
        binding["path"] = str(other.transform_rec.resolve())
        binding["sha256"] = self.core.digest(other.transform_rec.read_bytes())
        record["validation"] = dict(record["validation"], template_receipt=binding,
                                    previous_receipt_sha256=binding["sha256"])
        hashes = self.stage(record)
        with self.assertRaises(self.mc.OutputHeldError) as held:
            self.build(self.bound(hashes))
        self.assertEqual(held.exception.reason, "hort_transform_receipt_invalid")


if __name__ == "__main__":
    sys.exit(__import__("unittest").main())
