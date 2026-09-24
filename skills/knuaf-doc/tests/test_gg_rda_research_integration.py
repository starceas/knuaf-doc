"""Integration tests for the P4 shared-core integration slice
(stage-g005 ACCEPTANCE D05-D09).

Self-contained: ``python3 -B tests/test_gg_rda_research_integration.py``
from ``skills/knuaf-doc/`` or unittest discovery.  A synthetic pinned pack
(``test.pack``) exercises the propose/apply/hook paths end to end against
the real deployed modules; the shipped pinned packs cover live-data paths.

Bound requirements:
- D05  proposals never auto-promote unverified observations
       (quarantined/exploratory stay so; ambiguous/series/not_found stay
       unresolved; never first-record choice).
- D06  apply() CAS freshness — rejects stale proposals on pack-revision
       change, pin drift, or an already-answered target.
- D07  resolver verification hooks — core checks() cross-checks the
       audited ref (value/unit bound to the verified row, receipt through
       the provenance chain).
- D08  registered calculation snapshots stay verifiable after the live
       pack is deleted or mutated.
- D09  fabricated/unanchored manifest/receipt strings fail closed.
"""
import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

KNUAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KNUAF_DOC / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_provenance as prov  # noqa: E402
import gg_rda_research as research  # noqa: E402
import gg_core as core  # noqa: E402
import gg_finance as finance  # noqa: E402
import gg_school_excel as sx  # noqa: E402

TEST_PACK = "test.pack"
TEST_REV = "t1"
TEST_AUTHORITY = "test.synthetic.catalog"

RECORDS = [
    {
        "record_id": "test.pack.summary.food.감자", "pack_id": TEST_PACK,
        "kind": "crop_income", "schema_version": "1.0.0",
        "crop": {"name": "감자", "aliases": [], "form": None,
                 "cycle": "년 1기작", "category": "식량작물",
                 "major_ids": []},
        "region": {"level": "national", "name": "전국", "sido": None},
        "basis": {"year": 2024, "area_unit": "10a", "quantity_unit": "kg",
                  "currency": "원", "crops_per_year": 1},
        "metrics": {"quantity": "1000", "gross_receipts": "900000",
                    "operating_cost": "500000", "income": "400000",
                    "income_rate_pct": "44.4", "farmgate_price": None},
        "cost_items": None,
        "price_character": "gross_receipts_includes_farmgate_and_byproduct",
        "locator": {"pdf_page": 1, "printed_page": "1",
                    "section": "시험", "table": "시험표",
                    "row_label": "감자"},
        "caveats": ["시험용 합성 레코드"],
    },
    {
        "record_id": "test.pack.summary.food.고구마", "pack_id": TEST_PACK,
        "kind": "crop_income", "schema_version": "1.0.0",
        "crop": {"name": "고구마", "aliases": [], "form": None,
                 "cycle": "년 1기작", "category": "식량작물",
                 "major_ids": []},
        "region": {"level": "national", "name": "전국", "sido": None},
        "basis": {"year": 2024, "area_unit": "10a", "quantity_unit": "kg",
                  "currency": "원", "crops_per_year": 1},
        "metrics": {"quantity": "2000", "gross_receipts": "1800000",
                    "operating_cost": "900000", "income": "900000",
                    "income_rate_pct": "50.0", "farmgate_price": None},
        "cost_items": None, "price_character": "farmgate_unit_price",
        "locator": {"pdf_page": 1, "printed_page": "1",
                    "section": "시험", "table": "시험표",
                    "row_label": "고구마"},
        "caveats": [],
    },
]


TEST_PDF_SHA = hashlib.sha256(b"synthetic official pdf").hexdigest()


class PackFixture:
    """Synthetic pinned pack + verified index + observation catalog.

    Registers ``test.pack`` in ``prov.PINNED_PACKS`` (the same injection
    the accepted provenance lane's own tests use) and cleans it up.
    ``dir`` holds the physical records file so ``resolver_context`` and
    ``apply`` can build/verify live bytes from it.

    Catalog entries carry a well-formed ``source_observation`` receipt
    (P1-4: a verified status without a receipt must never promote);
    ``accepted=True`` wraps the catalog through ``research.accept_catalog``
    so positive paths exercise the real acceptance boundary."""

    def __init__(self, testcase, *, statuses=("verified_observation",),
                 accepted=True):
        self.td = tempfile.TemporaryDirectory(prefix="gg-p4-int-")
        testcase.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)
        self.records_path = self.dir / "records.jsonl"
        self.records_path.write_bytes(
            b"".join(json.dumps(r, ensure_ascii=False).encode("utf-8")
                     + b"\n" for r in RECORDS))
        self.file_sha = hashlib.sha256(
            self.records_path.read_bytes()).hexdigest()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": self.file_sha,
            "record_lines": len(RECORDS),
            "relpath": "records.jsonl",
        }
        testcase.addCleanup(lambda: prov.PINNED_PACKS.pop(TEST_PACK, None))
        self.index = prov.build_audit_index(
            self.records_path, TEST_PACK, self.file_sha)
        self.keys = sorted(self.index, key=lambda k: k[2])
        # observation catalog: audit-key TUPLE -> entry with catalog_status
        # + a complete observation receipt (verified rows only — the
        # quarantined/exploratory spellings keep locator/record_id but a
        # receipt is still attached for realism)
        self.catalog = {}
        for i, key in enumerate(self.keys):
            status = statuses[min(i, len(statuses) - 1)]
            rec = RECORDS[i]
            self.catalog[key] = {
                "catalog_status": status,
                "record_id": rec["record_id"],
                "locator": rec["locator"],
                "source_observation": {
                    "observation_status": "matched",
                    "source_pdf_sha256": TEST_PDF_SHA,
                    "prep_receipt_ref":
                        "runs/test/AUDIT-JOIN.jsonl#L%d" % key[2],
                },
            }
        self.accepted = accepted
        if accepted:
            # SCC1: acceptance must be authenticated — pin this synthetic
            # catalog's canonical digest as its authority BEFORE wrapping
            # (mirrors the real flow: independent acceptance exists first,
            # then the pinned content is consumed).
            digest = research.catalog_content_digest(self.catalog)
            research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = digest
            testcase.addCleanup(
                lambda: research.PINNED_CATALOG_AUTHORITIES.pop(
                    TEST_AUTHORITY, None))
            self.catalog_obj = research.accept_catalog(
                self.catalog, verified_indexes={TEST_PACK: self.index},
                authority=TEST_AUTHORITY)
        else:
            self.catalog_obj = self.catalog

    def context(self):
        return research.resolver_context(
            packs_dir=self.dir, catalog=self.catalog_obj,
            pack_ids={TEST_PACK})

    def key(self, i=0):
        return prov.audit_key_dict(self.keys[i])

    def mutate_pin(self, testcase, **overrides):
        pin = dict(prov.PINNED_PACKS[TEST_PACK])
        pin.update(overrides)
        prov.PINNED_PACKS[TEST_PACK] = pin
        testcase.addCleanup(
            lambda: prov.PINNED_PACKS.__setitem__(
                TEST_PACK, {"pack_revision": TEST_REV,
                            "records_file_sha256": self.file_sha,
                            "record_lines": len(RECORDS),
                            "relpath": "records.jsonl"}))


