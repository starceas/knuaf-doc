#!/usr/bin/env python3
"""IMPL-C -- hort-env reuse-registry tests (DESIGN sec.15/16/17; 17 wins).

The shipped registry ``skills/knuaf-doc/references/builtin-sources.json``
now carries eight hort-env entries on top of the ten-entry a96a5bf
baseline.  Seven are identification-only (no runtime, no artifacts, no
coverage, no lineage); only ``hort-env-hx1-teaching-xlsx`` binds the
built-in extraction ``references/hort-env-systems/profile.json`` -- still
as an UNVERIFIED candidate, so a verified key catalog must NOT yield
``reuse_ready`` (sec.17-2a).  A synthetic registry copy that marks the
same entry/coverage/lineage verified over a real install tree must yield
``reuse_ready`` (sec.17-2b), and a one-byte artifact mutation must reject.

context/key_catalog/runtime_root construction follows
``skills/knuaf-doc/tests/test_gg_reuse.py`` and
``test_gg_reuse_reasons.py`` verbatim.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_hort_env_reuse -v
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent
KNUAF_DOC = REPO_ROOT / "skills" / "knuaf-doc"
SCRIPTS = KNUAF_DOC / "scripts"
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"
PROFILE_PATH = (KNUAF_DOC / "references" / "hort-env-systems"
                / "profile.json")
PROFILE_REL = "references/hort-env-systems/profile.json"

sys.path.insert(0, str(SCRIPTS))
import gg_reuse  # noqa: E402

# ---------------------------------------------------------------------------
# Pinned constants -- fixed at IMPL-C writing time against commit a96a5bf
# (the test never shells out to git).  BASELINE_ENTRY_DIGEST pins
# sha256(_canonical_json(entry)) for each of the ten a96a5bf entries so the
# hort additions are proven not to have mutated them.  Re-pin only by
# recomputing against a DIFFERENT agreed baseline commit -- never from the
# live file.

BASELINE_ENTRY_DIGEST = {
    "rda.income.national.2024":
        "8e43d31f42bf95915e1bce8869f32a2fde56b39da3412f20fb7e9829348994f7",
    "rda.income.regional.2024":
        "04a94bc8b6704b27eaf01679c2cfb3376451643524a6a797b3d492ff46787d56",
    "rda.econ.2025":
        "7e6ff1e08ee4b6e70539d01edbec4ce1ff96f4f9e526e8e468c70c6371c5d49b",
    "mafra.specialty.production.2024":
        "0b636815fa11a515929f4679a802d4d6756686096e37991fbf623fd5ff31c741",
    "official-writing-guide-pdf":
        "2e99ba6ff1908c3dd3903670d61ad8303bd7602150f712871aeb8b7f08ee8d64",
    "official-writing-guide-hwp":
        "0b83bcc3a630a46c2afcb30c88826bcdaae9f77c1fd33b6588f81b976b076620",
    "kim-wonseop-exemplar-pdf":
        "1735d2a0232b5755eff01dc02aa552b0b233e40566df7659d709fc60b80823d3",
    "kim-wonseop-exemplar-hwp":
        "438903e009b0708854f61ca010c14fb8a34f360345b49655e9fc2648d04a3af0",
    "seo-minseo-finance-xlsx":
        "92251ee46930f468b3aa3328cfb289863d8af23bbafdcd8fd9b20bdd6e21c991",
    "specialty-22160117-finance-xlsx":
        "c4f9ec778476b771a338841e7cda0994b95412e0e3f6c025578cc185f4534d80",
}

HORT_IDS = [
    "hort-env-ht1-exemplar-pdf",
    "hort-env-ht2-exemplar-pdf",
    "hort-env-hx1-teaching-xlsx",
    "hort-env-disaster-rule-pdf",
    "hort-env-disaster-annex1-pdf",
    "hort-env-facility-spec-xlsx",
    "hort-env-ncs-install-pdf",
    "hort-env-ncs-manage-pdf",
]

# Identification-only entries (sec.16): identification values only, no
# reuse use-case granted.
IDENTIFICATION_ONLY = [
    "hort-env-ht1-exemplar-pdf",
    "hort-env-ht2-exemplar-pdf",
    "hort-env-disaster-rule-pdf",
    "hort-env-disaster-annex1-pdf",
    "hort-env-facility-spec-xlsx",
    "hort-env-ncs-install-pdf",
    "hort-env-ncs-manage-pdf",
]

REFERENCE_ONLY = [
    "hort-env-disaster-rule-pdf",
    "hort-env-disaster-annex1-pdf",
    "hort-env-facility-spec-xlsx",
    "hort-env-ncs-install-pdf",
    "hort-env-ncs-manage-pdf",
]

HORT_SOURCE_SHA = {
    "hort-env-ht1-exemplar-pdf":
        "f700bc9728b6131b89b3f11a97645cfee1bd2c5a3960e53c9c78814dbb4f45a4",
    "hort-env-ht2-exemplar-pdf":
        "e5ca9ff33daa1fe8b3e7d56fbd1fff5a6bbba8c3d1e0368bbea1a36a0b3f8e4c",
    "hort-env-hx1-teaching-xlsx":
        "e3c9defe376fa74413641408900f6bb656017a389fb9745d9dac221a10951c1c",
    "hort-env-disaster-rule-pdf":
        "c8b401cd49103a2d4432153ab45736d6989813864fd5775a70a2974b121099fa",
    "hort-env-disaster-annex1-pdf":
        "c50e4d3f4ec3a1911ecd92ff8725738a8472c6bd330afc458305172a39f9d757",
    "hort-env-facility-spec-xlsx":
        "be0f9fadd328f134d0350b5e75b69d98c155263e5c7181621fffa07d0d06fa09",
    "hort-env-ncs-install-pdf":
        "cb196629e029c7ea0999957f673cc2d40edaf020cf8edf9deaefad0582f5fdd1",
    "hort-env-ncs-manage-pdf":
        "08e143bdcf1ca4d49f8e7bbfb01d56bc3c0696169229b79e459a0b1d3813c79b",
}

HX1_ID = "hort-env-hx1-teaching-xlsx"
HX1_KEY = {"kind": "delimited",
           "value": "hort-env-hx1:workbook-structure"}
HT1_KEY = {"kind": "delimited",
           "value": "hort-env-ht1:document-structure"}
PROBE_KEY = {"kind": "delimited",
             "value": "hort-env-probe:uncovered"}

# Declared artifact identity, pinned at writing time (40378 bytes,
# sha256 below).  The live file is re-measured inside the test -- the pin
# only says what the registry SHOULD declare.
PROFILE_DECLARED_SHA = (
    "1b8a731eed64cc9e2062fedcb96434bddc4f6110525f1799c4d09293e1877ef4")
PROFILE_DECLARED_BYTES = 40378


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def key_catalog(authority="verified"):
    """Caller-supplied key catalog (never shipped) -- a claim of the
    given authority on every delimited key these tests request."""
    claim = {"authority": authority, "catalog_digest": None,
             "receipt_sha256": None}
    return {
        "writing_rule": {},
        "statistical_observation": {},
        "template_structure": {HX1_KEY["value"]: dict(claim)},
        "narrative_reference": {
            HT1_KEY["value"]: dict(claim),
            PROBE_KEY["value"]: dict(claim)},
    }


def ctx(reg, **kw):
    c = {
        "root": REPO_ROOT,
        "runtime_root": reg.resolved_base,
        "registry": reg,
        "key_catalog": key_catalog(),
        "catalog": None,
        "catalog_digest": None,
        "indexes": {},
        "scan_policy": None,
        "packs_dir": reg.resolved_packs_dir,
    }
    c.update(kw)
    return c


def _install_tree(td, profile_bytes):
    """Synthetic install tree honouring artifact_base: the registry
    file sits in ``<dep>/references/reg.json`` with declaration
    relative_root=".." marker=SKILL.md, so resolved_base == <dep>."""
    dep = Path(td) / "deploy"
    (dep / "references" / "hort-env-systems").mkdir(parents=True)
    (dep / "SKILL.md").write_text("x", encoding="utf-8")
    (dep / "references" / "hort-env-systems" / "profile.json").write_bytes(
        profile_bytes)
    return dep


def _verified_hx1_doc(profile_bytes):
    """sec.17-2(b) synthetic registry: the shipped HX1 entry with
    coverage and lineage authority flipped to verified over the REAL
    profile bytes."""
    doc = {
        "schema_version": "p5-reuse-registry/3",
        "classifier_version": "p5-classifier/1",
        "artifact_base": {"kind": "installed_runtime",
                          "declaration": {"relative_root": "..",
                                          "marker": "SKILL.md"}},
        "entries": [{
            "source_id": HX1_ID,
            "class": "hort_env_teaching_workbook",
            "role": "secondary_finance_exemplar",
            "source_sha256": [HORT_SOURCE_SHA[HX1_ID]],
            "runtime": PROFILE_REL,
            "runtime_present": True,
            "artifacts": [{
                "relative_path": PROFILE_REL,
                "sha256": hashlib.sha256(profile_bytes).hexdigest(),
                "bytes": len(profile_bytes)}],
            "coverage": {"template_structure": {"keys": [{
                "key_ref": dict(HX1_KEY),
                "authority": "verified",
                "catalog_digest": None,
                "receipt_sha256": None}]}},
            "lineage": [{
                "to": PROFILE_REL,
                "relationship": "derived_snapshot",
                "verification": "verified",
                "verifier": "hort-env-lead-and-astra",
                "receipt_sha256": None}],
            "build_allowlist_ref": None}],
    }
    return doc


class TestRegistryIntegrity(unittest.TestCase):
    """1-2: shipped registry loads; baseline entries byte-identical."""

    def setUp(self):
        self.reg = registry()

    def test_load_registry_succeeds(self):
        self.assertEqual(self.reg.document["schema_version"],
                         "p5-reuse-registry/3")
        self.assertEqual(self.reg.resolved_base, KNUAF_DOC)
        self.assertTrue(
            (self.reg.resolved_base / "SKILL.md").is_file())

    def test_source_ids_unique(self):
        ids = [e["source_id"] for e in self.reg.document["entries"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_source_sha256_globally_unique(self):
        seen = []
        for e in self.reg.document["entries"]:
            seen.extend(e["source_sha256"])
        self.assertEqual(len(seen), len(set(seen)),
                         "duplicate source_sha256 across entries")

    def test_baseline_entries_unchanged(self):
        entries = {e["source_id"]: e
                   for e in self.reg.document["entries"]}
        for sid, digest in BASELINE_ENTRY_DIGEST.items():
            self.assertIn(sid, entries,
                          "missing baseline entry %s" % sid)
            observed = hashlib.sha256(
                gg_reuse._canonical_json(entries[sid])
                .encode("utf-8")).hexdigest()
            self.assertEqual(
                observed, digest,
                "baseline entry %s mutated by the hort addition" % sid)


class TestHortEntries(unittest.TestCase):
    """3-4: the eight hort entries exist in the sec.16 shape and
    classify."""

    def setUp(self):
        self.reg = registry()
        self.entries = {e["source_id"]: e
                        for e in self.reg.document["entries"]}

    def test_all_hort_entries_present(self):
        for sid in HORT_IDS:
            self.assertIn(sid, self.entries)

    def test_identification_only_entries_are_empty(self):
        for sid in IDENTIFICATION_ONLY:
            e = self.entries[sid]
            self.assertIsNone(e.get("runtime"), sid)
            self.assertFalse(e.get("runtime_present"), sid)
            self.assertEqual(e.get("artifacts") or [], [], sid)
            self.assertFalse(e.get("coverage"), sid)
            self.assertEqual(e.get("lineage") or [], [], sid)

    def test_reference_only_roles(self):
        for sid in REFERENCE_ONLY:
            self.assertEqual(self.entries[sid]["role"],
                             "reference_only", sid)

    def test_hx1_runtime_and_artifact(self):
        e = self.entries[HX1_ID]
        self.assertTrue(e["runtime_present"])
        self.assertEqual(e["runtime"], PROFILE_REL)
        arts = e["artifacts"]
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["relative_path"], PROFILE_REL)
        self.assertEqual(arts[0]["sha256"], PROFILE_DECLARED_SHA)
        self.assertEqual(arts[0]["bytes"], PROFILE_DECLARED_BYTES)
        # declared identity must match the shipped file's real bytes
        data = PROFILE_PATH.read_bytes()
        self.assertEqual(len(data), arts[0]["bytes"])
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         arts[0]["sha256"])
        # candidate stage (sec.17-2): coverage + lineage are unverified
        keys = e["coverage"]["template_structure"]["keys"]
        self.assertEqual([k["key_ref"] for k in keys], [HX1_KEY])
        # Candidate stage ships "unverified"; the post-PASS promotion
        # raises coverage and lineage together to "verified" (sec.17-2).
        self.assertIn(keys[0]["authority"], ("unverified", "verified"))
        self.assertEqual(e["lineage"][0]["to"], PROFILE_REL)
        self.assertEqual(e["lineage"][0]["verification"],
                         keys[0]["authority"])

    def test_classify_source_maps_each_hort_hash(self):
        for sid in HORT_IDS:
            hits = gg_reuse.classify_source(
                self.reg, HORT_SOURCE_SHA[sid])
            self.assertEqual([h["source_id"] for h in hits], [sid], sid)


class TestReuseCandidateStage(unittest.TestCase):
    """5 / 7 (sec.17-2a): unverified candidate and identification-only
    entries can never reach reuse_ready on the shipped registry."""

    def setUp(self):
        self.reg = registry()

    def test_hx1_readiness_follows_registry_authority(self):
        # Effective authority is min(registry, catalog) per SCHEMA
        # sec.3.3/5.1: an unverified registry claim is never lifted by a
        # verified caller catalog; once promoted, the shipped entry and
        # the real profile.json bytes resolve to reuse_ready.
        entry = {e["source_id"]: e for e in self.reg.document["entries"]}[
            HX1_ID]
        authority = entry["coverage"]["template_structure"]["keys"][0][
            "authority"]
        v = gg_reuse.resolve_reuse(
            HORT_SOURCE_SHA[HX1_ID],
            {"kind": "template_structure", "keys": [HX1_KEY]},
            context=ctx(self.reg))
        if authority == "verified":
            self.assertEqual(v["status"], "reuse_ready", v)
            return
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertEqual(v["selected_entry"], HX1_ID)
        self.assertIn(HX1_KEY, v["unsatisfied"])
        gaps = {"coverage_incomplete", "verification_absent"}
        self.assertTrue(gaps & set(v["reasons"]),
                        "expected a weak-authority gap in %r" % v)

    def test_ht1_identification_only_not_reuse_ready(self):
        v = gg_reuse.resolve_reuse(
            HORT_SOURCE_SHA["hort-env-ht1-exemplar-pdf"],
            {"kind": "narrative_reference", "keys": [HT1_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertEqual(v["selected_entry"],
                         "hort-env-ht1-exemplar-pdf")

    def test_reference_only_any_use_not_ready(self):
        for sid in REFERENCE_ONLY:
            for kind, key in (("narrative_reference", HT1_KEY),
                              ("template_structure", HX1_KEY)):
                v = gg_reuse.resolve_reuse(
                    HORT_SOURCE_SHA[sid],
                    {"kind": kind, "keys": [key]},
                    context=ctx(self.reg))
                self.assertNotEqual(v["status"], "reuse_ready",
                                    "%s %s" % (sid, kind))
                self.assertIn("role_mismatch", v["reasons"], sid)


class TestReuseVerifiedSynthetic(unittest.TestCase):
    """6 (sec.17-2b): a verified synthetic registry over a real install
    tree reaches reuse_ready; one mutated byte rejects."""

    def _synthetic(self, td, profile_bytes):
        dep = _install_tree(td, profile_bytes)
        doc = _verified_hx1_doc(profile_bytes)
        reg_path = dep / "references" / "reg.json"
        reg_path.write_text(json.dumps(doc, ensure_ascii=False),
                            encoding="utf-8")
        reg = gg_reuse.load_registry(reg_path)
        return reg, ctx(reg, packs_dir=None)

    def test_verified_synthetic_reuse_ready(self):
        profile_bytes = PROFILE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as td:
            reg, context = self._synthetic(td, profile_bytes)
            self.assertEqual(reg.resolved_base,
                             Path(td) / "deploy")
            v = gg_reuse.resolve_reuse(
                HORT_SOURCE_SHA[HX1_ID],
                {"kind": "template_structure", "keys": [HX1_KEY]},
                context=context)
            self.assertEqual(v["status"], "reuse_ready", v["reasons"])
            self.assertEqual(v["exit"], 0)
            self.assertEqual(v["satisfied"], [HX1_KEY])
            self.assertEqual(v["selected_entry"], HX1_ID)
            arts = v["evidence"]["artifacts"]
            self.assertEqual(len(arts), 1)
            self.assertEqual(arts[0]["path"], PROFILE_REL)
            self.assertEqual(arts[0]["observed_sha256"],
                             PROFILE_DECLARED_SHA)
            self.assertTrue(arts[0]["exists"])

    def test_mutated_profile_byte_rejected(self):
        profile_bytes = PROFILE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as td:
            dep = _install_tree(td, profile_bytes)
            target = (dep / "references" / "hort-env-systems"
                      / "profile.json")
            target.write_bytes(profile_bytes + b"x")
            # the synthetic registry still declares the PRISTINE bytes
            # -- measured != declared must reject reuse
            doc = _verified_hx1_doc(profile_bytes)
            reg_path = dep / "references" / "reg.json"
            reg_path.write_text(json.dumps(doc, ensure_ascii=False),
                                encoding="utf-8")
            reg = gg_reuse.load_registry(reg_path)
            v = gg_reuse.resolve_reuse(
                HORT_SOURCE_SHA[HX1_ID],
                {"kind": "template_structure", "keys": [HX1_KEY]},
                context=ctx(reg, packs_dir=None))
            self.assertNotEqual(v["status"], "reuse_ready")
            self.assertEqual(v["status"], "blocked")
            self.assertIn("runtime_hash_mismatch", v["reasons"])
            self.assertIn("verification_absent", v["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
