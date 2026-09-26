"""industrial_insects built-in references in the reuse registry (P9).

Owner intent: a user who hands over the same example PDF gets the shipped
locator map (precedents.json) instead of a re-parse.  The registry only
*judges* reuse — nothing in code forbids a re-parse — so these tests lock
the verdicts the guidance relies on:

- F0/E1–E4 PDFs: narrative_exemplar over precedents.json, one key each,
  reuse_ready with that key only (no inheritance between the five entries
  sharing the artifact);
- E3/E4 HWP: identity only -> needs_excerpt (HWP/PDF sameness unmeasured);
- R0/R1: reference_only identity -> every use is a role_mismatch;
- student examples carry title and name only — no student ID, file name.

Source bytes are never needed: resolve_reuse takes the source hash.
"""
import copy
import json
import re
import shutil
import tempfile
from pathlib import Path

from tests._harness import SCRIPTS, ContractCase, runtime

KNUAF_DOC = SCRIPTS.parent
REGISTRY = KNUAF_DOC / "references" / "builtin-sources.json"
PRECEDENTS = KNUAF_DOC / "references" / "industrial-insects" / "precedents.json"
ART = "references/industrial-insects/precedents.json"

PDF = {
    "f0": "d582d884abdd21edf622fc0973844abbc4c62369bf7e92574da3d4e33ae75885",
    "e1": "9668c9abc68f750039e940bb78f995794696b6dd14b1b5714cd74e81e485a36d",
    "e2": "2527c590183666543ecb9d23e20e6f5f2a5d0f3215c1ba09577dcdb9912ca0d2",
    "e3": "b91fefd7ff77dbec1752a503c2c3238c7d66058d35b4e25cb9f1237842bf4f44",
    "e4": "198fd1e140ad41ec8612eafd484cfc46ac5cfea8d318408cc38a0a241ea90d55",
}
HWP = {
    "e3-hwp": "40a537112c252f55351f9b40714de97108381eb7f47a828b84e88041b85e960c",
    "e4-hwp": "922c56473d6d7d614d7ef18a8a07f2fd3eb16aa16acc7f8df26170aa9a0fd990",
}
STAT = {
    "r0": "3dd2b75f5588d1b710123f56cc37747521dee786a2c4a13ecf5e6ab6e66541be",
    "r1": "2582fc7703bb3c409225e1a7329a6a591513500bd0a827e3e9f1807b2bcebe8a",
}
USES = ("narrative_reference", "writing_rule", "template_structure")


def _key(name):
    return {"kind": "delimited",
            "value": "insect-precedents:%s-locator-map" % name}


def _catalog(authority="verified", drop=None):
    keys = {
        _key(n)["value"]: {"authority": authority, "catalog_digest": None,
                           "receipt_sha256": None}
        for n in PDF if n != drop}
    return {"narrative_reference": keys, "writing_rule": {},
            "template_structure": {}, "statistical_observation": {}}


