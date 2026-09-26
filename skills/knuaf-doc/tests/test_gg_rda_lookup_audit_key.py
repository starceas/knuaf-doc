"""Isolated tests for the D02 audit_key lookup contract in
``gg_rda_lookup.lookup_rda_data``.

Public status enum: ``not_found | ambiguous | series | unverified |
unique`` (rda-lookup 검증 상태 과제, DESIGN-RL K2 — the legacy
``blocked|miss|ambiguous|hit`` internal shorthands are NOT the public
contract).

Covered rules (ACCEPTANCE.md D02 + SPEC.md §4 + DESIGN-RL):
- ``ambiguous`` whenever >=2 distinct observations remain after ALL supplied
  filters — even under a complete filter set (작약 600g 8083/9000-style
  same-form/same-year collision sharing a legacy record_id).
- ``unique`` via exactly two paths — (a) explicit ``audit_key`` selection,
  (b) a complete filter combination isolating exactly one distinct
  observation — AND only when the shared is_verified_observation
  predicate holds; an unverified single resolves ``unverified`` instead
  (this synthetic pack carries no manifest, so every single-selection
  verdict here is ``unverified``).
- No partial-filter combination without ``audit_key`` may yield ``unique``
  — a lone candidate under a partial filter stays ``ambiguous``.
- Byte-identical physical rows are NEVER merged — every physical row is
  its own observation; there is no alias/collapse path on byte equality,
  legacy-ID/value coincidence, or any unverified field (corrections P4-02).
- Multi-observation ambiguity takes precedence over series typing
  (correction P4-04): >1 distinct series observation -> ``ambiguous``;
  ``series`` only when exactly one series observation remains.
- Candidate-order reversal / filter-argument order never changes verdict
  or candidate set (determinism).

Self-contained: ``python3 -B tests/test_gg_rda_lookup_audit_key.py`` from
``skills/knuaf-doc/``.  A synthetic fixture pack exercises the collision
matrix; the real pinned packs exercise live data paths.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

KNUAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KNUAF_DOC / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_lookup as lookup  # noqa: E402
import gg_rda_provenance as prov  # noqa: E402

TEST_PACK_ID = "test.synthetic.pack.v1"
TEST_REVISION = "t1"


def _record(rid, name, *, form=None, year=2024, kind="test_kind",
            category="시험", metrics=None, region_level="national",
            sido=None):
    return {
        "record_id": rid,
        "pack_id": TEST_PACK_ID,
        "kind": kind,
        "schema_version": "1.0.0",
        "crop": {"name": name, "aliases": [], "form": form,
                 "cycle": "년 1기작", "category": category,
                 "major_ids": ["specialty_crops"]},
        "region": {"level": region_level, "name": sido or "전국",
                   "sido": sido},
        "basis": {"year": year},
        "metrics": metrics or {},
        "cost_items": None,
        "locator": {"row_label": name},
    }


def _build_fixture(root):
    """Synthetic pack reproducing the 작약-600g 8083/9000 collision:

    - two DISTINCT observations sharing crop/form/year/kind/region AND a
      legacy record_id (the collision the SPEC names);
    - one byte-identical re-extraction pair (same raw bytes, two lines —
      distinct observations per P4-02);
    - TWO series-kind rows colliding on every filter axis (multi-series
      ambiguity, P4-04) plus ONE lone series row (unique -> series);
    - one plainly unique row.
    """
    packs = Path(root)
    pack_dir = packs / "common" / "test-pack"
    pack_dir.mkdir(parents=True)
    (packs / "aliases").mkdir()
    (packs / "aliases" / "major-aliases.json").write_text(json.dumps({
        "majors": {"specialty_crops": {"label": "특용작물전공",
                                       "aliases": ["특용작물"],
                                       "status": "완성"}}
    }, ensure_ascii=False), encoding="utf-8")

    rows = [
        # collision pair: same legacy id, same filter axes, different values
        _record("test.작약.collide", "작약", form="600g",
                metrics={"price": "8083"}),
        _record("test.작약.collide", "작약", form="600g",
                metrics={"price": "9000"}),
        # byte-identical re-extraction pair (identical JSON line, twice)
        _record("test.마늘.samecell", "마늘", form="대서",
                metrics={"price": "5500"}),
        _record("test.마늘.samecell", "마늘", form="대서",
                metrics={"price": "5500"}),
        # series-kind collision pair: identical filter axes, distinct
        # bytes — >1 series observation must yield ambiguous (P4-04)
        _record("test.양파.series", "양파", form=None, year=2024,
                kind="wholesale_price_series",
                metrics={"m1": "100", "m2": "110", "m3": "120"}),
        _record("test.양파.series", "양파", form=None, year=2024,
                kind="wholesale_price_series",
                metrics={"m1": "200", "m2": "210", "m3": "220"}),
        # lone series row — exactly one series observation -> series
        _record("test.감자.series", "감자", form=None, year=2024,
                kind="wholesale_price_series",
                metrics={"m1": "300", "m2": "310"}),
        # plain uniquely-identifiable row
        _record("test.배추.unique", "배추", form="가을",
                metrics={"price": "7000"}),
    ]
    raw = [json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n"
           for r in rows]
    records_path = pack_dir / "records.jsonl"
    records_path.write_bytes(b"".join(raw))
    file_sha = hashlib.sha256(records_path.read_bytes()).hexdigest()

    (packs / "catalog.json").write_text(json.dumps({
        "packs": [{"pack_id": TEST_PACK_ID, "kind": "common",
                   "major_id": None, "rank": 1,
                   "relpath": "common/test-pack/records.jsonl"}],
        "gaps": [],
    }, ensure_ascii=False), encoding="utf-8")
    return packs, file_sha


class SyntheticPackMixin:
    """Mount the fixture pack as the lookup's whole benchmark-packs tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gg-p4-test-")
        self.addCleanup(self._tmp.cleanup)
        packs_dir, self.file_sha = _build_fixture(self._tmp.name)
        self._base = lookup.BASE_DIR
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        self._pin = dict(prov.PINNED_PACKS)
        prov.PINNED_PACKS[TEST_PACK_ID] = {
            "pack_revision": TEST_REVISION,
            "records_file_sha256": self.file_sha,
            "record_lines": 8,
            "relpath": "common/test-pack/records.jsonl",
        }
        self.addCleanup(self._restore)

    def _restore(self):
        lookup.BASE_DIR = self._base
        lookup._CACHE.clear()
        prov.PINNED_PACKS.clear()
        prov.PINNED_PACKS.update(self._pin)


