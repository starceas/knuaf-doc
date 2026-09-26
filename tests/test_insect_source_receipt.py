"""Synthetic, offline boundaries for the pinned insect-source receipt CLI.

Covers DESIGN 6.1 (pinned-open hardening, provenance claims), 6.4 (runtime
conflict locus), 14.4 (per-field basis), 14.5 (no leaks on any channel), and
the return-contract opinion of 14.8.  Every fixture is synthetic — no real
R0/R1 bytes, no private paths.
"""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import warnings

from openpyxl import Workbook

from tests._harness import SCRIPTS, ContractCase, runtime

receipt = runtime("gg_insect_source_receipt")


def _layout_row(label, values, *, prefix_number=False):
    row = [" "] * 94
    row[8:8 + len(label)] = label
    if prefix_number:
        row[18] = "7"  # outside the 2020 band, after the row label
    for start, value in zip((24, 34, 44, 54, 64), values):
        value = str(value)
        position = start + 2
        row[position:position + len(value)] = value
    return "".join(row).rstrip()


def _species_rows(species_counts):
    """Species sales+parenthesized-count row pairs, mirroring R0 layout."""
    lines = []
    for label, sales, count in species_counts:
        lines.append(_layout_row(label, (1, 2, 3, 4, sales)))
        count_values = tuple("(%s)" % n for n in (1, 2, 3, 4, count))
        lines.append(_layout_row("", count_values))
    return lines


def synthetic_pages(*, farm_count=2293, total_sales=42010,
                    cricket_sales=2500, fly_sales=8300,
                    group_sales=9500, group_count=305,
                    cricket_prefix_number=False, include_locus=True):
    header = " " * 24 + "2020      2021      2022      2023      2024"
    table = [
        "참고 2 연도별 주요 통계",
        "(단위 : 개소, 백만원, 명, %)",
        header,
    ]
    if include_locus:
        table += _species_rows((
            ("흰점박이", 12505, 720),
            ("갈색거저리", 4800, 275),
        ))
    table.append(_layout_row(
        "귀뚜라미", (1, 2, 3, 4, cricket_sales),
        prefix_number=cricket_prefix_number))
    table.append(_layout_row("", ("(1)", "(2)", "(3)", "(4)", "(140)")))
    if include_locus:
        table += _species_rows((
            ("장수풍뎅이", 3600, 350),
        ))
    # 사슴벌레 prints its label on the count row (layout quirk in R0).
    table.append(_layout_row("", (1, 2, 3, 4, 1960)))
    table.append(_layout_row(
        "사슴벌레", ("(130)", "(135)", "(140)", "(185)", "(190)")))
    table.append(_layout_row("아메리카", (1, 2, 3, 4, fly_sales)))
    table.append(_layout_row("동애등에",
                             ("(210)", "(205)", "(200)", "(225)", "(220)")))
    if include_locus:
        table.append(_layout_row("기  타", (1, 2, 3, 4, group_sales)))
        table.append(_layout_row(
            "(반딧불이, 나비 등)",
            ("(1)", "(2)", "(3)", "(4)", "(%s)" % group_count)))
        table.append(_layout_row("소 계", (1, 2, 3, 4, group_sales)))
        table.append(_layout_row("", ("(1)", "(2)", "(3)", "(4)",
                                      "(%s)" % group_count)))
    table.append(_layout_row("합 계", (1, 2, 3, 4, total_sales)))
    table.append(_layout_row(
        "(중복포함)", ("(1)", "(2)", "(3)", "(4)", "(%s)" % farm_count)))
    table.append("- 4 -")
    return [
        "2024년 곤충산업 현황 실태조사 결과\n"
        "곤충생산·가공·유통업을 신고한 농가 및 법인\n* 꿀벌 제외",
        "",
        "",
        "\n".join(table),
        "승인번호: 제114060호",
    ]