class InsectReuseRegistryTests(ContractCase):
    def setUp(self):
        self.reuse = runtime("gg_reuse")
        self.reg = self.reuse.load_registry(REGISTRY)
        self.entries = {e["source_id"]: e
                        for e in self.reg.document["entries"]}

    def _ctx(self, reg=None, catalog=None):
        reg = reg or self.reg
        return {"root": KNUAF_DOC.parents[1], "runtime_root": reg.resolved_base,
                "registry": reg,
                "key_catalog": catalog if catalog is not None else _catalog(),
                "catalog": None, "catalog_digest": None, "indexes": {},
                "scan_policy": None, "packs_dir": reg.resolved_packs_dir}

    def _resolve(self, sha, name=None, kind="narrative_reference", **kw):
        use = {"kind": kind, "keys": [_key(name or "f0")]}
        return self.reuse.resolve_reuse(sha, use, context=self._ctx(**kw))

    def _copied_registry(self, mutate):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-insect-reuse-")
        self.addCleanup(tmp.cleanup)
        dst = Path(tmp.name) / "knuaf-doc"
        shutil.copytree(KNUAF_DOC, dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
        path = dst / "references" / "builtin-sources.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        mutate(doc, dst)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        return self.reuse.load_registry(path)

    # -- declaration ---------------------------------------------------

    def test_entries_declared_with_exact_identity(self):
        for name, sha in PDF.items():
            e = self.entries["insect.example." + name]
            self.assertEqual([sha], e["source_sha256"])
            self.assertEqual("narrative_exemplar", e["role"])
            self.assertEqual([ART], [a["relative_path"] for a in e["artifacts"]])
            self.assertEqual(
                "industrial_insects_example" if name == "f0"
                else "industrial_insects_student_example", e["class"])
        for name, sha in HWP.items():
            e = self.entries["insect.example." + name]
            self.assertEqual([sha], e["source_sha256"])
            self.assertEqual(([], {}, [], False),
                             (e["artifacts"], e["coverage"], e["lineage"],
                              e["runtime_present"]))
        for name, sha in STAT.items():
            e = self.entries["insect.stat." + name]
            self.assertEqual("reference_only", e["role"])
            self.assertEqual(([], {}, [], None),
                             (e["artifacts"], e["coverage"], e["lineage"],
                              e["runtime"]))

    def test_artifact_pin_matches_shipped_map_and_lineage_is_verified(self):
        import hashlib
        blob = PRECEDENTS.read_bytes()
        for name in PDF:
            e = self.entries["insect.example." + name]
            art = e["artifacts"][0]
            self.assertEqual(hashlib.sha256(blob).hexdigest(), art["sha256"])
            self.assertEqual(len(blob), art["bytes"])
            lin = e["lineage"][0]
            self.assertEqual(("derived_excerpt", "verified"),
                             (lin["relationship"], lin["verification"]))
            self.assertTrue(lin["verifier"].startswith(
                "independent-verify-precedents-" + art["sha256"][:12]))
            self.assertRegex(lin["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_student_identity_is_title_and_name_only(self):
        doc = json.loads(PRECEDENTS.read_text(encoding="utf-8"))
        texts = []
        for src in doc["sources"]:
            texts += [str(src.get("title")), str(src.get("author"))]
        for e in self.entries.values():
            if e["source_id"].startswith("insect."):
                texts += [e["source_id"], e["class"], str(e["runtime"])]
                texts += [str(l.get("verifier")) for l in e["lineage"]]
        blob = "\n".join(texts)
        self.assertIsNone(re.search(r"\d{8,}", blob), "student-ID-like digits")
        self.assertNotRegex(blob.lower(), r"\.(pdf|hwp|hwpx|xlsx)\b")

    # -- verdicts -------------------------------------------------------

    def test_each_example_is_ready_on_its_own_key_only(self):
        for name, sha in PDF.items():
            with self.subTest(case=name):
                v = self._resolve(sha, name)
                self.assertEqual("reuse_ready", v["status"], v["reasons"])
                other = "e1" if name == "f0" else "f0"
                v = self._resolve(sha, other)
                self.assertNotEqual("reuse_ready", v["status"])
                self.assertIn("coverage_incomplete", v["reasons"])

    def test_lineage_removed_on_one_entry_does_not_touch_its_peers(self):
        def drop_e1_lineage(doc, _dst):
            for e in doc["entries"]:
                if e["source_id"] == "insect.example.e1":
                    e["lineage"] = []
        reg = self._copied_registry(drop_e1_lineage)
        v = self._resolve(PDF["e1"], "e1", reg=reg)
        self.assertIn("lineage_missing", v["reasons"])
        self.assertNotEqual("reuse_ready", v["status"])
        self.assertEqual("reuse_ready",
                         self._resolve(PDF["f0"], "f0", reg=reg)["status"])

    def test_catalog_and_registry_authority_gate_readiness(self):
        v = self._resolve(PDF["e2"], "e2", catalog=_catalog(drop="e2"))
        self.assertIn("authority_conflict", v["reasons"])
        v = self._resolve(PDF["e2"], "e2", catalog=_catalog("unverified"))
        self.assertIn("coverage_incomplete", v["reasons"])

        def unverify_e3(doc, _dst):
            for e in doc["entries"]:
                if e["source_id"] == "insect.example.e3":
                    for k in e["coverage"]["narrative_reference"]["keys"]:
                        k["authority"] = "unverified"
        reg = self._copied_registry(unverify_e3)
        v = self._resolve(PDF["e3"], "e3", reg=reg)
        self.assertIn("coverage_incomplete", v["reasons"])

    def test_changed_map_bytes_block_reuse(self):
        def tamper(_doc, dst):
            p = dst / ART
            p.write_bytes(p.read_bytes() + b"\n")
        reg = self._copied_registry(tamper)
        v = self._resolve(PDF["f0"], "f0", reg=reg)
        self.assertEqual("blocked", v["status"])
        self.assertIn("runtime_hash_mismatch", v["reasons"])

    def test_hwp_twins_need_their_own_excerpt(self):
        for name, sha in HWP.items():
            with self.subTest(case=name):
                v = self._resolve(sha, name.split("-")[0])
                self.assertEqual("needs_excerpt", v["status"], v["reasons"])

    def test_official_statistics_are_identity_only(self):
        for name, sha in STAT.items():
            for kind in USES:
                with self.subTest(name=name, kind=kind):
                    v = self._resolve(sha, kind=kind)
                    self.assertEqual("blocked", v["status"])
                    self.assertIn("role_mismatch", v["reasons"])
