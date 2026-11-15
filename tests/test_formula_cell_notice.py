"""계산 칸 알림 — 설계 v4 Part A §7 / INTERFACE 계약.

학교 배포 양식과 임시 폴더만 쓴다. 실제 학생 자료·AI·엑셀 앱 호출 없음.
번호는 각 시험의 주석에 붙인다. 실행은 책임자의 전용 슬롯에서만 한다.
"""
import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from xml.dom import minidom

from tests._harness import ContractCase, bind_major, claim_of, fact_op, runtime, source_op
from tests.test_student_language import FORBIDDEN_STUDENT_TERMS

CHECK_ID = "finance_formula_cell_notice"
PREFIX = "finance.workbook.formula_cells."
SALES = " 5. 판매계획"
INVEST = "3.투자계획"
LABOR = "8. 노무비계획"
CELL = (SALES, "E22")
OTHER = (SALES, "E23")
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FormulaNoticeCase(ContractCase):
    def setUp(self):
        self.core = runtime("gg_core")
        self.tpl = runtime("gg_excel_template")
        self.fcn = runtime("gg_formula_cell_notice")
        self.patch = runtime("gg_excel_formula_patch")
        self.mc = runtime("gg_major_contract")
        self.notices = runtime("gg_notices")
        tmp = tempfile.TemporaryDirectory(prefix="formula-notice-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.make_project()
        self.source = self.tmp / "재무계획.xlsx"
        self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
        self.ref = "school_17_sheet_v2"
        bindings = json.loads(self.tpl.CROP_LABELS_PATH.read_text(encoding="utf-8"))["templates"]
        self.binding = bindings[self.ref]
        self.audit = self.tpl.registered_audit(self.binding)
        self.formulas = {(r["sheet"], r["cell"]): r["formula"] for r in self.audit["inventory"]}

    def edit_book(self, edits, path=None):
        """지정 칸 XML만 바꾸고 나머지 공유 수식·ZIP 멤버는 보존한다."""
        path = path or self.source
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
            sheets = dict(self.tpl.workbook_sheets(archive))
            replacement = {}
            for sheet, part in sheets.items():
                selected = {cell: value for (name, cell), value in edits.items() if name == sheet}
                if not selected:
                    continue
                tree = minidom.parseString(archive.read(part))
                data = tree.getElementsByTagNameNS(NS, "sheetData")[0]
                prefix = tree.documentElement.prefix
                def element(name):
                    return tree.createElementNS(NS, f"{prefix}:{name}" if prefix else name)
                def append_text(parent, name, value):
                    node = element(name)
                    node.appendChild(tree.createTextNode(str(value)))
                    parent.appendChild(node)
                    return node
                for address, value in selected.items():
                    cell = next((c for c in data.getElementsByTagNameNS(NS, "c")
                                 if c.getAttribute("r") == address), None)
                    if cell is None:
                        rownum = "".join(c for c in address if c.isdigit())
                        row = next((r for r in data.getElementsByTagNameNS(NS, "row")
                                    if r.getAttribute("r") == rownum), None)
                        if row is None:
                            row = element("row")
                            row.setAttribute("r", rownum)
                            data.appendChild(row)
                        cell = element("c")
                        cell.setAttribute("r", address)
                        row.appendChild(cell)
                    for child in list(cell.childNodes):
                        if child.namespaceURI == NS and child.localName in {"f", "v", "is"}:
                            cell.removeChild(child)
                    if cell.hasAttribute("t"):
                        cell.removeAttribute("t")
                    if isinstance(value, str) and value.startswith("="):
                        append_text(cell, "f", value[1:])
                    elif isinstance(value, str):
                        cell.setAttribute("t", "inlineStr")
                        inline = element("is")
                        cell.appendChild(inline)
                        append_text(inline, "t", value)
                    elif value is not None:
                        append_text(cell, "v", value)
                replacement[part] = tree.toxml(encoding="utf-8")
                tree.unlink()
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as dest:
                for info in archive.infolist():
                    dest.writestr(info, replacement.get(info.filename, archive.read(info.filename)))
        path.write_bytes(output.getvalue())
        return path

    def changes(self, path=None, **kwargs):
        return self.fcn.detect_changes(self.tpl.read_template(path or self.source), self.audit, **kwargs)

    def evaluate(self, path=None, changes=None, kind="patch", **kwargs):
        path = path or self.source
        return self.fcn.evaluate(
            self.root, input_bytes=path.read_bytes(), input_path=path,
            audit_ref=self.ref, audit_sha256=self.binding["audit_sha256"],
            changes=self.changes(path) if changes is None else changes, kind=kind,
            formulas={s + "!" + c: f for (s, c), f in self.formulas.items()}, **kwargs)

    def records(self):
        return {k: v for k, v in self.core.load(self.root)["questions"].items() if k.startswith(PREFIX)}

    def row(self):
        return [r for r in self.notices.notice_rows(self.root) if r["check_id"] == CHECK_ID]

    def register(self, path=None):
        path = path or self.source
        project = self.core.load(self.root)
        revision = project["sources"].get("finance-answer", {}).get("revision", 0) + 1
        (self.root / "finance-answer.txt").write_text("학생이 선택한 재무 파일\n", encoding="utf-8")
        fact = fact_op("current-finance", "intake.current_finance", str(path), "",
                       scope="project", verification="claim_supported",
                       source_id="finance-answer", claim_id="finance")
        fact["value"]["source_refs"][0]["revision"] = revision
        source = source_op("finance-answer.txt", claim_of(fact["value"], "finance"))
        source["value"]["id"] = "finance-answer"
        self.core.apply(self.root, {"request_id": f"register-finance:{revision}",
                                   "ops": [source, fact]}, project["revision"])
        return self.core.load(self.root)["facts"]["current-finance"]

    def shown(self, *, registered=True, kind="patch"):
        self.edit_book({CELL: 17})
        if registered:
            self.register()
        verdict = self.evaluate(kind=kind)
        self.assertEqual((verdict["verdict"], verdict["state"]), ("stop", "shown"))
        return verdict

    def acknowledge(self, verdict):
        result = self.fcn.confirm(self.root, verdict["notice_identity"])
        self.assertEqual(result["status"], "acknowledged", result)
        return self.evaluate(kind=self.records()[verdict["record_id"]]["kind"])

    def complete(self, verdict, output=b"published-output", receipt=b"published-receipt"):
        result = self.fcn.finish(self.root, verdict, output_sha256=sha(output), receipt_sha256=sha(receipt))
        self.assertTrue(result["completed"], result)
        return self.records()[verdict["record_id"]]

    def write_maps(self, source=None, *, expected="=SUM(D8:F8)", new="=SUM(D8:D8)",
                   profile="school_17_sheet_v2"):
        source = source or self.source
        patch_map = self.tmp / "map-patch.json"
        patch_map.write_text(json.dumps({"schema": self.patch.MAP_SCHEMA,
            "source": {"sha256": sha(source.read_bytes())}, "patches": [{
                "sheet": INVEST, "cell": "C8", "expected_formula": expected,
                "new_formula": new, "reason": "합성 시험",
                "evidence_ref": {"source_id": "synthetic", "revision": 1, "locator": "합성"}}]},
            ensure_ascii=False), encoding="utf-8")
        blank_map = self.tmp / "map-blank.json"
        blank_map.write_text(json.dumps({"schema": "gg-xlsx-template-map/v1",
            "source": {"sha256": sha(source.read_bytes())}, "workbook_profile": profile,
            "layout": {"mapOrigin": "default_x01"}, "mapStatus": "ok", "entries": [{
                "sheet": SALES, "cell": "B2", "action": "clear", "role": "input.student_estimate",
                "explicit": True, "reason": "합성"}]}, ensure_ascii=False), encoding="utf-8")
        return patch_map, blank_map

    def cli(self, kind, *, source=None, name="copy", maps=None):
        source = source or self.source
        maps = maps or self.write_maps(source)
        out, receipt = self.tmp / (name + ".xlsx"), self.tmp / (name + ".json")
        args = ["--source", str(source), "--map", str(maps[0 if kind == "patch" else 1]),
                "--out", str(out), "--receipt", str(receipt), "--project", str(self.root),
                "--major", "specialty_crops"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.patch.cli(args) if kind == "patch" else self.tpl.cli(["clear", *args])
        return code, stdout.getvalue(), stderr.getvalue(), out, receipt

    def put_question(self, record, request_id):
        self.core.apply(self.root, {"request_id": request_id, "ops": [{
            "collection": "questions", "value": record}]}, self.core.load(self.root)["revision"])


class RecordContractTests(FormulaNoticeCase):
    def test_apply_accepts_each_transition_without_sources(self):
        # §7-31,32: 실제 apply 검증기, 전이마다 레코드 한 건만 변경.
        self.assertEqual(self.core.load(self.root)["sources"], {})
        revision = self.core.load(self.root)["revision"]
        first = self.shown(registered=False)
        self.assertEqual(self.core.load(self.root)["revision"], revision + 1)
        shown = self.records()[first["record_id"]]
        self.assertEqual((shown["state"], shown["attempts"], shown["decision_revision"]), ("shown", 1, 1))
        ack = self.acknowledge(first)
        self.assertEqual(self.core.load(self.root)["revision"], revision + 2)
        acknowledged = self.records()[first["record_id"]]
        self.assertEqual((acknowledged["state"], acknowledged["attempts"], acknowledged["decision_revision"]),
                         ("acknowledged", 2, 1))
        self.assertEqual(set(self.records()), {first["record_id"]})
        done = self.complete(ack)
        self.assertEqual((done["state"], done["attempts"]), ("completed", 3))
        before = copy.deepcopy(self.records())
        revision = self.core.load(self.root)["revision"]
        self.complete(ack, b"second-output", b"second-receipt")
        after = self.records()
        self.assertEqual(set(after), {first["record_id"]})
        self.assertEqual(self.core.load(self.root)["revision"], revision + 1)
        self.assertEqual(after[first["record_id"]]["outputs"], before[first["record_id"]]["outputs"] + [
            {"sha256": sha(b"second-output"), "receipt_sha256": sha(b"second-receipt")}])
        self.assertEqual(after[first["record_id"]]["attempts"], 3)
        self.assertEqual(after[first["record_id"]]["decision_revision"], 1)
        self.assertEqual(after[first["record_id"]]["field_id"], first["record_id"])
        self.assertEqual(self.core.load(self.root)["sources"], {})
        self.edit_book({OTHER: 18})
        second = self.evaluate()
        self.assertEqual(second["state"], "shown")
        self.assertEqual(self.records()[first["record_id"]], after[first["record_id"]])
        self.assertEqual(len(self.records()), 2)

    def test_existing_accounting_choice_is_unchanged(self):
        # §7-33
        choice = {"id": self.notices.CHOICE_FIELD, "field_id": self.notices.CHOICE_FIELD,
                  "attempts": 1, "decision_revision": 1, "choice": "direct_tell"}
        self.put_question(choice, "synthetic-choice")
        before = canonical(self.core.load(self.root)["questions"][self.notices.CHOICE_FIELD])
        self.complete(self.acknowledge(self.shown(registered=False)))
        self.assertEqual(canonical(self.core.load(self.root)["questions"][self.notices.CHOICE_FIELD]), before)

    def test_notice_does_not_add_submission_blocks_or_fact_questions(self):
        # §7-34: gate의 안내 행은 허용하되 기존 검사 결과·차단은 바꾸지 않음.
        def signature():
            rows = self.core.gate(self.root, self.core.load(self.root))
            return sorted((r["check_id"], r["target"], r["status"], r["severity"])
                          for r in rows if r["check_id"] != CHECK_ID)
        before = signature()
        question = self.core.question(self.core.load(self.root), "finance.target")
        self.complete(self.acknowledge(self.shown(registered=False)))
        self.assertEqual(signature(), before)
        self.assertEqual(self.core.question(self.core.load(self.root), "finance.target"), question)
        self.assertEqual((self.row()[0]["severity"], self.row()[0]["required_for"]), ("info", []))

    def test_transition_retransmission_is_idempotent(self):
        # §7-60: 재전송한 apply 자체도 개정과 요청 장부를 늘리지 않음.
        captured = []
        real_apply = self.core.apply
        def capture(root, change, revision):
            captured.append(copy.deepcopy(change))
            return real_apply(root, change, revision)
        with mock.patch.object(self.core, "apply", side_effect=capture):
            self.shown(registered=False)
        change = next(c for c in captured if c["request_id"].endswith(":shown"))
        before = self.core.load(self.root)
        reconstructed = copy.deepcopy(next(iter(self.records().values())))
        reconstructed.pop("revision", None)
        self.assertEqual(reconstructed, change["ops"][0]["value"],
                         "저장 메타데이터를 뺀 기록과 최초 apply payload가 다름")
        self.core.apply(self.root, change, before["revision"])
        after = self.core.load(self.root)
        self.assertEqual((after["revision"], after["requests"], after["questions"]),
                         (before["revision"], before["requests"], before["questions"]))

    def test_repeated_stops_do_not_write_ledger(self):
        # §7-17,61
        first = self.shown()
        before = self.core.load(self.root)
        with mock.patch.object(self.core, "apply", wraps=self.core.apply) as apply:
            for _ in range(5):
                verdict = self.evaluate()
                self.assertEqual((verdict["verdict"], verdict["record_id"], verdict["written"]),
                                 ("stop", first["record_id"], False))
            apply.assert_not_called()
        after = self.core.load(self.root)
        self.assertEqual((after["revision"], after["requests"], after["questions"]),
                         (before["revision"], before["requests"], before["questions"]))

    def test_lineage_hash_uses_entire_key(self):
        # §7-76,77,80
        keys = ["reg:X@1", "reg:X@2", "reg:X@3", "reg:X@12"]
        ids = [self.fcn.lineage_record_id(key, 1) for key in keys]
        self.assertEqual(len(set(ids)), len(keys))
        for key, rid in zip(keys, ids):
            self.assertEqual(rid, PREFIX + sha(key.encode("utf-8"))[:32] + ".s1")
        first = self.shown()
        old = canonical(self.records()[first["record_id"]])
        self.register()
        second = self.evaluate()
        self.assertNotEqual(first["record_id"], second["record_id"])
        self.assertEqual(second["seq"], 1)
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_lineage_identifier_collision_preserves_existing_record(self):
        # §7-78: 공개 기록 id 함수만 충돌시키고 실제 apply 기록은 보존.
        first = self.shown()
        self.register()
        before = self.core.load(self.root)
        with mock.patch.object(self.fcn, "lineage_record_id", return_value=first["record_id"]):
            result = self.evaluate()
        self.assertEqual((result["verdict"], result["reason"]), ("stop", "lineage_id_collision"))
        after = self.core.load(self.root)
        self.assertEqual((after["revision"], after["questions"]), (before["revision"], before["questions"]))

    def test_competing_first_write_is_reread_not_overwritten(self):
        # §7-27,62,79: apply 직전 다른 실행을 먼저 커밋하는 결정적 경쟁.
        self.edit_book({CELL: 17})
        real_apply = self.core.apply
        winner = []
        def compete(root, change, revision):
            if not winner and any(op["value"].get("state") == "shown" for op in change["ops"]):
                winner.append(copy.deepcopy(change))
                real_apply(root, change, revision)
            return real_apply(root, change, revision)
        with mock.patch.object(self.core, "apply", side_effect=compete):
            result = self.evaluate()
        self.assertEqual((result["verdict"], result["state"]), ("stop", "shown"))
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(next(iter(self.records().values())), winner[0]["ops"][0]["value"] | {
            "revision": next(iter(self.records().values()))["revision"]})


class DetectionTests(FormulaNoticeCase):
    def test_registered_original_and_student_inputs_are_not_changes(self):
        # §7-1,3,8: v1·v2 모두, 감사에 없는 입력 칸의 새 식은 제외.
        for source, ref in ((self.tpl.LEGACY_TEMPLATE, "X01"), (self.tpl.BUNDLED_TEMPLATE, self.ref)):
            with self.subTest(ref=ref):
                binding = json.loads(self.tpl.CROP_LABELS_PATH.read_text(encoding="utf-8"))["templates"][ref]
                audit = self.tpl.registered_audit(binding)
                self.source.write_bytes(source.read_bytes())
                self.assertEqual(self.fcn.detect_changes(self.tpl.read_template(self.source), audit), [])
                self.edit_book({(SALES, "B2"): "합성 작목"})
                self.assertEqual(self.fcn.detect_changes(self.tpl.read_template(self.source), audit), [])
                self.edit_book({(SALES, "B2"): "=100+200"})
                self.assertEqual(self.fcn.detect_changes(self.tpl.read_template(self.source), audit), [])

    def test_value_formula_and_empty_each_have_one_change(self):
        # §7-2,8
        for source, ref in ((self.tpl.LEGACY_TEMPLATE, "X01"), (self.tpl.BUNDLED_TEMPLATE, self.ref)):
            binding = json.loads(self.tpl.CROP_LABELS_PATH.read_text(encoding="utf-8"))["templates"][ref]
            audit = self.tpl.registered_audit(binding)
            for value, kind in ((13, "value"), ("직접 입력", "value"), ("=0", "formula"), (None, "empty")):
                with self.subTest(ref=ref, kind=kind, value=value):
                    self.source.write_bytes(source.read_bytes())
                    self.edit_book({CELL: value})
                    changes = self.fcn.detect_changes(self.tpl.read_template(self.source), audit)
                    self.assertEqual([(c["sheet"], c["cell"], c["kind"]) for c in changes], [(*CELL, kind)])
                    self.assertEqual(changes[0]["content_fp"], sha(b"0") if kind == "formula" else "")

    def test_observation_block_is_not_a_student_formula_change(self):
        # §7-4
        self.edit_book({(LABOR, "X26"): None, (LABOR, "Y27"): 100, (LABOR, "Z31"): 200})
        self.assertEqual(self.changes(), [])

    def test_expected_tool_formula_is_exempt_but_third_formula_is_not(self):
        # §7-5
        key = (INVEST, "C8")
        self.edit_book({key: "=SUM(D8:D8)"})
        self.assertEqual(self.changes(patched_formulas={key: "=SUM(D8:D8)"}), [])
        self.edit_book({key: "=SUM(D8:E8)"})
        self.assertEqual([(c["sheet"], c["cell"], c["kind"]) for c in self.changes(
            patched_formulas={key: "=SUM(D8:D8)"})], [(*key, "formula")])

    def test_expanded_shared_formulas_are_equivalent(self):
        # §7-6
        from openpyxl import load_workbook
        book = load_workbook(self.source)
        book.save(self.source)
        book.close()
        self.assertEqual(self.changes(), [])

    def test_registered_receipt_requires_matching_bytes_and_cell(self):
        # §7-7
        self.edit_book({CELL: "=0", OTHER: "=1"})
        digest = sha(self.source.read_bytes())
        receipt = {"output": {"sha256": digest}, "patched": [{"sheet": CELL[0], "cell": CELL[1]}]}
        self.assertEqual([(c["sheet"], c["cell"]) for c in self.changes(
            input_sha256=digest, registered_receipts=[receipt])], [OTHER])
        for receipts in ([], [{**receipt, "output": {"sha256": "0" * 64}}]):
            with self.subTest(receipts=receipts):
                self.assertEqual(len(self.changes(input_sha256=digest, registered_receipts=receipts)), 2)


class IdentityTests(FormulaNoticeCase):
    def test_change_id_ignores_input_values_and_overwritten_numbers(self):
        # §7-10,13
        self.edit_book({CELL: 17})
        first = self.evaluate()
        self.edit_book({CELL: 99, (SALES, "B2"): "입력값 변경"})
        second = self.evaluate()
        self.assertEqual(first["change_id"], second["change_id"])
        self.assertNotEqual(first["lineage_key"], second["lineage_key"])

    def test_added_restored_and_different_formula_change_identity(self):
        # §7-11,12
        self.edit_book({CELL: "=0"})
        first = self.evaluate()["change_id"]
        self.edit_book({OTHER: 17})
        added = self.evaluate()["change_id"]
        self.assertNotEqual(first, added)
        self.edit_book({OTHER: "=" + self.formulas[OTHER], CELL: "=1"})
        self.assertNotEqual(self.evaluate()["change_id"], first)
        self.assertNotEqual(self.evaluate()["change_id"], added)

    def test_same_change_different_lineage_has_distinct_notice_and_record(self):
        # §7-14,77
        first = self.shown(registered=False)
        self.edit_book({(SALES, "B2"): "다른 입력값"})
        second = self.evaluate()
        self.assertEqual(first["change_id"], second["change_id"])
        for key in ("lineage_key", "notice_identity", "record_id"):
            self.assertNotEqual(first[key], second[key])
        self.assertEqual(len(self.records()), 2)

    def test_identity_matches_documented_canonical_preimage(self):
        # §7-14,80: 기대값은 제품의 digest 함수와 독립 계산.
        changes = [{"sheet": SALES, "cell": "E22", "kind": "formula", "content_fp": sha(b"0")}]
        cid = self.fcn.change_id("project", self.ref, "a" * 64, changes)
        self.assertEqual(cid, sha(canonical({"project_id": "project", "kind": "formula_cells_changed",
            "profile": self.ref, "audit_sha256": "a" * 64, "changes": changes})))
        self.assertEqual(self.fcn.notice_identity("reg:X@12", cid, 3), sha(canonical({
            "lineage_key": "reg:X@12", "change_id": cid, "seq": 3})))


class StateTransitionTests(FormulaNoticeCase):
    def test_first_stop_acknowledge_and_publication_for_both_copies(self):
        # §7-16,17,18: 실제 CLI 종료·파일·영수증·알림 행까지 확인.
        for kind in ("patch", "blank"):
            with self.subTest(kind=kind):
                self.root = self.make_project()
                bind_major(self.root)
                self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
                self.edit_book({CELL: 17})
                self.register()
                maps = self.write_maps()
                for _ in range(3):
                    code, text, error, out, receipt = self.cli(kind, name=kind, maps=maps)
                    self.assertEqual(code, 2, (text, error))
                    self.assertTrue(text.strip(), (text, error))
                    self.assertEqual(json.loads(text)["reason"], "formula_cells_changed")
                    self.assertFalse(out.exists())
                    self.assertFalse(receipt.exists())
                    self.assertEqual(list(self.tmp.glob(".*tmp-*")), [])
                    self.assertEqual(len(self.records()), 1)
                row = self.row()[0]
                self.assertEqual(len(self.row()), 1)
                ack = self.fcn.confirm(self.root, row["notice_identity"])
                self.assertEqual(ack["status"], "acknowledged")
                code, text, error, out, receipt = self.cli(kind, name=kind, maps=maps)
                self.assertEqual(code, 0, (text, error))
                record = next(iter(self.records().values()))
                published = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(record["state"], "completed")
                self.assertEqual(published["formula_cell_notice"]["acknowledged_by"], "app_click")
                self.assertEqual(record["outputs"], [{"sha256": sha(out.read_bytes()),
                    "receipt_sha256": sha(receipt.read_bytes())}])
                actual = self.tpl.read_template(out).cells[CELL]
                self.assertEqual(actual["kind"], "n")
                self.assertEqual(float(actual["value"]), 17)
                self.assertNotIn("confirm", self.row()[0])

    def test_reverted_marker_creates_fresh_cycle(self):
        # §7-19,48: shown/acknowledged를 되돌려도 과거 확인은 재사용 불가.
        for acknowledged in (False, True):
            for kind in ("patch", "blank"):
                with self.subTest(acknowledged=acknowledged, kind=kind):
                    self.root = self.make_project()
                    self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
                    first = self.shown(kind=kind)
                    if acknowledged:
                        self.acknowledge(first)
                    old = canonical(self.records()[first["record_id"]])
                    self.edit_book({CELL: "=" + self.formulas[CELL]})
                    reverted = self.evaluate(kind=kind)
                    self.assertEqual((reverted["verdict"], reverted["state"], reverted["seq"]),
                                     ("proceed", "reverted", 2))
                    self.assertEqual(self.row(), [])
                    self.edit_book({CELL: 17})
                    again = self.evaluate(kind=kind)
                    self.assertEqual((again["verdict"], again["state"], again["seq"]), ("stop", "shown", 3))
                    self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_normal_second_file_does_not_clear_first_notice(self):
        # §7-20,21
        first = self.shown()
        original = canonical(self.records()[first["record_id"]])
        normal = self.tmp / "정상.xlsx"
        normal.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
        self.assertEqual(self.evaluate(path=normal)["verdict"], "proceed")
        self.assertEqual(self.evaluate()["verdict"], "stop")
        self.assertEqual(canonical(self.records()[first["record_id"]]), original)
        self.complete(self.acknowledge(first))
        self.edit_book({CELL: 17, (SALES, "B2"): "다른 사본"}, path=normal)
        self.assertEqual(self.evaluate(path=normal)["state"], "shown")

    def test_changed_cells_after_ack_or_done_start_new_record(self):
        # §7-24,45,52
        for completed in (False, True):
            with self.subTest(completed=completed):
                self.root = self.make_project()
                self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
                first = self.shown()
                ack = self.acknowledge(first)
                if completed:
                    self.complete(ack)
                old = canonical(self.records()[first["record_id"]])
                self.edit_book({OTHER: 18})
                second = self.evaluate()
                self.assertEqual((second["verdict"], second["state"], second["seq"]), ("stop", "shown", 2))
                self.assertNotEqual(second["change_id"], first["change_id"])
                self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_failed_record_write_never_allows_creation(self):
        # §7-26,46,47: 최초 shown·새 shown·reverted 모두 저장 실패면 멈춤.
        for previous in ("none", "completed", "shown"):
            with self.subTest(previous=previous):
                self.root = self.make_project()
                self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
                first = self.shown() if previous != "none" else None
                if previous == "completed":
                    self.complete(self.acknowledge(first))
                    self.edit_book({OTHER: 18})
                elif previous == "shown":
                    self.edit_book({CELL: "=" + self.formulas[CELL]})
                else:
                    self.edit_book({CELL: 17})
                before = self.core.load(self.root)
                with mock.patch.object(self.core, "apply", side_effect=ValueError("synthetic lock conflict")):
                    result = self.evaluate()
                self.assertEqual((result["verdict"], result["reason"]), ("stop", "formula_cell_notice_record"))
                after = self.core.load(self.root)
                self.assertEqual((after["revision"], after["questions"]), (before["revision"], before["questions"]))
                if previous == "shown":
                    self.assertEqual(self.evaluate()["state"], "reverted")
                    self.edit_book({CELL: 17})
                    self.assertEqual(self.evaluate()["state"], "shown")

    def test_completed_write_failure_keeps_neutral_notice(self):
        # §7-23
        bind_major(self.root)
        first = self.shown()
        self.acknowledge(first)
        with mock.patch.object(self.fcn, "finish", return_value={"completed": False, "reason": "synthetic failure"}):
            code, text, error, out, receipt = self.cli("patch")
        self.assertEqual(code, 0, (text, error))
        self.assertTrue(out.is_file())
        self.assertTrue(receipt.is_file())
        self.assertEqual(self.records()[first["record_id"]]["state"], "acknowledged")
        self.assertNotIn("만들었어요", self.row()[0]["title"])
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["formula_cell_notice_error"], "synthetic failure")

    def test_mapping_failure_after_ack_is_retryable_without_new_notice(self):
        # §7-22,63: 지도 선행식 불일치 실패 뒤 정상 지도로 재시도.
        bind_major(self.root)
        first = self.shown()
        self.acknowledge(first)
        wrong = self.write_maps(expected="=999")
        code, text, error, out, receipt = self.cli("patch", maps=wrong)
        self.assertNotEqual(code, 0, (text, error))
        self.assertFalse(out.exists())
        self.assertFalse(receipt.exists())
        self.assertEqual(self.records()[first["record_id"]]["state"], "acknowledged")
        self.assertNotIn("만들었어요", self.row()[0]["title"])
        code, text, error, out, receipt = self.cli("patch", name="retry")
        self.assertEqual(code, 0, (text, error))
        self.assertEqual(self.records()[first["record_id"]]["state"], "completed")

    def test_done_response_loss_and_additional_output_preserve_history(self):
        # §7-65
        first = self.shown()
        ack = self.acknowledge(first)
        real_apply = self.core.apply
        lost = []
        def lose_response(root, change, revision):
            result = real_apply(root, change, revision)
            if change["request_id"].endswith(":done") and not lost:
                lost.append(True)
                raise OSError("synthetic response loss after commit")
            return result
        with mock.patch.object(self.core, "apply", side_effect=lose_response):
            self.complete(ack)
        done = self.records()[first["record_id"]]
        self.assertEqual(len(done["outputs"]), 1)
        self.complete(self.evaluate(), b"different-output", b"different-receipt")
        after = self.records()[first["record_id"]]
        self.assertEqual(after["outputs"], done["outputs"] + [{
            "sha256": sha(b"different-output"), "receipt_sha256": sha(b"different-receipt")}])

    def test_conflict_cells_never_create_a_cycle_but_blank_can(self):
        # §7-29,30
        bind_major(self.root)
        self.edit_book({(INVEST, "C8"): "=0", CELL: 17})
        maps = self.write_maps()
        code, text, error, out, receipt = self.cli("patch", maps=maps)
        self.assertEqual(code, 2, (text, error))
        message = json.loads(text)
        self.assertEqual(message["reason"], "formula_cell_conflict")
        self.assertIn("C8", message["guidance"])
        self.assertIn("빈 사본", message["guidance"])
        self.assertIn(SALES + "!E22", message["detail"]["cells"])
        self.assertFalse(out.exists())
        self.assertFalse(receipt.exists())
        self.assertEqual(self.records(), {})
        self.assertEqual(self.row(), [])
        code, text, error, _, _ = self.cli("blank", name="conflict-blank", maps=maps)
        self.assertEqual(code, 2, (text, error))
        self.assertEqual(json.loads(text)["reason"], "formula_cells_changed")
        self.assertEqual(len(self.records()), 1)
        self.edit_book({(INVEST, "C8"): "=" + self.formulas[(INVEST, "C8")]})
        code, text, error, out, receipt = self.cli("patch", name="conflict-repaired")
        self.assertEqual(code, 2, (text, error))
        self.assertEqual(json.loads(text)["reason"], "formula_cells_changed")
        active = self.evaluate()
        self.assertEqual([(c["sheet"], c["cell"]) for c in active["cells"]], [CELL])
        self.acknowledge(active)
        code, text, error, out, receipt = self.cli("patch", name="conflict-repaired")
        self.assertEqual(code, 0, (text, error))
        self.assertTrue(out.is_file())
        self.assertTrue(receipt.is_file())

    def test_no_project_cannot_bypass_changed_cells(self):
        # §7-26 / INTERFACE §6: 기록 없는 통과 경로 금지.
        self.edit_book({CELL: 17})
        result = self.fcn.evaluate(None, input_bytes=self.source.read_bytes(), input_path=self.source,
            audit_ref=self.ref, audit_sha256=self.binding["audit_sha256"], changes=self.changes())
        self.assertEqual((result["verdict"], result["reason"]), ("stop", "formula_cell_notice_record"))


class ConfirmationTests(FormulaNoticeCase):
    def test_changed_or_deleted_file_rejects_stale_confirmation(self):
        # §7-49,50
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                self.root = self.make_project()
                self.source.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
                first = self.shown()
                if deleted:
                    self.source.unlink()
                else:
                    self.edit_book({OTHER: 18})
                before = self.core.load(self.root)
                result = self.fcn.confirm(self.root, first["notice_identity"])
                self.assertEqual((result["status"], result["reason"]), ("rejected", "notice_stale"))
                self.assertEqual(self.core.load(self.root), before)

    def test_inactive_and_already_acknowledged_identity_is_rejected(self):
        # §7-51
        first = self.shown()
        self.edit_book({OTHER: 18})
        second = self.evaluate()
        before = self.core.load(self.root)
        result = self.fcn.confirm(self.root, first["notice_identity"])
        self.assertEqual((result["status"], result["reason"]), ("rejected", "record_not_active"))
        self.assertEqual(self.core.load(self.root), before)
        self.acknowledge(second)
        result = self.fcn.confirm(self.root, second["notice_identity"])
        self.assertEqual(result["status"], "rejected")

    def test_relayed_requires_quote_and_receipt_retains_attribution(self):
        # §7-53: CLI 선언과 앱 클릭은 서로 다른 확인 근거.
        bind_major(self.root)
        first = self.shown()
        before = self.core.load(self.root)
        for quote in (None, "", "  "):
            result = self.fcn.confirm(self.root, first["notice_identity"], by="relayed", relayed_quote=quote)
            self.assertEqual((result["status"], result["reason"]), ("rejected", "relayed_quote_missing"))
        self.assertEqual(self.core.load(self.root), before)
        text = io.StringIO()
        with redirect_stdout(text):
            code = self.notices.main(["confirm", str(self.root), "--identity", first["notice_identity"],
                                      "--relayed-quote", "일부러 바꿨어요", "--json"])
        self.assertEqual(code, 0, text.getvalue())
        self.assertEqual(json.loads(text.getvalue())["acknowledged_by"], "relayed")
        code, text, error, _, receipt = self.cli("blank")
        self.assertEqual(code, 0, (text, error))
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["formula_cell_notice"]["acknowledged_by"], "relayed")

    def test_settings_subcommands_are_absent(self):
        # INTERFACE §5의 부재 계약만: §12 기능 시험(81~90)은 작성하지 않음.
        for command in ("dont-ask", "settings", "disable"):
            with self.subTest(command=command), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self.notices.main([command, str(self.root)])
                self.assertEqual(caught.exception.code, 2)


