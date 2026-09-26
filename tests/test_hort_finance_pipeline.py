"""I1B end-to-end chain tests: plan -> transform -> native -> verify.

Only synthetic fixture bytes and synthetic contracts are used; no private
source values appear here.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import json
from pathlib import Path
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

TMP = ROOT / ".hw-work/fin/lanes/I3B-FIX/tmp"
SHEET5 = "5. 원리금상환계획"
SHEET6 = "6.시설계획"
SHEET12 = "12 .경비계획"
SHEET7 = " 7. 판매계획"
SHEET17 = "17. 추정대차대조표"
TEACHER_IMAGE_LABEL = "학생 시설 도면·견적서 첨부"


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
    for r in rows:
        for col in ("B", "C", "D", "E", "F"):
            es.append({"sheet": SHEET5, "cell": f"{col}{r}", "action": "formula",
                       "before_state": "absent", "after_formula": "=1"})
    for r in range(8, 14):
        es.append({"sheet": SHEET5, "cell": f"L{r}", "action": "formula",
                   "before_state": "absent", "after_formula": "=1"})
    for col in "CDEFGH":
        es.append({"sheet": SHEET17, "cell": f"{col}30", "action": "formula",
                   "before_state": "absent", "after_formula": "=1"})
        es.append({"sheet": SHEET17, "cell": f"{col}46", "action": "formula",
                   "before_state": "absent", "after_formula": "=1"})
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


def _native_workbook(path, roster, params, facts):
    """Synthetic recalculated workbook: every registry cell gets a cache."""
    cells = {}
    schedule = h._loan_schedule(10, Decimal("0.05"), 0, 2, "원금균등")
    _, rows = h._loan_slot_rows(1)
    for i, r in enumerate(rows):
        bal, interest, pp = schedule[i]
        cells[(SHEET5, f"B{r}")] = (None, str(2027 + i), "=1")
        cells[(SHEET5, f"C{r}")] = (None, str(bal), "=1")
        cells[(SHEET5, f"D{r}")] = (None, str(interest), "=1")
        cells[(SHEET5, f"E{r}")] = (None, str(pp), "=1")
        cells[(SHEET5, f"F{r}")] = (None, str(interest + pp), "=1")
    for r in range(8, 14):
        cells[(SHEET5, f"L{r}")] = (None, "0", "=1")
    for col in "CDEFGH":
        cells[(SHEET17, f"{col}30")] = (None, "10", "=1")
        cells[(SHEET17, f"{col}46")] = (None, "10", "=1")
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
    cells[(roster[0], "C1")] = (None, "4", "=A1+1")
    cells[(roster[0], "I1")] = (None, "7", "=A1+4")
    cells[(roster[0], "B2")] = ("inlineStr", "draft", None)
    cells[(roster[0], "G1")] = ("inlineStr", "new", None)
    cells[(SHEET6, "B17")] = ("inlineStr", TEACHER_IMAGE_LABEL, None)
    cells[(SHEET6, "B47")] = ("inlineStr", TEACHER_IMAGE_LABEL, None)
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
        self.native_rec = d / "native.json"
        self.verify_rec = d / "verify.json"

    def _run_to_transform(self):
        h.plan(self.root, self.source, self.plan_rec)
        h.transform(self.root, self.source, self.plan_rec, self.out,
                    self.transform_rec)

    def _issue_native(self, native=None):
        native = native or self.native
        import gg_core
        project = gg_core.load(self.root)
        auth = h._authorize(self.root)
        values, derivations, fact_sha = h._facts(
            project, self.docs[h.FILES[1]]["parameters"])
        rec = h._receipt("native", project, auth, self.source, self.docs,
                         fact_sha, values, self.transform_rec, self.root)
        rec["input_artifacts"] = [h._artifact(self.out)]
        rec["output_artifacts"] = [h._artifact(native)]
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

    def test_native_receipt_stale_rejected(self):
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        self._issue_native()
        rec = json.loads(self.native_rec.read_text(encoding="utf-8"))
        rec["previous_receipt_sha256"] = "0" * 64
        self.native_rec.write_text(json.dumps(rec), encoding="utf-8")
        with self.assertRaisesRegex(h.Held, "native_receipt_stale"):
            h.verify(self.root, self.source, self.transform_rec,
                     self.native_rec, self.verify_rec)

    def test_missing_interest_counterexample(self):
        """Principal 10 at 5%: a native cache dropping the 0.5 interest fails."""
        self._run_to_transform()
        _native_workbook(self.native, self.roster,
                         self.docs[h.FILES[1]]["parameters"], self.facts)
        payload = self.native.read_bytes()
        import io
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            sheet5 = z.read("xl/worksheets/sheet5.xml").decode()
        sheet5 = sheet5.replace('<c r="D43"><f>=1</f><v>0.50</v></c>',
                                '<c r="D43"><f>=1</f><v>0</v></c>')
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
                    data = data.replace(b'<c r="C46"><f>=1</f><v>10</v></c>',
                                        b'<c r="C46"><f>=1</f><v>11</v></c>')
                zout.writestr(info, data)
        self._issue_native()
        with self.assertRaisesRegex(h.Held, "accounting_identity_mismatch"):
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
        # Design 5.6: both printed sheets carry the draft marker.
        tr = h._json(h.REF / h.FILES[0])
        vc = h._json(h.REF / h.FILES[2])
        targets = [("1. 자산조사", "B2"), ("18. 현금흐름계획", "B2")]
        for sheet, cell in targets:
            hit = [e for e in tr["entries"]
                   if e["sheet"] == sheet and e["cell"] == cell]
            self.assertEqual(len(hit), 1, (sheet, cell))
            self.assertEqual(hit[0]["action"], "label_template")
            self.assertEqual(hit[0]["after_value"], "교수 확인 전 초안")
            self.assertEqual(hit[0]["before_state"], "blank_node")
            row = [r for r in vc["registry"]
                   if r["sheet"] == sheet and r["cell"] == cell]
            self.assertEqual(len(row), 1)
            self.assertEqual(row[0]["result_kind"], "display_string")
            self.assertTrue(row[0]["printed"])

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
