#!/usr/bin/env python3
"""Lane A SCHEMA section 4 key-identity tests — the structured AuditKey
domain (P5-narrowed), canonical serialization, ordering, and the
explicit bool rejection.

Run: python3 -B tests/test_gg_reuse_auditkey.py
"""
import json
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
KNUAF_DOC = TESTS.parent
SCRIPTS = KNUAF_DOC / "scripts"

sys.path.insert(0, str(SCRIPTS))
import gg_reuse  # noqa: E402
import gg_rda_provenance as prov  # noqa: E402
import gg_rda_research as research  # noqa: E402

RECORDS = ("6feec51322ab6ffeacd8131a03165ba06f2f8a64d75058010f4f36a50b152"
           "7b0")
RAW = ("298fba9503617abd2809e777f795d2008f88cc0aba287f4a90639caa6d89b34"
       "4")
BASE = {"kind": "audit_key", "pack_id": "rda.income.national.2024",
        "records_file_sha256": RECORDS, "physical_jsonl_line_1based": 1,
        "raw_line_sha256": RAW}
EMPTY_CATALOG = {k: {} for k in ("writing_rule", "statistical_observation",
                                 "template_structure",
                                 "narrative_reference")}


class TestAuditKeyDomain(unittest.TestCase):
    """P5 domain is narrower than the parent domain: pack_id grammar,
    lowercase 64-hex, int>=1 line with EXPLICIT bool rejection."""

    def _use(self, key):
        return {"kind": "statistical_observation", "keys": [key]}

    def test_valid_key_accepted(self):
        kind, keys = gg_reuse.validate_requested_use(
            self._use(BASE), key_catalog=EMPTY_CATALOG)
        self.assertEqual(kind, "statistical_observation")
        self.assertEqual(keys[0], BASE)

    def test_bool_line_rejected(self):
        for bad in (True, False):
            with self.assertRaises(ValueError):
                gg_reuse.validate_requested_use(
                    self._use(dict(BASE, physical_jsonl_line_1based=bad)),
                    key_catalog=EMPTY_CATALOG)

    def test_line_must_be_int_ge_1(self):
        for bad in (0, -3, "1", 1.5, None):
            with self.assertRaises(ValueError, msg="line %r" % bad):
                gg_reuse.validate_requested_use(
                    self._use(dict(BASE, physical_jsonl_line_1based=bad)),
                    key_catalog=EMPTY_CATALOG)

    def test_uppercase_hex_rejected_not_lowercased(self):
        for field in ("records_file_sha256", "raw_line_sha256"):
            bad = dict(BASE)
            bad[field] = BASE[field].upper()
            with self.assertRaises(ValueError):
                gg_reuse.validate_requested_use(
                    self._use(bad), key_catalog=EMPTY_CATALOG)

    def test_pack_id_outside_p5_domain(self):
        for bad in ("BAD PACK", "Upper", "", "-lead", "a:b", None, 5):
            with self.assertRaises(ValueError, msg="pack_id %r" % bad):
                gg_reuse.validate_requested_use(
                    self._use(dict(BASE, pack_id=bad)),
                    key_catalog=EMPTY_CATALOG)

    def test_wrong_discriminator_rejected(self):
        bad = dict(BASE)
        bad["kind"] = "delimited"
        with self.assertRaises(ValueError):
            gg_reuse.validate_requested_use(
                self._use(bad), key_catalog=EMPTY_CATALOG)

    def test_delimited_grammar(self):
        good = {"kind": "delimited", "value": "abc-def_1.2:x-y"}
        kind, keys = gg_reuse.validate_requested_use(
            {"kind": "narrative_reference", "keys": [good]},
            key_catalog=EMPTY_CATALOG)
        self.assertEqual(keys[0], good)
        for badv in ("noColon", "a:b:c", "A:b", "a:B", ":b", "a:",
                     "-a:b", "a:" + "x" * 65):
            with self.assertRaises(ValueError, msg="value %r" % badv):
                gg_reuse.validate_requested_use(
                    {"kind": "narrative_reference",
                     "keys": [{"kind": "delimited", "value": badv}]},
                    key_catalog=EMPTY_CATALOG)

    def test_empty_and_malformed_requests(self):
        for bad in (None, {}, {"kind": "bogus"}, {"keys": []},
                    {"kind": "narrative_reference"},
                    {"kind": "narrative_reference", "keys": "x"},
                    {"kind": "statistical_observation", "keys": []}):
            with self.assertRaises(ValueError, msg="use %r" % bad):
                gg_reuse.validate_requested_use(
                    bad, key_catalog=EMPTY_CATALOG)

    def test_unsorted_and_duplicate_rejected(self):
        a = {"kind": "delimited", "value": "b:x"}
        b = {"kind": "delimited", "value": "a:x"}
        with self.assertRaises(ValueError):   # unsorted
            gg_reuse.validate_requested_use(
                {"kind": "narrative_reference", "keys": [a, b]},
                key_catalog=EMPTY_CATALOG)
        with self.assertRaises(ValueError):   # duplicate
            gg_reuse.validate_requested_use(
                {"kind": "narrative_reference", "keys": [b, b]},
                key_catalog=EMPTY_CATALOG)


