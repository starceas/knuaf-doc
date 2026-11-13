"""Isolated unit tests for gg_rda_provenance — audit-key stability, raw-line
binary hashing, physical line numbering, duplicate detection, integrity
gate, reference resolution, snapshot registration.

Self-contained: runnable directly (``python3 -B tests/test_gg_rda_provenance.py``
from ``skills/knuaf-doc/``) or via unittest discovery; no external harness.
Binds SPEC.md §2–§5 / ACCEPTANCE.md D01, D03, D04.
"""
import dataclasses
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

KN_UAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KN_UAF_DOC / "scripts"
PACKS = KN_UAF_DOC / "references" / "benchmark-packs"
sys.path.insert(0, str(SCRIPTS))

import gg_rda_provenance as prov  # noqa: E402

NATIONAL = PACKS / "common/rda-income-national-2024/records.jsonl"
NATIONAL_SHA = prov.PINNED_PACKS["rda.income.national.2024"][
    "records_file_sha256"]


def _write(tmpdir, name, raw_lines):
    p = Path(tmpdir) / name
    p.write_bytes(b"".join(raw_lines))
    return p


def _verified_pack(testcase, records):
    """Write a synthetic records.jsonl, pin it under ``test.pack``, build
    a real verified index over its bytes; returns (index, first_key)."""
    td = tempfile.TemporaryDirectory(prefix="gg-p4-prov-")
    testcase.addCleanup(td.cleanup)
    p = Path(td.name) / "records.jsonl"
    p.write_bytes(b"".join(json.dumps(r, ensure_ascii=False)
                           .encode("utf-8") + b"\n" for r in records))
    file_sha = hashlib.sha256(p.read_bytes()).hexdigest()
    prov.PINNED_PACKS["test.pack"] = {
        "pack_revision": "t1", "records_file_sha256": file_sha,
        "record_lines": len(records), "relpath": "synthetic"}
    testcase.addCleanup(lambda: prov.PINNED_PACKS.pop("test.pack", None))
    idx = prov.build_audit_index(p, "test.pack", file_sha)
    return idx, next(iter(idx))


class AuditKeyAndHashingTests(unittest.TestCase):
    """SPEC §2: audit key tuple + exact binary raw-line hashing (binds D04)."""

    def test_raw_line_sha256_covers_exact_bytes_lf(self):
        raw = b'{"a": 1}\n'
        self.assertEqual(prov.raw_line_sha256(raw),
                         hashlib.sha256(raw).hexdigest())

    def test_raw_line_sha256_distinguishes_crlf_from_lf(self):
        lf = hashlib.sha256(b'{"a": 1}\n').hexdigest()
        crlf = hashlib.sha256(b'{"a": 1}\r\n').hexdigest()
        self.assertNotEqual(lf, crlf)
        self.assertEqual(prov.raw_line_sha256(b'{"a": 1}\r\n'), crlf)

    def test_no_terminator_added_to_final_line(self):
        raw = b'{"a": 1}'  # no newline at all
        self.assertEqual(prov.raw_line_sha256(raw),
                         hashlib.sha256(raw).hexdigest())

    def test_no_whitespace_normalization(self):
        a = prov.raw_line_sha256(b'{"a":1}\n')
        b = prov.raw_line_sha256(b'{"a": 1}\n')
        self.assertNotEqual(a, b)

    def test_audit_key_tuple_and_dict_forms(self):
        tup = ("p.x", "f" * 64, 3, "a" * 64)
        d = prov.audit_key_dict(tup)
        self.assertEqual(d["pack_id"], "p.x")
        self.assertEqual(d["records_file_sha256"], "f" * 64)
        self.assertEqual(d["physical_jsonl_line_1based"], 3)
        self.assertEqual(d["raw_line_sha256"], "a" * 64)
        self.assertEqual(prov.normalize_audit_key(d), tup)
        self.assertEqual(prov.normalize_audit_key(list(tup)), tup)
        with self.assertRaises(ValueError):
            prov.normalize_audit_key(("p.x", "f" * 64))
        with self.assertRaises(ValueError):
            prov.normalize_audit_key({"pack_id": "p.x"})


class LineNumberingTests(unittest.TestCase):
    """physical_jsonl_line_1based counts binary splitlines, blanks included."""

    def test_blank_lines_consume_physical_line_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [
                b'{"n": 1}\n', b'\n', b'{"n": 3}\n'])
            real = prov.sha256_file(p)
            idx = prov.build_audit_index(p, "test.pack", real)
        lines = sorted(k[2] for k in idx)
        self.assertEqual(lines, [1, 2, 3])
        self.assertIsNone(idx[("test.pack", real, 2,
                              prov.raw_line_sha256(b"\n"))].record)

    def test_line_numbers_are_1based_and_sequential_on_real_pack(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        self.assertEqual(sorted(k[2] for k in idx), list(range(1, 103)))

    def test_audit_key_stability_across_rebuilds(self):
        a = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   NATIONAL_SHA)
        b = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   NATIONAL_SHA)
        self.assertEqual(set(a), set(b))


