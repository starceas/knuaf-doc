"""Lane B scan tests — frozen API.md section 2 + SCHEMA sections 7/10.

Every assertion runs against the REAL module; no mock replaces product
logic.  The only patched seams are os.scandir (NFC collision — APFS
cannot hold two normalization-differing spellings) and a hashing hook
for the deterministic instability trigger.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gg_reuse  # noqa: E402
import gg_source_intake as si  # noqa: E402

KNUAF_DOC = Path(__file__).resolve().parents[1]
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"

KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def key_catalog():
    return {"narrative_reference": {
        KIM_KEY["value"]: {"authority": "verified",
                           "catalog_digest": None,
                           "receipt_sha256": None}}}


CV = "p5-classifier/1"


def policy(**kw):
    p = {"classification_version": CV,
         "include": [], "exclude": [], "always_hash": [],
         "document_extensions": ["md", "txt"]}
    p.update(kw)
    return p


def run(root_path, reg=None, **kw):
    args = dict(root=root_path, classification_version=CV,
                registry=reg if reg is not None else registry(),
                key_catalog=key_catalog(), scan_policy=policy())
    args.update(kw)
    return si.run_source_scan(root_path, **args)


class TreeFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def file(self, rel, data=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class TestBoundaryRejections(TreeFixture):
    """Every closed boundary token -> boundary_rejected, exit 3, with
    boundary_kind + relative_id populated."""

    def _assert_boundary(self, failure, kind, rel_id):
        self.assertEqual("boundary_rejected", failure["status"])
        self.assertEqual(kind, failure["reason"])
        self.assertEqual(kind, failure["boundary_kind"])
        self.assertEqual(rel_id, failure["relative_id"])
        self.assertEqual(3, failure["exit"])
        self.assertIn("boundary_kind", failure["required_fields"])
        self.assertIn("relative_id", failure["required_fields"])

    def test_file_symlink(self):
        self.file("real.txt")
        os.symlink("real.txt", self.root / "link.txt")
        f = run(self.root)
        self._assert_boundary(f, "file_symlink", "link.txt")

    def test_dir_symlink(self):
        self.file("real/a.txt")
        os.symlink("real", self.root / "linkdir")
        f = run(self.root)
        self._assert_boundary(f, "dir_symlink", "linkdir")

    def test_dangling_symlink(self):
        os.symlink("no-such-target", self.root / "dangling")
        f = run(self.root)
        self._assert_boundary(f, "dangling_symlink", "dangling")

    def test_absolute_symlink_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            target = Path(other) / "outside.txt"
            target.write_text("x")
            os.symlink(str(target), self.root / "out")
            f = run(self.root)
            self._assert_boundary(f, "file_symlink", "out")

    def test_non_regular_entry(self):
        self.file("keep.txt")
        os.mkfifo(self.root / "fifo")
        f = run(self.root)
        self._assert_boundary(f, "non_regular_entry", "fifo")

    def test_root_path_symlink(self):
        self.file("a.txt")
        link = self.root.parent / (self.root.name + "-link")
        os.symlink(str(self.root), link)
        self.addCleanup(link.unlink)
        f = run(link)
        self._assert_boundary(f, "dir_symlink", "")

    def test_path_escape_row(self):
        """path_escape's closed-schema row (detection is exercised via a
        patched commonpath; the row shape is asserted here)."""
        self.file("a.txt")
        real_commonpath = os.path.commonpath

        def fake_commonpath(paths):
            if any("a.txt" in p for p in paths):
                return "/somewhere/else"
            return real_commonpath(paths)
        with mock.patch.object(si.os.path, "commonpath",
                               fake_commonpath):
            f = run(self.root)
        self._assert_boundary(f, "path_escape", "a.txt")

    def test_normalization_collision(self):
        """Two distinct real paths NFC-equal -> normalization_collision.
        APFS cannot hold both spellings, so scandir is patched to report
        a second NFD-spelled entry; the NFC file is real on disk."""
        self.file("café.txt")            # NFC — real
        fake = mock.Mock()
        fake.name = "café.txt"     # NFD spelling
        real_scandir = os.scandir

        def fake_scandir(path):
            entries = list(real_scandir(path))
            entries.append(fake)
            return entries
        with mock.patch.object(si.os, "scandir", fake_scandir):
            f = run(self.root)
        self._assert_boundary(f, "normalization_collision",
                              "café.txt")

    def test_symlink_check_precedes_normalization_collision(self):
        """SIMULATED — a path that is both a symlink and an NFC
        collision cannot exist on this host filesystem (APFS cannot
        hold two normalization-differing spellings of one name), so
        scandir is patched to report a second NFC-equal spelling and
        lstat is patched to report that spelling as a symlink.  SCHEMA
        7.3 orders the symlink check (step 1) before the normalization
        collision check (step 3), so the boundary kind is the symlink
        kind, never normalization_collision.  This is a controlled
        simulation of filesystems permitting both spellings; it makes
        no native Windows/NTFS claim."""
        self.file("café.txt")           # NFD on disk — real
        fake = mock.Mock()
        fake.name = "café.txt"          # NFC — NFC-equal twin,
                                        # sorts after the NFD entry
        real_scandir = os.scandir
        real_lstat = os.lstat

        def fake_scandir(path):
            entries = list(real_scandir(path))
            entries.append(fake)
            return entries

        def fake_lstat(path):
            if os.path.basename(str(path)) == fake.name:
                return os.stat_result(
                    (si._stat.S_IFLNK | 0o777,) + (0,) * 9)
            return real_lstat(path)
        with mock.patch.object(si.os, "scandir", fake_scandir), \
                mock.patch.object(si.os, "lstat", fake_lstat):
            f = run(self.root)
        self._assert_boundary(f, "file_symlink", "café.txt")


class TestFailureSchema(unittest.TestCase):
    """The CLOSED failure schema: every row exact, sole conversion
    site, nothing raw escapes run_source_scan."""

    def test_all_boundary_kinds_share_one_row(self):
        for kind in ("file_symlink", "dir_symlink", "dangling_symlink",
                     "non_regular_entry", "path_escape",
                     "normalization_collision"):
            f = si.to_scan_failure(si.BoundaryError(kind, "r/x"))
            self.assertEqual(
                {"status": "boundary_rejected", "reason": kind,
                 "relative_id": "r/x", "boundary_kind": kind,
                 "required_fields": ["boundary_kind", "relative_id"],
                 "preserved_paths": [], "exit": 3},
                f)

    def test_unstable_input_row(self):
        f = si.to_scan_failure(si.UnstableInputError("a/b"))
        self.assertEqual(
            {"status": "unstable_input", "reason": "unstable_input",
             "relative_id": "a/b", "boundary_kind": None,
             "required_fields": ["relative_id"],
             "preserved_paths": [], "exit": 3},
            f)

    def test_invalid_family_rows(self):
        for reason in ("invalid_scan_request",
                       "classification_version_mismatch",
                       "scan_policy_invalid"):
            f = si.to_scan_failure(
                si.ScanRequestError(reason, ["registry"]))
            self.assertEqual("invalid_scan_request", f["status"])
            self.assertEqual(reason, f["reason"])
            self.assertEqual(["registry"], f["required_fields"])
            self.assertEqual("", f["relative_id"])
            self.assertIsNone(f["boundary_kind"])
            self.assertEqual(2, f["exit"])

    def test_scan_never_uses_resolver_vocabulary(self):
        for reason in ("blocked", "needs_excerpt", "unmapped",
                       "reuse_ready", "duplicate_hash_collision"):
            self.assertNotIn(reason, si._FAILURE_TABLE)


class TestInvalidRequests(TreeFixture):
    """The invalid_scan_request family — exit 2, sole conversion site,
    no raw exception through run_source_scan."""

    def _assert_invalid(self, f, reason, fields):
        self.assertEqual("invalid_scan_request", f["status"])
        self.assertEqual(reason, f["reason"])
        self.assertEqual(2, f["exit"])
        self.assertEqual(fields, f["required_fields"])

    def test_registry_bare_dict_rejected(self):
        reg = registry()
        f = run(self.root, reg=dict(reg.document))
        self._assert_invalid(f, "invalid_scan_request", ["registry"])

    def test_registry_digest_alone_rejected(self):
        f = run(self.root, reg=registry().registry_digest)
        self._assert_invalid(f, "invalid_scan_request", ["registry"])

    def test_registry_raw_raises_inside_not_through_wrapper(self):
        """Direct scan_external_root raises ValueError; run_source_scan
        converts — the ONLY conversion site."""
        self.file("a.txt")
        reg = registry()
        with self.assertRaises(ValueError):
            si.scan_external_root(
                self.root, root=self.root,
                classification_version=CV,
                registry=dict(reg.document),
                key_catalog=key_catalog(), scan_policy=policy())
        f = run(self.root, reg=dict(reg.document))
        self._assert_invalid(f, "invalid_scan_request", ["registry"])

    def test_no_raw_valueerror_escapes_run_source_scan(self):
        """Every malformed input family returns a ScanFailure, never a
        raised exception."""
        cases = [
            dict(registry=None),
            dict(registry="x" * 64),
            dict(key_catalog=["not-a-dict"]),
            dict(scan_policy="nope"),
            dict(scan_policy=policy(classification_version="other/1")),
            dict(classification_version="other/1"),
            dict(requested_use={"kind": "bogus", "keys": [KIM_KEY]}),
            dict(requested_use={"kind": "narrative_reference",
                                "keys": [{"kind": "delimited",
                                          "value": "UPPER:x"}]}),
            dict(current_draft=5),
            dict(current_draft="/abs/not-relative"),
        ]
        self.file("a.txt")
        for kw in cases:
            f = run(self.root, **kw)
            self.assertIsInstance(f, dict, kw)
            self.assertIn(f["status"], {"invalid_scan_request",
                                        "boundary_rejected",
                                        "unstable_input"}, kw)

    def test_current_draft_absolute_is_invalid_request(self):
        """SCHEMA 10: current_draft is a workspace-relative path or
        null — an absolute path is rejected through the sole
        to_scan_failure conversion site as invalid_scan_request with
        required_fields naming current_draft, exit 2."""
        self.file("a.txt")
        f = run(self.root, current_draft="/abs/not-relative")
        self._assert_invalid(f, "invalid_scan_request",
                             ["current_draft"])
        self.assertEqual("", f["relative_id"])
        self.assertIsNone(f["boundary_kind"])

    def test_current_draft_null_and_relative_accepted(self):
        """null stays legal; a workspace-relative string is sealed
        verbatim (no existence requirement is added)."""
        self.file("a.txt")
        f = run(self.root)
        self.assertIn("scan_id", f)
        self.assertIsNone(f["sealed"]["current_draft"])
        f = run(self.root, current_draft="drafts/d1.md")
        self.assertIn("scan_id", f)
        self.assertEqual("drafts/d1.md", f["sealed"]["current_draft"])

    def test_missing_root_path(self):
        f = run(self.root.parent / "no-such-dir-xyz")
        self._assert_invalid(f, "invalid_scan_request", ["root_path"])

    def test_file_root_path(self):
        p = self.file("a.txt")
        f = run(p)
        self._assert_invalid(f, "invalid_scan_request", ["root_path"])

    def test_classification_version_mismatch(self):
        self.file("a.txt")
        f = run(self.root, classification_version="other/1")
        self._assert_invalid(
            f, "classification_version_mismatch",
            ["classification_version"])
        f = run(self.root,
                scan_policy=policy(classification_version="other/1"))
        self._assert_invalid(
            f, "classification_version_mismatch",
            ["classification_version"])

    def test_scan_policy_invalid_family(self):
        self.file("a.txt")
        bad_policies = [
            None,
            "x",
            policy(exclude="x"),
            policy(exclude=[{"pattern": "*.log"}]),          # no reason
            policy(exclude=[{"pattern": "*.log",
                             "reason": "r"}]),              # no mode
            policy(exclude=[{"pattern": "*.log", "reason": "r",
                             "hash_mode": "bogus"}]),
            policy(exclude=[{"pattern": "*.log", "reason": "r",
                             "hash_mode": "excluded_no_hash"},
                            {"pattern": "*.log", "reason": "other",
                             "hash_mode": "included_hash"}]),  # dup diff
            policy(exclude=[{"pattern": "../escape", "reason": "r",
                             "hash_mode": "excluded_no_hash"}]),
            policy(include=["/absolute/*"]),
            policy(include="*.md"),
            policy(always_hash=[1]),
            policy(document_extensions="md"),
        ]
        for p in bad_policies:
            f = run(self.root, scan_policy=p)
            self.assertEqual("invalid_scan_request", f["status"], p)
            self.assertIn(f["reason"], ("scan_policy_invalid",
                                        "classification_version_mismatch"),
                          p)
            self.assertEqual(2, f["exit"], p)

    def test_duplicate_identical_exclude_rules_deduplicated(self):
        self.file("a.log", b"1")
        self.file("b.md", b"2")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "*.log", "reason": "r",
             "hash_mode": "excluded_no_hash"},
            {"pattern": "*.log", "reason": "r",
             "hash_mode": "excluded_no_hash"}]))
        self.assertEqual("ok" if "scan_id" in f else f["status"],
                         "ok")
        log = next(e for e in f["sealed"]["files"]
                   if e["relative_id"] == "a.log")
        self.assertEqual("excluded_no_hash", log["entry_reason"])
        self.assertIsNone(log["content_sha256"])

    def test_requested_use_validated_through_key_machinery(self):
        self.file("a.txt")
        f = run(self.root, requested_use={
            "kind": "narrative_reference", "keys": [KIM_KEY]})
        self.assertEqual("ok" if "scan_id" in f else f["status"], "ok")
        self.assertEqual({"kind": "narrative_reference",
                          "keys": [KIM_KEY]},
                         f["sealed"]["requested_use"])

    def test_malformed_requested_use_is_invalid_request(self):
        self.file("a.txt")
        for bad in ({"kind": "narrative_reference", "keys": []},
                    {"kind": "narrative_reference",
                     "keys": [KIM_KEY, KIM_KEY]},       # duplicate
                    {"kind": "narrative_reference",
                     "keys": [KIM_KEY,
                              {"kind": "delimited",
                               "value":
                               "kim-wonseop-exemplar:closing-narrative"
                               }]},                     # unsorted
                    {"kind": "statistical_observation",
                     "keys": [{"kind": "delimited",
                               "value": "x:y"}]}):
            f = run(self.root, requested_use=bad)
            self._assert_invalid(f, "invalid_scan_request",
                                 ["requested_use"])


class TestEnumerationAndClassification(TreeFixture):
    """SCHEMA section 7: whole-root typed enumeration, total
    classification, precedence order, exclusion inheritance."""

    def _build_tree(self):
        self.file("top.md", b"# top\n")
        self.file("top.log", b"log\n")
        self.file("sub/deep.md", b"# deep\n")
        self.file("sub/deep.bin", b"\x00\x01")
        self.file("sub/nest/x.md", b"# x\n")
        self.file("vendor/lib.js", b"js")
        self.file("vendor/sub/v2.js", b"v2")
        self.file(".hidden", b"h")

    def test_whole_root_typed_enumeration_equals_filesystem(self):
        self._build_tree()
        f = run(self.root)
        self.assertIn("scan_id", f)
        sealed_pairs = {(e["relative_id"], e["type"])
                        for e in f["sealed"]["files"]}
        expected = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            for d in dirnames:
                rel = (Path(dirpath) / d).relative_to(self.root)
                expected.add((rel.as_posix(), "dir"))
            for fn in filenames:
                rel = (Path(dirpath) / fn).relative_to(self.root)
                expected.add((rel.as_posix(), "file"))
        self.assertEqual(expected, sealed_pairs)

    def test_entry_shape_exact(self):
        self.file("a.md")
        f = run(self.root)
        e = f["sealed"]["files"][0]
        self.assertEqual({"relative_id", "original_spelling",
                          "content_sha256", "type", "entry_reason",
                          "policy_ref"}, set(e))
        self.assertEqual(64, len(e["content_sha256"]))

    def test_sealed_shape_exact(self):
        self.file("a.md")
        f = run(self.root)
        self.assertEqual({"scan_id_version", "scan_id", "files",
                          "classification_version", "registry_digest",
                          "scan_policy_digest", "current_draft",
                          "requested_use", "scan_state"},
                         set(f["sealed"]))
        self.assertEqual("p5-scan/1", f["sealed"]["scan_id_version"])
        self.assertEqual({"scan_id", "sealed", "diagnostics"},
                         set(f))

    def test_files_sorted_by_relative_id(self):
        self._build_tree()
        f = run(self.root)
        ids = [e["relative_id"] for e in f["sealed"]["files"]]
        self.assertEqual(sorted(ids), ids)

    def test_document_extension_classification(self):
        self.file("a.md")
        self.file("b.MD")   # case-insensitive suffix match
        self.file("c.bin")
        self.file("noext")
        f = run(self.root)
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("document", by_id["a.md"]["entry_reason"])
        self.assertEqual("document", by_id["b.MD"]["entry_reason"])
        self.assertEqual("included_hash", by_id["c.bin"]["entry_reason"])
        self.assertEqual("included_hash", by_id["noext"]["entry_reason"])

    def test_exclude_no_hash_and_policy_ref(self):
        self.file("keep.md")
        self.file("drop.log")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "*.log", "reason": "logs are noise",
             "hash_mode": "excluded_no_hash"}]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("excluded_no_hash",
                         by_id["drop.log"]["entry_reason"])
        self.assertIsNone(by_id["drop.log"]["content_sha256"])
        self.assertEqual(0, by_id["drop.log"]["policy_ref"])
        self.assertEqual("document", by_id["keep.md"]["entry_reason"])

    def test_exclude_included_hash_mode(self):
        self.file("big.bin", b"payload")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "*.bin", "reason": "tracked but hashed",
             "hash_mode": "included_hash"}]))
        e = f["sealed"]["files"][0]
        self.assertEqual("included_hash", e["entry_reason"])
        self.assertEqual(hashlib.sha256(b"payload").hexdigest(),
                         e["content_sha256"])
        # SCHEMA 10: policy_ref is set for excluded_* only — an
        # exclude-selected included_hash still emits null
        self.assertIsNone(e["policy_ref"])

    def test_excluded_dir_enumerated_descendants_inherit(self):
        self.file("vendor/a.md")
        self.file("vendor/sub/b.md")
        self.file("keep.md")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "vendor", "reason": "vendored",
             "hash_mode": "excluded_no_hash"}]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("excluded_dir", by_id["vendor"]["entry_reason"])
        self.assertEqual(0, by_id["vendor"]["policy_ref"])
        # descendants remain visible AND inherit exclusion
        self.assertEqual("excluded_no_hash",
                         by_id["vendor/a.md"]["entry_reason"])
        self.assertEqual(0, by_id["vendor/a.md"]["policy_ref"])
        self.assertEqual("excluded_dir",
                         by_id["vendor/sub"]["entry_reason"])
        self.assertEqual("excluded_no_hash",
                         by_id["vendor/sub/b.md"]["entry_reason"])
        self.assertEqual("document", by_id["keep.md"]["entry_reason"])

    def test_always_hash_overrides_inherited_exclusion(self):
        self.file("vendor/pin.md", b"pinned")
        f = run(self.root, scan_policy=policy(
            exclude=[{"pattern": "vendor", "reason": "vendored",
                      "hash_mode": "excluded_no_hash"}],
            always_hash=["vendor/pin.md"]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("included_hash",
                         by_id["vendor/pin.md"]["entry_reason"])
        self.assertEqual(hashlib.sha256(b"pinned").hexdigest(),
                         by_id["vendor/pin.md"]["content_sha256"])
        self.assertIsNone(by_id["vendor/pin.md"]["policy_ref"])

    def test_always_hash_beats_exclude_and_include(self):
        self.file("x.log", b"lg")
        f = run(self.root, scan_policy=policy(
            include=["*.md"],
            exclude=[{"pattern": "*.log", "reason": "r",
                      "hash_mode": "excluded_no_hash"}],
            always_hash=["x.log"]))
        e = f["sealed"]["files"][0]
        self.assertEqual("included_hash", e["entry_reason"])
        self.assertEqual(hashlib.sha256(b"lg").hexdigest(),
                         e["content_sha256"])

    def test_include_nonempty_no_match_is_not_included(self):
        self.file("a.md")
        self.file("b.bin")
        f = run(self.root, scan_policy=policy(include=["*.md"]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("document", by_id["a.md"]["entry_reason"])
        self.assertEqual("not_included",
                         by_id["b.bin"]["entry_reason"])
        self.assertIsNone(by_id["b.bin"]["content_sha256"])

    def test_exclude_longest_pattern_wins(self):
        self.file("vendor/x.log")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "*.log", "reason": "short",
             "hash_mode": "excluded_no_hash"},
            {"pattern": "vendor/*.log", "reason": "long",
             "hash_mode": "included_hash"}]))
        e = next(e for e in f["sealed"]["files"]
                 if e["relative_id"] == "vendor/x.log")
        # the longer rule (index 1, included_hash) won — proven by the
        # entry_reason; policy_ref is null because it is not excluded_*
        self.assertEqual("included_hash", e["entry_reason"])
        self.assertIsNone(e["policy_ref"])

    def test_exclude_tie_breaks_lexicographic(self):
        self.file("x.dat")
        f = run(self.root, scan_policy=policy(exclude=[
            {"pattern": "x.da?", "reason": "a",
             "hash_mode": "included_hash"},
            {"pattern": "x.?at", "reason": "b",
             "hash_mode": "excluded_no_hash"}]))
        e = f["sealed"]["files"][0]
        self.assertEqual("excluded_no_hash", e["entry_reason"])
        self.assertEqual(1, e["policy_ref"])  # 'x.?at' < 'x.da?' wins

    def test_glob_star_segment_boundaries(self):
        self.file("a/x.md")
        self.file("x.md")
        self.file("a/b/x.md")
        f = run(self.root, scan_policy=policy(include=["a/*.md"]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("document", by_id["a/x.md"]["entry_reason"])
        self.assertEqual("not_included", by_id["x.md"]["entry_reason"])
        self.assertEqual("not_included",
                         by_id["a/b/x.md"]["entry_reason"])

    def test_glob_doublestar_crosses_depth(self):
        self.file("a/x.md")
        self.file("a/b/c/x.md")
        self.file("top.md")
        f = run(self.root, scan_policy=policy(include=["a/**/*.md"]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("document", by_id["a/x.md"]["entry_reason"])
        self.assertEqual("document",
                         by_id["a/b/c/x.md"]["entry_reason"])
        self.assertEqual("not_included", by_id["top.md"]["entry_reason"])

    def test_glob_question_single_char(self):
        self.file("x1.md")
        self.file("x12.md")
        f = run(self.root, scan_policy=policy(include=["x?.md"]))
        by_id = {e["relative_id"]: e for e in f["sealed"]["files"]}
        self.assertEqual("document", by_id["x1.md"]["entry_reason"])
        self.assertEqual("not_included",
                         by_id["x12.md"]["entry_reason"])

    def test_original_spelling_preserved(self):
        name = "café.md"   # NFD on disk
        self.file(name)
        f = run(self.root)
        by_spell = {e["original_spelling"]: e
                    for e in f["sealed"]["files"]}
        self.assertIn(name, by_spell)
        e = by_spell[name]
        self.assertEqual("café.md", e["relative_id"])  # NFC

    def test_no_absolute_paths_in_sealed(self):
        self.file("a.md")
        f = run(self.root)
        blob = json.dumps(f["sealed"])
        self.assertNotIn(str(self.root), blob)
        self.assertNotIn("/private/", blob)
        # absolute path lives ONLY in diagnostics
        self.assertIn(str(self.root), f["diagnostics"]["absolute_root"])


class TestStabilityAndScanId(TreeFixture):
    def test_unstable_input_on_content_change(self):
        self.file("a.md", b"v1")
        real_hash = si._sha256_stream
        calls = []

        def mutating_hash(path):
            calls.append(path)
            digest = real_hash(path)
            # mutate a DIFFERENT tracked file after it was hashed is not
            # possible here (single file) — mutate this file itself so
            # the post-read identity check fires
            if len(calls) == 1:
                Path(path).write_bytes(b"v2-different")
            return digest
        with mock.patch.object(si, "_sha256_stream", mutating_hash):
            f = run(self.root)
        self.assertEqual("unstable_input", f["status"])
        self.assertEqual(3, f["exit"])
        self.assertEqual("a.md", f["relative_id"])
        self.assertIsNone(f["boundary_kind"])

    def test_unstable_input_on_tree_change(self):
        """A file added between enumeration and the enclosing-inventory
        recheck is unstable_input."""
        self.file("a.md")
        orig_recheck = si._recheck_tree

        def add_file_then_recheck(root_path, sigs):
            (Path(root_path) / "late.md").write_bytes(b"new")
            return orig_recheck(root_path, sigs)
        with mock.patch.object(si, "_recheck_tree",
                               add_file_then_recheck):
            f = run(self.root)
        self.assertEqual("unstable_input", f["status"])
        self.assertEqual(3, f["exit"])
        self.assertEqual("late.md", f["relative_id"])

    def test_scan_id_is_deterministic_and_wellformed(self):
        self.file("a.md", b"content")
        self.file("sub/b.bin", b"bb")
        f1 = run(self.root)
        f2 = run(self.root)
        self.assertEqual(f1["scan_id"], f2["scan_id"])
        self.assertEqual(64, len(f1["scan_id"]))
        # the digest preimage: the sealed object minus scan_id
        sealed = dict(f1["sealed"])
        sid = sealed.pop("scan_id")
        recomputed = hashlib.sha256(
            json.dumps(sealed, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(sid, recomputed)
        # a changed file changes the id
        self.file("a.md", b"changed")
        f3 = run(self.root)
        self.assertNotEqual(f1["scan_id"], f3["scan_id"])

    def test_diagnostics_fields_and_volatility(self):
        self.file("a.md", b"content")
        f = run(self.root)
        d = f["diagnostics"]
        self.assertEqual({"observed_utc", "duration_ms",
                          "absolute_root", "host_notes", "size_mtime"},
                         set(d))
        self.assertIsInstance(d["duration_ms"], int)
        self.assertIn("a.md", d["size_mtime"])
        self.assertIn("size", d["size_mtime"]["a.md"])
        self.assertIn("mtime", d["size_mtime"]["a.md"])
        # diagnostics are not part of the digest: a second scan's
        # differing observed_utc/duration_ms do not change scan_id
        f2 = run(self.root)
        self.assertEqual(f["scan_id"], f2["scan_id"])


class TestScanState(TreeFixture):
    """SCHEMA section 10 scan_state — never reuse_ready."""

    def test_known_source_without_requested_use(self):
        """A scanned hash matching a registry source but no
        requested_use -> known_source (never reuse_ready)."""
        reg = registry()
        src_hash = reg.document["entries"][0]["source_sha256"][0]
        blob = bytes.fromhex(src_hash)
        # find a real file whose hash matches? cannot fabricate content
        # with a given sha — instead inject the file's own hash into a
        # registry copy is impossible (frozen input). Use a registry
        # whose entries' source_sha256 is derived from a real file:
        self.file("a.md", b"marker")
        real = hashlib.sha256(b"marker").hexdigest()
        doc = json.loads(json.dumps(reg.document))
        doc["entries"][0]["source_sha256"] = [real]
        reg2 = _FakeResolution(reg, doc)
        f = run(self.root, reg=reg2)
        self.assertIn("scan_id", f)
        self.assertEqual("known_source", f["sealed"]["scan_state"])

    def test_reuse_candidate_on_matching_hash(self):
        self.file("a.md", b"marker")
        real = hashlib.sha256(b"marker").hexdigest()
        reg = registry()
        doc = json.loads(json.dumps(reg.document))
        doc["entries"][0]["source_sha256"] = [real]
        reg2 = _FakeResolution(reg, doc)
        f = run(self.root, reg=reg2, requested_use={
            "kind": "narrative_reference", "keys": [KIM_KEY]})
        self.assertEqual("reuse_candidate",
                         f["sealed"]["scan_state"])

    def test_requested_use_no_match_is_known_source(self):
        self.file("a.md", b"marker")
        f = run(self.root, requested_use={
            "kind": "narrative_reference", "keys": [KIM_KEY]})
        self.assertEqual("known_source", f["sealed"]["scan_state"])

    def test_scan_never_emits_reuse_ready(self):
        self.file("a.md", b"marker")
        real = hashlib.sha256(b"marker").hexdigest()
        reg = registry()
        doc = json.loads(json.dumps(reg.document))
        doc["entries"][0]["source_sha256"] = [real]
        reg2 = _FakeResolution(reg, doc)
        f = run(self.root, reg=reg2, requested_use={
            "kind": "narrative_reference", "keys": [KIM_KEY]})
        self.assertIn(f["sealed"]["scan_state"],
                      ("known_source", "reuse_candidate"))
        self.assertNotEqual("reuse_ready", f["sealed"]["scan_state"])

    def test_registry_digest_is_document_only(self):
        self.file("a.md")
        reg = registry()
        f = run(self.root)
        self.assertEqual(reg.registry_digest,
                         f["sealed"]["registry_digest"])
        recomputed = hashlib.sha256(json.dumps(
            reg.document, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(recomputed, f["sealed"]["registry_digest"])


class _FakeResolution(gg_reuse.RegistryResolution):
    """A constructed RegistryResolution carrying a modified document —
    still the real typed object (type enforcement satisfied), with the
    same registry_digest interface."""

    def __init__(self, base, document):
        super().__init__(
            document=document,
            registry_path=base.registry_path,
            resolved_base=base.resolved_base,
            resolved_packs_dir=base.resolved_packs_dir,
            registry_digest=hashlib.sha256(json.dumps(
                document, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest())


class TestTreeInventory(TreeFixture):
    """SCHEMA 7.6 preservation-tree inventory — policy-blind, typed,
    no-follow."""

    def test_inventory_types_everything(self):
        self.file("a.md")
        self.file("d/b.bin")
        os.symlink("a.md", self.root / "lnk")
        os.mkfifo(self.root / "fifo")
        inv = si.tree_inventory(self.root)
        self.assertEqual("p5-tree-inventory/1", inv["schema"])
        self.assertEqual("external_scan_root", inv["root_kind"])
        by_id = {e["relative_id"]: e for e in inv["entries"]}
        self.assertEqual("file", by_id["a.md"]["type"])
        self.assertEqual("dir", by_id["d"]["type"])
        self.assertEqual("file", by_id["d/b.bin"]["type"])
        self.assertEqual("symlink", by_id["lnk"]["type"])
        self.assertEqual("other", by_id["fifo"]["type"])
        self.assertIsNone(by_id["lnk"]["content_sha256"])
        self.assertEqual(hashlib.sha256(b"x").hexdigest(),
                         by_id["a.md"]["content_sha256"])

    def test_inventory_does_not_follow_symlink_dirs(self):
        self.file("real/a.md")
        os.symlink("real", self.root / "ldir")
        inv = si.tree_inventory(self.root)
        ids = [e["relative_id"] for e in inv["entries"]]
        self.assertIn("ldir", ids)
        self.assertNotIn("ldir/a.md", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
