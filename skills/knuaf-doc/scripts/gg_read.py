#!/usr/bin/env python3
"""Read DOCX, text-bearing PDF, and XLSX files locally without changing them.

Writes an extraction to a new Markdown file and a structured receipt to
stdout. HWP/HWPX, image OCR, and document creation are out of scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile
from xml.etree.ElementTree import ParseError


SCHEMA = "gg-document-read/1"
SUPPORTED = {".docx", ".pdf", ".xlsx"}


class ReadError(Exception):
    """Expected input failure; never report it as a successful read."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _markdown_cell(value) -> str:
    text = str(value).replace("\\", "\\\\")
    return (text.replace("\r\n", "<br>").replace("\r", "<br>")
            .replace("\n", "<br>").replace("|", "\\|")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _read_docx(path: Path, emit) -> dict:
    from docx import Document
    from docx.opc.exceptions import OpcError
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from lxml.etree import XMLSyntaxError

    try:
        document = Document(str(path))
        paragraphs = tables = nonempty_cells = 0
        for block in document.element.body.iterchildren():
            if block.tag.endswith("}p"):
                paragraph = Paragraph(block, document)
                text = paragraph.text
                if not text.strip():
                    continue
                paragraphs += 1
                style = paragraph.style.name if paragraph.style is not None else ""
                heading = style.removeprefix("Heading ") if style.startswith("Heading ") else None
                if heading and heading.isdigit() and 1 <= int(heading) <= 6:
                    emit(f"{'#' * int(heading)} {_markdown_cell(text)}\n\n")
                else:
                    emit(f"{_markdown_cell(text)}\n\n")
            elif block.tag.endswith("}tbl"):
                table = Table(block, document)
                tables += 1
                emit(f"### 표 {tables}\n\n")
                for row_number, row in enumerate(table.rows, start=1):
                    cells = [cell.text for cell in row.cells]
                    nonempty_cells += sum(bool(value.strip()) for value in cells)
                    values = [_markdown_cell(value) for value in cells]
                    emit(f"행 {row_number}: " + " | ".join(values) + "\n")
                emit("\n")
        return {"paragraphs": paragraphs, "tables": tables,
                "nonempty_cells": nonempty_cells}
    except (OpcError, XMLSyntaxError, ParseError, ValueError, KeyError,
            TypeError, zipfile.BadZipFile):
        raise ReadError("invalid_document") from None


def _read_pdf(path: Path, emit) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        with path.open("rb") as source:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise ReadError("encrypted_pdf")
            unreadable_pages = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                emit(f"## {page_number}쪽\n\n")
                if text.strip():
                    emit(text.rstrip() + "\n\n")
                else:
                    unreadable_pages.append(page_number)
                    emit("이 페이지에서는 읽을 수 있는 글자를 찾지 못했어요.\n\n")
            return {"pages": len(reader.pages),
                    "pages_without_extractable_text": unreadable_pages}
    except ReadError:
        raise
    except (PyPdfError, EOFError, ValueError, KeyError, TypeError,
            IndexError, OSError, zipfile.BadZipFile):
        raise ReadError("invalid_document") from None


def _read_xlsx(path: Path, emit) -> dict:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
    except (InvalidFileException, ValueError, TypeError, IndexError,
            ParseError, zipfile.BadZipFile):
        raise ReadError("invalid_document") from None
    worksheets = len(workbook.worksheets)
    nonempty_cells = formulas = 0
    try:
        for sheet in workbook.worksheets:
            state = "숨김" if sheet.sheet_state != "visible" else "표시"
            emit(f"## 시트: {_markdown_cell(sheet.title)} ({state})\n\n")
            emit("| 행 | 셀 값 (셀 주소 포함) |\n|---:|---|\n")
            for row_number, row in enumerate(sheet.iter_rows(), start=1):
                values = []
                for column_number, cell in enumerate(row, start=1):
                    value = cell.value
                    if value is None:
                        continue
                    address = f"{get_column_letter(column_number)}{row_number}"
                    if isinstance(value, str) and value.startswith("="):
                        formulas += 1
                    values.append(f"{address}: {_markdown_cell(value)}")
                if values:
                    nonempty_cells += len(values)
                    emit(f"| {row_number} | {'; '.join(values)} |\n")
            emit("\n")
    except (InvalidFileException, ValueError, TypeError, IndexError,
            ParseError, zipfile.BadZipFile):
        raise ReadError("invalid_document") from None
    finally:
        workbook.close()
    return {"worksheets": worksheets, "nonempty_cells": nonempty_cells,
            "formula_cells": formulas}


def _result(status: str, message: str, *, reason: str | None = None) -> dict:
    result = {"schema": SCHEMA, "status": status, "message": message}
    if reason:
        result["reason"] = reason
    return result


def _limitations(extension: str) -> list[str]:
    if extension == ".docx":
        return [
            "문단·표는 문서 안의 순서대로 옮겨요.",
            "쪽 번호·각주·머리말/꼬리말·그림 안 글자·복잡한 병합 표 모양은 보존하지 않아요.",
        ]
    if extension == ".pdf":
        return [
            "쪽 번호는 원문 PDF 쪽 순서예요. 표의 열 배치·각주·글자 위치는 원본과 대조해 주세요.",
            "이미지로만 된 글자는 읽지 못해요. OCR을 하지 않아요.",
        ]
    if extension == ".xlsx":
        return [
            "셀 주소와 셀 값은 옮겼지만 병합·색·차트·인쇄 쪽 배치는 보존하지 않아요.",
            "수식은 저장된 계산값이 아니라 수식 그대로 표시해요. 다시 계산하지 않아요.",
        ]
    return ["이 파일 형식은 이 읽기로 열지 않았어요."]


def read_document(source: Path, destination: Path) -> dict:
    """Extract one explicitly chosen file; never overwrite source or result."""
    source = source.expanduser().resolve()
    destination = destination.expanduser()
    destination = destination.parent.resolve() / destination.name
    extension = source.suffix.lower()

    def failure(reason: str, message: str, input_hash=None) -> dict:
        result = _result("blocked", message, reason=reason)
        result.update({
            "format": extension[1:] if extension.startswith(".") else extension,
            "input": {"path": str(source), "sha256": input_hash},
            "output": {"path": str(destination), "sha256": None,
                       "written": False},
            "limitations": _limitations(extension),
        })
        return result

    try:
        input_hash = _sha256(source) if source.is_file() else None
    except OSError:
        input_hash = None
    if extension not in SUPPORTED:
        return failure("unsupported_format", "이 파일은 읽지 못했어요. DOCX·PDF·XLSX 파일을 선택해 주세요.", input_hash)
    if not source.is_file():
        return failure("input_unavailable", "이 파일은 읽지 못했어요. 파일 위치와 접근 권한을 확인해 주세요.")
    try:
        if destination == source:
            return failure("output_is_input", "원본 파일은 그대로 두었어요. 결과를 저장할 다른 위치를 정해 주세요.", input_hash)
        if os.path.lexists(destination):
            if os.path.samefile(source, destination):
                return failure("output_is_input", "원본 파일은 그대로 두었어요. 결과를 저장할 다른 위치를 정해 주세요.", input_hash)
            return failure("output_exists", "기존 파일은 바꾸지 않았어요. 결과를 저장할 새 위치를 정해 주세요.", input_hash)
    except OSError:
        return failure("input_unavailable", "이 파일은 읽지 못했어요. 파일 위치와 접근 권한을 확인해 주세요.", input_hash)


    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)

            def emit(text: str) -> None:
                output.write(text)

            if extension == ".docx":
                details = _read_docx(source, emit)
            elif extension == ".pdf":
                details = _read_pdf(source, emit)
            else:
                details = _read_xlsx(source, emit)
            limitations = _limitations(extension)
            no_content = (
                (extension == ".docx" and not details["paragraphs"]
                 and not details["nonempty_cells"])
                or (extension == ".xlsx" and not details["nonempty_cells"])
                or (extension == ".pdf" and details["pages"] == 0)
            )
            if no_content:
                emit("읽을 수 있는 글·표·값을 찾지 못했어요. 파일이 비어 있지 않은지 확인해 주세요.\n")
            output.flush()
            os.fsync(output.fileno())

        after_hash = _sha256(source)
        missing_pages = details.get("pages_without_extractable_text", [])
        changed = after_hash != input_hash
        incomplete = bool(missing_pages) or changed or no_content
        if changed:
            limitations.append("읽는 동안 원본 파일이 바뀌어 결과를 완전한 읽기로 표시하지 않았어요.")
        if missing_pages:
            message = "이 PDF의 일부 페이지에서는 글자를 읽지 못했어요. 해당 쪽을 확인하거나 글자가 선택되는 PDF·DOCX를 보내 주세요."
        elif changed:
            message = "읽는 동안 원본 파일이 바뀌었어요. 파일을 다시 보내 주세요."
        elif no_content:
            message = "읽을 수 있는 글·표·값을 찾지 못했어요. 파일이 비어 있지 않은지 확인해 주세요."
        else:
            message = "파일을 읽었어요. 표·각주·쪽 위치는 원본과 대조해 주세요."
        result = {
            "schema": SCHEMA,
            "status": "incomplete" if incomplete else "read",
            "message": message,
            "format": extension[1:],
            "input": {"path": str(source), "sha256": input_hash,
                      "sha256_after_read": after_hash},
            "output": {"path": str(destination), "sha256": _sha256(temporary),
                       "written": True},
            "details": details,
            "limitations": limitations,
        }
        if missing_pages:
            result["reason"] = "pages_without_extractable_text"
        elif changed:
            result["reason"] = "input_changed_during_read"
        elif no_content:
            result["reason"] = "no_extractable_content"
        os.link(temporary, destination)
        temporary.unlink()
        temporary = None
        return result
    except FileExistsError:
        return failure("output_exists", "기존 파일은 바꾸지 않았어요. 결과를 저장할 새 위치를 정해 주세요.", input_hash)
    except ReadError as error:
        return failure(error.reason, "이 파일은 읽지 못했어요. 암호가 걸렸거나 형식이 손상되지 않았는지 확인해 주세요.", input_hash)
    except (ImportError, ModuleNotFoundError):
        return failure("reader_unavailable", "이 파일은 읽지 못했어요. 담당자에게 읽기 기능을 확인해 달라고 해 주세요.", input_hash)
    except Exception:
        return failure("invalid_document", "이 파일은 읽지 못했어요. 파일이 손상되지 않았거나 압축되어 있는지 확인해 주세요.", input_hash)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="읽을 DOCX·PDF·XLSX 파일 하나")
    parser.add_argument("--out", type=Path, required=True,
                        help="새 Markdown 결과 경로 (기존 파일은 덮어쓰지 않음)")
    args = parser.parse_args(argv)
    result = read_document(args.file, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "read" else 2


if __name__ == "__main__":
    raise SystemExit(main())
