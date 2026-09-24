#!/usr/bin/env python3
"""Lane A reason/status/exit totality + boundary-discipline tests.

Every verdict reason must come from the frozen closed set (API §1.5),
its class must be unique and the exit total; loader/caller/live-trust/
context boundaries are exercised separately so none is conflated.

Run: python3 -B tests/test_gg_reuse_reasons.py
"""
import hashlib
import json
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
import gg_reuse  # noqa: E402
import gg_rda_research as research  # noqa: E402

SEO_SHA = ("457249255929275b3ee55383bdc36897274f08933af3536866a652b293"
           "46cd3a")
NAT_SHA = ("0bd85164ad387228e7034deb502f3f436a05336e1cf2c30326a5257f"
           "b3dff6ab")
SEO_KEY = {"kind": "delimited",
           "value": "seo-minseo-finance:workbook-structure"}
GUIDE_SHA = ("f60b58b7dcde86eab9f273dd50114ac296d009bd60abb940aaa866a5"
             "4e866de2")
GUIDE_KEY = {"kind": "delimited",
             "value": "official-writing-guide:toc-rules"}
KIM_SHA = ("67c1eb5192f445d914d329748f1e3275bcb3ce5d4757c9d1ca9a6c3cac"
           "cb4720")
KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}
NAT_KEY = {"kind": "audit_key", "pack_id": "rda.income.national.2024",
           "records_file_sha256":
           "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
           "physical_jsonl_line_1based": 1,
           "raw_line_sha256":
           "298fba9503617abd2809e777f795d2008f88cc0aba287f4a90639caa6d89b344"}


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def ctx(reg, **kw):
    c = {
        "root": KNUAF_DOC.parents[2],
        "runtime_root": reg.resolved_base,
        "registry": reg,
        "key_catalog": {
            "writing_rule": {
                GUIDE_KEY["value"]: {"authority": "verified",
                                    "catalog_digest": None,
                                    "receipt_sha256": None}},
            "narrative_reference": {},
            "template_structure": {
                SEO_KEY["value"]: {"authority": "verified",
                                   "catalog_digest": None,
                                   "receipt_sha256": None}},
            "statistical_observation": {},
        },
        "catalog": None,
        "catalog_digest": None,
        "indexes": {},
        "scan_policy": None,
        "packs_dir": reg.resolved_packs_dir,
    }
    c.update(kw)
    return c