class LineageTests(FormulaNoticeCase):
    def test_registered_input_changes_reuse_ack_but_reregistration_does_not(self):
        # §7-54,56,76: 실제 apply가 사실 개정을 올리는지 포함.
        first = self.shown()
        self.complete(self.acknowledge(first))
        old = canonical(self.records()[first["record_id"]])
        self.edit_book({CELL: 999, (SALES, "B2"): "다른 입력"})
        same = self.evaluate()
        self.assertEqual((same["verdict"], same["change_id"], same["lineage_key"]),
                         ("proceed", first["change_id"], first["lineage_key"]))
        fact = self.register()
        self.assertEqual(fact["revision"], 2)
        second = self.evaluate()
        self.assertEqual((second["verdict"], second["state"], second["seq"]), ("stop", "shown", 1))
        self.assertEqual(second["lineage_key"], "reg:current-finance@2")
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)
        second_bytes = canonical(self.records()[second["record_id"]])
        self.register()
        third = self.evaluate()
        self.assertEqual(third["lineage_key"], "reg:current-finance@3")
        self.assertEqual(len({first["record_id"], second["record_id"], third["record_id"]}), 3)
        self.assertEqual(canonical(self.records()[second["record_id"]]), second_bytes)

    def test_registered_replacement_with_other_change_stops(self):
        # §7-55
        first = self.shown()
        self.complete(self.acknowledge(first))
        self.edit_book({CELL: "=0"})
        second = self.evaluate()
        self.assertEqual((second["verdict"], second["seq"]), ("stop", 2))
        self.assertNotEqual(second["change_id"], first["change_id"])

    def test_unregistered_input_edit_starts_new_lineage(self):
        # §7-57
        first = self.shown(registered=False)
        self.acknowledge(first)
        self.edit_book({(SALES, "B2"): "입력값 저장"})
        second = self.evaluate()
        self.assertEqual((second["verdict"], second["state"]), ("stop", "shown"))
        self.assertNotEqual(second["lineage_key"], first["lineage_key"])

    def published_copy(self):
        first = self.shown()
        ack = self.acknowledge(first)
        output = self.source.read_bytes()
        self.complete(ack, output, b"receipt")
        copied = self.tmp / "도구 사본.xlsx"
        copied.write_bytes(output)
        return first, copied

    def test_output_inherits_into_new_record_without_touching_original(self):
        # §7-58,72
        first, copied = self.published_copy()
        old = canonical(self.records()[first["record_id"]])
        before = set(self.records())
        result = self.evaluate(path=copied)
        self.assertEqual((result["verdict"], result["state"], result["acknowledged_by"]),
                         ("proceed", "acknowledged", "inherited"))
        self.assertEqual(result["lineage_key"], "file:" + sha(copied.read_bytes()))
        record = self.records()[result["record_id"]]
        self.assertEqual(record["inherited_from"], first["record_id"])
        self.assertEqual(set(self.records()) - before, {record["id"]})
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_registering_output_beats_inheritance(self):
        # §7-71,75
        first, copied = self.published_copy()
        old = canonical(self.records()[first["record_id"]])
        fact = self.register(copied)
        result = self.evaluate(path=copied)
        self.assertEqual((result["verdict"], result["state"], result["acknowledged_by"]),
                         ("stop", "shown", None))
        self.assertEqual(result["lineage_key"], f"reg:current-finance@{fact['revision']}")
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_modified_output_or_same_name_other_file_does_not_inherit(self):
        # §7-59,68: 완료 기록은 출력의 현재성 보장이 아님.
        first, copied = self.published_copy()
        old = canonical(self.records()[first["record_id"]])
        self.edit_book({(SALES, "B2"): "사본 입력 변경"}, path=copied)
        result = self.evaluate(path=copied)
        self.assertEqual((result["verdict"], result["state"], result["acknowledged_by"]), ("stop", "shown", None))
        copied.unlink()
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)
        copied.write_bytes(self.tpl.BUNDLED_TEMPLATE.read_bytes())
        self.edit_book({CELL: "=0"}, path=copied)
        self.assertEqual(self.evaluate(path=copied)["state"], "shown")
        self.assertEqual(canonical(self.records()[first["record_id"]]), old)

    def test_ambiguous_output_lineages_do_not_inherit_in_either_order(self):
        # §7-73
        first, copied = self.published_copy()
        original = copy.deepcopy(self.records()[first["record_id"]])
        other = {**original, "lineage_key": "reg:other@1"}
        other["id"] = other["field_id"] = self.fcn.lineage_record_id(other["lineage_key"], 1)
        other["notice_identity"] = self.fcn.notice_identity(other["lineage_key"], other["change_id"], 1)
        self.put_question(other, "synthetic-second-lineage")
        before = self.core.load(self.root)
        for reverse in (False, True):
            project = copy.deepcopy(before)
            if reverse:
                project["questions"] = dict(reversed(list(project["questions"].items())))
            with mock.patch.object(self.core, "load", return_value=project), mock.patch.object(
                    self.core, "apply", return_value=None) as apply:
                result = self.evaluate(path=copied)
            self.assertEqual((result["verdict"], result["state"], result["acknowledged_by"]),
                             ("stop", "shown", None))
            self.assertEqual(apply.call_args.args[1]["ops"][0]["value"]["state"], "shown")
        self.assertEqual(self.core.load(self.root), before)

    def test_latest_matching_cycle_is_inherited_independent_of_record_order(self):
        # §7-74
        first, copied = self.published_copy()
        record = copy.deepcopy(self.records()[first["record_id"]])
        record.update(seq=2)
        record["id"] = record["field_id"] = self.fcn.lineage_record_id(record["lineage_key"], 2)
        record["notice_identity"] = self.fcn.notice_identity(record["lineage_key"], record["change_id"], 2)
        self.put_question(record, "synthetic-later-cycle")
        before = self.core.load(self.root)
        for reverse in (False, True):
            project = copy.deepcopy(before)
            if reverse:
                project["questions"] = dict(reversed(list(project["questions"].items())))
            with mock.patch.object(self.core, "load", return_value=project), mock.patch.object(
                    self.core, "apply", return_value=None) as apply:
                result = self.evaluate(path=copied)
            self.assertEqual((result["verdict"], result["acknowledged_by"]), ("proceed", "inherited"))
            self.assertEqual(apply.call_args.args[1]["ops"][0]["value"]["inherited_from"], record["id"])


