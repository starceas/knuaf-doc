"""특용작물 17시트 2판 — 구조 감사 생성기·등록 계약 (ADDENDUM-1 §4 T1~T13).

두 판은 같은 `x01` 레이아웃을 쓰고 행·수식만 다르다. 2판은 등록 X01 원본
바이트가 트리에 없어 탐침(예시값 필요)을 돌릴 수 없으므로, 수식 감사 대신
**구조 감사**(`scope: structure_only`)를 생성기로 만든다. 이 시험은 그 감사가
손으로 쓰이지 않았음(T1·T12), 등록 계약이 읽는 필드만 담았음(T2·T13), 그리고
두 판의 등록·사본 판정이 서로를 넘보지 않음(T3~T7·T10)을 잠근다.

1판 회귀(T6·T7·T11)는 기존 논문 경로 그 자체다. 실측값은 트리 안 두 양식의
바이트에서만 나온다 — 교수·학생 파일은 열지 않는다.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from tests._harness import SCRIPTS, ContractCase, bind_major, runtime

SKILL = Path(SCRIPTS).parent

V1 = "finance-template.xlsx"
V2 = "finance-template-v2.xlsx"
V1_SHA = "6d3c9fe5e5992108a8bab18c0098edb0d808f0d14c099878a53939b17de88c7d"
V2_SHA = "76300305fdaf165bcce54fc3de2b476c91f4e8e368bc0455a8c69aa3eee9cd1d"
V2_AUDIT = "formula-audit/school_17_sheet_v2.json"
V2_AUDIT_SHA = "1b76232f89f70ffa12ecc05289787d4c63e226b3b230601fa30ca77d117bce5a"
X01_AUDIT_SHA = "785c2a157b8a774421624aa49d86f347ffc4c533051959cc822746bc6ebaecf5"
CASH_FLOW = "14. 현금흐름계획"
PRODUCTION = "6. 생산계획"
# The second edition's own marker cells (`EDITION_FORMULA_ADDRESSES`): one in
# the production plan, five in the cash flow. The first edition has none of
# them and five of its own in `6. 생산계획`.
V2_MARKERS = ((PRODUCTION, "H9"), (CASH_FLOW, "D24"), (CASH_FLOW, "E24"),
              (CASH_FLOW, "F24"), (CASH_FLOW, "G24"), (CASH_FLOW, "H24"))
V2_DIFF_CELLS = 61
GENERATOR = "gg_workbook_structure_audit.py"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _templates():
    return SKILL / "assets" / "templates"


class CopyMixin:
    """An openpyxl save is one of the defined transforms: values only.

    The copy lands in its own temporary directory, never beside a shipped
    asset, and is removed with the test.
    """

    def copy_of(self, path, changes=()):
        from openpyxl import load_workbook
        holder = tempfile.TemporaryDirectory(prefix="knuaf-copy-")
        self.addCleanup(holder.cleanup)
        book = load_workbook(path)
        for sheet, cell, value in changes:
            book[sheet][cell] = value
        out = Path(holder.name) / path.name
        book.save(out)
        book.close()
        return out


class GeneratorTests(ContractCase):
    """The audit is generated, and only the generator writes it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="knuaf-audit-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = _templates() / V2

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / GENERATOR), *map(str, argv)],
            capture_output=True, text=True, encoding="utf-8")

    def test_t1_two_generations_are_byte_identical(self):
        first, second = self.root / "a.json", self.root / "b.json"
        for out in (first, second):
            result = self._run("--ref", "school_17_sheet_v2",
                               "--source", self.source, "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(_sha(first), _sha(second))

    def test_t12_committed_audit_equals_the_generator(self):
        """`--check` compares bytes: the committed file was not edited."""
        committed = (SKILL / "references" / "common-workbooks"
                     / V2_AUDIT)
        self.assertEqual(_sha(committed), V2_AUDIT_SHA)
        result = self._run("--ref", "school_17_sheet_v2", "--source", self.source,
                           "--check", committed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matches the generator", result.stdout)
        drifted = self.root / "drifted.json"
        drifted.write_bytes(committed.read_bytes().replace(
            b'"structure_only"', b'"structure_only "'))
        self.assertEqual(self._run("--ref", "school_17_sheet_v2", "--source",
                                   self.source, "--check", drifted).returncode, 1)

    def test_t2_audit_binds_the_second_editions_bytes(self):
        doc = json.loads((SKILL / "references"
                          / "common-workbooks" / V2_AUDIT).read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], "knuaf-workbook-formula-audit/v1")
        self.assertEqual(doc["source_sha256"], V2_SHA)
        self.assertEqual(_sha(self.source), V2_SHA)

    def test_t13_audit_carries_no_values_or_findings(self):
        """A structure audit proves no defect and leaks no student value."""
        doc = json.loads((SKILL / "references"
                          / "common-workbooks" / V2_AUDIT).read_text(encoding="utf-8"))
        self.assertEqual(doc["scope"], "structure_only")
        forbidden = {"findings", "cached_value", "input_value", "example_value",
                     "computed_value"}
        def walk(node, trail=()):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(key, forbidden, f"forbidden key {key!r} at {trail!r}")
                    walk(value, trail + (key,))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, trail + (index,))
        walk(doc)
        # Every formula in the book is walked; none is evaluated against a
        # cached number because no probe input exists (scope: structure_only).
        coverage = doc["coverage"]
        self.assertEqual(len(doc["inventory"]), coverage["structural_formula_count"])
        # No probe input exists, so nothing is compared against a cached
        # number: the only counts are structural.
        self.assertEqual(0, coverage["cache_agreement_count"])
        self.assertTrue(doc["sheet_roles"])


