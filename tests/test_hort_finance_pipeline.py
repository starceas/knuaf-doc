"""I1B end-to-end chain tests: plan -> transform -> native -> verify.

Only synthetic fixture bytes and synthetic contracts are used; no private
source values appear here.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
sys.path.insert(0, str(ROOT / "tests/fixtures/hort_finance"))
import gg_hort_workbook as h
import gg_major_contract as mc
from synthetic import build, entries, R, P as PKG, C as CTYPES
from tests._harness import claim_of

TMP = Path(os.environ.get("HFIN_TEST_TMPDIR",
                          ROOT / ".hw-work/fin/lanes/I5-FIX/tmp"))
SHEET1 = "1. 자산조사"
SHEET5 = "5. 원리금상환계획"
SHEET6 = "6.시설계획"
SHEET4 = "4.투자계획"
SHEET12 = "12 .경비계획"
SHEET7 = " 7. 판매계획"
SHEET17 = "17. 추정대차대조표"
TEACHER_IMAGE_LABEL = "학생 시설 도면·견적서 첨부"
SOURCE_WORKBOOK = ROOT / "../scratch/src/original-finance.xlsx"
SOURCE_SHA = "e3c9defe376fa74413641408900f6bb656017a389fb9745d9dac221a10951c1c"


def _params(sheet1):
    base = {"data_type": "decimal", "unit_code": "KRW_1000",
            "allowed_range": {"min": 0, "max": None},
            "required_when": "always", "required_if": {"all": []},
            "allowed_answer_states": ["provided"], "output_conversion": "identity"}
    params = {
        "plan.start_year": {**base, "data_type": "integer", "unit_code": "YEAR",
                            "allowed_range": {"min": 1900, "max": None},
                            "canonical_cell": SHEET12 + "!I2"},
        "num": {**base, "canonical_cell": sheet1 + "!D1"},
        "zero": {**base, "canonical_cell": sheet1 + "!F1"},
        "loan.slot1.execution_year": {**base, "data_type": "integer",
                                      "unit_code": "YEAR",
                                      "period_axis": "loan_contract_year",
                                      "canonical_cell": SHEET5 + "!C39"},
        "loan.slot1.principal_krw1000": {**base, "canonical_cell": SHEET5 + "!D39"},
        "loan.slot1.annual_rate": {**base, "unit_code": "RATIO",
                                   "allowed_range": {"min": 0, "max": 1},
                                   "canonical_cell": SHEET5 + "!F39"},
        "loan.slot1.grace_years": {**base, "data_type": "integer",
                                   "unit_code": "YEARS",
                                   "canonical_cell": SHEET5 + "!I39"},
        "loan.slot1.repayment_years": {**base, "data_type": "integer",
                                       "unit_code": "YEARS",
                                       "canonical_cell": SHEET5 + "!J39"},
        "loan.slot1.method": {**base, "data_type": "string", "unit_code": "TEXT",
                              "allowed_values": list(h._LOAN_METHODS),
                              "allowed_range": None,
                              "canonical_cell": SHEET5 + "!K39"},
        "invest.slot1.amount": {**base, "required_when": "slot used",
                                "required_if": {"group_active": "invest.slot1"},
                                "canonical_cell": sheet1 + "!Z9"},
    }
    return params


def _map_entries(sheet1):
    es = entries(sheet1)
    es += [
        {"sheet": SHEET12, "cell": "I2", "action": "input_param",
         "before_state": "absent", "param_key": "plan.start_year"},
        {"sheet": SHEET7, "cell": "B70", "action": "label_template",
         "before_state": "absent",
         "after_formula": "=('12 .경비계획'!$I$2+0)&\"년 계획\""},
        {"sheet": SHEET6, "cell": "B17", "action": "label_template",
         "before_state": "absent", "after_value": TEACHER_IMAGE_LABEL},
        {"sheet": SHEET6, "cell": "B47", "action": "label_template",
         "before_state": "absent", "after_value": TEACHER_IMAGE_LABEL},
        {"sheet": SHEET5, "cell": "C39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.execution_year"},
        {"sheet": SHEET5, "cell": "D39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.principal_krw1000"},
        {"sheet": SHEET5, "cell": "F39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.annual_rate"},
        {"sheet": SHEET5, "cell": "I39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.grace_years"},
        {"sheet": SHEET5, "cell": "J39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.repayment_years"},
        {"sheet": SHEET5, "cell": "K39", "action": "input_param",
         "before_state": "absent", "param_key": "loan.slot1.method"},
    ]
    _, rows = h._loan_slot_rows(1)
    schedule = h._loan_schedule(10, Decimal("0.05"), 0, 2, "원금균등")
    for i, r in enumerate(rows):
        balance, interest, paid = schedule[i]
        for col, value in (("B", 2027 + i), ("C", balance), ("D", interest),
                           ("E", paid), ("F", interest + paid)):
            es.append({"sheet": SHEET5, "cell": f"{col}{r}", "action": "formula",
                       "before_state": "absent", "after_formula": f"={value}"})
    for r in range(8, 14):
        es.append({"sheet": SHEET5, "cell": f"L{r}", "action": "formula",
                   "before_state": "absent", "after_formula": "=0"})
    for col in "CDEFGH":
        es.append({"sheet": SHEET17, "cell": f"{col}30", "action": "formula",
                   "before_state": "absent", "after_formula": "=10"})
        es.append({"sheet": SHEET17, "cell": f"{col}46", "action": "formula",
                   "before_state": "absent", "after_formula": "=10"})
    es.append({"sheet": SHEET5, "cell": "M1", "action": "formula",
               "before_state": "absent", "after_formula": "=NA()"})
    es.append({"sheet": SHEET5, "cell": "M2", "action": "formula",
               "before_state": "absent", "after_formula": '=""'})
    return es


def _registry(map_entries, schedule):
    rows = []
    kind = {"formula": "numeric_required", "input_param": "intentional_blank_nonformula",
            "clear": "intentional_blank_nonformula",
            "label_template": "display_string", "retain": "display_string"}
    state = {"formula": "formula", "input_param": "input_value",
             "clear": "cleared_blank", "label_template": "formula",
             "retain": "retained_value"}
    for e in map_entries:
        row = {"sheet": e["sheet"], "cell": e["cell"], "action": e["action"],
               "result_kind": kind[e["action"]], "unit": "KRW_1000",
               "printed": True, "core_reachable": True,
               "expected_state": state[e["action"]]}
        if e["action"] == "label_template" and not e.get("after_formula"):
            row["expected_state"] = "label"
        if (e["sheet"], e["cell"]) == (SHEET5, "M1"):
            row["result_kind"] = "conditional_unused_na"
            row["condition_key_if_conditional"] = "any_inactive:invest.slot1"
            row["active_kind"] = "numeric_required"
        if (e["sheet"], e["cell"]) == (SHEET5, "M2"):
            row["result_kind"] = "normal_empty_string"
        if (e["sheet"], e["cell"]) == (SHEET7, "B70"):
            row["unit"] = "TEXT"
        rows.append(row)
    return rows


def _docs(sheet1, sha):
    es = _map_entries(sheet1)
    params = _params(sheet1)
    registry = _registry(es, None)
    return {
        h.FILES[0]: {"schema": "knuaf-hort-finance-transform/v2",
                     "source_id": "H01", "source_sha256": sha,
                     "entry_count": len(es), "entries": es},
        h.FILES[1]: {"schema": "knuaf-hort-finance-params/v2",
                     "source_id": "H01", "source_sha256": sha,
                     "param_count": len(params), "parameters": params,
                     "rules": {key: {} for key in h._AUDIT_RULE}},
        h.FILES[2]: {"schema": "knuaf-hort-finance-verify-classes/v2",
                     "source_id": "H01", "source_sha256": sha,
                     "classes": list(h._json(h.REF / h.FILES[2])["classes"]),
                     "exhaustiveness": "synthetic",
                     "explicit_none_zero_keys": [],
                     "explicit_none_blank_keys": [],
                     "rule_ids": [h.ZERO_RULE, h.BLANK_RULE, h.CATEGORY_RULE],
                     "teacher_example_coords": [],
                     "registry": registry, "registry_count": len(registry)},
    }


def _fact(fid, key, value, **extra):
    fact = {"id": fid, "field_id": "hort_env_systems.fin." + key,
            "kind": "reported_fact", "value": value, "unit": None,
            "value_type": "text", "period": None, "scope": "farm",
            "answer_state": "provided", "verification": "unreviewed",
            "source_refs": [{"id": "s1", "locator": "synthetic", "revision": 1}],
            "revision": 1}
    fact.update(extra)
    return fact


def _project(root):
    p = {"schema_version": 2, "revision": 3,
         "requests": {}, "views": {}, "issues": [], "history": []}
    for c in ("sources", "facts", "sections", "questions", "tasks",
              "reviews", "rules", "approvals", "outputs"):
        p[c] = {}
    p["sources"]["s1"] = {"id": "s1", "path": "answer.txt", "hash": "0" * 64,
                          "revision": 1}
    p["facts"]["m0"] = _fact("m0", "common.major_id", "hort_env_systems",
                             scope="project", verification="claim_supported",
                             module_version="0.2.0")
    p["facts"]["m0"]["field_id"] = "common.major_id"
    p["facts"]["m0"]["source_refs"][0]["claim_id"] = "major"
    p["sources"]["s1"]["claims"] = claim_of(p["facts"]["m0"], "major")
    values = {"plan.start_year": 2027, "num": 7, "zero": 0,
              "loan.slot1.execution_year": 2027,
              "loan.slot1.principal_krw1000": 10,
              "loan.slot1.annual_rate": "0.05",
              "loan.slot1.grace_years": 0,
              "loan.slot1.repayment_years": 2,
              "loan.slot1.method": "원금균등"}
    for i, (key, value) in enumerate(values.items()):
        p["facts"][f"f{i}"] = _fact(f"f{i}", key, value)
    (root / "project.json").write_text(json.dumps(p, ensure_ascii=False),
                                       encoding="utf-8")


def _native_workbook(path, roster, params, facts, extra=None):
    """Synthetic recalculated workbook: every registry cell gets a cache.

    ``extra`` maps ``(sheet, ref)`` to ``(t, v, f)`` and overrides the cache of
    that coordinate after the baseline layout is assembled, so a test can
    publish a specific native cache (e.g. an error cell) without copying the
    whole builder.
    """
    cells = {}
    schedule = h._loan_schedule(10, Decimal("0.05"), 0, 2, "원금균등")
    _, rows = h._loan_slot_rows(1)
    for i, r in enumerate(rows):
        bal, interest, pp = schedule[i]
        cells[(SHEET5, f"B{r}")] = (None, str(2027 + i), f"={2027 + i}")
        cells[(SHEET5, f"C{r}")] = (None, str(bal), f"={bal}")
        cells[(SHEET5, f"D{r}")] = (None, str(interest), f"={interest}")
        cells[(SHEET5, f"E{r}")] = (None, str(pp), f"={pp}")
        cells[(SHEET5, f"F{r}")] = (None, str(interest + pp), f"={interest + pp}")
    for slot in range(2, 6):
        _, unused_rows = h._loan_slot_rows(slot)
        for r in unused_rows:
            cells[(SHEET5, f"B{r}")] = ("str", "", '=""')
    for r in range(8, 14):
        cells[(SHEET5, f"L{r}")] = (None, "0", "=0")
    cells[("1. 자산조사", "E53")] = (None, "0", "=0")
    for r in range(8, 13):
        cells[(SHEET5, f"C{r}")] = (None, "0", "=0")
        cells[("4.투자계획", f"F{r}")] = (None, "0", "=0")
    for col in "CDEFGH":
        cells[(SHEET17, f"{col}30")] = (None, "10", "=10")
        cells[(SHEET17, f"{col}46")] = (None, "10", "=10")
    cells[(SHEET5, "M1")] = ("e", "#N/A", "=NA()")
    cells[(SHEET5, "M2")] = ("str", "", '=""')
    cells[(SHEET7, "B70")] = ("str", "2027년 계획", "=x")
    for sheet, cell, key in ((SHEET12, "I2", "plan.start_year"),
                             (SHEET5, "C39", "loan.slot1.execution_year"),
                             (SHEET5, "D39", "loan.slot1.principal_krw1000"),
                             (SHEET5, "F39", "loan.slot1.annual_rate"),
                             (SHEET5, "I39", "loan.slot1.grace_years"),
                             (SHEET5, "J39", "loan.slot1.repayment_years")):
        cells[(sheet, cell)] = (None, str(facts[key]), None)
    cells[(SHEET5, "K39")] = ("inlineStr", facts["loan.slot1.method"], None)
    cells[(roster[0], "D1")] = (None, "7", None)
    cells[(roster[0], "F1")] = (None, "0", None)
    cells[(roster[0], "A1")] = (None, "5", "=2+3")
    cells[(roster[0], "C1")] = (None, "6", "=A1+1")
    cells[(roster[0], "I1")] = (None, "9", "=A1+4")
    cells[(roster[0], "B2")] = ("inlineStr", "draft", None)
    cells[(roster[0], "G1")] = ("inlineStr", "new", None)
    cells[(SHEET6, "B17")] = ("inlineStr", TEACHER_IMAGE_LABEL, None)
    cells[(SHEET6, "B47")] = ("inlineStr", TEACHER_IMAGE_LABEL, None)
    for (sheet, ref), spec in (extra or {}).items():
        cells[(sheet, ref)] = spec
    per_sheet = {name: {} for name in roster}
    for (sheet, ref), spec in cells.items():
        per_sheet[sheet][ref] = spec
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        sheets = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
                         for i, name in enumerate(roster, 1))
        z.writestr("xl/workbook.xml",
                   f'<workbook xmlns="{h.M}" xmlns:r="{R}"><sheets>{sheets}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Type="{R}/worksheet" '
                       f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, 19))
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="{PKG}">{rels}</Relationships>')
        z.writestr("[Content_Types].xml",
                   f'<Types xmlns="{CTYPES}"><Default Extension="xml" '
                   'ContentType="application/xml"/></Types>')
        z.writestr("xl/sharedStrings.xml",
                   f'<sst xmlns="{h.M}" count="1" uniqueCount="1"><si><t>fixture</t></si></sst>')
        for i, name in enumerate(roster, 1):
            rows = {}
            for ref, (t, v, f) in per_sheet[name].items():
                import re as _re
                rn = int(_re.search(r"\d+", ref).group())
                attr = f' t="{t}"' if t else ""
                inner = (f"<f>{f}</f>" if f else "") + (f"<v>{v}</v>" if v is not None else "")
                if t == "inlineStr":
                    inner = f"<is><t>{v}</t></is>"
                rows.setdefault(rn, []).append(f'<c r="{ref}"{attr}>{inner}</c>')
            body = "".join(f'<row r="{rn}">{"".join(cs)}</row>'
                           for rn, cs in sorted(rows.items()))
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       f'<worksheet xmlns="{h.M}"><sheetData>{body}</sheetData></worksheet>')


class Chain(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.temp.cleanup)
        d = Path(self.temp.name)
        self.root = d / "project"
        self.root.mkdir()
        _project(self.root)
        self.roster = h._json(h.REF / "profile.json")["workbook_reference"]["sheets"]
        self.source = d / "synthetic.xlsx"
        build(self.source, self.roster)
        self.sha = h.file_digest(self.source)
        self.docs = _docs(self.roster[0], self.sha)
        self.patches = [mock.patch.object(h, "SOURCE_SHA", self.sha),
                        mock.patch.object(h, "contracts", return_value=self.docs)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.facts = {"plan.start_year": 2027, "num": 7, "zero": 0,
                      "loan.slot1.execution_year": 2027,
                      "loan.slot1.principal_krw1000": 10,
                      "loan.slot1.annual_rate": "0.05",
                      "loan.slot1.grace_years": 0,
                      "loan.slot1.repayment_years": 2,
                      "loan.slot1.method": "원금균등"}
        self.plan_rec = d / "plan.json"
        self.out = d / "out.xlsx"
        self.transform_rec = d / "transform.json"
        self.native = d / "native.xlsx"
        self.pdf = d / "native.pdf"
        self.native_rec = d / "native.json"
        self.verify_rec = d / "verify.json"

    def _run_to_transform(self):
        h.plan(self.root, self.source, self.plan_rec)
        h.transform(self.root, self.source, self.plan_rec, self.out,
                    self.transform_rec)

    def _issue_native(self, native=None):
        """Synthetic official-manifest shape; no Office engine in CI."""
        native = native or self.native
        import gg_office
        from pypdf import PdfWriter
        auth = h._authorize(self.root)
        if not self.pdf.exists():
            writer = PdfWriter()
            for _ in range(18):
                writer.add_blank_page(width=595, height=842)
            with self.pdf.open("wb") as output:
                writer.write(output)
        xmeta = gg_office.validate_office_file(native, "excel")
        pmeta = gg_office.validate_pdf_file(self.pdf)
        prior_sha = h.file_digest(self.transform_rec)
        rec = {
            "status": "converted", "engine": "Microsoft Excel",
            "engine_result": "synthetic fixture conversion",
            "input_file": str(self.out.resolve()),
            "input_sha256": h.file_digest(self.out),
            "published_office": str(native.resolve()),
            "published_office_sha256": xmeta["sha256"],
            "published_pdf": str(self.pdf.resolve()),
            "published_pdf_sha256": pmeta["sha256"],
            "major_authorization": [auth.to_dict()],
            "validation": {
                "finance_profile": h.PROFILE,
                "template_receipt": {"path": str(self.transform_rec.resolve()),
                                     "sha256": prior_sha,
                                     "input_sha256": h.file_digest(self.out),
                                     "authority": "hort_transform_receipt"},
                "previous_receipt_sha256": prior_sha,
                "excel": xmeta, "pdf": pmeta,
                "native_pdf_coverage": {"worksheet_count": 18,
                                        "pdf_pages": 18,
                                        "status": "page_count_floor_met"},
                "structure_issues": [], "school_issues": []}}
        self.native_rec.write_text(
            json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=2,
                       default=str) + "\n", encoding="utf-8")
        return rec

    def _run_full_chain(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        return h.verify(self.root, self.source, self.transform_rec,
                        self.native_rec, self.verify_rec)

    def test_full_chain_plan_transform_native_verify(self):
        rec = self._run_full_chain()
        self.assertEqual(rec["stage"], "verify")
        self.assertTrue(self.verify_rec.exists())
        counts = rec["verify_counts"]
        self.assertEqual(counts["kind_checked"],
                         self.docs[h.FILES[2]]["registry_count"])
        self.assertGreaterEqual(counts["loan_rows"], 25)
        self.assertGreaterEqual(counts["identity"], 12)
        self.assertGreaterEqual(counts["label_checked"], 1)

    def test_transform_receipt_stale_rejected(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        rec = json.loads(self.transform_rec.read_text(encoding="utf-8"))
        rec["params_sha256"] = "0" * 64
        self.transform_rec.write_text(json.dumps(rec), encoding="utf-8")
        with self.assertRaisesRegex(h.Held, "transform_receipt_stale"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_transform_receipt_requires_exact_source_input(self):
        self._run_to_transform()
        original = json.loads(self.transform_rec.read_text(encoding="utf-8"))
        for label, entries in (("empty", []),
                               ("output", original["output_artifacts"])):
            rec = copy.deepcopy(original)
            rec["input_artifacts"] = entries
            bad = Path(self.temp.name) / ("bad-" + label + ".json")
            bad.write_text(json.dumps(rec), encoding="utf-8")
            with self.subTest(label=label), self.assertRaisesRegex(
                    h.Held, "transform_receipt_invalid"):
                h.check_transform_receipt(bad)

    def test_explicit_fact_semantics_reject_conflicting_metadata(self):
        import gg_core
        project = gg_core.load(self.root)
        params = self.docs[h.FILES[1]]["parameters"]
        for key, field, value, code in (
                ("loan.slot1.principal_krw1000", "unit", "USD", "fact_unit_mismatch"),
                ("loan.slot1.execution_year", "period", "2099", "fact_period_axis_mismatch"),
                ("num", "per_unit", "unapproved basis", "fact_per_unit_mismatch"),
                ("num", "conversion_evidence", "unapproved conversion",
                 "fact_conversion_evidence_mismatch")):
            altered = copy.deepcopy(project)
            hit = next(f for f in altered["facts"].values()
                       if f.get("field_id") == "hort_env_systems.fin." + key)
            hit[field] = value
            with self.subTest(key=key, field=field), self.assertRaisesRegex(h.Held, code):
                h._facts(altered, params)

    def test_native_receipt_stale_rejected(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        rec = json.loads(self.native_rec.read_text(encoding="utf-8"))
        rec["validation"]["previous_receipt_sha256"] = "0" * 64
        self.native_rec.write_text(json.dumps(rec), encoding="utf-8")
        with self.assertRaisesRegex(h.Held, "native_receipt_stale"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_office_pdf_binding_rejects_manifest_tamper(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        rec = json.loads(self.native_rec.read_text(encoding="utf-8"))
        rec["published_pdf_sha256"] = "0" * 64
        self.native_rec.write_text(json.dumps(rec), encoding="utf-8")
        with self.assertRaisesRegex(h.Held, "native_receipt_stale"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_additive_cache_counterexample(self):
        """A +1 cache change in an additive formula cannot complete verify."""
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        payload = self.native.read_bytes()
        import io
        with zipfile.ZipFile(io.BytesIO(payload)) as zin, \
                zipfile.ZipFile(self.native, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "xl/worksheets/sheet1.xml":
                    data = data.replace(b'<c r="C1"><f>=A1+1</f><v>6</v></c>',
                                        b'<c r="C1"><f>=A1+1</f><v>7</v></c>')
                zout.writestr(info, data)
        self._issue_native()
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_unsupported_formula_cannot_complete(self):
        docs = copy.deepcopy(self.docs)
        target = next(e for e in docs[h.FILES[0]]["entries"]
                      if e["sheet"] == self.roster[0] and e["cell"] == "C1")
        target["after_formula"] = "=UNSUPPORTED(A1)"
        with mock.patch.object(h, "contracts", return_value=docs):
            self._run_to_transform()
            _native_workbook(self.native, self.roster,
                             docs[h.FILES[1]]["parameters"], self.facts)
            self._issue_native()
            with self.assertRaisesRegex(h.Held, "verify_independent_unresolved"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)

    def test_conditional_blank_cache_cannot_skip_independent(self):
        """Astra-2 counterexample 1: clearing the native cache of a
        conditional_unused_na cell must not demote the check to the
        non-numeric aggregate.  The independent evaluator computes
        =L8+1 -> 1, so a blank/empty cache is a failure, not a skip."""
        docs = copy.deepcopy(self.docs)
        docs[h.FILES[0]]["entries"].append(
            {"sheet": SHEET5, "cell": "M3", "action": "formula",
             "before_state": "absent", "after_formula": "=L8+1"})
        docs[h.FILES[2]]["registry"].append(
            {"sheet": SHEET5, "cell": "M3", "action": "formula",
             "result_kind": "conditional_unused_na", "unit": "KRW_1000",
             "printed": True, "core_reachable": True,
             "expected_state": "formula",
             "condition_key_if_conditional": "any_inactive:invest.slot1",
             "active_kind": "number"})
        docs[h.FILES[2]]["registry_count"] += 1
        import io
        with mock.patch.object(h, "contracts", return_value=docs):
            self._run_to_transform()
            _native_workbook(self.native, self.roster,
                             docs[h.FILES[1]]["parameters"], self.facts)
            payload = self.native.read_bytes()
            with zipfile.ZipFile(io.BytesIO(payload)) as zin, \
                    zipfile.ZipFile(self.native, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == "xl/worksheets/sheet5.xml":
                        data = data.replace(
                            b'<row r="8"><c r="L8">',
                            b'<row r="8"><c r="M3"><f>L8+1</f></c><c r="L8">', 1)
                    zout.writestr(info, data)
            self._issue_native()
            with self.assertRaisesRegex(h.Held, "verify_independent_non_numeric"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)

    def test_error_wrappers_cannot_swallow_unsupported(self):
        """Astra-2 counterexample 2: IFERROR and ISNUMBER must not convert a
        compute-unsupported inner formula into a normal branch value.
        Both formulas carry the wrong native cache 0 that the buggy
        evaluator would agree with."""
        import io
        for label, formula in (("iferror", "=IFERROR(ABS(-1),0)"),
                               ("isnumber", "=IF(ISNUMBER(ABS(-1)),1,0)"),
                               # Astra-3: the unsupported function's own
                               # arguments raise a catchable cell error, so
                               # an outer wrapper swallows it before the
                               # unsupported verdict is reached.
                               ("iferror_arg_error",
                                "=IFERROR(IF(ISNA(NA()),1,0),0)"),
                               ("ifna_arg_error",
                                "=IFNA(IF(ISNA(NA()),1,0),0)"),
                               ("iserror_arg_error",
                                "=IF(ISERROR(ISNA(NA())),0,1)"),
                               ("isnumber_arg_error",
                                "=IF(ISNUMBER(ISNA(NA())),1,0)")):
            with self.subTest(label=label):
                docs = copy.deepcopy(self.docs)
                target = next(e for e in docs[h.FILES[0]]["entries"]
                              if e["sheet"] == self.roster[0]
                              and e["cell"] == "C1")
                target["after_formula"] = formula
                self.out = Path(self.temp.name) / f"out-{label}.xlsx"
                self.plan_rec = Path(self.temp.name) / f"plan-{label}.json"
                self.transform_rec = Path(self.temp.name) / f"transform-{label}.json"
                self.native_rec = Path(self.temp.name) / f"native-{label}.json"
                self.verify_rec = Path(self.temp.name) / f"verify-{label}.json"
                with mock.patch.object(h, "contracts", return_value=docs):
                    self._run_to_transform()
                    _native_workbook(self.native, self.roster,
                                     docs[h.FILES[1]]["parameters"], self.facts)
                    payload = self.native.read_bytes()
                    with zipfile.ZipFile(io.BytesIO(payload)) as zin, \
                            zipfile.ZipFile(self.native, "w") as zout:
                        for info in zin.infolist():
                            data = zin.read(info.filename)
                            if info.filename == "xl/worksheets/sheet1.xml":
                                data = data.replace(
                                    b'<c r="C1"><f>=A1+1</f><v>6</v></c>',
                                    b'<c r="C1"><f>' + formula[1:].encode()
                                    + b'</f><v>0</v></c>')
                            zout.writestr(info, data)
                    self._issue_native()
                    with self.assertRaisesRegex(
                            h.Held, "verify_independent_unresolved"):
                        h.verify(self.root, self.source, self.transform_rec,
                                 self.native_rec, self.verify_rec)

    def test_evaluator_error_classes(self):
        """Cell errors are catchable Excel results; unsupported grammar is a
        distinct class that no error-aware function may turn into a value."""
        import gg_hort_formula_eval as fe

        def run(formula):
            return fe.Evaluator([{"sheet": "S", "cell": "A1",
                                  "after_formula": formula}]).cell("S", "A1")

        with self.assertRaises(fe.UnsupportedError):
            run("=ABS(-1)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IFERROR(ABS(-1),0)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IF(ISNUMBER(ABS(-1)),1,0)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IFNA(ABS(-1),0)")
        with self.assertRaises(fe.UnsupportedError):
            run("=ISERROR(ABS(-1))")
        with self.assertRaises(fe.UnsupportedError):
            run("=SUM(ABS(-1),1)")
        # Astra-3: an unsupported function name must surface before its
        # arguments can produce a catchable Excel cell error that an
        # IFERROR/IFNA/ISERROR/ISNUMBER wrapper would absorb.
        with self.assertRaises(fe.UnsupportedError):
            run("=IFERROR(IF(ISNA(NA()),1,0),0)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IFNA(IF(ISNA(NA()),1,0),0)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IF(ISERROR(ISNA(NA())),0,1)")
        with self.assertRaises(fe.UnsupportedError):
            run("=IF(ISNUMBER(ISNA(NA())),1,0)")
        # The same rule reaches unsupported names an error wrapper cannot
        # see because a lazy IF branch never selects them.
        with self.assertRaises(fe.UnsupportedError):
            run("=IF(1=1,7,ISNA(0))")
        with self.assertRaises(fe.UnsupportedError):
            run("=IF(1=2,ABS(0),7)")
        # Genuine Excel cell errors stay catchable by the wrappers.
        self.assertEqual(run("=IFERROR(1/0,7)"), 7)
        self.assertEqual(run("=IFNA(NA(),5)"), 5)
        self.assertEqual(run("=IFERROR(NA(),9)"), 9)
        self.assertFalse(run("=ISNUMBER(1/0)"))
        self.assertFalse(run("=ISNUMBER(NA())"))
        self.assertTrue(run("=ISERROR(1/0)"))
        self.assertFalse(run("=ISERROR(1+1)"))
        # A raw cell error propagates to the caller, not into PASS counts.
        with self.assertRaises(fe.FormulaError):
            run("=1/0")

    def test_land_row_arithmetic_is_excel_strict(self):
        """A formula that yields "" is text, not 0: consuming it in arithmetic
        is #VALUE! exactly as Excel does. `SUM` still skips it, which is why
        `1!D10` had to become `=SUM(D8:D9)` (W-L defect 2)."""
        import gg_hort_formula_eval as fe
        sheet = "1. 자산조사"
        entries = h._json(h.REF / h.FILES[0])["entries"]
        acts = [e for e in entries
                if e["sheet"] == sheet and e["cell"] in ("D9", "E9", "D10")]
        self.assertEqual({a["cell"]: a["after_formula"] for a in acts}, {
            "D9": '=IF(M10="","",IF(ISNUMBER(M10),M10,NA()))',
            "E9": '=IF(M9="","",IF(ISNUMBER(M9),M9,NA()))',
            "D10": "=SUM(D8:D9)"})

        def run(inputs):
            evaluator = fe.Evaluator(
                acts, inputs={(sheet, cell): value for cell, value in inputs.items()})
            out = {}
            for cell in ("E9", "D9", "D10"):
                try:
                    out[cell] = evaluator.cell(sheet, cell)
                except fe.FormulaError as exc:
                    out[cell] = str(exc)
            return out

        self.assertEqual(run({"D8": 100}),
                         {"E9": "", "D9": "", "D10": 100.0})
        self.assertEqual(run({"D8": 100, "M9": 500, "M10": 30}),
                         {"E9": 500, "D9": 30, "D10": 130.0})
        # Non-numeric text in the extra amount/area is NA(), and the total
        # follows it down (D10 sums through D9, not around it).
        self.assertEqual(run({"D8": 100, "M9": "abc"}),
                         {"E9": "#N/A", "D9": "", "D10": 100.0})
        self.assertEqual(run({"D8": 100, "M10": "x"}),
                         {"E9": "", "D9": "#N/A", "D10": "#N/A"})
        # Formula-produced blanks are "" and SUM ignores them.
        self.assertEqual(run({"D8": 100, "M9": "", "M10": ""}),
                         {"E9": "", "D9": "", "D10": 100.0})
        # Direct arithmetic on a formula blank is still #VALUE!, the r2 defect.
        ev = fe.Evaluator([{"sheet": "S", "cell": "A1",
                            "after_formula": "=D8+D9"}],
                          inputs={("S", "D8"): 100, ("S", "D9"): ""})
        with self.assertRaisesRegex(fe.FormulaError, "#VALUE!"):
            ev.cell("S", "A1")

    def test_evaluator_failure_cache_is_terminal(self):
        """A failing chain is evaluated once per cell: the workbook and inputs
        are fixed for one run, so the failure is deterministic. Without the
        cache a single error re-evaluates exponentially (measured 120 s ->
        0 s on ` 7. 판매계획!L32`)."""
        import gg_hort_formula_eval as fe
        evaluator = fe.Evaluator([
            {"sheet": "S", "cell": "A1", "after_formula": "=B1"},
            {"sheet": "S", "cell": "B1", "after_formula": "=C1"},
            {"sheet": "S", "cell": "C1", "after_formula": "=1/0"},
            {"sheet": "S", "cell": "D1", "after_formula": "=1+1"}])
        for cell in ("A1", "B1", "C1"):
            with self.assertRaisesRegex(fe.FormulaError, "#DIV/0!"):
                evaluator.cell("S", cell)
        self.assertEqual(sorted(evaluator.errors), [("S", "A1"), ("S", "B1"),
                                                    ("S", "C1")])
        self.assertEqual(evaluator.cache, {})
        self.assertEqual(evaluator.cell("S", "D1"), 2.0)

    def test_missing_interest_counterexample(self):
        """Principal 10 at 5%: a native cache dropping the 0.5 interest fails."""
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        payload = self.native.read_bytes()
        import io
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            sheet5 = z.read("xl/worksheets/sheet5.xml").decode()
        sheet5 = re.sub(r'(<c r="D43"><f>[^<]*</f><v>)[^<]*(</v></c>)',
                        r'\g<1>0\g<2>', sheet5, count=1)
        with zipfile.ZipFile(io.BytesIO(payload)) as zin, \
                zipfile.ZipFile(self.native, "w") as zout:
            for info in zin.infolist():
                data = sheet5.encode() if info.filename == "xl/worksheets/sheet5.xml" \
                    else zin.read(info.filename)
                zout.writestr(info, data)
        self._issue_native()
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_unclassified_kind_and_identity_failures(self):
        self._run_to_transform()
        docs = copy.deepcopy(self.docs)
        docs[h.FILES[2]]["registry"][0]["result_kind"] = "display_string"
        with mock.patch.object(h, "contracts", return_value=docs):
            _native_workbook(self.native, self.roster,
                             docs[h.FILES[1]]["parameters"], self.facts)
            self._issue_native()
            with self.assertRaisesRegex(h.Held, "verify_display_required"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)
        docs = copy.deepcopy(self.docs)
        docs[h.FILES[2]]["registry"][0]["result_kind"] = "unlisted"
        with mock.patch.object(h, "contracts", return_value=docs):
            with self.assertRaisesRegex(h.Held, "verify_kind_unclassified"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)

    def test_identity_counterexample(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        import io
        payload = self.native.read_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as zin, \
                zipfile.ZipFile(self.native, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "xl/worksheets/sheet17.xml":
                    data = re.sub(rb'(<c r="C46"><f>[^<]*</f><v>)[^<]*(</v></c>)',
                                  rb'\g<1>11\g<2>', data, count=1)
                zout.writestr(info, data)
        self._issue_native()
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch|accounting_identity_mismatch"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_teacher_example_residue_counterexample(self):
        """Unmapped cells flagged as teaching examples must not keep the
        original H01 value in the printed output; only coordinates ship."""
        self._run_to_transform()
        docs = copy.deepcopy(self.docs)
        docs[h.FILES[2]]["teacher_example_coords"] = [
            {"sheet": self.roster[0], "cell": "J1"},
            {"sheet": self.roster[0], "cell": "B5"}]
        with mock.patch.object(h, "contracts", return_value=docs):
            _native_workbook(self.native, self.roster,
                             docs[h.FILES[1]]["parameters"], self.facts)
            # positive: J1/B5 absent from the native caches -> no residue
            self._issue_native()
            rec = h.verify(self.root, self.source, self.transform_rec,
                           self.native_rec, self.verify_rec)
            self.assertEqual(
                rec["verify_counts"]["teacher_example_checked"], 2)
            # negative (number): original 99 back at J1 -> residue held
            native2 = Path(self.temp.name) / "native2.xlsx"
            with zipfile.ZipFile(self.native) as zin, \
                    zipfile.ZipFile(native2, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == "xl/worksheets/sheet1.xml":
                        data = data.replace(
                            b'<c r="I1"',
                            b'<c r="J1"><v>99</v></c><c r="I1"', 1)
                    zout.writestr(info, data)
            self._issue_native(native2)
            with self.assertRaisesRegex(h.Held, "teacher_example_residue"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)
            # negative (string): original 'merged' back at B5 -> residue held
            native3 = Path(self.temp.name) / "native3.xlsx"
            with zipfile.ZipFile(self.native) as zin, \
                    zipfile.ZipFile(native3, "w") as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    if info.filename == "xl/worksheets/sheet1.xml":
                        data = data.replace(
                            b'<c r="B2"',
                            b'<c r="B5" t="inlineStr"><is><t>merged</t></is></c>'
                            b'<c r="B2"', 1)
                    zout.writestr(info, data)
            self._issue_native(native3)
            with self.assertRaisesRegex(h.Held, "teacher_example_residue"):
                h.verify(self.root, self.source, self.transform_rec,
                         self.native_rec, self.verify_rec)

    def test_teacher_image_residue_counterexample(self):
        """A surviving picture on the facility sheet must fail verify; the
        label cells and a picture-free drawing are the passing state."""
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        rec = h.verify(self.root, self.source, self.transform_rec,
                       self.native_rec, self.verify_rec)
        self.assertEqual(rec["verify_counts"]["teacher_image_label_checked"], 2)
        self.assertEqual(rec["verify_counts"]["teacher_image_checked"], 1)
        # negative: graft a drawing with one picture anchor onto sheet 6
        native2 = Path(self.temp.name) / "native-pic.xlsx"
        with zipfile.ZipFile(self.native) as zin, \
                zipfile.ZipFile(native2, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "xl/worksheets/sheet6.xml":
                    data = data.replace(
                        b'<worksheet xmlns="' + h.M.encode() + b'">',
                        b'<worksheet xmlns="' + h.M.encode()
                        + b'" xmlns:r="' + h.R.encode() + b'">', 1)
                    data = data.replace(
                        b'</worksheet>',
                        b'<drawing r:id="rIdD"/></worksheet>', 1)
                zout.writestr(info, data)
            zout.writestr(
                "xl/worksheets/_rels/sheet6.xml.rels",
                f'<Relationships xmlns="{PKG}">'
                f'<Relationship Id="rIdD" Type="{R}/drawing" '
                'Target="../drawings/drawing9.xml"/></Relationships>')
            zout.writestr(
                "xl/drawings/drawing9.xml",
                f'<xdr:wsDr xmlns:xdr="{h.XDR}" xmlns:a="{h.A}">'
                '<xdr:twoCellAnchor><xdr:from><xdr:col>1</xdr:col>'
                '<xdr:colOff>0</xdr:colOff><xdr:row>16</xdr:row>'
                '<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>6</xdr:col>'
                '<xdr:colOff>0</xdr:colOff><xdr:row>36</xdr:row>'
                '<xdr:rowOff>0</xdr:rowOff></xdr:to><xdr:pic/>'
                '<xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>')
        self._issue_native(native2)
        with self.assertRaisesRegex(h.Held, "teacher_image_residue"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_active_group_rejects_na_in_full_verify(self):
        project = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        project["facts"]["invest-active"] = _fact(
            "invest-active", "invest.slot1.amount", 10)
        (self.root / "project.json").write_text(
            json.dumps(project, ensure_ascii=False), encoding="utf-8")
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        with self.assertRaisesRegex(h.Held, "verify_error_cache"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)


class LandGatePaths(unittest.TestCase):
    """W-L: the second-parcel kind gate holds on every entry point out of five.

    The land params are injected into the synthetic contract docs, not into the
    fixture `_params`, so `param_count` and the registry stay untouched.
    """

    def setUp(self):
        self.chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        self.chain.setUp()
        self.addCleanup(self.chain.doCleanups)
        self.temp = Path(self.chain.temp.name)
        self._inject_land_params()

    def _inject_land_params(self):
        base = {"data_type": "decimal", "unit_code": "KRW_1000",
                "allowed_range": {"min": 0, "max": None},
                "required_when": "always", "required_if": {"all": []},
                "allowed_answer_states": ["provided"],
                "output_conversion": "identity"}
        params = self.chain.docs[h.FILES[1]]["parameters"]
        sheet = self.chain.roster[0]
        params["opening.land_extra_kind"] = {
            **base, "data_type": "string", "unit_code": "TEXT",
            "allowed_range": None, "allowed_values": ["논", "밭"],
            "required_if": {"all": [
                {"any": [{"group_active": "opening.land_extra_value_krw1000"},
                         {"group_active": "opening.land_extra_area_m2"}]},
                {"not": {"key_present": "category_absence.land"}}]},
            "canonical_cell": sheet + "!B9"}
        params["opening.land_extra_value_krw1000"] = {
            **base, "required_if": {"all": [
                {"key_present": "opening.land_extra_kind"},
                {"not": {"key_present": "category_absence.land"}}]},
            "canonical_cell": sheet + "!M9"}
        params["opening.land_extra_area_m2"] = {
            **base, "unit_code": "M2", "required_if": {"all": [
                {"key_present": "opening.land_extra_kind"},
                {"not": {"key_present": "category_absence.land"}}]},
            "canonical_cell": sheet + "!M10"}

    def _set_land_facts(self, kind, amount, area):
        self._set_facts_exact((("opening.land_extra_kind", kind),
                               ("opening.land_extra_value_krw1000", amount),
                               ("opening.land_extra_area_m2", area)))

    def _set_facts_exact(self, pairs):
        path = self.chain.root / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        project["facts"] = {k: v for k, v in project["facts"].items()
                            if not k.startswith("x")}
        for i, (key, value) in enumerate(pairs, 1):
            project["facts"]["x%d" % i] = _fact("x%d" % i, key, value)
        path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    def _held(self, function, *args):
        with self.assertRaises(h.Held) as caught:
            function(*args)
        return caught.exception.code

    def test_second_parcel_kind_gate_on_all_five_paths(self):
        chain, d = self.chain, self.temp
        source = chain.source

        # ① plan, fresh project.
        self._set_land_facts("과수원", 500, 100)
        self.assertEqual(self._held(h.plan, chain.root, source, d / "p1.json"),
                         "fact_value_invalid")

        # ② plan, resumed root (only project.json carried over).
        resumed = d / "resumed"
        resumed.mkdir()
        (resumed / "project.json").write_text(
            (chain.root / "project.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(self._held(h.plan, resumed, source, d / "p2.json"),
                         "fact_value_invalid")

        # ③ transform, a plan receipt from good facts, then a bad kind.
        self._set_land_facts("논", 500, 100)
        h.plan(chain.root, source, d / "plan_ok.json")
        self._set_land_facts("과수원", 500, 100)
        self.assertEqual(self._held(h.transform, chain.root, source,
                                    d / "plan_ok.json", d / "o3.xlsx", d / "t3.json"),
                         "fact_value_invalid")

        # ④ transform with good facts but a stale plan receipt.
        self._set_land_facts("논", 500, 100)
        plan = json.loads((d / "plan_ok.json").read_text(encoding="utf-8"))
        plan["params_sha256"] = "0" * 64
        (d / "plan_stale.json").write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(self._held(h.transform, chain.root, source,
                                    d / "plan_stale.json", d / "o4.xlsx", d / "t4.json"),
                         "plan_receipt_stale")
        # The facts gate precedes the receipt gate, so a stale receipt cannot
        # be used to report the wrong refusal.
        self._set_land_facts("과수원", 500, 100)
        self.assertEqual(self._held(h.transform, chain.root, source,
                                    d / "plan_stale.json", d / "o4b.xlsx", d / "t4b.json"),
                         "fact_value_invalid")

        # ⑤ verify: a full chain on good facts, then the same chain on a bad kind.
        self._set_land_facts("논", 500, 100)
        h.transform(chain.root, source, d / "plan_ok.json", chain.out,
                    chain.transform_rec)
        _native_workbook(chain.native, chain.roster,
                         chain.docs[h.FILES[1]]["parameters"], chain.facts)
        chain._issue_native()
        rec = h.verify(chain.root, source, chain.transform_rec, chain.native_rec,
                       d / "verify_ok.json")
        self.assertEqual(rec["stage"], "verify")
        self.assertEqual(rec["verify_counts"]["kind_checked"], 165)

        self._set_land_facts("과수원", 500, 100)
        self.assertEqual(self._held(h.verify, chain.root, source,
                                    chain.transform_rec, chain.native_rec,
                                    d / "verify_bad.json"),
                         "fact_value_invalid")

        # ⑤' the Office boundary refuses a tampered transform receipt.
        import gg_office
        self._set_land_facts("논", 500, 100)
        receipt = json.loads(chain.transform_rec.read_text(encoding="utf-8"))
        receipt["params_sha256"] = "0" * 64
        tampered = d / "transform_tampered.json"
        tampered.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(mc.OutputHeldError) as caught:
            gg_office._hort_transform_binding(
                chain.out, tampered, h._authorize(chain.root),
                mc.OutputContext(chain.root, "hort_env_systems"))
        self.assertEqual(caught.exception.reason, "hort_transform_receipt_invalid")

    def test_missing_partner_fact_refused_on_every_path(self):
        """A half-filled second parcel is `required_fact_missing`, never a
        partial row: the kind alone and the amount alone both refuse, on every
        entry point, and the Office boundary refuses the receipt that the
        revision bump invalidated."""
        chain, d = self.chain, self.temp
        source = chain.source
        full = (("opening.land_extra_kind", "논"),
                ("opening.land_extra_value_krw1000", 500),
                ("opening.land_extra_area_m2", 100))
        cases = {"kind only": (("opening.land_extra_kind", "논"),),
                 "value only": (("opening.land_extra_value_krw1000", 500),)}

        for label, facts in cases.items():
            with self.subTest(label=label):
                chain.out = d / f"out-{label}.xlsx"
                chain.transform_rec = d / f"transform-{label}.json"
                chain.native = d / f"native-{label}.xlsx"
                chain.native_rec = d / f"native-{label}.json"
                # ① plan, fresh project.
                self._set_facts_exact(facts)
                self.assertEqual(
                    self._held(h.plan, chain.root, source, d / f"p1-{label}.json"),
                    "required_fact_missing")
                # ② plan, resumed root (only project.json carried over).
                resumed = d / f"resumed-{label}"
                resumed.mkdir()
                (resumed / "project.json").write_text(
                    (chain.root / "project.json").read_text(encoding="utf-8"),
                    encoding="utf-8")
                self.assertEqual(
                    self._held(h.plan, resumed, source, d / f"p2-{label}.json"),
                    "required_fact_missing")
                # ③ transform holding a plan receipt made from complete facts.
                self._set_facts_exact(full)
                h.plan(chain.root, source, d / f"plan-{label}.json")
                self._set_facts_exact(facts)
                self.assertEqual(
                    self._held(h.transform, chain.root, source,
                               d / f"plan-{label}.json", d / f"o3-{label}.xlsx",
                               d / f"t3-{label}.json"),
                    "required_fact_missing")
                # ④ control: complete facts with a stale receipt still reports
                # the receipt, so ③'s refusal is the facts gate, not ordering.
                self._set_facts_exact(full)
                plan = json.loads(
                    (d / f"plan-{label}.json").read_text(encoding="utf-8"))
                plan["params_sha256"] = "0" * 64
                (d / f"plan-stale-{label}.json").write_text(
                    json.dumps(plan), encoding="utf-8")
                self.assertEqual(
                    self._held(h.transform, chain.root, source,
                               d / f"plan-stale-{label}.json",
                               d / f"o4-{label}.xlsx", d / f"t4-{label}.json"),
                    "plan_receipt_stale")
                # ⑤ verify: the chain is complete on good facts, then the
                # half-filled parcel is presented.
                h.transform(chain.root, source, d / f"plan-{label}.json",
                            chain.out, chain.transform_rec)
                _native_workbook(chain.native, chain.roster,
                                 chain.docs[h.FILES[1]]["parameters"],
                                 chain.facts)
                chain._issue_native()
                self.assertEqual(
                    h.verify(chain.root, source, chain.transform_rec,
                             chain.native_rec, d / f"v-ok-{label}.json")["stage"],
                    "verify")
                self._set_facts_exact(facts)
                self.assertEqual(
                    self._held(h.verify, chain.root, source,
                               chain.transform_rec, chain.native_rec,
                               d / f"v5-{label}.json"),
                    "required_fact_missing")
                # ⑥ Office boundary. The receipt records both the revision it
                # was minted at and the facts digest; the two refusals are
                # separated so neither hides the other.
                import gg_office

                def binding():
                    with self.assertRaises(mc.OutputHeldError) as caught:
                        gg_office._hort_transform_binding(
                            chain.out, chain.transform_rec,
                            h._authorize(chain.root),
                            mc.OutputContext(chain.root, "hort_env_systems"))
                    return caught.exception.reason

                def accepted():
                    gg_office._hort_transform_binding(
                        chain.out, chain.transform_rec,
                        h._authorize(chain.root),
                        mc.OutputContext(chain.root, "hort_env_systems"))
                    return "accepted"

                # ⑥a restated facts bind, so the two refusals below are real.
                self._set_facts_exact(full)
                self.assertEqual(accepted(), "accepted")
                # ⑥b facts alone, revision untouched: the digest refuses it.
                self._set_facts_exact((
                    ("opening.land_extra_kind", "논"),
                    ("opening.land_extra_value_krw1000", 700),
                    ("opening.land_extra_area_m2", 100)))
                self.assertEqual(binding(), "hort_transform_receipt_invalid")
                # ...and the inner checker names the cause as staleness, not
                # malformation: only the facts digest moved.
                self.assertEqual(self._held(h.check_transform_receipt,
                                            chain.transform_rec),
                                 "transform_receipt_stale")
                # ⑥c the revision alone, facts restored: the receipt is stale.
                self._set_facts_exact(full)
                project = json.loads(
                    (chain.root / "project.json").read_text(encoding="utf-8"))
                project["revision"] = project.get("revision", 0) + 1
                (chain.root / "project.json").write_text(
                    json.dumps(project, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(binding(), "hort_transform_receipt_invalid")


LAND_COORDS = ([(SHEET1, cell) for cell in ("B8", "E8", "D8", "B9", "M9",
                                            "M10", "D9", "E9", "D10")]
               + [(SHEET17, f"{col}{row}") for col in "CDEFGH"
                  for row in ("27", "28")])
LAND_PARAM_KEYS = ("opening.land_kind", "opening.land_value_krw1000",
                   "opening.land_area_m2", "opening.land_extra_kind",
                   "opening.land_extra_value_krw1000",
                   "opening.land_extra_area_m2")


def _real(name):
    """A shipped reference document, read from disk (not from the fixture)."""
    return h._json(h.REF / name)


def _transform_entries():
    return {(e["sheet"], e["cell"]): e for e in _real(h.FILES[0])["entries"]}


def _set_facts(root, pairs):
    """Replace every ad-hoc ``x*``/``l*`` fact with exactly ``pairs``."""
    path = root / "project.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    project["facts"] = {k: v for k, v in project["facts"].items()
                        if not k[0] in "xl"}
    for i, (key, value) in enumerate(pairs, 1):
        project["facts"][f"l{i}"] = _fact(f"l{i}", key, value)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def _inject_land_entries(docs, coords=LAND_COORDS):
    """Copy the real land entries into the synthetic transform contract."""
    source = _transform_entries()
    entries_ = docs[h.FILES[0]]["entries"]
    have = {(e["sheet"], e["cell"]) for e in entries_}
    added = []
    for coord in coords:
        if coord in have:
            continue
        row = source[coord]
        entry = {"sheet": coord[0], "cell": coord[1],
                 "action": row["action"], "before_state": "absent"}
        for key in ("after_formula", "param_key"):
            if row.get(key):
                entry[key] = row[key]
        added.append(entry)
    entries_.extend(added)
    docs[h.FILES[0]]["entry_count"] = len(entries_)
    return added


def _inject_land_params(docs, keys=LAND_PARAM_KEYS):
    real = _real(h.FILES[1])["parameters"]
    params = docs[h.FILES[1]]["parameters"]
    for key in keys:
        params[key] = real[key]
    docs[h.FILES[1]]["param_count"] = len(params)


def _inject_land_registry(docs, coords):
    rows = {(r["sheet"], r["cell"]): r for r in _real(h.FILES[2])["registry"]}
    registry = docs[h.FILES[2]]["registry"]
    have = {(r["sheet"], r["cell"]) for r in registry}
    for coord in coords:
        if coord not in have:
            registry.append(dict(rows[coord]))
    docs[h.FILES[2]]["registry_count"] = len(registry)


class LandRowEvaluation(unittest.TestCase):
    """W-L: the land row, ``1!B8:M10`` feeding ``17!C27:H28``, as shipped.

    Three layers: the contract lock on the 21 coordinates, the evaluator on the
    15 formulas, and one end-to-end transform read back out of the workbook.
    """

    def test_land_contract_coordinates_and_formulas(self):
        entries = _transform_entries()
        params = _real(h.FILES[1])["parameters"]
        inputs = [c for c in LAND_COORDS
                  if entries[c]["action"] == "input_param"]
        formulas = [c for c in LAND_COORDS
                    if entries[c]["action"] == "formula"]
        self.assertEqual(len(inputs), 6)
        self.assertEqual(len(formulas), 15)
        for coord in inputs:
            self.assertIsNone(entries[coord].get("after_formula"))
        for coord in formulas:
            self.assertTrue(entries[coord].get("after_formula"))
        self.assertEqual({entries[c]["param_key"]: c[1] for c in inputs},
                         {"opening.land_kind": "B8",
                          "opening.land_value_krw1000": "E8",
                          "opening.land_area_m2": "D8",
                          "opening.land_extra_kind": "B9",
                          "opening.land_extra_value_krw1000": "M9",
                          "opening.land_extra_area_m2": "M10"})
        self.assertEqual({params[k]["canonical_cell"] for k in LAND_PARAM_KEYS},
                         {f"{SHEET1}!{c}" for c in
                          ("B8", "E8", "D8", "B9", "M9", "M10")})

        text = {c: entries[c]["after_formula"] for c in formulas}
        self.assertEqual(text[(SHEET1, "D9")],
                         '=IF(M10="","",IF(ISNUMBER(M10),M10,NA()))')
        self.assertEqual(text[(SHEET1, "E9")],
                         text[(SHEET1, "D9")].replace("M10", "M9"))
        self.assertEqual(text[(SHEET1, "D10")], "=SUM(D8:D9)")
        # Every "밭" column is its "논" column with the kind swapped, and every
        # later sum column is column D with its depreciation source column
        # swapped (D is the identity base).
        for col in "CDEFGH":
            self.assertEqual(text[(SHEET17, col + "28")],
                             text[(SHEET17, col + "27")].replace("논", "밭"))
        for source, column in zip("NOPQR", "DEFGH"):
            for row in ("27", "28"):
                self.assertEqual(
                    text[(SHEET17, column + row)],
                    text[(SHEET17, "D" + row)].replace(
                        "$N$67:$N$75", f"${source}$67:${source}$75"))

        rows = {(r["sheet"], r["cell"]): r for r in _real(h.FILES[2])["registry"]}
        for coord in ((SHEET17, "C27"), (SHEET17, "C28")):
            row = rows[coord]
            self.assertEqual(row["result_kind"], "conditional_unused_na")
            self.assertEqual(row["active_kind"], "number")
            self.assertEqual(row["unit"], "KRW_1000")
            self.assertTrue(row["printed"])
            self.assertTrue(row["core_reachable"])

    def _evaluate(self, acts, b8, e8, b9, m9, m10, b26=2027, extra=None):
        import gg_hort_formula_eval as fe
        inputs = {(SHEET1, "B8"): b8, (SHEET1, "E8"): e8,
                  (SHEET1, "B9"): b9, (SHEET1, "M9"): m9,
                  (SHEET1, "M10"): m10, (SHEET1, "D8"): 9000.0,
                  (SHEET17, "B26"): b26}
        inputs.update(extra or {})
        ev = fe.Evaluator(acts, inputs=inputs)
        out = {cell: ev.cell(SHEET1, cell) for cell in ("D9", "E9", "D10")}
        for col in "CDEFGH":
            for row in ("27", "28"):
                out[col + row] = ev.cell(SHEET17, col + row)
        return out

    def test_land_formula_evaluation(self):
        entries = _transform_entries()
        acts = [entries[c] for c in LAND_COORDS
                if entries[c]["action"] == "formula"]

        def check(row, d9, e9, d10, c27, c28, sum27, sum28):
            self.assertEqual({k: row[k] for k in ("D9", "E9", "D10")},
                             {"D9": d9, "E9": e9, "D10": d10})
            self.assertEqual((row["C27"], row["C28"]), (c27, c28))
            for col in "DEFGH":
                self.assertEqual((row[col + "27"], row[col + "28"]),
                                 (sum27, sum28))

        check(self._evaluate(acts, "논", 200000, "밭", 50000, 3000),
              3000, 50000, 12000.0, 200000, 50000, 200000, 50000)
        # Swap which parcel is which kind: the columns follow the kind.
        check(self._evaluate(acts, "밭", 200000, "논", 50000, 3000),
              3000, 50000, 12000.0, 50000, 200000, 50000, 200000)
        check(self._evaluate(acts, "밭", 50000, "논", 200000, 3000),
              3000, 200000, 12000.0, 200000, 50000, 200000, 50000)
        # Second parcel absent: M9/M10 blank, so E9/D9 are "" and D10 is the
        # first parcel's area alone.
        check(self._evaluate(acts, "논", 200000, "", "", None),
              "", "", 9000.0, 200000, 0, 200000, 0)
        # SUMIFS against the depreciation plan: 1,000 hits only for 논.
        plan = {("13. 감가상각비계획 ", "C67"): 27,
                ("13. 감가상각비계획 ", "N67"): 1000,
                ("13. 감가상각비계획 ", "O67"): 1000,
                ("13. 감가상각비계획 ", "P67"): 1000,
                ("13. 감가상각비계획 ", "Q67"): 1000,
                ("13. 감가상각비계획 ", "R67"): 1000,
                ("13. 감가상각비계획 ", "V67"): "논"}
        check(self._evaluate(acts, "논", 200000, "밭", 50000, 3000,
                             extra=plan),
              3000, 50000, 12000.0, 200000, 50000, 201000, 50000)
        # A non-numeric B26 makes RIGHT(B26,2) miss every row.
        check(self._evaluate(acts, "논", 200000, "밭", 50000, 3000,
                             b26=" 4. 토지"),
              3000, 50000, 12000.0, 200000, 50000, 200000, 50000)

    def test_land_row_end_to_end(self):
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        self.assertEqual(len(_inject_land_entries(chain.docs)), len(LAND_COORDS))
        _inject_land_params(chain.docs)
        _set_facts(chain.root, [("opening.land_kind", "밭"),
                                ("opening.land_value_krw1000", 200000),
                                ("opening.land_area_m2", 9000),
                                ("opening.land_extra_kind", "논"),
                                ("opening.land_extra_value_krw1000", 50000),
                                ("opening.land_extra_area_m2", 1000)])
        chain._run_to_transform()

        want = set(LAND_COORDS)
        got = {}
        with zipfile.ZipFile(chain.out) as book:
            paths = dict(h.workbook_sheets(book))
            for sheet in (SHEET1, SHEET17):
                _, cellmap, _, _ = h._sheet_structure(book.read(paths[sheet]),
                                                     paths[sheet])
                for coord, cell in cellmap.items():
                    if (sheet, coord) not in want:
                        continue
                    formula = h._direct(cell, "f")
                    if formula is not None:
                        got[(sheet, coord)] = "=" + h._text(formula)

        entries = _transform_entries()
        expected = {c: entries[c]["after_formula"] for c in LAND_COORDS
                    if entries[c]["action"] == "formula"}
        self.assertEqual(got, expected)

        acts = [{"sheet": s, "cell": c, "after_formula": f}
                for (s, c), f in sorted(got.items())]

        def row(c27, c28, sum27, sum28):
            out = {"C27": c27, "C28": c28}
            for col in "DEFGH":
                out[col + "27"] = sum27
                out[col + "28"] = sum28
            return out

        # The published formulas, re-evaluated with the workbook's own inputs.
        first = self._evaluate(acts, "밭", 200000, "논", 50000, 1000,
                               b26=" 4. 토지")
        self.assertEqual((first["D9"], first["E9"], first["D10"]),
                         (1000, 50000, 10000.0))
        self.assertEqual({k: first[k] for k in row(0, 0, 0, 0)},
                         row(50000, 200000, 50000, 200000))
        second = self._evaluate(acts, "논", 50000, "밭", 200000, 1000,
                                b26=" 4. 토지")
        self.assertEqual({k: second[k] for k in row(0, 0, 0, 0)},
                         row(50000, 200000, 50000, 200000))
        single = self._evaluate(acts, "논", 200000, "", "", None,
                                b26=" 4. 토지")
        self.assertEqual((single["D9"], single["E9"], single["D10"]),
                         ("", "", 9000.0))
        self.assertEqual({k: single[k] for k in row(0, 0, 0, 0)},
                         row(200000, 0, 200000, 0))


class LandVerifyMitigation(unittest.TestCase):
    """W-L: an ``#N/A`` cache in ``17!C27`` — which gate actually stops it.

    ``conditional_unused_na`` lets an error cache through ``_verify_cell_kind``
    while the land groups are inactive, so the independent recompute is the
    gate that holds. Both layers are asserted, one scenario each.
    """

    def _chain(self, facts, b8, b9=None):
        chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        chain.setUp()
        self.addCleanup(chain.doCleanups)
        added = _inject_land_entries(
            chain.docs, [(SHEET1, "B8"), (SHEET1, "E8"), (SHEET1, "B9"),
                         (SHEET1, "E9"), (SHEET17, "C27"), (SHEET17, "C28")])
        _inject_land_params(chain.docs, [
            "opening.land_kind", "opening.land_value_krw1000",
            "opening.land_extra_kind", "opening.land_extra_value_krw1000",
            "opening.land_extra_area_m2"])
        _inject_land_registry(chain.docs,
                              [(e["sheet"], e["cell"]) for e in added])
        _set_facts(chain.root, facts)
        chain._run_to_transform()
        extra = {(SHEET1, "B8"): ("inlineStr", b8, None),
                 (SHEET1, "E8"): (None, "200000", None),
                 (SHEET1, "E9"): ("str", "",
                                  '=IF(M9="","",IF(ISNUMBER(M9),M9,NA()))'),
                 (SHEET17, "C27"): ("e", "#N/A", "=NA()"),
                 (SHEET17, "C28"): (None, "0", "=0")}
        if b9:
            extra[(SHEET1, "B9")] = ("inlineStr", b9, None)
            extra[(SHEET1, "M9")] = (None, "50000", None)
        _native_workbook(chain.native, chain.roster,
                         chain.docs[h.FILES[1]]["parameters"], chain.facts,
                         extra=extra)
        chain._issue_native()
        return chain

    def _held(self, chain):
        with self.assertRaises(h.Held) as caught:
            h.verify(chain.root, chain.source, chain.transform_rec,
                     chain.native_rec, chain.verify_rec)
        return caught.exception

    def test_na_passes_kind_gate_only_while_groups_are_inactive(self):
        chain = self._chain([("opening.land_kind", "논"),
                             ("opening.land_value_krw1000", 200000)], b8="논")
        held = self._held(chain)
        self.assertEqual(held.code, "verify_independent_non_numeric")
        self.assertEqual(held.args[0],
                         f"verify_independent_non_numeric:{SHEET17}!C27")

    def test_na_rejected_by_kind_gate_once_groups_are_active(self):
        chain = self._chain([("opening.land_kind", "논"),
                             ("opening.land_value_krw1000", 200000),
                             ("opening.land_extra_kind", "밭"),
                             ("opening.land_extra_value_krw1000", 50000),
                             ("opening.land_extra_area_m2", 1000)],
                            b8="논", b9="밭")
        held = self._held(chain)
        self.assertEqual(held.code, "verify_error_cache")
        self.assertEqual(held.args[0],
                         f"verify_error_cache:{SHEET17}!C27")


class LandErrorRegression(unittest.TestCase):
    """R-01: the land row's error footprint does not depend on the scenario.

    The 22 coordinates that only the pre-W-L formula set produces are locked
    verbatim: they are the whole point of the change, and a pattern would hide
    a coordinate that moved.
    """

    def test_error_footprint_is_scenario_independent(self):
        if not SOURCE_WORKBOOK.exists():
            self.skipTest("private FD-A source workbook absent "
                          "(off-repo optional input)")
        self.assertEqual(h.file_digest(SOURCE_WORKBOOK), SOURCE_SHA)
        import gg_hort_formula_eval as fe
        import gg_hort_registry as g

        docs = {name: h._json(h.REF / name) for name in h.FILES}
        head = {(SHEET1, "E9"): "=IF(ISNUMBER(M9),M9,NA())"}
        for source, column in zip("NOPQR", "DEFGH"):
            head[(SHEET17, column + "27")] = (
                "='1. 자산조사'!E8+SUMIFS('13. 감가상각비계획 '!$%s$67:$%s$75,"
                "'13. 감가상각비계획 '!$C$67:$C$75,RIGHT('17. 추정대차대조표'"
                "!$B$26,2),'13. 감가상각비계획 '!$V$67:$V$75,'1. 자산조사'!$B$8)"
                % (source, source))
            head[(SHEET17, column + "28")] = (
                "='1. 자산조사'!E9+SUMIFS('13. 감가상각비계획 '!$%s$67:$%s$75,"
                "'13. 감가상각비계획 '!$C$67:$C$75,RIGHT('17. 추정대차대조표'"
                '!$B$26,2),\'13. 감가상각비계획 \'!$V$67:$V$75,"밭")'
                % (source, source))

        # Arm A: the pre-W-L contract, reconstructed from the shipped one.
        arm = copy.deepcopy(docs)
        drop = {(SHEET1, "B9"), (SHEET1, "D10"),
                (SHEET17, "C27"), (SHEET17, "C28")}
        arm[h.FILES[0]]["entries"] = [
            e for e in arm[h.FILES[0]]["entries"]
            if (e["sheet"], e["cell"]) not in drop]
        by = {(e["sheet"], e["cell"]): e for e in arm[h.FILES[0]]["entries"]}
        for coord, text in head.items():
            by[coord].update({"action": "formula", "after_formula": text})
        arm[h.FILES[0]]["entry_count"] = len(arm[h.FILES[0]]["entries"])
        self.assertEqual(arm[h.FILES[0]]["entry_count"], 4103)
        self.assertEqual(docs[h.FILES[0]]["entry_count"], 4107)

        errors = ("#VALUE!", "#N/A", "#REF!", "#DIV/0!", "#NAME?", "#NUM!",
                  "#NULL!")

        def scan(contract, inputs):
            _, _, formulas, _, areas = g.load_source(str(SOURCE_WORKBOOK))
            entries_ = {(e["sheet"], e["cell"]): e
                        for e in contract[h.FILES[0]]["entries"]}
            live, values = dict(formulas), dict(inputs)
            for coord, entry in entries_.items():
                live.pop(coord, None)
                if entry.get("after_formula"):
                    live[coord] = entry["after_formula"]
                if entry["action"] == "input_param" and coord in values:
                    continue
                if entry["action"] in ("input_param", "clear"):
                    values.setdefault(coord, None)
            evaluator = fe.Evaluator(
                [{"sheet": s, "cell": c, "after_formula": f}
                 for (s, c), f in live.items()], inputs=values)
            found = {}
            for sheet, ref in sorted(live):
                if not g.in_ranges(ref, areas.get(sheet, [])):
                    continue
                try:
                    value = evaluator.cell(sheet, ref)
                except fe.FormulaError as exc:
                    found[(sheet, ref)] = exc.args[0] if exc.args else "?"
                except (fe.UnsupportedError, RecursionError):
                    continue
                except Exception as exc:          # noqa: BLE001 - tag it
                    found[(sheet, ref)] = type(exc).__name__
                else:
                    if isinstance(value, str) and value in errors:
                        found[(sheet, ref)] = value
            return found

        def inputs(name):
            values = {(SHEET1, "B8"): "논", (SHEET1, "D8"): 9256.0,
                      (SHEET1, "E8"): 200000.0,
                      (SHEET1, "C8"): "전북 전주시 덕진구924-1",
                      (SHEET1, "F8"): 2.0, (SHEET1, "G8"): "2004년",
                      (SHEET1, "M1"): 46022.0, (SHEET12, "I2"): 2026.0,
                      (SHEET4, "H19"): 0.0}
            if name != "blank_second":
                values.update({(SHEET4, "M19"): "토지",
                               (SHEET4, "N19"): "논",
                               (SHEET4, "H19"): 50000.0,
                               (SHEET4, "D19"): 2029.0})
            if name == "invest_two_late":
                values.update({(SHEET4, "M20"): "토지",
                               (SHEET4, "N20"): "밭",
                               (SHEET4, "H20"): 30000.0,
                               (SHEET4, "D20"): 2030.0})
            return values

        expected = {
            (SHEET1, "D10"): "#VALUE!", (SHEET1, "E10"): "#N/A",
            (SHEET1, "E9"): "#N/A",
            (SHEET17, "C16"): "#N/A", (SHEET17, "C26"): "#N/A",
            (SHEET17, "C28"): "#N/A", (SHEET17, "C30"): "#N/A",
            (SHEET17, "D16"): "#N/A", (SHEET17, "D26"): "#N/A",
            (SHEET17, "D28"): "#N/A", (SHEET17, "E16"): "#N/A",
            (SHEET17, "E26"): "#N/A", (SHEET17, "E28"): "#N/A",
            (SHEET17, "F16"): "#N/A", (SHEET17, "F26"): "#N/A",
            (SHEET17, "F28"): "#N/A", (SHEET17, "G16"): "#N/A",
            (SHEET17, "G26"): "#N/A", (SHEET17, "G28"): "#N/A",
            (SHEET17, "H16"): "#N/A", (SHEET17, "H26"): "#N/A",
            (SHEET17, "H28"): "#N/A"}
        self.assertEqual(len(expected), 22)

        for name in ("blank_second", "invest_one", "invest_two_late"):
            with self.subTest(name=name):
                after = scan(docs, inputs(name))
                before = scan(arm, inputs(name))
                self.assertEqual(len(before), 863)
                self.assertEqual(len(after), 841)
                self.assertEqual(sorted(set(before) - set(after)),
                                 sorted(expected))
                self.assertEqual(set(after) - set(before), set())
                self.assertEqual(sorted(set(before.values())),
                                 ["#N/A", "#VALUE!"])
                self.assertEqual(sorted(set(after.values())), ["#N/A"])
                self.assertEqual({k: before[k] for k in set(before) & set(after)
                                  if before[k] != after[k]}, {})


class Gates(unittest.TestCase):
    """Condition-tree, category and labor gates on synthetic spec subsets."""

    def _spec(self, key, **over):
        spec = {"canonical_cell": f"S!{key[-2:]}1", "data_type": "decimal",
                "unit_code": "KRW_1000", "required_when": "always",
                "required_if": {"all": []},
                "allowed_answer_states": ["provided"],
                "output_conversion": "identity"}
        spec.update(over)
        return spec

    def test_zero_actual_sales_holds_before_transform(self):
        params = {"plan.start_year": self._spec(
            "plan.start_year", data_type="integer", unit_code="YEAR")}
        project = {"facts": {"year": _fact("year", "plan.start_year", 2027)}}
        for month in range(1, 13):
            key = f"sales.y1.m{month}.production_qty"
            params[key] = self._spec(key, unit_code="SALES_UNIT")
            project["facts"][key] = _fact(key, key, 10)
        rate = "sales.y1.marketability_rate"
        params[rate] = self._spec(rate, unit_code="RATIO")
        project["facts"][rate] = _fact(rate, rate, 0)
        with self.assertRaisesRegex(h.Held, "zero_sales_year_unsupported"):
            h._facts(project, params)
        project["facts"][rate]["value"] = "0.5"
        values, _, _ = h._facts(project, params)
        self.assertEqual(values[rate], Decimal("0.5"))

    def test_required_if_group_active(self):
        params = {"a": self._spec("a"),
                  "b": self._spec("b", required_if={"group_active": "slot1"}),
                  "slot1.x": self._spec("slot1.x",
                                        required_if={"any": []})}
        project = {"facts": {"f1": _fact("f1", "a", 1)}}
        values, _, _ = h._facts(project, params)
        self.assertNotIn("b", values)
        with self.assertRaisesRegex(h.Held, "required_fact_missing"):
            params2 = dict(params)
            params2["b"] = self._spec("b", required_if={"key_present": "a"})
            h._facts(project, params2)

    def test_forbidden_if_branch(self):
        params = {"a": self._spec("a"),
                  "b": self._spec("b", required_if={"any": []},
                                  forbidden_if={"key_present": "a"})}
        project = {"facts": {"f1": _fact("f1", "a", 1),
                             "f2": _fact("f2", "b", 2)}}
        with self.assertRaisesRegex(h.Held, "param_fact_forbidden"):
            h._facts(project, params)

    def test_labor_kind_self_positive_held(self):
        params = {
            "cost.labor.y1.slot1.labor_kind": self._spec(
                "labor_kind", data_type="string", unit_code="TEXT",
                allowed_values=["hired", "self"], allowed_range=None),
            "cost.labor.y1.slot1.hours": self._spec(
                "hours", required_if={"any": []}),
        }
        project = {"facts": {"f1": _fact("f1", "cost.labor.y1.slot1.labor_kind",
                                         "self"),
                             "f2": _fact("f2", "cost.labor.y1.slot1.hours", 4)}}
        with self.assertRaisesRegex(h.Held, "self_labor_positive_unsupported"):
            h._facts(project, params)
        project["facts"]["f2"]["value"] = 0
        values, _, _ = h._facts(project, params)
        self.assertEqual(values["cost.labor.y1.slot1.labor_kind"], "self")

    def test_category_absence_and_conflict(self):
        params = {
            "category_absence.cash": self._spec(
                "cash", data_type="string", unit_code="TEXT",
                allowed_values=["confirmed_absent"], allowed_range=None,
                required_if={"any": []}),
            "opening.cash_krw1000": self._spec(
                "cash_krw1000", required_if={"any": []},
                explicit_none_calculation=0),
        }
        cat = _fact("c1", "category_absence.cash", "confirmed_absent",
                    reason="synthetic absence")
        project = {"facts": {"c1": cat}}
        values, derivations, _ = h._facts(project, params)
        self.assertEqual(values["opening.cash_krw1000"], 0)
        self.assertEqual(derivations[0]["rule_id"], h.CATEGORY_RULE)
        project["facts"]["f2"] = _fact("f2", "opening.cash_krw1000", 5)
        with self.assertRaisesRegex(h.Held, "category_absence_conflict"):
            h._facts(project, params)

    def test_condition_tree_operators(self):
        params = {"g.x": {"required_if": {"all": []}},
                  "a": {"required_if": {"all": []}},
                  "m": {"method_tag": "area_unit", "required_if": {"all": []}}}
        present = {"a": {}, "g.x": {}}
        resolved = {"a": Decimal(3), "g.x": Decimal(1)}
        ev = lambda node, spec=None: h._eval_cond(node, params, present,
                                                  resolved, spec or params["a"])
        self.assertTrue(ev({"all": [{"key_present": "a"}, {"key_present": "g.x"}]}))
        self.assertTrue(ev({"any": [{"key_present": "zzz"}, {"key_present": "a"}]}))
        self.assertTrue(ev({"not": {"key_present": "zzz"}}))
        self.assertTrue(ev({"key_equals": ["a", 3]}))
        self.assertTrue(ev({"group_active": "g"}, params["a"]))
        self.assertTrue(ev({"method_is": "area_unit"}, params["m"]))
        self.assertFalse(ev({"method_is": "direct"}, params["m"]))
        # Stored contracts use the list form: `method_tag in [...]` must hold
        # exactly as the string form does, or 40 overhead rate keys go
        # unconstrained (W-L defect 1).
        self.assertTrue(ev({"method_is": ["area_unit", "asset_rate", "tax_base_rate"]},
                           params["m"]))
        self.assertTrue(ev({"method_is": ["area_unit"]}, params["m"]))
        self.assertEqual(ev({"method_is": ["area_unit"]}, params["m"]),
                         ev({"method_is": "area_unit"}, params["m"]))
        self.assertFalse(ev({"method_is": []}, params["m"]))
        self.assertTrue(ev({"method_is": ["direct", "area_unit"]}, params["m"]))
        with self.assertRaisesRegex(h.Held, "condition_tree_invalid"):
            ev({"bogus": "a"})


class ContractShape(unittest.TestCase):
    """The generated public contract locks the 37 phrase families to trees."""

    @classmethod
    def setUpClass(cls):
        cls.params = h._json(h.REF / h.FILES[1])["parameters"]

    def test_all_params_have_condition_tree(self):
        for key, spec in self.params.items():
            self.assertIsNotNone(spec.get("required_if"), key)
            self.assertIsInstance(spec.get("required_when"), str, key)

    def test_labor_kind_keys(self):
        kinds = [k for k in self.params if k.endswith(".labor_kind")]
        self.assertEqual(len(kinds), 20)
        for key in kinds:
            self.assertEqual(self.params[key]["allowed_values"], ["hired", "self"])

    def test_category_absence_keys(self):
        cats = [k for k in self.params if k.startswith("category_absence.")]
        self.assertEqual(len(cats), 11)
        for key in cats:
            self.assertEqual(self.params[key]["allowed_values"],
                             ["confirmed_absent"])

    def test_registry_exhaustive_over_map(self):
        vc = h._json(h.REF / h.FILES[2])
        tr = h._json(h.REF / h.FILES[0])
        coords = {(r["sheet"], r["cell"]) for r in vc["registry"]}
        self.assertEqual(vc["registry_count"], len(vc["registry"]))
        self.assertEqual(len(coords), len(vc["registry"]))
        for e in tr["entries"]:
            self.assertIn((e["sheet"], e["cell"]), coords)
        for row in vc["registry"]:
            self.assertIn(row["result_kind"], vc["classes"])


class OfficeBoundary(unittest.TestCase):
    """I2 gg_office consumes the transform receipt via check_transform_receipt."""

    def setUp(self):
        self.chain = Chain(methodName="test_full_chain_plan_transform_native_verify")
        self.chain.setUp()
        self.addCleanup(self.chain.doCleanups)
        self.chain._run_to_transform()

    def test_i2_accepts_valid_transform_receipt(self):
        import gg_office
        auth = h._authorize(self.chain.root)
        context = mc.OutputContext(self.chain.root, "hort_env_systems")
        binding = gg_office._hort_transform_binding(
            self.chain.out, self.chain.transform_rec, auth, context)
        self.assertEqual(binding["authority"], "hort_transform_receipt")

    def test_i2_rejects_stale_receipt(self):
        import gg_office
        auth = h._authorize(self.chain.root)
        context = mc.OutputContext(self.chain.root, "hort_env_systems")
        rec = json.loads(self.chain.transform_rec.read_text(encoding="utf-8"))
        rec["params_sha256"] = "0" * 64
        self.chain.transform_rec.write_text(json.dumps(rec), encoding="utf-8")
        with self.assertRaisesRegex(mc.OutputHeldError,
                                    "hort_transform_receipt_invalid"):
            gg_office._hort_transform_binding(
                self.chain.out, self.chain.transform_rec, auth, context)


class NativeRegressions(unittest.TestCase):
    """Defects surfaced by the real-Excel native run (I3-NATIVE)."""

    def test_draft_label_entries(self):
        # BC §5: draft markers are absent from both printed sheets.
        tr = h._json(h.REF / h.FILES[0])
        vc = h._json(h.REF / h.FILES[2])
        targets = [("1. 자산조사", "B2"), ("18. 현금흐름계획", "B2")]
        for sheet, cell in targets:
            hit = [e for e in tr["entries"]
                   if e["sheet"] == sheet and e["cell"] == cell]
            self.assertEqual(hit, [], (sheet, cell))
            row = [r for r in vc["registry"]
                   if r["sheet"] == sheet and r["cell"] == cell]
            self.assertEqual(row, [])

    def test_every_cna_group_has_members(self):
        # any_inactive keys must name param groups with members.
        params = h._json(h.REF / h.FILES[1])["parameters"]
        vc = h._json(h.REF / h.FILES[2])
        for row in vc["registry"]:
            ck = row.get("condition_key_if_conditional") or ""
            if not ck.startswith("any_inactive:"):
                continue
            for group in ck.removeprefix("any_inactive:").split(","):
                self.assertTrue(h._group_members(params, group),
                                (row["sheet"], row["cell"], group))

    def test_mutually_exclusive_wage_fields_do_not_license_na(self):
        vc = h._json(h.REF / h.FILES[2])
        labor_rows = [row for row in vc["registry"]
                      if row["sheet"] == "11. 인건비계획"
                      and row["result_kind"] == "conditional_unused_na"]
        self.assertTrue(labor_rows)
        for row in labor_rows:
            parts = row["condition_key_if_conditional"].removeprefix(
                "any_inactive:").split(",")
            self.assertFalse(any(part.endswith((".hourly_krw", ".daily_krw",
                                                ".paid_hours_day"))
                                 for part in parts), (row["sheet"], row["cell"]))

    def test_product_oracle_multi_factor(self):
        caches = {"4.투자계획": {"F24": (None, 900, True, True)},
                  "13. 감가상각비계획 ": {"D5": (None, 2, True, True),
                                        "E6": (None, 4, True, True)}}
        sheet = "13. 감가상각비계획 "
        got = h._product_eval("=D5*'4.투자계획'!F24*E6/1000", caches, sheet)
        self.assertEqual(got, Decimal("7.2"))
        two = h._product_eval("=D5*E6/1000", caches, sheet)
        self.assertEqual(two, Decimal("0.008"))
        self.assertIsNone(h._product_eval("=SUM(D5:E5)", caches, sheet))

    def test_operand_error_raises_for_oracle_skip(self):
        caches = {"S": {"A1": ("e", "#N/A", True, True)}}
        with self.assertRaisesRegex(h.Held, "verify_operand_error"):
            h._cache_num(caches, "S", "A1")

    def test_raw_cache_units_classified(self):
        # Native units that previously fell through as unclassified.
        for unit in ("DATE", "HOUR", "M2", "HOUR_PER_DAY",
                     "CHARGE_UNIT_PER_SALES_UNIT"):
            self.assertEqual(h.compare_raw_cache(5, 5, unit), Decimal(0))
            with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
                h.compare_raw_cache(5, 6, unit)

    def test_profitability_ratio_registry_units(self):
        rows = h._json(h.REF / h.FILES[2])["registry"]
        for col in "GHIJK":
            row = next(r for r in rows if r["sheet"] == "16. 수익성분석"
                       and r["cell"] == col + "27")
            self.assertEqual(row["unit"], "RATIO")
        with self.assertRaisesRegex(h.Held, "raw_cache_mismatch"):
            h.compare_raw_cache(0.5, 1.5, "RATIO")

    def test_conditional_na_requires_inactive_facts(self):
        row = {"sheet": "S", "cell": "A1", "action": "formula",
               "result_kind": "conditional_unused_na",
               "condition_key_if_conditional": "any_inactive:invest.slot1",
               "active_kind": "number"}
        params = {"invest.slot1.amount": {"unit_code": "KRW_1000"}}
        error_cache = ("e", "#N/A", True, True)
        inactive = h._condition_value(row, params, set(), {}, "S!A1")
        self.assertTrue(inactive)
        h._verify_cell_kind(row, error_cache, inactive)
        active = h._condition_value(row, params, {"invest.slot1.amount"},
                                    {"invest.slot1.amount": Decimal("10")}, "S!A1")
        self.assertFalse(active)
        with self.assertRaisesRegex(h.Held, "verify_error_cache"):
            h._verify_cell_kind(row, error_cache, active)
        h._verify_cell_kind(row, (None, 10, True, True), active)

    def test_equal_na_is_not_an_accounting_identity(self):
        with self.assertRaisesRegex(h.Held, "identity_operand_invalid"):
            h.check_identity("#N/A", "#N/A")

    def test_label_tokens_are_substituted_in_transformed_workbook(self):
        from io import BytesIO
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            source = Path(temp) / "fixture.xlsx"
            roster = h._json(h.REF / "profile.json")["workbook_reference"]["sheets"]
            build(source, roster)
            entry = {"sheet": SHEET7, "cell": "B5", "action": "label_template",
                     "before_state": "absent", "after_value": "crop {crop_item}"}
            tr = {"schema": "knuaf-hort-finance-transform/v2",
                  "source_id": "H01", "source_sha256": h.file_digest(source),
                  "entry_count": 1, "entries": [entry]}
            with mock.patch.object(h, "SOURCE_SHA", h.file_digest(source)):
                payload, _ = h._zip_transform(source, tr,
                                                {"sales.crop_item": "synthetic"}, {})
            with zipfile.ZipFile(BytesIO(payload)) as z:
                sheet = ET.fromstring(z.read("xl/worksheets/sheet7.xml"))
            cell = next(c for c in sheet.iter("{" + h.M + "}c")
                        if c.get("r") == "B5")
            self.assertEqual("".join(cell.itertext()), "crop synthetic")
            with self.assertRaisesRegex(h.Held, "label_token_unknown"):
                h._substitute_label("{unexpected2}", {})

    def test_material_rows_use_per_item_area_helper(self):
        tr = h._json(h.REF / h.FILES[0])
        rows = [e for e in tr["entries"]
                if e["sheet"] == "9. 영농자재구매계획"
                and e["cell"].startswith("E")
                and (e.get("reason") or "").startswith("T5 품목별 적용면적")]
        self.assertEqual(len(rows), 25)
        for row in rows:
            source_row = __import__("re").search(r"\$U\$(\d+)", row["after_formula"])
            self.assertIsNotNone(source_row)
            self.assertIn("$AD$" + source_row.group(1), row["after_formula"])
            self.assertNotIn("ISNUMBER(D" + row["cell"][1:] + ")",
                             row["after_formula"])
        anchors = [e for e in tr["entries"]
                   if e["sheet"] == "9. 영농자재구매계획"
                   and e["cell"] in {"E9", "E20", "E31", "E42", "E53"}]
        self.assertEqual(len(anchors), 5)
        for row in anchors:
            self.assertIn("ISNUMBER(D" + row["cell"][1:] + ")",
                          row["after_formula"])


if __name__ == "__main__":
    unittest.main()
