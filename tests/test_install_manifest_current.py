"""The committed install manifest must describe the shipped skill tree.

``gg_update`` refuses an incoming release whose ``install-manifest.json``
does not list exactly the files and SHA-256 hashes of the skill tree
(``zip_invalid`` / ``manifest_mismatch``).  Changing a skill file without
running ``gg_update.py manifest --write`` therefore breaks every user
update of the next release.  This case runs the deployed generator and the
deployed acceptance check against the real tree, so exclusions
(``__pycache__``, ``*.pyc``, ``.DS_Store``, the manifest itself) follow the
generator rather than a list kept here.
"""
import json
import unittest

from tests._harness import SCRIPTS, runtime

SKILL_DIR = SCRIPTS.parent


class InstallManifestMatchesTree(unittest.TestCase):
    def test_committed_manifest_equals_generated_manifest(self):
        gu = runtime("gg_update")
        committed = json.loads(
            (SKILL_DIR / gu.MANIFEST_NAME).read_text(encoding="utf-8"))
        generated = gu.build_install_manifest(SKILL_DIR)
        declared, actual = committed.get("files", {}), generated["files"]
        drift = {
            "hash_mismatch": sorted(
                k for k in declared.keys() & actual.keys()
                if declared[k] != actual[k]),
            "unlisted": sorted(actual.keys() - declared.keys()),
            "listed_but_absent": sorted(declared.keys() - actual.keys()),
        }
        self.assertEqual(
            {"hash_mismatch": [], "unlisted": [], "listed_but_absent": []},
            drift,
            "install-manifest.json is stale; run "
            "`python3 skills/knuaf-doc/scripts/gg_update.py manifest --write`")
        self.assertEqual(generated["schema"], committed.get("schema"))
        # The same tree must pass the check an incoming release goes through.
        gu._check_new_tree_manifest(SKILL_DIR)


if __name__ == "__main__":
    unittest.main()
