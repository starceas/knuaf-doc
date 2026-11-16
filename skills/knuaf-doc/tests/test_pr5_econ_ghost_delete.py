"""PR⑤ contract: the econ 2025 pack contains no source-absent ghost rows."""

import builtins
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "references/benchmark-packs/common/rda-econ-2025"
RECORDS = PACK / "records.jsonl"
MANIFEST = PACK / "manifest.json"
REGISTRY = ROOT / "references/builtin-sources.json"
FIXTURE = Path(__file__).resolve().parent / "fixtures/econ-ghost-lines-pr5.jsonl"
sys.path.insert(0, str(ROOT / "scripts"))

import gg_rda_candidates as candidates  # noqa: E402
import gg_rda_lookup as lookup  # noqa: E402
import gg_rda_provenance as provenance  # noqa: E402


BASE_RECORDS_SHA256 = "049e7e73763b29d5c36fe1408082f52d77bebfd8158cf14b75c372a80a39a973"
BASE_GHOST_LINES = tuple(range(105, 122)) + tuple(range(2581, 2588))
GHOST_RECORD_ID = "rda.econ.2025.useful_life.farm_structures_animals."
GHOST_SHA256_BY_LINE = {
    105: "f4a92a9617ac22923922ff3d89c3af8f6456388d7057c585825a9ed544408818",
    106: "90ad1e9401bdb590d4a40a6564fd3e0d8873fbea71c3ac0216c9b898eb720831",
    107: "97dd2f5c97222ea9a031eaaa42a5f4e4e94b685fbde0be4be1cff1e73e3cc9fc",
    108: "b519fb2d0cb4d2b0164d547edc2c15fca2f46e2efd7ff45259d474e3db87b94c",
    109: "37b7d23311f474e53218206ba98bae9f6e4b6d4b9be766e9110c8db30ba8f874",
    110: "97e92c30fcd5904b17e1bb79797b59db3425e5e898497443bb5319c0547843c8",
    111: "61f9721fe9c2e8de9f089433d585a9dd0c88d9427b5d0217f2f2c1f802ac2a7e",
    112: "9d6b3d7b92a6843c2dca50df84e4764ce8494d3a93ea5adbcbc029c84df2a53e",
    113: "66c4a9e6f6975fd9da107101fb98439752251997d65ba10d4ab85745af5e50d4",
    114: "9ac144b8cb1de0e161586cc2b4b846cd36958d79891892390e4878e6cfc39d10",
    115: "5f76687a43f18de01d115230c2eac2309ab4c7c00fd1f24e9dd2286cc514b4e8",
    116: "1d945fa1a71e100b2c7a1ec506444d6dd3e2f53855cb55447258faa656d911be",
    117: "077964f816370e61dffdc45663e105fce2e6da2fdc46c6f9860c913d2787cfea",
    118: "c1bc2df830deceb1545802e97fac426e1695c8451a4b17cd70631a568d111f7a",
    119: "173efdd3a11079c7f51b26d8311b87c23068184993b0c1a51e755272433942c1",
    120: "37a38084bed8e36073e419c9d2ff3fa7f5322a724a670a48e0a4a681eac68b6d",
    121: "6fafc5274d4775caf9270c73e048098a4318aba324ea5b01c45737405a0e75d4",
    2581: "ef3eefc0e69cf534b1b8b7bb81f4f4a28d8147ebbd2b9bdde07c6cb24d7de3cd",
    2582: "0d179393597a0db433445ee108d3f456004ffdd3ba27661c3003e9998c3b2b80",
    2583: "1aa5b6f18bada9660a270e1c9dcd70e31b3b3ce963695d55c29a8a1d443ce03d",
    2584: "68ba0275cf365f5b0515d91ef7a2f9dd43b51130a09d84a74c1b2e1a75047042",
    2585: "865ec76bd938f16284a3adb65269ef416b16ed03ffbaa481dd6215be7e47b715",
    2586: "23ba0cdbd962bd8a37e591e57899cf955db63ffccac84efcf19c4258cc74c93f",
    2587: "37f44fd4806f7c491a1b01148853a400d1267aae62e9ad6a2c12170c47edf3b6",
}
GHOST_SHA256S = frozenset(GHOST_SHA256_BY_LINE.values())
EXPECTED_MANIFEST_PROJECTION_SHA256 = "23dbfddbe83ececca9ac34a45867b8a188bf9af321d64b9e5344f5de996a6d48"
DUPLICATE_PRIMARY_LINES = (
    (1220, 1219), (1388, 1386), (1389, 1387), (2183, 2180),
    (2184, 2181), (2185, 2182), (2189, 2188),
)