class DuplicateDetectionTests(unittest.TestCase):
    """D01: raw-list duplicate audit keys raise BEFORE dict construction;
    duplicate SOURCE LINES (different physical lines) are legal."""

    def test_identical_source_lines_at_distinct_lines_are_legal(self):
        line = b'{"record_id": "x.dup", "kind": "k"}\n'
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [line, line])
            real = prov.sha256_file(p)
            idx = prov.build_audit_index(p, "test.pack", real)
        self.assertEqual(len(idx), 2)  # distinct physical identities kept

    def test_duplicate_audit_key_in_observation_list_raises(self):
        key = ("p.x", "f" * 64, 7, "b" * 64)
        with self.assertRaises(prov.DuplicateAuditKeyError):
            prov.check_unique_audit_keys([key, key])
        with self.assertRaises(prov.DuplicateAuditKeyError):
            prov.check_unique_audit_keys(
                [key, prov.audit_key_dict(key)])

    def test_unique_observation_list_passes(self):
        keys = [("p.x", "f" * 64, i, "b" * 64) for i in (1, 2, 3)]
        self.assertEqual(prov.check_unique_audit_keys(keys), keys)


class IntegrityGateTests(unittest.TestCase):
    """SPEC §5 / D04: pinned-hash verification is a hard failure."""

    def test_correct_pin_passes(self):
        prov.verify_pack_integrity("rda.income.national.2024", "2024",
                                   NATIONAL_SHA)

    def test_hash_mismatch_raises(self):
        with self.assertRaises(prov.IntegrityError):
            prov.verify_pack_integrity("rda.income.national.2024", "2024",
                                       "0" * 64)

    def test_revision_mismatch_raises(self):
        with self.assertRaises(prov.IntegrityError):
            prov.verify_pack_integrity("rda.income.national.2024", "9999",
                                       NATIONAL_SHA)

    def test_unpinned_pack_raises(self):
        with self.assertRaises(prov.UnpinnedPackError):
            prov.verify_pack_integrity("no.such.pack", "1", "0" * 64)

    def test_build_index_refuses_wrong_declared_hash(self):
        with self.assertRaises(prov.IntegrityError):
            prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                   "0" * 64)


class RealPackIdentityTests(unittest.TestCase):
    """D01: all 6,315 physical rows preserved with immutable audit keys."""

    def test_all_pinned_packs_index_to_6315_physical_keys(self):
        total = 0
        for pack_id, pin in prov.PINNED_PACKS.items():
            idx = prov.build_audit_index(PACKS / pin["relpath"], pack_id,
                                         pin["records_file_sha256"])
            self.assertEqual(len(idx), pin["record_lines"])
            total += len(idx)
        self.assertEqual(total, 6315)

    def test_manifest_audit_chain_matches_record_bytes(self):
        # Every pinned pack's manifest must describe the records.jsonl bytes
        # it ships beside: file hash == pin == actual bytes, and the rows'
        # audit keys == the index built from those bytes.  Catches a record
        # edit (e.g. a field rename) whose manifest hashes were not rebuilt.
        for pack_id, pin in prov.PINNED_PACKS.items():
            with self.subTest(pack=pack_id):
                records = PACKS / pin["relpath"]
                manifest = json.loads(
                    (records.parent / "manifest.json").read_text(
                        encoding="utf-8"))
                actual = hashlib.sha256(records.read_bytes()).hexdigest()
                self.assertEqual(manifest["records_file_sha256"], actual)
                self.assertEqual(pin["records_file_sha256"], actual)
                idx = prov.build_audit_index(records, pack_id, actual)
                rows = manifest["rows"]
                self.assertEqual(len(rows), len(idx))
                keys = set()
                for row in rows:
                    key = prov.normalize_audit_key(row["audit_key"])
                    self.assertEqual(row["raw_line_sha256"], key[3])
                    keys.add(key)
                self.assertEqual(keys, set(idx))

    def test_builtin_sources_pack_artifacts_match_disk(self):
        # builtin-sources.json artifacts[] for benchmark packs must equal
        # the shipped bytes (sha256 and size) — a rebuilt manifest whose
        # registry artifact hash was not refreshed fails here.
        registry = json.loads((KN_UAF_DOC / "references" /
                               "builtin-sources.json").read_text(
                                   encoding="utf-8"))
        seen = []

        def walk(node):
            if isinstance(node, dict):
                for art in node.get("artifacts") or []:
                    rel = art.get("relative_path") \
                        if isinstance(art, dict) else None
                    if isinstance(rel, str) and rel.startswith("common/"):
                        seen.append((rel, art))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(registry)
        self.assertTrue(seen)
        for rel, art in seen:
            with self.subTest(artifact=rel):
                data = (PACKS / rel).read_bytes()
                self.assertEqual(art["sha256"],
                                 hashlib.sha256(data).hexdigest())
                self.assertEqual(art["bytes"], len(data))

    def test_source_aggregation_group_contract(self):
        # record.schema.json common_envelope.source: aggregation_group is
        # survey + "." + data_year, and a pack whose records span more than
        # one group must carry the manifest's no-mixing rule text.
        required = {"publisher", "survey", "survey_label",
                    "aggregation_group", "data_year", "document"}
        checked = 0
        for pack_id, pin in prov.PINNED_PACKS.items():
            records = PACKS / pin["relpath"]
            groups = set()
            for line in records.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                src = rec.get("source")
                if src is None:
                    continue
                with self.subTest(record=rec.get("record_id")):
                    self.assertTrue(required <= set(src), sorted(src))
                    self.assertEqual(
                        src["aggregation_group"],
                        "%s.%d" % (src["survey"], src["data_year"]))
                    self.assertNotIn("aggregation_key", src)
                groups.add(src["aggregation_group"])
                checked += 1
            if len(groups) > 1:
                manifest = json.loads(records.parent.joinpath(
                    "manifest.json").read_text(encoding="utf-8"))
                with self.subTest(pack=pack_id):
                    self.assertIn("aggregation_group",
                                  manifest.get("aggregation_rule", ""))
        self.assertEqual(checked, 153)

    def test_catalog_authority_pins_reproduce_from_real_manifests(self):
        # Every pinned accepted-catalog authority that names a pinned pack
        # must reproduce from that pack's shipped manifest rows; a manifest
        # rebuilt without re-pinning (or the reverse) fails here.
        import gg_rda_research as research
        for auth, digest in research.PINNED_CATALOG_AUTHORITIES.items():
            pack_id = auth.rsplit("/", 1)[-1]
            pin = prov.PINNED_PACKS.get(pack_id)
            if pin is None:
                continue
            with self.subTest(authority=auth):
                manifest = json.loads(
                    (PACKS / pin["relpath"]).parent.joinpath(
                        "manifest.json").read_text(encoding="utf-8"))
                entries = {prov.normalize_audit_key(r["audit_key"]): r
                           for r in manifest["rows"]}
                self.assertEqual(
                    research.catalog_content_digest(entries), digest)

    def test_econ_byte_identical_lines_keep_distinct_keys(self):
        pin = prov.PINNED_PACKS["rda.econ.2025"]
        idx = prov.build_audit_index(PACKS / pin["relpath"],
                                     "rda.econ.2025",
                                     pin["records_file_sha256"])
        by_raw = {}
        for key in idx:
            by_raw.setdefault(key[3], []).append(key)
        dup_groups = [v for v in by_raw.values() if len(v) > 1]
        # 7 byte-identical re-extraction groups exist and stay distinct
        # physical identities — never dropped (D01 check 1)
        self.assertEqual(len(dup_groups), 7)
        for group in dup_groups:
            self.assertEqual(len({k[2] for k in group}), len(group))