class TestCanonicalSerialization(unittest.TestCase):
    """SCHEMA 4.3: statistical keys serialize as sorted canonical JSON of
    the four identity fields — identical to the parent's
    catalog_content_digest key form. No unit-separator joins."""

    def test_canonical_key_matches_parent_form(self):
        norm = prov.normalize_audit_key(
            {f: BASE[f] for f in ("pack_id", "records_file_sha256",
                                  "physical_jsonl_line_1based",
                                  "raw_line_sha256")})
        parent_form = json.dumps(prov.audit_key_dict(norm),
                                 sort_keys=True)
        ours = gg_reuse.canonical_key(BASE, "statistical_observation")
        self.assertEqual(ours, parent_form)
        # and it is the same key form the digest machinery emits
        digested = research.catalog_content_digest({norm: {}})
        self.assertIsInstance(digested, str)
        self.assertNotIn("\x1f", ours)      # no unit separator
        self.assertEqual(json.loads(ours), prov.audit_key_dict(norm))

    def test_delimited_canonical_is_value(self):
        k = {"kind": "delimited", "value": "a:b"}
        self.assertEqual(gg_reuse.canonical_key(k, "writing_rule"), "a:b")

    def test_statistical_canonical_field_set(self):
        ser = json.loads(gg_reuse.canonical_key(BASE,
                                                "statistical_observation"))
        self.assertEqual(set(ser), {"pack_id", "records_file_sha256",
                                    "physical_jsonl_line_1based",
                                    "raw_line_sha256"})
        # pack_revision / record_id are metadata — never identity
        self.assertNotIn("pack_revision", ser)
        self.assertNotIn("record_id", ser)

    def test_metadata_fields_not_identity_substitutes(self):
        # a key carrying record_id/pack_revision still normalizes to the
        # four identity fields only
        k = dict(BASE)
        k["record_id"] = "whatever"
        k["pack_revision"] = "2024"
        kind, keys = gg_reuse.validate_requested_use(
            {"kind": "statistical_observation", "keys": [k]},
            key_catalog=EMPTY_CATALOG)
        self.assertEqual(set(keys[0]), set(BASE) | {"kind"})


class TestOrdering(unittest.TestCase):
    """Canonical order: AuditKey tuple order for statistical keys;
    value-string order for delimited keys."""

    def test_tuple_order(self):
        k2 = dict(BASE, physical_jsonl_line_1based=2)
        kind, keys = gg_reuse.validate_requested_use(
            {"kind": "statistical_observation", "keys": [BASE, k2]},
            key_catalog=EMPTY_CATALOG)
        self.assertEqual(keys, (BASE, k2))
        with self.assertRaises(ValueError):
            gg_reuse.validate_requested_use(
                {"kind": "statistical_observation", "keys": [k2, BASE]},
                key_catalog=EMPTY_CATALOG)

    def test_classify_pure_lookup(self):
        reg = gg_reuse.load_registry(
            KNUAF_DOC / "references" / "builtin-sources.json")
        hits = gg_reuse.classify_source(
            reg, "457249255929275b3ee55383bdc36897274f08933af3536866a652"
            "b29346cd3a")
        self.assertEqual([h["source_id"] for h in hits],
                         ["seo-minseo-finance-xlsx"])
        self.assertEqual(gg_reuse.classify_source(reg, "0" * 64), [])
        with self.assertRaises(ValueError):
            gg_reuse.classify_source(reg, "nothex")
        with self.assertRaises(ValueError):
            gg_reuse.classify_source(reg.document, "0" * 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
