"""A refused first read must not keep the student's workbook open.

The shared read (``gg_excel_template.read_template``) asks openpyxl to open
the file before any other check.  openpyxl 3.1.5 closes the package archive
only after a *successful* read, so a parse failure inside
``xl/sharedStrings.xml`` left the file open; on Windows the temporary
cleanup of ``tests/test_specialty_template_route.py`` then failed with
``WinError 32`` for ``sb.xlsx`` (run 44572336358, py3.14).

The contract: after ``read_template`` refuses a broken workbook, the caller
can rename and delete that file immediately, in the same process, with the
original exception and traceback still alive.  No process exit, no
``gc.collect()``, no sleep, no retry, no cleanup-error tolerance.

POSIX renames an open file happily, so the rename/unlink alone only fails on
Windows.  The descriptor observation below is the POSIX-visible half: a
descriptor of this process still pointing at the file's inode.  Neither half
is evidence for the other platform.
"""

import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import Workbook

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gg_excel_template as tpl  # noqa: E402

BROKEN = b"<sst"


def broken_shared_strings(path):
    """A workbook whose labels live in ``xl/sharedStrings.xml``, truncated.

    openpyxl writes inline strings, so the labels are moved into a shared
    string table the way Excel stores them; openpyxl then parses that part
    while opening and fails inside it.
    """
    base = path.with_name("base-" + path.name)
    wb = Workbook()
    wb.active["B2"] = "미상품과"
    wb.save(base)
    try:
        with zipfile.ZipFile(base) as z:
            members = [(item, z.read(item.filename)) for item in z.infolist()]
    finally:
        base.unlink()
    strings = []

    def to_shared(data):
        def repl(m):
            strings.append(m.group(2))
            return (m.group(1).replace(b' t="inlineStr"', b' t="s"')
                    + b"<v>%d</v></c>" % (len(strings) - 1))
        return re.sub(rb'(<c [^>]*t="inlineStr"[^>]*>)<is><t>(.*?)</t></is></c>', repl, data)

    with zipfile.ZipFile(path, "w") as w:
        for item, data in members:
            if item.filename.startswith("xl/worksheets/"):
                data = to_shared(data)
            elif item.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType='
                                    b'"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
            elif item.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdSST" Type="http://schemas.'
                                    b'openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
                                    b'Target="sharedStrings.xml"/></Relationships>')
            w.writestr(item, data)
        w.writestr("xl/sharedStrings.xml", BROKEN)
    if not strings:
        raise AssertionError("fixture move produced no shared strings")
    return path


def descriptors_on(path):
    """Descriptors of this process that still point at ``path``.

    Returns ``None`` where the platform has no descriptor directory, so the
    rename/unlink half carries the test alone there.
    """
    if os.name == "nt":
        return None
    base = "/dev/fd" if os.path.isdir("/dev/fd") else "/proc/self/fd"
    if not os.path.isdir(base):
        return None
    target = os.stat(path)
    hits = []
    for name in os.listdir(base):
        if not name.isdigit():
            continue
        try:
            st = os.fstat(int(name))
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino):
            hits.append(int(name))
    return sorted(hits)


class RefusedReadReleasesSource(unittest.TestCase):
    def test_refused_read_leaves_the_source_replaceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = broken_shared_strings(Path(tmp) / "sb.xlsx")
            with zipfile.ZipFile(path) as z:
                self.assertEqual(z.read("xl/sharedStrings.xml"), BROKEN)
                self.assertEqual(z.namelist().count("xl/sharedStrings.xml"), 1)
            # No openpyxl call of our own here: a failed load_workbook leaks
            # its own descriptor through the chained cause's traceback, and
            # the observation below must measure the product only.
            self.assertEqual(descriptors_on(path) or [], [])

            refused = None
            try:
                tpl.read_template(path)
            except tpl.TemplateUnreadable as exc:
                refused = exc
            if refused is None:
                self.fail("read_template accepted a workbook whose shared "
                          "string table is truncated")
            self.assertIn("workbook does not open", str(refused))
            self.assertIsNotNone(refused.__cause__)
            self.assertIsNotNone(refused.__traceback__)
            # Where descriptors are observable (macOS/Linux) none may still
            # point at the student's file.  Windows has no descriptor
            # directory; there the rename and unlink below are the check,
            # because an open handle makes both fail with WinError 32.
            held = descriptors_on(path)
            if held is not None:
                self.assertEqual(held, [])

            moved = path.with_name("sb-moved.xlsx")
            path.rename(moved)
            moved.unlink()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