# --- W-SUM: cost-item sums vs the printed 계 lines -------------------------
#
# Appendix A of the W-SUM audit (``wsum/RESULT-WSUM.md`` §9, out of tree)
# lists pages whose *printed* figures disagree with the sum of the rows
# printed on the same page.  Those are source defects: the pack is not to be
# "fixed" against them (patching the pack would make it disagree with its own
# source).  ``WSUM_PRINTED_DEFECTS`` freezes each one so that
#   - a new |세목 합 − 계| > 3 anywhere else fails, and
#   - a whitelisted record that gets corrected (or drifts) is reported
#     instead of silently disappearing from the tolerance budget.
#
# Section membership follows the printed table, not the key's name:
#   중간재  = every intermediate row (기타비용 included — it is printed inside
#             the 중간재 block; adding it again to get 경영비 double-counts)
#   경영비  = 중간재 + 임차료(농기계·시설 / 토지) + 위탁영농비 + 고용노력비
#   생산비  = 경영비 + 자가노동비 + 유동/고정/토지 자본용역비
COST_SECTION_INTERMEDIATE = frozenset({
    "seed_and_seedling", "seed_seedling", "fertilizer_mineral",
    "fertilizer_inorganic", "fertilizer_organic", "pesticide",
    "utilities_fuel", "utilities_power", "vehicle", "other_materials",
    "small_tools", "depreciation_large_tools", "large_machinery_depreciation",
    "depreciation_facilities", "farming_facility_depreciation",
    "repair_maintenance", "production_management", "other_costs",
    # 2026-11-13 additions: rows printed on the source page that had no pack
    # key; the money was already inside the printed 중간재 total.
    "orchard_establishment", "bee_colony", "bee_feed",
    "beekeeping_transport_overwinter", "bee_disease_control",
    "mushroom_spawn", "mushroom_substrate", "disinfectant",
})
COST_SECTION_OPERATING_EXTRA = frozenset({
    "rent_machinery_facility", "machinery_facility_rent", "rent_land",
    "land_rent", "consigned_farming", "labor_hired", "hired_labor",
})
COST_SECTION_PRODUCTION_EXTRA = frozenset({
    "labor_own", "unpaid_family_labor", "working_capital_interest",
    "fixed_capital_interest", "land_capital_interest",
})
# Totals and revenue columns — never summed as 세목.
COST_TOTAL_KEYS = frozenset({
    "intermediate_cost_total", "operating_cost", "production_cost_total",
    "main_product_value", "byproduct_value",
})
#: Printed-figure defects from RESULT-WSUM §9 — 12 regional + 1 national 2024.
#: ``printed`` is the value printed on the page for ``printed_line``;
#: ``page_rows_sum`` is the sum of the rows printed on that same page, so
#: ``page_delta`` is purely a source inconsistency.  ``pack_rows_sum`` is the
#: same sum recomputed from the pack's cost_items and ``pack_delta`` its
#: distance from the printed figure — the pack-side number the tolerance
#: check reports.  Correcting any of these records must edit this table.
WSUM_PRINTED_DEFECTS = {
    "rda.income.national.2024.detail.식량작물.봄감자":
        {"pdf_page": 78, "printed_page": "72", "printed_line": "경영비·생산비",
         "printed": 1443511, "page_rows_sum": 1442469, "page_delta": 1042,
         "pack_rows_sum": 1442469, "pack_delta": -1042},
    "rda.income.regional.2024.chungnam.crop.느타리버섯_균상":
        {"pdf_page": 166, "printed_page": "162", "printed_line": "생산비",
         "printed": 21280422, "page_rows_sum": 21381119, "page_delta": -100697,
         "pack_rows_sum": 20396454, "pack_delta": -883968},
    "rda.income.regional.2024.chungnam.crop.양송이버섯":
        {"pdf_page": 167, "printed_page": "163", "printed_line": "생산비",
         "printed": 73601822, "page_rows_sum": 73704228, "page_delta": -102406,
         "pack_rows_sum": 71715773, "pack_delta": -1886049},
    "rda.income.regional.2024.gangwon.crop.봄감자":
        {"pdf_page": 66, "printed_page": "62", "printed_line": "경영비·생산비",
         "printed": 1175498, "page_rows_sum": 1168951, "page_delta": 6547,
         "pack_rows_sum": 1168951, "pack_delta": -6547},
    "rda.income.regional.2024.gyeonggi.crop.노지가을무":
        {"pdf_page": 30, "printed_page": "26", "printed_line": "생산비",
         "printed": 2890241, "page_rows_sum": 2890245, "page_delta": -4,
         "pack_rows_sum": 2666684, "pack_delta": -223557},
    "rda.income.regional.2024.gyeonggi.crop.느타리버섯_병재배":
        {"pdf_page": 60, "printed_page": "56", "printed_line": "생산비",
         "printed": 604379, "page_rows_sum": 605488, "page_delta": -1109,
         "pack_rows_sum": 580915, "pack_delta": -23464},
    "rda.income.regional.2024.gyeonggi.crop.쌀":
        {"pdf_page": 425, "printed_page": "421", "printed_line": "생산비",
         "printed": 881865, "page_rows_sum": 881861, "page_delta": 4,
         "pack_rows_sum": 749901, "pack_delta": -131964},
    "rda.income.regional.2024.gyeongnam.crop.시설장미":
        {"pdf_page": 362, "printed_page": "358", "printed_line": "생산비",
         "printed": 34908584, "page_rows_sum": 34908588, "page_delta": -4,
         "pack_rows_sum": 33464157, "pack_delta": -1444427},
    "rda.income.regional.2024.jeonbuk.crop.느타리버섯_균상":
        {"pdf_page": 208, "printed_page": "204", "printed_line": "생산비",
         "printed": 18503358, "page_rows_sum": 18690160, "page_delta": -186802,
         "pack_rows_sum": 18159583, "pack_delta": -343775},
    "rda.income.regional.2024.jeonbuk.crop.오미자":
        {"pdf_page": 206, "printed_page": "202", "printed_line": "생산비",
         "printed": 6141077, "page_rows_sum": 6141081, "page_delta": -4,
         "pack_rows_sum": 5672355, "pack_delta": -468722},
    "rda.income.regional.2024.jeonnam.crop.가을감자":
        {"pdf_page": 221, "printed_page": "217", "printed_line": "중간재 계",
         "printed": 9763783, "page_rows_sum": 976384, "page_delta": 8787399,
         "pack_rows_sum": 976384, "pack_delta": -8787399},
    "rda.income.regional.2024.jeonnam.crop.느타리버섯_균상":
        {"pdf_page": 267, "printed_page": "263", "printed_line": "경영비·생산비",
         "printed": 17343459, "page_rows_sum": 17351312, "page_delta": -7853,
         "pack_rows_sum": 17351312, "pack_delta": 7853},
    "rda.income.regional.2024.jeonnam.crop.수국":
        {"pdf_page": 261, "printed_page": "257", "printed_line": "생산비",
         "printed": 33786995, "page_rows_sum": 33786999, "page_delta": -4,
         "pack_rows_sum": 31719497, "pack_delta": -2067498},
}

