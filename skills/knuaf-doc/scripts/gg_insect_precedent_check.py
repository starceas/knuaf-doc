"""Offline identity check for the five industrial-insect example PDFs.

Usage: python gg_insect_precedent_check.py --f0 PDF --e1 PDF --e2 PDF --e3 PDF --e4 PDF
Only source IDs, SHA-256 values, physical page counts, and verification states
are emitted. Source paths and PDF contents are never printed.

The locator map is validated by gg_insect_document.load_precedents — the
single shared validator, so this checker and the document plan cannot
disagree on map structure.
"""

import argparse
import contextlib
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import BytesIO, StringIO
import json
import logging
from pathlib import Path
import warnings

import gg_insect_document


SOURCE_IDS = ("F0", "E1", "E2", "E3", "E4")
MAP_PATH = gg_insect_document.DEFAULT_PRECEDENTS
VERIFIES = ["sha256", "physical_page_count"]
INVALID_ARGUMENTS = {"status": "rejected", "reason": "invalid_arguments"}


class _InvalidArguments(Exception):
    pass


class _QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise _InvalidArguments()


@contextlib.contextmanager
def _isolated_parse():
    """Silence parser output: prints, warnings, and existing logger handlers.

    pypdf diagnostics may contain source content, so nothing the parser
    writes may reach this CLI's output channels — including records sent
    to handlers that callers attached before this process ran.  The
    previous logging disable level is always restored afterwards.
    """
    prev_disable = logging.root.manager.disable
    try:
        with warnings.catch_warnings(record=True):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                logging.disable(logging.CRITICAL)
                yield
    finally:
        logging.disable(prev_disable)


def _result(source_id, sha256=None, physical_pages=None,
            verification="check_failed"):
    return {
        "id": source_id,
        "sha256": sha256,
        "physical_pages": physical_pages,
        "verification": verification,
        "verifies": list(VERIFIES),
    }


def _all_failed(verification):
    return [_result(source_id, verification=verification)
            for source_id in SOURCE_IDS]


def verify(map_path, pdf_paths):
    """Return sanitized results; any missing, invalid, or mismatched input fails.

    A row's verification: verified means only sha256 and physical page
    count equality — see verifies.  Locator-map content accuracy is
    evidenced separately by independent audit, not by this check.
    """
    if not isinstance(pdf_paths, dict) or set(pdf_paths) != set(SOURCE_IDS):
        return _all_failed("input_invalid")
    try:
        doc = gg_insect_document.load_precedents(map_path)
    except Exception:
        return _all_failed("map_invalid")
    expected = {s["id"]: s for s in doc["sources"]}

    results = []
    for source_id in SOURCE_IDS:
        try:
            blob = Path(pdf_paths[source_id]).read_bytes()
        except (OSError, TypeError, ValueError):
            results.append(_result(source_id, verification="pdf_unreadable"))
            continue
        digest = hashlib.sha256(blob).hexdigest()
        pages = None
        try:
            if not blob.startswith(b"%PDF-"):
                raise ValueError("invalid PDF header")
            # Open, count pages, and finish inside one isolation: pypdf
            # prints, warnings, and pre-attached logger handlers all stay
            # off this CLI's output.
            with _isolated_parse():
                from pypdf import PdfReader

                pages = len(PdfReader(BytesIO(blob), strict=True).pages)
        except Exception:
            results.append(_result(source_id, digest,
                                   verification="pdf_unreadable"))
            continue
        hash_matches = digest == expected[source_id]["sha256"]
        pages_match = pages == expected[source_id]["physical_pages"]
        if hash_matches and pages_match:
            state = "verified"
        elif not hash_matches and not pages_match:
            state = "sha256_and_page_count_mismatch"
        elif not hash_matches:
            state = "sha256_mismatch"
        else:
            state = "page_count_mismatch"
        results.append(_result(source_id, digest, pages, state))
    return results


def main(argv=None, *, map_path=MAP_PATH):
    parser = _QuietParser()
    for source_id in SOURCE_IDS:
        parser.add_argument("--" + source_id.lower(), required=True)
    try:
        args = parser.parse_args(argv)
        pdf_paths = {
            source_id: getattr(args, source_id.lower())
            for source_id in SOURCE_IDS
        }
        results = verify(map_path, pdf_paths)
    except _InvalidArguments:
        print(json.dumps(INVALID_ARGUMENTS, separators=(",", ":")))
        return 2
    except Exception:
        results = _all_failed("input_invalid")
    print(json.dumps(results, separators=(",", ":")))
    if all(row["verification"] == "verified" for row in results):
        return 0
    if any(row["verification"] in ("input_invalid", "map_invalid")
           for row in results):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