class NoticeRowsTests(FormulaNoticeCase):
    def test_written_rows_match_checks_byte_for_byte(self):
        # §7-35: 알림 파일은 통합문서를 다시 읽지 않고 기록만 사용.
        first = self.shown()
        for state in ("shown", "acknowledged", "completed"):
            if state == "acknowledged":
                ack = self.acknowledge(first)
            elif state == "completed":
                self.complete(ack)
            with mock.patch.object(self.tpl, "read_template", side_effect=AssertionError("알림에서 엑셀 읽음")):
                self.notices.write_notices(self.root)
                full = [r for r in self.core.checks(self.root, self.core.load(self.root)) if r["check_id"] == CHECK_ID]
                written = json.loads((self.root / ".knuaf-notices.json").read_text(encoding="utf-8"))["notices"]
            thin = [r for r in written if r["check_id"] == CHECK_ID]
            self.assertEqual(canonical(thin), canonical(full))

    def test_row_fields_and_cell_addresses_have_no_formula_payload(self):
        # §7-15,36
        first = self.shown()
        row = self.row()[0]
        self.assertEqual({k: row[k] for k in ("target", "status", "severity", "required_for", "dismissible", "redisplay_rule")},
            {"target": "xlsx", "status": "check_needed", "severity": "info", "required_for": [],
             "dismissible": True, "redisplay_rule": "identity_changed"})
        for key in ("title", "notice_identity", "project_id"):
            self.assertTrue(row[key])
        self.assertEqual(row["record_refs"], [{"collection": "questions", "id": first["record_id"]}])
        self.assertEqual(row["confirm"], {"label": "확인했어요, 그대로 만들어 주세요"})
        self.assertEqual(row["items"][0]["cells"], [SALES + "!E22"])
        self.assertEqual(row["items"][0]["kind"], "other")
        self.assertIn("what", row["items"][0])
        self.assertIn("how", row["items"][0])

    def test_fifty_one_cells_are_one_row_with_overflow_item(self):
        # §7-36: confirm은 유일한 첫 행, 주소는 첫 50개 + 외 1곳.
        cells = [{"sheet": "합성 시트", "cell": f"A{i}", "kind": "value", "content_fp": ""} for i in range(1, 52)]
        self.evaluate(changes=cells)
        rows = self.row()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["items"]), 51)
        self.assertEqual(rows[0]["items"][-1]["what"], "외 1곳")
        self.assertEqual(rows[0]["items"][-1]["cells"], [])
        self.assertEqual(rows[0]["items"][-1]["key"], "more")
        self.assertIn("confirm", rows[0])

    def test_repair_guidance_uses_exact_audit_formula_or_plain_fallback(self):
        # §7-37
        cells = [{"sheet": CELL[0], "cell": CELL[1], "kind": "value", "content_fp": ""}]
        text = self.fcn.texts("shown", label="재무계획.xlsx", count=1, cells=cells,
                              formulas={SALES + "!E22": self.formulas[CELL]})
        self.assertIn(self.formulas[CELL], text["items"][0]["how"])
        fallback = self.fcn.texts("shown", label="재무계획.xlsx", count=1, cells=cells)
        self.assertIn("학교에서 받은 원본 양식", fallback["items"][0]["how"])
        self.assertNotIn(self.formulas[CELL], fallback["items"][0]["how"])

    def test_student_phrases_have_no_internal_terms_or_false_success(self):
        # §7-38: 지정된 학생 문구 정본만 검사, 소스 텍스트 고정 아님.
        forbidden = (*FORBIDDEN_STUDENT_TERMS, "change_id", "lineage_key", "acknowledged", "completed",
                     "formula_cells_changed", "formula_cell_conflict", "formula_audit_unavailable", "seq", "sha256")
        for key, phrase in self.fcn.PHRASES.items():
            for term in forbidden:
                with self.subTest(phrase=key, term=term):
                    self.assertNotIn(term, phrase)
        for state in ("shown", "acknowledged"):
            self.assertNotIn("만들었어요", self.fcn.texts(state, label="재무계획.xlsx", count=3)["title"])
        self.assertIn("만들었어요", self.fcn.texts("completed", label="재무계획.xlsx", count=3)["title"])

    def test_two_shown_lineages_produce_two_rows(self):
        # §7-39
        self.shown(registered=False)
        second = self.tmp / "두번째.xlsx"
        second.write_bytes(self.source.read_bytes())
        self.edit_book({(SALES, "B2"): "다른 파일"}, path=second)
        self.evaluate(path=second)
        rows = self.row()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["notice_identity"] for r in rows}), 2)


