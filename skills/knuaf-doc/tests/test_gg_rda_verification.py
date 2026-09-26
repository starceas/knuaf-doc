"""rda-lookup 검증 상태 계약 시험 (DESIGN-RL r3 K1–K8, §3 T1–T10, §6 F1/F2/F4,
§7 R2-2/R2-4/R2-5).

- K1  반환 레코드마다 ``verification`` 묶음 — 계산값만, 팩 레코드의 키는
      항상 덮어씀(T5 위조 차단).
- K2  판정 enum ``not_found|ambiguous|series|unverified|unique`` — 검증된
      단일 관측만 ``unique``; audit_key 경로도 같은 순서(단일 시계열은
      ``series``).
- K3  최상위 ``verification_summary`` — 레코드 level 개수와 일치.
- K4  manifest 인증 함수는 lookup 공용 — candidates는 호출만(동작 불변,
      항목은 깊은 복사본).
- K7  반환 레코드는 캐시와 공유되지 않는 깊은 복사본 — 변형 오염 불가(F1).
- K8  단일 술어 ``is_verified_observation`` = 인증 manifest ∧
      verified_observation ∧ 수령증 완전 ∧ extraction.status == "extracted".
      lookup verification.verified / candidates 관측 축 / research
      promotable이 같은 술어다.
- R2-2 rejected는 술어 직접 단위 시험 + 실팩 D5 연동 시험 — D5
      병합 이후 rejected 행은 매칭에서 제외되며 술어도 false를
      강제한다(이중 방어).
- R2-4 검증 실패(인증/항목/수령증/상태/extraction/중복 키/타 팩 권위)와
      증거만 실패(source/rights/conflict/applicability)를 구분 — 후자는
      lookup이 ``unique``+verified를 유지하고 candidates만 unapproved.
- R2-5 T1 행별 불변식: 단일+비시계열+완전필터(또는 audit 선택)일 때만
      미검증 → ``unverified``; 시계열 단일 → ``series``; 다중 → ``ambiguous``.

실데이터 기대값은 파일 해시·줄 번호를 하드코딩하지 않고 manifest를 읽어
계산한다(D5 재기준화 대비). 대표 격리 사례는 사과 왜성(M9/M26)의
useful_life 행이다.

Self-contained: ``python3 -B tests/test_gg_rda_verification.py`` from
``skills/knuaf-doc/``.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KNUAF_DOC = Path(__file__).resolve().parents[1]
SCRIPTS = KNUAF_DOC / "scripts"
REPO_ROOT = KNUAF_DOC.parents[1]
sys.path.insert(0, str(SCRIPTS))

import gg_rda_lookup as lookup  # noqa: E402
import gg_rda_provenance as prov  # noqa: E402
import gg_rda_research as research  # noqa: E402
import gg_rda_candidates as cand  # noqa: E402

TEST_PACK = "test.verification.pack"
TEST_REV = "t1"
TEST_AUTHORITY = "test.verification.accepted/" + TEST_PACK
SRC_SHA = hashlib.sha256(b"verification test source pdf").hexdigest()
OUT_SHA = hashlib.sha256(b"verification test parser output").hexdigest()

_LABEL_SUFFIX = "옮기지 말 것"


def _record(rid, name, *, form="생체", year=2024, kind="test_kind",
            metrics=None, region_level="national", sido=None,
            quantity_unit="kg", extraction="extracted", extra=None):
    """Synthetic physical row; ``extraction`` mirrors the real pack shape
    (dict with ``status``).  Pass a dict for malformed shapes, ``False`` to
    omit the key entirely; ``extra`` is merged verbatim last."""
    rec = {
        "record_id": rid,
        "pack_id": TEST_PACK,
        "kind": kind,
        "schema_version": "1.0.0",
        "crop": {"name": name, "aliases": [], "form": form,
                 "cycle": "년 1기작", "category": "시험",
                 "major_ids": ["specialty_crops"]},
        "region": {"level": region_level, "name": sido or "전국",
                   "sido": sido},
        "basis": {"year": year, "quantity_unit": quantity_unit,
                  "area_unit": None, "currency": None},
        "metrics": metrics or {"value": "100"},
        "cost_items": None,
        "locator": {"row_label": name},
    }
    if isinstance(extraction, dict):
        rec["extraction"] = extraction
    elif extraction:
        rec["extraction"] = {"status": extraction}
    if extra:
        rec.update(extra)
    return rec


def _evidence_for(rec):
    """Fully-approved axis declaration for one synthetic record."""
    crop = rec["crop"]
    return {
        "source": {
            "custody": "bytes_secured",
            "media_type": "pdf",
            "source_id": "test.source.2024",
            "source_sha256": SRC_SHA,
            "acquired": {"path": "docs/test-source.pdf",
                         "at": "2026-01-01T00:00:00Z"},
            "locator": {"pdf_page": 3},
            "parser": {"id": "test-parser", "version": "1.0.0"},
            "output_sha256": OUT_SHA,
            "unit": "kg",
            "population": "시험 모집단",
        },
        "conflict": "none",
        "rights": {"redistribution": "confirmed", "use": "confirmed"},
        "applicability": {
            "major_ids": ["specialty_crops"],
            "species": [crop["name"]],
            "products": [crop["form"] or "생체"],
            "regions": ["national"],
            "periods": [2024],
            "units": ["kg"],
            "population": "시험 모집단",
            "use_scope": ["plan_input", "market_context"],
        },
    }


def _receipt():
    return {"observation_status": "matched",
            "source_pdf_sha256": SRC_SHA,
            "prep_receipt_ref": "runs/test/AUDIT-JOIN.jsonl#L1"}


def _build(root, specs):
    """Write a synthetic pinned pack: records.jsonl + manifest.json rows
    + catalog.json (with pinned source_sha256) + major aliases.

    ``specs`` items: {"record": rec, "catalog_status": str,
    "source_observation": dict|"keep"|"omit", "evidence": dict|None,
    "include_row": bool}.
    Returns (packs_dir, file_sha, keys, entries, records_path)."""
    packs = Path(root)
    pack_dir = packs / "common" / "test-pack"
    pack_dir.mkdir(parents=True)
    (packs / "aliases").mkdir()
    (packs / "aliases" / "major-aliases.json").write_text(json.dumps({
        "majors": {"specialty_crops": {"label": "특용작물전공",
                                       "aliases": ["특용작물"],
                                       "status": "완성"}}
    }, ensure_ascii=False), encoding="utf-8")

    records_path = pack_dir / "records.jsonl"
    records_path.write_bytes(b"".join(
        json.dumps(s["record"], ensure_ascii=False).encode("utf-8")
        + b"\n" for s in specs))
    file_sha = hashlib.sha256(records_path.read_bytes()).hexdigest()
    index = prov.build_audit_index(records_path, TEST_PACK, file_sha)
    keys = sorted(index, key=lambda k: k[2])

    rows, entries = [], {}
    for spec, key in zip(specs, keys):
        if spec.get("include_row", True) is False:
            continue
        so = spec.get("source_observation", "keep")
        row = {
            "physical_line": key[2],
            "raw_line_sha256": key[3],
            "audit_key": prov.audit_key_dict(key),
            "catalog_status": spec.get("catalog_status",
                                       "verified_observation"),
            "duplicate_link": {"is_primary": True,
                               "primary_audit_key": None},
            "gap_evidence": None,
        }
        if so == "keep":
            row["source_observation"] = _receipt()
        elif so != "omit":
            row["source_observation"] = so
        if spec.get("evidence") is not None:
            row["evidence"] = spec["evidence"]
        rows.append(row)
        entries[key] = row
    manifest = {
        "schema": "knuaf-doc-benchmark-pack-manifest/1",
        "pack_id": TEST_PACK,
        "pack_revision": "1.0.0",
        "records_file": "records.jsonl",
        "records_file_sha256": file_sha,
        "total_physical_rows": len(keys),
        "rows": rows,
    }
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (packs / "catalog.json").write_text(json.dumps({
        "packs": [{"pack_id": TEST_PACK, "kind": "common",
                   "major_id": None, "rank": 1,
                   "relpath": "common/test-pack/records.jsonl",
                   "source_sha256": SRC_SHA}],
        "gaps": [],
    }, ensure_ascii=False), encoding="utf-8")
    return packs, file_sha, keys, entries, records_path


GOOD_QUERY = dict(major="특용작물", kind="test_kind", year=2024,
                  form="생체")
# lookup_approved_candidates takes major positionally — keep it out of
# the keyword dict
CAND_QUERY = dict(kind="test_kind", year=2024, form="생체",
                  unit="kg", use_scope="plan_input")


def _good_spec(name="감자", rid="test.verification.감자", **over):
    rec = _record(rid, name, **over.pop("record_over", {}))
    spec = {"record": rec,
            "catalog_status": over.pop("catalog_status",
                                       "verified_observation"),
            "source_observation": over.pop("source_observation", "keep"),
            "evidence": over.pop("evidence", _evidence_for(rec)),
            "include_row": over.pop("include_row", True)}
    spec.update(over)
    return spec


class VerificationPackMixin(unittest.TestCase):
    """Mount ``self.specs`` as the whole benchmark-packs tree, pinned in
    ``prov.PINNED_PACKS``; pin the manifest digest under ``self.authority``
    unless ``pin_authority`` is false."""

    specs = None
    authority = TEST_AUTHORITY
    pin_authority = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gg-vfy-test-")
        self.addCleanup(self._tmp.cleanup)
        specs = self.specs if self.specs is not None else [_good_spec()]
        (self.packs_dir, self.file_sha, self.keys, self.entries,
         self.records_path) = _build(self._tmp.name, specs)
        self._base = lookup.BASE_DIR
        lookup.BASE_DIR = self.packs_dir
        lookup._CACHE.clear()
        self._pin = dict(prov.PINNED_PACKS)
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": self.file_sha,
            "record_lines": len(specs),
            "relpath": "common/test-pack/records.jsonl",
        }
        self._auth = dict(research.PINNED_CATALOG_AUTHORITIES)
        if self.pin_authority:
            research.PINNED_CATALOG_AUTHORITIES[self.authority] = \
                research.catalog_content_digest(self.entries)
        self.addCleanup(self._restore)

    def _restore(self):
        lookup.BASE_DIR = self._base
        lookup._CACHE.clear()
        prov.PINNED_PACKS.clear()
        prov.PINNED_PACKS.update(self._pin)
        research.PINNED_CATALOG_AUTHORITIES.clear()
        research.PINNED_CATALOG_AUTHORITIES.update(self._auth)

    def _lookup(self, crop="감자", region="전국", **kw):
        args = dict(GOOD_QUERY)
        args.update(kw)
        return lookup.lookup_rda_data(crop, region, **args)

    def _candidates(self, crop="감자", region="전국", **kw):
        args = dict(CAND_QUERY)
        args.update(kw)
        return cand.lookup_approved_candidates(crop, region, "특용작물",
                                               **args)

    def _propose_ctx(self):
        """Accepted-catalog resolver context for the synthetic pack."""
        index = prov.build_audit_index(
            self.records_path, TEST_PACK, self.file_sha)
        cat = research.accept_catalog(
            self.entries, verified_indexes={TEST_PACK: index},
            authority=TEST_AUTHORITY)
        return research.resolver_context(
            packs_dir=self.packs_dir, catalog=cat, pack_ids={TEST_PACK})

    def _manifest_path(self):
        return self.records_path.parent / "manifest.json"


# ---------------------------------------------------------------------------
# K1/K2/K3 — verification bundle, verdicts, summary (synthetic positive)


class SyntheticPositiveTests(VerificationPackMixin):
    """F4 positive: authenticated manifest + verified + receipt +
    extracted + every evidence axis -> lookup unique/verified AND
    candidates approved (the approval path stays alive)."""

    def test_positive_is_unique_verified_and_approved(self):
        got = self._lookup()
        self.assertEqual(got["status"], "unique")
        self.assertEqual(len(got["records"]), 1)
        v = got["records"][0]["verification"]
        self.assertTrue(v["verified"])
        self.assertEqual(v["level"], "verified")
        self.assertEqual(v["catalog_status"], "verified_observation")
        self.assertTrue(v["catalog_accepted"])
        self.assertTrue(v["receipt_complete"])
        self.assertEqual(v["observation_status"], "matched")
        self.assertEqual(v["extraction_status"], "extracted")
        self.assertIn("검증됨", v["label"])
        self.assertIn("승인 필요", v["label"])
        self.assertNotIn(_LABEL_SUFFIX, v["label"])
        self.assertIn("사용 승인 아님", got["reason"])
        s = got["verification_summary"]
        self.assertEqual(s["verified"], 1)
        self.assertTrue(s["all_verified"])
        self.assertIsNone(s["notice"])
        approved = self._candidates()
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["observation"]["status"], "unique")
        self.assertEqual(approved["counts"]["approved"], 1)

    def test_audit_key_selection_of_verified_row_is_unique(self):
        base = self._lookup()
        key = base["records"][0]["audit_key"]
        got = self._lookup(audit_key=key)
        self.assertEqual(got["status"], "unique")
        self.assertTrue(got["records"][0]["verification"]["verified"])


class VerificationFailureAxisTests(VerificationPackMixin):
    """R2-4 검증 실패 축 — each break makes lookup non-unique with the
    expected ``verification.level`` and candidates unapproved."""

    def _expect(self, specs, level, *, reason=None, crop="감자",
                note="", authority=TEST_AUTHORITY, **q):
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-ax-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, specs)
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": len(specs),
            "relpath": "common/test-pack/records.jsonl",
        }
        # the helper controls the authority pin for THIS spec —
        # authority=None means no pinned digest at all
        research.PINNED_CATALOG_AUTHORITIES.pop(TEST_AUTHORITY, None)
        if authority is not None:
            research.PINNED_CATALOG_AUTHORITIES[authority] = \
                research.catalog_content_digest(entries)
        with self.subTest(case="%s:%s" % (note or "axis", level)):
            got = lookup.lookup_rda_data(
                crop, "전국", **dict(GOOD_QUERY, **q))
            self.assertNotEqual(got["status"], "unique")
            self.assertEqual(got["status"], "unverified")
            self.assertEqual(
                got["records"][0]["verification"]["level"], level)
            self.assertFalse(
                got["records"][0]["verification"]["verified"])
            if level != "verified":
                self.assertIn(_LABEL_SUFFIX,
                              got["records"][0]["verification"]["label"])
            approved = cand.lookup_approved_candidates(
                crop, "전국", "특용작물", **dict(CAND_QUERY, **q))
            self.assertEqual(approved["status"], "unapproved")
            if reason:
                self.assertIn(
                    reason,
                    approved["candidates"][0]["rejections"])

    def test_catalog_not_accepted(self):
        # authority table lacks this pack's pinned digest entirely
        self._expect([_good_spec()], "unknown", authority=None,
                     reason="catalog_not_accepted",
                     note="unauthenticated manifest")

    def test_other_packs_authority_never_authenticates(self):
        # the manifest's own digest pinned under a DIFFERENT pack name —
        # authentication is pack_id-scoped, so accepted stays False
        self._expect([_good_spec()], "unknown",
                     authority="test.verification.accepted/other.pack.id",
                     reason="catalog_not_accepted",
                     note="foreign pack authority")

    def test_catalog_entry_absent(self):
        self._expect([_good_spec(include_row=False)], "unknown",
                     reason="catalog_entry_absent",
                     note="manifest row missing")

    def test_receipt_incomplete(self):
        self._expect(
            [_good_spec(source_observation={"observation_status": "matched",
                                            "source_pdf_sha256": SRC_SHA})],
            "unknown", reason="catalog_receipt_incomplete",
            note="incomplete receipt")

    def test_status_exploratory(self):
        self._expect([_good_spec(catalog_status="exploratory")],
                     "exploratory", reason="observation_not_verified",
                     note="exploratory")

    def test_status_quarantined(self):
        self._expect([_good_spec(catalog_status="quarantined")],
                     "quarantined", reason="observation_not_verified",
                     note="quarantined")

    def test_extraction_absent_and_malformed(self):
        # rejected rows are covered on the real packs by
        # test_d5_rejected_row_unreachable (R2-2/D5 filter);
        # missing/malformed extraction stays here.
        for label, extraction in (
            ("absent", False),
            ("non-dict", {"extraction": "not-a-dict"}),
            ("non-string status", {"status": 123}),
        ):
            if extraction is False:
                spec = _good_spec(record_over={"extraction": False})
            elif isinstance(extraction, dict) and "extraction" in extraction:
                spec = _good_spec(record_over={"extra": extraction})
            else:
                spec = _good_spec(record_over={"extraction": extraction})
            self._expect([spec], "unknown",
                         reason="observation_not_verified",
                         note="extraction %s" % label)

    def test_duplicate_manifest_key_not_authenticatable(self):
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-dup-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, [_good_spec()])
        mpath = records_path.parent / "manifest.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["rows"].append(dict(manifest["rows"][0]))  # same key x2
        mpath.write_text(json.dumps(manifest, ensure_ascii=False),
                         encoding="utf-8")
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": 1,
            "relpath": "common/test-pack/records.jsonl",
        }
        research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
            research.catalog_content_digest(entries)
        got = lookup.lookup_rda_data("감자", "전국", **GOOD_QUERY)
        self.assertEqual(got["status"], "unverified")
        self.assertEqual(got["records"][0]["verification"]["level"],
                         "unknown")
        self.assertIsNone(
            got["records"][0]["verification"]["catalog_status"])

    def test_manifest_absent_fails_closed(self):
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-noman-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, [_good_spec()])
        (records_path.parent / "manifest.json").unlink()
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": 1,
            "relpath": "common/test-pack/records.jsonl",
        }
        got = lookup.lookup_rda_data("감자", "전국", **GOOD_QUERY)
        self.assertEqual(got["status"], "unverified")
        self.assertEqual(got["records"][0]["verification"]["level"],
                         "unknown")

    def test_manifest_lone_surrogate_content_fails_closed(self):
        # FIX1-M2: a manifest value carrying a JSON-escaped lone
        # surrogate (a backslash-u-d800 escape decoded by json.loads)
        # makes the canonical digest raise UnicodeEncodeError; lookup
        # must degrade to unknown verification instead of crashing, and
        # the parsed entries stay available.
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-surr-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, [_good_spec()])
        mpath = records_path.parent / "manifest.json"
        mtxt = mpath.read_text(encoding="utf-8")
        mtxt = mtxt.replace('"matched"',
                            '"mat' + chr(92) + 'ud800ched"', 1)
        mpath.write_text(mtxt, encoding="utf-8")
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": 1,
            "relpath": "common/test-pack/records.jsonl",
        }
        research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
            research.catalog_content_digest(entries)
        got = lookup.lookup_rda_data("감자", "전국", **GOOD_QUERY)
        self.assertEqual(got["status"], "unverified")
        v = got["records"][0]["verification"]
        self.assertEqual(v["level"], "unknown")
        self.assertFalse(v["verified"])
        self.assertFalse(v["catalog_accepted"])
        # entries survived the digest failure — the claimed status
        # still surfaces for audit output
        self.assertEqual(v["catalog_status"], "verified_observation")
        self.assertIsNone(v["observation_status"])


class NestedMetadataIsolationTests(VerificationPackMixin):
    """Malformed nested status fields become null on both lookup paths;
    the cached manifest and pack record retain their original values."""

    specs = [_good_spec(
        source_observation={
            "observation_status": {"detail": "matched"},
            "source_pdf_sha256": SRC_SHA,
            "prep_receipt_ref": "runs/test/AUDIT-JOIN.jsonl#L1"},
        record_over={"extraction": {"status": {"raw": "extracted"}}})]

    def _pack(self):
        return {"pack_id": TEST_PACK,
                "relpath": "common/test-pack/records.jsonl"}

    def test_all_status_fields_reject_malformed_values(self):
        rec = self._lookup()["records"][0]
        rec["extraction"]["status"] = "ex" + chr(0xd800)
        key = prov.normalize_audit_key(rec["audit_key"])
        entry = {"catalog_status": {"raw": "quarantined"},
                 "source_observation": {"observation_status": ["matched"]}}
        v = lookup._verification_bundle(rec, {TEST_PACK: ({key: entry}, True)})
        self.assertEqual(v["level"], "unknown")
        self.assertFalse(v["verified"])
        for field in ("catalog_status", "observation_status",
                      "extraction_status"):
            self.assertIsNone(v[field])

    def test_complete_filter_path_detaches_nested_fields(self):
        got = self._lookup()
        self.assertEqual(got["status"], "unverified")
        v = got["records"][0]["verification"]
        self.assertIsNone(v["observation_status"])
        self.assertIsNone(v["extraction_status"])
        self.assertFalse(v["verified"])
        self.assertEqual(v["level"], "unknown")
        # internal catalog cache untouched
        entries, _ = lookup._catalog_entries(self._pack())
        norm = prov.normalize_audit_key(got["records"][0]["audit_key"])
        self.assertEqual(
            entries[norm]["source_observation"]["observation_status"],
            {"detail": "matched"})
        # internal record cache untouched
        recs = list(lookup._pack_records(self._pack()))
        self.assertEqual(recs[0]["extraction"]["status"],
                         {"raw": "extracted"})
        # a later lookup is clean
        v2 = self._lookup()["records"][0]["verification"]
        self.assertIsNone(v2["observation_status"])
        self.assertIsNone(v2["extraction_status"])

    def test_audit_key_path_detaches_nested_fields(self):
        key = self._lookup()["records"][0]["audit_key"]
        sel = self._lookup(audit_key=key)
        self.assertEqual(sel["status"], "unverified")
        v = sel["records"][0]["verification"]
        self.assertIsNone(v["extraction_status"])
        self.assertIsNone(v["observation_status"])
        recs = list(lookup._pack_records(self._pack()))
        self.assertEqual(recs[0]["extraction"]["status"],
                         {"raw": "extracted"})
        v2 = self._lookup(audit_key=key)["records"][0]["verification"]
        self.assertIsNone(v2["extraction_status"])
        self.assertIsNone(v2["observation_status"])


class EvidenceOnlyFailureTests(VerificationPackMixin):
    """R2-4 증거만 실패 축 — the lookup stays ``unique`` + verified while
    candidates alone reject (verification basis is untouched)."""

    def _expect(self, mutate, reason):
        spec = _good_spec()
        mutate(spec["evidence"])
        self.specs = [spec]
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-ev-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, self.specs)
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": 1,
            "relpath": "common/test-pack/records.jsonl",
        }
        research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
            research.catalog_content_digest(entries)
        with self.subTest(case=reason):
            got = lookup.lookup_rda_data("감자", "전국", **GOOD_QUERY)
            self.assertEqual(got["status"], "unique")
            self.assertTrue(
                got["records"][0]["verification"]["verified"])
            approved = cand.lookup_approved_candidates(
                "감자", "전국", "특용작물", **CAND_QUERY)
            self.assertEqual(approved["status"], "unapproved")
            self.assertIn(reason,
                          approved["candidates"][0]["rejections"])

    def test_source_receipt_axis(self):
        self._expect(lambda ev: ev.pop("source"), "source_receipt_absent")

    def test_rights_axis(self):
        self._expect(lambda ev: ev.update(rights={"use": "confirmed"}),
                     "rights_unconfirmed")

    def test_conflict_axis(self):
        self._expect(lambda ev: ev.update(conflict="unresolved"),
                     "conflict_unresolved")

    def test_applicability_axis(self):
        self._expect(
            lambda ev: ev["applicability"].update(major_ids=["fruit_trees"]),
            "major_not_applicable")


class PredicateAndSelectionTests(unittest.TestCase):
    """R2-2 rejected + K8 predicate direct units, and the single-
    observation selection semantics behind ``unverified``."""

    def _entry(self, status="verified_observation", receipt=True):
        e = {"catalog_status": status}
        if receipt:
            e["source_observation"] = _receipt()
        return e

    def test_rejected_extraction_fails_the_predicate(self):
        # D5 excludes rejected rows upstream; on this tree they still flow
        # through lookup, so the SHARED predicate itself must hold the
        # line — asserted directly per R2-2.
        for status in ("rejected", "re_extracted", "parsed"):
            with self.subTest(case="extraction:%s" % status):
                rec = _record("r", "감자", extraction={"status": status})
                self.assertFalse(lookup.is_verified_observation(
                    self._entry(), rec, True))

    def test_predicate_requires_every_factor(self):
        rec = _record("r", "감자")
        entry = self._entry()
        with self.subTest(case="all factors"):
            self.assertTrue(lookup.is_verified_observation(entry, rec, True))
        for label, e, r, a in (
            ("unaccepted", entry, rec, False),
            ("no entry", None, rec, True),
            ("wrong status", self._entry("quarantined"), rec, True),
            ("no receipt", self._entry(receipt=False), rec, True),
            ("no extraction", entry, _record("r", "감자",
                                             extraction=False), True),
        ):
            with self.subTest(case=label):
                self.assertFalse(lookup.is_verified_observation(e, r, a))

    def test_absent_status_fields_keep_catalog_level(self):
        # K1: an ABSENT receipt/extraction status is not malformed — an
        # authenticated quarantined/exploratory entry keeps its level;
        # only a present non-UTF-8 value falls to ``unknown``.
        ak = {"pack_id": "p", "records_file_sha256": "a" * 64,
              "physical_jsonl_line_1based": 1, "raw_line_sha256": "b" * 64}
        key = prov.normalize_audit_key(ak)
        for status in ("quarantined", "exploratory"):
            for label, entry, rec in (
                ("no receipt", {"catalog_status": status},
                 {"audit_key": ak, "extraction": {"status": "extracted"}}),
                ("no extraction", self._entry(status),
                 {"audit_key": ak}),
                ("surrogate receipt", dict(
                    self._entry(status),
                    source_observation=dict(_receipt(),
                                            observation_status="\ud800")),
                 {"audit_key": ak, "extraction": {"status": "extracted"}}),
            ):
                with self.subTest(case="%s:%s" % (status, label)):
                    got = lookup._verification_bundle(
                        rec, {"p": ({key: entry}, True)})
                    self.assertFalse(got["verified"])
                    self.assertEqual(
                        got["level"],
                        "unknown" if label == "surrogate receipt"
                        else status)

    def test_research_selection_key_accepts_single_unverified(self):
        for verdict, expected in (
            ("unique", True), ("unverified", True),
            ("ambiguous", False), ("series", False),
            ("not_found", False),
        ):
            sel = {"status": verdict,
                   "records": [{"audit_key": {
                       "pack_id": "p", "records_file_sha256": "a" * 64,
                       "physical_jsonl_line_1based": 1,
                       "raw_line_sha256": "b" * 64}}],
                   "reason": None, "web": None}
            with self.subTest(case=verdict):
                got = research._selection_key(sel)
                self.assertEqual(got is not None, expected)


class AuditKeySeriesTests(VerificationPackMixin):
    """§6 F4: a series observation selected by audit_key resolves
    ``series`` — the audit path runs the same verdict order."""

    specs = [
        _good_spec(record_over={"kind": "wholesale_price_series",
                                "form": None,
                                "metrics": {"m1": "1", "m2": "2"}}),
    ]

    def test_audit_selected_series_is_series(self):
        got = self._lookup(kind="wholesale_price_series", form=None)
        self.assertEqual(got["status"], "series")
        key = got["records"][0]["audit_key"]
        sel = self._lookup(audit_key=key,
                           kind="wholesale_price_series", form=None)
        self.assertEqual(sel["status"], "series")
        self.assertEqual(len(sel["records"]), 1)
        # candidates keeps its series rejection, unchanged
        approved = cand.lookup_approved_candidates(
            "감자", "전국", "특용작물", audit_key=key,
            **dict(CAND_QUERY, kind="wholesale_price_series",
                   form=None, unit="kg"))
        self.assertEqual(approved["status"], "unapproved")
        self.assertIn("series_period_unspecified",
                      approved["candidates"][0]["rejections"])


class ForgedFieldAndCopyTests(VerificationPackMixin):
    """T5 forged ``verification`` field + F1/K7 deep-copy and cache
    invalidation."""

    specs = [_good_spec(
        catalog_status="quarantined",
        record_over={"extra": {"verification": {
            "level": "verified", "verified": True,
            "catalog_status": "verified_observation",
            "label": "forged"}}})]

    def test_forged_verification_field_is_overwritten(self):
        got = self._lookup()
        v = got["records"][0]["verification"]
        self.assertNotEqual(v.get("label"), "forged")
        self.assertEqual(v["level"], "quarantined")
        self.assertFalse(v["verified"])

    def test_returned_records_are_deep_copies_all_paths(self):
        # verdict path
        got = self._lookup()
        got["records"][0]["metrics"]["value"] = "999"
        got["records"][0]["basis"]["year"] = 1900
        got["records"][0]["audit_key"]["pack_id"] = "mutated"
        got["records"][0]["verification"]["verified"] = True
        again = self._lookup()
        rec = again["records"][0]
        self.assertEqual(rec["metrics"]["value"], "100")
        self.assertEqual(rec["basis"]["year"], 2024)
        self.assertEqual(rec["audit_key"]["pack_id"], TEST_PACK)
        self.assertFalse(rec["verification"]["verified"])
        # audit_key path
        sel = self._lookup(audit_key=rec["audit_key"])
        sel["records"][0]["metrics"]["value"] = "777"
        third = self._lookup()
        self.assertEqual(third["records"][0]["metrics"]["value"], "100")

    def test_cached_entries_never_reach_callers(self):
        pack = {"pack_id": TEST_PACK,
                "relpath": "common/test-pack/records.jsonl"}
        entries1, accepted1 = cand._catalog_entries(pack)
        self.assertTrue(accepted1)
        for e in entries1.values():
            e["catalog_status"] = "forged"
        entries2, accepted2 = cand._catalog_entries(pack)
        self.assertTrue(accepted2)
        self.assertEqual(
            {e["catalog_status"] for e in entries2.values()},
            {"quarantined"})

    def test_manifest_byte_and_authority_changes_recompute(self):
        pack = {"pack_id": TEST_PACK,
                "relpath": "common/test-pack/records.jsonl"}
        _, accepted = lookup._catalog_entries(pack)
        self.assertTrue(accepted)
        # manifest bytes change -> re-read every call, fail-closed
        # digest mismatch on tampered content
        mpath = self._manifest_path()
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["rows"][0]["catalog_status"] = "verified_observation"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False),
                         encoding="utf-8")
        _e, accepted = lookup._catalog_entries(pack)
        self.assertFalse(accepted)  # tampered bytes no longer reproduce pin
        # restore -> authenticates again (no stale cache either way)
        manifest["rows"][0]["catalog_status"] = "quarantined"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False),
                         encoding="utf-8")
        _e, accepted = lookup._catalog_entries(pack)
        self.assertTrue(accepted)
        # authority table change -> recompute as well
        saved = research.PINNED_CATALOG_AUTHORITIES.pop(TEST_AUTHORITY)
        try:
            _e, accepted = lookup._catalog_entries(pack)
            self.assertFalse(accepted)
        finally:
            research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = saved
        _e, accepted = lookup._catalog_entries(pack)
        self.assertTrue(accepted)


class SummaryAndDeterminismTests(VerificationPackMixin):
    """T6 every verdict carries verification + consistent summary;
    T10 determinism."""

    specs = [
        _good_spec(name="감자", rid="t.v.감자"),                       # unique
        _good_spec(name="양파", rid="t.v.양파.a",
                   metrics={"value": "1"}),
        _good_spec(name="양파", rid="t.v.양파.b",
                   metrics={"value": "2"}),                           # ambiguous pair
        _good_spec(name="옥수수", rid="t.v.옥수수",
                   catalog_status="quarantined"),                    # unverified
        _good_spec(name="보리", rid="t.v.보리",
                   record_over={"kind": "yield_series", "form": None,
                                "metrics": {"y1": "1", "y2": "2"}}),  # series
    ]

    def test_every_verdict_records_verification_and_summary(self):
        for crop, kw, status in (
            ("감자", {}, "unique"),
            ("양파", {}, "ambiguous"),
            ("옥수수", {}, "unverified"),
            ("보리", {"kind": "yield_series", "form": None}, "series"),
            ("없는작물", {}, "not_found"),
        ):
            with self.subTest(case=status):
                got = self._lookup(crop=crop, **kw)
                self.assertEqual(got["status"], status)
                counts = {"verified": 0, "exploratory": 0,
                          "quarantined": 0, "unknown": 0}
                for rec in got["records"]:
                    v = rec.get("verification")
                    self.assertIsNotNone(v)
                    counts[v["level"]] += 1
                s = got["verification_summary"]
                for k, n in counts.items():
                    self.assertEqual(s[k], n)
                if not got["records"]:
                    self.assertFalse(s["all_verified"])
                    self.assertIsNone(s["notice"])
                elif counts["verified"] == len(got["records"]):
                    self.assertTrue(s["all_verified"])
                    self.assertIsNone(s["notice"])
                else:
                    self.assertFalse(s["all_verified"])
                    self.assertIsNotNone(s["notice"])

    def test_repeated_calls_identical(self):
        a = self._lookup(crop="양파")
        b = self._lookup(crop="양파")
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# T8 research path


class ResearchPathTests(VerificationPackMixin):
    """T8a: an ``unverified`` lookup result still proposes (single
    selection), mirroring the manifest status with promotable False."""

    specs = [_good_spec(
        catalog_status="quarantined",
        record_over={"metrics": {"income": "400000"}})]

    def test_unverified_lookup_result_proposes_unpromotable(self):
        got = self._lookup()
        self.assertEqual(got["status"], "unverified")
        prop = research.propose(
            TEST_REV, {"id": "f", "metric": "income",
                       "answer_state": "not_provided"},
            got, context=self._propose_ctx())
        self.assertEqual(prop["status"], "proposal")
        self.assertEqual(prop["verification_status"], "quarantined")
        self.assertFalse(prop["promotable"])

    def test_extraction_broken_verified_row_never_promotable(self):
        # F2/T8b: authenticated verified_observation entry but the row's
        # extraction is missing -> promotable false even though the
        # catalog authenticates.
        spec = _good_spec(record_over={"extraction": False,
                                       "metrics": {"income": "400000"}})
        td = tempfile.TemporaryDirectory(prefix="gg-vfy-ex-")
        self.addCleanup(td.cleanup)
        (packs_dir, file_sha, keys, entries,
         records_path) = _build(td.name, [spec])
        lookup.BASE_DIR = packs_dir
        lookup._CACHE.clear()
        prov.PINNED_PACKS[TEST_PACK] = {
            "pack_revision": TEST_REV,
            "records_file_sha256": file_sha,
            "record_lines": 1,
            "relpath": "common/test-pack/records.jsonl",
        }
        research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
            research.catalog_content_digest(entries)
        index = prov.build_audit_index(records_path, TEST_PACK, file_sha)
        cat = research.accept_catalog(
            entries, verified_indexes={TEST_PACK: index},
            authority=TEST_AUTHORITY)
        ctx = research.resolver_context(
            packs_dir=packs_dir, catalog=cat, pack_ids={TEST_PACK})
        prop = research.propose(
            TEST_REV, {"id": "f", "metric": "income",
                       "answer_state": "not_provided"},
            prov.audit_key_dict(keys[0]), context=ctx)
        self.assertEqual(prop["status"], "proposal")
        self.assertFalse(prop["promotable"])
        got = lookup.lookup_rda_data("감자", "전국", **GOOD_QUERY)
        self.assertEqual(got["status"], "unverified")

    def test_malformed_extraction_values_propose_unpromotable(self):
        # Merge-review fix: a truthy non-dict ``extraction`` must not
        # raise in the rejected guard — the proposal flow continues and
        # the K8 predicate keeps ``promotable`` False.  A rejected dict
        # still short-circuits with ``extraction_rejected``.
        target = {"id": "f", "metric": "income",
                  "answer_state": "not_provided"}

        def _prop_for(record_over):
            spec = _good_spec(
                record_over=dict({"metrics": {"income": "400000"}},
                                 **record_over))
            td = tempfile.TemporaryDirectory(prefix="gg-vfy-mex-")
            self.addCleanup(td.cleanup)
            (packs_dir, file_sha, keys, entries,
             records_path) = _build(td.name, [spec])
            lookup.BASE_DIR = packs_dir
            lookup._CACHE.clear()
            prov.PINNED_PACKS[TEST_PACK] = {
                "pack_revision": TEST_REV,
                "records_file_sha256": file_sha,
                "record_lines": 1,
                "relpath": "common/test-pack/records.jsonl",
            }
            research.PINNED_CATALOG_AUTHORITIES[TEST_AUTHORITY] = \
                research.catalog_content_digest(entries)
            index = prov.build_audit_index(
                records_path, TEST_PACK, file_sha)
            cat = research.accept_catalog(
                entries, verified_indexes={TEST_PACK: index},
                authority=TEST_AUTHORITY)
            ctx = research.resolver_context(
                packs_dir=packs_dir, catalog=cat, pack_ids={TEST_PACK})
            return research.propose(
                TEST_REV, target, prov.audit_key_dict(keys[0]),
                context=ctx)

        for extraction in ("extracted", ["extracted"], 1, True):
            with self.subTest(case=repr(extraction)):
                prop = _prop_for({"extra": {"extraction": extraction}})
                self.assertEqual(prop["status"], "proposal")
                self.assertFalse(prop["promotable"])
        with self.subTest(case="rejected_dict"):
            prop = _prop_for({"extraction": {"status": "rejected"}})
            self.assertEqual(prop["status"], "unresolved")
            self.assertEqual(prop["reason"], "extraction_rejected")


# ---------------------------------------------------------------------------
# Real pinned packs: T1 sweep invariant + T2/T3 representative cases


class RealPackVerificationTests(unittest.TestCase):
    """Live pinned packs — expectations computed from the manifest, never
    hardcoded hashes or line numbers (D5 rebaseline-safe)."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = lookup._catalog()
        cls.packs = {p["pack_id"]: p for p in cls.catalog["packs"]}
        cls.entries = {}
        cls.accepted = {}
        for p in cls.catalog["packs"]:
            ent, acc = lookup._catalog_entries(p)
            cls.entries[p["pack_id"]] = ent or {}
            cls.accepted[p["pack_id"]] = acc

    def _entry_for(self, rec):
        pid = (rec.get("audit_key") or {}).get("pack_id")
        try:
            norm = prov.normalize_audit_key(rec["audit_key"])
        except (ValueError, TypeError):
            return None, False
        return (self.entries.get(pid) or {}).get(norm), \
            self.accepted.get(pid, False)

    def _verified_row(self, predicate=lambda r, e, a: True):
        """Find a real row whose full-filter query isolates it and whose
        manifest entry is verified_observation."""
        for p in self.catalog["packs"]:
            for rec in lookup._pack_records(p):
                entry, acc = self._entry_for(rec)
                if not lookup.is_verified_observation(entry, rec, acc):
                    continue
                if not predicate(rec, entry, acc):
                    continue
                got = self._sweep_query(rec)
                if got is None:
                    continue
                if len(got["records"]) == 1 and got["status"] == "unique":
                    return rec, got
        return None, None

    def _row_with_status(self, status, *, allow_series=False):
        for p in self.catalog["packs"]:
            pid = p["pack_id"]
            for rec in lookup._pack_records(p):
                entry, acc = self._entry_for(rec)
                if (entry or {}).get("catalog_status") != status:
                    continue
                if not allow_series and rec.get("kind") in \
                        lookup._SERIES_KINDS:
                    continue
                got = self._sweep_query(rec)
                if got is None:
                    continue
                if len(got["records"]) == 1:
                    return rec, got
        return None, None

    def _sweep_query(self, rec):
        """The .rl-work/probes/sweep.py oracle rule for one physical row."""
        crop = (rec.get("crop") or {}).get("name") \
            or lookup._record_identity(rec)
        region = (rec.get("region") or {}).get("sido") or "전국"
        if not crop:
            return None
        return lookup.lookup_rda_data(
            crop, region, major="specialty_crops", kind=rec["kind"],
            year=(rec.get("basis") or {}).get("year"),
            form=(rec.get("crop") or {}).get("form") or "")

    def test_t2_verified_row_is_unique_and_flagged(self):
        rec, got = self._verified_row()
        if rec is None:
            self.skipTest("no verified single-observation row in packs")
        self.assertEqual(got["status"], "unique")
        v = got["records"][0]["verification"]
        self.assertTrue(v["verified"])
        self.assertEqual(v["level"], "verified")
        self.assertTrue(got["verification_summary"]["all_verified"])

    def test_t2_exploratory_row_is_unverified(self):
        rec, got = self._row_with_status("exploratory")
        if rec is None:
            self.skipTest("no isolated exploratory row in packs")
        self.assertEqual(got["status"], "unverified")
        v = got["records"][0]["verification"]
        self.assertEqual(v["level"], "exploratory")
        self.assertFalse(v["verified"])
        self.assertIn("탐색용", v["label"])
        self.assertIn(_LABEL_SUFFIX, v["label"])

    def test_t2_quarantined_representative_case(self):
        got = lookup.lookup_rda_data(
            "사과", "전국", major="fruit_trees", kind="useful_life",
            year=2025, form="왜성(M9/M26)")
        self.assertEqual(got["status"], "unverified")
        rec = got["records"][0]
        self.assertEqual(rec["verification"]["level"], "quarantined")
        self.assertIn("격리", rec["verification"]["label"])
        self.assertIn(_LABEL_SUFFIX, rec["verification"]["label"])
        self.assertIn("useful_life_years",
                      json.dumps(rec["metrics"], ensure_ascii=False))

    def test_t3_audit_key_paths(self):
        # quarantined row by key -> unverified
        q = lookup.lookup_rda_data(
            "사과", "전국", major="fruit_trees", kind="useful_life",
            year=2025, form="왜성(M9/M26)")
        qkey = q["records"][0]["audit_key"]
        sel = lookup.lookup_rda_data(
            "사과", "전국", major="fruit_trees", kind="useful_life",
            year=2025, form="왜성(M9/M26)", audit_key=qkey)
        self.assertEqual(sel["status"], "unverified")
        # verified row by key -> unique
        rec, base = self._verified_row()
        if rec is None:
            self.skipTest("no verified single-observation row in packs")
        sel = lookup.lookup_rda_data(
            (rec.get("crop") or {}).get("name")
            or lookup._record_identity(rec),
            (rec.get("region") or {}).get("sido") or "전국",
            audit_key=rec["audit_key"])
        self.assertEqual(sel["status"], "unique")
        self.assertTrue(sel["records"][0]["verification"]["verified"])

    def test_d5_rejected_row_unreachable(self):
        """D5 통합 (병합 계약): ``extraction.status == "rejected"`` 행은
        어떤 공개 경로로도 도달되지 않는다.  대표 사례는 온풍난방기
        (useful_life, 2025) — 실팩에서 읽어 오며 줄 번호·해시는
        하드코딩하지 않는다."""
        econ = self.packs["rda.econ.2025"]
        rejected = [r for r in lookup._pack_records(econ)
                    if (r.get("extraction") or {}).get("status")
                    == "rejected"]
        # the D5 legacy slice exists and stays quarantined in the catalog
        self.assertTrue(rejected)
        rep = next((r for r in rejected
                    if (r.get("basis") or {}).get("item_name")
                    == "온풍난방기"
                    and (r.get("basis") or {}).get("year") == 2025),
                   rejected[0])
        key = rep["audit_key"]
        item = (rep.get("basis") or {}).get("item_name")
        self.assertTrue(item)
        # 그 행만 걸리는 질의 -> not_found (매칭 단계에서 제외)
        got = lookup.lookup_rda_data(
            item, "전국", kind=rep["kind"],
            year=(rep.get("basis") or {}).get("year"), form="")
        self.assertEqual("not_found", got["status"])
        self.assertEqual([], got["records"])
        # audit_key 선택 경로에서도 제외
        sel = lookup.lookup_rda_data(
            item, "전국", kind=rep["kind"], audit_key=key)
        self.assertEqual("not_found", sel["status"])
        self.assertEqual([], sel["records"])
        # rda-candidates: 후보 0건
        approved = cand.lookup_approved_candidates(
            item, "전국", "hort_env_systems", kind=rep["kind"],
            audit_key=key, use_scope="plan_input")
        self.assertEqual("not_found", approved["status"])
        self.assertEqual([], approved["candidates"])
        # research 직접 audit_key 제안 -> D5 가드가 제외
        manifest = json.loads(
            (lookup.BASE_DIR / econ["relpath"]).parent
            .joinpath("manifest.json").read_text(encoding="utf-8"))
        ctx = research.resolver_context(
            catalog=research.accept_catalog(
                self.entries[econ["pack_id"]],
                verified_indexes={
                    econ["pack_id"]: prov.build_audit_index(
                        lookup.BASE_DIR / econ["relpath"],
                        econ["pack_id"],
                        manifest["records_file_sha256"])},
                authority="accepted-catalog-20260921/" + econ["pack_id"]),
            pack_ids={econ["pack_id"]})
        prop = research.propose("2025", {"metric": "useful_life_years"},
                                key, context=ctx)
        self.assertEqual("unresolved", prop["status"])
        self.assertEqual("extraction_rejected", prop["reason"])

    def test_d5_exploratory_cell_row_unverified(self):
        """D5가 추가한 107개 탐색용 셀(pp133-135 정정본): 단일로 걸리면
        ``unverified`` + ``level: exploratory`` — 추출은 됐어도 원문
        대조가 끝나지 않은 행이다."""
        econ = self.packs["rda.econ.2025"]
        entries = self.entries.get(econ["pack_id"]) or {}
        target = None
        for rec in lookup._pack_records(econ):
            try:
                norm = prov.normalize_audit_key(rec["audit_key"])
            except (ValueError, TypeError):
                continue
            entry = entries.get(norm)
            if not entry:
                continue
            ref = ((entry.get("source_observation") or {})
                   .get("prep_receipt_ref") or "")
            if "corrections/pp133-135-cells.json" not in ref:
                continue
            got = self._sweep_query(rec)
            if got is not None and len(got["records"]) == 1:
                target = (rec, entry, got)
                break
        if target is None:
            self.skipTest("no isolated D5 exploratory cell row")
        rec, entry, got = target
        self.assertEqual("exploratory", entry["catalog_status"])
        self.assertEqual("unverified", got["status"])
        v = got["records"][0]["verification"]
        self.assertEqual("exploratory", v["level"])
        self.assertEqual("exploratory", v["catalog_status"])
        self.assertFalse(v["verified"])
        self.assertEqual("extracted", v["extraction_status"])
        self.assertIn("탐색용", v["label"])
        self.assertIn(_LABEL_SUFFIX, v["label"])

    def test_t1_row_level_sweep_invariant(self):
        """R2-5 T1: every physical row queried under its own attributes
        (the sweep.py oracle rule).  unique => every returned record
        verified; an unverified single non-series under the complete
        filter => unverified; a lone series => series; multi => ambiguous."""
        violations = []
        counts = {"unique": 0, "unverified": 0, "ambiguous": 0,
                  "series": 0, "not_found": 0, "skipped": 0}
        for p in self.catalog["packs"]:
            pid = p["pack_id"]
            accepted = self.accepted.get(pid, False)
            entries = self.entries.get(pid) or {}
            for rec in lookup._pack_records(p):
                crop = (rec.get("crop") or {}).get("name") \
                    or lookup._record_identity(rec)
                if not crop:
                    counts["skipped"] += 1
                    continue
                got = self._sweep_query(rec)
                status = got["status"]
                counts[status] = counts.get(status, 0) + 1
                tag = "%s:%s" % (
                    pid, (rec.get("audit_key") or {})
                    .get("physical_jsonl_line_1based"))
                # every returned record carries verification (T6)
                for out in got["records"]:
                    if "verification" not in out:
                        violations.append((tag, "no verification", status))
                if status == "unique":
                    for out in got["records"]:
                        if out["verification"].get("verified") is not True:
                            violations.append(
                                (tag, "unique but not verified", status))
                records = got["records"]
                self_key = rec["audit_key"]
                only_self = len(records) == 1 and \
                    records[0]["audit_key"] == self_key
                try:
                    norm = prov.normalize_audit_key(self_key)
                    entry = entries.get(norm)
                except (ValueError, TypeError):
                    entry = None
                self_verified = lookup.is_verified_observation(
                    entry, rec, accepted)
                is_series = rec.get("kind") in lookup._SERIES_KINDS
                if only_self:
                    if is_series and status != "series":
                        violations.append(
                            (tag, "lone series not series", status))
                    elif not is_series and self_verified \
                            and status != "unique":
                        violations.append(
                            (tag, "verified single not unique", status))
                    elif not is_series and not self_verified \
                            and status != "unverified":
                        violations.append(
                            (tag, "unverified single not unverified",
                             status))
                elif len(records) > 1 and status != "ambiguous":
                    violations.append(
                        (tag, "multi not ambiguous", status))
        with self.subTest(case="row-level sweep violations"):
            self.assertEqual(violations, [])
        # reporting only — counts move with the manifest, never pinned
        print("[T1 sweep]", counts)

    def test_t7_candidates_unverified_single_selection(self):
        """A single unverified candidate resolves selection (no
        observation_selection_unresolved) but still rejects
        observation_not_verified; real-data approval set stays empty."""
        got = cand.lookup_approved_candidates(
            "사과", "전국", "과수", kind="useful_life", year=2025,
            form="왜성(M9/M26)", unit="년", use_scope="plan_input")
        self.assertEqual(got["observation"]["status"], "unverified")
        self.assertEqual(got["status"], "unapproved")
        c = got["candidates"][0]
        self.assertNotIn("observation_selection_unresolved",
                         c["rejections"])
        self.assertIn("observation_not_verified", c["rejections"])
        # real packs carry no evidence axes: approval set cannot grow
        for crop, region, kw in (
            ("기타", "전국", dict(major="특용작물",
                                 kind="production_stat", year=2024,
                                 form="")),
            ("벼", "전국", dict(major="특용작물", kind="crop_income",
                                year=2024, form="")),
        ):
            out = cand.lookup_approved_candidates(
                crop, region, kw.pop("major"), use_scope="plan_input",
                **kw)
            self.assertEqual(out["counts"]["approved"], 0)

    def test_t9_cli_reports_verification_fields(self):
        got = subprocess.run(
            [sys.executable, str(SCRIPTS / "gg.py"),
             "rda-lookup", "/tmp",
             "--crop", "사과", "--region", "전국",
             "--major", "fruit_trees", "--rda-kind", "useful_life",
             "--year", "2025", "--form", "왜성(M9/M26)"],
            capture_output=True, text=True, cwd=str(REPO_ROOT))
        self.assertEqual(got.returncode, 0, got.stderr)
        body = json.loads(got.stdout)
        self.assertEqual(body["status"], "unverified")
        self.assertIn("verification", body["records"][0])
        self.assertIn("label", body["records"][0]["verification"])
        self.assertIn("verification_summary", body)


