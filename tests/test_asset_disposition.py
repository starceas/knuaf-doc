"""합성 트리에서 payload·설치 manifest·필수 출처 검사를 확인한다."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_asset_disposition", ROOT / "tools" / "check_asset_disposition.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
sys.path.insert(0, str(ROOT / "tools"))
import build_validation_bundle as builder
import check_bundle


class AssetDispositionTests(unittest.TestCase):
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
        (self.root / "docs").mkdir()
        self.disposition_path = self.root / "docs" / "asset-disposition.json"
        self.disposition = {
            "schema": "knuaf-doc/asset-disposition@1",
            "assets": [{"path": "S0/scripts/verify_doi.py",
                        "payload_path": "scripts/gg_doi.py",
                        "disposition": "배포"}],
            "required_builtin_source_ids": ["school-pdf"],
        }
        self.write_disposition()

    def write_disposition(self):
        self.disposition_path.write_text(
            json.dumps(self.disposition, ensure_ascii=False), encoding="utf-8")

    def test_matching_payload_manifest_and_registry_pass(self):
        self.assertEqual(checker.check(self.root), [])

    def test_missing_manifest_or_false_required_source_fails(self):
        manifest = json.loads((self.skill / "install-manifest.json")
                              .read_text(encoding="utf-8"))
        manifest["files"] = {}
        (self.skill / "install-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        registry_path = self.skill / "references" / "builtin-sources.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"][0]["runtime_present"] = False
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        codes = {error["code"] for error in checker.check(self.root)}
        self.assertIn("deployment_missing_manifest", codes)
        self.assertIn("required_source_not_runtime", codes)

    def test_unsafe_payload_path_fails_closed(self):
        self.disposition["assets"][0]["payload_path"] = "../outside.py"
        self.write_disposition()
        self.assertIn("deployment_path_unsafe",
                      {error["code"] for error in checker.check(self.root)})

    def write_bundle(self, runtime_changes=None):
        """Regenerate integrity hashes so only the deployment error remains."""
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        baseline = {"main": "0" * 40, "files": {}}
        baseline_path = tools / "baseline-files.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        (tools / "runtime-changes.json").write_text(json.dumps({
            "schema": "knuaf-doc/runtime-changes@2", "stage": "synthetic",
            "baseline_main": baseline["main"],
            "baseline_spec_sha256": hashlib.sha256(
                baseline_path.read_bytes()).hexdigest(),
            "approved_new_files": list(runtime_changes or {}),
            "changes": runtime_changes or {},
        }), encoding="utf-8")
        provenance = {"defaults": {"origin": "new"}, "files": {}}
        (tools / "validation-provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8")
        (tools / "validation-bundle.json").write_bytes(builder.manifest_bytes(
            builder.build_manifest(self.root, provenance)))

    def assert_bundle_errors(self, *codes):
        self.write_bundle()
        report = check_bundle.check(self.root)
        self.assertEqual("errors", report["status"])
        self.assertEqual(set(codes), {e["code"] for e in report["errors"]})
        for error in report["errors"]:
            self.assertEqual(error["detail"],
                             f"{error['code']}:{error['path']}")

    def test_bundle_rejects_missing_deployment_payload_or_manifest(self):
        self.write_bundle()
        self.assertEqual("ok", check_bundle.check(self.root)["status"])
        (self.skill / "install-manifest.json").write_text(
            json.dumps({"files": {}}), encoding="utf-8")
        self.assert_bundle_errors("asset_disposition:deployment_missing_manifest")
        self.payload.unlink()
        self.assert_bundle_errors("asset_disposition:deployment_missing_payload")

    def test_bundle_rejects_false_required_school_source(self):
        registry_path = self.skill / "references" / "builtin-sources.json"
        registry_path.write_text(json.dumps({"entries": [{
            "source_id": "school-pdf", "runtime_present": False}]}),
            encoding="utf-8")
        self.assert_bundle_errors("asset_disposition:required_source_not_runtime")

    def test_inventory_and_lineage_removal_survives_manifest_rebuild(self):
        required = {"references/guideline-inventory.json",
                    "references/official-guide-lineage.json"}
        actual = json.loads((ROOT / "docs/asset-disposition.json")
                            .read_text(encoding="utf-8"))
        self.disposition["assets"].extend(
            row for row in actual["assets"]
            if row.get("payload_path", row["path"].split("/", 1)[1])
            in required)
        self.write_disposition()
        manifest_path = self.skill / "install-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        changes = {}
        for rel in sorted(required):
            payload = self.skill / rel
            payload.write_text('{"synthetic": true}\n', encoding="utf-8")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            manifest["files"][rel] = digest
            changes["skills/knuaf-doc/" + rel] = {
                "before_sha256": None, "after_sha256": digest,
                "first_changed": "synthetic", "owner": "test",
                "reason": "synthetic school payload", "source": "new"}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.write_bundle(changes)
        self.assertEqual("ok", check_bundle.check(self.root)["status"])
        for rel in required:
            (self.skill / rel).unlink()
            del manifest["files"][rel]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        # Drop both runtime declarations/allowlist entries as well, then
        # regenerate a valid bundle from the incomplete installation.
        self.write_bundle()
        report = check_bundle.check(self.root)
        self.assertEqual("errors", report["status"])
        self.assertEqual({("asset_disposition:deployment_missing_payload", rel)
                          for rel in required},
                         {(error["code"], error["path"])
                          for error in report["errors"]})


if __name__ == "__main__":
    unittest.main()