class CopyBoundaryTests(FormulaNoticeCase):
    def test_unavailable_or_mismatched_audit_holds_both_copy_routes(self):
        # §7-9: 실제 감사 읽기/해시/스키마 검증을 합성 디렉터리에서 실행.
        bind_major(self.root)
        maps = self.write_maps()
        for defect in ("missing", "hash", "schema", "edition"):
            for kind in ("patch", "blank"):
                with self.subTest(defect=defect, kind=kind):
                    directory = self.tmp / ("audit-" + defect + kind)
                    directory.mkdir()
                    binding = copy.deepcopy(self.binding)
                    binding["audit_file"] = "audit.json"
                    audit = copy.deepcopy(self.audit)
                    if defect == "schema":
                        audit["schema"] = "unknown"
                    elif defect == "edition":
                        audit["source_sha256"] = "0" * 64
                    raw = json.dumps(audit).encode("utf-8")
                    if defect != "missing":
                        (directory / "audit.json").write_bytes(raw)
                    binding["audit_sha256"] = "0" * 64 if defect == "hash" else sha(raw)
                    table = json.loads(self.tpl.CROP_LABELS_PATH.read_text(encoding="utf-8"))
                    table["templates"][self.ref] = binding
                    with mock.patch.object(self.tpl, "COMMON_WORKBOOKS_DIR", directory), mock.patch.object(
                            self.tpl, "_crop_label_table", return_value=table):
                        code, text, error, out, receipt = self.cli(kind, name=defect + kind, maps=maps)
                    self.assertEqual(code, 2, (text, error))
                    self.assertEqual(json.loads(text)["reason"], "formula_audit_unavailable")
                    self.assertFalse(out.exists())
                    self.assertFalse(receipt.exists())
                    self.assertEqual(self.records(), {})

    def test_tool_patched_copy_can_be_patched_again_in_both_editions(self):
        # §7-5,7,8 + 책임자 회귀: 자동 등록 근거가 두 번째 패치에 사용됨.
        bind_major(self.root)
        context = self.mc.output_context(self.root, "specialty_crops")
        for template, profile in ((self.tpl.LEGACY_TEMPLATE, "school_17_sheet_v1"),
                                  (self.tpl.BUNDLED_TEMPLATE, "school_17_sheet_v2")):
            with self.subTest(profile=profile):
                self.source.write_bytes(template.read_bytes())
                mapping, _ = self.write_maps()
                first = self.tmp / (profile + "-first.xlsx")
                receipt1 = self.patch.patch_copy(self.source, mapping, first, context=context)
                self.assertEqual(receipt1["workbook_profile"], profile)
                mapping, _ = self.write_maps(first, expected="=SUM(D8:D8)", new="=SUM(D8:E8)")
                second = self.tmp / (profile + "-second.xlsx")
                receipt2 = self.patch.patch_copy(first, mapping, second, context=context)
                self.assertEqual(receipt2["workbook_profile"], profile)
                self.assertEqual(self.tpl.read_template(second).cells[(INVEST, "C8")]["resolved"], "SUM(D8:E8)")
                self.assertEqual(receipt2["output"]["sha256"], sha(second.read_bytes()))
                self.assertEqual(self.records(), {})
                proof = [q for q in self.core.load(self.root)["questions"].values()
                         if q.get("schema") == "knuaf-formula-tool-output/v1"
                         and q.get("output_sha256") == sha(first.read_bytes())]
                self.assertTrue(proof)
                self.assertIn((INVEST, "C8"), {(p["sheet"], p["cell"]) for p in proof[0]["patched"]})

    def test_snapshot_wins_when_source_changes_after_comparison(self):
        # §7-66: 첫 실행 영수증·출력은 확보한 스냅샷, 다음 실행은 새 변경.
        bind_major(self.root)
        first = self.shown()
        self.acknowledge(first)
        snapshot = self.source.read_bytes()
        real_detect = self.fcn.detect_changes
        mutated = []
        def change_original(read, audit, **kwargs):
            result = real_detect(read, audit, **kwargs)
            if not mutated:
                mutated.append(True)
                self.edit_book({OTHER: 18})
            return result
        with mock.patch.object(self.fcn, "detect_changes", side_effect=change_original):
            code, text, error, out, receipt = self.cli("patch")
        self.assertEqual(code, 0, (text, error))
        published = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(published["source"]["sha256"], sha(snapshot))
        self.assertEqual(self.tpl.read_template(out).cells[OTHER]["kind"], "f")
        self.assertEqual(self.evaluate()["state"], "shown")
        self.assertNotEqual(sha(snapshot), sha(self.source.read_bytes()))

    def test_publication_hash_mismatch_never_marks_completed(self):
        # §7-67: 출력 발행 직후 변조 모의, 완료 기록 금지.
        import os
        bind_major(self.root)
        first = self.shown()
        self.acknowledge(first)
        target = (self.tmp / "copy.xlsx").resolve()
        real_replace = os.replace
        changed = []
        def alter_output(source, destination):
            result = real_replace(source, destination)
            if Path(destination).resolve() == target:
                target.write_bytes(target.read_bytes() + b"changed after publication")
                changed.append(True)
            return result
        with mock.patch.object(os, "replace", side_effect=alter_output):
            code, text, error, out, receipt = self.cli("patch")
        self.assertEqual(code, 0, (text, error))
        self.assertTrue(out.is_file())
        self.assertTrue(changed, "출력 발행 뒤 변조 지점에 도달하지 못함")
        self.assertNotEqual(sha(out.read_bytes()), json.loads(receipt.read_text(encoding="utf-8"))["output"]["sha256"])
        self.assertEqual(self.records()[first["record_id"]]["state"], "acknowledged")
        self.assertEqual(self.records()[first["record_id"]]["outputs"], [])
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["formula_cell_notice_error"], "output_hash_mismatch")

    def test_orphan_output_cannot_be_overwritten_and_other_name_can_complete(self):
        # §7-64: 출력만 남은 중단 상태를 직접 구성(프로세스/앱 실행 없음).
        bind_major(self.root)
        first = self.shown()
        self.acknowledge(first)
        mapping, _ = self.write_maps()
        out = self.tmp / "orphan.xlsx"
        self.patch.patch_copy(self.source, mapping, out,
                              context=self.mc.output_context(self.root, "specialty_crops"))
        before = out.read_bytes()
        code, text, error, _, receipt = self.cli("patch", name="orphan")
        self.assertNotEqual(code, 0, (text, error))
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse(receipt.exists())
        self.assertEqual(self.records()[first["record_id"]]["state"], "acknowledged")
        code, text, error, _, _ = self.cli("patch", name="recovered")
        self.assertEqual(code, 0, (text, error))
        self.assertEqual(self.records()[first["record_id"]]["state"], "completed")