def _target(fid="f-income", metric="income", status="not_provided"):
    return {"id": fid, "metric": metric, "answer_state": status}


class ProposeContractTests(unittest.TestCase):
    """D05: proposals carry verified binding and never auto-promote."""

    def test_unique_lookup_result_proposes_audit_bound_value(self):
        fx = PackFixture(self)
        sel = self._lookup_result(fx, status="unique")
        prop = research.propose(TEST_REV, _target(), sel,
                                context=fx.context())
        self.assertEqual("proposal", prop["status"])
        self.assertEqual("400000", prop["value"])
        self.assertEqual("원", prop["unit"])
        self.assertEqual(2024, prop["period"])
        self.assertEqual("verified_observation", prop["verification_status"])
        self.assertTrue(prop["promotable"])
        self.assertEqual(fx.key(0), prop["audit_key"])
        self.assertEqual(
            "%s@%s#%s" % (TEST_PACK, TEST_REV,
                          RECORDS[0]["record_id"]),
            prop["source_ref"])
        # proposal binds physical-row identity — not legacy record_id alone
        self.assertIn("physical_jsonl_line_1based", prop["audit_key"])
        self.assertIn("raw_line_sha256", prop["audit_key"])

    def _lookup_result(self, fx, status, records=None):
        return {
            "status": status,
            "rank_used": 1,
            "pack_id": TEST_PACK,
            "records": records if records is not None else [
                dict(RECORDS[0], audit_key=fx.key(0))],
            "reason": None,
            "web": None,
        }

    def test_ambiguous_result_never_chooses_first_record(self):
        fx = PackFixture(self)
        sel = self._lookup_result(
            fx, status="ambiguous",
            records=[dict(RECORDS[0], audit_key=fx.key(0)),
                     dict(RECORDS[1], audit_key=fx.key(1))])
        prop = research.propose(TEST_REV, _target(), sel,
                                context=fx.context())
        self.assertEqual("unresolved", prop["status"])
        self.assertEqual("ambiguous", prop["reason"])
        self.assertIsNone(prop["value"])

    def test_series_result_never_scalar_extracted(self):
        fx = PackFixture(self)
        sel = self._lookup_result(fx, status="series")
        prop = research.propose(TEST_REV, _target(), sel,
                                context=fx.context())
        self.assertEqual("unresolved", prop["status"])
        self.assertEqual("series", prop["reason"])
        self.assertIsNone(prop["value"])

    def test_not_found_and_missing_selection_stay_unresolved(self):
        fx = PackFixture(self)
        for sel in (self._lookup_result(fx, status="not_found", records=[]),
                    {"status": "unique", "records": []},
                    "not-a-key", None):
            prop = research.propose(TEST_REV, _target(), sel,
                                    context=fx.context())
            self.assertEqual("unresolved", prop["status"], repr(sel))
            self.assertIsNone(prop["value"], repr(sel))

    def test_quarantined_and_exploratory_never_promoted(self):
        fx = PackFixture(self, statuses=("quarantined", "exploratory"))
        for i, expected in ((0, "quarantined"), (1, "exploratory")):
            prop = research.propose(
                TEST_REV, _target(), fx.key(i), context=fx.context())
            self.assertEqual("proposal", prop["status"])
            self.assertEqual(expected, prop["verification_status"])
            self.assertFalse(prop["promotable"])

    def test_fabricated_verified_status_never_promotable(self):
        # P1-4: a caller-claimed catalog asserting verified_observation on
        # a REAL audit key — no independent acceptance — must resolve but
        # never promote.
        fx = PackFixture(self, accepted=False)
        prop = research.propose(TEST_REV, _target(), fx.key(0),
                                context=fx.context())
        self.assertEqual("proposal", prop["status"])
        self.assertEqual("verified_observation",
                         prop["verification_status"])
        self.assertFalse(prop["catalog_accepted"])
        self.assertFalse(prop["promotable"])

    def test_verified_entry_without_receipt_not_promotable(self):
        # P1-4: even through the acceptance wrapper, a verified claim
        # lacking an observation receipt cannot validate — and a plain
        # receipt-less catalog entry never promotes.
        fx = PackFixture(self, accepted=False)
        fx.catalog[fx.keys[0]].pop("source_observation")
        prop = research.propose(TEST_REV, _target(), fx.key(0),
                                context=fx.context())
        self.assertEqual("proposal", prop["status"])
        self.assertFalse(prop["receipt_verified"])
        self.assertFalse(prop["promotable"])
        with self.assertRaises(ValueError):
            research.accept_catalog(
                {fx.keys[0]: {"catalog_status": "verified_observation",
                              "record_id": RECORDS[0]["record_id"],
                              "locator": RECORDS[0]["locator"]}})

    def test_accept_catalog_rejects_orphan_and_bad_status(self):
        fx = PackFixture(self)
        with self.assertRaises(ValueError):  # key not in verified bytes
            research.accept_catalog(
                {("test.pack", "f" * 64, 99, "e" * 64):
                 {"catalog_status": "quarantined"}},
                verified_indexes={TEST_PACK: fx.index},
                authority=TEST_AUTHORITY)
        with self.assertRaises(ValueError):  # unknown status spelling
            research.accept_catalog(
                {fx.keys[0]: {"catalog_status": "totally_verified"}},
                authority=TEST_AUTHORITY)

    def test_forged_verified_on_real_key_cannot_authenticate(self):
        # SCC1: the reviewer's exact route — copy a REAL quarantined key,
        # assert verified_observation + a well-shaped fabricated receipt,
        # pass the REAL verified index under a claimed real authority.
        # Shape validation passes; the pinned-authority digest must
        # reject the forged content.
        fx = PackFixture(self, statuses=("quarantined",))
        forged = dict(fx.catalog)
        forged[fx.keys[0]] = {
            "catalog_status": "verified_observation",
            "source_observation": {
                "observation_status": "made-up",
                "source_pdf_sha256": "a" * 64,
                "prep_receipt_ref": "nonexistent",
            },
        }
        with self.assertRaises(ValueError) as cm:
            research.accept_catalog(
                forged, verified_indexes={TEST_PACK: fx.index},
                authority=TEST_AUTHORITY)
        self.assertIn("unauthenticated", str(cm.exception))
        # no authority claimed at all — also unauthenticated
        with self.assertRaises(ValueError):
            research.accept_catalog(fx.catalog)
        # and a hand-constructed wrapper cannot mint acceptance either:
        # the constructor takes entries only — supplied digest text is
        # not even a parameter (SCC1)
        with self.assertRaises(TypeError):
            research.AcceptedCatalog(forged, "0" * 64)
        fake = research.AcceptedCatalog(forged)
        self.assertFalse(fake.accepted)
        prop = research.propose(
            TEST_REV, _target(), fx.key(0),
            context=research.resolver_context(
                packs_dir=fx.dir, catalog=fake, pack_ids={TEST_PACK}))
        self.assertEqual("proposal", prop["status"])
        self.assertFalse(prop["catalog_accepted"])
        self.assertFalse(prop["promotable"])

    def test_metric_absent_fails_closed(self):
        fx = PackFixture(self)
        prop = research.propose(
            TEST_REV, _target(metric="farmgate_price"), fx.key(0),
            context=fx.context())
        self.assertEqual("unresolved", prop["status"])
        self.assertEqual("metric_absent_in_verified_row", prop["reason"])

    def test_no_context_fails_closed(self):
        fx = PackFixture(self)
        # empty context dict -> no verified index -> unresolved
        prop = research.propose(
            TEST_REV, _target(), fx.key(0),
            context={"indexes": {}, "catalog": {}})
        self.assertEqual("unresolved", prop["status"])
        self.assertIn(prop["reason"],
                      {"resolver_context_required", "audit_key_unresolved"})