#: Print rounding: the source notes that row sums may miss the total by a
#: sub-unit amount, measured never above 3 (RESULT-WSUM §1).
WSUM_TOLERANCE = 3

#: Records whose packed 세목 sum misses a 계 line **the pack carries** by more
#: than the tolerance. Only 3 of the 13 printed defects land here: the regional
#: 2024 pack stores no 중간재/생산비 total, so the other 10 defects are invisible
#: to a pack-side check and are frozen from the source side instead.
WSUM_PACK_OVER_TOLERANCE = frozenset({
    "rda.income.national.2024.detail.식량작물.봄감자",
    "rda.income.regional.2024.gangwon.crop.봄감자",
    "rda.income.regional.2024.jeonnam.crop.느타리버섯_균상",
})


def cost_section_sums(cost_items):
    """Return ``(intermediate, operating, production)`` sums over 세목 only —
    계 lines, revenue columns and non-numeric (``"-"``) cells excluded."""
    totals = dict.fromkeys(("intermediate", "operating", "production"), 0)
    for key, value in cost_items.items():
        if key in COST_TOTAL_KEYS or value in (None, "-"):
            continue
        amount = int(value)
        if key in COST_SECTION_INTERMEDIATE:
            totals["intermediate"] += amount
            totals["operating"] += amount
            totals["production"] += amount
        elif key in COST_SECTION_OPERATING_EXTRA:
            totals["operating"] += amount
            totals["production"] += amount
        elif key in COST_SECTION_PRODUCTION_EXTRA:
            totals["production"] += amount
        else:  # a new cost key needs a section — fail loudly, not silently
            raise AssertionError("cost_items key %r has no printed section"
                                 % key)
    return tuple(totals[k] for k in ("intermediate", "operating",
                                     "production"))