class RegistrationTests(CopyMixin, ContractCase):
    """Two editions, one registration each — neither admits the other's copy."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="knuaf-editions-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tpl = runtime("gg_excel_template")
        self.school = runtime("gg_school_excel")
        self.templates = self.tpl._crop_label_table()["templates"]
        self.v1 = _templates() / V1
        self.v2 = _templates() / V2

    def test_t3_the_second_edition_is_registered_by_its_own_bytes(self):
        self.assertEqual(_sha(self.v2), V2_SHA)
        self.assertEqual(
            self.tpl.registered_template(self.tpl.read_template(self.v2), self.templates),
            ("school_17_sheet_v2", "registered"))

    def test_t5_a_crossed_audit_cannot_admit_the_other_edition(self):
        """The two editions differ in 61 formula cells; each audit refuses the
        other's bytes, in both directions."""
        only_v2 = {"school_17_sheet_v2": self.templates["school_17_sheet_v2"]}
        with self.assertRaisesRegex(ValueError, f"{V2_DIFF_CELLS} cells differ"):
            self.tpl.registered_template(self.tpl.read_template(self.v1), only_v2)
        only_v1 = {"X01": self.templates["X01"]}
        with self.assertRaisesRegex(ValueError, f"{V2_DIFF_CELLS} cells differ"):
            self.tpl.registered_template(self.tpl.read_template(self.v2), only_v1)

    def test_t6_the_bundled_first_edition_is_a_derived_x01_form(self):
        """The bundle ships the openpyxl-built first edition, not the
        registered X01 original (whose bytes are not in the tree), so the
        admission is `derived` — measured, not assumed.  The profile the map
        declares is the first edition all the same."""
        self.assertEqual(_sha(self.v1), V1_SHA)
        read = self.tpl.read_template(self.v1)
        self.assertEqual(read.layout, "x01")
        self.assertEqual(self.tpl.registered_template(read, self.templates),
                         ("X01", "derived"))
        mapping, values = self.tpl.crop_label_map(self.v1, "herbaceous")
        self.assertEqual(mapping["workbook_profile"], "school_17_sheet_v1")
        self.assertEqual(len(mapping["entries"]), 7)
        self.assertEqual(len(values["values"]), 7)
        self.assertIn("X01 derived", values["values"][0]["evidence_ref"]["locator"])

    def test_t7_a_first_edition_copy_stays_inside_the_first_edition(self):
        """Value-filling a first-edition copy keeps it `X01`; the same copy is
        never mistaken for the second edition, and formula drift still ends
        the admission entirely."""
        filled = self.copy_of(self.v1, [(PRODUCTION, "H8", 5520),
                                    (PRODUCTION, "H9", 3000)])
        self.assertNotEqual(_sha(filled), V1_SHA)
        self.assertEqual(
            self.tpl.registered_template(self.tpl.read_template(filled), self.templates),
            ("X01", "derived"))
        mapping, _ = self.tpl.crop_label_map(filled, "herbaceous")
        self.assertEqual(mapping["workbook_profile"], "school_17_sheet_v1")
        drift = self.copy_of(self.v1, [(CASH_FLOW, "D20", "=D9-D10")])
        with self.assertRaisesRegex(ValueError, "not a registered template"):
            self.tpl.registered_template(self.tpl.read_template(drift), self.templates)

    def test_t4_a_second_edition_copy_derives_from_its_own_audit(self):
        copy = self.copy_of(self.v2, [(PRODUCTION, "H7", 350)])
        read = self.tpl.read_template(copy)
        self.assertEqual(read.layout, "x01")
        self.assertEqual(
            self.tpl.registered_template(read, self.templates),
            ("school_17_sheet_v2", "derived"))
        mapping, values = self.tpl.crop_label_map(copy, "herbaceous")
        self.assertEqual(mapping["workbook_profile"], "school_17_sheet_v2")
        self.assertEqual(len(mapping["entries"]), 7)
        self.assertIn("school_17_sheet_v2 derived",
                      values["values"][0]["evidence_ref"]["locator"])

    def test_t5b_a_formula_drift_in_the_copy_is_refused(self):
        copy = self.copy_of(self.v2, [(CASH_FLOW, "D22", "=D9-D10")])
        with self.assertRaisesRegex(ValueError, "not a registered template"):
            self.tpl.registered_template(self.tpl.read_template(copy), self.templates)

    def test_the_two_editions_are_structurally_distinguishable(self):
        """"A copy of either edition declared as the other" fails the gate.

        This is what makes the declared profile trustworthy: the refused copy
        is the *same bytes* under the wrong label.
        """
        for source, profile, label in ((self.v2, "school_17_sheet_v2", "v2"),
                                       (self.v1, "school_17_sheet_v1", "v1")):
            with self.subTest(label=label):
                self.school.require_profile_structure(source, profile)
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.school.require_profile_structure(self.v1, "school_17_sheet_v2")
        with self.assertRaisesRegex(ValueError, "registered school_17_sheet_v2 form"):
            self.school.require_profile_structure(self.v2, "school_17_sheet_v1")


