"""Lane B receipt tests — write_scan_receipt per frozen API section 2.4.

Receipt publication is a capability-gated write through the P2
no-replace primitive.  The capability boundary is the parent's own
``gg_lock.assert_held`` (matching gg_publication.py:909): a missing,
forged, released or misbound capability must FAIL — nothing is
silently written.  ``03_sources.md`` and generated views are never
touched.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gg_lock  # noqa: E402
import gg_reuse  # noqa: E402
import gg_source_intake as si  # noqa: E402

KNUAF_DOC = Path(__file__).resolve().parents[1]
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"

KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}
CV = "p5-classifier/1"


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def key_catalog():
    return {"narrative_reference": {
        KIM_KEY["value"]: {"authority": "verified",
                           "catalog_digest": None,
                           "receipt_sha256": None}}}


def policy(**kw):
    p = {"classification_version": CV,
         "include": [], "exclude": [], "always_hash": [],
         "document_extensions": ["md", "txt"]}
    p.update(kw)
    return p


class WorkspaceFixture(unittest.TestCase):
    """A scan source tree + a separate workspace root with an installed
    v2 lock control."""

    def setUp(self):
        self._t1 = tempfile.TemporaryDirectory()
        self._t2 = tempfile.TemporaryDirectory()
        self.addCleanup(self._t1.cleanup)
        self.addCleanup(self._t2.cleanup)
        self.ext = Path(self._t1.name)      # external scan root
        self.ws = Path(self._t2.name)       # workspace root (product)
        gg_lock.install_new(self.ws)

    def file(self, rel, data=b"x"):
        p = self.ext / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def scan(self, **kw):
        args = dict(root=self.ws, classification_version=CV,
                    registry=registry(), key_catalog=key_catalog(),
                    scan_policy=policy())
        args.update(kw)
        return si.run_source_scan(self.ext, **args)


class TestReceiptPublication(WorkspaceFixture):
    def test_writes_sealed_and_diagnostics(self):
        self.file("a.md", b"hello")
        res = self.scan()
        self.assertIn("scan_id", res)
        with gg_lock.Lock(self.ws) as cap:
            path = si.write_scan_receipt(self.ws, res, capability=cap)
        expected = (self.ws / "sources" / "intake"
                    / ("%s.json" % res["scan_id"]))
        self.assertEqual(expected, path)
        self.assertTrue(path.exists())
        diag = path.with_suffix(".diagnostics.json")
        self.assertTrue(diag.exists())
        # sealed file contains ONLY the sealed receipt, byte-canonical
        on_disk = json.loads(path.read_text())
        self.assertEqual(res["sealed"], on_disk)
        self.assertNotIn("diagnostics", on_disk)
        self.assertNotIn("absolute_root", json.dumps(on_disk))
        # diagnostics companion is the volatile object
        on_disk_diag = json.loads(diag.read_text())
        self.assertEqual(res["diagnostics"], on_disk_diag)
        self.assertEqual(str(self.ext),
                         on_disk_diag["absolute_root"])

    def test_no_replace_conflict_preserved(self):
        self.file("a.md", b"hello")
        res = self.scan()
        with gg_lock.Lock(self.ws) as cap:
            si.write_scan_receipt(self.ws, res, capability=cap)
            first = (self.ws / "sources" / "intake"
                     / ("%s.json" % res["scan_id"]))
            before = first.read_bytes()
            # identical rewrite is the primitive's idempotent
            # already_present — not a conflict, bytes unchanged
            si.write_scan_receipt(self.ws, res, capability=cap)
            self.assertEqual(before, first.read_bytes())
            # FOREIGN content at the sealed path is the real no-replace
            # conflict: it must fail and must never be overwritten
            first.write_bytes(b'{"forged":true}')
            with self.assertRaises(ValueError):
                si.write_scan_receipt(self.ws, res, capability=cap)
            self.assertEqual(b'{"forged":true}', first.read_bytes())

    def test_diagnostics_are_volatile_regenerable(self):
        self.file("a.md", b"hello")
        res = self.scan()
        with gg_lock.Lock(self.ws) as cap:
            si.write_scan_receipt(self.ws, res, capability=cap)
            diag = (self.ws / "sources" / "intake"
                    / ("%s.diagnostics.json" % res["scan_id"]))
            # the companion may be regenerated/replaced freely
            diag.write_text(json.dumps(
                dict(res["diagnostics"], host_notes="regenerated")))
            self.assertIn("regenerated", diag.read_text())
            # the sealed sibling is untouched and still parses
            sealed = (self.ws / "sources" / "intake"
                      / ("%s.json" % res["scan_id"]))
            self.assertEqual(res["sealed"],
                             json.loads(sealed.read_text()))

    def test_never_writes_03_sources_md(self):
        self.file("a.md", b"hello")
        res = self.scan()
        with gg_lock.Lock(self.ws) as cap:
            si.write_scan_receipt(self.ws, res, capability=cap)
        self.assertFalse((self.ws / "03_sources.md").exists())
        # nothing outside sources/intake was created
        created = sorted(
            str(p.relative_to(self.ws))
            for p in self.ws.rglob("*")
            if ".gg-lock" not in p.parts)
        self.assertEqual(
            ["sources",
             "sources/intake",
             "sources/intake/%s.diagnostics.json" % res["scan_id"],
             "sources/intake/%s.json" % res["scan_id"]],
            created)

    def test_never_touches_generated_views(self):
        self.file("a.md", b"hello")
        gen = self.ws / "generated"
        (gen / "views").mkdir(parents=True)
        sentinel = gen / "views" / "v.md"
        sentinel.write_text("sentinel")
        res = self.scan()
        with gg_lock.Lock(self.ws) as cap:
            si.write_scan_receipt(self.ws, res, capability=cap)
        self.assertEqual("sentinel", sentinel.read_text())


class TestCapabilityEnforcement(WorkspaceFixture):
    """The receipt capability boundary — gg_lock.assert_held is the
    gate; invalid capabilities raise LockError BEFORE any write."""

    def _result(self):
        self.file("a.md", b"hello")
        res = self.scan()
        self.assertIn("scan_id", res)
        return res

    def _intake_empty(self):
        intake = self.ws / "sources" / "intake"
        return not intake.exists() or not any(intake.iterdir())

    def test_missing_capability_fails(self):
        res = self._result()
        with self.assertRaises(Exception):
            si.write_scan_receipt(self.ws, res, capability=None)
        self.assertTrue(self._intake_empty())

    def test_forged_capability_fails(self):
        res = self._result()
        with self.assertRaises(Exception):
            si.write_scan_receipt(self.ws, res, capability=object())
        self.assertTrue(self._intake_empty())

    def test_capability_for_wrong_root_fails(self):
        res = self._result()
        with tempfile.TemporaryDirectory() as other:
            gg_lock.install_new(other)
            with gg_lock.Lock(other) as cap_other:
                with self.assertRaises(gg_lock.LockError):
                    si.write_scan_receipt(self.ws, res,
                                          capability=cap_other)
        self.assertTrue(self._intake_empty())

    def test_released_capability_fails(self):
        res = self._result()
        lock = gg_lock.Lock(self.ws)
        cap = lock.__enter__()
        lock.__exit__(None, None, None)  # released — now stale
        with self.assertRaises(gg_lock.LockError):
            si.write_scan_receipt(self.ws, res, capability=cap)
        self.assertTrue(self._intake_empty())

    def test_scan_failure_result_not_writable(self):
        """A ScanFailure has no receipt — write_scan_receipt rejects it
        before the capability check matters."""
        os.symlink("no-such", self.ext / "dangling")
        failure = self.scan()
        self.assertEqual("boundary_rejected", failure["status"])
        with gg_lock.Lock(self.ws) as cap:
            with self.assertRaises(ValueError):
                si.write_scan_receipt(self.ws, failure, capability=cap)
        self.assertTrue(self._intake_empty())


class TestScanResultIntegrity(WorkspaceFixture):
    def test_sealed_scan_id_mismatch_rejected(self):
        self.file("a.md")
        res = self.scan()
        tampered = dict(res)
        tampered["sealed"] = dict(res["sealed"], scan_id="0" * 64)
        with gg_lock.Lock(self.ws) as cap:
            with self.assertRaises(ValueError):
                si.write_scan_receipt(self.ws, tampered, capability=cap)

    def test_capability_checked_before_publication(self):
        """assert_held runs before the intake directory is created."""
        self.file("a.md")
        res = self.scan()
        calls = []
        orig = gg_lock.assert_held

        def spy(cap, root):
            calls.append(root)
            return orig(cap, root)
        with mock.patch.object(si.gg_lock, "assert_held", spy):
            with self.assertRaises(Exception):
                si.write_scan_receipt(self.ws, res, capability=None)
        self.assertEqual([self.ws], calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
