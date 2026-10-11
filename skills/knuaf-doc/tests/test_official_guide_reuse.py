"""Proposed deployed normal-path test (one test).

Use D1_TEST_RUNTIME only while exercising the scoped draft copy.
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

RUNTIME = Path(os.environ.get("D1_TEST_RUNTIME", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(RUNTIME / "scripts"))
import gg_reuse


class TestOfficialGuideRestoration(unittest.TestCase):
    def test_verified_pdf_toc_rules_are_available(self):
        reg = gg_reuse.load_registry(RUNTIME / "references" / "builtin-sources.json")
        entry = next(e for e in reg.document["entries"] if e["source_id"] == "official-writing-guide-pdf")
        self.assertTrue(entry["runtime_present"])
        self.assertEqual(len(entry["artifacts"]), 9)
        for artifact in entry["artifacts"]:
            actual = (RUNTIME / artifact["relative_path"]).read_bytes()
            self.assertEqual(len(actual), artifact["bytes"])
            self.assertEqual(hashlib.sha256(actual).hexdigest(), artifact["sha256"])
        key = {"kind": "delimited", "value": "official-writing-guide:toc-rules"}
        v = gg_reuse.resolve_reuse(entry["source_sha256"][0], {"kind": "writing_rule", "keys": [key]}, context={"root": RUNTIME, "runtime_root": reg.resolved_base, "registry": reg, "key_catalog": {"writing_rule": {key["value"]: {"authority": "verified", "catalog_digest": None, "receipt_sha256": None}}}, "catalog": None, "catalog_digest": None, "indexes": {}, "scan_policy": None, "packs_dir": reg.resolved_packs_dir})
        self.assertEqual(v["status"], "reuse_ready")
        self.assertEqual(v["exit"], 0)
        self.assertEqual(v["satisfied"], [key])
        self.assertEqual(v["reasons"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