class StructureGateTests(ContractCase):
    """The declared profile decides the form; the form gates the route."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="knuaf-gate-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tpl = runtime("gg_excel_template")
        self.school = runtime("gg_school_excel")
        self.v1 = _templates() / V1
        self.v2 = _templates() / V2

    def test_t10_the_wage_map_declares_the_edition_it_read(self):
        for source, profile in ((self.v1, "school_17_sheet_v1"),
                                (self.v2, "school_17_sheet_v2")):
            with self.subTest(edition=source.name):
                mapping = self.tpl.wage_fill_map(source)
                self.assertEqual(mapping["workbook_profile"], profile)
                # The declaration is not a bare string: it is the form the
                # engine then demands, and both editions pass their own.
                self.school.require_profile_structure(source, profile)
                with self.assertRaises(ValueError):
                    other = ("school_17_sheet_v2" if profile == "school_17_sheet_v1"
                             else "school_17_sheet_v1")
                    self.school.require_profile_structure(source, other)

    def test_the_named_profiles_are_the_two_editions(self):
        self.assertEqual(self.school.PROFILES,
                         ("school_17_sheet_v1", "school_17_sheet_v2"))
        self.assertIsNotNone(self.school.profile_structure("school_17_sheet_v2"))
        # The archived first edition has no registered form of its own; it is
        # the specialty route's historical name and stays unchecked.
        self.assertIsNone(self.school.profile_structure("school_17_sheet_v1"))

    def test_specialty_major_declares_both_editions(self):
        contract = runtime("gg_major_contract")
        capability = contract.default_registry().resolve("specialty_crops")
        self.assertEqual(capability.workbook_profiles,
                         ("school_17_sheet_v1", "school_17_sheet_v2"))
        declared = json.loads(self.school.WORKBOOK_REFERENCE_SET.read_text(
            encoding="utf-8"))
        self.assertIn("school_17_sheet_v2",
                      {entry["profile"] for entry in declared["workbook_profiles"]})


class EditionTests(ContractCase):
    """What the second edition actually changed, read from the shipped bytes."""

    def _sheet(self, path, name):
        from openpyxl import load_workbook
        book = load_workbook(path)
        try:
            return book, book[name]
        except Exception:
            book.close()
            raise

    def test_t8_the_bundled_form_is_the_second_edition_and_v1_stays_shipped(self):
        """A new paper's bundle is the corrected form; v1 is still on disk and
        still bound to its own hash for the papers already written on it."""
        tpl = runtime("gg_excel_template")
        v2 = _templates() / V2
        v1 = _templates() / V1
        self.assertEqual(hashlib.sha256(v2.read_bytes()).hexdigest(),
                         tpl.BUNDLED_TEMPLATE_SHA256)
        self.assertEqual(tpl.BUNDLED_TEMPLATE.resolve(), v2.resolve())
        self.assertEqual(tpl.BUNDLED_EDITION, "school_17_sheet_v2")
        self.assertEqual(hashlib.sha256(v1.read_bytes()).hexdigest(), V1_SHA)
        self.assertEqual(tpl.LEGACY_TEMPLATE_SHA256, V1_SHA)
        self.assertEqual(tpl.LEGACY_TEMPLATE.resolve(), v1.resolve())
        self.assertNotEqual(tpl.BUNDLED_TEMPLATE_SHA256, tpl.LEGACY_TEMPLATE_SHA256)

    def test_t9_the_second_edition_rows_and_formats(self):
        book, cash = self._sheet(_templates() / V2, CASH_FLOW)
        try:
            self.assertEqual(cash["C17"].value, "토지구입")
            self.assertEqual(cash.print_area, f"'{CASH_FLOW}'!$B$2:$I$25")
            production = book[PRODUCTION]
            self.assertEqual(production["H9"].value, "=H7*H8/1000")
            self.assertIsNone(production["H7"].value)
            with zipfile.ZipFile(_templates() / V2) as archive:
                styles = archive.read("xl/styles.xml").decode("utf-8")
            codes = dict(re.findall(r'numFmtId="(\d+)" formatCode="([^"]*)"', styles))
            self.assertEqual(codes.get("187"), "#,##0_);[Red]\\(#,##0\\);")
        finally:
            book.close()

    def test_t11_the_first_edition_is_unchanged(self):
        book, cash = self._sheet(_templates() / V1, CASH_FLOW)
        try:
            self.assertEqual(cash["C17"].value, "당기총생산 원가")
            self.assertEqual(cash.print_area, f"'{CASH_FLOW}'!$B$2:$I$24")
            production = book[PRODUCTION]
            self.assertEqual(production["H7"].value, "=H9/5520*1000")
            self.assertIsNone(production["H9"].value)
            with zipfile.ZipFile(_templates() / V1) as archive:
                styles = archive.read("xl/styles.xml").decode("utf-8")
            self.assertNotIn('numFmtId="187"', styles)
        finally:
            book.close()

    def test_the_second_edition_is_a_derived_asset_not_the_bundle(self):
        declared = json.loads(
            (SKILL / "references" / "workbook-reference-set.json")
            .read_text(encoding="utf-8"))
        derived = {entry.get("path") for entry in declared["derived_assets"]}
        self.assertIn(f"assets/templates/{V2}", derived)
        self.assertIn(f"assets/templates/{V1}", derived)


class CopyGateTests(CopyMixin, ContractCase):
    """`blank_copy` routes on the declared profile, not on the file name."""

    def setUp(self):
        self.root = self.make_project()
        bind_major(self.root, "specialty_crops")
        self.contract = runtime("gg_major_contract")
        self.context = self.contract.output_context(self.root, "specialty_crops")
        self.authorization = self.contract.authorize_output(
            self.contract.OUTPUT_SCHOOL_WORKBOOK, self.context)
        self.tpl = runtime("gg_excel_template")

    def _map(self, source, profile, name):
        """An inventory map pinned to `source`, declaring `profile`."""
        path = self.root / f"{name}.map.json"
        self.tpl.inspect_source(source, path, self.root / f"{name}.report.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["workbook_profile"] = profile
        doc["mapStatus"] = "ok"
        doc["layout"] = {"mapOrigin": "default_x01", "variant": "x01"}
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

    def _keyless_map(self, source, name):
        """An accepted inventory map that declares no edition at all — the
        shape a specialty map has when it was reviewed before the second
        edition existed."""
        path = self.root / f"{name}.map.json"
        self.tpl.inspect_source(source, path, self.root / f"{name}.report.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.pop("workbook_profile")
        doc["mapStatus"] = "ok"
        doc["layout"] = {"mapOrigin": "default_x01", "variant": "x01"}
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

    def test_t10_both_editions_pass_the_copy_gate_under_their_own_name(self):
        for source, profile, preserved in ((_templates() / V1, "school_17_sheet_v1", 766),
                                           (_templates() / V2, "school_17_sheet_v2", 767)):
            with self.subTest(edition=source.name):
                out = self.root / f"{profile}.xlsx"
                receipt = self.tpl.blank_copy(
                    source, self._map(source, profile, profile), out,
                    context=self.context)
                self.assertEqual(receipt["workbook_profile"], profile)
                self.assertEqual(len(receipt["preserved"]), preserved)
                self.assertEqual(receipt["missingCells"], [])
                self.assertTrue(out.is_file())

    def test_the_declared_profile_is_gated_against_the_form(self):
        """Same bytes, wrong label: the copy is refused, so a declaration can
        never smuggle one edition's form past the other's gate."""
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.blank_copy(_templates() / V1,
                                self._map(_templates() / V1, "school_17_sheet_v2", "cross"),
                                self.root / "cross.xlsx", context=self.context)
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")
        self.assertIn("school_17_sheet_v2", str(caught.exception))
        self.assertFalse((self.root / "cross.xlsx").exists())

    def test_a_map_without_a_declaration_is_never_pinned_to_the_first_edition(self):
        """A map that declares nothing takes the source's edition; with no
        source to read there is no edition to name, and the first edition is
        not substituted for it — an unlabelled second-edition paper would
        otherwise be handed the older form's rows."""
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.require_map_workbook_profile({}, self.authorization)
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")
        self.assertEqual(
            self.tpl.require_map_workbook_profile(
                {"workbook_profile": "school_17_sheet_v2"}, self.authorization),
            "school_17_sheet_v2")
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.require_map_workbook_profile(
                {"workbook_profile": "food_crops_16_sheet_v1"}, self.authorization)
        self.assertEqual(caught.exception.reason, "unsupported_finance_profile")

    def test_a_map_without_a_declaration_takes_the_edition_of_its_source(self):
        """The two editions share one form, so a reviewed map written before
        the second edition must clear/fill the copy it was made from — reading
        the edition out of that copy's own bytes, not pinning every keyless
        specialty map to the first edition. A source that names no edition
        (synthetic form) is refused: the first edition is not its answer. A
        copy of one edition is still refused when the map declares the other."""
        for source, expected in ((_templates() / V2, "school_17_sheet_v2"),
                                 (_templates() / V1, "school_17_sheet_v1")):
            with self.subTest(source=source.name):
                self.assertEqual(
                    self.tpl.require_map_workbook_profile(
                        {}, self.authorization, source), expected)
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.require_map_workbook_profile(
                {}, self.authorization, self.root / "no-such-edition.xlsx")
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")
        receipt = self.tpl.blank_copy(
            _templates() / V2, self._keyless_map(_templates() / V2, "keyless"),
            self.root / "keyless.xlsx", context=self.context)
        self.assertEqual(receipt["workbook_profile"], "school_17_sheet_v2")
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.blank_copy(
                _templates() / V1,
                self._map(_templates() / V1, "school_17_sheet_v2", "cross-keyless"),
                self.root / "cross-keyless.xlsx", context=self.context)
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")

    def test_a_keyless_map_reaches_the_receipt_stage_on_either_edition(self):
        """The copy engine reads a keyless map's edition from the source; the
        Office receipt gate must agree instead of refusing what the copy
        accepted. Both editions, end to end: copy -> receipt -> binding. A
        receipt whose recorded edition the output bytes do not show is still
        refused, so the keyless path cannot carry one edition under the
        other's label."""
        office = runtime("gg_office")
        for source, expected in ((_templates() / V2, "school_17_sheet_v2"),
                                 (_templates() / V1, "school_17_sheet_v1")):
            with self.subTest(edition=source.name):
                receipt = self.tpl.blank_copy(
                    source, self._keyless_map(source, "keyless-" + expected),
                    self.root / f"keyless-{expected}.xlsx", context=self.context)
                self.assertEqual(receipt["workbook_profile"], expected)
                out = self.root / f"keyless-{expected}.xlsx"
                path = self.root / f"receipt-{expected}.json"
                path.write_text(json.dumps(receipt, ensure_ascii=False),
                                encoding="utf-8")
                binding = office.template_receipt_binding(out, path)
                self.assertEqual(binding["workbook_profile"], expected)

    def test_a_keyless_map_survives_the_fill_route_too(self):
        """The fill engine resolves a keyless map the same way the clear
        engine does, and the receipt it writes must then pass the Office
        binding — the whole chain, both editions."""
        fill = runtime("gg_excel_fill")
        office = runtime("gg_office")
        sales = " 5. 판매계획"
        for source, expected in ((_templates() / V1, "school_17_sheet_v1"),
                                 (_templates() / V2, "school_17_sheet_v2")):
            with self.subTest(edition=source.name):
                mapping = self.root / f"fill-{expected}.json"
                mapping.write_text(json.dumps({
                    "schema": fill.MAP_SCHEMA,
                    "template": {"sha256": _sha(source)},
                    "entries": [{"sheet": sales, "cell": "B2",
                                 "role": "farm.area", "period": "2026",
                                 "editable": True, "source_note": "합성"}]},
                    ensure_ascii=False), encoding="utf-8")
                values = self.root / f"values-{expected}.json"
                values.write_text(json.dumps({
                    "schema": fill.VALUES_SCHEMA,
                    "values": [{"sheet": sales, "cell": "B2", "value": 40,
                                "value_type": "integer",
                                "answer_state": "provided",
                                "evidence_ref": {"source_id": "합성",
                                                 "revision": 1,
                                                 "locator": "B2",
                                                 "origin": "synthetic"}}]},
                    ensure_ascii=False), encoding="utf-8")
                out = self.root / f"filled-{expected}.xlsx"
                receipt = fill.fill_copy(source, mapping, values, out,
                                         context=self.context)
                self.assertEqual(receipt["workbook_profile"], expected)
                path = self.root / f"fill-receipt-{expected}.json"
                path.write_text(json.dumps(receipt, ensure_ascii=False),
                                encoding="utf-8")
                binding = office.template_receipt_binding(out, path)
                self.assertEqual(binding["workbook_profile"], expected)

    def test_a_receipt_claiming_the_other_edition_is_refused(self):
        """The keyless allowance is bound to the output bytes, not to the
        receipt's own word."""
        office = runtime("gg_office")
        receipt = self.tpl.blank_copy(
            _templates() / V1, self._keyless_map(_templates() / V1, "keyless-v1"),
            self.root / "claimed-v2.xlsx", context=self.context)
        receipt["workbook_profile"] = "school_17_sheet_v2"
        path = self.root / "claimed-v2.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError,
                                    "requires explicit map workbook profile"):
            office.template_receipt_binding(self.root / "claimed-v2.xlsx", path)

    def test_a_second_edition_copy_that_lost_all_its_markers_cannot_claim_the_first(self):
        """A declaration never answers for a form that cannot be read.

        Every second-edition marker cell is overwritten with a constant, so
        the formula addresses name no edition at all.  Named as the first
        edition, such a file must still be refused: the first edition has no
        registered form of its own, so nothing later in the route would ask.
        """
        school = runtime("gg_school_excel")
        lost = self.copy_of(_templates() / V2,
                            [(sheet, cell, 0) for sheet, cell in V2_MARKERS])
        self.assertIsNone(school.edition_of_workbook(lost))
        with self.assertRaisesRegex(ValueError, "unrecognised"):
            school.require_profile_structure(lost, "school_17_sheet_v1")
        out = self.root / "unreadable-as-first.xlsx"
        with self.assertRaises(self.contract.OutputHeldError) as caught:
            self.tpl.blank_copy(lost,
                                self._map(lost, "school_17_sheet_v1", "lost"),
                                out, context=self.context)
        self.assertEqual(caught.exception.reason, "workbook_structure_mismatch")
        self.assertFalse(out.exists())

    def test_a_second_edition_copy_keeps_its_edition_after_two_markers_are_lost(self):
        """The declared text says what the code does: the edition is read from
        the markers still standing, so losing two of the six leaves it the
        second edition.  An edition is undetermined when both marker
        counts are zero or the two counts are equal."""
        school = runtime("gg_school_excel")
        hurt = self.copy_of(_templates() / V2,
                            [(sheet, cell, 0) for sheet, cell in V2_MARKERS[:2]])
        self.assertEqual(school.edition_of_workbook(hurt), "school_17_sheet_v2")
        school.require_profile_edition(hurt, "school_17_sheet_v2")
        with self.assertRaises(ValueError):
            school.require_profile_edition(hurt, "school_17_sheet_v1")


