"""Office OOXML relationship screening, the 17-sheet trigger, and receipt
paths (JUDGE-B 3011a5f: items d, e, f).

Synthetic zips only — no Office application is started.
"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests._harness import ContractCase, runtime

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

SETTINGS_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">{}</Relationships>'
)


def _rel(rel_id, rel_type, target, external=True):
    mode = ' TargetMode="External"' if external else ""
    return ('<Relationship Id="%s" Type="%s" Target="%s"%s/>'
            % (rel_id, rel_type, target, mode))


def _docx(path, rels=""):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("word/_rels/settings.xml.rels", SETTINGS_RELS.format(rels))
        zf.writestr("word/settings.xml", "<w:settings/>")
    return path


class RelationshipTests(ContractCase):
    def setUp(self):
        self.office = runtime("gg_office")
        tmp = tempfile.TemporaryDirectory(prefix="p1-rel-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_attached_template_external_rel_passes(self):
        for target in (
            "file:///C:/Users/student/AppData/Roaming/Microsoft/Templates/"
            "Normal.dotm",
            "C:\\Users\\student\\Templates\\Normal.dotm",
            "Normal.dotm",
        ):
            with self.subTest(target=target):
                path = _docx(self.root / "word.docx", _rel(
                    "rId1", REL + "attachedTemplate", target))
                self.office.check_ooxml_relationships(path)

    def test_a_local_file_url_template_passes(self):
        """Word writes the user's own template as a host-less file URL."""
        path = _docx(self.root / "word.docx", _rel(
            "rId1", REL + "attachedTemplate",
            "file:///Users/student/Library/Group%20Containers/User%20Content"
            ".dotm"))
        self.office.check_ooxml_relationships(path)

    def test_remote_network_and_traversing_targets_are_refused(self):
        """The allowance is for local files only: these five shapes stay
        refused on input and output alike."""
        cases = (
            ("attachedTemplate", "https://example.invalid/remote.dotm"),
            ("externalLinkPath", "\\\\example.invalid\\share\\book.xlsx"),
            ("oleObject", "//example.invalid/share/object.bin"),
            ("oleObject", "../../outside/object.bin"),
            ("attachedTemplate", "file://example.invalid/share/remote.dotm"),
            # R1b: the same shapes reached through URL parsing or encoding.
            ("attachedTemplate",
             "file:////example.invalid/share/remote.dotm"),
            ("attachedTemplate",
             "%5C%5Cexample.invalid%5Cshare%5Cremote.dotm"),
        )
        for suffix, target in cases:
            with self.subTest(target=target):
                path = _docx(self.root / "word.docx", _rel(
                    "rId1", REL + suffix, target))
                with self.assertRaises(ValueError) as cm:
                    self.office.check_ooxml_relationships(path)
                self.assertIn("위험한 외부 OOXML 관계 참조", str(cm.exception))

    def test_remote_targets_are_refused_even_when_present_on_input(self):
        """There is no input baseline that excuses them: the same rule
        screens staging and the app-rewritten output."""
        for suffix, target in (
            ("attachedTemplate", "https://example.invalid/remote.dotm"),
            ("oleObject", "\\\\example.invalid\\share\\object.bin"),
            ("attachedTemplate",
             "file:////example.invalid/share/remote.dotm"),
            ("attachedTemplate",
             "%5C%5Cexample.invalid%5Cshare%5Cremote.dotm"),
        ):
            with self.subTest(target=target):
                path = _docx(self.root / "word.docx", _rel(
                    "rId1", REL + suffix, target))
                for call in (self.office.stage_job,
                             self.office.validate_office_file):
                    with self.assertRaises(ValueError):
                        if call is self.office.stage_job:
                            call(path, self.root / "ws")
                        else:
                            call(path, "word")

    def test_glossary_document_external_rel_passes(self):
        path = _docx(self.root / "word.docx", _rel(
            "rId1", REL + "glossaryDocument",
            "file:///C:/Program%20Files/Office/Building%20Blocks.dotx"))
        self.office.check_ooxml_relationships(path)

    def test_a_dangerous_relationship_is_rejected(self):
        path = _docx(self.root / "word.docx", _rel(
            "rId1", REL + "oleObject", "https://example.invalid/payload.exe"))
        with self.assertRaises(ValueError) as cm:
            self.office.check_ooxml_relationships(path)
        self.assertIn("위험한 외부 OOXML 관계 참조", str(cm.exception))

    def test_local_targets_and_plain_hyperlinks_are_allowed(self):
        for rel_type, target in (
            (REL + "hyperlink", "https://example.com/paper"),
            (REL + "externalLink", "data.xlsx"),
        ):
            path = _docx(self.root / "word.docx",
                         _rel("rId1", rel_type, target))
            self.office.check_ooxml_relationships(path)

    def test_an_unsafe_hyperlink_is_rejected(self):
        path = _docx(self.root / "word.docx", _rel(
            "rId1", REL + "hyperlink", "javascript:alert(1)"))
        with self.assertRaises(ValueError):
            self.office.check_ooxml_relationships(path)


class SchoolSheetTriggerTests(ContractCase):
    """A single overlapping sheet name must not start the 17-sheet check."""

    def test_legacy_school_check_requires_the_full_ordered_set(self):
        office = runtime("gg_office")
        sheets = list(runtime("gg_school_excel").SCHOOL_SHEETS)
        self.assertFalse(office._legacy_school_check_applies(
            ["목록", "내 농장 계획", "자금계획"], False, sheets))
        self.assertTrue(office._legacy_school_check_applies(
            sheets, False, sheets))
        # Order matters: same names, different sequence.
        self.assertFalse(office._legacy_school_check_applies(
            list(reversed(sheets)), False, sheets))
        self.assertTrue(office._legacy_school_check_applies(
            ["아무거나"], True, sheets))


class ReceiptPathTests(ContractCase):
    """Receipts carry workspace-relative paths, never the student's home."""

    def test_receipt_paths_are_workspace_relative(self):
        office = runtime("gg_office")
        with tempfile.TemporaryDirectory(prefix="p1-ws-") as tmp:
            ws = Path(tmp).resolve()
            job = ws / "job-abc-1700000000"
            (job / "output").mkdir(parents=True)
            work = job / "output" / "학생 문서.docx"
            work.write_bytes(b"x")
            frag = office.job_receipt_paths(
                job, ws, Path("/Users/student/Desktop/학생 문서.docx"),
                work, job / "output" / "학생 문서.pdf")
        self.assertTrue(frag["workspace_relative"])
        self.assertEqual("job-abc-1700000000", frag["job_id"])
        self.assertEqual("job-abc-1700000000", frag["job_dir"])
        self.assertEqual("job-abc-1700000000/output/학생 문서.docx",
                         frag["staged_working_file"])
        self.assertEqual("학생 문서.docx", frag["input_file"])
        self.assertNotIn("student", json.dumps(frag, ensure_ascii=False))

    def test_an_outside_path_degrades_to_its_basename(self):
        office = runtime("gg_office")
        with tempfile.TemporaryDirectory(prefix="p1-ws-") as tmp:
            ws = Path(tmp).resolve()
            job = ws / "job-x"
            job.mkdir()
            frag = office.job_receipt_paths(
                job, ws, Path("/elsewhere/in.docx"),
                Path("/elsewhere/out.docx"))
        self.assertEqual("out.docx", frag["staged_working_file"])
        self.assertEqual("in.docx", frag["input_file"])


if __name__ == "__main__":
    unittest.main()
