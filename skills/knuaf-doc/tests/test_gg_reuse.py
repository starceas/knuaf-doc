#!/usr/bin/env python3
"""Lane A reuse-resolver tests — I01/I02/CU.7 controls (frozen v19).

Real temp filesystems throughout; the shipped registry is exercised
through its own loader, and the statistical positive goes through the
parent's accept_catalog route on real accepted-catalog bytes.

Run: python3 -B tests/test_gg_reuse.py
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KNUAF_DOC = TESTS.parent
SCRIPTS = KNUAF_DOC / "scripts"
LANE_ROOT = KNUAF_DOC.parents[2]          # work/g006-p5-lane-a-1
REGISTRY_PATH = KNUAF_DOC / "references" / "builtin-sources.json"
PACKS_DIR = KNUAF_DOC / "references" / "benchmark-packs"
ACCEPTED_ROOT = (LANE_ROOT.parent
                 / "g005-p4-source-catalog-transform-corrections-1"
                 / "references" / "benchmark-packs")

sys.path.insert(0, str(SCRIPTS))
import gg_reuse  # noqa: E402
import gg_rda_provenance as prov  # noqa: E402
import gg_rda_research as research  # noqa: E402

AUTHORITY = {
    "rda.income.national.2024":
        "accepted-catalog-20260921/rda.income.national.2024",
    "rda.income.regional.2024":
        "accepted-catalog-20260921/rda.income.regional.2024",
    "rda.econ.2025": "accepted-catalog-20260921/rda.econ.2025",
    "mafra.specialty.production.2024":
        "accepted-catalog-20260921/mafra.specialty.production.2024",
}

SOURCE_SHA = {
    "rda.income.national.2024":
        "0bd85164ad387228e7034deb502f3f436a05336e1cf2c30326a5257fb3dff6ab",
    "rda.income.regional.2024":
        "b229ca4f83e9152656945090c15ac7f8780c578042854200b9e11b77bf8d3813",
    "rda.econ.2025":
        "38e38ffb8b478584b9f76a8b9d98d56c7cdeb8eb18a2200aef26639a72935a83",
    "mafra.specialty.production.2024":
        "0974c3d30aa5c1abd795be0af453f4de3bd4e569285c250537897b820d2aa1d5",
    "official-writing-guide-pdf":
        "f60b58b7dcde86eab9f273dd50114ac296d009bd60abb940aaa866a54e866de2",
    "official-writing-guide-hwp":
        "efcbbc7fe261477ebd8628883733aacd4214fa365974617dced1304d99f6c631",
    "kim-wonseop-exemplar-pdf":
        "67c1eb5192f445d914d329748f1e3275bcb3ce5d4757c9d1ca9a6c3caccb4720",
    "kim-wonseop-exemplar-hwp":
        "906ad6ff04760f30feefad938e1b40a8dcbf7d9f62704784cdfe6cfe5504e13e",
    "seo-minseo-finance-xlsx":
        "457249255929275b3ee55383bdc36897274f08933af3536866a652b29346cd3a",
    "specialty-grad-thesis-finance-xlsx":
        "029f8107ec2fee318544799524f7f4317946666d323d30174c931d3469d02129",
    "kang-finance-workbook-x01":
        "5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80",
    "kang-finance-workbook-x02":
        "54d4e55cd57bcb1db0fbfaafe2ed42422767f1561959b8a4f3c673daf77d7e78",
    # hort_env_systems reference entries (identification-only except hx1)
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
    # industrial_insects built-in references (P9): F0/E1-E4 locator map,
    # E3/E4 HWP and R0/R1 official statistics as identity-only entries.
    "insect.example.f0":
        "d582d884abdd21edf622fc0973844abbc4c62369bf7e92574da3d4e33ae75885",
    "insect.example.e1":
        "9668c9abc68f750039e940bb78f995794696b6dd14b1b5714cd74e81e485a36d",
    "insect.example.e2":
        "2527c590183666543ecb9d23e20e6f5f2a5d0f3215c1ba09577dcdb9912ca0d2",
    "insect.example.e3":
        "b91fefd7ff77dbec1752a503c2c3238c7d66058d35b4e25cb9f1237842bf4f44",
    "insect.example.e4":
        "198fd1e140ad41ec8612eafd484cfc46ac5cfea8d318408cc38a0a241ea90d55",
    "insect.example.e3-hwp":
        "40a537112c252f55351f9b40714de97108381eb7f47a828b84e88041b85e960c",
    "insect.example.e4-hwp":
        "922c56473d6d7d614d7ef18a8a07f2fd3eb16aa16acc7f8df26170aa9a0fd990",
    "insect.stat.r0":
        "3dd2b75f5588d1b710123f56cc37747521dee786a2c4a13ecf5e6ab6e66541be",
    "insect.stat.r1":
        "2582fc7703bb3c409225e1a7329a6a591513500bd0a827e3e9f1807b2bcebe8a",
}
BASELINE_IDS = [
    "rda.income.national.2024", "rda.income.regional.2024",
    "rda.econ.2025", "mafra.specialty.production.2024",
    "official-writing-guide-pdf", "official-writing-guide-hwp",
    "kim-wonseop-exemplar-pdf", "kim-wonseop-exemplar-hwp",
    "seo-minseo-finance-xlsx", "specialty-grad-thesis-finance-xlsx",
    "kang-finance-workbook-x01", "kang-finance-workbook-x02",
]

KIM_KEY = {"kind": "delimited",
           "value": "kim-wonseop-exemplar:opening-narrative"}
KIM_KEY2 = {"kind": "delimited",
            "value": "kim-wonseop-exemplar:closing-narrative"}
SEO_KEY = {"kind": "delimited",
           "value": "seo-minseo-finance:workbook-structure"}
GUIDE_KEY = {"kind": "delimited",
             "value": "official-writing-guide:toc-rules"}


def registry():
    return gg_reuse.load_registry(REGISTRY_PATH)


def key_catalog_extra(kind=None, ser=None, authority="verified"):
    cat = {
        "writing_rule": {
            GUIDE_KEY["value"]: {"authority": "verified",
                                "catalog_digest": None,
                                "receipt_sha256": None}},
        "narrative_reference": {
            KIM_KEY["value"]: {"authority": "verified",
                               "catalog_digest": None,
                               "receipt_sha256": None}},
        "template_structure": {
            SEO_KEY["value"]: {"authority": "verified",
                               "catalog_digest": None,
                               "receipt_sha256": None}},
        "statistical_observation": {},
    }
    if kind and ser:
        cat.setdefault(kind, {})[ser] = {
            "authority": authority, "catalog_digest": None,
            "receipt_sha256": None}
    return cat


def ctx(reg, **kw):
    c = {
        "root": KNUAF_DOC.parents[2],
        "runtime_root": reg.resolved_base,
        "registry": reg,
        "key_catalog": key_catalog_extra(),
        "catalog": None,
        "catalog_digest": None,
        "indexes": {},
        "scan_policy": None,
        "packs_dir": reg.resolved_packs_dir,
    }
    c.update(kw)
    return c


def write_export(tmp, pack_id, mutate=None):
    """Build a real p5-catalog-export/1 file from the accepted manifest
    rows — entries are the accepted row objects verbatim so the pinned
    authority digest authenticates the whole content."""
    rel = dict((p, r) for p, r in {
        "rda.income.national.2024": "common/rda-income-national-2024",
        "rda.income.regional.2024": "common/rda-income-regional-2024",
        "rda.econ.2025": "common/rda-econ-2025",
        "mafra.specialty.production.2024":
            "majors/specialty_crops/mafra-specialty-production-2024",
    }.items())[pack_id]
    manifest = json.loads(
        (ACCEPTED_ROOT / rel / "manifest.json").read_text())
    rows = manifest["rows"]
    if mutate:
        rows = mutate(rows)
    entries = {json.dumps(row["audit_key"], sort_keys=True,
                          ensure_ascii=False): row for row in rows}
    path = Path(tmp) / ("catalog-%s.json" % pack_id)
    path.write_text(json.dumps(
        {"schema": "p5-catalog-export/1",
         "authority": AUTHORITY[pack_id],
         "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def stat_key_of(registry_doc, pack_id, which="verified"):
    """A coverage-claimed audit key_ref from the shipped registry."""
    for e in registry_doc.document["entries"]:
        if e["source_id"] != pack_id:
            continue
        for claim in (e["coverage"]["statistical_observation"]["keys"]):
            if claim["authority"] == which:
                return claim["key_ref"]
    raise KeyError("%s has no %s coverage claim" % (pack_id, which))


class TestContextBoundaries(unittest.TestCase):
    """CU.7 negative control A + malformed-context escaping ValueError."""

    def setUp(self):
        self.reg = registry()

    def test_missing_context_kwarg_typeerror(self):
        with self.assertRaises(TypeError):
            gg_reuse.resolve_reuse(
                SOURCE_SHA["seo-minseo-finance-xlsx"],
                {"kind": "template_structure", "keys": [SEO_KEY]})

    def test_catalog_plain_dict_rejected(self):
        bad = ctx(self.reg, catalog={"entries": {}},
                  catalog_digest="0" * 64)
        with self.assertRaises(ValueError) as cm:
            gg_reuse.resolve_reuse(
                SOURCE_SHA["rda.income.national.2024"],
                {"kind": "statistical_observation",
                 "keys": [{"kind": "audit_key",
                           "pack_id": "rda.income.national.2024",
                           "records_file_sha256": "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
                           "physical_jsonl_line_1based": 1,
                           "raw_line_sha256": "298fba9503617abd2809e777f795d2008f88cc0aba287f4a90639caa6d89b344"}]},
                context=bad)
        self.assertIn("catalog", str(cm.exception))

    def test_catalog_handbuilt_accepted_asserting_object_rejected(self):
        class FakeAccepted:
            accepted = True
            digest = "0" * 64

        bad = ctx(self.reg, catalog=FakeAccepted(),
                  catalog_digest="0" * 64)
        with self.assertRaises(ValueError) as cm:
            gg_reuse.resolve_reuse(
                SOURCE_SHA["seo-minseo-finance-xlsx"],
                {"kind": "template_structure", "keys": [SEO_KEY]},
                context=bad)
        self.assertIn("catalog", str(cm.exception))

    def test_registry_bare_dict_rejected(self):
        bad = ctx(self.reg, registry=self.reg.document)
        with self.assertRaises(ValueError) as cm:
            gg_reuse.resolve_reuse(
                SOURCE_SHA["seo-minseo-finance-xlsx"],
                {"kind": "template_structure", "keys": [SEO_KEY]},
                context=bad)
        self.assertIn("registry", str(cm.exception))

    def test_packs_dir_mismatch_is_malformed_context_not_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            bad = ctx(self.reg, packs_dir=Path(td))
            with self.assertRaises(ValueError) as cm:
                gg_reuse.resolve_reuse(
                    SOURCE_SHA["rda.income.national.2024"],
                    {"kind": "statistical_observation",
                     "keys": [{"kind": "audit_key",
                               "pack_id": "rda.income.national.2024",
                               "records_file_sha256": "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
                               "physical_jsonl_line_1based": 1,
                               "raw_line_sha256": "298fba9503617abd2809e777f795d2008f88cc0aba287f4a90639caa6d89b344"}]},
                    context=bad)
            self.assertIn("packs_dir", str(cm.exception))

    def test_runtime_base_mismatch_is_verdict_not_valueerror(self):
        with tempfile.TemporaryDirectory() as td:
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["seo-minseo-finance-xlsx"],
                {"kind": "template_structure", "keys": [SEO_KEY]},
                context=ctx(self.reg, runtime_root=Path(td)))
            self.assertEqual(v["status"], "blocked")
            self.assertIn("runtime_base_mismatch", v["reasons"])
            self.assertEqual(v["exit"], 2)


class TestI01Negatives(unittest.TestCase):
    """I01.1–I01.10 — no reuse by document-link presence."""

    def setUp(self):
        self.reg = registry()

    def test_i01_1_missing_runtime_artifact(self):
        # shipped truth: official-toc.md is absent at runtime (R3)
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["official-writing-guide-pdf"],
            {"kind": "writing_rule", "keys": [GUIDE_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("runtime_missing", v["reasons"])
        self.assertTrue(v["needed_excerpt"])
        ex = v["needed_excerpt"][0]
        self.assertEqual(ex["artifact"], "references/official-toc.md")
        self.assertEqual(ex["range_hint"]["kind"], "whole_file")

    def test_i01_2_tampered_artifact_bytes(self):
        # relocate the shipped registry onto a temp deployment with the
        # exemplar artifact's bytes flipped -> measured != declared
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "deploy"
            shutil.copytree(KNUAF_DOC, dst)
            target = dst / "references" / "exemplar-quality.md"
            target.write_bytes(target.read_bytes() + b"x")
            reg = gg_reuse.load_registry(
                dst / "references" / "builtin-sources.json")
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["kim-wonseop-exemplar-pdf"],
                {"kind": "narrative_reference", "keys": [KIM_KEY]},
                context=ctx(reg))
            self.assertNotEqual(v["status"], "reuse_ready")
            self.assertIn("runtime_hash_mismatch", v["reasons"])
            self.assertEqual(v["status"], "blocked")
            self.assertEqual(v["exit"], 2)
            # adjudicated I01.2/F-A2 repair: the lineage 'verified'
            # label attests the DECLARED bytes; the divergent observed
            # bytes carry no applicable verification -> a GENUINE
            # verification_absent gap on the AFFECTED key itself, and
            # the actionable route names that key and the tampered
            # artifact (no companion key borrows this route).
            self.assertIn("verification_absent", v["reasons"])
            self.assertIn(KIM_KEY, v["unsatisfied"])
            ex = [e for e in v["needed_excerpt"]
                  if e["key_ref"] == KIM_KEY]
            self.assertEqual(len(ex), 1)
            self.assertEqual(ex[0]["reason"], "verification_absent")
            self.assertEqual(ex[0]["artifact"],
                             "references/exemplar-quality.md")
            self.assertEqual(ex[0]["range_hint"]["kind"], "whole_file")
            self.assertEqual(
                gg_reuse._REASON_CLASS[ex[0]["reason"]][0],
                "needs_excerpt")

    def test_i01_2_tampered_then_restored(self):
        # adjudicated state (4): tamper + the original authority
        # conflict -> BOTH conflict reasons plus the genuine
        # current-byte verification gap and route; restoring the exact
        # original bytes removes ONLY the tamper/verification-gap leg —
        # the authority conflict remains, with no registry/key-catalog
        # edits to make restoration pass.
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "deploy"
            shutil.copytree(KNUAF_DOC, dst)
            target = dst / "references" / "exemplar-quality.md"
            pristine = target.read_bytes()
            reg = gg_reuse.load_registry(
                dst / "references" / "builtin-sources.json")
            kc = ctx(reg)["key_catalog"]
            del kc["narrative_reference"][KIM_KEY["value"]]
            target.write_bytes(pristine + b"x")
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["kim-wonseop-exemplar-pdf"],
                {"kind": "narrative_reference", "keys": [KIM_KEY]},
                context=ctx(reg, key_catalog=kc))
            self.assertEqual(v["status"], "blocked")
            self.assertEqual(v["exit"], 2)
            self.assertIn("runtime_hash_mismatch", v["reasons"])
            self.assertIn("authority_conflict", v["reasons"])
            self.assertIn("verification_absent", v["reasons"])
            ex = [e for e in v["needed_excerpt"]
                  if e["key_ref"] == KIM_KEY]
            self.assertEqual(len(ex), 1)
            self.assertEqual(ex[0]["reason"], "verification_absent")
            self.assertEqual(ex[0]["artifact"],
                             "references/exemplar-quality.md")
            # restore the EXACT original bytes -> measured bytes match
            # declared again: tamper + verification-gap legs lift
            target.write_bytes(pristine)
            v2 = gg_reuse.resolve_reuse(
                SOURCE_SHA["kim-wonseop-exemplar-pdf"],
                {"kind": "narrative_reference", "keys": [KIM_KEY]},
                context=ctx(reg, key_catalog=kc))
            self.assertEqual(v2["status"], "blocked")
            self.assertEqual(v2["exit"], 2)
            self.assertEqual(v2["reasons"], ["authority_conflict"])
            self.assertIn(KIM_KEY, v2["unsatisfied"])
            self.assertFalse(v2["needed_excerpt"])

    def test_i01_3_wrong_role(self):
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["official-writing-guide-pdf"],
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("role_mismatch", v["reasons"])
        self.assertTrue(v["needed_excerpt"])

    def test_i01_4_partial_coverage(self):
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-pdf"],
            {"kind": "narrative_reference",
             "keys": [KIM_KEY2, KIM_KEY]},   # canonical order
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("coverage_incomplete", v["reasons"])
        self.assertIn(KIM_KEY, v["satisfied"])
        self.assertIn(KIM_KEY2, v["unsatisfied"])
        ex = [e for e in v["needed_excerpt"] if e["key_ref"] == KIM_KEY2]
        self.assertEqual(len(ex), 1)
        self.assertEqual(ex[0]["artifact"], "references/exemplar-quality.md")

    def test_i01_5_different_edition(self):
        v = gg_reuse.resolve_reuse(
            "a" * 64,
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertEqual(v["status"], "unmapped")

    def test_i01_6_malformed_requests(self):
        base_key = {"kind": "audit_key",
                    "pack_id": "rda.income.national.2024",
                    "records_file_sha256":
                        "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
                    "physical_jsonl_line_1based": 1,
                    "raw_line_sha256":
                        "298fba9503617abd2809e777f795d2008f88cc0aba287f4a90639caa6d89b344"}
        bads = [
            {"kind": "bogus_kind", "keys": [KIM_KEY]},
            {"kind": "narrative_reference", "keys": []},
            {"kind": "narrative_reference",
             "keys": [{"kind": "delimited", "value": "UPPER:case"}]},
            {"kind": "narrative_reference",
             "keys": [KIM_KEY, KIM_KEY2]},          # unsorted
            # ("opening" sorts after "closing")
            {"kind": "narrative_reference",
             "keys": [KIM_KEY, KIM_KEY]},           # duplicate
            # P5-domain violation: bool line number
            {"kind": "statistical_observation",
             "keys": [dict(base_key, physical_jsonl_line_1based=True)]},
            # P5-domain violation: pack_id outside grammar
            {"kind": "statistical_observation",
             "keys": [dict(base_key, pack_id="BAD PACK")]},
            # uppercase hash — rejected, never lowercased
            {"kind": "statistical_observation",
             "keys": [dict(base_key,
                           raw_line_sha256=base_key["raw_line_sha256"]
                           .upper())]},
        ]
        for bad in bads:
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["kim-wonseop-exemplar-pdf"], bad,
                context=ctx(self.reg))
            self.assertEqual(v["status"], "invalid_request",
                             "expected invalid_request for %r" % bad)
            self.assertEqual(v["exit"], 2)

    def test_i01_7_unmapped(self):
        v = gg_reuse.resolve_reuse(
            "b" * 64,
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "unmapped")
        self.assertIn("source_unmatched", v["reasons"])
        self.assertEqual(v["exit"], 2)

    def _collision_registry(self, td):
        """Two entries, one source hash, conflicting roles/coverage."""
        dep = Path(td)
        (dep / "SKILL.md").write_text("x")
        (dep / "references").mkdir()
        (dep / "references" / "art-a.md").write_bytes(b"alpha")
        (dep / "references" / "art-b.md").write_bytes(b"beta")
        import hashlib
        sha_a = hashlib.sha256(b"alpha").hexdigest()
        sha_b = hashlib.sha256(b"beta").hexdigest()
        shared = "c" * 64
        doc = {
            "schema_version": "p5-reuse-registry/3",
            "classifier_version": "p5-classifier/1",
            "artifact_base": {"kind": "installed_runtime",
                              "declaration": {"relative_root": "..",
                                              "marker": "SKILL.md"}},
            "entries": [
                {"source_id": "collide.alpha", "class": "c1",
                 "role": "narrative_exemplar",
                 "source_sha256": [shared],
                 "runtime": "references/art-a.md",
                 "runtime_present": True,
                 "artifacts": [{"relative_path": "references/art-a.md",
                                "sha256": sha_a, "bytes": 5}],
                 "coverage": {}, "lineage": [],
                 "build_allowlist_ref": None},
                {"source_id": "collide.beta", "class": "c1",
                 "role": "narrative_exemplar",
                 "source_sha256": [shared],
                 "runtime": "references/art-b.md",
                 "runtime_present": True,
                 "artifacts": [{"relative_path": "references/art-b.md",
                                "sha256": sha_b, "bytes": 4}],
                 "coverage": {}, "lineage": [],
                 "build_allowlist_ref": None},
            ],
        }
        path = dep / "references" / "reg.json"
        path.write_text(json.dumps(doc))
        return gg_reuse.load_registry(path), shared

    def test_i01_8_duplicate_hash_collision_no_first_pick(self):
        with tempfile.TemporaryDirectory() as td:
            reg, shared = self._collision_registry(td)
            v = gg_reuse.resolve_reuse(
                shared,
                {"kind": "narrative_reference", "keys": [KIM_KEY]},
                context=ctx(reg, packs_dir=None))
            self.assertEqual(v["status"], "blocked")
            self.assertIn("duplicate_hash_collision", v["reasons"])
            self.assertIsNone(v["selected_entry"])
            self.assertIsNone(v["disambiguator"])

    def test_i01_8_disambiguated_names_exact_token(self):
        with tempfile.TemporaryDirectory() as td:
            reg, shared = self._collision_registry(td)
            # make the two roles differ -> the request's role filter
            # selects exactly one; the disambiguator must name it
            doc = reg.document
            doc["entries"][1]["role"] = "official_guideline"
            reg2_path = reg.registry_path
            reg2_path.write_text(json.dumps(doc))
            reg2 = gg_reuse.load_registry(reg2_path)
            v = gg_reuse.resolve_reuse(
                shared,
                {"kind": "narrative_reference", "keys": [KIM_KEY]},
                context=ctx(reg2, packs_dir=None))
            self.assertEqual(v["selected_entry"], "collide.alpha")
            self.assertEqual(v["disambiguator"], "role:narrative_exemplar")

    def test_i01_9_shared_artifact_no_inheritance(self):
        # hwp shares exemplar-quality.md with pdf but declares neither
        # coverage nor lineage -> nothing inherited
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-hwp"],
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("coverage_incomplete", v["reasons"])
        self.assertIn("lineage_missing", v["reasons"])
        # same artifact, sibling entry IS reusable — proves the artifact
        # itself is fine; coverage is per-entry
        vp = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-pdf"],
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertEqual(vp["status"], "reuse_ready")

    def test_i01_10_unaccepted_catalog_never_ready(self):
        key = stat_key_of(self.reg, "rda.income.national.2024")
        # no catalog at all -> live trust fails at T1
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["rda.income.national.2024"],
            {"kind": "statistical_observation", "keys": [key]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("catalog_not_accepted", v["reasons"])


class TestI02Positives(unittest.TestCase):
    """Only the exact requested use is reuse-ready."""

    def setUp(self):
        self.reg = registry()

    def test_i02_1_exact_use_ready(self):
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["seo-minseo-finance-xlsx"],
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "reuse_ready")
        self.assertEqual(v["exit"], 0)
        self.assertEqual(v["satisfied"], [SEO_KEY])
        self.assertEqual(v["selected_entry"], "seo-minseo-finance-xlsx")

    def test_i02_1_exemplar_ready(self):
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-pdf"],
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "reuse_ready")
        self.assertEqual(v["satisfied"], [KIM_KEY])

    def test_i02_2_different_edition_not_ready(self):
        v = gg_reuse.resolve_reuse(
            "d" * 64,
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")

    def test_i02_3_unrequested_use_not_ready(self):
        # a covered kind's other key is not covered
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["seo-minseo-finance-xlsx"],
            {"kind": "template_structure",
             "keys": [{"kind": "delimited",
                       "value": "seo-minseo-finance:other-sheet"}]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("coverage_incomplete", v["reasons"])

    def test_i02_4_no_requested_use_cannot_be_ready(self):
        # the scanner path is later-lane scope; here the resolver must
        # never reach reuse_ready without a well-formed requested_use
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["seo-minseo-finance-xlsx"], None,
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertEqual(v["status"], "invalid_request")

    def test_i02_5_partial_multikey_not_rounded_up(self):
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-pdf"],
            {"kind": "narrative_reference",
             "keys": [KIM_KEY2, KIM_KEY]},   # canonical order
            context=ctx(self.reg))
        self.assertEqual(v["satisfied"], [KIM_KEY])
        self.assertEqual(v["unsatisfied"], [KIM_KEY2])
        self.assertEqual(len(v["needed_excerpt"]), 1)

    def test_i02_6_guideline_use_does_not_expand(self):
        # writing_rule reuse on a guideline must NOT become a template
        # grant — requesting template_structure on it mismatches role
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["official-writing-guide-pdf"],
            {"kind": "template_structure", "keys": [SEO_KEY]},
            context=ctx(self.reg))
        self.assertNotEqual(v["status"], "reuse_ready")
        self.assertIn("role_mismatch", v["reasons"])

    def test_i02_7_statistical_positive_via_accepted_catalog(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td, "rda.income.national.2024")
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            self.assertTrue(catalog.accepted)   # real pin, live check
            key = stat_key_of(self.reg, "rda.income.national.2024")
            kc = key_catalog_extra(
                "statistical_observation",
                gg_reuse.canonical_key(key, "statistical_observation"))
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["rda.income.national.2024"],
                {"kind": "statistical_observation", "keys": [key]},
                context=ctx(self.reg, catalog=catalog,
                            catalog_digest=catalog.digest,
                            indexes=indexes, key_catalog=kc))
            self.assertEqual(v["status"], "reuse_ready")
            self.assertEqual(v["satisfied"], [key])
            self.assertTrue(v["evidence"]["catalog"]["live_accepted"])
            self.assertEqual(v["exit"], 0)

    def test_evidence_artifact_path_rebased_to_runtime_root(self):
        # API section 1.4: evidence.artifacts[].path is relative to
        # runtime_root; SCHEMA section 3.2 declares a statistical_pack's
        # artifacts relative to resolved_packs_dir — the EMITTED path is
        # rebased from the actual lookup target while the lookup itself
        # stays packs-relative and needed_excerpt.artifact stays
        # entry-relative.
        key = stat_key_of(self.reg, "rda.income.national.2024")
        v = gg_reuse.resolve_reuse(
            SOURCE_SHA["rda.income.national.2024"],
            {"kind": "statistical_observation", "keys": [key]},
            context=ctx(self.reg))
        arts = v["evidence"]["artifacts"]
        self.assertTrue(arts)
        for a in arts:
            self.assertTrue(
                a["path"].startswith("references/benchmark-packs/"),
                "statistical evidence path not runtime_root-relative: %r"
                % a["path"])
            # the emitted path resolves against runtime_root and the
            # observed hash still came from the packs-relative lookup
            self.assertTrue(
                (self.reg.resolved_base / a["path"]).is_file())
            self.assertEqual(a["observed_sha256"], a["declared_sha256"])
        # needed_excerpt.artifact remains the entry's declared
        # relative_path (packs-root-relative), NOT the rebased form
        for ex in v["needed_excerpt"]:
            self.assertFalse(
                ex["artifact"].startswith("references/benchmark-packs/"))
        # a non-statistical entry is unaffected — its declared
        # relative_path already resolves under runtime_root
        v2 = gg_reuse.resolve_reuse(
            SOURCE_SHA["kim-wonseop-exemplar-pdf"],
            {"kind": "narrative_reference", "keys": [KIM_KEY]},
            context=ctx(self.reg))
        self.assertEqual(v2["evidence"]["artifacts"][0]["path"],
                         "references/exemplar-quality.md")


class TestCU7(unittest.TestCase):
    """CU.7 — v6 schema/trust conformance, three controls."""

    def setUp(self):
        self.reg = registry()

    def test_cu7_registry_validates_p5_v3(self):
        self.assertEqual(self.reg.document["schema_version"],
                         "p5-reuse-registry/3")
        ids = {e["source_id"] for e in self.reg.document["entries"]}
        for bid in BASELINE_IDS:
            self.assertIn(bid, ids, "mandatory baseline entry %s" % bid)

    def test_cu7_portable_relocation(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "moved"
            shutil.copytree(KNUAF_DOC, dst)
            reg2 = gg_reuse.load_registry(
                dst / "references" / "builtin-sources.json")
            self.assertEqual(reg2.resolved_base, dst)
            # portable document digest identical — resolutions never
            # enter the serialized digest
            self.assertEqual(reg2.registry_digest, self.reg.registry_digest)

    def test_cu7_negative_a_supplied_digest_never_consulted(self):
        # a dict asserting the pinned digest is not a catalog
        real_pin = research.PINNED_CATALOG_AUTHORITIES[
            AUTHORITY["rda.income.national.2024"]]
        bad = ctx(self.reg,
                  catalog={"entries": {}, "digest": real_pin,
                           "accepted": True},
                  catalog_digest=real_pin)
        key = stat_key_of(self.reg, "rda.income.national.2024")
        with self.assertRaises(ValueError) as cm:
            gg_reuse.resolve_reuse(
                SOURCE_SHA["rda.income.national.2024"],
                {"kind": "statistical_observation", "keys": [key]},
                context=bad)
        self.assertIn("catalog", str(cm.exception))

    def test_cu7_negative_b_live_invalidation(self):
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td, "rda.income.national.2024")
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            self.assertTrue(catalog.accepted)
            good_digest = catalog.digest
            # mutate content AFTER legitimate construction — the live
            # properties must recompute differently
            doomed = next(iter(catalog.entries))
            catalog.entries.pop(doomed)
            self.assertFalse(catalog.accepted)
            # the mutation also drifts the live digest away from the
            # bind-time context["catalog_digest"] — both SCHEMA section
            # 6.4 legs fail at once
            self.assertNotEqual(catalog.digest, good_digest)
            key = stat_key_of(self.reg, "rda.income.national.2024")
            kc = key_catalog_extra(
                "statistical_observation",
                gg_reuse.canonical_key(key, "statistical_observation"))
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["rda.income.national.2024"],
                {"kind": "statistical_observation", "keys": [key]},
                context=ctx(self.reg, catalog=catalog,
                            catalog_digest=good_digest,
                            indexes=indexes, key_catalog=kc))
            self.assertEqual(v["status"], "blocked")
            self.assertEqual(v["exit"], 2)
            # CU.7-B requires the COMPLETE union: live .accepted False
            # AND live .digest != context["catalog_digest"] — both
            # reasons MUST be present (the digest check is not
            # else-chained off the acceptance check).
            self.assertIn("catalog_not_accepted", v["reasons"])
            self.assertIn("catalog_digest_mismatch", v["reasons"])

    def test_cu7_positive_control_paired(self):
        # required pair for the negative controls: real accepted catalog
        # -> reuse_ready on a covered statistical_pack key (I02.7)
        if not ACCEPTED_ROOT.exists():
            self.skipTest("accepted g005 catalog tree not present")
        with tempfile.TemporaryDirectory() as td:
            export = write_export(td, "rda.income.national.2024")
            catalog, indexes = gg_reuse.load_accepted_catalog(
                export, registry=self.reg,
                packs_dir=self.reg.resolved_packs_dir)
            self.assertTrue(catalog.accepted)
            key = stat_key_of(self.reg, "rda.income.national.2024")
            kc = key_catalog_extra(
                "statistical_observation",
                gg_reuse.canonical_key(key, "statistical_observation"))
            v = gg_reuse.resolve_reuse(
                SOURCE_SHA["rda.income.national.2024"],
                {"kind": "statistical_observation", "keys": [key]},
                context=ctx(self.reg, catalog=catalog,
                            catalog_digest=catalog.digest,
                            indexes=indexes, key_catalog=kc))
            self.assertEqual(v["status"], "reuse_ready")


class TestI01Matrix(unittest.TestCase):
    """I01.11 — entry x criterion negative matrix over the shipped
    registry's final bytes (mandatory baseline entry set)."""

    def test_i01_11_matrix(self):
        reg = registry()
        doc = reg.document
        entries = {e["source_id"]: e for e in doc["entries"]}
        for bid in BASELINE_IDS:
            self.assertIn(bid, entries)

        kind_for = {"statistical_pack": "statistical_observation",
                    "official_guideline": "writing_rule",
                    "narrative_exemplar": "narrative_reference",
                    "primary_finance_template": "template_structure",
                    "secondary_finance_exemplar": "template_structure",
                    # reference_only admits no use: any requested kind is
                    # a role mismatch, so the probe still exercises it.
                    "reference_only": "narrative_reference"}
        wrong_kind = {"statistical_pack": "writing_rule",
                      "official_guideline": "narrative_reference",
                      "narrative_exemplar": "writing_rule",
                      "primary_finance_template": "narrative_reference",
                      "secondary_finance_exemplar": "writing_rule",
                      "reference_only": "writing_rule"}
        delimited_probe = {"kind": "delimited",
                           "value": "matrix-probe:uncovered"}
        # well-formed but NOT coverage-claimed -> coverage_incomplete
        stat_probe = {"kind": "audit_key",
                      "pack_id": "rda.income.national.2024",
                      "records_file_sha256":
                          "6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b1527b0",
                      "physical_jsonl_line_1based": 999,
                      "raw_line_sha256": "0" * 64}
        shared_art = {"references/exemplar-quality.md",
                      "references/official-toc.md",
                      "references/industrial-insects/precedents.json"}

        matrix = {}
        for sid, entry in entries.items():
            kind = kind_for[entry["role"]]
            probe = stat_probe if kind == "statistical_observation" \
                else delimited_probe
            cells = {}
            use = {"kind": kind, "keys": [probe]}
            c = ctx(reg)

            v = gg_reuse.resolve_reuse(
                SOURCE_SHA[sid], use, context=c)
            cells["I01.verdict"] = v["status"]
            cells["I01.reasons"] = v["reasons"]

            # I01.3 — wrong role for the requested use
            vv = gg_reuse.resolve_reuse(
                SOURCE_SHA[sid],
                {"kind": wrong_kind[entry["role"]],
                 "keys": [delimited_probe]}, context=c)
            cells["I01.3"] = ("role_mismatch" in vv["reasons"])

            # I01.4 — coverage-incomplete probe (an uncovered key)
            cells["I01.4"] = ("coverage_incomplete" in v["reasons"])

            # I01.6 — malformed request against this entry
            bad = {"kind": kind, "keys": [
                dict(probe, physical_jsonl_line_1based=True)
                if kind == "statistical_observation"
                else {"kind": "delimited", "value": "BAD:UPPER"}]}
            vm = gg_reuse.resolve_reuse(SOURCE_SHA[sid], bad, context=c)
            cells["I01.6"] = (vm["status"] == "invalid_request")

            # I01.1 — missing artifact: real where the artifact is
            # absent at runtime; otherwise exercised via an empty
            # runtime_root (union also reports runtime_base_mismatch)
            arts = entry.get("artifacts") or []
            if arts:
                missing_now = any(
                    not ((reg.resolved_packs_dir
                          if entry["role"] == "statistical_pack"
                          else reg.resolved_base) / a["relative_path"]
                         ).is_file()
                    for a in arts)
                if missing_now:
                    cells["I01.1"] = ("runtime_missing" in v["reasons"])
                else:
                    with tempfile.TemporaryDirectory() as td:
                        ve = gg_reuse.resolve_reuse(
                            SOURCE_SHA[sid], use,
                            context=ctx(reg, runtime_root=Path(td)))
                        cells["I01.1"] = (
                            "runtime_missing" in ve["reasons"]
                            or "runtime_base_mismatch" in ve["reasons"])
            else:
                cells["I01.1"] = "n/a-no-artifacts-declared"

            # I01.9 — shared physical artifact => no inheritance
            if any(a["relative_path"] in shared_art for a in arts):
                cells["I01.9"] = (
                    v["status"] != "reuse_ready"
                    or "coverage_incomplete" in v["reasons"]
                    or "runtime_missing" in v["reasons"])
            else:
                cells["I01.9"] = "n/a-unshared-artifact"

            # I01.10 — statistical: unaccepted catalog never ready
            if kind == "statistical_observation":
                cells["I01.10"] = (
                    "catalog_not_accepted" in v["reasons"]
                    or "verification_absent" in v["reasons"])
            else:
                cells["I01.10"] = "n/a-non-statistical"

            # I01.5/I01.7 entry-independent — exercised separately
            cells["I01.5"] = cells["I01.7"] = "entry-independent"
            # I01.2 exercised via relocation+tamper elsewhere
            cells["I01.2"] = "see test_i01_2_tampered_artifact_bytes"
            cells["I01.8"] = "n/a-no-colliding-hashes-in-registry"
            matrix[sid] = cells

        # every applicable cell must hold its required observation
        for sid, cells in matrix.items():
            self.assertTrue(cells["I01.3"], "%s I01.3" % sid)
            self.assertTrue(cells["I01.4"], "%s I01.4" % sid)
            self.assertTrue(cells["I01.6"], "%s I01.6" % sid)
            self.assertNotEqual(cells["I01.verdict"], "reuse_ready",
                                "%s must not be ready on a probe key"
                                % sid)
        # Keep the diagnostic inside this test's own temporary directory.
        # LANE_ROOT resolves to the user's home in a standalone worktree.
        with tempfile.TemporaryDirectory(prefix="knuaf-i01-matrix-") as td:
            out = Path(td) / "i01-11-matrix.json"
            out.write_text(json.dumps(matrix, ensure_ascii=False,
                                      indent=1), encoding="utf-8")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                             matrix)
        print(json.dumps(matrix, ensure_ascii=False, indent=1))


