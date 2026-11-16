"""PR6: source-frozen seed rows and append-only provenance contracts."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "references/benchmark-packs/common/rda-econ-2025"
CORRECTIONS = PACK / "corrections/pp126-127-seed-cells.json"
FIXTURE = Path(__file__).parent / "fixtures/econ-ghost-lines-pr5.jsonl"
sys.path.insert(0, str(ROOT / "scripts"))
import gg_rda_lookup as lookup  # noqa: E402
import gg_rda_candidates as candidates  # noqa: E402
import gg_rda_provenance as provenance  # noqa: E402

BASE_SHA = "f3e7609ce45c0b26ffb36759403761699f2dacbeb7226b3bc8de38322176d70f"
METRICS = ("volume_seed", "seedling_plants", "seed_count", "weight_seed")
# Transcribed from the page images, not from the correction receipt.
ROWS = (
    ("참외(노지)", "1.5㎗", "-", "2,400~3,000", "21~40g"),
    ("참외(시설)", "1.5㎗", "-", "2,400~3,000", "21~40g"),
    ("수박(노지)", "2.4㎗", "-", "800~920", "42~60g"),
    ("수박(시설)", "2.4㎗", "-", "800~920", "42~60g"),
    ("토마토", "0.6㎗", "-", "4,800", "13~18g"),
    ("딸기", "-", "9,000주", "-", "-"),
    ("방울토마토(대립)", "20~30㎖", "-", "1,500", "3.45g"),
    ("방울토마토(중립)", "20~30㎖", "-", "3,500", "8.05g"),
    ("방울토마토(소립)", "20~30㎖", "-", "6,000", "13.8g"),
    ("취나물", "2~4ℓ", "30,000~36,000주", "550,000~600,000", "800~1,600g"),
    ("들깻잎", "-", "-", "-", "4~5kg"),
    ("시설시금치", "5~7ℓ", "-", "240,000~330,000환", "3,336~4,587g"),
    ("시설시금치", "5~7ℓ", "-", "540,000~750,000침", "7,506~10,425g"),
    ("아욱", "25~30ℓ", "-", "-", "1,250~2,500g"),
    ("브로콜리", "50~60㎖", "3,000~5,000주", "-", "21.8~34.8g"),
    ("마늘", "-", "-", "-", "180~200kg"),
    ("양파", "0.6㎗", None, None, "240g"),
    ("알타리무", "-", "-", "-", "1,200~2,160g"),
    ("국화", None, "44,292.3주", None, None),
    ("장미", None, "4,143.4구", None, None),
    ("참깨", "1.2㎗", "-", None, None),
    ("엽연초", "-", "3,076.6주", None, None),
    ("인삼", "8.9㎗", "22,566.8주", None, None),
    ("오미자", "-", "1,712.7kg", None, None),
    ("느타리버섯", "-", "1,032.0kg", None, None),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_rows(records):
    assert len(records) == len(ROWS)
    for i, (record, (name, *values)) in enumerate(zip(records, ROWS)):
        locator = record["locator"]
        assert record["crop"]["name"] == record["basis"]["item_name"] == locator["row_label"] == name
        assert record["metrics"] == dict(zip(METRICS, values))
        if i < 18:
            suffix = f"p126.n{i + 1:02d}"
            assert locator["pdf_page"] == 126 and locator["printed_page"] == "120"
            assert record["crop"]["major_ids"] == ["vegetables"]
            assert locator["subtable"] == "가 채소"
            assert locator["column_labels"] == dict(zip(METRICS, ("종자(부피)", "종묘(주)", "소요립수(립)", "무게환산")))
        elif i < 20:
            suffix = f"p127.flower.n{i - 17:02d}"
            assert locator["pdf_page"] == 127 and locator["printed_page"] == "121"
            assert locator["subtable"] == "나 화훼"
            assert locator["column_labels"] == {"seedling_plants": "종자소요량(종묘)"}
            assert record["crop"]["major_ids"] == []
        else:
            suffix = f"p127.medicinal.n{i - 19:02d}"
            assert locator["pdf_page"] == 127 and locator["printed_page"] == "121"
            assert locator["subtable"] == "다 약용작물"
            assert locator["column_labels"] == {"volume_seed": "종자", "seedling_plants": "종묘(종균)"}
            assert record["crop"]["major_ids"] == ["specialty_crops"]
        assert record["record_id"] == "rda.econ.2025.seed_requirement." + suffix
        assert locator.get("sub_row") == ({11: 1, 12: 2}.get(i))
        shared = ({"weight_seed"} if i < 4 else
                  {"volume_seed", "seedling_plants"} if i in (6, 7, 8, 11, 12) else set())
        actual = {note.removeprefix("merged_cell_shared: ") for note in record["extraction"]["notes"]
                  if note.startswith("merged_cell_shared: ")}
        assert actual == shared
        assert "source" not in record
        assert record["extraction"]["confidence"] == "table_cell_visual_verified"


def check_cells(cells):
    assert len(cells) == len(ROWS)
    for i, (cell, (name, *values)) in enumerate(zip(cells, ROWS)):
        if i < 18:
            cell_id = f"p126.n{i + 1:02d}"
            page, printed, subtable, majors = 126, "120", "가 채소", ["vegetables"]
            labels = dict(zip(METRICS, ("종자(부피)", "종묘(주)", "소요립수(립)", "무게환산")))
        elif i < 20:
            cell_id = f"p127.flower.n{i - 17:02d}"
            page, printed, subtable, majors = 127, "121", "나 화훼", []
            labels = {"seedling_plants": "종자소요량(종묘)"}
        else:
            cell_id = f"p127.medicinal.n{i - 19:02d}"
            page, printed, subtable, majors = 127, "121", "다 약용작물", ["specialty_crops"]
            labels = {"volume_seed": "종자", "seedling_plants": "종묘(종균)"}
        merged = (["weight_seed"] if i < 4 else
                  ["volume_seed", "seedling_plants"] if i in (6, 7, 8, 11, 12) else [])
        assert cell == {"cell_id": cell_id, "physical_page": page, "printed_page": printed,
                        "subtable": subtable, "row_label": name,
                        "sub_row": {11: 1, 12: 2}.get(i), "column_labels": labels,
                        "major_ids": majors, "metrics": dict(zip(METRICS, values)),
                        "merged_metrics": merged}, cell_id


def receipt_cells():
    receipt = json.loads(CORRECTIONS.read_text(encoding="utf-8"))
    assert receipt["schema"] == "rda-econ-2025/pp126-127-seed-cells@1"
    assert receipt["source_pdf_sha256"] == "38e38ffb8b478584b9f76a8b9d98d56c7cdeb8eb18a2200aef26639a72935a83"
    return receipt["cells"]


def reconstructed_lines(cells, template):
    result = []
    for cell in cells:
        record = copy.deepcopy(template)
        name, cell_id = cell["row_label"], cell["cell_id"]
        record["record_id"] = "rda.econ.2025.seed_requirement." + cell_id
        record["crop"].update(name=name, major_ids=cell["major_ids"])
        record["basis"]["item_name"] = name
        record["metrics"] = {metric: cell["metrics"][metric] for metric in METRICS}
        title = "10a당 작목별 종자 소요량"
        table = title if cell["physical_page"] == 126 else title + " — " + cell["subtable"]
        locator = {"pdf_page": cell["physical_page"], "printed_page": cell["printed_page"],
                   "section": "Ⅲ-4 " + title, "table": table, "row_label": name,
                   "subtable": cell["subtable"], "source_cell_id": cell_id}
        if cell["sub_row"] is not None:
            locator["sub_row"] = cell["sub_row"]
        locator["column_labels"] = cell["column_labels"]
        record["locator"] = locator
        record["extraction"] = {"status": "extracted", "confidence": "table_cell_visual_verified",
                                "notes": ["merged_cell_shared: " + metric for metric in cell["merged_metrics"]]}
        result.append((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    return result


class SeedAppendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = (PACK / "records.jsonl").read_bytes()
        cls.lines = cls.blob.splitlines(keepends=True)
        cls.records = [json.loads(line) for line in cls.lines]
        cls.manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))

    def test_append_and_pins(self):
        self.assertEqual(2708, len(self.lines))
        self.assertEqual(BASE_SHA, sha(b"".join(self.lines[:2683])))
        digest = sha(self.blob)
        self.assertEqual(digest, provenance.PINNED_PACKS["rda.econ.2025"]["records_file_sha256"])
        self.assertEqual(2708, provenance.PINNED_PACKS["rda.econ.2025"]["record_lines"])
        self.assertEqual(digest, self.manifest["records_file_sha256"])
        self.assertEqual(2708, self.manifest["total_physical_rows"])
        self.assertEqual({"verified_observation_count": 105, "exploratory_count": 156,
                          "quarantined_count": 2447}, self.manifest["status_summary"])

    def test_source_transcription_and_structure(self):
        check_rows(self.records[2683:])

    def test_receipt_reconstructs_appended_bytes(self):
        cells = receipt_cells()
        self.assertEqual(self.lines[2683:], reconstructed_lines(cells, self.records[2545]))
        changed = copy.deepcopy(cells)
        changed[0]["metrics"]["volume_seed"] = "0㎗"
        self.assertNotEqual(self.lines[2683:], reconstructed_lines(changed, self.records[2545]))

    def test_receipt_matches_source_and_rejects_mutations(self):
        cells = receipt_cells()
        check_cells(cells)
        for kind in ("value", "name", "page", "column"):
            changed = copy.deepcopy(cells)
            if kind == "value":
                changed[0]["metrics"]["volume_seed"] = "wrong"
            elif kind == "name":
                changed[0]["row_label"] = "wrong"
            elif kind == "page":
                changed[0]["physical_page"] = 127
            else:
                changed[20]["column_labels"]["volume_seed"] = "종묘(종균)"
            with self.subTest(kind=kind), self.assertRaises(AssertionError):
                check_cells(changed)

    def test_manifest_new_entries(self):
        rows = self.manifest["rows"]
        self.assertEqual(2708, len(rows))
        receipt = CORRECTIONS.relative_to(ROOT.parents[1]).as_posix()
        for index, (line, record, entry) in enumerate(zip(self.lines[2683:], self.records[2683:], rows[2683:]), 2684):
            digest = sha(line)
            self.assertEqual(index, entry["physical_line"])
            self.assertEqual(digest, entry["raw_line_sha256"])
            self.assertEqual({"pack_id": "rda.econ.2025", "records_file_sha256": sha(self.blob),
                              "physical_jsonl_line_1based": index, "raw_line_sha256": digest}, entry["audit_key"])
            self.assertEqual("exploratory", entry["catalog_status"])
            self.assertEqual("unresolved", entry["source_observation"]["observation_status"])
            self.assertEqual(receipt + "#" + record["locator"]["source_cell_id"], entry["source_observation"]["prep_receipt_ref"])
            self.assertEqual(["verification_receipt_pending"], entry["gap_evidence"]["gaps"])
            self.assertEqual([], entry["gap_evidence"]["conflict_groups"])
            self.assertTrue(entry["duplicate_link"]["is_primary"])
            self.assertIsNone(entry["duplicate_link"]["primary_audit_key"])

    def test_no_ghost_bytes_or_seed_values(self):
        ghosts = FIXTURE.read_bytes().splitlines(keepends=True)
        self.assertEqual(24, len(ghosts))
        self.assertFalse({sha(line) for line in ghosts} & {sha(line) for line in self.lines})
        old = [json.loads(line) for line in ghosts[-7:]]
        ghost_values = {(r["crop"]["name"], json.dumps(r["metrics"], sort_keys=True, ensure_ascii=False)) for r in old}
        present = {(r["crop"]["name"], json.dumps(r["metrics"], sort_keys=True, ensure_ascii=False))
                   for r in self.records if r.get("kind") == "seed_requirement" and r.get("crop")}
        self.assertFalse(ghost_values & present)

    def test_lookup_and_approved(self):
        for crop in ("참외", "수박", "토마토", "딸기", "마늘", "양파", "시설시금치", "방울토마토"):
            with self.subTest(crop=crop):
                result = lookup.lookup_rda_data(crop, "전국", kind="seed_requirement", year=2025)
                records = result["records"]
                self.assertTrue(records)
                if crop in ("시설시금치", "방울토마토"):
                    self.assertEqual({"시설시금치": 2, "방울토마토": 3}[crop], len(records))
                for record in records:
                    self.assertEqual(126, record["locator"]["pdf_page"])
                    self.assertEqual("exploratory", record["verification"]["level"])
                approved = candidates.lookup_approved_candidates(crop, "전국", "vegetables", kind="seed_requirement", year=2025, use_scope="plan_input")
                self.assertEqual("unapproved", approved["status"])
                self.assertTrue(approved["candidates"])
                self.assertTrue(all(row["approved"] is False for row in approved["candidates"]))

    def test_append_byte_mutations_rejected(self):
        def check(blob):
            lines = blob.splitlines(keepends=True)
            assert len(lines) == 2708
            assert sha(b"".join(lines[:2683])) == BASE_SHA
            assert sha(blob) == self.manifest["records_file_sha256"]

        check(self.blob)
        for index in (0, 2683):
            changed = list(self.lines)
            changed[index] = bytes([changed[index][0] ^ 1]) + changed[index][1:]
            with self.subTest(index=index), self.assertRaises(AssertionError):
                check(b"".join(changed))
        with self.assertRaises(AssertionError):
            check(b"".join(self.lines[:-1]))

    def test_semantic_mutations_rejected(self):
        rows = self.records[2683:]
        for field in ("name", "page", "column", "blank"):
            changed = copy.deepcopy(rows)
            if field == "name":
                changed[0]["crop"]["name"] = "수박"
            elif field == "page":
                changed[0]["locator"]["pdf_page"] = 127
            elif field == "column":
                changed[20]["locator"]["column_labels"]["volume_seed"] = "종묘(종균)"
            else:
                changed[16]["metrics"]["seed_count"] = "-"
            with self.subTest(field=field), self.assertRaises(AssertionError):
                check_rows(changed)


if __name__ == "__main__":
    unittest.main()