class AbcRegressionTests(FormulaNoticeCase):
    def test_six_legacy_inputs_keep_approved_copy_behavior(self):
        # §7-41: 설계 §1 우선(책임자 t119): 변경0 두 종은 즉시 진행.
        # 나머지 셋은 멈춤→확인→받음. C8은 기존 선행식 불일치로 patch 거절.
        # blank에는 E가 없으므로 C8도 일반 변경 확인 흐름(§3-3/INTERFACE).
        scenarios = (
            ("original", {}),
            ("values", {(SALES, "C4"): 1000, (SALES, "D4"): 1000, (SALES, "E4"): 1000}),
            ("overwritten", {(SALES, "C38"): 12345}),
            ("zero", {CELL: "=0"}),
            ("number", {CELL: 12000}),
            ("other_formula", {(INVEST, "C8"): "=SUM(D8:D8)"}),
        )
        for name, edits in scenarios:
            for kind in ("patch", "blank"):
                with self.subTest(name=name, kind=kind):
                    self.root = self.make_project()
                    bind_major(self.root)
                    self.source.write_bytes(self.tpl.LEGACY_TEMPLATE.read_bytes())
                    if edits:
                        self.edit_book(edits)
                    maps = self.write_maps(profile="school_17_sheet_v1")
                    outname = "abc-" + name + "-" + kind
                    code, text, error, out, receipt = self.cli(kind, name=outname, maps=maps)
                    if name == "other_formula" and kind == "patch":
                        self.assertEqual(code, 2, (text, error))
                        self.assertIn("expected formula mismatch", error)
                        self.assertFalse(out.exists())
                        self.assertFalse(receipt.exists())
                        self.assertEqual(self.records(), {})
                        continue
                    if name not in {"original", "values"}:
                        self.assertEqual(code, 2, (text, error))
                        self.assertEqual(json.loads(text)["reason"], "formula_cells_changed")
                        self.assertFalse(out.exists())
                        self.assertFalse(receipt.exists())
                        shown = next(iter(self.records().values()))
                        result = self.fcn.confirm(self.root, shown["notice_identity"])
                        self.assertEqual(result["status"], "acknowledged", result)
                        code, text, error, out, receipt = self.cli(kind, name=outname, maps=maps)
                    else:
                        self.assertEqual(self.records(), {})
                    self.assertEqual(code, 0, (text, error))
                    self.assertTrue(out.is_file())
                    self.assertTrue(receipt.is_file())
                    published = json.loads(receipt.read_text(encoding="utf-8"))
                    self.assertEqual(published["workbook_profile"], "school_17_sheet_v1")


if __name__ == "__main__":
    unittest.main()
