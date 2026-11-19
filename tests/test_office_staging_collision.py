"""A resource whose name collides with the staged working copy or the PDF this
run will export must be refused before any job folder or Office call exists.

Synthetic OOXML zips and a sentinel PDF in a temporary folder; no Office.
"""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests._harness import runtime


def _ooxml(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("synthetic.xml", "<x/>")


class StagingNameCollisionTests(unittest.TestCase):
    def setUp(self):
        self.office = runtime("gg_office")
        tmp = tempfile.TemporaryDirectory(prefix="office-collision-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.src = self.root / "src"
        self.src.mkdir()
        self.ws = self.root / "ws"
        self.ws.mkdir()

    def make(self, suffix):
        doc = self.src / ("a" + suffix)
        _ooxml(doc)
        pdf = self.src / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4 earlier student PDF")
        return doc, pdf

    def assert_refused_untouched(self, ctx, files):
        self.assertIn("이름이 같은", str(ctx.exception))
        self.assertEqual(list(self.ws.iterdir()), [])
        for path, data in files.items():
            self.assertEqual(path.read_bytes(), data)

    def test_resource_named_like_the_export_pdf_is_refused_before_staging(self):
        for suffix in (".docx", ".xlsx"):
            for name in ("a.pdf", "A.PDF"):
                with self.subTest(suffix=suffix, name=name):
                    doc, pdf = self.make(suffix)
                    if name != pdf.name:
                        pdf.rename(self.src / name)
                        pdf = self.src / name
                    before = {doc: doc.read_bytes(), pdf: pdf.read_bytes()}
                    with self.assertRaises(ValueError) as ctx:
                        self.office.stage_job(doc, self.ws, [name])
                    self.assert_refused_untouched(ctx, before)
                    pdf.unlink()
                    doc.unlink()

    def test_resource_named_like_the_working_copy_is_refused_before_staging(self):
        doc, _ = self.make(".docx")
        twin = self.src / "sub"
        twin.mkdir()
        (twin / "a.docx").write_bytes(b"different file, same name")
        # Same relative name as the working copy: a case variant of the input.
        variant = self.src / "A.DOCX"
        if not variant.exists():  # case-sensitive filesystem
            variant.write_bytes(b"case variant of the input name")
        before = {doc: doc.read_bytes(), variant: variant.read_bytes()}
        with self.assertRaises(ValueError) as ctx:
            self.office.stage_job(doc, self.ws, ["A.DOCX"])
        self.assert_refused_untouched(ctx, before)
        # A same basename in a subfolder keeps its relative path: allowed.
        self.office.stage_job(doc, self.ws, ["sub/a.docx"])
        self.assertEqual(len(list(self.ws.iterdir())), 1)

    def test_hangul_precomposed_and_decomposed_names_collide(self):
        nfc, nfd = "\uac00", "\u1100\u1161"  # the same syllable on screen
        for input_stem, resource_stem in ((nfc, nfd), (nfd, nfc)):
            for target in ("pdf", "working copy"):
                with self.subTest(input=ascii(input_stem), target=target):
                    doc = self.src / (input_stem + ".docx")
                    _ooxml(doc)
                    resource = resource_stem + (".pdf" if target == "pdf" else ".docx")
                    path = self.src / resource
                    if not path.exists():  # normalization-sensitive volume
                        path.write_bytes(b"%PDF-1.4 earlier student PDF")
                    before = {doc: doc.read_bytes(), path: path.read_bytes()}
                    with self.assertRaises(ValueError) as ctx:
                        self.office.stage_job(doc, self.ws, [resource])
                    self.assert_refused_untouched(ctx, before)
                    for leftover in self.src.iterdir():
                        leftover.unlink()

    def test_student_command_entry_never_reaches_office_on_collision(self):
        for kind, suffix in (("word", ".docx"), ("excel", ".xlsx")):
            with self.subTest(kind=kind):
                doc, pdf = self.make(suffix)
                before = {doc: doc.read_bytes(), pdf: pdf.read_bytes()}
                engine = mock.Mock(side_effect=AssertionError("Office reached"))
                patches = [mock.patch.object(self.office, "_authorize_office", return_value=[]),
                           mock.patch.object(self.office, "_run_%s_engine" % kind, engine)]
                if kind == "excel":
                    patches.append(mock.patch.object(
                        self.office, "_preflight_excel_profile", return_value=(None, None)))
                for p in patches:
                    p.start()
                    self.addCleanup(p.stop)
                run = self.office.process_word if kind == "word" else self.office.process_excel
                with self.assertRaises(ValueError) as ctx:
                    run(doc, self.root / ("out-" + kind), resources=["a.pdf"], workspace=self.ws)
                engine.assert_not_called()
                self.assert_refused_untouched(ctx, before)
                self.assertFalse((self.root / ("out-" + kind)).exists())
                for p in reversed(patches):
                    p.stop()
                pdf.unlink()
                doc.unlink()


if __name__ == "__main__":
    unittest.main()
