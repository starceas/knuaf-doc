"""Isolated unit tests for gg_rda_provenance — audit-key stability, raw-line
binary hashing, physical line numbering, duplicate detection, integrity
gate, reference resolution, snapshot registration.

Self-contained: runnable directly (``python3 -B tests/test_gg_rda_provenance.py``
from ``skills/knuaf-doc/``) or via unittest discovery; no external harness.
Binds SPEC.md §2–§5 / ACCEPTANCE.md D01, D03, D04.
"""
import dataclasses
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

KN_UAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KN_UAF_DOC / "scripts"
PACKS = KN_UAF_DOC / "references" / "benchmark-packs"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_provenance as prov  # noqa: E402

NATIONAL = PACKS / "common/rda-income-national-2024/records.jsonl"
NATIONAL_SHA = prov.PINNED_PACKS["rda.income.national.2024"][
    "records_file_sha256"]


def _write(tmpdir, name, raw_lines):
    p = Path(tmpdir) / name
    p.write_bytes(b"".join(raw_lines))
    return p


def _verified_pack(testcase, records):
    """Write a synthetic records.jsonl, pin it under ``test.pack``, build
    a real verified index over its bytes; returns (index, first_key)."""
    td = tempfile.TemporaryDirectory(prefix="gg-p4-prov-")
    testcase.addCleanup(td.cleanup)
    p = Path(td.name) / "records.jsonl"
    p.write_bytes(b"".join(json.dumps(r, ensure_ascii=False)
                           .encode("utf-8") + b"\n" for r in records))
    file_sha = hashlib.sha256(p.read_bytes()).hexdigest()
    prov.PINNED_PACKS["test.pack"] = {
        "pack_revision": "t1", "records_file_sha256": file_sha,
        "record_lines": len(records), "relpath": "synthetic"}
    testcase.addCleanup(lambda: prov.PINNED_PACKS.pop("test.pack", None))
    idx = prov.build_audit_index(p, "test.pack", file_sha)
    return idx, next(iter(idx))


class AuditKeyAndHashingTests(unittest.TestCase):
    """SPEC §2: audit key tuple + exact binary raw-line hashing (binds D04)."""

    def test_raw_line_sha256_covers_exact_bytes_lf(self):
        raw = b'{"a": 1}\n'
        self.assertEqual(prov.raw_line_sha256(raw),
                         hashlib.sha256(raw).hexdigest())

    def test_raw_line_sha256_distinguishes_crlf_from_lf(self):
        lf = hashlib.sha256(b'{"a": 1}\n').hexdigest()
        crlf = hashlib.sha256(b'{"a": 1}\r\n').hexdigest()
        self.assertNotEqual(lf, crlf)
        self.assertEqual(prov.raw_line_sha256(b'{"a": 1}\r\n'), crlf)

    def test_no_terminator_added_to_final_line(self):
        raw = b'{"a": 1}'  # no newline at all
        self.assertEqual(prov.raw_line_sha256(raw),
                         hashlib.sha256(raw).hexdigest())

    def test_no_whitespace_normalization(self):
        a = prov.raw_line_sha256(b'{"a":1}\n')
        b = prov.raw_line_sha256(b'{"a": 1}\n')
        self.assertNotEqual(a, b)

    def test_audit_key_tuple_and_dict_forms(self):
        tup = ("p.x", "f" * 64, 3, "a" * 64)
        d = prov.audit_key_dict(tup)
        self.assertEqual(d["pack_id"], "p.x")
        self.assertEqual(d["records_file_sha256"], "f" * 64)
        self.assertEqual(d["physical_jsonl_line_1based"], 3)
        self.assertEqual(d["raw_line_sha256"], "a" * 64)
        self.assertEqual(prov.normalize_audit_key(d), tup)
        self.assertEqual(prov.normalize_audit_key(list(tup)), tup)
        with self.assertRaises(ValueError):
            prov.normalize_audit_key(("p.x", "f" * 64))
        with self.assertRaises(ValueError):
            prov.normalize_audit_key({"pack_id": "p.x"})