class ApplyCasTests(unittest.TestCase):
    """D06/P1-2: apply() requires an eligible live target AND verified
    live pack bytes — stale or unverifiable proposals rejected."""

    def _proposal(self, fx):
        return research.propose(
            TEST_REV, _target(), fx.key(0), context=fx.context())

    def test_apply_ready_on_fresh_proposal(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("ready", out["status"])
        self.assertTrue(out["target_verified"])
        self.assertEqual("400000", out["fact"]["value"])
        self.assertEqual("원", out["fact"]["unit"])
        self.assertEqual(fx.key(0), out["fact"]["audit_key"])
        self.assertEqual("proposed_research", out["fact"]["verification"])

    def test_apply_requires_current_target(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(prop, expected_revision=TEST_REV,
                             packs_dir=fx.dir)
        self.assertEqual("rejected", out["status"])
        self.assertEqual("target_current_required", out["reason"])

    def test_apply_rejects_target_mismatch(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(fid="f-other"),
                             packs_dir=fx.dir)
        self.assertEqual("rejected", out["status"])
        self.assertEqual("target_mismatch", out["reason"])

    def test_apply_rejects_missing_bound_target_id(self):
        # SCC3: the proposal bound NO target id — apply cannot certify
        # what it would write to, even with a well-formed current target.
        fx = PackFixture(self)
        prop = research.propose(
            TEST_REV, {"metric": "income", "answer_state": "not_provided"},
            fx.key(0), context=fx.context())
        self.assertEqual("proposal", prop["status"])
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("rejected", out["status"])
        self.assertEqual("bound_target_id_required", out["reason"])

    def test_apply_rejects_missing_current_target_id(self):
        # SCC3: a current target with no identity — only an answer_state —
        # passed every old guard and returned ready.
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(
            prop, expected_revision=TEST_REV,
            target_current={"answer_state": "not_provided"},
            packs_dir=fx.dir)
        self.assertEqual("rejected", out["status"])
        self.assertEqual("current_target_id_required", out["reason"])

    def test_apply_rejects_target_answered_at_propose(self):
        # P1-2: the target was already answered at propose time — the old
        # equality check would call this ready; it must reject.
        fx = PackFixture(self)
        prop = research.propose(
            TEST_REV, _target(status="provided"), fx.key(0),
            context=fx.context())
        self.assertEqual("proposal", prop["status"])
        out = research.apply(
            prop, expected_revision=TEST_REV,
            target_current=_target(status="provided"), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("target_ineligible_at_propose", out["reason"])

    def test_apply_rejects_wrong_expected_revision(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(prop, expected_revision="t0",
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("expected_revision != bound pack_revision",
                         out["reason"])

    def test_apply_rejects_pack_revision_change(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        fx.mutate_pin(self, pack_revision="t2")
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("pack_integrity_changed", out["reason"])

    def test_apply_rejects_pack_content_drift(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        fx.mutate_pin(self, records_file_sha256="0" * 64)
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("pack_integrity_changed", out["reason"])

    def test_apply_rejects_on_disk_pack_mutation(self):
        # P1-2: pin untouched but the live file bytes changed — only the
        # re-read freshness check can see this.
        fx = PackFixture(self)
        prop = self._proposal(fx)
        fx.records_path.write_bytes(b'{"mutated": true}\n')
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("pack_bytes_changed", out["reason"])

    def test_apply_rejects_deleted_pack_file(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        fx.records_path.unlink()
        out = research.apply(prop, expected_revision=TEST_REV,
                             target_current=_target(), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("pack_bytes_missing", out["reason"])

    def test_apply_rejects_already_answered_target(self):
        fx = PackFixture(self)
        prop = self._proposal(fx)
        out = research.apply(
            prop, expected_revision=TEST_REV,
            target_current=_target(status="provided"), packs_dir=fx.dir)
        self.assertEqual("stale", out["status"])
        self.assertEqual("target_already_answered", out["reason"])

    def test_apply_rejects_malformed_and_unresolved_proposals(self):
        fx = PackFixture(self)
        unresolved = research.propose(
            TEST_REV, _target(), {"status": "not_found", "records": []},
            context=fx.context())
        for bad in (unresolved, {"status": "proposal"}, {}, None):
            out = research.apply(bad, expected_revision=TEST_REV,
                                 target_current=_target(),
                                 packs_dir=fx.dir)
            self.assertEqual("rejected", out["status"], repr(bad))


class CoreVerificationHookTests(unittest.TestCase):
    """D07/D09: checks() cross-checks audited statistical refs."""

    def _workspace(self, fx):
        root = Path(tempfile.mkdtemp(prefix="gg-p4-core-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        src_name = "pack-records.jsonl"
        (root / src_name).write_bytes(fx.records_path.read_bytes())
        return root, src_name

    def _project(self, fx, src_name, audit_key, *, verification="unreviewed",
                 src_hash=None, fact_overrides=None, ref_overrides=None):
        if src_hash is None:
            src_hash = hashlib.sha256(
                fx.records_path.read_bytes()).hexdigest()
        fact = {
            "id": "f1", "field_id": "income", "kind": "observation",
            "value": "400000", "unit": "원", "value_type": "decimal",
            "period": 2024, "scope": "전국", "answer_state": "provided",
            "verification": verification}
        fact.update(fact_overrides or {})
        ref = {"id": "s1", "locator": "물리행 1", "revision": 1,
               "audit_key": audit_key,
               "source_pdf_sha256": TEST_PDF_SHA}
        ref.update(ref_overrides or {})
        fact["source_refs"] = [ref]
        return {
            "revision": 1,
            "sources": {"s1": {
                "path": src_name, "hash": src_hash, "revision": 1,
                "claims": {}, "claim_review": True}},
            "facts": {"f1": fact},
            "reviews": {}, "sections": {}, "rules": {},
            "questions": {},
        }

    def _stat_issues(self, rows):
        return [r for r in rows
                if r["check_id"].startswith("statistical_ref")]

    def _patch_ctx(self, fx):
        """Point the default resolver context at the synthetic fixture —
        same module-state injection pattern the accepted provenance lane's
        tests use on ``PINNED_PACKS``.  The context is computed BEFORE the
        patch (``fx.context()`` itself calls ``resolver_context``)."""
        ctx = fx.context()
        real = research.resolver_context
        research.resolver_context = lambda **kw: ctx
        self.addCleanup(
            lambda: setattr(research, "resolver_context", real))

    def test_resolvable_audit_ref_adds_no_statistical_block(self):
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0))
        rows = core.checks(root, p)
        self.assertEqual([], self._stat_issues(rows))

    def test_fabricated_audit_key_fails_closed(self):
        fx = PackFixture(self)
        root, src_name = self._workspace(fx)
        bad_key = dict(fx.key(0))
        bad_key["raw_line_sha256"] = "f" * 64
        p = self._project(fx, src_name, bad_key)
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_unresolved", issues[0]["check_id"])
        self.assertEqual("blocked", issues[0]["status"])

    def test_malformed_audit_key_fails_closed(self):
        fx = PackFixture(self)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, {"pack_id": TEST_PACK})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertEqual("blocked", issues[0]["status"])

    def test_unpinned_pack_fails_closed(self):
        fx = PackFixture(self)
        root, src_name = self._workspace(fx)
        bad_key = dict(fx.key(0))
        bad_key["pack_id"] = "fabricated.pack.2099"
        p = self._project(fx, src_name, bad_key)
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("no pinned entry", issues[0]["reason"])

    def test_quarantined_observation_blocks_claim_supported(self):
        fx = PackFixture(self, statuses=("quarantined",))
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                        verification="claim_supported")
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_unverified",
                         issues[0]["check_id"])
        self.assertEqual("blocked", issues[0]["status"])

    def test_source_hash_binding_enforced(self):
        fx = PackFixture(self)
        root = Path(tempfile.mkdtemp(prefix="gg-p4-core-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        # workspace source bytes differ from the pinned pack bytes
        other = b'{"x": 1}\n'
        (root / "other.jsonl").write_bytes(other)
        p = self._project(fx, "other.jsonl", fx.key(0),
                          src_hash=hashlib.sha256(other).hexdigest())
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("records_file_sha256", issues[0]["reason"])

    # --- P1-1: the submitted fact itself is cross-checked, not just its
    # audit reference ---

    def test_wrong_value_fails_claim_check(self):
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          fact_overrides={"value": "999999"})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("claim value", issues[0]["reason"])

    def test_wrong_unit_fails_claim_check(self):
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          fact_overrides={"unit": "USD"})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("claim unit", issues[0]["reason"])

    def test_wrong_locator_fails_claim_check(self):
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          ref_overrides={"locator": "물리행 2"})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("claim locator", issues[0]["reason"])

    def test_wrong_source_pdf_sha256_fails_claim_check(self):
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          ref_overrides={"source_pdf_sha256": "e" * 64})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("source_pdf_sha256", issues[0]["reason"])

    def test_nonfinite_value_rejected_when_bound_row_matches(self):
        # SCC6/D07(b): bound row AND claim both 'NaN' — the old ``a == b``
        # shortcut passed; now both sides must parse as finite numerics.
        orig = RECORDS[0]["metrics"]["income"]
        RECORDS[0]["metrics"]["income"] = "NaN"
        self.addCleanup(
            lambda: RECORDS[0]["metrics"].__setitem__("income", orig))
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          fact_overrides={"value": "NaN"})
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid", issues[0]["check_id"])
        self.assertIn("claim value", issues[0]["reason"])

    def test_num_eq_rejects_nonfinite_and_preserves_ranges(self):
        ne = research._num_eq
        self.assertFalse(ne("NaN", "NaN"))
        self.assertFalse(ne("Infinity", "Infinity"))
        self.assertFalse(ne("-Inf", "-Inf"))
        self.assertFalse(ne("12,34.5", "12,34.5"))
        self.assertFalse(ne("400000", "not-a-number"))
        self.assertTrue(ne("400000", 400000))
        self.assertTrue(ne("26.5", "26.50"))
        self.assertFalse(ne("400000", "999999"))
        # dict 'range' values: identical finite structure passes; any
        # leaf difference, nonfinite leaf, or empty range fails
        rng = {"1": "19893", "12": "13340"}
        self.assertTrue(ne(rng, dict(rng)))
        self.assertFalse(ne(rng, {**rng, "12": "1"}))
        self.assertFalse(ne({"1": "NaN"}, {"1": "NaN"}))
        self.assertFalse(ne({}, {}))

    def test_valid_direct_registration_verified(self):
        # P1-1 positive: a fully bound claim (value+unit+locator+pdf hash)
        # through an accepted catalog verifies end to end.
        fx = PackFixture(self)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          verification="claim_supported")
        rows = core.checks(root, p)
        self.assertEqual([], self._stat_issues(rows))

    def test_claim_check_requires_accepted_catalog(self):
        # P1-4: the same valid claim under a caller-claimed catalog —
        # claim_supported cannot substantiate without acceptance.
        fx = PackFixture(self, accepted=False)
        self._patch_ctx(fx)
        root, src_name = self._workspace(fx)
        p = self._project(fx, src_name, fx.key(0),
                          verification="claim_supported")
        rows = core.checks(root, p)
        issues = self._stat_issues(rows)
        self.assertTrue(issues)
        self.assertEqual("statistical_ref_invalid",
                         issues[0]["check_id"])


class SnapshotIndependenceTests(unittest.TestCase):
    """D08/P1-3: registered snapshots stay verifiable after the live
    pack is deleted or mutated — through the P4 hooks, not just on disk."""

    def _register(self, fx):
        resolved = prov.resolve_reference(
            fx.key(0), catalog=fx.catalog_obj, verified_index=fx.index)
        self.assertIsNotNone(resolved)
        snap_dir = fx.dir / "verifier-snapshots"
        receipt = prov.register_snapshot(resolved, sources_dir=snap_dir)
        return receipt

    def _snapshot_workspace(self, snap_path):
        root = Path(tempfile.mkdtemp(prefix="gg-p4-snap-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        name = snap_path.name
        (root / name).write_bytes(snap_path.read_bytes())
        return root, name

    def test_snapshot_survives_pack_deletion_and_mutation(self):
        fx = PackFixture(self)
        receipt = self._register(fx)
        snap_path = Path(receipt.path)
        self.assertTrue(snap_path.exists())
        before = snap_path.read_bytes()
        self.assertEqual(receipt.sha256,
                         hashlib.sha256(before).hexdigest())

        # live pack deleted + pin drifted — snapshot is untouched
        fx.records_path.unlink()
        fx.mutate_pin(self, pack_revision="t2")
        self.assertEqual(before, snap_path.read_bytes())
        snap = json.loads(before.decode("utf-8"))
        self.assertEqual(fx.key(0), snap["audit_key"])
        self.assertEqual("400000", snap["record"]["metrics"]["income"])
        # the frozen physical line is re-hashable to the audit key
        self.assertTrue(snap.get("raw_line_b64"))

        # same snapshot path holding different bytes is a conflict —
        # re-registering the same observation never silently rewrites it
        Path(receipt.path).write_text("{}", encoding="utf-8")
        with self.assertRaises(prov.SnapshotConflictError):
            prov.register_snapshot(
                prov.resolve_reference(
                    fx.key(0), catalog=fx.catalog_obj,
                    verified_index=fx.index)
                or self._resolved_again(fx),
                sources_dir=Path(receipt.path).parent)

    def _resolved_again(self, fx):
        # index still in memory even though the file is gone
        return prov.resolve_reference(
            fx.key(0), catalog=fx.catalog_obj, verified_index=fx.index)

    def test_historical_fact_accepted_via_snapshot_receipt(self):
        # P1-3 core proof: delete the live pack, then a fact whose ref
        # carries the registered snapshot receipt still verifies through
        # core.checks() — no live index involved.
        fx = PackFixture(self)
        receipt = self._register(fx)
        fx.records_path.unlink()
        fx.mutate_pin(self, records_file_sha256="d" * 64)
        root, name = self._snapshot_workspace(Path(receipt.path))
        real = research.resolver_context
        research.resolver_context = lambda **kw: {
            "indexes": {}, "catalog": fx.catalog_obj,
            "catalog_accepted": True}
        self.addCleanup(
            lambda: setattr(research, "resolver_context", real))
        ref = {
            "id": "s1", "locator": "물리행 1", "revision": 1,
            "audit_key": fx.key(0),
            "source_pdf_sha256": TEST_PDF_SHA,
            "snapshot_receipt": {"path": str(root / name),
                                 "sha256": receipt.sha256},
        }
        fact = {
            "id": "f1", "field_id": "income", "kind": "observation",
            "value": "400000", "unit": "원", "value_type": "decimal",
            "period": 2024, "scope": "전국", "answer_state": "provided",
            "verification": "claim_supported",
            "source_refs": [ref]}
        p = {
            "revision": 1,
            "sources": {"s1": {
                "path": name, "hash": receipt.sha256, "revision": 1,
                "claims": {}, "claim_review": True}},
            "facts": {"f1": fact},
            "reviews": {}, "sections": {}, "rules": {}, "questions": {},
        }
        rows = core.checks(root, p)
        stat = [r for r in rows
                if r["check_id"].startswith("statistical_ref")]
        self.assertEqual([], stat)

    def test_snapshot_path_rejects_fabricated_receipts(self):
        fx = PackFixture(self)
        receipt = self._register(fx)
        fx.records_path.unlink()
        ctx = {"indexes": {}, "catalog": fx.catalog_obj,
               "catalog_accepted": True}
        # wrong bound sha256
        bad = research.audit_source_ref(
            None,
            {"audit_key": fx.key(0),
             "snapshot_receipt": {"path": receipt.path, "sha256": "0" * 64}},
            context=ctx)
        self.assertEqual("invalid", bad["status"])
        # different audit key than the snapshot binds
        other_key = dict(fx.key(0))
        other_key["physical_jsonl_line_1based"] = 2
        bad = research.audit_source_ref(
            None,
            {"audit_key": other_key,
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": receipt.sha256}},
            context=ctx)
        self.assertEqual("invalid", bad["status"])
        self.assertIn("audit_key", bad["reason"])
        # no catalog context — nothing anchors the historical status
        bad = research.audit_source_ref(
            None,
            {"audit_key": fx.key(0),
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": receipt.sha256}},
            context={"indexes": {}, "catalog": None})
        self.assertEqual("unresolved", bad["status"])

    def test_explicit_snapshot_verified_even_when_live_valid(self):
        # SCC4/D07(c): with the live pack INTACT and resolving, a ref
        # carrying an explicit snapshot_receipt is still routed through
        # the snapshot validator — a bound registered receipt can never
        # be bypassed by (or rescued by) live resolution.
        fx = PackFixture(self)
        ctx = fx.context()   # live index + accepted catalog — VALID
        # missing snapshot file: live resolution cannot rescue it
        v = research.audit_source_ref(
            None,
            {"audit_key": fx.key(0),
             "snapshot_receipt": {"path": str(fx.dir / "nonexistent"),
                                  "sha256": "0" * 64}},
            context=ctx)
        self.assertEqual("unresolved", v["status"])
        self.assertEqual("snapshot_missing", v["reason"])
        # wrong bound sha256 against an existing snapshot
        receipt = self._register(fx)
        v = research.audit_source_ref(
            None,
            {"audit_key": fx.key(0),
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": "0" * 64}},
            context=ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("sha256", v["reason"])
        # changed bound observation: the receipt binds keys[0] — declaring
        # keys[1] rejects even though keys[1] resolves live
        v = research.audit_source_ref(
            None,
            {"audit_key": fx.key(1),
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": receipt.sha256}},
            context=ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("audit_key", v["reason"])
        # and the intact receipt still verifies through the same path
        v = research.audit_source_ref(
            {"hash": receipt.sha256},
            {"audit_key": fx.key(0),
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": receipt.sha256}},
            context=ctx)
        self.assertEqual("resolved", v["status"])

    def _tampered_snapshot(self, receipt, mutate):
        """Copy a registered snapshot, apply ``mutate`` to its body, and
        return ``{path, sha256}`` bound to the TAMPERED file — the bound
        sha is honest so only the payload check can reject it."""
        body = json.loads(
            Path(receipt.path).read_text(encoding="utf-8"))
        mutate(body)
        tmp = Path(tempfile.mkdtemp(prefix="gg-p4-tamper-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        p = tmp / "snapshot.json"
        p.write_text(json.dumps(body, ensure_ascii=False),
                     encoding="utf-8")
        return {"path": str(p), "sha256": _file_sha(p)}

    def test_snapshot_payload_tamper_rejected_with_live_pack(self):
        # B-TEST-1/D07(c): original raw bytes + key, live pack VALID —
        # a snapshot whose frozen record / raw_line_b64 was altered
        # must be rejected through the explicit receipt path.
        fx = PackFixture(self)
        ctx = fx.context()   # live index + accepted catalog — VALID
        receipt = self._register(fx)
        key = fx.key(0)

        # (1) record payload altered, raw_line_b64 untouched
        tampered = self._tampered_snapshot(
            receipt,
            lambda b: b["record"]["metrics"].__setitem__(
                "income", "999999"))
        v = research.audit_source_ref(
            None,
            {"audit_key": key, "snapshot_receipt": tampered},
            context=ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("raw line", v["reason"])

        # (2) raw_line_b64 swapped to different bytes — does not
        # re-hash to the audit key's raw_line_sha256
        other_raw = json.dumps(
            {"record_id": "forged", "metrics": {"income": "1"},
             "locator": {}, "basis": {}, "caveats": []},
            ensure_ascii=False).encode("utf-8")
        tampered = self._tampered_snapshot(
            receipt,
            lambda b: b.__setitem__(
                "raw_line_b64",
                base64.b64encode(other_raw).decode("ascii")))
        v = research.audit_source_ref(
            None,
            {"audit_key": key, "snapshot_receipt": tampered},
            context=ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("raw_line", v["reason"])

        # (3) raw-line hash mismatch via malformed base64 payload
        tampered = self._tampered_snapshot(
            receipt,
            lambda b: b.__setitem__("raw_line_b64", "!!!not-b64!!!"))
        v = research.audit_source_ref(
            None,
            {"audit_key": key, "snapshot_receipt": tampered},
            context=ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("raw_line_b64", v["reason"])

        # control: the untampered receipt still resolves on the same
        # live-valid context
        v = research.audit_source_ref(
            {"hash": receipt.sha256},
            {"audit_key": key,
             "snapshot_receipt": {"path": receipt.path,
                                  "sha256": receipt.sha256}},
            context=ctx)
        self.assertEqual("resolved", v["status"])


class FinanceClaimTests(unittest.TestCase):
    """P1-1: the finance adapter cross-checks economic claims, not just
    ref strings — wired into the real admission guard."""

    def _cell(self, fx, *, value="400000", unit="원",
              pdf=TEST_PDF_SHA, locator=None):
        rec = {
            "status": "research", "unit": unit, "period": "2024",
            "source_ref": "%s@%s#%s" % (
                TEST_PACK, TEST_REV, RECORDS[0]["record_id"]),
            "meaning_id": "income", "value": value,
            "source_pdf_sha256": pdf,
            "source_locator": (locator if locator is not None
                               else "물리행 1"),
        }
        return {"map_action": "clear", "kind": "number", "economic": rec}

    def _context(self, fx):
        out = finance.resolve_economic_source_refs(
            [self._cell(fx)["economic"]["source_ref"]],
            resolver=fx.context())
        return {"resolved_source_refs": out["resolved_source_refs"],
                "p4_resolver": fx.context()}

    def test_valid_economic_claim_passes(self):
        fx = PackFixture(self)
        wb = {"economic_classification": "complete",
              "cells": [self._cell(fx)]}
        finance.require_classified_workbook_inputs(
            wb, context=self._context(fx))

    def test_wrong_economic_value_blocked(self):
        fx = PackFixture(self)
        wb = {"economic_classification": "complete",
              "cells": [self._cell(fx, value="999999")]}
        with self.assertRaises(ValueError) as cm:
            finance.require_classified_workbook_inputs(
                wb, context=self._context(fx))
        self.assertEqual("STATISTICAL_CLAIM_UNVERIFIED",
                         str(cm.exception))

    def test_wrong_economic_unit_blocked(self):
        fx = PackFixture(self)
        wb = {"economic_classification": "complete",
              "cells": [self._cell(fx, unit="USD")]}
        with self.assertRaises(ValueError) as cm:
            finance.require_classified_workbook_inputs(
                wb, context=self._context(fx))
        self.assertEqual("STATISTICAL_CLAIM_UNVERIFIED",
                         str(cm.exception))

    def test_statistical_claim_needs_resolver(self):
        fx = PackFixture(self)
        wb = {"economic_classification": "complete",
              "cells": [self._cell(fx)]}
        ctx = dict(self._context(fx))
        ctx.pop("p4_resolver")
        with self.assertRaises(ValueError) as cm:
            finance.require_classified_workbook_inputs(wb, context=ctx)
        self.assertEqual("MAP_SLOT_UNRESOLVED", str(cm.exception))

    def test_admission_review_flags_bad_claim(self):
        fx = PackFixture(self)
        spec = {"workbook_inputs": {
            "economic_classification": "complete",
            "cells": [dict(self._cell(fx, value="1"), cell="B2")]}}
        issues = finance.validate_economic_inputs(
            spec, context=self._context(fx), purpose="admission")
        stat = [i for i in issues
                if i["code"] == "STATISTICAL_CLAIM_UNVERIFIED"]
        self.assertTrue(stat)
        self.assertEqual("blocked", stat[0]["status"])


class UnanchoredRefTests(unittest.TestCase):
    """D09: fabricated/unanchored manifest·receipt strings fail closed."""

    def test_fabricated_pack_ref_rejected(self):
        fx = PackFixture(self)
        out = research.resolve_statistical_refs(
            ["fabricated.pack@t9#whatever"], context=fx.context())
        self.assertEqual([], out["resolved_source_refs"])
        self.assertEqual("invalid", out["conflicts"][0]["kind"])
        self.assertIn("unpinned", out["conflicts"][0]["reason"])

    def test_record_id_without_audit_key_stays_unresolved(self):
        fx = PackFixture(self)
        # correct pack/rev but a record_id that resolves to nothing
        out = research.resolve_statistical_refs(
            ["%s@%s#nonexistent.record" % (TEST_PACK, TEST_REV)],
            context=fx.context())
        self.assertEqual([], out["resolved_source_refs"])
        self.assertEqual("unresolved", out["conflicts"][0]["kind"])

    def test_plain_strings_are_not_statistical_claims(self):
        fx = PackFixture(self)
        out = research.resolve_statistical_refs(
            ["src:a", "synthetic:행복농장-원답변"], context=fx.context())
        self.assertEqual(["src:a", "synthetic:행복농장-원답변"],
                         out["resolved_source_refs"])
        self.assertEqual([], out["conflicts"])

    def test_finance_adapter_never_resolves_fabricated_refs(self):
        fx = PackFixture(self)
        out = finance.resolve_economic_source_refs(
            ["fabricated.pack@t9#x", "src:answer-1"],
            resolver=fx.context())
        self.assertEqual(["src:answer-1"], out["resolved_source_refs"])
        self.assertEqual(1, len(out["conflicts"]))
        # a fabricated ref absent from resolved_source_refs hits the
        # existing MAP_SLOT_UNRESOLVED guard — fail closed
        workbook = {
            "economic_classification": "complete",
            "cells": [{
                "map_action": "clear", "kind": "number",
                "economic": {
                    "status": "research", "unit": "원", "period": "2024",
                    "source_ref": "fabricated.pack@t9#x",
                    "meaning_id": "income", "value": "1"}}],
        }
        with self.assertRaises(ValueError) as cm:
            finance.require_classified_workbook_inputs(
                workbook,
                context={"resolved_source_refs":
                         out["resolved_source_refs"]})
        self.assertEqual("MAP_SLOT_UNRESOLVED", str(cm.exception))

    def test_finance_adapter_resolves_anchored_ref(self):
        fx = PackFixture(self)
        ref = "%s@%s#%s" % (TEST_PACK, TEST_REV, RECORDS[0]["record_id"])
        out = finance.resolve_economic_source_refs(
            [ref], resolver=fx.context())
        self.assertEqual([ref], out["resolved_source_refs"])
        self.assertEqual([], out["conflicts"])

    def test_school_validate_rejects_fabricated_stat_ref(self):
        fx = PackFixture(self)
        spec = {
            "profile": "school_17_sheet_v1",
            "source_refs": ["fabricated.pack@t9#x"],
            "unit": "천원", "quantity_unit": "kg",
            "production_evidence": {},
        }
        with self.assertRaises(ValueError) as cm:
            sx.validate(spec, resolver=fx.context())
        self.assertIn("통계 근거 참조 해석 실패", str(cm.exception))

    def test_school_labels_annotate_physical_line(self):
        fx = PackFixture(self)
        ref = "%s@%s#%s" % (TEST_PACK, TEST_REV, RECORDS[0]["record_id"])
        labels = sx.source_ref_labels([ref, "src:answer-1"],
                                      resolver=fx.context())
        self.assertEqual("%s (물리행 1)" % ref, labels[0])
        self.assertEqual("src:answer-1", labels[1])

    def test_core_hook_fails_closed_without_p4_layer(self):
        # _statistical_ref_verdict never raises: simulated absence of the
        # P4 module must degrade to invalid (blocked), not silent pass.
        real_import = __import__

        def deny_p4(name, *args, **kwargs):
            if name == "gg_rda_research":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        import builtins
        old = builtins.__import__
        builtins.__import__ = deny_p4
        try:
            verdict = core._statistical_ref_verdict(
                None, {"audit_key": {"pack_id": "x"}})
        finally:
            builtins.__import__ = old
        self.assertEqual("invalid", verdict["status"])


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AcceptedCatalogRealPackTests(unittest.TestCase):
    """P2-8 bounded positive — exercises the ACCEPTED catalog-lane output
    (frozen sibling root ``work/g005-p4-source-catalog-transform-
    corrections-1/``, bundle pin ``1778fd02…``) READ-ONLY as the
    receipt-anchored observation catalog for a real
    ``rda.income.national.2024`` observation.

    The accepted catalog intentionally ships NO per-observation
    ``observation-catalog.json`` (the P2-8 gap): this test exercises the
    accepted input — it does NOT assert an end-to-end production
    lifecycle.  Skips when the accepted tree is absent."""

    CATALOG_ROOT = KNUAF_DOC.parents[3] / \
        "g005-p4-source-catalog-transform-corrections-1"
    PACK_ID = "rda.income.national.2024"
    PACK_REL = "common/rda-income-national-2024"
    # coordinator-pinned sha256 of the accepted bundle's output-hashes
    BUNDLE_PIN = ("1778fd02fc2286ac40ed86890d3d0dbc2e10acf344afeacb"
                  "5fcb2bee0a6698f4")
    # first verified_observation row of the accepted manifest
    VERIFIED_LINE = 1
    VERIFIED_PDF = ("0bd85164ad387228e7034deb502f3f436a05336e1cf2c30"
                    "326a5257fb3dff6ab")

    def setUp(self):
        self.ohashes = self.CATALOG_ROOT / "output-hashes.json"
        self.manifest_path = (
            self.CATALOG_ROOT / "references/benchmark-packs"
            / self.PACK_REL / "manifest.json")
        if not self.ohashes.is_file() \
                or not self.manifest_path.is_file():
            self.skipTest("accepted catalog tree not present")
        # bind to the pinned bundle: the frozen manifest must hash to the
        # acceptance pin, and the consumed manifest.json must hash to the
        # entry the frozen bundle declares for it — no drift tolerated.
        self.assertEqual(self.BUNDLE_PIN, _file_sha(self.ohashes))
        listing = json.loads(self.ohashes.read_text(encoding="utf-8"))
        rel = self.manifest_path.relative_to(self.CATALOG_ROOT) \
            .as_posix()
        declared = {e["path"]: e for e in listing["entries"]}
        self.assertIn(rel, declared)
        self.assertEqual("file", declared[rel]["type"])
        self.assertEqual(declared[rel]["sha256"],
                         _file_sha(self.manifest_path))
        self.manifest = json.loads(
            self.manifest_path.read_text(encoding="utf-8"))
        pin = prov.PINNED_PACKS[self.PACK_ID]
        self.assertEqual(pin["records_file_sha256"],
                         self.manifest["records_file_sha256"])
        packs_base = KNUAF_DOC / "references/benchmark-packs"
        records_path = packs_base / self.PACK_REL / "records.jsonl"
        self.index = prov.build_audit_index(
            records_path, self.PACK_ID, pin["records_file_sha256"])
        self.accepted = research.accept_catalog(
            {prov.normalize_audit_key(r["audit_key"]): r
             for r in self.manifest["rows"]},
            verified_indexes={self.PACK_ID: self.index},
            authority="accepted-catalog-20260921/" + self.PACK_ID)
        self.ctx = research.resolver_context(
            packs_dir=packs_base, catalog=self.accepted,
            pack_ids={self.PACK_ID})
        self.rows = {r["physical_line"]: r
                     for r in self.manifest["rows"]}
        # the registry revision is the pinned pack_revision — the catalog
        # manifest's own "pack_revision" field is its schema stamp, not
        # the pinned registry value apply() CAS-checks against.
        self.revision = prov.PINNED_PACKS[self.PACK_ID]["pack_revision"]
        self.record = json.loads(
            records_path.read_text(encoding="utf-8")
            .splitlines()[self.VERIFIED_LINE - 1])
        self.source = {"hash": self.manifest["records_file_sha256"]}
        self.ref = {
            "audit_key": self.rows[self.VERIFIED_LINE]["audit_key"],
            "locator": {"pdf_page": self.record["locator"]["pdf_page"]},
            "source_pdf_sha256": self.VERIFIED_PDF,
        }

    def test_real_verified_claim_verifies(self):
        claim = {"metric": "income",
                 "value": self.record["metrics"]["income"],
                 "unit": self.record["basis"]["currency"],
                 "source_pdf_sha256": self.VERIFIED_PDF}
        v = research.audit_source_ref(
            self.source, self.ref, claim=claim, context=self.ctx)
        self.assertEqual("resolved", v["status"])
        self.assertTrue(v["claim_verified"])
        self.assertTrue(v["catalog_accepted"])
        self.assertTrue(v["receipt_verified"])
        self.assertEqual("verified_observation", v["catalog_status"])

    def test_real_wrong_value_rejected(self):
        claim = {"metric": "income", "value": "999999",
                 "unit": self.record["basis"]["currency"],
                 "source_pdf_sha256": self.VERIFIED_PDF}
        v = research.audit_source_ref(
            self.source, self.ref, claim=claim, context=self.ctx)
        self.assertEqual("invalid", v["status"])
        self.assertIn("claim value", v["reason"])

    def test_real_propose_promotes_only_verified(self):
        prop = research.propose(
            self.revision, {"metric": "income"},
            self.ref["audit_key"], context=self.ctx)
        self.assertEqual("proposal", prop["status"])
        self.assertTrue(prop["promotable"])
        self.assertTrue(prop["receipt_verified"])
        # a quarantined real row resolves but never promotes
        q = next(r for r in self.manifest["rows"]
                 if r["catalog_status"] == "quarantined")
        qprop = research.propose(
            self.revision, {"metric": "income"},
            q["audit_key"], context=self.ctx)
        self.assertNotEqual(True, qprop.get("promotable"))

    def test_real_apply_roundtrip_on_fresh_bytes(self):
        target = {"id": "econ_cell_1", "metric": "income",
                  "answer_state": "not_provided"}
        prop = research.propose(
            self.revision, target,
            self.ref["audit_key"], context=self.ctx)
        self.assertEqual("proposal", prop["status"])
        out = research.apply(
            prop, expected_revision=self.revision,
            target_current=target,
            packs_dir=KNUAF_DOC / "references/benchmark-packs")
        self.assertEqual("ready", out["status"])

    def _forged_real_catalog(self):
        """A real accepted key — a QUARANTINED row — forged to
        ``verified_observation`` with a fabricated but well-shaped
        receipt (SCC1/P1-4 regression (a))."""
        entries = {prov.normalize_audit_key(r["audit_key"]): dict(r)
                   for r in self.manifest["rows"]}
        qkey = next(
            prov.normalize_audit_key(r["audit_key"])
            for r in self.manifest["rows"]
            if r["catalog_status"] == "quarantined")
        forged_entry = dict(entries[qkey])
        forged_entry["catalog_status"] = "verified_observation"
        forged_entry["source_observation"] = {
            "observation_status": "verified",
            "source_pdf_sha256": "a" * 64,
            "prep_receipt_ref": "fabricated://receipt",
        }
        entries[qkey] = forged_entry
        return entries, qkey

    def test_scc1_forged_real_key_rejected_via_public_accept(self):
        entries, _ = self._forged_real_catalog()
        with self.assertRaises(ValueError) as cm:
            research.accept_catalog(
                entries,
                verified_indexes={self.PACK_ID: self.index},
                authority="accepted-catalog-20260921/" + self.PACK_ID)
        self.assertIn("unauthenticated", str(cm.exception))

    def test_scc1_copied_public_pin_cannot_mint_wrapper(self):
        entries, qkey = self._forged_real_catalog()
        real_pin = research.PINNED_CATALOG_AUTHORITIES[
            "accepted-catalog-20260921/" + self.PACK_ID]
        # the old mint route is gone — the constructor takes no digest
        with self.assertRaises(TypeError):
            research.AcceptedCatalog(entries, real_pin)
        # and a wrapper over forged entries derives its digest from
        # content — the copied pin is never consulted
        forged_cat = research.AcceptedCatalog(entries)
        self.assertFalse(forged_cat.accepted)
        self.assertNotEqual(real_pin, forged_cat.digest)
        packs_base = KNUAF_DOC / "references/benchmark-packs"
        ctx = research.resolver_context(
            packs_dir=packs_base, catalog=forged_cat,
            pack_ids={self.PACK_ID})
        prop = research.propose(
            self.revision, {"metric": "income"},
            prov.audit_key_dict(qkey), context=ctx)
        self.assertNotEqual(True, prop.get("catalog_accepted"))
        self.assertNotEqual(True, prop.get("promotable"))

    def test_scc1_post_acceptance_mutation_self_invalidates(self):
        entries = {prov.normalize_audit_key(r["audit_key"]): dict(r)
                   for r in self.manifest["rows"]}
        cat = research.accept_catalog(
            entries,
            verified_indexes={self.PACK_ID: self.index},
            authority="accepted-catalog-20260921/" + self.PACK_ID)
        self.assertTrue(cat.accepted)
        packs_base = KNUAF_DOC / "references/benchmark-packs"
        ctx = research.resolver_context(
            packs_dir=packs_base, catalog=cat, pack_ids={self.PACK_ID})
        # mutate the CALLER-ALIASED dict after acceptance — acceptance
        # is recomputed from live content, so it cannot be retained
        key = next(iter(entries))
        entries[key]["catalog_status"] = "quarantined"
        self.assertFalse(cat.accepted)
        v = research.audit_source_ref(
            self.source, self.ref, context=ctx)
        self.assertFalse(v["catalog_accepted"])
        # mutating through the wrapper's own .entries view likewise
        cat2 = research.accept_catalog(
            {prov.normalize_audit_key(r["audit_key"]): dict(r)
             for r in self.manifest["rows"]},
            verified_indexes={self.PACK_ID: self.index},
            authority="accepted-catalog-20260921/" + self.PACK_ID)
        self.assertTrue(cat2.accepted)
        k2 = next(iter(cat2.entries))
        cat2.entries[k2]["catalog_status"] = "exploratory"
        self.assertFalse(cat2.accepted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
