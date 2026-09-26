"""gg_insect_precedent_check + precedents.json invariants.

The map content accuracy itself is proven only by independent audit of
the private originals; CI locks structure, closed key sets, the
verification-code table, roster, and that the checker and the document
plan share one loader verdict.  No original PDF bytes are read here.
"""
import contextlib
import copy
import json
import logging
import subprocess
import sys
import tempfile
import warnings
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from tests._harness import SCRIPTS, ContractCase, runtime

KNOWN_ROLES = {
    "alternative_eight_chapter_flow", "annual_investment",
    "assets_investment_production_and_marketing", "cash_flow",
    "climate_context", "closing_plan_and_conclusion", "content_unresolved",
    "contents", "depreciation", "environment_and_strategy",
    "farm_and_production_context", "figure_index", "financial_plan",
    "financial_position", "front_matter_and_summary", "human_resources",
    "industry_survey_context", "input_purchases", "introduction",
    "investment_plan", "itemized_investment_and_funding", "labor_cost",
    "land_and_asset_basis", "loan_repayment", "monthly_labor",
    "noncurrent_assets", "operating_expenses", "production_and_sales",
    "production_cost", "production_plan", "profit_and_loss",
    "profitability_analysis", "profitability_summary",
    "references_and_back_matter", "regional_agriculture_context",
    "repayment_summary", "swot_analysis", "swot_strategy", "table_index",
    "vision_and_goals",
}
ROSTER = {"F0", "E1", "E2", "E3", "E4"}


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-B",
         str(SCRIPTS / "gg_insect_precedent_check.py"), *map(str, args)],
        capture_output=True, text=True)