class AuditKeySelectionTests(SyntheticPackMixin, unittest.TestCase):
    """D02 path (a): explicit audit_key selection yields unique."""

    def test_audit_key_selects_exactly_one_from_collision(self):
        base = lookup.lookup_rda_data("작약", "전국", major="특용작물",
                                      kind="test_kind", year=2024,
                                      form="600g")
        self.assertEqual(base["status"], "ambiguous")
        self.assertEqual(len(base["records"]), 2)
        # colliding legacy record_ids stay distinguishable via audit keys
        keys = [r["audit_key"] for r in base["records"]]
        self.assertEqual(len({k["raw_line_sha256"] for k in keys}), 2)
        for wanted in keys:
            got = lookup.lookup_rda_data("작약", "전국", major="특용작물",
                                         kind="test_kind", year=2024,
                                         form="600g", audit_key=wanted)
            # no manifest on this pack -> unknown verification ->
            # unverified (selection still resolves exactly one row)
            self.assertEqual(got["status"], "unverified")
            self.assertEqual(len(got["records"]), 1)
            self.assertEqual(got["records"][0]["audit_key"], wanted)

    def test_audit_key_dict_and_tuple_forms(self):
        base = lookup.lookup_rda_data("배추", "전국", major="특용작물",
                                      kind="test_kind", year=2024,
                                      form="가을")
        key = base["records"][0]["audit_key"]
        by_dict = lookup.lookup_rda_data("배추", "전국", audit_key=key)
        tup = (key["pack_id"], key["records_file_sha256"],
               key["physical_jsonl_line_1based"], key["raw_line_sha256"])
        by_tuple = lookup.lookup_rda_data("배추", "전국", audit_key=tup)
        self.assertEqual(by_dict["status"], "unverified")
        self.assertEqual(by_tuple["status"], "unverified")
        self.assertEqual(by_dict["records"], by_tuple["records"])

    def test_audit_key_not_in_filtered_candidates_is_not_found(self):
        other = lookup.lookup_rda_data("배추", "전국")
        foreign_key = other["records"][0]["audit_key"]
        got = lookup.lookup_rda_data("작약", "전국", major="특용작물",
                                     kind="test_kind", year=2024,
                                     form="600g", audit_key=foreign_key)
        self.assertEqual(got["status"], "not_found")

    def test_malformed_audit_key_rejected(self):
        with self.assertRaises(ValueError):
            lookup.lookup_rda_data("작약", "전국",
                                   audit_key=("only", "three", 1))