def _manifest_projection(manifest):
    top = {key: value for key, value in manifest.items()
           if key not in ("rows", "records_file_sha256", "total_physical_rows", "status_summary")}
    rows = []
    for row in manifest["rows"]:
        if row.get("raw_line_sha256") in GHOST_SHA256S:
            continue
        projected = {key: value for key, value in row.items()
                     if key not in ("physical_line", "raw_line_sha256", "audit_key")}
        duplicate_link = dict(projected.get("duplicate_link") or {})
        primary = duplicate_link.get("primary_audit_key")
        if primary:
            duplicate_link["primary_audit_key"] = {
                "raw_line_sha256": primary["raw_line_sha256"]
            }
        projected["duplicate_link"] = duplicate_link
        rows.append(projected)
    encoded = json.dumps(
        {"top": top, "rows": rows}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded


def _duplicate_primary_lines(manifest):
    return tuple(
        (row["physical_line"], row["duplicate_link"]["primary_audit_key"][
            "physical_jsonl_line_1based"])
        for row in manifest["rows"]
        if row.get("duplicate_link", {}).get("primary_audit_key")
    )


def _runtime_python_sources():
    scripts = ROOT / "scripts"
    yield from scripts.rglob("*.py")
    entrypoint = ROOT / "gg.py"
    if entrypoint.is_file():
        yield entrypoint
CROPS_WITH_GHOST_SEED_ROWS = ("수박", "참외", "토마토", "딸기", "시금치", "마늘", "양파")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _fixture_lines(blob):
    return blob.splitlines(keepends=True)


def _restore_base(current_blob, fixture_blob):
    """Reinsert fixture lines at their frozen original physical positions."""
    current = current_blob.splitlines(keepends=True)
    ghosts = _fixture_lines(fixture_blob)
    if len(current) != 2683 or len(ghosts) != 24:
        raise AssertionError("expected 2683 current rows and 24 fixture rows")
    if tuple(_sha(line) for line in ghosts) != tuple(
            GHOST_SHA256_BY_LINE[line] for line in BASE_GHOST_LINES):
        raise AssertionError("fixture ghost signatures or order changed")
    restored = list(current)
    for line_no, ghost in zip(BASE_GHOST_LINES, ghosts):
        restored.insert(line_no - 1, ghost)
    return b"".join(restored)


def _require_clean_current(blob):
    lines = blob.splitlines(keepends=True)
    if len(lines) != 2683:
        raise AssertionError("current econ row count changed")
    present = {_sha(line) for line in lines}
    if present & GHOST_SHA256S:
        raise AssertionError("a frozen ghost line remains in current records")


def _walk_audit_refs(value):
    if isinstance(value, dict):
        if value.get("kind") == "audit_key" or (
                value.get("pack_id") == "rda.econ.2025"
                and "raw_line_sha256" in value):
            yield value
        for child in value.values():
            yield from _walk_audit_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_audit_refs(child)


class EconGhostDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records_blob = RECORDS.read_bytes()
        cls.fixture_blob = FIXTURE.read_bytes()
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.records = [json.loads(line) for line in cls.records_blob.splitlines()]
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_current_records_are_pinned_and_manifest_bound(self):
        digest = _sha(self.records_blob)
        pin = provenance.PINNED_PACKS["rda.econ.2025"]
        self.assertEqual(2683, len(self.records))
        self.assertEqual(digest, pin["records_file_sha256"])
        self.assertEqual(2683, pin["record_lines"])
        self.assertEqual(digest, self.manifest["records_file_sha256"])
        self.assertEqual(2683, self.manifest["total_physical_rows"])

    def test_fixture_contains_the_frozen_ghosts_in_original_order(self):
        ghosts = _fixture_lines(self.fixture_blob)
        self.assertEqual(24, len(ghosts))
        self.assertEqual(
            tuple(GHOST_SHA256_BY_LINE[line] for line in BASE_GHOST_LINES),
            tuple(_sha(line) for line in ghosts),
        )

    def test_reinsertion_reproduces_pr4_base_and_only_removes_ghosts(self):
        restored = _restore_base(self.records_blob, self.fixture_blob)
        self.assertEqual(BASE_RECORDS_SHA256, _sha(restored))
        self.assertEqual(
            [line for line in restored.splitlines(keepends=True)
             if _sha(line) not in GHOST_SHA256S],
            self.records_blob.splitlines(keepends=True),
        )

    def test_ghosts_are_absent_from_records_manifest_and_registry_refs(self):
        _require_clean_current(self.records_blob)
        self.assertFalse(any(r.get("record_id") == GHOST_RECORD_ID for r in self.records))
        manifest_ghosts = {
            row.get("raw_line_sha256") for row in self.manifest.get("rows", [])
        }
        self.assertFalse(manifest_ghosts & GHOST_SHA256S)
        manifest_keys = {
            row.get("audit_key", {}).get("raw_line_sha256")
            for row in self.manifest.get("rows", [])
        }
        self.assertFalse(manifest_keys & GHOST_SHA256S)
        manifest_refs = {
            ref.get("raw_line_sha256")
            for ref in _walk_audit_refs(self.manifest)
            if ref.get("pack_id") == "rda.econ.2025"
        }
        self.assertFalse(manifest_refs & GHOST_SHA256S)
        registry_ghosts = {
            ref.get("raw_line_sha256")
            for ref in _walk_audit_refs(self.registry)
            if ref.get("pack_id") == "rda.econ.2025"
        }
        self.assertFalse(registry_ghosts & GHOST_SHA256S)

    def test_manifest_audit_chain_and_summary_match_current_pack(self):
        digest = _sha(self.records_blob)
        rows = self.manifest["rows"]
        self.assertEqual(2683, len(rows))
        self.assertEqual(
            {"verified_observation_count": 105, "exploratory_count": 131,
             "quarantined_count": 2447},
            self.manifest["status_summary"],
        )
        for line_no, (raw, row) in enumerate(
                zip(self.records_blob.splitlines(keepends=True), rows), 1):
            raw_digest = _sha(raw)
            key = row["audit_key"]
            self.assertEqual(line_no, row["physical_line"])
            self.assertEqual(raw_digest, row["raw_line_sha256"])
            self.assertEqual("rda.econ.2025", key["pack_id"])
            self.assertEqual(digest, key["records_file_sha256"])
            self.assertEqual(line_no, key["physical_jsonl_line_1based"])
            self.assertEqual(raw_digest, key["raw_line_sha256"])

    def test_manifest_nondiscriminating_projection_and_duplicate_pairs(self):
        projected = _manifest_projection(self.manifest)
        self.assertEqual(EXPECTED_MANIFEST_PROJECTION_SHA256, _sha(projected))
        self.assertEqual(DUPLICATE_PRIMARY_LINES, _duplicate_primary_lines(self.manifest))

    def test_live_runtime_has_no_fixture_reference(self):
        forbidden = (b"econ-ghost-lines-pr5", b"tests/fixtures")
        for source in _runtime_python_sources():
            content = source.read_bytes()
            for marker in forbidden:
                self.assertNotIn(marker, content, str(source))

    def test_survivor_record_byte_mutation_changes_restored_base(self):
        lines = self.records_blob.splitlines(keepends=True)
        mutated = list(lines)
        mutated[0] = bytes([mutated[0][0] ^ 1]) + mutated[0][1:]
        restored = _restore_base(b"".join(mutated), self.fixture_blob)
        self.assertNotEqual(BASE_RECORDS_SHA256, _sha(restored))

    def test_survivor_manifest_catalog_status_mutation_changes_projection(self):
        rows = self.manifest["rows"]
        survivor_index = next(
            index for index, row in enumerate(rows)
            if row["raw_line_sha256"] not in GHOST_SHA256S
        )
        mutated = json.loads(json.dumps(self.manifest))
        status = mutated["rows"][survivor_index]["catalog_status"]
        mutated["rows"][survivor_index]["catalog_status"] = (
            "exploratory" if status != "exploratory" else "quarantined"
        )
        self.assertNotEqual(
            EXPECTED_MANIFEST_PROJECTION_SHA256,
            _sha(_manifest_projection(mutated)),
        )
    def test_duplicate_primary_mutation_is_detected(self):
        mutated = json.loads(json.dumps(self.manifest))
        duplicate = next(
            row for row in mutated["rows"]
            if row.get("duplicate_link", {}).get("primary_audit_key")
        )
        duplicate["duplicate_link"]["primary_audit_key"][
            "physical_jsonl_line_1based"] += 1
        self.assertNotEqual(DUPLICATE_PRIMARY_LINES,
                            _duplicate_primary_lines(mutated))

    def test_old_ghost_audit_keys_are_not_found(self):
        ghosts = [json.loads(line) for line in self.fixture_blob.splitlines()]
        for line_no, record in zip(BASE_GHOST_LINES, ghosts):
            raw_digest = GHOST_SHA256_BY_LINE[line_no]
            item = (record.get("crop") or {}).get("name") or record["basis"]["item_name"]
            old_key = {
                "pack_id": "rda.econ.2025",
                "records_file_sha256": BASE_RECORDS_SHA256,
                "physical_jsonl_line_1based": line_no,
                "raw_line_sha256": raw_digest,
            }
            result = lookup.lookup_rda_data(
                item, "전국", kind=record["kind"], year=2025, audit_key=old_key,
            )
            self.assertEqual("not_found", result["status"], line_no)
            self.assertEqual([], result["records"], line_no)

    def test_p127_ghost_seed_crops_no_longer_resolve(self):
        for crop in CROPS_WITH_GHOST_SEED_ROWS:
            result = lookup.lookup_rda_data(
                crop, "전국", kind="seed_requirement", year=2025,
            )
            self.assertEqual("not_found", result["status"], crop)
            self.assertEqual([], result["records"], crop)
            approved = candidates.lookup_approved_candidates(
                crop, "전국", "vegetables", kind="seed_requirement",
                year=2025, use_scope="plan_input",
            )
            self.assertEqual("not_found", approved["status"], crop)
            self.assertEqual([], approved["candidates"], crop)

    def test_lookup_entrypoints_never_open_or_enumerate_test_paths(self):
        observed_paths = []
        econ_pack_reads = []
        repo_root = ROOT.parents[1].resolve()
        forbidden_root = (repo_root / "skills/knuaf-doc/tests").resolve()

        def record_path(value):
            try:
                path = Path(os.fspath(value))
                if not path.is_absolute():
                    path = repo_root / path
                resolved = path.resolve()
                normalized = resolved.as_posix()
                observed_paths.append(normalized)
                if "references/benchmark-packs/" in normalized \
                        and resolved.name == "records.jsonl":
                    econ_pack_reads.append(normalized)
            except (TypeError, ValueError, OSError):
                pass

        real_open = builtins.open
        real_scandir = os.scandir
        real_listdir = os.listdir
        real_iterdir = Path.iterdir
        real_path_open = Path.open

        def watched_path_open(path, *args, **kwargs):
            record_path(path)
            return real_path_open(path, *args, **kwargs)


        def watched_open(file, *args, **kwargs):
            record_path(file)
            return real_open(file, *args, **kwargs)

        def watched_scandir(path="."):
            record_path(path)
            return real_scandir(path)

        def watched_listdir(path="."):
            record_path(path)
            return real_listdir(path)

        def watched_iterdir(path):
            record_path(path)
            return real_iterdir(path)

        self.assertTrue(hasattr(lookup, "_CACHE"))
        lookup._CACHE.clear()
        fixture_ghosts = [json.loads(line) for line in self.fixture_blob.splitlines()]
        old_record = fixture_ghosts[17]
        old_key = {
            "pack_id": "rda.econ.2025",
            "records_file_sha256": BASE_RECORDS_SHA256,
            "physical_jsonl_line_1based": 2581,
            "raw_line_sha256": GHOST_SHA256_BY_LINE[2581],
        }

        with mock.patch.object(builtins, "open", side_effect=watched_open), \
                mock.patch.object(Path, "open", watched_path_open), \
                mock.patch.object(os, "scandir", side_effect=watched_scandir), \
                mock.patch.object(os, "listdir", side_effect=watched_listdir), \
                mock.patch.object(Path, "iterdir", watched_iterdir):
            for crop in CROPS_WITH_GHOST_SEED_ROWS:
                self.assertEqual(
                    "not_found",
                    lookup.lookup_rda_data(
                        crop, "전국", kind="seed_requirement", year=2025,
                    )["status"],
                )
                self.assertEqual(
                    "not_found",
                    candidates.lookup_approved_candidates(
                        crop, "전국", "vegetables", kind="seed_requirement",
                        year=2025, use_scope="plan_input",
                    )["status"],
                )
            self.assertEqual(
                "not_found",
                lookup.lookup_rda_data(
                    old_record["crop"]["name"], "전국",
                    kind="seed_requirement", year=2025, audit_key=old_key,
                )["status"],
            )
            self.assertEqual(
                "not_found",
                candidates.lookup_approved_candidates(
                    old_record["crop"]["name"], "전국", "vegetables",
                    kind="seed_requirement", year=2025, audit_key=old_key,
                    use_scope="plan_input",
                )["status"],
            )
        self.assertTrue(econ_pack_reads, "lookup did not open any pack records.jsonl")
        forbidden_path = forbidden_root.as_posix()
        self.assertEqual([], [path for path in observed_paths
                              if path == forbidden_path or forbidden_path in path])

    def test_mutations_fail_in_memory(self):
        ghosts = _fixture_lines(self.fixture_blob)
        changed_ghosts = list(ghosts)
        changed_ghosts[0] = bytes([changed_ghosts[0][0] ^ 1]) + changed_ghosts[0][1:]
        with self.assertRaises(AssertionError):
            _restore_base(self.records_blob, b"".join(changed_ghosts))

        survivors = self.records_blob.splitlines(keepends=True)
        with self.assertRaises(AssertionError):
            _restore_base(b"".join(survivors[:100] + survivors[101:]), self.fixture_blob)

        retained_ghost = b"".join(survivors[:-1] + [ghosts[0]])
        with self.assertRaises(AssertionError):
            _require_clean_current(retained_ghost)


if __name__ == "__main__":
    unittest.main(verbosity=2)
