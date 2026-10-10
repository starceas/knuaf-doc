"""Regression guard for the four reference docs restored from PR #1.

These files lived only on the rda-intake branch and were lost when the
public candidate tree was replaced.  Fails when any doc is missing from
skills/knuaf-doc/references/, absent from install-manifest.json, or not
linked from SKILL.md.
"""

import json
import unittest

from tests._harness import REPO_ROOT

SKILL_DIR = REPO_ROOT / "skills" / "knuaf-doc"
REFERENCES_DIR = SKILL_DIR / "references"
MANIFEST = SKILL_DIR / "install-manifest.json"
SKILL_MD = SKILL_DIR / "SKILL.md"

RESTORED_DOCS = (
    "lessons-learned.md",
    "kim-finance-bible.md",
    "open-questions.md",
    "rda-benchmark.md",
)


class RestoredReferenceTests(unittest.TestCase):
    def test_docs_exist_in_references(self):
        missing = [
            name for name in RESTORED_DOCS
            if not (REFERENCES_DIR / name).is_file()
        ]
        self.assertEqual([], missing)

    def test_docs_in_install_manifest(self):
        files = json.loads(
            MANIFEST.read_text(encoding="utf-8")
        ).get("files", {})
        missing = [
            name for name in RESTORED_DOCS
            if f"references/{name}" not in files
        ]
        self.assertEqual([], missing)

    def test_skill_md_links_docs(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        missing = [
            name for name in RESTORED_DOCS
            if f"references/{name}" not in text
        ]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