def write_export(tmp, pack_id="rda.income.national.2024"):
    rel = "common/rda-income-national-2024"
    manifest = json.loads(
        (ACCEPTED_ROOT / rel / "manifest.json").read_text())
    entries = {json.dumps(r["audit_key"], sort_keys=True,
                          ensure_ascii=False): r
               for r in manifest["rows"]}
    path = Path(tmp) / "catalog.json"
    path.write_text(json.dumps(
        {"schema": "p5-catalog-export/1",
         "authority": "accepted-catalog-20260921/rda.income.national.2024",
         "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


class TestReasonClosure(unittest.TestCase):
    """Every emitted reason is a member of the closed set; every verdict
    carries a status from the closed status set and a total exit."""

    def setUp(self):
        self.reg = registry()

    def _check_wire(self, v):
        self.assertIn(v["status"], gg_reuse.STATUSES)
        self.assertTrue(set(v["reasons"]) <= gg_reuse.REASONS,
                        "non-closed reasons: %r" % v["reasons"])
        self.assertIn(v["exit"], (0, 2, 3))
        for r in v["reasons"]:
            cls, code = gg_reuse._REASON_CLASS[r]
            # class fully determines the exit contribution
            self.assertEqual(code, {"invalid_request": 2, "blocked": 2,
                                    "unmapped": 2, "needs_excerpt": 3,
                                    "reuse_ready": 0}[cls])
        for ex in v["needed_excerpt"]:
            self.assertIn(ex["reason"], gg_reuse.REASONS)
            # API section 1.4: the excerpt reason slot is a GAP-CLASS
            # token only — a conflict-class token can never appear here
            self.assertEqual(
                gg_reuse._REASON_CLASS[ex["reason"]][0],
                "needs_excerpt",
                "needed_excerpt reason %r is not gap-class"
                % ex["reason"])
            self.assertIn(ex["range_hint"]["kind"],
                          ("lines", "whole_file"))

    def test_reasons_closed_on_positive(self):
        v = gg_reuse.resolve_reuse(
            SEO_SHA,
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "reuse_ready")
        self.assertEqual(v["exit"], 0)
        self.assertEqual(v["reasons"], [])
        self._check_wire(v)

    def test_reasons_union_precedence(self):
        # role_mismatch (blocked) + runtime_missing (needs_excerpt)
        # -> union keeps both, status picks the higher-precedence class
        v = gg_reuse.resolve_reuse(
            GUIDE_SHA,
            {"kind": "narrative_reference",
             "keys": [{"kind": "delimited", "value": "a:b"}]},
            context=ctx(self.reg))
        self.assertIn("role_mismatch", v["reasons"])
        self.assertIn("runtime_missing", v["reasons"])
        self.assertEqual(v["status"], "blocked")
        self.assertEqual(v["exit"], 2)
        self._check_wire(v)

    def test_unmapped_exit_2(self):
        v = gg_reuse.resolve_reuse(
            "e" * 64,
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "unmapped")
        self.assertEqual(v["exit"], 2)
        self.assertIn("source_unmatched", v["reasons"])
        self._check_wire(v)

    def test_needs_excerpt_exit_3(self):
        v = gg_reuse.resolve_reuse(
            GUIDE_SHA,
            {"kind": "writing_rule", "keys": [GUIDE_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "needs_excerpt")
        self.assertEqual(v["exit"], 3)
        self.assertIn("runtime_missing", v["reasons"])
        self._check_wire(v)

    def test_authority_conflict_blocked(self):
        # coverage-claimed key absent from the key catalog
        kc = ctx(self.reg)["key_catalog"]
        del kc["template_structure"][SEO_KEY["value"]]
        v = gg_reuse.resolve_reuse(
            SEO_SHA,
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg, key_catalog=kc))
        self.assertEqual(v["status"], "blocked")
        self.assertIn("authority_conflict", v["reasons"])
        self.assertEqual(v["exit"], 2)

    def test_conflict_only_key_no_fabricated_excerpt_reason(self):
        # F-A2 + adjudication negative control: a key whose ONLY
        # failure is conflict-class on HEALTHY hash-matching bytes
        # stays unsatisfied and blocked WITHOUT an excerpt entry — no
        # verification_absent or other gap is manufactured merely to
        # fill the list; the measured byte/verification predicate, not
        # the desire for a route, must explain any extra reason.
        v = gg_reuse.resolve_reuse(
            KIM_SHA,
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "blocked")
        self.assertEqual(v["exit"], 2)
        self.assertEqual(v["reasons"], ["authority_conflict"])
        self.assertIn(KIM_KEY, v["unsatisfied"])
        self.assertFalse(
            [e for e in v["needed_excerpt"]
             if e["key_ref"] == KIM_KEY],
            "conflict-only key must not fabricate an excerpt reason")
        # a mixed conflict+actual-gap key retains its genuine gap route:
        # the guide's artifact is absent at runtime (runtime_missing,
        # gap-class) while its coverage-claimed key absent from the key
        # catalog is authority_conflict (conflict-class)
        kc = ctx(self.reg)["key_catalog"]
        del kc["writing_rule"][GUIDE_KEY["value"]]
        v2 = gg_reuse.resolve_reuse(
            GUIDE_SHA,
            {"kind": "writing_rule", "keys": [GUIDE_KEY]},
            context=ctx(self.reg, key_catalog=kc))
        self.assertEqual(v2["status"], "blocked")
        self.assertIn("authority_conflict", v2["reasons"])
        self.assertIn("runtime_missing", v2["reasons"])
        ex = [e for e in v2["needed_excerpt"]
              if e["key_ref"] == GUIDE_KEY]
        self.assertEqual(len(ex), 1)
        self.assertEqual(
            gg_reuse._REASON_CLASS[ex[0]["reason"]][0],
            "needs_excerpt")

    def test_effective_authority_min(self):
        # key-catalog says unverified -> effective unverified -> gap
        kc = ctx(self.reg)["key_catalog"]
        kc["template_structure"][SEO_KEY["value"]]["authority"] = \
            "unverified"
        v = gg_reuse.resolve_reuse(
            SEO_SHA,
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg, key_catalog=kc))
        self.assertEqual(v["status"], "needs_excerpt")
        self.assertIn("coverage_incomplete", v["reasons"])

    def test_lineage_invalid_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            dep = Path(td)
            (dep / "SKILL.md").write_text("x")
            (dep / "references").mkdir()
            (dep / "references" / "art.md").write_bytes(b"zz")
            sha = hashlib.sha256(b"zz").hexdigest()
            doc = {
                "schema_version": "p5-reuse-registry/3",
                "classifier_version": "p5-classifier/1",
                "artifact_base": {"kind": "installed_runtime",
                                  "declaration": {"relative_root": "..",
                                                  "marker": "SKILL.md"}},
                "entries": [{
                    "source_id": "rejected.lineage", "class": "c",
                    "role": "narrative_exemplar",
                    "source_sha256": ["f" * 64],
                    "runtime": "references/art.md",
                    "runtime_present": True,
                    "artifacts": [{"relative_path": "references/art.md",
                                   "sha256": sha, "bytes": 2}],
                    "coverage": {"narrative_reference": {"keys": [{
                        "key_ref": {"kind": "delimited", "value": "a:b"},
                        "authority": "verified",
                        "catalog_digest": None,
                        "receipt_sha256": None}]}},
                    "lineage": [{
                        "to": "references/art.md",
                        "relationship": "derived_excerpt",
                        "verification": "rejected",
                        "verifier": "t", "receipt_sha256": None}],
                    "build_allowlist_ref": None}]}
            rp = dep / "references" / "reg.json"
            rp.write_text(json.dumps(doc))
            reg = gg_reuse.load_registry(rp)
            kc = {"narrative_reference": {
                "a:b": {"authority": "verified", "catalog_digest": None,
                        "receipt_sha256": None}}}
            v = gg_reuse.resolve_reuse(
                "f" * 64,
                {"kind": "narrative_reference",
                 "keys": [{"kind": "delimited", "value": "a:b"}]},
                context=ctx(reg, packs_dir=None, key_catalog=kc))
            self.assertEqual(v["status"], "blocked")
            self.assertIn("lineage_invalid", v["reasons"])

    def test_lineage_unverified_verification_absent(self):
        with tempfile.TemporaryDirectory() as td:
            dep = Path(td)
            (dep / "SKILL.md").write_text("x")
            (dep / "references").mkdir()
            (dep / "references" / "art.md").write_bytes(b"zz")
            sha = hashlib.sha256(b"zz").hexdigest()
            doc = {
                "schema_version": "p5-reuse-registry/3",
                "classifier_version": "p5-classifier/1",
                "artifact_base": {"kind": "installed_runtime",
                                  "declaration": {"relative_root": "..",
                                                  "marker": "SKILL.md"}},
                "entries": [{
                    "source_id": "unverified.lineage", "class": "c",
                    "role": "narrative_exemplar",
                    "source_sha256": ["7" * 64],
                    "runtime": "references/art.md",
                    "runtime_present": True,
                    "artifacts": [{"relative_path": "references/art.md",
                                   "sha256": sha, "bytes": 2}],
                    "coverage": {"narrative_reference": {"keys": [{
                        "key_ref": {"kind": "delimited", "value": "a:b"},
                        "authority": "verified",
                        "catalog_digest": None,
                        "receipt_sha256": None}]}},
                    "lineage": [{
                        "to": "references/art.md",
                        "relationship": "derived_excerpt",
                        "verification": "unverified",
                        "verifier": None, "receipt_sha256": None}],
                    "build_allowlist_ref": None}]}
            rp = dep / "references" / "reg.json"
            rp.write_text(json.dumps(doc))
            reg = gg_reuse.load_registry(rp)
            kc = {"narrative_reference": {
                "a:b": {"authority": "verified", "catalog_digest": None,
                        "receipt_sha256": None}}}
            v = gg_reuse.resolve_reuse(
                "7" * 64,
                {"kind": "narrative_reference",
                 "keys": [{"kind": "delimited", "value": "a:b"}]},
                context=ctx(reg, packs_dir=None, key_catalog=kc))
            self.assertEqual(v["status"], "needs_excerpt")
            self.assertIn("verification_absent", v["reasons"])


class TestBoundaryDiscipline(unittest.TestCase):
    """Loader-failure / caller-mapped / live-trust / malformed-context
    boundaries are kept distinct (V10-1, V11-3, V15-1)."""

    def setUp(self):
        self.reg = registry()

    def test_loader_failure_raises_valueerror_not_verdict(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td)
            # corrupt the export: flip one entry's status text
            doc = json.loads(export.read_text())
            k = next(iter(doc["entries"]))
            doc["entries"][k]["catalog_status"] = "forged_status"
            export.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                gg_reuse.load_accepted_catalog(
                    export, registry=self.reg,
                    packs_dir=self.reg.resolved_packs_dir)

    def test_loader_missing_packs_base_raises_valueerror(self):
        # F-A3 / SCHEMA section 6.2 "Raises ValueError on any failure":
        # a registry may legally declare p4_trust.packs without
        # p4_trust.packs_dir (resolved_packs_dir is then None); a
        # catalog referencing that pack has no base to resolve
        # records_relpath against — a construction failure in the
        # loader's ValueError vocabulary, never a raw TypeError.
        with tempfile.TemporaryDirectory() as td:
            dep = Path(td) / "dep"
            (dep / "references").mkdir(parents=True)
            (dep / "SKILL.md").write_text("marker")
            doc = {"schema_version": "p5-reuse-registry/3",
                   "classifier_version": "p5-classifier/1",
                   "artifact_base": {
                       "kind": "installed_runtime",
                       "declaration": {"relative_root": "..",
                                       "marker": "SKILL.md"}},
                   "entries": [],
                   "p4_trust": {"packs": {
                       "p.test": {"records_relpath": "r.jsonl",
                                  "records_file_sha256": "0" * 64}}}}
            rp = dep / "references" / "reg.json"
            rp.write_text(json.dumps(doc))
            reg2 = gg_reuse.load_registry(rp)
            self.assertIsNone(reg2.resolved_packs_dir)
            ser = json.dumps(
                {"pack_id": "p.test", "records_file_sha256": "0" * 64,
                 "physical_jsonl_line_1based": 1,
                 "raw_line_sha256": "1" * 64}, sort_keys=True)
            export = Path(td) / "cat.json"
            export.write_text(json.dumps(
                {"schema": "p5-catalog-export/1",
                 "authority":
                     "accepted-catalog-20260921/rda.income.national.2024",
                 "entries": {ser: {"catalog_status": "exploratory",
                                   "record_id": "x", "locator": "y",
                                   "source_observation": None}}}))
            with self.assertRaises(ValueError) as cm:
                gg_reuse.load_accepted_catalog(
                    export, registry=reg2, packs_dir=None)
            self.assertIn("packs_dir", str(cm.exception))
            # legal companions stay legal: the null packs_dir itself is
            # not a violation — a catalog referencing NO pack reaches
            # the ordinary loader failure vocabulary (still ValueError)
            export2 = Path(td) / "cat-nopack.json"
            export2.write_text(json.dumps(
                {"schema": "p5-catalog-export/1",
                 "authority": "not-a-pinned-authority",
                 "entries": {}}))
            with self.assertRaises(ValueError):
                gg_reuse.load_accepted_catalog(
                    export2, registry=reg2, packs_dir=None)
            doc.pop("p4_trust")
            rp3 = dep / "references" / "reg3.json"
            rp3.write_text(json.dumps(doc))
            reg3 = gg_reuse.load_registry(rp3)
            with self.assertRaises(ValueError):
                gg_reuse.load_accepted_catalog(
                    export2, registry=reg3, packs_dir=None)

    def test_loader_packs_dir_claim_mismatch_raises(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td)
            with tempfile.TemporaryDirectory() as other:
                with self.assertRaises(ValueError):
                    gg_reuse.load_accepted_catalog(
                        export, registry=self.reg,
                        packs_dir=Path(other))

    def test_caller_mapped_catalog_absent_gives_not_accepted(self):
        # caller could not construct the catalog -> catalog=None ->
        # the resolver's live-trust check reports catalog_not_accepted
        v = gg_reuse.resolve_reuse(
            NAT_SHA,
            {"kind": "statistical_observation", "keys": [NAT_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "blocked")
        self.assertIn("catalog_not_accepted", v["reasons"])

    def test_catalog_digest_mismatch(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td)
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            self.assertTrue(catalog.accepted)
            key = None
            for e in self.reg.document["entries"]:
                if e["source_id"] == "rda.income.national.2024":
                    for c in e["coverage"]["statistical_observation"
                                          ]["keys"]:
                        if c["authority"] == "verified":
                            key = c["key_ref"]
            kc = ctx(self.reg)["key_catalog"]
            kc["statistical_observation"][
                gg_reuse.canonical_key(key, "statistical_observation")] = {
                "authority": "verified", "catalog_digest": None,
                "receipt_sha256": None}
            v = gg_reuse.resolve_reuse(
                NAT_SHA,
                {"kind": "statistical_observation", "keys": [key]},
                context=ctx(self.reg, catalog=catalog,
                            catalog_digest="9" * 64,  # stale claim
                            indexes=indexes, key_catalog=kc))
            self.assertEqual(v["status"], "blocked")
            self.assertIn("catalog_digest_mismatch", v["reasons"])

    def test_audit_key_unresolved_is_needs_excerpt(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td)
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            # well-formed key absent from the verified index
            ghost = dict(NAT_KEY)
            ghost["physical_jsonl_line_1based"] = 999
            ghost["raw_line_sha256"] = "0" * 64
            kc = ctx(self.reg)["key_catalog"]
            ser = gg_reuse.canonical_key(ghost, "statistical_observation")
            kc["statistical_observation"][ser] = {
                "authority": "verified", "catalog_digest": None,
                "receipt_sha256": None}
            # registry must claim coverage for the ghost key too —
            # reuse the shipped doc with an added claim so coverage
            # doesn't mask the index check
            doc = json.loads(json.dumps(self.reg.document))
            for e in doc["entries"]:
                if e["source_id"] == "rda.income.national.2024":
                    e["coverage"]["statistical_observation"]["keys"]\
                        .append({"key_ref": ghost, "authority": "verified",
                                 "catalog_digest": None,
                                 "receipt_sha256": None})
            rp = Path(td) / "reg.json"
            dep = Path(td) / "d"
            dep.mkdir()
            (dep / "references").mkdir()
            rp2 = dep / "references" / "reg.json"
            shutil_imported = __import__("shutil")
            shutil_imported.copytree(KNUAF_DOC, dep, dirs_exist_ok=True)
            rp2.write_text(json.dumps(doc))
            reg2 = gg_reuse.load_registry(rp2)
            v = gg_reuse.resolve_reuse(
                NAT_SHA,
                {"kind": "statistical_observation", "keys": [ghost]},
                context=ctx(reg2, catalog=catalog,
                            catalog_digest=catalog.digest,
                            indexes=indexes, key_catalog=kc))
            self.assertEqual(v["status"], "needs_excerpt")
            self.assertIn("audit_key_unresolved", v["reasons"])

    def test_verification_absent_quarantined_key(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td)
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            # the shipped registry claims a quarantined key with
            # authority unverified; effective authority stays unverified
            qkey = None
            for e in self.reg.document["entries"]:
                if e["source_id"] == "rda.income.national.2024":
                    for c in e["coverage"]["statistical_observation"
                                          ]["keys"]:
                        if c["authority"] == "unverified":
                            qkey = c["key_ref"]
            self.assertIsNotNone(qkey)
            kc = ctx(self.reg)["key_catalog"]
            kc["statistical_observation"][
                gg_reuse.canonical_key(qkey, "statistical_observation")] = {
                "authority": "verified", "catalog_digest": None,
                "receipt_sha256": None}
            v = gg_reuse.resolve_reuse(
                NAT_SHA,
                {"kind": "statistical_observation", "keys": [qkey]},
                context=ctx(self.reg, catalog=catalog,
                            catalog_digest=catalog.digest,
                            indexes=indexes, key_catalog=kc))
            self.assertNotEqual(v["status"], "reuse_ready")
            self.assertTrue(
                "verification_absent" in v["reasons"]
                or "coverage_incomplete" in v["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