# Synthetic R1 cells: headers and locus cells mirror the real sheet layout;
# every numeric value below is invented — R1 values must never be copied
# into the package because its redistribution rights are unconfirmed
# (DESIGN 16.3).  Counts differ from the R0 fixture by one; sales differ
# only by a fraction display rounding explains.
R1_BASE_CELLS = {
    "A1": "1. 곤충 사육·가공·유통 농가·업체",
    "B3": "농가수\n합계", "C3": "판매액",
    "D3": "연간 사육곤충 농가수(호), 마리수(마리), 판매액(백만원)",
    "A23": "계",
    "D4": "장수풍뎅이", "D5": "농가수",
    "G4": "사슴벌레", "G5": "농가수",
    "J4": "흰점박이 꽃무지", "J5": "농가수",
    "M4": "갈색거저리", "M5": "농가수",
    "P4": "나비", "P5": "농가수",
    "S4": "동애등에", "S5": "농가수",
    "V4": "귀뚜라미", "V5": "농가수",
    "Y4": "반딧불이", "Y5": "농가수",
    "AB4": "누에", "AB5": "농가수",
    "AE4": "기타", "AE5": "농가수",
    "R5": "판매액", "U5": "판매액", "X5": "판매액",
    "AA5": "판매액", "AD5": "판매액", "AG5": "판매액",
    "B23": 2294, "C23": 42010.3,
    "D23": 350, "G23": 190, "J23": 720, "M23": 275,
    "P23": 40, "S23": 220, "V23": 140, "Y23": 20,
    "AB23": 108, "AE23": 138,
    "R23": 900, "AA23": 400, "AD23": 2100, "AG23": 6099.7,
    "U23": 8299.7, "X23": 2500,
}