class TestCommonWorkbooks(unittest.TestCase):
    """P9: common Kang X01/X02 registration.  P9-D3: the duplicated
    hort-raw registry entry was removed; the H01 base identity lives in
    the reference set's known_workbooks, and a hort module registers its
    own workbook (F2 one-hash-one-entry invariant below)."""

    def setUp(self):
        self.reg = registry()

    @staticmethod
    def _keys(*values):
        return {"template_structure": {
            v: {"authority": "verified", "catalog_digest": None,
                "receipt_sha256": None} for v in values}}

    def test_registry_entries(self):
        entries = {e["source_id"]: e
                   for e in self.reg.document["entries"]}
        for sid, sha, key in (
                ("kang-finance-workbook-x01",
                 "5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80",
                 "kang-finance-workbook:x01-structure"),
                ("kang-finance-workbook-x02",
                 "54d4e55cd57bcb1db0fbfaafe2ed42422767f1561959b8a4f3c673daf77d7e78",
                 "kang-finance-workbook:x02-structure")):
            e = entries[sid]
            self.assertEqual(e["source_sha256"], [sha])
            self.assertEqual(e["role"], "primary_finance_template")
            self.assertTrue(e["runtime_present"])
            claimed = {c["key_ref"]["value"]
                       for c in e["coverage"]["template_structure"]["keys"]}
            self.assertEqual(claimed, {key})
            self.assertEqual(
                e["lineage"][0]["to"],
                "references/common-workbooks/"
                "kang-finance-workbook.json")
            self.assertEqual(e["lineage"][0]["verification"], "verified")
        # E1 (P9-D3): the hort module owns registering its own workbook —
        # the duplicated reference_only entry is gone
        self.assertNotIn("hort-env-kang-raw-workbook", entries)
        # D-C rename: no student number in any id
        self.assertIn("specialty-grad-thesis-finance-xlsx", entries)

    def test_ids_and_labels_carry_no_digit_runs(self):
        # Hygiene: a 6+ digit run in a public identifier is the shape of
        # a student number — none may appear in registry source_ids or
        # in reference-set / extract ref_ids and labels.
        digit_run = re.compile(r"\d{6,}")
        separators = re.compile(r"[\s\-_.:/·,()]")
        names = [e["source_id"]
                 for e in self.reg.document["entries"]]
        refset = json.loads((KNUAF_DOC / "references" /
                             "workbook-reference-set.json").read_text(
                                 encoding="utf-8"))
        for block in ("members", "known_workbooks"):
            for e in refset.get(block, []):
                names.extend([e["ref_id"], e["label"]])
        extract = json.loads(
            (KNUAF_DOC / "references" / "common-workbooks" /
             "kang-finance-workbook.json").read_text(encoding="utf-8"))
        for e in extract["members"].values():
            names.extend([e["ref_id"], *e["labels"].values()])
        offenders = [n for n in names
                     if digit_run.search(separators.sub("", n))]
        self.assertEqual(offenders, [])

    def test_kang_x01_and_x02_reuse_ready(self):
        for sid, sha, key in (
                ("kang-finance-workbook-x01",
                 "5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80",
                 "kang-finance-workbook:x01-structure"),
                ("kang-finance-workbook-x02",
                 "54d4e55cd57bcb1db0fbfaafe2ed42422767f1561959b8a4f3c673daf77d7e78",
                 "kang-finance-workbook:x02-structure")):
            kc = self._keys(key)
            v = gg_reuse.resolve_reuse(
                sha,
                {"kind": "template_structure",
                 "keys": [{"kind": "delimited", "value": key}]},
                context=ctx(self.reg, key_catalog=kc))
            self.assertEqual(v["status"], "reuse_ready", v)
            self.assertEqual(v["selected_entry"], sid)
            self.assertEqual(
                v["evidence"]["lineage"]["to"],
                "references/common-workbooks/"
                "kang-finance-workbook.json")

    def test_kang_key_without_catalog_claim_conflicts(self):
        key = {"kind": "delimited",
               "value": "kang-finance-workbook:x01-structure"}
        v = gg_reuse.resolve_reuse(
            "5d19b6a2e5dcddccb70c68e39abee8f423bba005c008db7b64076326a17b9c80",
            {"kind": "template_structure", "keys": [key]},
            context=ctx(self.reg))
        self.assertEqual(v["status"], "blocked")
        self.assertIn("authority_conflict", v["reasons"])

    def test_no_sha256_in_more_than_one_registry_entry(self):
        # F2 (P9-D3): one source hash may never back two entries — the
        # same bytes could resolve through either owner otherwise.  On
        # the hort module owns the H01 hash in exactly one entry.
        owners = {}
        for e in self.reg.document["entries"]:
            for sha in e.get("source_sha256") or []:
                owners.setdefault(sha, []).append(e["source_id"])
        dup = {s: ids for s, ids in owners.items() if len(ids) > 1}
        self.assertEqual(dup, {})


if __name__ == "__main__":

    unittest.main(verbosity=2)