class CostItemSumTests(unittest.TestCase):
    """W-SUM: 세목 합 vs 계 stays inside ±3 except the 13 source defects."""

    def test_cost_section_sums_follow_printed_sections(self):
        # 기타비용 sits inside the 중간재 block; counting it again for 경영비
        # would double it (the mistake that made 111 national rows look
        # wrong).  자가노동비/자본용역비 reach 생산비 only.
        int_sum, op_sum, prod_sum = cost_section_sums({
            "seed_and_seedling": "100", "other_costs": "50",
            "rent_land": "20", "labor_hired": "30",
            "labor_own": "40", "working_capital_interest": "10",
            "intermediate_cost_total": "999999", "operating_cost": "999999",
            "main_product_value": "999999", "consigned_farming": "-",
        })
        self.assertEqual((int_sum, op_sum, prod_sum), (150, 200, 250))

    def test_unknown_cost_key_is_rejected(self):
        with self.assertRaises(AssertionError):
            cost_section_sums({"seed_and_seedling": "1", "mystery_row": "2"})

    def test_over_tolerance_records_are_whitelisted_printed_defects(self):
        # 세목 합이 팩이 실제로 담고 있는 계와 ±3 넘게 어긋나는 레코드는 원문
        # 인쇄 결함 허용 목록 안에만 있어야 한다. 목록 밖에서 새 차이가 생기면
        # 실패하고, 허용 목록 레코드가 고쳐져 차이가 사라져도 실패한다(그때는
        # WSUM_PACK_OVER_TOLERANCE와 WSUM_PRINTED_DEFECTS를 같이 고친다).
        observed = set()
        for pack_id, pin in prov.PINNED_PACKS.items():
            path = PACKS / pin["relpath"]
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                items = record.get("cost_items")
                if not items:
                    continue
                worst = max(self._carried_deltas(record, items).values(),
                            key=abs)
                if abs(worst) > WSUM_TOLERANCE:
                    observed.add(record["record_id"])
        self.assertEqual(
            observed, set(WSUM_PACK_OVER_TOLERANCE),
            "±%d 초과 레코드가 허용 목록과 다르다" % WSUM_TOLERANCE)
        # 허용 목록은 13건 고정 — 2024 지역 12 + 전국 1 (RESULT-WSUM §9).
        self.assertEqual(len(WSUM_PRINTED_DEFECTS), 13)
        self.assertTrue(set(WSUM_PACK_OVER_TOLERANCE)
                        <= set(WSUM_PRINTED_DEFECTS))

    def _carried_deltas(self, record, items):
        """세목 합 − 계, 팩이 그 계를 실제로 담고 있을 때만."""
        int_sum, op_sum, prod_sum = cost_section_sums(items)
        deltas = {"경영비": op_sum - int(record["metrics"]["operating_cost"])}
        for line_name, key, total in (
                ("중간재 계", "intermediate_cost_total", int_sum),
                ("생산비", "production_cost_total", prod_sum)):
            printed = items.get(key)
            if printed not in (None, "-"):
                deltas[line_name] = total - int(printed)
        return deltas

    def test_whitelisted_defects_reproduce_their_pack_sum(self):
        # 허용 목록은 면죄부가 아니라 동결이다: 13건 각각이 기록된 쪽·인쇄
        # 값·팩 쪽 세목 합을 그대로 재현해야 한다. 허용 목록 아래에서 값이
        # 조용히 바뀌면 여기서 걸린다.
        by_id = {}
        for pack_id, pin in prov.PINNED_PACKS.items():
            path = PACKS / pin["relpath"]
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                by_id[record["record_id"]] = record
        for record_id, expect in WSUM_PRINTED_DEFECTS.items():
            with self.subTest(record=record_id):
                record = by_id.get(record_id)
                self.assertIsNotNone(record, "허용 목록 레코드가 팩에 없다")
                self.assertEqual(record["locator"]["pdf_page"],
                                 expect["pdf_page"])
                self.assertEqual(record["locator"]["printed_page"],
                                 expect["printed_page"])
                int_sum, op_sum, prod_sum = cost_section_sums(
                    record["cost_items"])
                # '경영비·생산비'는 두 계가 같은 값으로 잘못 찍힌 쪽이라
                # 경영비 쪽 세목 합으로 잰다.
                got = {"중간재 계": int_sum, "경영비": op_sum,
                       "생산비": prod_sum,
                       "경영비·생산비": op_sum}[expect["printed_line"]]
                self.assertEqual(got, expect["pack_rows_sum"])
                self.assertEqual(got - expect["printed"], expect["pack_delta"])
                # 허용 목록에 있으면서 ±3 안인 10건은 팩에 그 계 칸이 없어서
                # (지역 2024 팩은 중간재·생산비 계를 담지 않는다) 팩 쪽
                # 검사가 볼 수 없는 원문 결함이다 — 그 사실을 함께 잠근다.
                carried = self._carried_deltas(record, record["cost_items"])
                if record_id in WSUM_PACK_OVER_TOLERANCE:
                    self.assertGreater(max(map(abs, carried.values())),
                                       WSUM_TOLERANCE)
                else:
                    self.assertLessEqual(
                        max(map(abs, carried.values())), WSUM_TOLERANCE,
                        "팩이 그 계를 담고 있으면 팩 쪽 검사가 잡아야 한다")