class CardinalityTests(SyntheticPackMixin, unittest.TestCase):
    """D02 path (b): post-selection cardinality + complete-filter rule."""

    def test_complete_filter_isolating_one_observation_is_unique(self):
        got = lookup.lookup_rda_data("배추", "전국", major="특용작물",
                                     kind="test_kind", year=2024,
                                     form="가을")
        # verified only via an authenticated manifest — absent here,
        # so the single observation resolves unverified, not unique
        self.assertEqual(got["status"], "unverified")
        self.assertEqual(len(got["records"]), 1)

    def test_same_form_same_year_collision_stays_ambiguous(self):
        """Complete filter set, two distinct observations -> ambiguous.
        The 작약-600g contract: shared legacy id + identical filter axes
        must NOT auto-resolve."""
        got = lookup.lookup_rda_data("작약", "전국", major="특용작물",
                                     kind="test_kind", year=2024,
                                     form="600g")
        self.assertEqual(got["status"], "ambiguous")
        self.assertEqual(len(got["records"]), 2)

    def test_byte_identical_pair_stays_distinct_and_ambiguous(self):
        """P4-02: two physical lines with identical raw bytes are still
        two distinct observations — raw-byte equality alone is not proof
        of one source cell.  Complete filter -> ambiguous."""
        got = lookup.lookup_rda_data("마늘", "전국", major="특용작물",
                                     kind="test_kind", year=2024,
                                     form="대서")
        self.assertEqual(got["status"], "ambiguous")
        self.assertEqual(len(got["records"]), 2)
        keys = {tuple(sorted(r["audit_key"].items()))  # hashable form
                for r in got["records"]}
        self.assertEqual(len(keys), 2)  # full keys differ by line number
        self.assertEqual(
            len({r["audit_key"]["raw_line_sha256"]
                 for r in got["records"]}), 1)  # same bytes, still distinct

    def test_unverified_alias_field_does_not_collapse(self):
        """P4-02: a record carrying ANY duplicate-alias field (e.g.
        ``verified_duplicate_of``) is still counted as its own physical
        observation — no merge path exists at this layer."""
        k1 = ("p", "a" * 64, 1, "b" * 64)
        k2 = ("p", "a" * 64, 2, "b" * 64)
        canon = {"audit_key": prov.audit_key_dict(k1), "kind": "k"}
        aliased = {"audit_key": prov.audit_key_dict(k2), "kind": "k",
                   "verified_duplicate_of": prov.audit_key_dict(k1)}
        self.assertEqual(
            len(lookup._distinct_observations([canon, aliased])), 2)
        verdict, _ = lookup._resolve_verdict([canon, aliased],
                                             complete_filter=True)
        self.assertEqual(verdict, "ambiguous")

    def test_partial_filter_single_candidate_is_not_unique(self):
        """No partial-filter path without audit_key may yield unique —
        even when exactly one observation remains."""
        got = lookup.lookup_rda_data("배추", "전국")
        self.assertEqual(got["status"], "ambiguous")
        self.assertEqual(len(got["records"]), 1)
        got = lookup.lookup_rda_data("배추", "전국", form="가을",
                                     year=2024)
        self.assertEqual(got["status"], "ambiguous")

    def test_series_kind_yields_series_verdict(self):
        """Exactly one series observation remains -> series; payload is
        the full record, never collapsed to a scalar (binds D03)."""
        got = lookup.lookup_rda_data("감자", "전국", major="특용작물",
                                     kind="wholesale_price_series",
                                     year=2024, form="")
        self.assertEqual(got["status"], "series")
        self.assertEqual(len(got["records"]), 1)
        self.assertEqual(got["records"][0]["metrics"]["m2"], "310")

    def test_multiple_series_observations_are_ambiguous(self):
        """P4-04: ambiguity precedence — >1 distinct series observation
        under a COMPLETE filter is ambiguous, not series."""
        got = lookup.lookup_rda_data("양파", "전국", major="특용작물",
                                     kind="wholesale_price_series",
                                     year=2024, form="")
        self.assertEqual(got["status"], "ambiguous")
        self.assertEqual(len(got["records"]), 2)
        self.assertTrue(all(r["kind"] == "wholesale_price_series"
                            for r in got["records"]))
        # explicit audit_key isolates one series row -> series
        # (DESIGN-RL §6 F4: the audit path runs the same verdict order —
        #  a lone series observation is series whichever way it was
        #  selected; the former unique expectation is superseded)
        wanted = got["records"][0]["audit_key"]
        sel = lookup.lookup_rda_data("양파", "전국", major="특용작물",
                                     kind="wholesale_price_series",
                                     year=2024, form="", audit_key=wanted)
        self.assertEqual(sel["status"], "series")
        self.assertEqual(sel["records"][0]["audit_key"], wanted)


