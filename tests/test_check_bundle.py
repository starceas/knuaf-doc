"""Contract tests for the deployment-disposition-only bundle checker."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "check_bundle", ROOT / "tools" / "check_bundle.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class CheckBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root / "skills" / "knuaf-doc"
        self.skill.mkdir(parents=True)
        self.payload = self.skill / "scripts" / "gg_doi.py"
        self.payload.parent.mkdir()
        self.payload.write_bytes(b"synthetic tool\n")
        digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        (self.skill / "install-manifest.json").write_text(json.dumps({
            "files": {"scripts/gg_doi.py": digest},
        }), encoding="utf-8")
        (self.skill / "references").mkdir()
        (self.skill / "references" / "builtin-sources.json").write_text(
            json.dumps({"entries": [{"source_id": "school-pdf",
                                     "runtime_present": True}]}),
            encoding="utf-8")
        docs = self.root / "docs"
        docs.mkdir()
        self.disposition_path = docs / "asset-disposition.json"
        self.disposition_path.write_text(json.dumps({
            "schema": "knuaf-doc/asset-disposition@1",
            "assets": [{"path": "S0/scripts/verify_doi.py",
                        "payload_path": "scripts/gg_doi.py",
                        "disposition": "배포"}],
            "required_builtin_source_ids": ["school-pdf"],
        }, ensure_ascii=False), encoding="utf-8")

    def test_ok_when_disposition_matches(self):
        result = checker.check(self.root)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["errors"], [])

    def test_fails_on_disposition_error(self):
        self.payload.unlink()

        result = checker.check(self.root)

        self.assertEqual(result["status"], "errors")
        self.assertEqual(len(result["errors"]), 1)
        error = result["errors"][0]
        self.assertEqual(error["code"],
                         "asset_disposition:deployment_missing_payload")
        self.assertEqual(error["detail"],
                         f"{error['code']}:{error['path']}")

    def test_extra_and_modified_files_do_not_fail(self):
        notes = self.root / "notes.txt"
        notes.write_text("extra file", encoding="utf-8")
        notes.write_text("modified non-deployment file", encoding="utf-8")
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "x.json").write_text("extra", encoding="utf-8")

        self.assertEqual(checker.check(self.root)["status"], "ok")

    def test_runs_without_ledger_files(self):
        tools = self.root / "tools"
        self.assertFalse((tools / "baseline-files.json").exists())
        self.assertFalse((tools / "runtime-changes.json").exists())
        self.assertFalse((tools / "validation-bundle.json").exists())
        self.assertFalse((tools / "validation-provenance.json").exists())

        self.assertEqual(checker.check(self.root)["status"], "ok")

    def test_cli_summary_and_json_shape(self):
        json_path = self.root / "result.json"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_bundle.py"),
             "--root", str(self.root), "--json", str(json_path)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            check=False)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines()[0],
                         "check_bundle: status=ok errors: none")
        report = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(set(report), {"status", "errors", "notes", "python"})
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["notes"], [])
        self.assertEqual(report["python"], sys.version.split()[0])


if __name__ == "__main__":
    unittest.main()