class LineNumberingTests(unittest.TestCase):
    """physical_jsonl_line_1based counts binary splitlines, blanks included."""

    def test_blank_lines_consume_physical_line_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [
                b'{"n": 1}\n', b'\n', b'{"n": 3}\n'])
            real = prov.sha256_file(p)
            idx = prov.build_audit_index(p, "test.pack", real)
        lines = sorted(k[2] for k in idx)
        self.assertEqual(lines, [1, 2, 3])
        self.assertIsNone(idx[("test.pack", real, 2,
                              prov.raw_line_sha256(b"\n"))].record)

    def test_line_numbers_are_1based_and_sequential_on_real_pack(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        self.assertEqual(sorted(k[2] for k in idx), list(range(1, 103)))

    def test_audit_key_stability_across_rebuilds(self):
        a = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   NATIONAL_SHA)
        b = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   NATIONAL_SHA)
        self.assertEqual(set(a), set(b))


class DuplicateDetectionTests(unittest.TestCase):
    """D01: raw-list duplicate audit keys raise BEFORE dict construction;
    duplicate SOURCE LINES (different physical lines) are legal."""

    def test_identical_source_lines_at_distinct_lines_are_legal(self):
        line = b'{"record_id": "x.dup", "kind": "k"}\n'
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [line, line])
            real = prov.sha256_file(p)
            idx = prov.build_audit_index(p, "test.pack", real)
        self.assertEqual(len(idx), 2)  # distinct physical identities kept

    def test_duplicate_audit_key_in_observation_list_raises(self):
        key = ("p.x", "f" * 64, 7, "b" * 64)
        with self.assertRaises(prov.DuplicateAuditKeyError):
            prov.check_unique_audit_keys([key, key])
        with self.assertRaises(prov.DuplicateAuditKeyError):
            prov.check_unique_audit_keys(
                [key, prov.audit_key_dict(key)])

    def test_unique_observation_list_passes(self):
        keys = [("p.x", "f" * 64, i, "b" * 64) for i in (1, 2, 3)]
        self.assertEqual(prov.check_unique_audit_keys(keys), keys)


class IntegrityGateTests(unittest.TestCase):
    """SPEC §5 / D04: pinned-hash verification is a hard failure."""

    def test_correct_pin_passes(self):
        prov.verify_pack_integrity("rda.income.national.2024", "2024",
                                   NATIONAL_SHA)

    def test_hash_mismatch_raises(self):
        with self.assertRaises(prov.IntegrityError):
            prov.verify_pack_integrity("rda.income.national.2024", "2024",
                                       "0" * 64)

    def test_revision_mismatch_raises(self):
        with self.assertRaises(prov.IntegrityError):
            prov.verify_pack_integrity("rda.income.national.2024", "9999",
                                       NATIONAL_SHA)

    def test_unpinned_pack_raises(self):
        with self.assertRaises(prov.UnpinnedPackError):
            prov.verify_pack_integrity("no.such.pack", "1", "0" * 64)

    def test_build_index_refuses_wrong_declared_hash(self):
        with self.assertRaises(prov.IntegrityError):
            prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   "0" * 64)


class RealPackIdentityTests(unittest.TestCase):
    """D01: all 6,055 physical rows preserved with immutable audit keys."""

    def test_all_four_packs_index_to_6162_physical_keys(self):
        total = 0
        for pack_id, pin in prov.PINNED_PACKS.items():
            idx = prov.build_audit_index(PACKS / pin["relpath"], pack_id,
                                         pin["records_file_sha256"])
            self.assertEqual(len(idx), pin["record_lines"])
            total += len(idx)
        self.assertEqual(total, 6162)

    def test_econ_byte_identical_lines_keep_distinct_keys(self):
        pin = prov.PINNED_PACKS["rda.econ.2025"]
        idx = prov.build_audit_index(PACKS / pin["relpath"],
                                     "rda.econ.2025",
                                     pin["records_file_sha256"])
        by_raw = {}
        for key in idx:
            by_raw.setdefault(key[3], []).append(key)
        dup_groups = [v for v in by_raw.values() if len(v) > 1]
        # 7 byte-identical re-extraction groups exist and stay distinct
        # physical identities — never dropped (D01 check 1)
        self.assertEqual(len(dup_groups), 7)
        for group in dup_groups:
            self.assertEqual(len({k[2] for k in group}), len(group))


