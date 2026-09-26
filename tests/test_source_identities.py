"""D4 identity catalogue: shape, privacy, registry and scope controls."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._harness import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "skills" / "knuaf-doc" / "scripts"))
import gg_source_identity as identities  # noqa: E402


SHA = "a" * 64
SECOND_SHA = "b" * 64


def _row(**changes):
    row = {
        "sha256": SHA,
        "source_id": None,
        "major": "common",
        "kind": "official_publication",
        "bibliographic": {
            "title": "2026 농업전망 PET/유리",
            "authors": ["연구원"],
            "publisher": "공공기관",
            "year": "2026",
        },
        "note": "서지 식별",
    }
    row.update(changes)
    return row


def _registry_entry(sha=SHA, sid="demo-source", **changes):
    entry = {"source_sha256": [sha], "source_id": sid,
             "role": "reference_only",
             "runtime": None, "runtime_present": False, "artifacts": [],
             "lineage": []}
    entry.update(changes)
    return entry


class CatalogueTests(unittest.TestCase):
    def _load(self, rows, registry=None):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            path = Path(tmp) / "catalogue.json"
            path.write_text(json.dumps({"schema": identities.SCHEMA,
                                        "entries": rows}, ensure_ascii=False),
                            encoding="utf-8")
            return identities.load_source_identities(path, registry={
                "entries": []} if registry is None else registry)

    def _bad(self, rows, rule, field=None, registry=None):
        with self.assertRaises(ValueError) as caught:
            self._load(rows, registry)
        message = str(caught.exception)
        self.assertIn(rule, message)
        if field:
            self.assertIn(field, message)
        self.assertTrue(message.startswith("$"), message)

    def test_shipped_empty_catalogue_and_cli_contract(self):
        self.assertEqual({}, self._load([]))

    def test_unregistered_identity_and_all_kinds(self):
        for kind in sorted(identities.KINDS):
            with self.subTest(kind=kind):
                row = _row(kind=kind)
                if kind == "student_example":
                    row["bibliographic"].update(publisher=None, year=None)
                    row["note"] = None
                self.assertEqual(row, self._load([row])[SHA])

    def test_registered_identity_and_major(self):
        major = next(iter(identities.gg_major_contract.default_registry().major_ids))
        row = _row(source_id="demo-source", major=major)
        self.assertEqual(row, self._load([row], {
            "entries": [_registry_entry()]})[SHA])

    def test_shipped_registry_identity_row(self):
        registry = identities.gg_reuse.load_registry(identities.REGISTRY_PATH)
        entry = next(e for e in registry.document["entries"]
                     if e["role"] == "reference_only")
        sha = entry["source_sha256"][0]
        row = _row(sha256=sha, source_id=entry["source_id"])
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            path = Path(tmp) / "catalogue.json"
            path.write_text(json.dumps({"schema": identities.SCHEMA,
                                        "entries": [row]}, ensure_ascii=False),
                            encoding="utf-8")
            self.assertEqual(row, identities.load_source_identities(path)[sha])

    def test_schema_shapes_and_duplicates(self):
        for raw, rule, field in (
            ({"schema": "wrong", "entries": []}, "schema_invalid", "schema"),
            ({"schema": identities.SCHEMA, "entries": {}}, "list_required", "entries"),
            ({"schema": identities.SCHEMA, "entries": [], "extra": 1},
             "unknown_key", "$"),
            ({"schema": identities.SCHEMA}, "missing_field", "entries"),
        ):
            with self.subTest(rule=rule):
                with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
                    path = Path(tmp) / "c.json"
                    path.write_text(json.dumps(raw), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, rule) as caught:
                        identities.load_source_identities(path, registry={"entries": []})
                    self.assertIn(field, str(caught.exception))
        for raw in (
            '{"schema":"x","schema":"y","entries":[]}',
            '{"schema":"knuaf-source-identities/v1","entries":'
            '[{"bibliographic":{"title":"x","title":"y"}}]}',
        ):
            with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
                path = Path(tmp) / "c.json"
                path.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate_key") as caught:
                    identities.load_source_identities(path, registry={"entries": []})
                self.assertTrue(str(caught.exception).startswith("$"))

    def test_row_and_bibliographic_closed_shapes(self):
        for mutation, rule in (
            (lambda r: r.update(extra="x"), "unknown_key"),
            (lambda r: r.update(isbn="identifier"), "unknown_key"),
            (lambda r: r.pop("kind"), "missing_field"),
            (lambda r: r.update(bibliographic={}), "missing_field"),
            (lambda r: r["bibliographic"].update(extra="x"), "unknown_key"),
            (lambda r: r.update(bibliographic=[]), "shape_object"),
            (lambda r: r.update(note=1), "text_shape"),
        ):
            with self.subTest(rule=rule):
                row = _row()
                mutation(row)
                self._bad([row], rule)

    def test_field_types_and_domains(self):
        cases = (
            ("sha256", "A" * 64, "sha256_invalid"),
            ("sha256", "a" * 63, "sha256_invalid"),
            ("sha256", "g" * 64, "sha256_invalid"),
            ("major", "unregistered", "major_unknown"),
            ("major", [], "major_unknown"),
            ("kind", "unknown", "kind_unknown"),
            ("kind", [], "kind_unknown"),
            ("source_id", "bad/id", "source_id_grammar"),
        )
        for field, value, rule in cases:
            with self.subTest(field=field, rule=rule):
                row = _row()
                row[field] = value
                self._bad([row], rule, field)
        for field, value, rule in (
            ("title", "  ", "text_shape"),
            ("authors", "name", "list_required"),
            ("authors", [""], "text_shape"),
            ("publisher", 1, "text_shape"),
            ("year", "20a6", "year_invalid"),
            ("year", 2026, "year_invalid"),
        ):
            row = _row()
            row["bibliographic"][field] = value
            self._bad([row], rule, field)

    def test_privacy_digit_runs_in_all_free_text_fields(self):
        separated = "-".join(str(part) for part in (12, 34, 56, 78))
        for field in ("title", "authors", "publisher", "note"):
            with self.subTest(field=field):
                row = _row()
                if field == "authors":
                    row["bibliographic"][field] = [separated]
                elif field in row["bibliographic"]:
                    row["bibliographic"][field] = separated
                else:
                    row[field] = separated
                self._bad([row], "digit_run", field)
        sid = "student-" + separated
        row = _row(source_id=sid)
        self._bad([row], "digit_run", "source_id", {
            "entries": [_registry_entry(sid=sid)]})
        fullwidth_parts = ("１２", "３４", "５６", "７８")
        row = _row(source_id="student-" + "-".join(fullwidth_parts))
        self._bad([row], "digit_run", "source_id")
        row = _row()
        row["bibliographic"]["title"] = "·".join(fullwidth_parts)
        self._bad([row], "digit_run", "title")

    def test_privacy_path_tokens_and_safe_slash(self):
        tokens = ("/" + "example.pdf", "C:" + "/x.pdf", "~/" + "x",
                  "\\\\" + "srv\\x", "file:" + "///x",
                  "prefix/" + "Users/x")
        for token in tokens:
            with self.subTest(token=token):
                for field in ("title", "authors", "publisher", "note"):
                    row = _row()
                    if field == "authors":
                        row["bibliographic"][field] = [token]
                    elif field in row["bibliographic"]:
                        row["bibliographic"][field] = token
                    else:
                        row[field] = token
                    self._bad([row], "path_like", field)
        self.assertEqual("2026 농업전망 PET/유리",
                         self._load([_row()])[SHA]["bibliographic"]["title"])

    def test_student_example_cardinality_and_nulls(self):
        valid = _row(kind="student_example", note=None)
        valid["bibliographic"].update(publisher=None, year=None)
        self.assertEqual(valid, self._load([valid])[SHA])
        for field, value in (("authors", []), ("authors", ["A", "B"]),
                             ("publisher", "기관"), ("year", "2026")):
            row = copy.deepcopy(valid)
            row["bibliographic"][field] = value
            self._bad([row], "student_example_shape")
        row = copy.deepcopy(valid)
        row["note"] = "설명"
        self._bad([row], "student_example_shape")

    def test_registry_identity_only_conjuncts(self):
        row = _row(source_id="demo-source")
        for changes in (
            {"role": "narrative_exemplar"}, {"runtime": "relative"},
            {"runtime_present": True}, {"artifacts": [{"x": 1}]},
            {"coverage": {"x": 1}}, {"lineage": [{"x": 1}]},
        ):
            with self.subTest(changes=changes):
                self._bad([row], "registry_not_identity_only", "sha256", {
                    "entries": [_registry_entry(**changes)]})
        self._bad([row], "registry_not_identity_only", "sha256", {
            "entries": [_registry_entry(), _registry_entry(sid="other-source",
                                                   role="narrative_exemplar")]})

    def test_registry_source_id_linkage(self):
        self._bad([_row(source_id="demo-source")],
                  "registry_source_id_must_be_null", "source_id", {
                      "entries": [_registry_entry(sha=SECOND_SHA)]})
        self._bad([_row()], "registry_source_id_required", "source_id", {
            "entries": [_registry_entry()]})
        self._bad([_row(source_id="other-source")],
                  "registry_source_id_required", "source_id", {
                      "entries": [_registry_entry(),
                                  _registry_entry(sha=SECOND_SHA,
                                                  sid="other-source")]})
        self._bad([_row(source_id="absent-source")],
                  "registry_source_id_unknown", "source_id", {
                      "entries": [_registry_entry()]})
        self._bad([_row(), _row()], "duplicate_sha256", "sha256")

    def test_errors_do_not_echo_values_or_paths(self):
        secret = "/" + "private/record.pdf"
        row = _row(note=secret)
        with self.assertRaises(ValueError) as caught:
            self._load([row])
        self.assertEqual("$.entries[0].note: path_like", str(caught.exception))

    def _cli_failure(self, scenario, expected):
        """Run a copied script against isolated, synthetic dependencies."""
        with tempfile.TemporaryDirectory(prefix="d4-cli-private-",
                                         dir=REPO_ROOT) as tmp:
            skill = Path(tmp) / "skill"
            scripts = skill / "scripts"
            references = skill / "references"
            scripts.mkdir(parents=True)
            references.mkdir()
            source_scripts = REPO_ROOT / "skills" / "knuaf-doc" / "scripts"
            for source in source_scripts.glob("*.py"):
                shutil.copyfile(source, scripts / source.name)
            (skill / "SKILL.md").write_text("fixture\n", encoding="utf-8")
            catalogue = references / "source-identities.json"
            catalogue.write_text(json.dumps({"schema": identities.SCHEMA,
                                             "entries": []}), encoding="utf-8")
            if scenario != "missing_registry":
                registry = json.loads(identities.REGISTRY_PATH.read_text(
                    encoding="utf-8"))
                if scenario == "missing_major_catalogue":
                    registry["p4_trust"].pop("packs_dir", None)
                (references / "builtin-sources.json").write_text(
                    json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            packs = references / "benchmark-packs"
            packs.mkdir()
            major_catalogue = packs / "catalog.json"
            if scenario == "malformed_major_catalogue":
                major_catalogue.write_text('{"owner":"private-value"',
                                           encoding="utf-8")
            elif scenario != "missing_major_catalogue":
                shutil.copyfile(
                    REPO_ROOT / "skills" / "knuaf-doc" / "references"
                    / "benchmark-packs" / "catalog.json", major_catalogue)
            if scenario == "deep_nesting":
                catalogue.write_text("[" * 1100 + "0" + "]" * 1100,
                                     encoding="utf-8")
            elif scenario == "bounded_nesting":
                catalogue.write_text("[" * 70 + "0" + "]" * 70,
                                     encoding="utf-8")
            elif scenario == "missing_catalogue":
                catalogue.unlink()
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, "-B", str(scripts / "gg_source_identity.py"),
                 "check"], cwd=tmp, env=env, text=True, capture_output=True,
                timeout=15, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertEqual("", completed.stdout)
            self.assertEqual(expected + "\n", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertNotIn(str(skill), completed.stderr)
            self.assertNotIn("private-value", completed.stderr)

    def test_cli_dependency_and_depth_failures_are_sanitized(self):
        for scenario, expected in (
            ("missing_major_catalogue", "$.entries: major_catalogue_invalid"),
            ("malformed_major_catalogue", "$.entries: major_catalogue_invalid"),
            ("missing_registry", "$.registry: registry_invalid"),
            ("missing_catalogue", "$: catalogue_unreadable"),
            ("deep_nesting", "$: nesting_too_deep"),
            ("bounded_nesting", "$: nesting_too_deep"),
        ):
            with self.subTest(scenario=scenario):
                self._cli_failure(scenario, expected)


if __name__ == "__main__":
    unittest.main()
