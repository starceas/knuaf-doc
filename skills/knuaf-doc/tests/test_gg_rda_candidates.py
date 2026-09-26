"""Tests for the approved-candidate gate in
``gg_rda_candidates.lookup_approved_candidates`` (DECISION-20260924 §2–§3,
major-aware writing path).

Bound requirements:
- ``gg_rda_lookup.lookup_rda_data`` gained the rda-lookup verification-
  status contract (DESIGN-RL K1–K8 — supersedes the earlier
  byte-for-byte-unchanged clause): candidate set, order, and audit keys
  are preserved; returned records are deep copies carrying
  ``verification``; verdicts are
  ``not_found|ambiguous|series|unverified|unique``.  The new API consumes
  the raw result, never redefines it.
- Raw observation, eligibility, and use approval are DISTINCT results;
  ``unique`` is never permission.
- Promotion fails closed on: missing source bytes/receipt (new-format
  receipt binds parser id+version, parsed-output hash, acquisition
  path/time, physical locator, original unit/population, custody), any
  observed status other than ``verified_observation``, unresolved or
  undeclared conflict, missing redistribution/use rights, mismatched or
  unverifiable major/species/product/region/period/unit/population/
  use_scope (query-unset axes verify against the physical record),
  absent accepted-catalog receipt, ambiguous/series/partial-filter
  observation sets, and explicit ``audit_key`` selection (a selector,
  never a bypass — it resolves no declared conflict).
- Full physical audit identity + pinned pack integrity stay required
  (IntegrityError propagates).
- Current packs carry no ``evidence`` axes, so current records stay
  unapproved; a synthetic fixture demonstrates the positive path.

Self-contained: ``python3 -B tests/test_gg_rda_candidates.py`` from
``skills/knuaf-doc/``.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KNUAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KNUAF_DOC / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_lookup as lookup  # noqa: E402
import gg_rda_provenance as prov  # noqa: E402
import gg_rda_research as research  # noqa: E402
import gg_rda_candidates as cand  # noqa: E402

TEST_PACK = "test.candidate.pack"
TEST_REV = "t1"
TEST_AUTHORITY = "test.accepted-catalog/" + TEST_PACK
SRC_SHA = hashlib.sha256(b"synthetic official source pdf").hexdigest()
OUT_SHA = hashlib.sha256(b"synthetic parser output").hexdigest()


def _record(rid, name, *, form="생체", year=2024, kind="test_kind",
            metrics=None, region_level="national", sido=None,
            quantity_unit="kg"):
    return {
        "record_id": rid,
        "pack_id": TEST_PACK,
        "kind": kind,
        "schema_version": "1.0.0",
        "crop": {"name": name, "aliases": [], "form": form,
                 "cycle": "년 1기작", "category": "시험",
                 "major_ids": ["specialty_crops"]},
        "region": {"level": region_level, "name": sido or "전국",
                   "sido": sido},
        "basis": {"year": year, "quantity_unit": quantity_unit,
                  "area_unit": None, "currency": None},
        "metrics": metrics or {"value": "100"},
        "cost_items": None,
        "locator": {"row_label": name},
        # mirrors every real pack row's extraction shape — the shared
        # is_verified_observation predicate requires status "extracted"
        "extraction": {"status": "extracted"},
    }


def _evidence_for(rec):
    """Fully-approved axis declaration for one synthetic record."""
    crop = rec["crop"]
    return {
        "source": {
            "custody": "bytes_secured",
            "media_type": "pdf",
            "source_id": "test.source.2024",
            "source_sha256": SRC_SHA,
            "acquired": {"path": "docs/test-source.pdf",
                         "at": "2026-01-01T00:00:00Z"},
            "locator": {"pdf_page": 3},
            "parser": {"id": "test-parser", "version": "1.0.0"},
            "output_sha256": OUT_SHA,
            "unit": "kg",
            "population": "시험 모집단",
        },
        "conflict": "none",
        "rights": {"redistribution": "confirmed", "use": "confirmed"},
        "applicability": {
            "major_ids": ["specialty_crops"],
            "species": [crop["name"]],
            "products": [crop["form"] or "생체"],
            "regions": ["national"],
            "periods": [2024],
            "units": ["kg"],
            "population": "시험 모집단",
            "use_scope": ["plan_input", "market_context"],
        },
    }


def _receipt():
    return {"observation_status": "matched",
            "source_pdf_sha256": SRC_SHA,
            "prep_receipt_ref": "runs/test/AUDIT-JOIN.jsonl#L1"}


def _build(root, specs):
    """Write a synthetic pinned pack: records.jsonl + manifest.json rows
    (catalog_status + source_observation receipt + evidence axes) +
    catalog.json (with pinned source_sha256) + major aliases.

    ``specs`` items: {"record": rec, "catalog_status": str,
    "source_observation": dict|"keep"|"omit", "evidence": dict|None,
    "include_row": bool}."""
    packs = Path(root)
    pack_dir = packs / "common" / "test-pack"
    pack_dir.mkdir(parents=True)
    (packs / "aliases").mkdir()
    (packs / "aliases" / "major-aliases.json").write_text(json.dumps({
        "majors": {"specialty_crops": {"label": "특용작물전공",
                                       "aliases": ["특용작물"],
                                       "status": "완성"}}
    }, ensure_ascii=False), encoding="utf-8")

    records_path = pack_dir / "records.jsonl"
    records_path.write_bytes(b"".join(
        json.dumps(s["record"], ensure_ascii=False).encode("utf-8")
        + b"\n" for s in specs))
    file_sha = hashlib.sha256(records_path.read_bytes()).hexdigest()
    index = prov.build_audit_index(records_path, TEST_PACK, file_sha)
    keys = sorted(index, key=lambda k: k[2])

    rows, entries = [], {}
    for spec, key in zip(specs, keys):
        if spec.get("include_row", True) is False:
            continue
        so = spec.get("source_observation", "keep")
        row = {
            "physical_line": key[2],
            "raw_line_sha256": key[3],
            "audit_key": prov.audit_key_dict(key),
            "catalog_status": spec.get("catalog_status",
                                       "verified_observation"),
            "duplicate_link": {"is_primary": True,
                               "primary_audit_key": None},
            "gap_evidence": None,
        }
        if so == "keep":
            row["source_observation"] = _receipt()
        elif so != "omit":
            row["source_observation"] = so
        if spec.get("evidence") is not None:
            row["evidence"] = spec["evidence"]
        rows.append(row)
        entries[key] = row
    manifest = {
        "schema": "knuaf-doc-benchmark-pack-manifest/1",
        "pack_id": TEST_PACK,
        "pack_revision": "1.0.0",
        "records_file": "records.jsonl",
        "records_file_sha256": file_sha,
        "total_physical_rows": len(keys),
        "rows": rows,
    }
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (packs / "catalog.json").write_text(json.dumps({
        "packs": [{"pack_id": TEST_PACK, "kind": "common",
                   "major_id": None, "rank": 1,
                   "relpath": "common/test-pack/records.jsonl",
                   "source_sha256": SRC_SHA}],
        "gaps": [],
    }, ensure_ascii=False), encoding="utf-8")
    return packs, file_sha, keys, entries


GOOD_QUERY = dict(major="특용작물", kind="test_kind", year=2024,
                  form="생체", unit="kg", use_scope="plan_input")


class SyntheticFixtureMixin:
    """Mount ``self.specs`` as a pinned pack (default: row 0 감자 fully
    approved-able, row 1 고구마 with no evidence axes)."""

    specs = None
    pin_authority = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gg-cand-test-")
        self.addCleanup(self._tmp.cleanup)
        specs = self.specs if self.specs is not None else [
            {"record": _record("test.candidate.감자", "감자"),
             "evidence": _evidence_for(_record("t", "감자"))},
            {"record": _record("test.candidate.고구마", "고구마",
                               form="건품"),
             "evidence": None},
        ]
        packs_dir, file_sha, self.keys, entries = _build(
            self._tmp.name, specs)
        self._base = lookup.BASE_DIR
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        self._pin = dict(prov.PINNED_PACKS)
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": len(specs),
            "relpath": "common/test-pack/records.jsonl",
        }
        self._auth = dict(research.PINNED_CATALOG_AUTHORITIES)
        if self.pin_authority:
            research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
                research.catalog_content_digest(entries)
        self.addCleanup(self._restore)

    def _restore(self):
        lookup.BASE_DIR = self._base
        lookup._CACHE.clear()
        prov.PINNED_PACKS.clear()
        prov.PINNED_PACKS.update(self._pin)
        research.PINNED_CATALOG_AUTHORITIES.clear()
        research.PINNED_CATALOG_AUTHORITIES.update(self._auth)


class PositivePathTests(SyntheticFixtureMixin, unittest.TestCase):
    """A fully declared + verified + receipted candidate approves."""

    def test_approved_candidate(self):
        got = cand.lookup_approved_candidates("감자", "전국", **GOOD_QUERY)
        self.assertEqual(got["status"], "approved")
        self.assertEqual(got["observation"]["status"], "unique")
        self.assertEqual(got["counts"], {"candidates": 1, "eligible": 1,
                                       "approved": 1})
        c = got["approved_candidates"][0]
        self.assertEqual(c["rejections"], [])
        self.assertTrue(c["eligible"])
        self.assertTrue(c["approved"])
        self.assertEqual(c["audit_key"]["physical_jsonl_line_1based"], 1)
        self.assertEqual(c["observation_status"], "verified_observation")
        self.assertTrue(c["axes"]["use_approval"]["catalog_accepted"])

    def test_unevidenced_row_fails_every_evidence_axis(self):
        got = cand.lookup_approved_candidates("고구마", "전국", **dict(
            GOOD_QUERY, form="건품"))
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        # the approved catalog still authenticates and the row is a
        # verified observation — but every evidence axis fails closed
        self.assertEqual(c["observation_status"], "verified_observation")
        self.assertTrue(c["axes"]["use_approval"]["catalog_accepted"])
        for reason in ("source_receipt_absent",
                       "conflict_status_unconfirmed",
                       "rights_unconfirmed",
                       "major_not_applicable"):
            self.assertIn(reason, c["rejections"])
        self.assertFalse(c["eligible"])
        self.assertFalse(c["approved"])


class RawPreservationTests(SyntheticFixtureMixin, unittest.TestCase):
    """lookup_rda_data byte-for-byte + candidate set/order/keys intact."""

    def test_returned_records_never_share_the_cache(self):
        """K7/F1 — the 'lookup unchanged' byte contract is superseded by
        the rda-lookup verification status task; what stays guaranteed is
        that returned records are deep copies: mutating one never
        contaminates a later (warm-cache) lookup."""
        kw = dict(major="특용작물", kind="test_kind", year=2024,
                  form="생체")
        first = lookup.lookup_rda_data("감자", "전국", **kw)
        first["records"][0]["metrics"]["value"] = "999"
        first["records"][0]["basis"]["year"] = 1900
        first["records"][0]["audit_key"]["raw_line_sha256"] = "x"
        first["records"][0]["verification"]["verified"] = False
        second = lookup.lookup_rda_data("감자", "전국", **kw)
        rec = second["records"][0]
        self.assertEqual(rec["metrics"]["value"], "100")
        self.assertEqual(rec["basis"]["year"], 2024)
        self.assertNotEqual(rec["audit_key"]["raw_line_sha256"], "x")
        self.assertTrue(rec["verification"]["verified"])

    def test_candidate_set_order_and_audit_keys_identical_to_raw(self):
        kw = dict(major="특용작물", kind="test_kind", year=2024)
        raw = lookup.lookup_rda_data("감자", "전국", form="생체", **kw)
        got = cand.lookup_approved_candidates(
            "감자", "전국", form="생체", unit="kg", use_scope="plan_input",
            **kw)
        self.assertEqual(got["observation"]["status"], raw["status"])
        self.assertEqual(
            [r["audit_key"] for r in raw["records"]],
            [c["audit_key"] for c in got["candidates"]])
        for c, r in zip(got["candidates"], raw["records"]):
            # the raw result is consumed verbatim — equal deep-copied
            # records, never the internal cache reference (K7)
            self.assertEqual(c["record"], r)

    def test_integrity_failure_propagates(self):
        prov.PINNED_PACKS[TEST_PACK]["records_file_sha256"] = "0" * 64
        lookup._CACHE.clear()
        with self.assertRaises(prov.IntegrityError):
            cand.lookup_approved_candidates("감자", "전국", **GOOD_QUERY)

    def test_malformed_audit_key_rejected_like_raw(self):
        with self.assertRaises(ValueError):
            cand.lookup_approved_candidates(
                "감자", "전국", audit_key=("only", "three", 1),
                **GOOD_QUERY)


class GateFixtureMixin(SyntheticFixtureMixin):
    """Single-row fixture whose evidence the test mutates before setUp
    via ``self.mutate`` (callable applied to the evidence dict) and whose
    record shape may be overridden via ``self.record_over``."""

    mutate = None
    record_over = None
    catalog_status = "verified_observation"
    source_observation = "keep"
    include_row = True

    def setUp(self):
        rec = _record("test.candidate.감자", "감자",
                      **(self.record_over or {}))
        ev = _evidence_for(rec)
        if self.mutate:
            self.mutate(ev)
        self.specs = [{"record": rec,
                       "catalog_status": self.catalog_status,
                       "source_observation": self.source_observation,
                       "evidence": ev,
                       "include_row": self.include_row}]
        super().setUp()


def _gate_test(name, mutate, reason, *, query_over=None, q_region="전국",
               record_over=None, catalog_status="verified_observation",
               source_observation="keep", include_row=True,
               pin_authority=True):
    """Build a TestCase asserting ``reason`` appears (and approval is
    denied) for a one-axis mutation of an otherwise approvable row."""

    def body(self):
        q = dict(GOOD_QUERY)
        if query_over:
            q.update(query_over)
        got = cand.lookup_approved_candidates("감자", q_region, **q)
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        self.assertIn(reason, c["rejections"])
        self.assertFalse(c["approved"])

    return type("Gate_%s" % name, (GateFixtureMixin, unittest.TestCase), {
        "mutate": staticmethod(mutate) if mutate else None,
        "record_over": record_over,
        "catalog_status": catalog_status,
        "source_observation": source_observation,
        "include_row": include_row,
        "pin_authority": pin_authority,
        "test_gate": body})


def _drop(path):
    def f(ev):
        cur = ev
        for k in path[:-1]:
            cur = cur[k]
        cur.pop(path[-1], None)
    return f


def _set(path, value):
    def f(ev):
        cur = ev
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
    return f


GateTests = [
    # --- source custody / new-format receipt
    _gate_test("source_absent", _drop(["source"]),
               "source_receipt_absent"),
    _gate_test("source_link_only",
               _set(["source", "custody"], "link_only"),
               "source_bytes_not_secured"),
    _gate_test("source_bad_locator",
               _set(["source", "locator"], {"note": "no physical pos"}),
               "source_receipt_incomplete"),
    _gate_test("source_parser_no_version",
               _set(["source", "parser"], {"id": "test-parser"}),
               "source_receipt_incomplete"),
    _gate_test("source_output_hash_absent",
               _drop(["source", "output_sha256"]),
               "source_receipt_incomplete"),
    _gate_test("source_acquired_bad_time",
               _set(["source", "acquired"], {"path": "docs/x.pdf",
                                            "at": "not-a-time"}),
               "source_receipt_incomplete"),
    _gate_test("source_acquired_no_path",
               _set(["source", "acquired"], {"path": "  ",
                                            "at": "2026-01-01T00:00:00Z"}),
               "source_receipt_incomplete"),
    _gate_test("source_sha_mismatch",
               _set(["source", "source_sha256"], "f" * 64),
               "source_sha256_mismatch"),
    # --- observation status
    _gate_test("status_exploratory", None,
               "observation_not_verified", catalog_status="exploratory"),
    _gate_test("status_quarantined", None,
               "observation_not_verified", catalog_status="quarantined"),
    # --- conflict
    _gate_test("conflict_unresolved", _set(["conflict"], "unresolved"),
               "conflict_unresolved"),
    _gate_test("conflict_corrected", _set(["conflict"], "corrected"),
               "conflict_correction_unverified"),
    _gate_test("conflict_absent", _drop(["conflict"]),
               "conflict_status_unconfirmed"),
    # --- rights
    _gate_test("rights_internal_only",
               _set(["rights"], {"redistribution": "internal_only",
                                 "use": "confirmed"}),
               "rights_unconfirmed"),
    _gate_test("rights_bare_list",
               _set(["rights"], ["redistribution", "use"]),
               "rights_unconfirmed"),
    _gate_test("rights_partial_dict",
               _set(["rights"], {"redistribution": "confirmed"}),
               "rights_unconfirmed"),
    _gate_test("rights_absent", _drop(["rights"]),
               "rights_unconfirmed"),
    # --- applicability: major / species / product / region / period /
    #     unit / population / use_scope
    _gate_test("major_mismatch",
               _set(["applicability", "major_ids"], ["fruit_trees"]),
               "major_not_applicable"),
    _gate_test("species_mismatch",
               _set(["applicability", "species"], ["고구마"]),
               "species_mismatch"),
    _gate_test("product_mismatch",
               _set(["applicability", "products"], ["건품"]),
               "product_mismatch"),
    _gate_test("product_unconfirmed", None,
               "product_unconfirmed",
               record_over={"form": None}, query_over={"form": None}),
    _gate_test("region_mismatch",
               _set(["applicability", "regions"], [{"sido": "경기"}]),
               "region_mismatch"),
    _gate_test("region_record_mismatch", None,
               "region_mismatch",
               record_over={"region_level": "provincial", "sido": "경기"},
               q_region="경기"),
    _gate_test("period_mismatch",
               _set(["applicability", "periods"], [2019, 2020]),
               "period_mismatch"),
    _gate_test("period_unconfirmed", None,
               "period_unconfirmed",
               record_over={"year": None}, query_over={"year": None}),
    _gate_test("unit_mismatch", None,
               "unit_mismatch", query_over={"unit": "원"}),
    _gate_test("unit_record_mismatch",
               _set(["applicability", "units"], ["원"]),
               "unit_mismatch"),
    _gate_test("unit_unconfirmed",
               _set(["source", "unit"], None),
               "unit_unconfirmed",
               record_over={"quantity_unit": None},
               query_over={"unit": None}),
    _gate_test("population_mismatch",
               _set(["applicability", "population"], "다른 모집단"),
               "population_mismatch"),
    _gate_test("use_scope_unspecified", None,
               "use_scope_unspecified",
               query_over={"use_scope": None}),
    _gate_test("use_scope_mismatch", None,
               "use_scope_mismatch",
               query_over={"use_scope": "legal_evidence"}),
    _gate_test("use_scope_undeclared",
               _set(["applicability", "use_scope"], []),
               "use_scope_unconfirmed"),
    # --- approved-catalog receipt / acceptance
    _gate_test("receipt_incomplete", None,
               "catalog_receipt_incomplete",
               source_observation={"observation_status": "matched",
                                   "source_pdf_sha256": SRC_SHA}),
    _gate_test("catalog_entry_absent", None,
               "catalog_entry_absent", include_row=False),
    _gate_test("catalog_not_accepted", None,
               "catalog_not_accepted", pin_authority=False),
]

for _t in GateTests:
    globals()[_t.__name__] = _t


class DistinctResultTests(GateFixtureMixin, unittest.TestCase):
    """Raw observation, eligibility, and use approval stay distinct."""

    pin_authority = False  # manifest exists but is unauthenticated

    def test_unauthenticated_catalog_fails_approval_and_verification(self):
        got = cand.lookup_approved_candidates("감자", "전국",
                                              **GOOD_QUERY)
        self.assertEqual(got["status"], "unapproved")
        # K8: catalog authentication is part of the shared verification
        # predicate — an unauthenticated manifest also fails the
        # observation axis, so the raw verdict is unverified, not unique
        self.assertEqual(got["observation"]["status"], "unverified")
        c = got["candidates"][0]
        # selection still resolved (unverified is a single selection);
        # both the approval layer and the K8 observation axis fail
        self.assertEqual(c["rejections"],
                         ["catalog_not_accepted",
                          "observation_not_verified"])
        self.assertFalse(c["eligible"])
        self.assertFalse(c["approved"])
        self.assertEqual(got["counts"]["eligible"], 0)
        self.assertEqual(got["counts"]["approved"], 0)


class AmbiguityPreservationTests(unittest.TestCase):
    """Top-level approval requires an unambiguous observation selection —
    a fully eligible row inside an ambiguous set or a lone partial-filter
    candidate never marks the query approved."""

    def _fixture(self, specs):
        td = tempfile.TemporaryDirectory(prefix="gg-cand-amb-")
        self.addCleanup(td.cleanup)
        packs_dir, file_sha, keys, entries = _build(td.name, specs)
        self._base = lookup.BASE_DIR
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        self._pin = dict(prov.PINNED_PACKS)
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": len(specs),
            "relpath": "common/test-pack/records.jsonl",
        }
        self._auth = dict(research.PINNED_CATALOG_AUTHORITIES)
        research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
            research.catalog_content_digest(entries)

        def restore():
            lookup.BASE_DIR = self._base
            lookup._CACHE.clear()
            prov.PINNED_PACKS.clear()
            prov.PINNED_PACKS.update(self._pin)
            research.PINNED_CATALOG_AUTHORITIES.clear()
            research.PINNED_CATALOG_AUTHORITIES.update(self._auth)
        self.addCleanup(restore)
        return keys

    def test_raw_ambiguous_with_one_eligible_row_stays_unapproved(self):
        rec_a = _record("test.candidate.감자.a", "감자",
                        metrics={"value": "100"})
        rec_b = _record("test.candidate.감자.b", "감자",
                        metrics={"value": "200"})
        keys = self._fixture([
            {"record": rec_a, "evidence": _evidence_for(rec_a)},
            {"record": rec_b, "evidence": None},
        ])
        got = cand.lookup_approved_candidates("감자", "전국",
                                              **GOOD_QUERY)
        self.assertEqual(got["observation"]["status"], "ambiguous")
        self.assertEqual(len(got["candidates"]), 2)
        self.assertEqual(got["status"], "unapproved")
        self.assertEqual(got["approved_candidates"], [])
        # row A passes every eligibility axis but the set is ambiguous —
        # eligibility is reported distinctly, approval is denied
        a = got["candidates"][0]
        self.assertEqual(a["rejections"],
                         ["observation_selection_unresolved"])
        self.assertTrue(a["eligible"])
        self.assertFalse(a["approved"])
        self.assertEqual(got["counts"], {"candidates": 2, "eligible": 1,
                                       "approved": 0})
        # an explicit audit_key taken from THIS set resolves the row
        keyed = cand.lookup_approved_candidates(
            "감자", "전국", audit_key=prov.audit_key_dict(keys[0]),
            **GOOD_QUERY)
        self.assertEqual(keyed["status"], "approved")
        self.assertEqual(len(keyed["approved_candidates"]), 1)
        # the unevidenced member selected by key still fails its axes
        keyed_b = cand.lookup_approved_candidates(
            "감자", "전국", audit_key=prov.audit_key_dict(keys[1]),
            **GOOD_QUERY)
        self.assertEqual(keyed_b["status"], "unapproved")

    def test_lone_partial_filter_row_never_approved(self):
        rec = _record("test.candidate.감자", "감자")
        self._fixture([{"record": rec, "evidence": _evidence_for(rec)}])
        # complete axes supplied except the filter is partial (no kind /
        # year / form): raw verdict is ambiguous even with one candidate
        got = cand.lookup_approved_candidates(
            "감자", "전국", "특용작물", unit="kg", use_scope="plan_input")
        self.assertEqual(got["observation"]["status"], "ambiguous")
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        self.assertIn("observation_selection_unresolved",
                      c["rejections"])
        self.assertFalse(c["approved"])
        self.assertEqual(got["counts"]["approved"], 0)

    def test_series_kind_never_approves_without_period_selection(self):
        rec = _record("test.candidate.감자.series", "감자",
                      kind="wholesale_price_series", form=None,
                      metrics={"m1": "100", "m2": "110"})
        ev = _evidence_for(rec)
        ev["applicability"]["products"] = ["생체"]
        keys = self._fixture([{"record": rec, "evidence": ev}])
        got = cand.lookup_approved_candidates(
            "감자", "전국", audit_key=prov.audit_key_dict(keys[0]),
            **dict(GOOD_QUERY, kind="wholesale_price_series", form=None))
        # DESIGN-RL §6 F4: the audit path keeps the same verdict order —
        # a lone series observation resolves series, never unique
        self.assertEqual(got["observation"]["status"], "series")
        self.assertEqual(got["status"], "unapproved")
        self.assertIn("series_period_unspecified",
                      got["candidates"][0]["rejections"])

    def test_audit_key_does_not_resolve_declared_conflict(self):
        rec = _record("test.candidate.감자", "감자")
        ev = _evidence_for(rec)
        ev["conflict"] = "unresolved"  # declared cross-source conflict
        keys = self._fixture([{"record": rec, "evidence": ev}])
        got = cand.lookup_approved_candidates(
            "감자", "전국", audit_key=prov.audit_key_dict(keys[0]),
            **GOOD_QUERY)
        self.assertEqual(got["observation"]["status"], "unique")
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        self.assertIn("conflict_unresolved", c["rejections"])
        self.assertNotIn("observation_selection_unresolved",
                         c["rejections"])
        self.assertFalse(c["approved"])


class AuditKeyBypassTests(SyntheticFixtureMixin, unittest.TestCase):
    """Explicit audit_key is a selector, never a gate bypass."""

    def test_audit_key_selects_but_does_not_bypass(self):
        raw = lookup.lookup_rda_data("고구마", "전국", major="특용작물",
                                     kind="test_kind", year=2024,
                                     form="건품")
        key = raw["records"][0]["audit_key"]
        got = cand.lookup_approved_candidates(
            "고구마", "전국", audit_key=key,
            **dict(GOOD_QUERY, form="건품"))
        # raw layer resolved the key to a physical row (unique)...
        self.assertEqual(got["observation"]["status"], "unique")
        # ...but approval still denied — the key bypasses nothing
        self.assertEqual(got["status"], "unapproved")
        self.assertFalse(got["candidates"][0]["approved"])
        self.assertIn("source_receipt_absent",
                      got["candidates"][0]["rejections"])

    def test_audit_key_outside_filtered_set_is_not_found(self):
        other = lookup.lookup_rda_data("감자", "전국", major="특용작물",
                                       kind="test_kind", year=2024,
                                       form="생체")
        foreign = other["records"][0]["audit_key"]
        got = cand.lookup_approved_candidates(
            "고구마", "전국", audit_key=foreign,
            **dict(GOOD_QUERY, form="건품"))
        self.assertEqual(got["status"], "not_found")
        self.assertEqual(got["candidates"], [])


class InputGuardTests(unittest.TestCase):

    def test_major_unresolved_fails_closed(self):
        for bad in ("없는전공", None, ""):
            got = cand.lookup_approved_candidates("감자", "전국", bad)
            self.assertEqual(got["status"], "major_unresolved")
            self.assertEqual(got["candidates"], [])
            self.assertEqual(got["observation"]["candidate_count"], 0)

    def test_no_candidates_is_not_found(self):
        got = cand.lookup_approved_candidates(
            "없는작물", "전국", "특용작물", use_scope="plan_input")
        self.assertEqual(got["status"], "not_found")
        self.assertEqual(got["observation"]["status"], "not_found")


class RealPackTests(unittest.TestCase):
    """Current packs lack the evidence axes — real records stay
    unapproved even when the raw verdict is ``unique``."""

    def test_unique_raw_verdict_is_not_permission(self):
        got = cand.lookup_approved_candidates(
            "사과", "전국", "과수", kind="useful_life", year=2025,
            form="왜성(M9/M26)", unit="년", use_scope="plan_input")
        self.assertEqual(got["observation"]["status"], "unverified")
        self.assertEqual(got["observation"]["candidate_count"], 1)
        self.assertEqual(got["status"], "unapproved")
        self.assertEqual(got["approved_candidates"], [])
        c = got["candidates"][0]
        # honest per-axis truth on real data: this row is quarantined in
        # the authenticated manifest — the accepted catalog AND its
        # receipt are real, yet raw ``unique`` still is not permission
        self.assertEqual(c["observation_status"], "quarantined")
        self.assertTrue(c["axes"]["use_approval"]["catalog_accepted"])
        self.assertTrue(c["axes"]["use_approval"]["receipt_complete"])
        self.assertIn("observation_not_verified", c["rejections"])
        self.assertIn("source_receipt_absent", c["rejections"])
        self.assertIn("rights_unconfirmed", c["rejections"])
        self.assertIn("major_not_applicable", c["rejections"])

    def test_ambiguous_raw_set_all_unapproved(self):
        got = cand.lookup_approved_candidates(
            "기타", "전국", "특용작물", kind="production_stat",
            year=2024, form="", use_scope="plan_input")
        self.assertEqual(got["observation"]["status"], "ambiguous")
        self.assertEqual(len(got["candidates"]), 3)
        self.assertEqual(got["status"], "unapproved")
        for c in got["candidates"]:
            self.assertFalse(c["approved"])
            self.assertIn("observation_selection_unresolved",
                          c["rejections"])

    def test_real_quarantined_row_audit_key_no_bypass(self):
        """A real quarantined row picked by exact audit_key still fails."""
        pin = prov.PINNED_PACKS["mafra.specialty.production.2024"]
        man = json.loads(
            (lookup.BASE_DIR / pin["relpath"]).parent.joinpath(
                "manifest.json").read_text(encoding="utf-8"))
        row = next(r for r in man["rows"]
                   if r["catalog_status"] == "quarantined")
        line = row["audit_key"]["physical_jsonl_line_1based"]
        rec = json.loads(
            (lookup.BASE_DIR / pin["relpath"]).read_text(
                encoding="utf-8").splitlines()[line - 1])
        region = rec["region"]
        q_region = "전국" if region["level"] in ("national", "market") \
            else region["sido"]
        got = cand.lookup_approved_candidates(
            (rec["crop"] or {}).get("name") or "", q_region, "특용작물",
            kind=rec["kind"], year=(rec["basis"] or {}).get("year"),
            form=(rec["crop"] or {}).get("form"),
            use_scope="plan_input", audit_key=row["audit_key"])
        # quarantined row: the audit-key selection resolves one
        # candidate but verifies nothing -> unverified, still unapproved
        self.assertEqual(got["observation"]["status"], "unverified")
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        self.assertIn("observation_not_verified", c["rejections"])
        self.assertFalse(c["approved"])

    def test_industrial_insects_empty_slot_no_candidates(self):
        got = cand.lookup_approved_candidates(
            "쌍별귀뚜라미", "전국", "산업곤충", kind="production_stat",
            year=2024, form="생체", use_scope="plan_input")
        self.assertEqual(got["status"], "not_found")
        self.assertEqual(got["observation"]["reason"], "major_pack_empty")


if __name__ == "__main__":
    unittest.main()