class KeyRefBindingTests(unittest.TestCase):
    """I-01: every builtin-sources ``key_ref`` line reference is bound to the
    pack bytes shipped next to it.

    The pack pins (``PINNED_PACKS``), the manifest audit chain and the
    registry ``artifacts[]`` hashes are all covered elsewhere; the registry's
    per-row ``key_ref`` (records_file_sha256 + physical line + raw line hash)
    was not, so a pack rebuilt without re-pointing the references kept
    passing.  These tests recompute both hashes from the bytes on disk.
    """

    def _key_refs(self):
        registry = json.loads((KN_UAF_DOC / "references" /
                               "builtin-sources.json").read_text(
                                   encoding="utf-8"))
        refs = []

        def walk(node):
            if isinstance(node, dict):
                ref = node.get("key_ref")
                if isinstance(ref, dict) and ref.get("kind") == "audit_key":
                    refs.append(ref)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(registry)
        return refs

    def test_every_key_ref_matches_current_pack_bytes(self):
        refs = self._key_refs()
        # 7 audit_key refs ship today (4 packs × 2 rows, regional 2024 × 1);
        # the count is pinned so a generator that drops refs is caught too.
        self.assertEqual(len(refs), 7)
        for ref in refs:
            pack_id = ref["pack_id"]
            with self.subTest(pack=pack_id,
                              line=ref["physical_jsonl_line_1based"]):
                pin = prov.PINNED_PACKS[pack_id]
                data = (PACKS / pin["relpath"]).read_bytes()
                self.assertEqual(ref["records_file_sha256"],
                                 hashlib.sha256(data).hexdigest())
                lines = data.splitlines(keepends=True)
                line_no = ref["physical_jsonl_line_1based"]
                self.assertLessEqual(line_no, len(lines))
                self.assertEqual(ref["raw_line_sha256"],
                                 prov.raw_line_sha256(lines[line_no - 1]))

    def test_key_ref_lines_resolve_in_the_audit_index(self):
        # The reference must also point at a key the pack actually indexes:
        # same line, same raw hash, same file hash — otherwise a reordered
        # pack keeps the hashes but loses the row.
        for ref in self._key_refs():
            pack_id = ref["pack_id"]
            pin = prov.PINNED_PACKS[pack_id]
            with self.subTest(pack=pack_id,
                              line=ref["physical_jsonl_line_1based"]):
                idx = prov.build_audit_index(PACKS / pin["relpath"], pack_id,
                                             pin["records_file_sha256"])
                self.assertIn(prov.normalize_audit_key(ref), idx)