class DeterminismTests(SyntheticPackMixin, unittest.TestCase):
    """Filter-argument / candidate-order reversal must not change the
    verdict or candidate set."""

    def test_argument_order_reversal_identical(self):
        a = lookup.lookup_rda_data("작약", "전국", major="특용작물",
                                   kind="test_kind", year=2024,
                                   form="600g")
        b = lookup.lookup_rda_data("작약", "전국", form="600g",
                                   year=2024, kind="test_kind",
                                   major="특용작물")
        self.assertEqual(a["status"], b["status"])
        self.assertEqual(
            [r["audit_key"] for r in a["records"]],
            [r["audit_key"] for r in b["records"]])

    def test_repeated_calls_identical(self):
        a = lookup.lookup_rda_data("작약", "전국")
        b = lookup.lookup_rda_data("작약", "전국")
        self.assertEqual(a, b)

    def test_candidate_order_reversal_identical_verdict_and_set(self):
        """Two REAL lookup_rda_data calls — the second with the underlying
        candidate enumeration reversed (same physical rows, reversed
        order) — must return the identical verdict and identical
        candidate audit-key set."""
        kw = dict(major="특용작물", kind="test_kind", year=2024,
                  form="600g")
        fwd = lookup.lookup_rda_data("작약", "전국", **kw)
        orig = lookup._pack_records
        lookup._pack_records = lambda pack: list(reversed(orig(pack)))
        try:
            rev = lookup.lookup_rda_data("작약", "전국", **kw)
        finally:
            lookup._pack_records = orig
        self.assertEqual(fwd["status"], rev["status"])
        self.assertEqual(rev["status"], "ambiguous")
        fwd_keys = {tuple(sorted(r["audit_key"].items()))
                    for r in fwd["records"]}
        rev_keys = {tuple(sorted(r["audit_key"].items()))
                    for r in rev["records"]}
        self.assertEqual(fwd_keys, rev_keys)  # same physical candidates
        self.assertNotEqual(  # enumeration order really was reversed
            [r["audit_key"]["physical_jsonl_line_1based"]
             for r in fwd["records"]],
            [r["audit_key"]["physical_jsonl_line_1based"]
             for r in rev["records"]])


class StatusEnumTests(SyntheticPackMixin, unittest.TestCase):
    """Public enum only: not_found | ambiguous | series | unverified |
    unique."""

    STATUSES = {"not_found", "ambiguous", "unverified", "unique",
                "series"}

    def test_public_enum_values_only(self):
        for got in (
            lookup.lookup_rda_data("", "전국"),          # empty input
            lookup.lookup_rda_data("없는작물", "전국"),  # miss
            lookup.lookup_rda_data("작약", "전국"),      # ambiguous
            lookup.lookup_rda_data("배추", "전국", major="특용작물",
                                   kind="test_kind", year=2024,
                                   form="가을"),        # unique
            lookup.lookup_rda_data("양파", "전국",
                                   kind="wholesale_price_series"),
        ):
            self.assertIn(got["status"], self.STATUSES)
            self.assertNotIn(got["status"],
                             {"blocked", "miss", "hit"})

    def test_empty_and_unresolvable_inputs_are_not_found(self):
        self.assertEqual(
            lookup.lookup_rda_data("", "전국")["status"], "not_found")
        self.assertEqual(
            lookup.lookup_rda_data("작약", "전국",
                                   major="없는전공")["status"], "not_found")
        self.assertEqual(
            lookup.lookup_rda_data("없는작물", "전국")["status"],
            "not_found")

    def test_pack_integrity_failure_propagates(self):
        """SPEC §5: tampered pack bytes are a hard failure, not a verdict."""
        prov.PINNED_PACKS[TEST_PACK_ID]["records_file_sha256"] = "0" * 64
        lookup._CACHE.clear()
        with self.assertRaises(prov.IntegrityError):
            lookup.lookup_rda_data("작약", "전국")


