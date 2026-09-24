"""G006/P5 Lane C — I06 integrated flow on the real core.

SPEC section 6 bounded runtime context, exercised end-to-end with REAL
P3/P4 machinery — no mocks:

  intake      — the P5 scanner over a real external root (sealed receipt
                + diagnostics written through the held capability)
  lookup      — the real pack's audit index rebuilt from pinned bytes;
                a verified row's audit_key resolved against the
                constructed AcceptedCatalog
  select      — research.propose binds value/unit/period to the verified
                physical row
  proposal    — PROPOSAL_SCHEMA object with promotable=True
  apply       — research.apply returns the ready payload; the P4->core
                bridge hands ready["fact"] to gg_core.apply under CAS
                (canonical revision, not the pack revision), and the
                pack itself is registered via register_source_snapshot
                (kind=p4_snapshot, catalog/indexes from
                load_accepted_catalog)

I06.2  the committed fact keeps value/unit/period and its source_refs
       resolve to the registered source; core.checks' statistical-ref
       hook cross-verifies the claim against the real verified row.
I06.3  a review bound to the fact is marked stale after an intervening
       revision changes its input fingerprint.
I06.4  a named partial-progress scenario: a rejected change leaves the
       workspace usable and a permitted continuation still commits.
I06.5  diagnostics accuracy — the exact reason/detail strings match the
       actual state (stale revision, mutated pack bytes, answered
       target).
I06.6  no synthetic bypass: the catalog is authenticated through
       accept_catalog with a pinned authority, indexes are rebuilt from
       real pack bytes, and research.apply re-reads live bytes.

Skips when the accepted g005 catalog tree is absent (same bound fixture
the parent real-pack tests use).
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KNUAF_DOC = TESTS.parent
SCRIPTS = KNUAF_DOC / "scripts"
LANE_ROOT = KNUAF_DOC.parents[2]
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"
PACKS_DIR = KNUAF_DOC / "references" / "benchmark-packs"
ACCEPTED_ROOT = (LANE_ROOT.parent
                 / "g005-p4-source-catalog-transform-corrections-1"
                 / "references" / "benchmark-packs")

sys.path.insert(0, str(SCRIPTS))
import gg_core          # noqa: E402
import gg_lock          # noqa: E402
import gg_reuse         # noqa: E402
import gg_source_intake # noqa: E402
import gg_rda_provenance as prov      # noqa: E402
import gg_rda_research as research    # noqa: E402

PACK_ID = "rda.income.national.2024"
PACK_REL = "common/rda-income-national-2024"
AUTHORITY = "accepted-catalog-20260921/" + PACK_ID
CV = "p5-classifier/1"
POLICY = {"classification_version": CV,
          "include": ["**/*"], "always_hash": ["**/*"],
          "document_extensions": [".md", ".txt"],
          "exclude": []}
GUIDE_KEY = {"kind": "delimited",
             "value": "official-writing-guide:toc-rules"}
VERIFIED_LINE = 1


def sha(b):
    return hashlib.sha256(b).hexdigest()


def runtime_root():
    return Path(os.path.normpath(str(KNUAF_DOC.absolute())))


def key_catalog(stat_ser=None):
    kc = {
        "writing_rule": {
            GUIDE_KEY["value"]: {"authority": "verified",
                                 "catalog_digest": None,
                                 "receipt_sha256": None}},
        "narrative_reference": {},
        "template_structure": {},
        "statistical_observation": {},
    }
    if stat_ser is not None:
        kc["statistical_observation"][stat_ser] = {
            "authority": "verified", "catalog_digest": None,
            "receipt_sha256": None}
    return kc


def write_export(tmp, pack_id):
    """A real p5-catalog-export/1 built from the accepted manifest rows
    (identical construction to test_gg_source_register.write_export)."""
    rel = {PACK_ID: PACK_REL}[pack_id]
    manifest = json.loads(
        (ACCEPTED_ROOT / rel / "manifest.json").read_text())
    entries = {json.dumps(r["audit_key"], sort_keys=True,
                          ensure_ascii=False): r
               for r in manifest["rows"]}
    path = Path(tmp) / ("catalog-%s.json" % pack_id)
    path.write_text(json.dumps(
        {"schema": "p5-catalog-export/1",
         "authority": AUTHORITY,
         "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path, manifest


class TestI06Flow(unittest.TestCase):
    """One shared fixture per test: workspace + real pack context."""

    def setUp(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name) / "ws"
        self.root.mkdir()
        gg_core.init(self.root)
        self.reg = gg_reuse.load_registry(str(REGISTRY_PATH))
        export, self.manifest = write_export(self.td.name, PACK_ID)
        self.catalog, self.indexes = gg_reuse.load_accepted_catalog(
            export, registry=self.reg, packs_dir=PACKS_DIR)
        self.assertTrue(self.catalog.accepted)
        self.ctx = research.resolver_context(
            packs_dir=PACKS_DIR, catalog=self.catalog,
            pack_ids={PACK_ID})
        pin = prov.PINNED_PACKS[PACK_ID]
        self.revision = pin["pack_revision"]
        records_path = PACKS_DIR / PACK_REL / "records.jsonl"
        self.record = json.loads(
            records_path.read_text(encoding="utf-8")
            .splitlines()[VERIFIED_LINE - 1])
        self.row = next(r for r in self.manifest["rows"]
                        if r["physical_line"] == VERIFIED_LINE)
        self.audit_key = self.row["audit_key"]

    def _intake_and_register_source(self):
        """P5 legs: scan a real external root, publish the sealed
        receipt, register the source (scanned_external)."""
        ext = Path(self.td.name) / "ext"
        ext.mkdir()
        (ext / "doc.md").write_text("# 외부 문서\n본문\n",
                                    encoding="utf-8")
        kc = key_catalog()
        with gg_lock.Lock(self.root) as cap:
            scan = gg_source_intake.run_source_scan(
                ext, root=self.root, classification_version=CV,
                registry=self.reg, key_catalog=kc, scan_policy=POLICY,
                requested_use={"kind": "writing_rule",
                               "keys": [GUIDE_KEY]})
            assert "sealed" in scan, scan
            gg_source_intake.write_scan_receipt(
                self.root, scan, capability=cap)
        f = ext / "doc.md"
        data = f.read_bytes()
        sel = {
            "kind": "external_document",
            "source_hash": sha(data),
            "scan_id": scan["scan_id"],
            "byte_source": {"kind": "scanned_external",
                            "root": str(ext.resolve()),
                            "scan_policy_digest":
                                scan["sealed"]["scan_policy_digest"]},
            "files": [{"relative_id": "doc.md",
                       "content_sha256": sha(data),
                       "bytes": len(data)}],
            "registry_digest": self.reg.registry_digest,
            "requested_use": {"kind": "writing_rule",
                              "keys": [GUIDE_KEY]},
            "excerpt": None,
            "external_identity": {
                "original_basename": "doc.md",
                "original_sha256": sha(data),
                "observed_utc": "2026-09-22T00:00:00Z",
                "role": "user_document",
                "class": "external"},
        }
        with gg_lock.Lock(self.root) as cap:
            p = gg_core.register_source_snapshot(
                self.root, sel, 0, capability=cap,
                request_id="i06-src", registry=self.reg,
                runtime_root=runtime_root(), key_catalog=kc)
        return p, scan

    def _propose_apply(self, target=None, metric="income"):
        """lookup -> select -> proposal -> P4 apply -> ready payload."""
        target = target or {"id": "econ_cell_1", "metric": metric,
                            "answer_state": "not_provided"}
        prop = research.propose(self.revision, target, self.audit_key,
                                context=self.ctx)
        out = research.apply(prop, expected_revision=self.revision,
                             target_current=target, packs_dir=PACKS_DIR)
        return prop, out, target

    def _commit_fact(self, ready, request_id, rev):
        """The P4->core bridge: ready["fact"] -> gg_core.apply ops."""
        # register the pack records file as a canonical source first so
        # the fact's source_refs resolve inside the workspace.
        rec_rel = "work-records.jsonl"
        (self.root / rec_rel).write_bytes(
            (PACKS_DIR / PACK_REL / "records.jsonl").read_bytes())
        fact = {
            "id": "f-income", "field_id": "income",
            "kind": "observation",
            "value": ready["fact"]["value"],
            "unit": ready["fact"]["unit"],
            "value_type": "decimal",
            "period": ready["fact"]["period"],
            "scope": self.record["region"]["name"],
            "answer_state": "provided",
            # bridge mapping: P4's "proposed_research" is not a canonical
            # token — the audited verified_observation claim maps to the
            # canonical claim_supported
            "verification": "claim_supported",
            "source_refs": [{
                "id": "s-pack",
                "locator": {"pdf_page":
                            self.record["locator"]["pdf_page"]},
                "revision": 1,
                "audit_key": ready["fact"]["audit_key"],
                "source_pdf_sha256":
                    self.row["source_observation"]["source_pdf_sha256"],
            }],
        }
        change = {"request_id": request_id,
                  "ops": [
                      {"collection": "sources",
                       "value": {"id": "s-pack", "path": rec_rel,
                                 "revision": 1, "claims": {},
                                 "claim_review": True}},
                      {"collection": "facts", "value": fact}]}
        return gg_core.apply(self.root, change, rev), fact

    # ---------------------------------------------------------------

    def test_i06_1_intake_lookup_propose_apply_register(self):
        """I06.1 — the full chain on real machinery, P4 ready payload
        handed to the canonical path under CAS."""
        p1, scan = self._intake_and_register_source()
        self.assertEqual(p1["revision"], 1)
        prop, out, target = self._propose_apply()
        self.assertEqual("proposal", prop["status"])
        self.assertTrue(prop["promotable"])
        self.assertEqual("ready", out["status"])
        p2, fact = self._commit_fact(out, "i06-fact", 1)
        self.assertEqual(p2["revision"], 2)
        self.assertEqual(p2["facts"]["f-income"]["value"],
                         self.record["metrics"]["income"])
        # the pack itself registers as a p4_snapshot under the
        # constructed catalog — the P5 leg of the same bounded context
        rec_rel = ("references/benchmark-packs/%s/records.jsonl"
                   % PACK_REL)
        fdata = (KNUAF_DOC / rec_rel).read_bytes()
        # the registry's statistical key for this pack
        stat_kref = None
        for e in self.reg.document["entries"]:
            if e["source_id"] == PACK_ID:
                for claim in (e["coverage"]["statistical_observation"]
                              ["keys"]):
                    if claim["authority"] == "verified":
                        stat_kref = claim["key_ref"]
                        break
        sel = {
            "kind": "p4_snapshot",
            "source_hash": sha(fdata),
            "scan_id": None,
            "byte_source": {"kind": "installed_runtime", "root": None,
                            "scan_policy_digest": None},
            "files": [{"relative_id": rec_rel,
                       "content_sha256": sha(fdata),
                       "bytes": len(fdata)}],
            "registry_digest": self.reg.registry_digest,
            "requested_use": {"kind": "statistical_observation",
                              "keys": [stat_kref]},
            "excerpt": None,
            "external_identity": {
                "original_basename": "records.jsonl",
                "original_sha256": sha(fdata),
                "observed_utc": "2026-09-22T00:00:00Z",
                "role": "statistical_pack",
                "class": "common_public_book"},
            "p4_trust": {
                "pack_id": PACK_ID,
                "pack_revision": self.revision,
                "audit_key": dict(stat_kref),
                "catalog_authority": AUTHORITY,
                "catalog_digest": self.catalog.digest,
                "observation_receipt_sha256": "00" * 32},
        }
        kc = key_catalog(
            gg_reuse.canonical_key(stat_kref, "statistical_observation"))
        with gg_lock.Lock(self.root) as cap:
            p3 = gg_core.register_source_snapshot(
                self.root, sel, 2, capability=cap,
                request_id="i06-pack", registry=self.reg,
                runtime_root=runtime_root(), key_catalog=kc,
                catalog=self.catalog, indexes=self.indexes)
        self.assertEqual(p3["revision"], 3)

    def test_i06_2_answers_sources_units_linked(self):
        """I06.2 — the committed fact keeps value/unit/period and its
        source_refs resolve to the registered source; the real checks()
        statistical hook cross-verifies against the verified row."""
        self._intake_and_register_source()
        prop, out, target = self._propose_apply()
        p, fact = self._commit_fact(out, "i06-fact", 1)
        f = p["facts"]["f-income"]
        self.assertEqual(f["value"], self.record["metrics"]["income"])
        self.assertEqual(f["unit"], self.record["basis"]["currency"])
        self.assertEqual(f["period"], self.record["basis"]["year"])
        # source linkage resolves to a real workspace source
        ref = f["source_refs"][0]
        self.assertIn("s-pack", p["sources"])
        self.assertEqual(p["sources"]["s-pack"]["hash"],
                         sha((self.root / "work-records.jsonl")
                             .read_bytes()))
        # the parent's statistical-ref check cross-verifies the claim
        # against the verified row — checks() resolves through
        # resolver_context; bind it to this test's bounded context (the
        # same module-state injection the parent's own tests use)
        real = research.resolver_context
        research.resolver_context = lambda **kw: self.ctx
        self.addCleanup(
            lambda: setattr(research, "resolver_context", real))
        rows = gg_core.checks(self.root, p)
        stat_issues = [r for r in rows
                       if r["check_id"].startswith("statistical_ref")]
        self.assertEqual([], stat_issues)

    def test_i06_3_revision_change_stales_review(self):
        """I06.3 — a review bound to the fact is invalidated (stale)
        once an intervening revision changes its input fingerprint."""
        self._intake_and_register_source()
        prop, out, target = self._propose_apply()
        p, fact = self._commit_fact(out, "i06-fact", 1)
        # a review bound to the fact at revision 2
        (self.root / "review-1.md").write_text(
            "검토 보고서\n", encoding="utf-8")
        fp = gg_core.fingerprint(
            self.root, p, [{"collection": "facts", "id": "f-income"}])
        review = {
            "id": "r-1", "review_kind": "content",
            "author_id": "author-a", "reviewer_id": "rev-b",
            "target_refs": [{"collection": "facts", "id": "f-income"}],
            "input_fingerprint": fp, "input_revision": 3,
            "findings": [], "disposition": "clean", "status": "done",
            "path": "review-1.md", "coverage": {"facts": ["f-income"]},
        }
        p3 = gg_core.apply(
            self.root,
            {"request_id": "i06-review",
             "ops": [{"collection": "reviews", "value": review}]}, 2)
        self.assertEqual(p3["revision"], 3)
        self.assertFalse(p3["reviews"]["r-1"]["stale"])
        # an intervening revision changing the fact invalidates it
        fact2 = dict(p3["facts"]["f-income"])
        fact2["scope"] = "변경된 범위"
        p4 = gg_core.apply(
            self.root,
            {"request_id": "i06-fact2",
             "ops": [{"collection": "facts", "value": fact2}]}, 3)
        self.assertEqual(p4["revision"], 4)
        self.assertTrue(p4["reviews"]["r-1"]["stale"])

    def test_i06_4_partial_progress(self):
        """I06.4 — a rejected change leaves the workspace usable; the
        named remaining operation still completes at the next revision."""
        self._intake_and_register_source()
        prop, out, target = self._propose_apply()
        # a change carrying an outputs op outside adopt_output must be
        # rejected — nothing commits
        bad = {"request_id": "i06-bad",
               "ops": [{"collection": "outputs",
                        "value": {"id": "o-1", "path": "x.pdf",
                                  "format": "pdf", "file_hash": "0" * 64,
                                  "target_refs": [],
                                  "input_fingerprint": "0" * 64}}]}
        with self.assertRaises(gg_core.OperationError) as cm:
            gg_core.apply(self.root, bad, 1)
        self.assertEqual(cm.exception.result["commit_state"],
                         "not_committed")
        self.assertEqual(cm.exception.result["reason"],
                         "adopt_output_required")
        self.assertEqual(gg_core.load(self.root)["revision"], 1)
        # the permitted continuation still completes
        p, fact = self._commit_fact(out, "i06-fact", 1)
        self.assertEqual(p["revision"], 2)

    def test_i06_5_diagnostics_accuracy(self):
        """I06.5 — the exact diagnostic strings match the actual state."""
        prop, out, target = self._propose_apply()
        # stale expected pack revision
        out2 = research.apply(prop, expected_revision="1999",
                              target_current=target, packs_dir=PACKS_DIR)
        self.assertEqual("stale", out2["status"])
        self.assertEqual("expected_revision != bound pack_revision",
                         out2["reason"])
        # already-answered target
        answered = dict(target, answer_state="provided")
        out3 = research.apply(prop, expected_revision=self.revision,
                              target_current=answered, packs_dir=PACKS_DIR)
        self.assertEqual("stale", out3["status"])
        self.assertEqual("target_already_answered", out3["reason"])
        # canonical CAS: stale expected_revision -> revision_conflict
        # naming the ACTUAL current revision
        self._intake_and_register_source()
        _, fact = self._commit_fact(out, "i06-fact", 1)
        with self.assertRaises(gg_core.OperationError) as cm:
            gg_core.apply(self.root,
                          {"request_id": "i06-stale",
                           "ops": [{"collection": "facts",
                                    "value": dict(fact, id="f-x")}]},
                          1)
        self.assertEqual(cm.exception.result["reason"],
                         "revision_conflict")
        self.assertEqual(cm.exception.result["revision"], 2)

    def test_i06_6_no_synthetic_bypass(self):
        """I06.6 — the real verification chain is not bypassed: the
        catalog is authenticated, indexes rebuilt from real bytes, and
        apply re-reads live pack bytes (mutation is detected)."""
        self.assertTrue(self.catalog.accepted)
        self.assertIn(PACK_ID, self.indexes)
        # a mirrored packs dir — the live-byte mutation happens on the
        # COPY; the installed tree is never touched
        mirror = Path(self.td.name) / "packs-mirror" / PACK_REL
        mirror.mkdir(parents=True)
        (mirror / "records.jsonl").write_bytes(
            (PACKS_DIR / PACK_REL / "records.jsonl").read_bytes())
        packs = mirror.parent.parent   # the dir CONTAINING common/...
        ctx = research.resolver_context(
            packs_dir=packs, catalog=self.catalog, pack_ids={PACK_ID})
        target = {"id": "t", "metric": "income",
                  "answer_state": "not_provided"}
        prop = research.propose(self.revision, target, self.audit_key,
                                context=ctx)
        out = research.apply(prop, expected_revision=self.revision,
                             target_current=target, packs_dir=packs)
        self.assertEqual("ready", out["status"])
        # mutate the live pack bytes — apply must detect it on re-read
        (mirror / "records.jsonl").write_bytes(b'{"mutated": true}\n')
        out2 = research.apply(prop, expected_revision=self.revision,
                              target_current=target, packs_dir=packs)
        self.assertEqual("stale", out2["status"])
        self.assertEqual("pack_bytes_changed", out2["reason"])
        # a fabricated claim value never verifies (real row check)
        v = research.audit_source_ref(
            {"hash": self.manifest["records_file_sha256"]},
            {"audit_key": self.audit_key,
             "source_pdf_sha256":
                 self.row["source_observation"]["source_pdf_sha256"]},
            claim={"metric": "income", "value": "999999",
                   "unit": self.record["basis"]["currency"]},
            context=self.ctx)
        self.assertEqual("invalid", v["status"])


if __name__ == "__main__":
    unittest.main()
