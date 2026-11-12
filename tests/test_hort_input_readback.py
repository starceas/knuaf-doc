"""원예: 채운 학교 엑셀의 251칸을 상태 하나씩 읽어 분류하는 시험 (v0.5.0).

원본 `H-X1`은 저장소에 없다. 그래서 이 파일은 원본 사본이 있을 때만 돈다 —
없거나 지문이 다르면 통과가 아니라, 탐색(`unittest discover`)에서는 시험을
하나도 모으지 않고 이름으로 직접 부르면 **보이는 SKIP**으로 보고한다.
학교 원본은 읽기만 하고, 고친 사본은 임시 폴더에만 만든다(zip 재작성).
Excel·Word는 띄우지 않는다. 학생 실명·개인정보는 쓰지 않는다.

Run (한 번에 하나):
  HIN_X1=<원본 사본 경로> python3 -m unittest \
    tests.test_hort_input_readback.<Class>.<test> -v
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import zipfile

from tests._harness import ContractCase, runtime

#: 원본 사본 경로. `HIN_X1`(이 시험) → `HFIN_H01`(재무 시험 관례) → 개발자 기본값.
#: 기본값은 집 폴더 기준 상대 경로다 — 계정 이름을 저장소에 적지 않는다.
ENV = "HIN_X1"
DEFAULT_ORIGINAL = (
    Path.home() / "work/knuaf-doc/2026-11-11-hort-excel-fidelity"
    / "scratch/src/original-finance.xlsx")
WORKBOOK = Path(os.environ[ENV]) if os.environ.get(ENV) else (
    Path(os.environ["HFIN_H01"]) if os.environ.get("HFIN_H01") else DEFAULT_ORIGINAL)

#: 명세 `source_sha256` = 등록 원천 해시. 이 값이 틀리면 원천 등록이 걸러져
#: `project_invalid`가 되므로 시험 프로젝트는 반드시 이 해시로 등록한다.
SHA = "e3c9defe376fa74413641408900f6bb656017a389fb9745d9dac221a10951c1c"

#: 무변경 원본의 분류 결과(실측). 15키 전부, 합 251.
BASELINE = {
    "not_read_template": 8,
    "not_read_textbook": 11,
    "not_read_footnote": 7,
    "not_read_unclear": 4,
    "constant_overwritten": 0,
    "same_value": 183,
    "constant_deleted": 0,
    "blank_filled": 0,
    "blank_kept": 37,
    "formula_kept": 1,
    "formula_cache_only": 0,
    "formula_replaced": 0,
    "formula_to_constant": 0,
    "formula_deleted": 0,
    "formula_added": 0,
}

SHEET1 = "1. 자산조사"
SHEET4 = "4.투자계획"
SHEET5 = "5. 원리금상환계획"
SHEET11 = "11. 인건비계획"
SHEET15 = "15. 손익계획"


def _source_problem():
    """원본 사본을 쓸 수 없는 이유 — 없으면 None."""
    if not WORKBOOK.is_file():
        return "원본 사본이 없다: %s" % WORKBOOK
    if hashlib.sha256(WORKBOOK.read_bytes()).hexdigest() != SHA:
        return "원본 사본 지문이 명세와 다르다: %s" % WORKBOOK
    return None


def load_tests(loader, tests, pattern):
    """탐색은 원본이 없으면 빈 스위트, 이름 호출은 `setUp`의 보이는 SKIP.

    `load_tests`에서 `SkipTest`를 올리면 unittest는 skip이 아니라 모듈 로딩
    실패(`unittest.loader._FailedTest.<module>`)로 만들어, CI가 이를 미승인
    skip·import 계약 위반으로 판정한다. 그래서 탐색(`pattern` 지정)에서는
    시험을 하나도 모으지 않고, 이름으로 직접 부를 때(`pattern is None`)만
    `setUp`의 `skipTest`가 사유를 보이는 SKIP으로 보고한다.
    """
    if pattern is not None and _source_problem() is not None:
        return unittest.TestSuite()
    return tests


class ReadbackCase(ContractCase):
    """원본 사본을 프로젝트 원천으로 등록하고, 고친 사본만 임시 폴더에 쓴다."""

    def setUp(self):
        problem = _source_problem()
        if problem is not None:
            self.skipTest(problem)
        self.rb = runtime("gg_hort_input_readback")
        self.tpl = runtime("gg_excel_template")
        self.root = self.make_project()
        self.project_path = self.root / "project.json"
        self.canonical = self.root / "src" / "legacy.xlsx"
        self._register_source()
        tmp = tempfile.TemporaryDirectory(prefix="hort-readback-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def _register_source(self):
        project = json.loads(self.project_path.read_text(encoding="utf-8"))
        project["sources"]["H-X1"] = {"id": "H-X1", "path": "src/legacy.xlsx",
                                      "hash": SHA, "revision": 1}
        self.project_path.write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        self.canonical.parent.mkdir()
        self.canonical.write_bytes(WORKBOOK.read_bytes())

    def _copy(self, name="student.xlsx"):
        path = self.tmp / name
        path.write_bytes(WORKBOOK.read_bytes())
        return path

    def _rewrite(self, path, edits_by_sheet):
        """원본 사본 중 지정한 시트만 바꾼 사본 — zip 재작성(Excel 미실행)."""
        with zipfile.ZipFile(WORKBOOK) as archive:
            infos = archive.infolist()
            parts = {info.filename: archive.read(info.filename) for info in infos}
            sheets = dict(self.tpl.workbook_sheets(archive))
        for sheet, edits in edits_by_sheet.items():
            part = sheets[sheet]
            text = parts[part].decode("utf-8")
            for old, new in edits:
                self.assertEqual(text.count(old), 1,
                                 "%s의 조각이 하나가 아니다: %r" % (sheet, old))
                text = text.replace(old, new)
            parts[part] = text.encode("utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
            for info in infos:
                out.writestr(info, parts[info.filename])
        return path

    def _mutated(self, sheet, edits, name="student.xlsx"):
        return self._rewrite(self._copy(name), {sheet: edits})

    def _patch_many(self, edits_by_sheet, name="student.xlsx"):
        return self._rewrite(self._copy(name), edits_by_sheet)

    def _run(self, student, out="build/out.json"):
        return self.rb.run(self.root, student, out)

    def _written(self, value, out="build/out.json"):
        stored = json.loads((self.root / out).read_text(encoding="utf-8"))
        self.assertEqual(stored, value)
        return stored

    def _read(self, student, out="build/out.json"):
        value, code = self._run(student, out)
        self.assertEqual(code, 0, value)
        self.assertEqual(value["status"], "read", value)
        self.assertEqual(value["counts"]["recorded"], 251, value["counts"])
        self._written(value, out)
        return value

    def _held(self, student, out="build/out.json"):
        value, code = self._run(student, out)
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "held", value)
        self.assertEqual(value["cells"], [])
        return value

    def _input_error(self, student, out="build/out.json"):
        """입력 오류 — held·코드 3·분류표 0·출력 파일 없음."""
        value, code = self._run(student, out)
        self.assertEqual(code, 3, value)
        self.assertEqual(value["status"], "held", value)
        self.assertEqual(value["cells"], [])
        self.assertEqual(value["reason"], self.rb.REASON_SNAPSHOT, value)
        self.assertFalse((self.root / out).exists())
        return value

    def _cell(self, value, sheet, ref):
        for row in value["cells"]:
            if row["sheet"] == sheet and row["cell"] == ref:
                return row
        self.fail("%s!%s 이 분류표에 없다" % (sheet, ref))

    def _states(self, value, cells):
        return {key: self._cell(value, *key)["state"] for key in cells}

    def _raw(self, sheet):
        with zipfile.ZipFile(WORKBOOK) as archive:
            return archive.read(dict(self.tpl.workbook_sheets(archive))[sheet]) \
                .decode("utf-8")

    def _sheet_bytes(self, sheet):
        with zipfile.ZipFile(WORKBOOK) as archive:
            return archive.read(dict(self.tpl.workbook_sheets(archive))[sheet])

    def _sheet_names(self):
        with zipfile.ZipFile(WORKBOOK) as archive:
            return list(dict(self.tpl.workbook_sheets(archive)))

    def _edits(self, sheet, changes):
        """``{주소: 새 조각}`` → (원본 조각, 새 조각) 목록."""
        raw = self._raw(sheet)
        out = []
        for ref, new in changes.items():
            old = _fragment(raw, ref)
            self.assertNotEqual(old, new, "%s!%s 조각이 그대로다" % (sheet, ref))
            out.append((old, new))
        return out

    def _value_edits(self, sheet, values):
        """``{주소: 새 값}`` → 값만 바꾸는 (원본, 새) 목록. 서식은 그대로."""
        raw = self._raw(sheet)
        return [(_fragment(raw, ref), _with_value(_fragment(raw, ref), value))
                for ref, value in values.items()]

    def _patch(self, sheet, changes, name="student.xlsx"):
        """``{주소: 새 조각}`` — 원본 조각을 읽어 바꾼 사본."""
        return self._mutated(sheet, self._edits(sheet, changes), name)

    def _values(self, sheet, values, name="student.xlsx"):
        """``{주소: 새 값}`` — 값만 바꾼 사본. 서식·수식은 손대지 않는다."""
        return self._mutated(sheet, self._value_edits(sheet, values), name)

    def _value_patches(self, by_sheet, name="student.xlsx"):
        """``{시트: {주소: 새 값}}`` — 여러 시트를 한 사본에서."""
        return self._patch_many(
            {sheet: self._value_edits(sheet, values)
             for sheet, values in by_sheet.items()}, name)


class SnapshotTests(ReadbackCase):
    """⓪ 원본 사본 대조 — 251 밖 구조·수식 글자·서식."""

    def test_deleted_merge_holds_the_whole_table(self):
        """1. 병합 하나 지운 사본 → held·template_merges_changed·코드 2."""
        student = self._mutated(SHEET1, [('<mergeCell ref="B1:H1"/>', "")])
        value = self._held(student)
        self.assertEqual(value["reason"], "template_merges_changed", value)
        self.assertEqual([item["reason"] for item in value["rejections"]],
                         ["template_merges_changed"])
        self.assertEqual(value["rejections"][0]["sheet"], SHEET1)

    def test_input_merge_removal_reads_xml_not_spelling(self):
        """1-1. 같은 병합을 4가지 표기로 적어도 결과가 같다 — XML 의미로 지운다."""
        raw = self._sheet_bytes(SHEET1)
        original = b'<mergeCell ref="H36:I36"/>'
        self.assertEqual(raw.count(original), 1)
        namespace = (b'xmlns:m="http://schemas.openxmlformats.org/'
                     b'spreadsheetml/2006/main"')
        spellings = {
            "자체닫힘+공백": b'<mergeCell ref="H36:I36" />',
            "작은따옴표": b"<mergeCell ref='H36:I36'/>",
            "접두사": b'<m:mergeCell ' + namespace + b' ref="H36:I36"/>',
            "닫는태그": b'<mergeCell ref="H36:I36"></mergeCell>',
        }
        self.assertEqual(
            len(self.rb._merge_spans(raw, self.rb.INPUT_BY_SHEET[SHEET1])), 6)
        expected = self.rb._drop_input_merges(raw, self.rb.INPUT_BY_SHEET[SHEET1])
        for name, spelling in spellings.items():
            with self.subTest(name=name):
                variant = raw.replace(original, spelling)
                self.assertEqual(
                    self.rb._drop_input_merges(variant,
                                               self.rb.INPUT_BY_SHEET[SHEET1]),
                    expected, name)
                student = self._mutated(SHEET1, [(original.decode("utf-8"),
                                                  spelling.decode("utf-8"))],
                                        "%s.xlsx" % name)
                value = self._read(student, out="build/merge-%s.json" % name)
                self.assertEqual(value["issues"], [], name)
                self.assertEqual(value["counts"]["by_state"], BASELINE, name)

    def test_merges_outside_the_input_cells_survive_byte_for_byte(self):
        """1-2. 원본 병합 432개 중 입력 20개만 지운다 — 나머지 412개는 그대로."""
        dropped = kept = 0
        for sheet in self._sheet_names():
            raw = self._sheet_bytes(sheet)
            refs = self.rb.INPUT_BY_SHEET.get(sheet)
            after = raw if refs is None else self.rb._drop_input_merges(raw, refs)
            before_refs = re.findall(rb'<mergeCell ref="([^"]+)"', raw)
            after_refs = re.findall(rb'<mergeCell ref="([^"]+)"', after)
            self.assertTrue(set(after_refs) <= set(before_refs), sheet)
            for ref in after_refs:
                self.assertIn(b'<mergeCell ref="%s"' % ref, after, (sheet, ref))
            dropped += len(before_refs) - len(after_refs)
            kept += len(after_refs)
        self.assertEqual((dropped, kept), (20, 412))

    def test_temporary_workbooks_leave_no_residue(self):
        """1-3. 임시 폴더는 성공·거절·예외 세 길 모두에서 남지 않는다."""
        with tempfile.TemporaryDirectory(prefix="hort-residue-") as scratch:
            root = Path(scratch)
            seen = []
            real = self.rb.gg_office.inspect_preserved_template

            def watch(before, after):
                seen.append((Path(before), Path(after)))
                return real(before, after)

            with mock.patch.object(tempfile, "tempdir", str(root)), \
                    mock.patch.object(self.rb.gg_office,
                                      "inspect_preserved_template", watch):
                self._read(self._copy("clean.xlsx"), out="build/clean.json")
                self.assertEqual(list(root.iterdir()), [], "성공 경로 잔여물")
                self._held(self._mutated(
                    SHEET1, [('<mergeCell ref="B1:H1"/>', "")], "held.xlsx"),
                    out="build/held.json")
                self.assertEqual(list(root.iterdir()), [], "거절 경로 잔여물")
                with mock.patch.object(self.rb.gg_office,
                                       "inspect_preserved_template",
                                       side_effect=OSError("boom")):
                    with self.assertRaises(OSError):
                        self._run(self._copy("boom.xlsx"), out="build/boom.json")
                self.assertEqual(list(root.iterdir()), [], "예외 경로 잔여물")
            self.assertEqual(len(seen), 2, seen)
            for before, after in seen:
                self.assertEqual(before.parent, after.parent)
                self.assertEqual(before.parent.parent, root)
                self.assertTrue(before.parent.name.startswith("hort-readback-"))
                self.assertEqual((before.name, after.name),
                                 ("before.xlsx", "after.xlsx"))
                self.assertFalse(before.parent.exists())

    def test_broken_workbook_is_an_input_error_not_a_traceback(self):
        """1-4. 깨진 ZIP·깨진 시트 XML → held·코드 3·출력 파일 없음."""
        broken_zip = self.tmp / "broken-zip.xlsx"
        broken_zip.write_bytes(WORKBOOK.read_bytes()[:4096])
        value = self._input_error(broken_zip, out="build/broken-zip.json")
        self.assertIn("zip", value["detail"].lower(), value)

        student = self._mutated(
            SHEET1, [("</sheetData>", "</sheetData")], "broken-xml.xlsx")
        self._input_error(student, out="build/broken-xml.json")

    def test_snapshot_boundary_turns_a_corrupt_copy_into_held(self):
        """1-5. ⓪ 자체 경계 — 병합 제거가 못 읽는 사본도 Held(코드 3)로."""
        broken = self.tmp / "corrupt.xlsx"
        broken.write_bytes(b"not a zip")
        with self.assertRaises(self.rb.hwb.Held) as caught:
            self.rb._snapshot(broken, self.canonical, {}, {})
        self.assertEqual(caught.exception.code, self.rb.REASON_SNAPSHOT)

    def test_label_formula_edit_outside_the_251_holds(self):
        """2. 251 밖 라벨 수식 글자 변경 → held·template_cell_changed."""
        student = self._mutated(
            SHEET1, [('<c r="D10" s="356"><f>D8+D9</f>',
                      '<c r="D10" s="356"><f>D8+D9+1</f>')])
        value = self._held(student)
        self.assertEqual(value["reason"], "template_cell_changed", value)
        self.assertEqual(value["rejections"][0]["cell"], "D10")

    def test_recalculated_cache_outside_the_251_is_not_a_rejection(self):
        """3. 251 밖 수식의 캐시만 바뀐 사본(재계산 정상 파일) → read, 거절 0."""
        student = self._mutated(
            SHEET1, [('<c r="D10" s="356"><f>D8+D9</f><v>9256</v></c>',
                      '<c r="D10" s="356"><f>D8+D9</f><v>9257</v></c>')])
        value = self._read(student)
        self.assertEqual(value["issues"], [])
        self.assertEqual(value["counts"]["by_state"], BASELINE)

    def test_number_format_change_is_reported_not_held(self):
        """4. 서식만 변경 → read + issues needs_visual_review 1건."""
        student = self._mutated(
            SHEET1, [('<c r="B1" s="516" t="s"><v>505</v></c>',
                      '<c r="B1" s="39" t="s"><v>505</v></c>')])
        value = self._read(student)
        self.assertEqual(len(value["issues"]), 1, value["issues"])
        issue = value["issues"][0]
        self.assertEqual(issue["status"], "needs_visual_review", issue)
        self.assertEqual(issue["reason"], "native number format changed", issue)
        self.assertEqual((issue["sheet"], issue["cell"]), (SHEET1, "B1"))
        self.assertEqual(value["counts"]["by_state"], BASELINE)


def _fragment(raw, ref):
    """원본 시트 XML에서 ``ref`` 칸의 바이트 그대로 조각 하나."""
    match = re.search(r'<c r="%s"(?=[ />])[^>]*?(?:/>|>.*?</c>)' % re.escape(ref), raw)
    if match is None:
        raise AssertionError("원본에 칸이 없다: %s" % ref)
    return match.group(0)


def _head(fragment):
    """``<c r="A1" s="1">`` — 여는 태그까지(자체 닫힘 태그는 ``/>``를 ``>``로)."""
    head = fragment[:fragment.index(">") + 1]
    return head[:-2] + ">" if head.endswith("/>") else head


def _with_value(fragment, value):
    """같은 칸 조각에서 값만 바꾼 조각(수식은 그대로 둔다)."""
    text = repr(value) if isinstance(value, float) else str(value)
    if "<v>" in fragment:
        return re.sub(r"<v>[^<]*</v>", "<v>%s</v>" % text, fragment, count=1)
    return _head(fragment) + "<v>%s</v></c>" % text


def _as_formula(fragment, expression, cache):
    """상수·빈칸 칸을 수식 칸으로 — 캐시 숫자를 일부러 넣어 둔다."""
    return _head(fragment) + "<f>%s</f><v>%s</v></c>" % (expression, cache)


ZERO_CELLS = {
    SHEET15: ("C33", "D33", "E33", "F33", "G33"),
    SHEET4: ("G21", "G22", "F23", "G23", "F25", "G25", "H25", "F26",
             "G26", "H26", "F27", "G27", "H27", "F28", "G28", "H28"),
}
BLANK_CELLS = {SHEET4: ("G19", "F20", "G20", "F21", "H21", "F22", "H22", "H23")}
DISPLAY_ONLY = {SHEET1: {"D8": 9300, "D36": 2, "D37": 2, "F36": 11,
                         "F37": 10, "G36": 2018, "G37": 2022}}


class StateTests(ReadbackCase):
    """② 적는 자리 221칸의 상태 11가지."""

    def test_constants_overwritten_same_and_deleted(self):
        """5. 상수 3칸 — 덮어씀·같은 값·지움."""
        student = self._patch(SHEET1, {
            "E8": '<c r="E8" s="19"><v>250000</v></c>',
            "D8": '<c r="D8" s="19"/>',
        })
        value = self._read(student)
        self.assertEqual(self._states(value, [(SHEET1, "E8"), (SHEET1, "F8"),
                                             (SHEET1, "D8")]),
                         {(SHEET1, "E8"): "constant_overwritten",
                          (SHEET1, "F8"): "same_value",
                          (SHEET1, "D8"): "constant_deleted"})
        self.assertEqual(value["issues"], [])
        counts = value["counts"]["by_state"]
        self.assertEqual(counts["constant_overwritten"], 1)
        self.assertEqual(counts["constant_deleted"], 1)
        self.assertEqual(counts["same_value"], BASELINE["same_value"] - 2)
        overwritten = self._cell(value, SHEET1, "E8")
        self.assertEqual(overwritten["student_value"], 250000)
        self.assertTrue(overwritten["differs"])
        self.assertIn("250000", overwritten["message"])
        deleted = self._cell(value, SHEET1, "D8")
        self.assertEqual(deleted["student_kind"], "blank")
        self.assertIsNone(deleted["student_value"])
        self.assertTrue(deleted["differs"])

    def test_blank_filled_and_blank_kept(self):
        """6. 빈칸 2칸 — 채움·그대로."""
        student = self._values(SHEET4, {"G19": 1000})
        value = self._read(student)
        self.assertEqual(self._states(value, [(SHEET4, "G19"), (SHEET4, "F20")]),
                         {(SHEET4, "G19"): "blank_filled",
                          (SHEET4, "F20"): "blank_kept"})
        filled = self._cell(value, SHEET4, "G19")
        self.assertIsNone(filled["source_value"])
        self.assertEqual(filled["student_value"], 1000)
        self.assertTrue(filled["differs"])
        kept = self._cell(value, SHEET4, "F20")
        self.assertIsNone(kept["student_value"])
        self.assertFalse(kept["differs"])
        counts = value["counts"]["by_state"]
        self.assertEqual(counts["blank_filled"], 1)
        self.assertEqual(counts["blank_kept"], BASELINE["blank_kept"] - 1)

    def test_formula_five_states(self):
        """7. 수식 5칸 — 그대로·캐시만·다른 수식·상수로 덮음·지움."""
        raw = self._raw(SHEET5)
        original = _fragment(raw, "D39")
        self.assertEqual(original,
                         '<c r="D39" s="86"><f>\'4.투자계획\'!F8</f><v>500000</v></c>')
        tables = {
            "kept": self._copy("kept.xlsx"),
            "cache_only": self._mutated(SHEET5, [
                (original, '<c r="D39" s="86"><f>\'4.투자계획\'!F8</f>'
                           '<v>510000</v></c>')], "cache-only.xlsx"),
            "replaced": self._mutated(SHEET5, [
                ('<f>\'4.투자계획\'!F8</f>', '<f>\'4.투자계획\'!F8+1</f>')],
                "replaced.xlsx"),
            "to_constant": self._mutated(SHEET5, [
                (original, '<c r="D39" s="86"><v>500000</v></c>')],
                "constant.xlsx"),
            "deleted": self._mutated(SHEET5, [
                (original, '<c r="D39" s="86"/>')], "deleted.xlsx"),
        }
        expected = {"kept": "formula_kept", "cache_only": "formula_cache_only",
                    "replaced": "formula_replaced",
                    "to_constant": "formula_to_constant",
                    "deleted": "formula_deleted"}
        for name, path in tables.items():
            with self.subTest(name=name):
                value = self._read(path, out="build/%s.json" % name)
                row = self._cell(value, SHEET5, "D39")
                self.assertEqual(row["state"], expected[name], row)
                self.assertIsNone(row["differs"], row)
                self.assertEqual(row["source_value"], "='4.투자계획'!F8")
                if name in ("kept", "cache_only", "replaced"):
                    self.assertEqual(row["student_kind"], "formula")
                    self.assertNotEqual(row["student_value"], 510000)
                if name == "replaced":
                    self.assertEqual(row["student_value"], "='4.투자계획'!F8+1")
                if name == "to_constant":
                    self.assertEqual(row["student_value"], 500000)
                if name == "deleted":
                    self.assertIsNone(row["student_value"])
                counts = value["counts"]["by_state"]
                self.assertEqual(counts[expected[name]], 1, counts)
                self.assertEqual(counts["formula_kept"],
                                 1 if name == "kept" else 0, counts)

    def test_formula_without_a_cached_value_is_flagged(self):
        """7-1. 수식 그대로·캐시 없음 → formula_kept + cache_not_compared 1줄."""
        raw = self._raw(SHEET5)
        original = _fragment(raw, "D39")
        student = self._mutated(SHEET5, [
            (original, '<c r="D39" s="86"><f>\'4.투자계획\'!F8</f></c>')],
            "no-cache.xlsx")
        value = self._read(student)
        row = self._cell(value, SHEET5, "D39")
        self.assertEqual(row["state"], "formula_kept", row)
        self.assertEqual([item["reason"] for item in value["issues"]],
                         ["cache_not_compared"], value["issues"])
        self.assertEqual(value["issues"][0]["cell"], "D39")

    def test_formula_added_never_carries_a_cache(self):
        """8. 상수·빈칸에 수식 → formula_added, 캐시 숫자 0건."""
        student = self._patch_many({
            SHEET1: self._edits(SHEET1, {
                "E8": _as_formula(_fragment(self._raw(SHEET1), "E8"),
                                  "SUM(1,2)", "3")}),
            SHEET4: self._edits(SHEET4, {
                "G19": _as_formula(_fragment(self._raw(SHEET4), "G19"),
                                   "SUM(1,2)", "3")}),
        }, "added.xlsx")
        value = self._read(student)
        self.assertEqual(self._states(value, [(SHEET1, "E8"), (SHEET4, "G19")]),
                         {(SHEET1, "E8"): "formula_added",
                          (SHEET4, "G19"): "formula_added"})
        message = self.rb.MESSAGES["formula_added"]
        for sheet, ref in ((SHEET1, "E8"), (SHEET4, "G19")):
            row = self._cell(value, sheet, ref)
            self.assertEqual(row["student_value"], "=SUM(1,2)", row)
            self.assertEqual(row["message"], message)
            self.assertIsNone(row["differs"], row)
        self.assertEqual(value["counts"]["by_state"]["formula_added"], 2)
        added = [row for row in value["cells"] if row["state"] == "formula_added"]
        self.assertEqual(len(added), 2, added)
        for row in added:
            self.assertIsInstance(row["student_value"], str, row)
            self.assertTrue(row["student_value"].startswith("="), row)

    def test_not_read_cells_change_without_entering_the_answer(self):
        """9. 아닌 자리·못 가림 3칸 변경 → not_read_* 3건, 합 251."""
        student = self._value_patches({
            "3.모델농장분석": {"C3": 460},
            SHEET15: {"B47": 388},
            SHEET1: {"E36": 9000},
        }, "not-read.xlsx")
        value = self._read(student)
        self.assertEqual(
            self._states(value, [("3.모델농장분석", "C3"), (SHEET15, "B47"),
                                 (SHEET1, "E36")]),
            {("3.모델농장분석", "C3"): "not_read_textbook",
             (SHEET15, "B47"): "not_read_footnote",
             (SHEET1, "E36"): "not_read_unclear"})
        self.assertEqual(value["counts"]["by_state"], BASELINE)
        self.assertEqual(value["counts"]["recorded"], 251)

    def test_zero_convention_36_cells(self):
        """10. `0` 규약 36칸 — 빈칸 8 + 값 `0` 21 + 표시용 7."""
        values = {SHEET4: dict({ref: 5 for ref in BLANK_CELLS[SHEET4]},
                               **{ref: 11 for ref in ZERO_CELLS[SHEET4]}),
                  SHEET15: {ref: 11 for ref in ZERO_CELLS[SHEET15]},
                  SHEET1: dict(DISPLAY_ONLY[SHEET1])}
        student = self._value_patches(values, "zeros.xlsx")
        value = self._read(student)
        counts = value["counts"]["by_state"]
        self.assertEqual(counts["blank_filled"], 8, counts)
        self.assertEqual(counts["constant_overwritten"], 28, counts)
        self.assertEqual(counts["blank_kept"], BASELINE["blank_kept"] - 8, counts)
        self.assertEqual(counts["same_value"], BASELINE["same_value"] - 28, counts)
        self.assertEqual(value["counts"]["recorded"], 251, counts)
        self.assertEqual(value["issues"], [])
        raw = {SHEET1: {"D8": 9256, "D36": 1, "D37": 1, "F36": 10,
                        "F37": 9, "G36": 2017, "G37": 2021}}
        for sheet, pairs in raw.items():
            for ref, expected in pairs.items():
                row = self._cell(value, sheet, ref)
                self.assertEqual(row["source_kind"], "constant", row)
                self.assertEqual(row["source_value"], expected, row)
                self.assertEqual(row["state"], "constant_overwritten", row)
                self.assertTrue(row["differs"], row)

    def test_ratio_cells_keep_the_raw_decimal(self):
        """11. 비율 — 100배 변환 없음, 범위 밖·단위 미확인은 issue로."""
        raw = self._raw(SHEET5)
        original = _fragment(raw, "F39")
        self.assertEqual(original, '<c r="F39" s="407"><v>1.4999999999999999E-2</v></c>')
        cases = {
            "kept": (self._copy("ratio-kept.xlsx"), None, []),
            "half": (self._mutated(SHEET5, [
                (original, '<c r="F39" s="407"><v>0.5</v></c>')], "ratio-half.xlsx"),
                "constant_overwritten", []),
            "out": (self._mutated(SHEET5, [
                (original, '<c r="F39" s="407"><v>15</v></c>')], "ratio-out.xlsx"),
                "constant_overwritten", ["ratio_out_of_range"]),
            "format": (self._mutated(SHEET5, [
                (original, '<c r="F39" s="87"><v>0.02</v></c>')], "ratio-format.xlsx"),
                "constant_overwritten", ["ratio_unit_unconfirmed"]),
        }
        for name, (path, state, reasons) in cases.items():
            with self.subTest(name=name):
                value = self._read(path, out="build/ratio-%s.json" % name)
                row = self._cell(value, SHEET5, "F39")
                if state is None:
                    self.assertEqual(row["state"], "same_value", row)
                else:
                    self.assertEqual(row["state"], state, row)
                self.assertEqual([item["reason"] for item in value["issues"]], reasons,
                                 value["issues"])
                self.assertNotEqual(row["student_value"], 50)
                if name == "half":
                    self.assertEqual(row["student_value"], 0.5)
                    self.assertEqual(row["message"], "엑셀에 적으신 값 0.5예요.")
                if name == "out":
                    self.assertEqual(row["student_value"], 15)
                    self.assertEqual(row["message"], "엑셀에 적으신 값 15예요.")
                    self.assertEqual(
                        value["issues"][0]["message"],
                        "비율 칸에는 0과 1 사이 소수로 적어 주세요. "
                        "적으신 값 15는 1500%로 읽혀요.")


class BoundaryTests(ReadbackCase):
    """경계 — 251 밖 확장 열, 빈 구역 행 삽입."""

    def test_non_numeric_ratio_text_holds_as_an_input_error(self):
        """11-2. 비율 칸 글자 'abc' → held·코드 3·임시 폴더 잔류 0."""
        raw = self._raw(SHEET5)
        original = _fragment(raw, "F39")
        student = self._mutated(SHEET5, [
            (original, original.replace("<v>1.4999999999999999E-2</v>",
                                        "<v>abc</v>"))], "ratio-abc.xlsx")
        with tempfile.TemporaryDirectory(prefix="hort-nonnumber-") as scratch:
            root = Path(scratch)
            with mock.patch.object(tempfile, "tempdir", str(root)):
                value = self._input_error(student, out="build/ratio-abc.json")
            self.assertEqual(value["reason"], self.rb.REASON_SNAPSHOT, value)
            self.assertEqual(list(root.iterdir()), [], "임시 폴더 잔여물")
        self.assertIn("not a number", value["detail"], value)

    def test_non_finite_ratio_value_is_reported_not_crashed(self):
        """11-1. NaN·Infinity — 범위 비교 예외 없이 issue 1줄, 적은 값은 그대로."""
        raw = self._raw(SHEET5)
        original = _fragment(raw, "F39")
        for name, value in (("nan", "NaN"), ("inf", "Infinity"), ("neginf", "-Infinity")):
            with self.subTest(name=name):
                student = self._mutated(SHEET5, [
                    (original, original.replace("<v>1.4999999999999999E-2</v>",
                                                "<v>%s</v>" % value))],
                    "ratio-%s.xlsx" % name)
                result = self._read(student, out="build/ratio-%s.json" % name)
                row = self._cell(result, SHEET5, "F39")
                self.assertEqual(row["state"], "constant_overwritten", row)
                self.assertEqual(row["student_value"], value, row)
                self.assertEqual([item["reason"] for item in result["issues"]],
                                 ["ratio_value_not_finite"], result["issues"])

    def test_value_in_an_extra_column_is_reported_not_classified(self):
        """12. 원본에 없는 확장 열 값 → cells 제외, unmapped_cell 1줄."""
        student = self._mutated(SHEET11, [
            ('<c r="U4" s="33"/>',
             '<c r="U4" s="33"/><c r="AJ4" s="33"><v>7</v></c>')], "extra.xlsx")
        value = self._read(student)
        self.assertEqual(len(value["cells"]), 251)
        self.assertEqual(value["counts"]["by_state"], BASELINE)
        self.assertNotIn("AJ4", [row["cell"] for row in value["cells"]])
        self.assertEqual(value["issues"], [{
            "status": "needs_visual_review", "reason": "unmapped_cell",
            "sheet": SHEET11, "cell": "AJ4"}])

    def test_blank_region_row_insert_is_a_documented_non_reaction(self):
        """13. 빈 구역 행 삽입 → read, 거절 0·경고 0, 251 불변."""
        student = self._mutated(SHEET11, [
            ("</sheetData>", '<row r="900"/></sheetData>')], "row.xlsx")
        value = self._read(student)
        self.assertEqual(value["issues"], [])
        self.assertEqual(value["counts"]["by_state"], BASELINE)
        self.assertEqual(value["counts"]["recorded"], 251)


class OriginalTests(ReadbackCase):
    """실원본 — 무변경과 3칸 수정."""

    def test_untouched_original_reads_as_a_pure_mirror(self):
        """14. H-X1 무변경 → 251행, 덮어씀 0, 캐시만 0, 경고 0."""
        value = self._read(self._copy("untouched.xlsx"))
        self.assertEqual(value["counts"]["by_state"], BASELINE)
        self.assertEqual(value["counts"]["recorded"], 251)
        self.assertEqual(value["counts"]["read"], 221)
        self.assertEqual(value["counts"]["not_read"], 30)
        self.assertEqual(value["issues"], [])
        self.assertEqual(value["source_sha256"], SHA)
        self.assertEqual(value["student_sha256"], SHA)
        self.assertEqual(value["cells"], self._read(
            self._copy("untouched2.xlsx"), out="build/again.json")["cells"])

    def test_three_edits_leave_the_canonical_and_the_extra_files_alone(self):
        """15. 상수·빈칸·수식 3칸 수정 → 상태 3개, 정본 불변, 새 파일은 --out뿐."""
        canonical_sha = hashlib.sha256(self.canonical.read_bytes()).hexdigest()
        before = self._tree_bytes(self.root)
        student = self._patch_many({
            SHEET1: self._edits(SHEET1, {
                "E8": '<c r="E8" s="19"><v>250000</v></c>'}),
            SHEET4: self._value_edits(SHEET4, {"G19": 1000}),
            SHEET5: self._edits(SHEET5, {
                "D39": '<c r="D39" s="86"><f>\'4.투자계획\'!F8+1</f>'
                       '<v>500000</v></c>'}),
        }, "three.xlsx")
        value = self._read(student)
        self.assertEqual(self._states(value, [(SHEET1, "E8"), (SHEET4, "G19"),
                                              (SHEET5, "D39")]),
                         {(SHEET1, "E8"): "constant_overwritten",
                          (SHEET4, "G19"): "blank_filled",
                          (SHEET5, "D39"): "formula_replaced"})
        self.assertEqual(value["counts"]["by_state"]["constant_overwritten"], 1)
        self.assertEqual(value["counts"]["by_state"]["blank_filled"], 1)
        self.assertEqual(value["counts"]["by_state"]["formula_replaced"], 1)
        self.assertEqual(value["counts"]["recorded"], 251)
        self.assertEqual(hashlib.sha256(self.canonical.read_bytes()).hexdigest(),
                         canonical_sha)
        after = self._tree_bytes(self.root)
        self.assertEqual(sorted(after), sorted(set(before) | {"build/out.json"}))
        for rel in before:
            self.assertEqual(after[rel], before[rel], rel)
        self.assertEqual(json.loads((self.root / "build/out.json")
                                    .read_text(encoding="utf-8")), value)


class PartitionTests(ReadbackCase):
    """15상태가 겹치지 않고 251칸을 빠짐없이 덮는지."""

    def test_states_partition_all_251_cells(self):
        value = self._read(self._copy("partition.xlsx"))
        counts = value["counts"]
        by_state = counts["by_state"]
        self.assertEqual(set(by_state), set(self.rb.STATE_ORDER))
        self.assertEqual(sum(by_state.values()), 251)
        self.assertEqual(counts["recorded"], 251)
        self.assertEqual(counts["read"] + counts["not_read"], 251)
        self.assertEqual(counts["read"], sum(by_state[key] for key in self.rb.READ_STATES))
        self.assertEqual(counts["not_read"],
                         sum(by_state[key] for key in self.rb.STATE_ORDER[:4]))
        self.assertEqual(len(value["cells"]), 251)
        addresses = [(row["sheet"], row["cell"]) for row in value["cells"]]
        self.assertEqual(len(set(addresses)), 251)
        for row in value["cells"]:
            self.assertIn(row["state"], self.rb.STATE_ORDER, row)
        seen = {row["state"] for row in value["cells"]}
        self.assertEqual(seen, {key for key in self.rb.STATE_ORDER if by_state[key]})
        for key in self.rb.STATE_ORDER:
            self.assertEqual(sum(1 for row in value["cells"] if row["state"] == key),
                             by_state[key], key)