class ObservationAdmissionTests(CopyMixin, ContractCase):
    """D8 admits the wage chain of each edition against its own pinned audit."""

    def setUp(self):
        self.patch = runtime("gg_excel_formula_patch")

    def test_each_edition_prepares_its_own_wage_chain(self):
        """Both editions clear the same six cells, each against its own audit.

        The bundled first edition is an openpyxl-built copy, so its admission
        is `derived`; the second edition ships byte-registered and is
        `registered`.  The audit named in the evidence is what separates them.
        """
        for source, admitted, audit in (
                (_templates() / V1, "(derived)", "formula-audit/x01.json"),
                (_templates() / V2, "(registered)", V2_AUDIT)):
            with self.subTest(edition=source.name):
                plan = self.patch.materialize_d8(source, prepare_observations=True)
                cleared = {p["cell"] for p in plan["patches"]
                           if p.get("operation") == "prepare_wage_input"}
                self.assertEqual(cleared, {"X27", "X28", "X29", "X30", "X31", "Z27"})
                target = next(p for p in plan["patches"] if p["cell"] == "Z27")
                self.assertIn(admitted, target["evidence_ref"]["locator"])
                self.assertIn(audit, target["evidence_ref"]["locator"])

    def test_a_copy_is_admitted_by_the_edition_it_derives_from(self):
        copy = self.copy_of(_templates() / V2, [(PRODUCTION, "H7", 350)])
        plan = self.patch.materialize_d8(copy, prepare_observations=True)
        target = next(p for p in plan["patches"] if p["cell"] == "Z27")
        self.assertIn("(derived)", target["evidence_ref"]["locator"])
        self.assertIn("school_17_sheet_v2.json", target["evidence_ref"]["locator"])

    def test_the_spec_pins_one_binding_per_edition(self):
        spec = json.loads(
            (SKILL / "references" / "common-workbooks"
             / "corrections" / "d8-spec.json").read_text(encoding="utf-8"))
        bindings = {(entry["source_sha256"], entry["audit_file"],
                     entry["sheet"], entry["cell"]): entry["audit_sha256"]
                    for entry in spec["reviewed_observation_formulas"]}
        self.assertEqual(
            bindings[("5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80",
                      "formula-audit/x01.json", "8. 노무비계획", "Z27")],
            X01_AUDIT_SHA)
        self.assertEqual(
            bindings[(V2_SHA, V2_AUDIT, "8. 노무비계획", "Z27")], V2_AUDIT_SHA)

    def test_a_copy_carrying_the_other_editions_audit_is_not_admitted(self):
        """The admissions are per source hash and audit, never per cell name."""
        copy = self.copy_of(_templates() / V2, [(PRODUCTION, "H7", 350)])
        spec = json.loads(
            (SKILL / "references" / "common-workbooks"
             / "corrections" / "d8-spec.json").read_text(encoding="utf-8"))
        first = next(entry for entry in spec["reviewed_observation_formulas"]
                     if entry["audit_file"] == "formula-audit/x01.json")
        stripped = dict(spec, reviewed_observation_formulas=[first])
        path = Path(tempfile.mkdtemp(prefix="knuaf-d8-")) / "spec.json"
        self.addCleanup(shutil.rmtree, path.parent, True)
        path.write_text(json.dumps(stripped, ensure_ascii=False), encoding="utf-8")
        from unittest import mock
        with mock.patch.object(self.patch, "D8_SPEC_PATH", path), \
                self.assertRaisesRegex(ValueError, "unreviewed observation formula"):
            self.patch.materialize_d8(copy, prepare_observations=True)