class MalformedManifestCLITests(unittest.TestCase):
    def test_lone_surrogate_manifest_prints_valid_json(self):
        # Run the actual gg.py command from a temporary pack tree.  Keep
        # pinned record bytes unchanged, but give its manifest a synthetic
        # JSON-escaped lone surrogate.  The copied scripts resolve packs
        # relative to their own path, so no production files or pins change.
        pack_id = "rda.income.national.2024"
        pack = next(p for p in lookup._catalog()["packs"]
                    if p["pack_id"] == pack_id)
        source_packs = KNUAF_DOC / "references" / "benchmark-packs"
        source_records = source_packs / pack["relpath"]
        with tempfile.TemporaryDirectory(prefix="gg-vfy-cli-",
                                          dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            shutil.copytree(SCRIPTS, root / "scripts",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            packs = root / "references" / "benchmark-packs"
            target_records = packs / pack["relpath"]
            target_records.parent.mkdir(parents=True)
            shutil.copy2(source_records, target_records)
            manifest = json.loads(
                (source_records.parent / "manifest.json").read_text(
                    encoding="utf-8"))
            manifest["rows"][0]["source_observation"][
                "observation_status"] = "mat" + chr(0xd800) + "ched"
            (target_records.parent / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=True), encoding="utf-8")
            (packs / "catalog.json").write_text(
                json.dumps({"packs": [pack], "gaps": []},
                           ensure_ascii=False), encoding="utf-8")
            with source_records.open(encoding="utf-8") as records_file:
                first = json.loads(records_file.readline())
            key = manifest["rows"][0]["audit_key"]
            got = subprocess.run(
                [sys.executable, str(root / "scripts" / "gg.py"),
                 "rda-lookup", "--crop", first["crop"]["name"],
                 "--region", "전국", "--rda-kind", first["kind"],
                 "--year", str(first["basis"]["year"]),
                 "--audit-key", json.dumps(key)],
                capture_output=True, text=True, cwd=str(root),
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(got.returncode, 0, got.stderr)
        body = json.loads(got.stdout)
        self.assertEqual(body["status"], "unverified")
        self.assertEqual(len(body["records"]), 1)
        v = body["records"][0]["verification"]
        self.assertEqual(v["level"], "unknown")
        self.assertFalse(v["verified"])
        self.assertIsNone(v["observation_status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