class RealPackTests(unittest.TestCase):
    """Live pinned packs: real legacy-ID collision + real unique path."""

    def test_mafra_legacy_id_collision_ambiguous_then_keyed(self):
        """mafra 기타 national: 3 distinct observations share legacy
        record_id — ambiguous under complete filter, each distinguishable
        via audit_key (the D02 colliding-legacy-ID clause on real data).
        The keyed verdict follows each row's own manifest status
        (DESIGN-RL T3): verified -> unique, otherwise unverified."""
        got = lookup.lookup_rda_data(
            "기타", "전국", major="specialty_crops",
            kind="production_stat", year=2024, form="")
        self.assertEqual(got["status"], "ambiguous")
        self.assertEqual(len(got["records"]), 3)
        ids = {r["record_id"] for r in got["records"]}
        self.assertEqual(len(ids), 1)  # identical legacy id on all three
        keys = [r["audit_key"] for r in got["records"]]
        for wanted in keys:
            sel = lookup.lookup_rda_data(
                "기타", "전국", major="specialty_crops",
                kind="production_stat", year=2024, form="",
                audit_key=wanted)
            expected = ("unique" if sel["records"][0]["verification"]
                        ["verified"] else "unverified")
            self.assertEqual(sel["status"], expected)
            self.assertEqual(sel["records"][0]["audit_key"], wanted)

    def test_complete_filter_single_quarantined_on_real_pack(self):
        """사과 왜성(M9/M26) useful_life — the representative quarantined
        isolation case: complete filter still isolates one row, the
        verdict is ``unverified`` and the value stays visible (K2/T2)."""
        got = lookup.lookup_rda_data(
            "사과", "전국", major="fruit_trees", kind="useful_life",
            year=2025, form="왜성(M9/M26)")
        self.assertEqual(got["status"], "unverified")
        self.assertEqual(len(got["records"]), 1)
        v = got["records"][0]["verification"]
        self.assertEqual(v["level"], "quarantined")
        self.assertFalse(v["verified"])
        ak = got["records"][0]["audit_key"]
        self.assertEqual(ak["pack_id"], "rda.econ.2025")

    def test_real_byte_identical_rows_stay_distinct(self):
        """P4-02 on real data: the econ pack's byte-identical physical
        rows remain distinct observations — cardinality counts physical
        rows, not byte content."""
        pin = prov.PINNED_PACKS["rda.econ.2025"]
        path = lookup.BASE_DIR / pin["relpath"]
        idx = prov.build_audit_index(path, "rda.econ.2025",
                                     pin["records_file_sha256"])
        by_bytes = {}
        for key in idx:
            by_bytes.setdefault(key[3], []).append(key)
        dup_groups = [g for g in by_bytes.values() if len(g) > 1]
        self.assertTrue(dup_groups)  # fixture property: real byte-dups
        for group in dup_groups:
            recs = [{"audit_key": prov.audit_key_dict(k)} for k in group]
            self.assertEqual(len(lookup._distinct_observations(recs)),
                             len(group))

    def test_every_returned_record_carries_audit_key(self):
        got = lookup.lookup_rda_data("사과", "전국")
        for rec in got["records"]:
            self.assertIn("audit_key", rec)
            self.assertEqual(set(rec["audit_key"]),
                             {"pack_id", "records_file_sha256",
                              "physical_jsonl_line_1based",
                              "raw_line_sha256"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