def _minimal_pdf():
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _MapCase(ContractCase):
    def setUp(self):
        self.doc = runtime("gg_insect_document")
        self.check = runtime("gg_insect_precedent_check")
        self.map = self.doc.load_precedents()

    def write_map(self, doc):
        tmp = tempfile.TemporaryDirectory(prefix="insect-map-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "precedents.json"
        path.write_text(json.dumps(doc, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def write_raw_map(self, text):
        tmp = tempfile.TemporaryDirectory(prefix="insect-map-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "precedents.json"
        path.write_text(text, encoding="utf-8")
        return path

    def reject_load(self, doc_or_text):
        path = (self.write_raw_map(doc_or_text)
                if isinstance(doc_or_text, str)
                else self.write_map(doc_or_text))
        with self.assertRaises(ValueError):
            self.doc.load_precedents(path)
        return path

    def mutate(self, fn):
        doc = copy.deepcopy(self.map)
        fn(doc)
        return doc


class MapInvariantTests(_MapCase):
    def test_top_level_closed_keys(self):
        self.assertEqual(
            set(self.map),
            {"schema", "sources", "verification_codes", "observations"})
        self.assertEqual(self.map["schema"], self.doc.SCHEMA_ID)

    def test_source_records(self):
        sources = self.map["sources"]
        self.assertEqual({s["id"] for s in sources}, ROSTER)
        for s in sources:
            self.assertEqual(
                set(s),
                {"id", "sha256", "physical_pages", "authority",
                 "title", "author", "title_basis"})
            self.assertEqual(len(s["sha256"]), 64)
            self.assertTrue(type(s["physical_pages"]) is int)
            self.assertGreaterEqual(s["physical_pages"], 1)
            self.assertEqual(s["authority"], "example_observed")

    def test_source_identifiers(self):
        expected = {
            "F0": (
                "영농창업계획 사료용 아메리카동애등에(Hermetia illucens) "
                "생충 대량생산 및 분변토 활용 기반 창업계획",
                None, "first_text_page"),
            "E1": (
                "계약판매를 통한 쌍별귀뚜라미(Gryllus bimaculatus) "
                "종충 생산 및 시스템 구축",
                "박진희", "page_1_render"),
            "E2": (
                "우량종 쌍별귀뚜라미(Gryllus bimaculatus) 대량사육방법",
                "민장욱", "first_text_page"),
            "E3": (
                "저온처리를 통한 여치(Gampsocleis sedakovii obscura) "
                "연중 대량사육 및 판매",
                "이현승", "first_text_page"),
            "E4": (None, "민경도", "not_present_in_source"),
        }
        for s in self.map["sources"]:
            title, author, basis = expected[s["id"]]
            self.assertEqual(s["title"], title)
            self.assertEqual(s["author"], author)
            self.assertEqual(s["title_basis"], basis)

    def test_no_private_identifiers(self):
        # sha256 is an identifier by contract, not student data; every
        # other string in the map must carry no 8+ digit run (student
        # IDs, phones) and no original filename extension.
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for key, sub in value.items():
                    if key == "sha256":
                        continue
                    yield from strings(sub)
            elif isinstance(value, list):
                for sub in value:
                    yield from strings(sub)
        import re
        for s in strings(self.map):
            self.assertIsNone(
                re.search(r"\d{8,}", s), s)
            self.assertNotIn(".pdf", s.lower())
            self.assertNotIn(".hwp", s.lower())

    def test_observation_records(self):
        keys = {"source_id", "physical_page", "object_label", "role",
                "status", "verification"}
        page_counts = {s["id"]: s["physical_pages"]
                       for s in self.map["sources"]}
        anchors = set()
        for o in self.map["observations"]:
            self.assertEqual(set(o), keys)
            self.assertIn(o["source_id"], ROSTER)
            self.assertTrue(type(o["physical_page"]) is int)
            self.assertGreaterEqual(o["physical_page"], 1)
            self.assertLessEqual(
                o["physical_page"], page_counts[o["source_id"]])
            self.assertTrue(o["object_label"])
            self.assertIn(o["role"], KNOWN_ROLES)
            self.assertEqual(o["status"], "example_observed")
            self.assertIn(o["verification"], self.doc.VERIFICATION_CODES)
            anchor = (o["source_id"], o["physical_page"], o["object_label"])
            self.assertNotIn(anchor, anchors)
            anchors.add(anchor)

    def test_verification_codes_match_contract(self):
        declared = self.map["verification_codes"]
        self.assertEqual(set(declared), set(self.doc.VERIFICATION_CODES))
        for code, usable in self.doc.VERIFICATION_CODES.items():
            self.assertEqual(declared[code]["usable"], usable)
            self.assertTrue(declared[code]["meaning"])
        self.assertFalse(
            declared["under_20_extracted_chars; render_or_ocr_pending"]
            ["usable"])

    def test_missing_pages_now_mapped_as_unresolved(self):
        gaps = {(o["source_id"], o["physical_page"])
                for o in self.map["observations"]
                if o["role"] == "content_unresolved"}
        for expected in (("E2", 3), ("E2", 29), ("E2", 30), ("E2", 60),
                         ("E3", 7)):
            self.assertIn(expected, gaps)
        for o in self.map["observations"]:
            if o["role"] == "content_unresolved":
                self.assertEqual(
                    o["verification"],
                    "under_20_extracted_chars; render_or_ocr_pending")

    def test_f0_table_index_complete(self):
        tables = {o["object_label"] for o in self.map["observations"]
                  if o["source_id"] == "F0"
                  and o["object_label"].startswith("table_")}
        self.assertEqual(
            tables, {"table_%02d" % i for i in range(1, 25)})

    def test_roster_and_counts(self):
        self.assertEqual(len(self.map["observations"]), 145)
        self.assertEqual(
            {s["id"]: s["physical_pages"] for s in self.map["sources"]},
            {"F0": 70, "E1": 57, "E2": 60, "E3": 65, "E4": 59})


class LoaderStrictnessTests(_MapCase):
    def test_duplicate_keys_rejected(self):
        raw = Path(self.doc.DEFAULT_PRECEDENTS).read_text(encoding="utf-8")
        self.assertIn('"physical_pages": 70', raw)
        bad = raw.replace('"physical_pages": 70',
                          '"physical_pages": 70, "physical_pages": 70', 1)
        self.reject_load(bad)

    def test_nonfinite_number_rejected(self):
        raw = Path(self.doc.DEFAULT_PRECEDENTS).read_text(encoding="utf-8")
        bad = raw.replace('"physical_pages": 70',
                          '"physical_pages": NaN', 1)
        self.reject_load(bad)

    def test_extra_or_missing_top_key_rejected(self):
        for mut in (
                lambda d: d.update(extra=1),
                lambda d: d.pop("schema"),
                lambda d: d.update(schema="other/v9")):
            with self.subTest(mut=mut):
                self.reject_load(self.mutate(mut))

    def test_code_added_or_removed_rejected(self):
        for mut in (
                lambda d: d["verification_codes"].update(
                    shiny_new_code={"usable": True, "meaning": "x"}),
                lambda d: d["verification_codes"].pop(
                    "caption_text_checked")):
            with self.subTest(mut=mut):
                self.reject_load(self.mutate(mut))

    def test_code_usable_change_rejected(self):
        def mut(d):
            d["verification_codes"]["caption_text_checked"]["usable"] = False
        self.reject_load(self.mutate(mut))

    def test_observation_unknown_code_rejected(self):
        def mut(d):
            d["observations"][0]["verification"] = "render_required"
        self.reject_load(self.mutate(mut))

    def test_source_closed_keys_and_bool_pages(self):
        for mut in (
                lambda d: d["sources"][0].update(note="x"),
                lambda d: d["sources"][0].pop("authority"),
                lambda d: d["sources"][0].update(physical_pages=True),
                lambda d: d["sources"][0].update(authority="other"),
                lambda d: d["sources"][0].update(sha256="z" * 64),
                lambda d: d["sources"][0].pop("title"),
                lambda d: d["sources"][0].update(title=42),
                lambda d: d["sources"][0].update(author=3.5),
                lambda d: d["sources"][0].update(title_basis="guessed"),
                lambda d: d["sources"][0].update(title_basis=None)):
            with self.subTest(mut=mut):
                self.reject_load(self.mutate(mut))

    def test_observation_closed_keys_and_bool_page(self):
        for mut in (
                lambda d: d["observations"][0].update(note="x"),
                lambda d: d["observations"][0].pop("role"),
                lambda d: d["observations"][0].update(physical_page=True),
                lambda d: d["observations"][0].update(physical_page=71),
                lambda d: d["observations"][0].update(role=""),
                lambda d: d["observations"][0].update(status="draft")):
            with self.subTest(mut=mut):
                self.reject_load(self.mutate(mut))

    def test_roster_enforced(self):
        self.reject_load(self.mutate(
            lambda d: d["sources"][4].update(id="E5")))


class CheckerConsistencyTests(_MapCase):
    PDFS = {sid: "/nonexistent/%s.pdf" % sid for sid in ROSTER}

    def test_corrupt_map_same_verdict_as_loader(self):
        path = self.mutate(
            lambda d: d["observations"][0].update(
                verification="render_required"))
        map_path = self.write_map(path)
        with self.assertRaises(ValueError):
            self.doc.load_precedents(map_path)
        rows = self.check.verify(map_path, self.PDFS)
        self.assertTrue(all(r["verification"] == "map_invalid"
                            for r in rows))

    def test_row_contract_verifies_and_sanitized(self):
        rows = self.check.verify(self.doc.DEFAULT_PRECEDENTS, self.PDFS)
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(
                set(row), {"id", "sha256", "physical_pages",
                           "verification", "verifies"})
            self.assertEqual(row["verifies"],
                             ["sha256", "physical_page_count"])
            self.assertEqual(row["verification"], "pdf_unreadable")
        self.assertNotIn("nonexistent", json.dumps(rows))

    def test_mismatch_states_on_synthetic_pdf(self):
        tmp = tempfile.TemporaryDirectory(prefix="insect-pdf-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "one.pdf"
        path.write_bytes(_minimal_pdf())
        pdfs = {sid: "/nonexistent/x.pdf" for sid in ROSTER}
        pdfs["F0"] = str(path)
        rows = {r["id"]: r for r in
                self.check.verify(self.doc.DEFAULT_PRECEDENTS, pdfs)}
        self.assertEqual(
            rows["F0"]["verification"],
            "sha256_and_page_count_mismatch")
        self.assertEqual(rows["F0"]["physical_pages"], 1)

    def test_missing_args_fixed_json_exit_2(self):
        out = _cli("--f0", "/tmp/SECRET-MARKER/f.pdf")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(json.loads(out.stdout),
                         {"status": "rejected",
                          "reason": "invalid_arguments"})
        self.assertNotIn("SECRET-MARKER", out.stdout + out.stderr)

    def test_unknown_option_fixed_json_exit_2(self):
        out = _cli("--f0", "a", "--e1", "b", "--e2", "c",
                   "--e3", "d", "--e4", "e", "--bogus",
                   "/tmp/SECRET-MARKER/x")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(json.loads(out.stdout),
                         {"status": "rejected",
                          "reason": "invalid_arguments"})
        self.assertNotIn("SECRET-MARKER", out.stdout + out.stderr)

    def test_unreadable_inputs_fixed_rows_exit_1(self):
        args = []
        for sid in ("f0", "e1", "e2", "e3", "e4"):
            args += ["--" + sid, "/tmp/SECRET-MARKER/%s.pdf" % sid]
        out = _cli(*args)
        self.assertEqual(out.returncode, 1)
        rows = json.loads(out.stdout)
        self.assertTrue(all(r["verification"] == "pdf_unreadable"
                            for r in rows))
        self.assertNotIn("SECRET-MARKER", out.stdout + out.stderr)


class _DynamicStream:
    """Write to whatever sys.stderr is *now* — mimics a handler stream
    that survives outer stdout/stderr redirection."""

    def write(self, data):
        sys.stderr.write(data)
        return len(data)

    def flush(self):
        sys.stderr.flush()


def _marker_handler():
    handler = logging.StreamHandler(_DynamicStream())
    handler.setFormatter(logging.Formatter("HANDLED: %(message)s"))
    return handler


def _pdf_file(case, data):
    tmp = tempfile.TemporaryDirectory(prefix="insect-pdf-")
    case.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "probe.pdf"
    path.write_bytes(data)
    return path


class ParserIsolationTests(_MapCase):
    """Final review finding 2: parser diagnostics must never escape.

    A pre-attached pypdf logger handler, parser prints, and parser
    warnings all stay off stdout/stderr, and logging.disable is restored
    afterwards even when the check path raises.
    """

    MARKER = "MARKER-W1-3-SECRET"

    def _verify_f0(self, path):
        pdfs = {sid: "/nonexistent/x.pdf" for sid in ROSTER}
        pdfs["F0"] = str(path)
        return {r["id"]: r for r in self.check.verify(
            self.doc.DEFAULT_PRECEDENTS, pdfs)}

    def _captured(self, fn):
        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(err):
                result = fn()
        return result, out.getvalue() + err.getvalue()

    def test_preattached_pypdf_handler_is_silenced_and_restored(self):
        logger = logging.getLogger("pypdf")
        handler = _marker_handler()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        # Sanity: the handler really emits markers on the live stderr.
        before = logging.root.manager.disable
        def _sanity():
            logger.warning(self.MARKER)
        _, leaked = self._captured(_sanity)
        self.assertIn("HANDLED: " + self.MARKER, leaked)

        good = _pdf_file(self, _minimal_pdf())
        bad = _pdf_file(self, b"%PDF-1.4\nMARKER-BROKEN-BYTES")
        for label, path in (("good", good), ("broken", bad)):
            with self.subTest(label=label):
                rows, captured = self._captured(
                    lambda: self._verify_f0(path))
                self.assertNotIn(self.MARKER, captured)
                self.assertNotIn("HANDLED", captured)
                self.assertEqual(
                    rows["F0"]["verification"],
                    "sha256_and_page_count_mismatch"
                    if label == "good" else "pdf_unreadable")
        self.assertEqual(logging.root.manager.disable, before)

    def test_parser_print_and_warning_is_silenced(self):
        prev_disable = logging.root.manager.disable

        class FakeReader:
            def __init__(self, stream, strict=False):
                print(ParserIsolationTests.MARKER)
                warnings.warn(ParserIsolationTests.MARKER)
                logging.getLogger("pypdf").warning(
                    ParserIsolationTests.MARKER)

            @property
            def pages(self):
                return [1, 2, 3]

        path = _pdf_file(self, _minimal_pdf())
        import pypdf
        with mock.patch.object(pypdf, "PdfReader", FakeReader):
            rows, captured = self._captured(
                lambda: self._verify_f0(path))
        self.assertNotIn(self.MARKER, captured)
        self.assertEqual(rows["F0"]["physical_pages"], 3)
        self.assertEqual(
            rows["F0"]["verification"], "sha256_and_page_count_mismatch")
        self.assertEqual(logging.root.manager.disable, prev_disable)

    def test_logging_disable_restored_when_isolation_raises(self):
        prev_disable = logging.root.manager.disable
        with self.assertRaises(RuntimeError):
            with self.check._isolated_parse():
                raise RuntimeError("boom")
        self.assertEqual(logging.root.manager.disable, prev_disable)