class ResolveReferenceTests(unittest.TestCase):
    """resolve_reference: requires a verified index context (P4-03), checks
    physical membership in it, registered -> observation; unregistered ->
    None; stale pin -> IntegrityError; never promotes (SPEC §3/§6)."""

    def _key(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        return next(iter(idx)), idx

    def test_requires_verified_index_context(self):
        """D09/P4-03: no verified context -> fails closed, not open."""
        key, idx = self._key()
        with self.assertRaises(TypeError):  # required kwarg — fail closed
            prov.resolve_reference(key, catalog={})
        with self.assertRaises(prov.ResolutionContextError):
            prov.resolve_reference(key, catalog={}, verified_index=None)
        with self.assertRaises(prov.ResolutionContextError):
            prov.resolve_reference(key, catalog=None, verified_index=idx)

    def test_key_not_in_verified_index_returns_none(self):
        """A key that is not a member of the verified index does not
        resolve — declared hashes alone are never accepted (P4-03)."""
        key, idx = self._key()
        other = (key[0], key[1], key[2] + 1, "0" * 64)
        self.assertIsNone(prov.resolve_reference(
            other, catalog={other: {"catalog_status": "exploratory"}},
            verified_index=idx))

    def test_unregistered_returns_none(self):
        key, idx = self._key()
        self.assertIsNone(prov.resolve_reference(key, catalog={},
                                                 verified_index=idx))

    def test_registered_resolves_with_declared_status(self):
        key, idx = self._key()
        row = idx[key]
        catalog = {key: {"catalog_status": "exploratory",
                         "record_id": row.record["record_id"],
                         "record": row.record, "locator": row.record.get(
                             "locator")}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.catalog_status,
                         prov.CatalogStatus.EXPLORATORY)
        self.assertEqual(obs.audit_key, key)
        self.assertEqual(obs.record["record_id"], row.record["record_id"])

    def test_resolved_record_comes_from_verified_bytes(self):
        """The resolved payload is the verified index row — a catalog entry
        cannot inject different record bytes."""
        key, idx = self._key()
        row = idx[key]
        catalog = {key: {"catalog_status": "exploratory",
                         "record": {"forged": True}}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        self.assertEqual(obs.record, row.record)
        self.assertNotIn("forged", obs.record)

    def test_stale_file_hash_in_key_is_hard_failure(self):
        key, idx = self._key()
        stale = (key[0], "0" * 64, key[2], key[3])
        with self.assertRaises(prov.IntegrityError):
            prov.resolve_reference(stale, catalog={key: {}},
                                   verified_index=idx)

    def test_missing_catalog_status_rejected(self):
        key, idx = self._key()
        with self.assertRaises(ValueError):
            prov.resolve_reference(key, catalog={key: {"record_id": "x"}},
                                   verified_index=idx)

    def test_d03_value_types_preserved_verbatim(self):
        """null vs "0" vs "-"/unobserved vs missing stay strictly
        distinguishable through resolve — no coercion, no series collapse
        to bare scalar.  Uses a synthetic verified index (record bytes are
        really hashed) since resolve reads the verified row, not the
        catalog entry."""
        rec = {"record_id": "t.d03", "pack_id": "test.pack",
               "kind": "k", "locator": {"row_label": "x"},
               "metrics": {"scalar": "422", "zero": "0",
                           "null_metric": None, "dash": "-",
                           "unobserved": "미조사",
                           "series": {"y2023": "1", "y2024": "2"},
                           "range": {"low": "10", "high": "20"},
                           "int_zero": 0, "float_zero": 0.0}}
        idx, key = _verified_pack(self, [rec])
        catalog = {key: {"catalog_status": "quarantined"}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        m = obs.record["metrics"]
        self.assertIsNone(m["null_metric"])
        self.assertEqual(m["zero"], "0")
        self.assertIsNot(m["zero"], m["null_metric"])
        # numeric zeros preserved verbatim: int 0, float 0.0, string "0",
        # and null are four strictly distinguishable values
        self.assertEqual(m["int_zero"], 0)
        self.assertIsInstance(m["int_zero"], int)
        self.assertNotIsInstance(m["int_zero"], bool)
        self.assertEqual(m["float_zero"], 0.0)
        self.assertIsInstance(m["float_zero"], float)
        self.assertIsNot(m["int_zero"], m["null_metric"])
        self.assertIsNot(m["float_zero"], m["null_metric"])
        self.assertEqual(m["dash"], "-")
        self.assertEqual(m["unobserved"], "미조사")
        self.assertNotIn("missing_key", m)
        self.assertEqual(m["series"], {"y2023": "1", "y2024": "2"})
        self.assertEqual(m["range"], {"low": "10", "high": "20"})




class SourceRefConflictTests(unittest.TestCase):
    """D04: stale/duplicate/unresolvable source_refs surface as conflicts."""

    def _key(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        return next(iter(idx))

    def test_duplicate_ref_conflict(self):
        key = self._key()
        out = prov.audit_source_refs([{"audit_key": key},
                                      {"audit_key": prov.audit_key_dict(key)}])
        self.assertEqual(len(out["resolved"]), 1)
        self.assertEqual(out["conflicts"][0]["kind"], "duplicate_ref")

    def test_stale_pin_conflict(self):
        key = self._key()
        stale = (key[0], "0" * 64, key[2], key[3])
        out = prov.audit_source_refs([{"audit_key": stale}])
        self.assertEqual(out["conflicts"][0]["kind"], "stale_pin")

    def test_unregistered_conflict(self):
        key = self._key()
        out = prov.audit_source_refs([{"audit_key": key}], catalog={})
        self.assertEqual(out["conflicts"][0]["kind"], "unregistered")

    def test_malformed_conflict(self):
        out = prov.audit_source_refs([{"audit_key": ("only", "three", 1)}])
        self.assertEqual(out["conflicts"][0]["kind"], "malformed")


class SnapshotTests(unittest.TestCase):
    """register_snapshot: immutable frozen copy (binds D08)."""

    def _resolved(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        key = next(iter(idx))
        row = idx[key]
        catalog = {key: {"catalog_status": "verified_observation",
                         "record_id": row.record["record_id"],
                         "record": row.record,
                         "locator": row.record.get("locator")}}
        return prov.resolve_reference(key, catalog=catalog,
                                      verified_index=idx)

    def test_snapshot_written_and_receipt_hashes_match(self):
        obs = self._resolved()
        with tempfile.TemporaryDirectory() as td:
            rec = prov.register_snapshot(obs, sources_dir=td)
            data = Path(rec.path).read_bytes()
            self.assertEqual(rec.sha256, hashlib.sha256(data).hexdigest())
            body = json.loads(data)
            self.assertEqual(body["audit_key"]["raw_line_sha256"],
                             obs.audit_key[3])
            self.assertEqual(body["record"], obs.record)

    def test_reregister_identical_is_idempotent(self):
        obs = self._resolved()
        with tempfile.TemporaryDirectory() as td:
            a = prov.register_snapshot(obs, sources_dir=td)
            b = prov.register_snapshot(obs, sources_dir=td)
            self.assertEqual(a.path, b.path)

    def test_different_bytes_same_path_conflict(self):
        obs = self._resolved()
        other = prov.ResolvedObservation(
            obs.audit_key, obs.pack_id, obs.pack_revision,
            obs.record_id, {"different": True}, obs.locator,
            obs.catalog_status)
        with tempfile.TemporaryDirectory() as td:
            prov.register_snapshot(obs, sources_dir=td)
            with self.assertRaises(prov.SnapshotConflictError):
                prov.register_snapshot(other, sources_dir=td)


class AdmissionValidationTests(unittest.TestCase):
    """P4-01: D01 admission checks — 1:1 join, schema types, semantic-
    duplicate rejection, nonfinite rejection."""

    def _obs(self, key, record=None):
        entry = {"audit_key": key}
        if record is not None:
            entry["record"] = record
        return entry

    def test_clean_join_passes(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k, idx[k].record) for k in idx]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["checked_observations"], 102)

    def test_key_only_entries_validate_against_verified_payloads(self):
        """Corrections-2 P4-01: key-only entries resolve payloads from the
        verified index — they still undergo every check (national has
        distinct payloads, so a complete key-only join PASSes — proving
        resolution, not bypass)."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [{"audit_key": k} for k in idx]  # key-only, no record field
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "PASS")

    def test_complete_join_duplicate_payloads_rejected(self):
        """Corrections-2 P4-01 negative: a COMPLETE 1:1 join (all keys
        present, all unique) still fails when two distinct physical keys
        carry identical observation payloads."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k1, k2 = list(idx)[:2]
        obs = [{"audit_key": k} for k in idx]
        # inject: entry for k2 claims k1's payload instead of its own
        obs[1] = {"audit_key": k2, "record": dict(idx[k1].record)}
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(rep["checks"]["join_1to1"]["ok"])  # join itself clean
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])

    def test_key_only_entry_cannot_hide_nonfinite_verified_payload(self):
        """Key-only admission of a row whose VERIFIED payload contains
        NaN is still flagged — resolving the payload does not skip the
        nonfinite check."""
        rec = {"record_id": "t.nan", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": float("nan")}}
        idx, key = _verified_pack(self, [rec])
        rep = prov.validate_observation_admission(
            [{"audit_key": key}], verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_rawrow_record_none_resolves_semantic_duplicates(self):
        """Corrections-3 P4-01: a RawRow with record=None resolves its
        payload from the verified index — duplicate semantic payloads in
        the index are still rejected (no None-bypass)."""
        rec = {"record_id": "t.dup", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": "1"}}
        idx, _k = _verified_pack(self, [rec, dict(rec)])  # same payload
        k1, k2 = list(idx)
        obs = [dataclasses.replace(idx[k1], record=None),
               dataclasses.replace(idx[k2], record=None)]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(rep["checks"]["join_1to1"]["ok"])
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])

    def test_rawrow_record_none_resolves_nonfinite_payload(self):
        """Corrections-3 P4-01: RawRow(record=None) resolves the verified
        payload — a NaN inside it is still flagged as nonfinite."""
        rec = {"record_id": "t.nan", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": float("nan")}}
        idx, key = _verified_pack(self, [rec])
        obs = [dataclasses.replace(idx[key], record=None)]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_orphan_key_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k) for k in idx]
        obs.append(self._obs(("rda.income.national.2024", NATIONAL_SHA,
                            999, "f" * 64)))
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["join_1to1"]["ok"])
        self.assertEqual(
            len(rep["checks"]["join_1to1"]["orphan_observation_keys"]), 1)

    def test_missing_observation_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k) for k in list(idx)[1:]]  # drop first key
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(
            len(rep["checks"]["join_1to1"]["missing_index_keys"]), 1)

    def test_schema_type_errors_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        bad = dict(prov.audit_key_dict(next(iter(idx))))
        bad["raw_line_sha256"] = "not-hex"
        rep = prov.validate_observation_admission(
            [self._obs(bad)] + [self._obs(k) for k in list(idx)[1:]],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["schema_types"]["ok"])

    def test_semantic_duplicate_rejected(self):
        """Same observation content admitted under two different physical
        keys — a distinct failure mode from raw-list key duplicates."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k1, k2 = list(idx)[:2]
        same = idx[k1].record  # identical content, different physical key
        rep = prov.validate_observation_admission(
            [self._obs(k1, same), self._obs(k2, dict(same))],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])
        self.assertEqual(
            len(rep["checks"]["semantic_duplicates"]["groups"]), 1)

    def test_duplicate_observation_key_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k = next(iter(idx))
        rep = prov.validate_observation_admission(
            [self._obs(k), self._obs(prov.audit_key_dict(k))],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(
            rep["checks"]["join_1to1"]["duplicate_observation_keys"])

    def test_nonfinite_values_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k = next(iter(idx))
        for bad in (float("nan"), float("inf"), float("-inf")):
            rep = prov.validate_observation_admission(
                [self._obs(k, {"metrics": {"v": bad}})],
                verified_index=idx)
            self.assertEqual(rep["verdict"], "FAIL")
            self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_requires_verified_index(self):
        with self.assertRaises(prov.ResolutionContextError):
            prov.validate_observation_admission([], verified_index=None)


class NegativePackTests(unittest.TestCase):
    """P4-05 negatives: missing/tampered pack bytes are hard failures."""

    def test_missing_pack_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                prov.load_pack_records(Path(td) / "nope.jsonl",
                                       "rda.income.national.2024")

    def test_tampered_pack_bytes_raise(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [b'{"a": 1}\n'])
            pin = prov.PINNED_PACKS["rda.income.national.2024"]
            with self.assertRaises(prov.IntegrityError):
                # real file sha != pinned national sha
                prov.load_pack_records(p, "rda.income.national.2024")
            with self.assertRaises(prov.IntegrityError):
                prov.build_audit_index(p, "rda.income.national.2024",
                                       pin["records_file_sha256"])

    def test_unpinned_pack_load_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [b'{"a": 1}\n'])
            with self.assertRaises(prov.UnpinnedPackError):
                prov.load_pack_records(p, "totally.unpinned.pack")


if __name__ == "__main__":
    unittest.main(verbosity=2)