class SourceReceiptTests(ContractCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pdf = self.root / "private-absolute-path.pdf"
        self.xlsx = self.root / "private-absolute-path.xlsx"
        self.pdf.write_bytes(b"synthetic PDF bytes; no real source content")
        self._make_xlsx()

    def _make_xlsx(self, **overrides):
        cells = dict(R1_BASE_CELLS)
        cells.update(overrides)
        wb = Workbook()
        ws = wb.active
        ws.title = receipt.SHEET
        for address, value in cells.items():
            ws[address] = value
        ws["Z11"] = "Private Person 010-9999-9999"
        wb.save(self.xlsx)
        wb.close()

    def _pins(self):
        return {
            "expected_pdf_sha256": hashlib.sha256(
                self.pdf.read_bytes()).hexdigest(),
            "expected_xlsx_sha256": hashlib.sha256(
                self.xlsx.read_bytes()).hexdigest(),
        }

    def _verify(self, pages=None, *, pins=None):
        with mock.patch.object(receipt, "_pdf_pages",
                               return_value=(pages or synthetic_pages(),
                                             "test-parser")):
            return receipt._verify_with_pins(
                str(self.pdf), str(self.xlsx), **(pins or self._pins()))

    def test_wrong_pdf_hash_fails_before_parse_and_cli_leaks_no_path(self):
        # The public API pins the official hashes; synthetic bytes can never
        # satisfy them, so parsing must not even start.
        with mock.patch.object(receipt, "_pdf_pages") as parse:
            with self.assertRaises(receipt.ReceiptError) as caught:
                receipt.verify_sources(str(self.pdf), str(self.xlsx))
        self.assertEqual(("hash_mismatch", "R0"),
                         (caught.exception.reason, caught.exception.source_id))
        parse.assert_not_called()
        out = StringIO()
        with redirect_stdout(out):
            code = receipt.main(
                ["--pdf", str(self.pdf), "--xlsx", str(self.xlsx)])
        self.assertEqual(2, code)
        response = json.loads(out.getvalue())
        self.assertEqual("rejected", response["status"])
        self.assertNotIn(str(self.root), out.getvalue())
        self.assertNotIn("Private Person", out.getvalue())

    def test_wrong_xlsx_hash_fails_before_either_parser(self):
        pins = self._pins()
        pins["expected_xlsx_sha256"] = "0" * 64
        with mock.patch.object(receipt, "_pdf_pages") as parse:
            with self.assertRaises(receipt.ReceiptError) as caught:
                receipt._verify_with_pins(str(self.pdf), str(self.xlsx),
                                          **pins)
        self.assertEqual(("hash_mismatch", "R1"),
                         (caught.exception.reason, caught.exception.source_id))
        parse.assert_not_called()

    def test_public_verify_sources_takes_no_hash_arguments(self):
        params = set(inspect.signature(receipt.verify_sources).parameters)
        self.assertEqual({"pdf_path", "xlsx_path"}, params)

    def test_schema_declares_revision_two(self):
        # DESIGN 16.4: output shape changed (origin_claims, conflict_locus,
        # audit_notes), so the receipt schema marker is /2.
        self.assertEqual("gg-insect-source-receipt/2", receipt.SCHEMA)
        self.assertEqual("gg-insect-source-receipt/2", self._verify()["schema"])

    def test_non_regular_inputs_refused_before_open(self):
        pins = self._pins()
        fifo = self.root / "fifo-source.pdf"
        os.mkfifo(fifo)
        link = self.root / "linked.pdf"
        os.symlink(self.pdf, link)
        for path in (fifo, self.root, link):
            with self.subTest(path=path.name):
                try:
                    receipt._verify_with_pins(
                        str(path), str(self.xlsx),
                        expected_pdf_sha256="0" * 64,
                        expected_xlsx_sha256=pins["expected_xlsx_sha256"])
                except receipt.ReceiptError as error:
                    self.assertIn(error.reason,
                                  {"not_regular_file", "source_unreadable"})
                else:
                    self.fail("non-regular input accepted")

    def test_argparse_error_is_fixed_json_without_argv_echo(self):
        marker = self.root / "SECRET-MARKER" / "arg.pdf"
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                receipt.main(["--pdf", str(marker), "--xlsx",
                              str(self.xlsx), "--unexpected-flag"])
            except SystemExit as exit_:
                code = exit_.code
            else:
                code = 0
        self.assertEqual(2, code)
        response = json.loads(out.getvalue())
        self.assertEqual({"status": "rejected",
                          "reason": "invalid_arguments"},
                         {k: response[k] for k in ("status", "reason")})
        self.assertNotIn("SECRET-MARKER", out.getvalue())
        self.assertNotIn("SECRET-MARKER", err.getvalue())

    def test_cli_rejection_is_clean_in_subprocess(self):
        import subprocess

        marker = str(self.root / "SECRET-MARKER" / "missing.pdf")
        for argv in (
            ["--pdf", marker, "--xlsx", str(self.xlsx)],
            ["--pdf", marker, "--xlsx", str(self.xlsx), "--bogus"],
        ):
            proc = subprocess.run(
                [sys.executable, "-B",
                 str(SCRIPTS / "gg_insect_source_receipt.py"), *argv],
                capture_output=True, text=True)
            self.assertEqual(2, proc.returncode)
            self.assertNotIn("SECRET-MARKER", proc.stdout)
            self.assertNotIn("SECRET-MARKER", proc.stderr)
            self.assertEqual("rejected", json.loads(proc.stdout)["status"])

    def test_parser_diagnostics_never_reach_output_channels(self):
        def noisy_parser(blob):
            print("parser sees /tmp/SECRET-MARKER/real.pdf")
            sys.stderr.write("diagnostic with /tmp/SECRET-MARKER\n")
            import warnings as _w
            _w.warn("warning naming /tmp/SECRET-MARKER")
            raise RuntimeError("failed inside /tmp/SECRET-MARKER")

        out, err = StringIO(), StringIO()
        with mock.patch.object(receipt, "_pdf_pages", noisy_parser):
            with redirect_stdout(out), redirect_stderr(err):
                with self.assertRaises(receipt.ReceiptError) as caught:
                    receipt._verify_with_pins(
                        str(self.pdf), str(self.xlsx), **self._pins())
        self.assertEqual("pdf_parse_failed", caught.exception.reason)
        self.assertNotIn("SECRET-MARKER", out.getvalue() + err.getvalue())

    def test_preattached_logger_handlers_are_silenced_and_restored(self):
        # Review counterexample: a handler attached before the parse writes
        # to the streams directly, so propagate toggles cannot stop it.
        def chatty_success(blob):
            logging.getLogger("pypdf").error(
                "SECRET-MARKER via a pre-attached pypdf handler")
            return synthetic_pages(), "test-parser"

        def chatty_failure(blob):
            logging.getLogger("pypdf").error(
                "SECRET-MARKER via a pre-attached pypdf handler")
            raise RuntimeError("SECRET-MARKER boom")

        baseline = logging.root.manager.disable
        parsers = {"success": chatty_success, "failure": chatty_failure}
        for label, parser in parsers.items():
            with self.subTest(outcome=label):
                out, err = StringIO(), StringIO()
                logger = logging.getLogger("pypdf")
                # Bound inside the redirect so a leak would be captured.
                handler = logging.StreamHandler(sys.stderr)
                with redirect_stdout(out), redirect_stderr(err):
                    logger.addHandler(handler)
                    try:
                        with mock.patch.object(receipt, "_pdf_pages", parser):
                            try:
                                self._verify()
                            except receipt.ReceiptError as caught:
                                self.assertEqual(label, "failure")
                                self.assertEqual("pdf_parse_failed",
                                                 caught.reason)
                    finally:
                        logger.removeHandler(handler)
                self.assertNotIn("SECRET-MARKER",
                                 out.getvalue() + err.getvalue())
                self.assertEqual(baseline, logging.root.manager.disable)
                self.assertTrue(logger.isEnabledFor(logging.WARNING))

    def test_lazy_xlsx_cell_reads_cannot_leak(self):
        # Review counterexample: read_only sheets resolve cells lazily, so a
        # marker raised during cell reads must stay inside the quiet box.
        import openpyxl

        emitted = []

        class NoisyCell:
            def __init__(self, value, data_type="n"):
                self._value = value
                self.data_type = data_type

            @property
            def value(self):
                emitted.append("read")
                print("SECRET-MARKER lazy cell read")
                warnings.warn("SECRET-MARKER warning")
                logging.getLogger("openpyxl").error("SECRET-MARKER log")
                return self._value

        class FakeWorkbook:
            def __init__(self):
                self.sheetnames = [receipt.SHEET]
                self.sheet = {address: NoisyCell(value)
                              for address, value in R1_BASE_CELLS.items()}
                self.closed = False

            def __getitem__(self, name):
                return self.sheet

            def close(self):
                self.closed = True

        fake_wb, fake_fx = FakeWorkbook(), FakeWorkbook()
        loads = iter((fake_wb, fake_fx))
        baseline = logging.root.manager.disable
        out, err = StringIO(), StringIO()
        logger = logging.getLogger("openpyxl")
        handler = logging.StreamHandler(sys.stderr)
        with redirect_stdout(out), redirect_stderr(err):
            logger.addHandler(handler)
            try:
                with mock.patch.object(openpyxl, "load_workbook",
                                       side_effect=lambda *a, **k: next(loads)):
                    values, storage, locus, version = receipt._extract_xlsx(
                        self.xlsx.read_bytes())
            finally:
                logger.removeHandler(handler)
        self.assertEqual(2294, values["farm_count"])
        self.assertEqual(
            {key: "literal_numeric" for key in
             ("sales_total", "sales_fly", "sales_cricket", "farm_count")},
            storage)
        self.assertTrue(fake_wb.closed and fake_fx.closed)
        self.assertTrue(emitted)  # the noisy property really ran
        self.assertNotIn("SECRET-MARKER", out.getvalue() + err.getvalue())
        self.assertEqual(baseline, logging.root.manager.disable)

    def test_isolation_restores_logging_state(self):
        baseline = logging.root.manager.disable

        def success():
            logging.getLogger("pypdf").error("suppressed")
            return "values-only"

        self.assertEqual("values-only", receipt._isolated_parse(success))
        self.assertEqual(baseline, logging.root.manager.disable)

        def failure():
            logging.getLogger("pypdf").error("suppressed")
            raise RuntimeError("SECRET-MARKER")

        with self.assertRaises(RuntimeError):
            receipt._isolated_parse(failure)
        self.assertEqual(baseline, logging.root.manager.disable)
        self.assertTrue(logging.getLogger("pypdf").isEnabledFor(
            logging.WARNING))

    def test_no_acquisition_date_or_w21_basis_anywhere(self):
        result = self._verify()
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("local_acquisition_date", blob)
        self.assertNotIn("acquisition_date", blob)
        self.assertNotIn("w21_", blob)
        self.assertNotIn("date_basis", blob)

    def test_origin_claims_carry_per_field_basis(self):
        # Force the receipt to treat the synthetic bytes as the official pins
        # so origin_claims are emitted; then inspect the claim structure.
        with mock.patch.object(receipt, "PDF_SHA256",
                               hashlib.sha256(self.pdf.read_bytes()).hexdigest()), \
             mock.patch.object(receipt, "XLSX_SHA256",
                               hashlib.sha256(self.xlsx.read_bytes()).hexdigest()):
            result = self._verify()
        self.assertTrue(result["official_pins"])
        claims = result["origin_claims"]
        r0, r1 = claims["R0"], claims["R1"]
        self.assertEqual("research_claim_unverified",
                         r0["download_url"]["basis"])
        self.assertEqual("research_claim_unverified",
                         r0["source_publication_date"]["basis"])
        self.assertEqual("pdf_metadata_creation_date",
                         r0["source_publication_date"]["partial_evidence"])
        for key in ("data_id", "download_path", "download_method",
                    "download_parameters", "portal_dataset_registered_date",
                    "portal_last_modified_date", "license_scope_field",
                    "file_publication_date"):
            self.assertEqual("private_portal_snapshot_2026-09-24",
                             r1[key]["basis"], key)
        self.assertIsNone(r1["file_publication_date"]["value"])
        self.assertEqual("blank", r1["license_scope_field"]["value"])
        self.assertEqual({"file_ty_code": "file", "file_sn": "17"},
                         r1["download_parameters"]["value"])
        self.assertEqual("inferred_host", r1["host"]["basis"])
        self.assertEqual("composed", r1["landing_url"]["status"])
        self.assertEqual("composed", r1["download_url"]["status"])
        self.assertEqual("research_claim_unverified",
                         r1["landing_url"]["parts_basis"]["service_ty=F"])
        self.assertEqual("inferred_host",
                         r1["landing_url"]["parts_basis"]["host"])
        self.assertEqual("private_portal_snapshot_2026-09-24",
                         r1["download_url"]["parts_basis"]["download_path"])
        self.assertEqual("not_rechecked", r1["r1_portal_identity"])

    def test_unofficial_pins_get_no_origin_claims(self):
        result = self._verify()
        self.assertFalse(result["official_pins"])
        self.assertIsNone(result["origin_claims"])

    def test_display_rounding_and_conflict_locus_extracted(self):
        result = self._verify()
        relations = {c["metric"]: c["relation"] for c in result["comparisons"]}
        self.assertEqual("display_rounding_consistent", relations["sales_total"])
        self.assertEqual("exact_numeric_match", relations["sales_cricket"])
        self.assertEqual("display_rounding_consistent", relations["sales_fly"])
        self.assertEqual("source_conflict", relations["reported_farm_count"])
        self.assertIn("relation_note", result)
        locus = result["conflict_locus"]
        self.assertEqual("extracted", locus["status"])
        self.assertEqual("runtime_extracted", locus["basis"])
        self.assertEqual("unresolved", locus["cause"])
        self.assertEqual("305", locus["R0"]["farm_count_2024"])
        self.assertEqual("9500", locus["R0"]["sales_2024_KRW_million"])
        self.assertEqual("306", locus["R1"]["farm_count_2024_total"])
        self.assertEqual("9499.7",
                         locus["R1"]["sales_2024_KRW_million_total"])
        self.assertEqual({"P23": "40", "Y23": "20", "AB23": "108",
                          "AE23": "138"},
                         locus["R1"]["farm_count_cells"])
        matched = [s for s in locus["species_farm_counts"]
                   if s["relation"] == "match"]
        self.assertEqual(6, len(matched))
        audits = [n["basis"] for n in result["audit_notes"]]
        self.assertTrue(all(b == "independent_audit_note" for b in audits))
        self.assertTrue(any("누에" in n["note"] for n in result["audit_notes"]))

    def test_locus_extraction_failure_changes_nothing_else(self):
        # A table without the species/group rows still produces the pinned
        # receipt; only the diagnostic degrades (DESIGN 15.2).
        result = self._verify(pages=synthetic_pages(include_locus=False))
        self.assertEqual("partial_verification_source_conflict",
                         result["status"])
        self.assertEqual({"status": "not_extracted", "cause": "unresolved"},
                         result["conflict_locus"])
        self.assertEqual("source_conflict",
                         result["conflicts"][0]["status"])
        self.assertEqual("quarantined",
                         result["conflicts"][0]["disposition"])

    def test_locus_xlsx_header_mismatch_degrades_only_locus(self):
        self._make_xlsx(AB4="멸종위기종")
        result = self._verify()
        self.assertEqual("partial_verification_source_conflict",
                         result["status"])
        self.assertEqual("not_extracted", result["conflict_locus"]["status"])

    def test_missing_required_numeric_cell_fails_closed(self):
        self._make_xlsx(C23=None)
        with self.assertRaises(receipt.ReceiptError) as caught:
            self._verify()
        self.assertEqual("xlsx_required_numeric_cell_missing",
                         caught.exception.reason)

    def test_formula_without_cached_value_fails_closed(self):
        self._make_xlsx(C23="=SUM(U23:X23)")
        with self.assertRaises(receipt.ReceiptError) as caught:
            self._verify()
        self.assertEqual("xlsx_required_numeric_cell_missing",
                         caught.exception.reason)

    def test_extra_number_before_year_columns_rejects_misparsed_2024(self):
        pages = synthetic_pages(cricket_prefix_number=True)
        with self.assertRaises(receipt.ReceiptError) as caught:
            self._verify(pages)
        self.assertEqual(("pdf_year_column_mismatch", "R0"),
                         (caught.exception.reason, caught.exception.source_id))

    def test_matching_figures_have_no_count_conflict(self):
        self._make_xlsx(B23=2293, C23=42010, U23=8300, X23=2500)
        result = self._verify()
        self.assertEqual([], result["conflicts"])
        self.assertEqual("selected_aggregates_parsed", result["status"])
        self.assertEqual("exact_numeric_match",
                         result["comparisons"][0]["relation"])

    def test_conflict_quarantined_and_private_column_absent(self):
        result = self._verify()
        self.assertEqual("partial_verification_source_conflict", result["status"])
        self.assertEqual({"R0": "2293", "R1": "2294",
                          "metric": "reported_farm_count",
                          "status": "source_conflict",
                          "disposition": "quarantined",
                          "resolution": "unresolved_definition_timing_or_erratum"},
                         result["conflicts"][0])
        self.assertEqual("-0.3", result["comparisons"][2]["r1_minus_r0_KRW_million"])
        self.assertEqual("species_label_mapping_unconfirmed",
                         result["comparisons"][2].get("caveat"))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Private Person", serialized)
        self.assertNotIn("010-9999-9999", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertIn("no_yield_claims", result["claim_limits"])
        self.assertIn("unconfirmed", result["rights"]["R1"])

    def test_return_contract_has_no_generated_body_or_finance(self):
        # DESIGN 14.8: the receipt is a review artifact — it must not smuggle
        # rendered body fields, computed finance keys, or output authority.
        result = self._verify()

        def keys(node, found):
            if isinstance(node, dict):
                for key, value in node.items():
                    found.add(key)
                    keys(value, found)
            elif isinstance(node, list):
                for item in node:
                    keys(item, found)

        found = set()
        keys(result, found)
        forbidden = {"body", "rendered_paper", "calculated", "computed",
                     "result", "total_production", "projected_revenue",
                     "farm_price", "authorization", "authorized",
                     "approve", "promoted"}
        self.assertEqual(set(), found & forbidden)
        self.assertNotIn("authorization", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
