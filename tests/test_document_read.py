"""First-read contracts for synthetic DOCX, text PDF, and XLSX files."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from docx.oxml import OxmlElement
from docx import Document
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject


READER = Path(__file__).resolve().parents[1] / "skills" / "knuaf-doc" / "scripts" / "gg_read.py"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text_pdf(path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Readable PDF text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with Path(path).open("wb") as output:
        writer.write(output)


class DocumentReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_reader(self, source, output, *, path=None):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if path is not None:
            env["PATH"] = str(path)
        result = subprocess.run(
            [sys.executable, "-B", str(READER), str(source), "--out", str(output)],
            capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        return result, json.loads(result.stdout)

    def test_docx_preserves_paragraph_table_order_and_source_bytes(self):
        source = self.root / "원고.docx"
        document = Document()
        document.add_paragraph("앞 문단: 합성 문서")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "항목"
        table.cell(0, 1).text = "값"
        table.cell(1, 0).text = "면적"
        table.cell(1, 1).text = "12ha"
        document.add_paragraph("뒤 문단: 끝")
        document.save(source)
        before = _sha256(source)
        output = self.root / "읽은결과.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(0, process.returncode)
        self.assertEqual("read", result["status"])
        self.assertEqual(2, result["details"]["paragraphs"])
        self.assertEqual(1, result["details"]["tables"])
        self.assertEqual(4, result["details"]["nonempty_cells"])
        text = output.read_text(encoding="utf-8")
        self.assertLess(text.index("앞 문단"), text.index("표 1"))
        self.assertLess(text.index("표 1"), text.index("뒤 문단"))
        self.assertIn("면적 | 12ha", text)
        self.assertEqual(before, _sha256(source))
        self.assertEqual(before, result["input"]["sha256_after_read"])
        self.assertEqual(_sha256(output), result["output"]["sha256"])

    def test_docx_extracts_text_from_nested_table_cells(self):
        source = self.root / "중첩 표.docx"
        document = Document()
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "바깥 항목"
        parent_cell = table.cell(0, 1)
        parent_cell.text = "상위 셀"
        nested = parent_cell.add_table(rows=1, cols=2)
        nested.cell(0, 0).text = "NESTED_CELL_ALPHA_94827"
        nested.cell(0, 1).text = "NESTED_CELL_BETA_61503"
        document.save(source)
        output = self.root / "중첩 표 읽기.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(0, process.returncode)
        self.assertEqual("read", result["status"])
        self.assertEqual(2, result["details"]["tables"])
        self.assertEqual(4, result["details"]["nonempty_cells"])
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("NESTED_CELL_ALPHA_94827", rendered)
        self.assertIn("NESTED_CELL_BETA_61503", rendered)

    def test_docx_extracts_body_content_control_paragraph_in_order(self):
        source = self.root / "내용 컨트롤.docx"
        document = Document()
        document.add_paragraph("앞 문단")
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "중간 표"
        document.add_paragraph("뒤 문단")
        sdt = OxmlElement("w:sdt")
        sdt.append(OxmlElement("w:sdtPr"))
        content = OxmlElement("w:sdtContent")
        paragraph = OxmlElement("w:p")
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "CONTENT_CONTROL_47381"
        run.append(text)
        paragraph.append(run)
        content.append(paragraph)
        sdt.append(content)
        document.element.body.insert(1, sdt)
        document.save(source)
        output = self.root / "내용 컨트롤.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(0, process.returncode)
        self.assertEqual("read", result["status"])
        self.assertEqual(3, result["details"]["paragraphs"])
        rendered = output.read_text(encoding="utf-8")
        self.assertIn("CONTENT_CONTROL_47381", rendered)
        self.assertLess(rendered.index("앞 문단"), rendered.index("CONTENT_CONTROL_47381"))
        self.assertLess(rendered.index("CONTENT_CONTROL_47381"), rendered.index("표 1"))
        self.assertLess(rendered.index("표 1"), rendered.index("뒤 문단"))

    def test_text_pdf_reads_text_and_records_page_numbers(self):
        source = self.root / "텍스트.pdf"
        _text_pdf(source)
        before = _sha256(source)
        output = self.root / "읽은.pdf.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(0, process.returncode)
        self.assertEqual("read", result["status"])
        self.assertEqual(1, result["details"]["pages"])
        self.assertEqual([], result["details"]["pages_without_extractable_text"])
        self.assertEqual(before, result["input"]["sha256"])
        self.assertEqual(str(output.resolve()), result["output"]["path"])
        self.assertEqual(_sha256(output), result["output"]["sha256"])
        self.assertTrue(result["output"]["written"])
        self.assertIn("Readable PDF text", output.read_text(encoding="utf-8"))
        self.assertEqual(before, _sha256(source))


    def test_damaged_xref_pdf_is_blocked_instead_of_repaired_as_success(self):
        source = self.root / "xref 손상.pdf"
        _text_pdf(source)
        original = source.read_bytes()
        matches = list(re.finditer(rb"(startxref\s*)\d+", original))
        self.assertTrue(matches)
        match = matches[-1]
        source.write_bytes(original[:match.start()] + match.group(1) + b"1" + original[match.end():])
        before = _sha256(source)
        output = self.root / "손상 읽기.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("invalid_document", result["reason"])
        self.assertEqual(before, result["input"]["sha256"])
        self.assertFalse(result["output"]["written"])
        self.assertFalse(output.exists())

    def test_image_only_pdf_is_incomplete_not_success(self):
        source = self.root / "스캔.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        image = DecodedStreamObject()
        image.set_data(b"\x00\x00\x00")
        image.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        image_ref = writer._add_object(image)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref})
        })
        content = DecodedStreamObject()
        content.set_data(b"q 300 0 0 300 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        with source.open("wb") as output_file:
            writer.write(output_file)
        with source.open("rb") as source_file:
            source_page = PdfReader(source_file).pages[0]
            self.assertIn("/Im0", source_page["/Resources"]["/XObject"])
        before = _sha256(source)
        output = self.root / "스캔.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("incomplete", result["status"])
        self.assertEqual("pages_without_extractable_text", result["reason"])
        self.assertEqual([1], result["details"]["pages_without_extractable_text"])
        self.assertEqual(before, _sha256(source))
        self.assertIn("읽을 수 있는 글자를 찾지 못했어요", output.read_text(encoding="utf-8"))


    def test_xlsx_reads_values_by_cell_and_leaves_formulas_unrecalculated(self):
        source = self.root / "자료.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "생산 계획"
        sheet["A1"] = "품목"
        sheet["C1"] = "수량"
        sheet["A2"] = "사과"
        sheet["C2"] = 12
        sheet["D2"] = "=C2*2"
        workbook.save(source)
        workbook.close()
        before = _sha256(source)
        output = self.root / "자료.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(0, process.returncode)
        self.assertEqual("read", result["status"])
        self.assertEqual(1, result["details"]["worksheets"])
        self.assertEqual(5, result["details"]["nonempty_cells"])
        self.assertEqual(1, result["details"]["formula_cells"])
        text = output.read_text(encoding="utf-8")
        self.assertIn("A2: 사과", text)
        self.assertIn("C2: 12", text)
        self.assertIn("D2: =C2*2", text)
        self.assertIn("수식 그대로", result["limitations"][-1])
        self.assertEqual(before, result["input"]["sha256_after_read"])
        self.assertEqual(_sha256(output), result["output"]["sha256"])
        self.assertEqual(before, _sha256(source))

    def test_corrupt_pdf_is_blocked_with_input_hash_and_no_output(self):
        source = self.root / "깨진.pdf"
        source.write_bytes(b"not a PDF document")
        output = self.root / "실패.pdf.md"
        before = _sha256(source)

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(before, result["input"]["sha256"])
        self.assertEqual(str(output.resolve()), result["output"]["path"])
        self.assertFalse(result["output"]["written"])
        self.assertEqual("invalid_document", result["reason"])
        self.assertFalse(output.exists())

    def test_corrupt_xlsx_is_blocked_with_input_hash_and_no_output(self):
        source = self.root / "깨진.xlsx"
        source.write_bytes(b"not an XLSX package")
        output = self.root / "실패.xlsx.md"
        before = _sha256(source)

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(before, result["input"]["sha256"])
        self.assertEqual(str(output.resolve()), result["output"]["path"])
        self.assertFalse(result["output"]["written"])
        self.assertEqual("invalid_document", result["reason"])
        self.assertFalse(output.exists())

    def test_empty_pdf_is_incomplete_not_success(self):
        source = self.root / "빈 PDF.pdf"
        with source.open("wb") as output_file:
            PdfWriter().write(output_file)
        output = self.root / "빈 PDF.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("incomplete", result["status"])
        self.assertEqual("no_extractable_content", result["reason"])
        self.assertEqual(0, result["details"]["pages"])
        self.assertTrue(output.exists())

    def test_empty_docx_is_incomplete_not_success(self):
        source = self.root / "비어있는.docx"
        Document().save(source)
        output = self.root / "빈결과.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("incomplete", result["status"])
        self.assertEqual("no_extractable_content", result["reason"])
        self.assertTrue(output.exists())
        self.assertIn("읽을 수 있는 글·표·값을 찾지 못했어요", output.read_text(encoding="utf-8"))

    def test_empty_xlsx_is_incomplete_not_success(self):
        source = self.root / "빈칸.xlsx"
        workbook = Workbook()
        workbook.save(source)
        workbook.close()
        output = self.root / "빈칸.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("incomplete", result["status"])
        self.assertEqual("no_extractable_content", result["reason"])
        self.assertEqual(0, result["details"]["nonempty_cells"])

    def test_cleanup_failure_reports_saved_output_and_real_hash(self):
        source = self.root / "unlink-fail.docx"
        document = Document()
        document.add_paragraph("정상 문서 내용")
        document.save(source)
        output = self.root / "unlink-fail.md"
        before = _sha256(source)
        spec = importlib.util.spec_from_file_location("gg_read_cleanup_test", READER)
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        real_unlink = Path.unlink

        def fail_temp_unlink(path, *args, **kwargs):
            if path.name.startswith(f".{output.name}.") and path.suffix == ".tmp":
                raise PermissionError(13, "injected cleanup failure", str(path))
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(reader.Path, "unlink", fail_temp_unlink):
            receipt = reader.read_document(source, output)

        self.assertEqual("incomplete", receipt["status"])
        self.assertEqual("output_saved_cleanup_failed", receipt["reason"])
        self.assertEqual({"path": str(output.resolve()), "sha256": _sha256(output), "written": True}, receipt["output"])
        self.assertIn("정상 문서 내용", output.read_text(encoding="utf-8"))
        self.assertEqual(before, _sha256(source))
        self.assertEqual("failed", receipt["cleanup"]["status"])
        self.assertTrue(Path(receipt["cleanup"]["path"]).exists())

    def test_hwp_is_out_of_scope_and_source_is_unchanged(self):
        source = self.root / "원문.hwp"
        source.write_bytes(b"synthetic HWP")
        before = _sha256(source)
        output = self.root / "원문.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("unsupported_format", result["reason"])
        self.assertFalse(output.exists())
        self.assertEqual(before, _sha256(source))

    def test_output_aliasing_input_is_rejected(self):
        source = self.root / "same.docx"
        document = Document()
        document.add_paragraph("합성")
        document.save(source)
        before = _sha256(source)

        process, result = self.run_reader(source, source)

        self.assertEqual("output_is_input", result["reason"])
        self.assertEqual(before, result["input"]["sha256"])
        self.assertEqual(str(source.resolve()), result["output"]["path"])
        self.assertFalse(result["output"]["written"])
        self.assertEqual(before, _sha256(source))

    def test_corrupt_docx_is_blocked_without_output(self):
        source = self.root / "깨짐.docx"
        source.write_bytes(b"not a DOCX package")
        output = self.root / "실패.md"

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("invalid_document", result["reason"])
        self.assertEqual(_sha256(source), result["input"]["sha256"])
        self.assertEqual(str(output.resolve()), result["output"]["path"])
        self.assertFalse(result["output"]["written"])
        self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        source = self.root / "원고.docx"
        document = Document()
        document.add_paragraph("합성")
        document.save(source)
        output = self.root / "기존.md"
        output.write_text("기존 결과", encoding="utf-8")

        process, result = self.run_reader(source, output)

        self.assertEqual(2, process.returncode)
        self.assertEqual("output_exists", result["reason"])
        self.assertEqual(_sha256(source), result["input"]["sha256"])
        self.assertEqual(str(output.resolve()), result["output"]["path"])
        self.assertFalse(result["output"]["written"])
        self.assertEqual("기존 결과", output.read_text(encoding="utf-8"))

    def test_docx_read_needs_no_node_or_kordoc_on_path(self):
        source = self.root / "로컬.docx"
        document = Document()
        document.add_paragraph("로컬 Python 읽기")
        document.save(source)
        empty_path = self.root / "empty-path"
        empty_path.mkdir()
        output = self.root / "로컬.md"

        process, result = self.run_reader(source, output, path=empty_path)

        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual("read", result["status"])
        self.assertIn("로컬 Python 읽기", output.read_text(encoding="utf-8"))

    def test_missing_reader_dependency_is_blocked_without_output(self):
        source = self.root / "dependency.docx"
        document = Document()
        document.add_paragraph("합성")
        document.save(source)
        output = self.root / "unavailable.md"
        result = subprocess.run(
            [sys.executable, "-S", "-B", str(READER), str(source), "--out", str(output)],
            capture_output=True, text=True, encoding="utf-8", check=False)
        receipt = json.loads(result.stdout)

        self.assertEqual(2, result.returncode)
        self.assertEqual("blocked", receipt["status"])
        self.assertEqual("reader_unavailable", receipt["reason"])
        self.assertEqual(_sha256(source), receipt["input"]["sha256"])
        self.assertFalse(receipt["output"]["written"])
        self.assertFalse(output.exists())

    def test_same_file_alias_and_missing_input_are_blocked(self):
        source = self.root / "alias.docx"
        document = Document()
        document.add_paragraph("합성")
        document.save(source)
        before = _sha256(source)
        alias = self.root / "alias-link.docx"
        alias.symlink_to(source)

        process, alias_result = self.run_reader(source, alias)

        self.assertEqual(2, process.returncode)
        self.assertEqual("output_is_input", alias_result["reason"])
        self.assertEqual(before, _sha256(source))

        missing = self.root / "없음.docx"
        process, missing_result = self.run_reader(missing, self.root / "missing.md")

        self.assertEqual(2, process.returncode)
        self.assertEqual("input_unavailable", missing_result["reason"])
        self.assertFalse((self.root / "missing.md").exists())

    def test_pdf_and_xlsx_read_need_no_node_or_kordoc_on_path(self):
        pdf = self.root / "로컬.pdf"
        _text_pdf(pdf)
        xlsx = self.root / "로컬.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "로컬 XLSX 읽기"
        workbook.save(xlsx)
        workbook.close()
        empty_path = self.root / "empty-path"
        empty_path.mkdir()

        for source, expected in ((pdf, "Readable PDF text"),
                                 (xlsx, "로컬 XLSX 읽기")):
            with self.subTest(source=source.suffix):
                output = self.root / f"{source.stem}-{source.suffix[1:]}.md"
                process, result = self.run_reader(source, output, path=empty_path)
                self.assertEqual(0, process.returncode, process.stderr)
                self.assertEqual("read", result["status"])
                self.assertIn(expected, output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