class ResolveReferenceTests(unittest.TestCase):
    """resolve_reference: requires a verified index context (P4-03), checks
    physical membership in it, registered -> observation; unregistered ->
    None; stale pin -> IntegrityError; never promotes (SPEC §3/§6)."""

    def _key(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        return next(iter(idx)), idx

    def test_requires_verified_index_context(self):
        """D09/P4-03: no verified context -> fails closed, not open."""
        key, idx = self._key()
        with self.assertRaises(TypeError):  # required kwarg — fail closed
            prov.resolve_reference(key, catalog={})
        with self.assertRaises(prov.ResolutionContextError):
            prov.resolve_reference(key, catalog={}, verified_index=None)
        with self.assertRaises(prov.ResolutionContextError):
            prov.resolve_reference(key, catalog=None, verified_index=idx)

    def test_key_not_in_verified_index_returns_none(self):
        """A key that is not a member of the verified index does not
        resolve — declared hashes alone are never accepted (P4-03)."""
        key, idx = self._key()
        other = (key[0], key[1], key[2] + 1, "0" * 64)
        self.assertIsNone(prov.resolve_reference(
            other, catalog={other: {"catalog_status": "exploratory"}},
            verified_index=idx))

    def test_unregistered_returns_none(self):
        key, idx = self._key()
        self.assertIsNone(prov.resolve_reference(key, catalog={},
                                                 verified_index=idx))

    def test_registered_resolves_with_declared_status(self):
        key, idx = self._key()
        row = idx[key]
        catalog = {key: {"catalog_status": "exploratory",
                         "record_id": row.record["record_id"],
                         "record": row.record, "locator": row.record.get(
                             "locator")}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.catalog_status,
                         prov.CatalogStatus.EXPLORATORY)
        self.assertEqual(obs.audit_key, key)
        self.assertEqual(obs.record["record_id"], row.record["record_id"])

    def test_resolved_record_comes_from_verified_bytes(self):
        """The resolved payload is the verified index row — a catalog entry
        cannot inject different record bytes."""
        key, idx = self._key()
        row = idx[key]
        catalog = {key: {"catalog_status": "exploratory",
                         "record": {"forged": True}}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        self.assertEqual(obs.record, row.record)
        self.assertNotIn("forged", obs.record)

    def test_stale_file_hash_in_key_is_hard_failure(self):
        key, idx = self._key()
        stale = (key[0], "0" * 64, key[2], key[3])
        with self.assertRaises(prov.IntegrityError):
            prov.resolve_reference(stale, catalog={key: {}},
                                   verified_index=idx)

    def test_missing_catalog_status_rejected(self):
        key, idx = self._key()
        with self.assertRaises(ValueError):
            prov.resolve_reference(key, catalog={key: {"record_id": "x"}},
                                   verified_index=idx)

    def test_d03_value_types_preserved_verbatim(self):
        """null vs "0" vs "-"/unobserved vs missing stay strictly
        distinguishable through resolve — no coercion, no series collapse
        to bare scalar.  Uses a synthetic verified index (record bytes are
        really hashed) since resolve reads the verified row, not the
        catalog entry."""
        rec = {"record_id": "t.d03", "pack_id": "test.pack",
               "kind": "k", "locator": {"row_label": "x"},
               "metrics": {"scalar": "422", "zero": "0",
                           "null_metric": None, "dash": "-",
                           "unobserved": "미조사",
                           "series": {"y2023": "1", "y2024": "2"},
                           "range": {"low": "10", "high": "20"},
                           "int_zero": 0, "float_zero": 0.0}}
        idx, key = _verified_pack(self, [rec])
        catalog = {key: {"catalog_status": "quarantined"}}
        obs = prov.resolve_reference(key, catalog=catalog,
                                     verified_index=idx)
        m = obs.record["metrics"]
        self.assertIsNone(m["null_metric"])
        self.assertEqual(m["zero"], "0")
        self.assertIsNot(m["zero"], m["null_metric"])
        # numeric zeros preserved verbatim: int 0, float 0.0, string "0",
        # and null are four strictly distinguishable values
        self.assertEqual(m["int_zero"], 0)
        self.assertIsInstance(m["int_zero"], int)
        self.assertNotIsInstance(m["int_zero"], bool)
        self.assertEqual(m["float_zero"], 0.0)
        self.assertIsInstance(m["float_zero"], float)
        self.assertIsNot(m["int_zero"], m["null_metric"])
        self.assertIsNot(m["float_zero"], m["null_metric"])
        self.assertEqual(m["dash"], "-")
        self.assertEqual(m["unobserved"], "미조사")
        self.assertNotIn("missing_key", m)
        self.assertEqual(m["series"], {"y2023": "1", "y2024": "2"})
        self.assertEqual(m["range"], {"low": "10", "high": "20"})




class SourceRefConflictTests(unittest.TestCase):
    """D04: stale/duplicate/unresolvable source_refs surface as conflicts."""

    def _key(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        return next(iter(idx))

    def test_duplicate_ref_conflict(self):
        key = self._key()
        out = prov.audit_source_refs([{"audit_key": key},
                                      {"audit_key": prov.audit_key_dict(key)}])
        self.assertEqual(len(out["resolved"]), 1)
        self.assertEqual(out["conflicts"][0]["kind"], "duplicate_ref")

    def test_stale_pin_conflict(self):
        key = self._key()
        stale = (key[0], "0" * 64, key[2], key[3])
        out = prov.audit_source_refs([{"audit_key": stale}])
        self.assertEqual(out["conflicts"][0]["kind"], "stale_pin")

    def test_unregistered_conflict(self):
        key = self._key()
        out = prov.audit_source_refs([{"audit_key": key}], catalog={})
        self.assertEqual(out["conflicts"][0]["kind"], "unregistered")

    def test_malformed_conflict(self):
        out = prov.audit_source_refs([{"audit_key": ("only", "three", 1)}])
        self.assertEqual(out["conflicts"][0]["kind"], "malformed")


class SnapshotTests(unittest.TestCase):
    """register_snapshot: immutable frozen copy (binds D08)."""

    def _resolved(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        key = next(iter(idx))
        row = idx[key]
        catalog = {key: {"catalog_status": "verified_observation",
                         "record_id": row.record["record_id"],
                         "record": row.record,
                         "locator": row.record.get("locator")}}
        return prov.resolve_reference(key, catalog=catalog,
                                      verified_index=idx)

    def test_snapshot_written_and_receipt_hashes_match(self):
        obs = self._resolved()
        with tempfile.TemporaryDirectory() as td:
            rec = prov.register_snapshot(obs, sources_dir=td)
            data = Path(rec.path).read_bytes()
            self.assertEqual(rec.sha256, hashlib.sha256(data).hexdigest())
            body = json.loads(data)
            self.assertEqual(body["audit_key"]["raw_line_sha256"],
                             obs.audit_key[3])
            self.assertEqual(body["record"], obs.record)

    def test_reregister_identical_is_idempotent(self):
        obs = self._resolved()
        with tempfile.TemporaryDirectory() as td:
            a = prov.register_snapshot(obs, sources_dir=td)
            b = prov.register_snapshot(obs, sources_dir=td)
            self.assertEqual(a.path, b.path)

    def test_different_bytes_same_path_conflict(self):
        obs = self._resolved()
        other = prov.ResolvedObservation(
            obs.audit_key, obs.pack_id, obs.pack_revision,
            obs.record_id, {"different": True}, obs.locator,
            obs.catalog_status)
        with tempfile.TemporaryDirectory() as td:
            prov.register_snapshot(obs, sources_dir=td)
            with self.assertRaises(prov.SnapshotConflictError):
                prov.register_snapshot(other, sources_dir=td)


class AdmissionValidationTests(unittest.TestCase):
    """P4-01: D01 admission checks — 1:1 join, schema types, semantic-
    duplicate rejection, nonfinite rejection."""

    def _obs(self, key, record=None):
        entry = {"audit_key": key}
        if record is not None:
            entry["record"] = record
        return entry

    def test_clean_join_passes(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k, idx[k].record) for k in idx]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "PASS")
        self.assertEqual(rep["checked_observations"], 102)

    def test_key_only_entries_validate_against_verified_payloads(self):
        """Corrections-2 P4-01: key-only entries resolve payloads from the
        verified index — they still undergo every check (national has
        distinct payloads, so a complete key-only join PASSes — proving
        resolution, not bypass)."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [{"audit_key": k} for k in idx]  # key-only, no record field
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "PASS")

    def test_complete_join_duplicate_payloads_rejected(self):
        """Corrections-2 P4-01 negative: a COMPLETE 1:1 join (all keys
        present, all unique) still fails when two distinct physical keys
        carry identical observation payloads."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k1, k2 = list(idx)[:2]
        obs = [{"audit_key": k} for k in idx]
        # inject: entry for k2 claims k1's payload instead of its own
        obs[1] = {"audit_key": k2, "record": dict(idx[k1].record)}
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(rep["checks"]["join_1to1"]["ok"])  # join itself clean
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])

    def test_key_only_entry_cannot_hide_nonfinite_verified_payload(self):
        """Key-only admission of a row whose VERIFIED payload contains
        NaN is still flagged — resolving the payload does not skip the
        nonfinite check."""
        rec = {"record_id": "t.nan", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": float("nan")}}
        idx, key = _verified_pack(self, [rec])
        rep = prov.validate_observation_admission(
            [{"audit_key": key}], verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_rawrow_record_none_resolves_semantic_duplicates(self):
        """Corrections-3 P4-01: a RawRow with record=None resolves its
        payload from the verified index — duplicate semantic payloads in
        the index are still rejected (no None-bypass)."""
        rec = {"record_id": "t.dup", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": "1"}}
        idx, _k = _verified_pack(self, [rec, dict(rec)])  # same payload
        k1, k2 = list(idx)
        obs = [dataclasses.replace(idx[k1], record=None),
               dataclasses.replace(idx[k2], record=None)]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(rep["checks"]["join_1to1"]["ok"])
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])

    def test_rawrow_record_none_resolves_nonfinite_payload(self):
        """Corrections-3 P4-01: RawRow(record=None) resolves the verified
        payload — a NaN inside it is still flagged as nonfinite."""
        rec = {"record_id": "t.nan", "pack_id": "test.pack", "kind": "k",
               "locator": {"row_label": "x"}, "metrics": {"v": float("nan")}}
        idx, key = _verified_pack(self, [rec])
        obs = [dataclasses.replace(idx[key], record=None)]
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_orphan_key_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k) for k in idx]
        obs.append(self._obs(("rda.income.national.2024", NATIONAL_SHA,
                            999, "f" * 64)))
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["join_1to1"]["ok"])
        self.assertEqual(
            len(rep["checks"]["join_1to1"]["orphan_observation_keys"]), 1)

    def test_missing_observation_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        obs = [self._obs(k) for k in list(idx)[1:]]  # drop first key
        rep = prov.validate_observation_admission(obs,
                                                  verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertEqual(
            len(rep["checks"]["join_1to1"]["missing_index_keys"]), 1)

    def test_schema_type_errors_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        bad = dict(prov.audit_key_dict(next(iter(idx))))
        bad["raw_line_sha256"] = "not-hex"
        rep = prov.validate_observation_admission(
            [self._obs(bad)] + [self._obs(k) for k in list(idx)[1:]],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["schema_types"]["ok"])

    def test_semantic_duplicate_rejected(self):
        """Same observation content admitted under two different physical
        keys — a distinct failure mode from raw-list key duplicates."""
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k1, k2 = list(idx)[:2]
        same = idx[k1].record  # identical content, different physical key
        rep = prov.validate_observation_admission(
            [self._obs(k1, same), self._obs(k2, dict(same))],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertFalse(rep["checks"]["semantic_duplicates"]["ok"])
        self.assertEqual(
            len(rep["checks"]["semantic_duplicates"]["groups"]), 1)

    def test_duplicate_observation_key_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k = next(iter(idx))
        rep = prov.validate_observation_admission(
            [self._obs(k), self._obs(prov.audit_key_dict(k))],
            verified_index=idx)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(
            rep["checks"]["join_1to1"]["duplicate_observation_keys"])

    def test_nonfinite_values_rejected(self):
        idx = prov.build_audit_index(NATIONAL, "rda.income.national.2024",
                                     NATIONAL_SHA)
        k = next(iter(idx))
        for bad in (float("nan"), float("inf"), float("-inf")):
            rep = prov.validate_observation_admission(
                [self._obs(k, {"metrics": {"v": bad}})],
                verified_index=idx)
            self.assertEqual(rep["verdict"], "FAIL")
            self.assertFalse(rep["checks"]["nonfinite_values"]["ok"])

    def test_requires_verified_index(self):
        with self.assertRaises(prov.ResolutionContextError):
            prov.validate_observation_admission([], verified_index=None)


class NegativePackTests(unittest.TestCase):
    """P4-05 negatives: missing/tampered pack bytes are hard failures."""

    def test_missing_pack_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                prov.load_pack_records(Path(td) / "nope.jsonl",
                                       "rda.income.national.2024")

    def test_tampered_pack_bytes_raise(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [b'{"a": 1}\n'])
            pin = prov.PINNED_PACKS["rda.income.national.2024"]
            with self.assertRaises(prov.IntegrityError):
                # real file sha != pinned national sha
                prov.load_pack_records(p, "rda.income.national.2024")
            with self.assertRaises(prov.IntegrityError):
                prov.build_audit_index(p, "rda.income.national.2024",
                                       pin["records_file_sha256"])

    def test_unpinned_pack_load_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "r.jsonl", [b'{"a": 1}\n'])
            with self.assertRaises(prov.UnpinnedPackError):
                prov.load_pack_records(p, "totally.unpinned.pack")


if __name__ == "__main__":
    unittest.main(verbosity=2)
