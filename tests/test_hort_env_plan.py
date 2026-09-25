"""원예환경시스템전공 읽기 전용 계획 CLI 시험 (설계 §9 + §13 + §14).

합성 fixture만으로 바인딩·질문 상태·workbook 판정·profile 무결성·개인정보
검사·CLI 계약을 고정한다. 실제 profile.json 로드·개인정보 사례는 파일이
있을 때만 의미가 있으므로 부재 시 skip하지 않고 실패로 남긴다.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from tests._harness import ContractCase, SCRIPTS, runtime


MAJOR = "hort_env_systems"
COLLECTIONS = ("sources", "facts", "sections", "questions", "tasks",
               "reviews", "rules", "approvals", "outputs")
SENTINEL_VALUE = "합성값-A1B2C3D4"
SENTINEL_UNIT = "합성단위XYZ"
SENTINEL_LOCATOR = "합성 원자료 위치 표시"

WB_BYTES = b"PK\x03\x04 hort-env synthetic workbook bytes"
WB_SHA = hashlib.sha256(WB_BYTES).hexdigest()
NON_WB_SHA = "f" * 64

SYNTH_SHEETS = ["%d. 합성시트%02d" % (i, i) for i in range(1, 19)]
# 실제 profile처럼 앞 공백이 있는 시트명 — strip 비교 불일치 사례용
SYNTH_SHEETS[6] = " 7. 합성시트07"

SYNTH_CAUTIONS = [
    {
        "id": "X-C1",
        "status": "confirmed_inconsistency",
        "kind": "year_label_offset",
        "locations": [
            {"sheet": "8. 합성시트08", "cells": ["B2", "D3:D8"]},
            {"sheet": " 7. 합성시트07", "cells": ["$E$4"]},
        ],
        "guidance": "연도 라벨이 한 해 앞서 표시된다.",
    },
    {
        "id": "X-N1",
        "status": "needs_confirmation",
        "kind": "axis_mismatch",
        "locations": [{"sheet": "15. 합성시트15", "cells": ["A1"]}],
        "guidance": "연도 축 정의를 확인해야 한다.",
    },
]


def _synth_precedent(ref, stage, pages, sha, axes):
    return {
        "ref": ref,
        "title": "합성 %s 선행 사례" % ref,
        "author": "합성 작성자",
        "sha256": sha,
        "physical_pages": pages,
        "file_stage": stage,
        "sections": [
            {"item": "preface", "title": "머리말", "toc_page": 1,
             "physical_page": 5, "printed_page": 3, "note": None},
            {"item": "closing", "title": "맺음말", "toc_page": None,
             "physical_page": None, "printed_page": None,
             "note": "합성 비고"},
        ],
        "appendices": [
            {"no": i, "title": "합성 부록 %d" % i, "toc_page": None,
             "physical_start": None, "physical_end": None,
             "printed_start": None, "note": None}
            for i in range(1, 19)
        ],
        "appendix8_axes": list(axes),
    }


def synth_profile(workbook_sha=WB_SHA):
    sheets = list(SYNTH_SHEETS)
    return {
        "schema": "knuaf-hort-env-profile/v1",
        "major_id": "hort_env_systems",
        "module_version": "0.1.0",
        "notice": "합성 시험용 profile — 모든 값이 합성이다.",
        "precedents": [
            _synth_precedent("HT1", "draft_1_by_filename", 200,
                             "a" * 64, ["crop_cycle", "fiscal_year"]),
            _synth_precedent("HT2", "unknown", 170,
                             "b" * 64, ["fiscal_year"]),
        ],
        "workbook_reference": {
            "ref": "H-X1",
            "sha256": workbook_sha,
            "role": "professor_reference",
            "authority": "user_statement",
            "statement_date": "2026-09-25",
            "finance_status": "unsupported",
            "sheets": sheets,
            "appendix_map": [
                {"no": i, "sheet": sheets[i - 1]} for i in range(1, 19)
            ],
            "cautions": copy.deepcopy(SYNTH_CAUTIONS),
        },
        "evidence_sources": [
            {"id": "OF-G1", "title": "합성 공식 근거", "publisher": "합성 발행처",
             "edition": "합성 고시 제0000-0호", "effective": "2025-01-01",
             "sha256": None, "acquisition": "link_only",
             "use_scope": "disaster_design_standard",
             "caveats": ["합성 주의문."]}
        ],
        "prohibited_rows": [
            {"pack_id": "synthetic.pack", "kind": "useful_life",
             "locator_pdf_pages": [1], "reason": "합성 금지 행."}
        ],
    }


def _fact(fid, field_id, state, value=None, period=None, scope="farm",
          reason=None, verification="unreviewed", source_id="s1", **extra):
    refs = []
    if state != "not_provided":
        refs = [{"id": source_id, "locator": SENTINEL_LOCATOR,
                 "revision": 1}]
    fact = {
        "id": fid, "field_id": field_id, "kind": "reported_fact",
        "value": value, "unit": None, "value_type": "text",
        "period": period, "scope": scope, "answer_state": state,
        "verification": verification, "source_refs": refs, "revision": 1,
    }
    if state in {"explicit_none", "withheld", "not_applicable"}:
        fact["reason"] = reason or "합성 사유"
    fact.update(extra)
    return fact


def _source(sid, **fields):
    src = {"id": sid, "path": "answer.txt", "hash": NON_WB_SHA,
           "revision": 1}
    src.update(fields)
    return src


def _base_project():
    p = {"schema_version": 2, "revision": 3,
         "requests": {}, "views": {}, "issues": [], "history": []}
    for c in COLLECTIONS:
        p[c] = {}
    p["sources"]["s1"] = _source("s1")
    return p


def _binding_fact(major_id=MAJOR, module_version="0.1.0", fid="m0"):
    return _fact(fid, "common.major_id", "provided", value=major_id,
                 scope="project", verification="claim_supported",
                 module_version=module_version)


def _all_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_keys(v)


class _PlanCase(ContractCase):
    def setUp(self):
        self.mc = runtime("gg_major_contract")
        self.core = runtime("gg_core")
        self.hp = runtime("gg_hort_plan")
        self.module = self.mc.HORT_ENV_SYSTEMS_MODULE

    def _project(self, facts=(), sources=()):
        tmp = tempfile.TemporaryDirectory(prefix="knuaf-hort-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        p = _base_project()
        for fact in facts:
            p["facts"][fact["id"]] = fact
        for src in sources:
            p["sources"][src["id"]] = src
        (root / "project.json").write_text(
            json.dumps(p, ensure_ascii=False), encoding="utf-8"
        )
        return root

    def _profile_file(self, profile=None):
        profile = profile if profile is not None else synth_profile()
        path = Path(tempfile.mkdtemp(prefix="knuaf-hort-profile-")) / "profile.json"
        path.write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def _registry(self, *modules):
        return self.mc.ModuleRegistry(
            modules or (self.module,), pack_owner=None
        )


class BindingHeldTests(_PlanCase):
    def test_no_binding_returns_common_only_held(self):
        root = self._project()
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file()
        )
        self.assertEqual(code, 2)
        self.assertEqual(plan, self.mc.common_only_plan("major_required"))
        self.assertEqual(plan["status"], "held")
        self.assertTrue(plan["common_only"])

    def test_wrong_major_binding_is_held(self):
        other = self.mc.declare_module(
            major_id="other_major", module_version="0.1.0",
            capabilities={"question": "supported"},
        )
        root = self._project(facts=[_binding_fact("other_major")])
        plan, code = self.hp.build_plan(
            root, registry=self._registry(other, self.module),
            profile_path=self._profile_file()
        )
        self.assertEqual(code, 2)
        self.assertEqual(plan, {
            "status": "held",
            "reason": "wrong_major",
            "major_id": "other_major",
        })

    def test_module_version_mismatch_is_binding_invalid(self):
        root = self._project(
            facts=[_binding_fact(module_version="9.9.9")]
        )
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file()
        )
        self.assertEqual(code, 2)
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "binding_invalid")
        self.assertTrue(plan["common_only"])

    def test_unknown_major_is_held(self):
        root = self._project(facts=[_binding_fact("ghost_major")])
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file()
        )
        self.assertEqual(code, 2)
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "unknown_major")

    def test_profile_invalid_is_held_with_detail(self):
        root = self._project(facts=[_binding_fact()])
        bad = synth_profile()
        bad["module_version"] = "9.9.9"
        path = self._profile_file(bad)
        plan, code = self.hp.build_plan(
            root, registry=self._registry(), profile_path=path
        )
        self.assertEqual(code, 2)
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "profile_invalid")
        self.assertIn("detail", plan)
        self.assertEqual(plan["detail"]["path"], str(path))

    def test_wrong_value_types_are_held_not_raised(self):
        # Final review 1 #1: a list/dict where a sheet name belongs must
        # hold as profile_invalid (exit 2), never escape as TypeError.
        root = self._project(facts=[_binding_fact()])
        mutations = (
            lambda p: p["workbook_reference"]["sheets"].__setitem__(0, []),
            lambda p: p["workbook_reference"]["appendix_map"][0]
            .__setitem__("sheet", []),
            lambda p: p["workbook_reference"]["cautions"][0]["locations"][0]
            .__setitem__("sheet", {}),
            lambda p: p["precedents"][0].__setitem__("ref", ["HT1"]),
        )
        for i, mutate in enumerate(mutations):
            with self.subTest(case=i):
                bad = synth_profile()
                mutate(bad)
                plan, code = self.hp.build_plan(
                    root, registry=self._registry(),
                    profile_path=self._profile_file(bad),
                )
                self.assertEqual(code, 2)
                self.assertEqual(plan["status"], "held")
                self.assertEqual(plan["reason"], "profile_invalid")


class QuestionStateTests(_PlanCase):
    FIELD = MAJOR + ".crop_item"

    def _plan(self, facts):
        root = self._project(facts=[_binding_fact()] + facts)
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file()
        )
        self.assertEqual(code, 0)
        self.assertEqual(plan["status"], "ok")
        return plan

    def _entry(self, plan, field_id):
        for q in plan["questions"]:
            if q["field_id"] == field_id:
                return q
        self.fail("field not in questions: " + field_id)

    def test_six_answer_states_passthrough(self):
        states = ("not_provided", "explicit_none", "unknown",
                  "provided", "not_applicable", "withheld")
        facts = [
            _fact("f%d" % i, self.FIELD, s,
                  value="값" if s == "provided" else None)
            for i, s in enumerate(states)
        ]
        plan = self._plan(facts)
        q = self._entry(plan, self.FIELD)
        self.assertTrue(q["recorded"])
        self.assertEqual(q["fact_count"], 6)
        self.assertEqual(
            [f["answer_state"] for f in q["facts"]], list(states)
        )
        self.assertEqual(
            [f["fact_id"] for f in q["facts"]],
            ["f%d" % i for i in range(6)],
        )

    def test_zero_one_two_facts(self):
        plan = self._plan([
            _fact("g1", MAJOR + ".cultivar", "provided", value="합성 품종"),
            _fact("g2", MAJOR + ".business_type", "provided",
                  value="합성 형태", period="2026"),
            _fact("g3", MAJOR + ".business_type", "unknown",
                  period="2027", scope="field"),
        ])
        empty = self._entry(plan, MAJOR + ".crop_item")
        self.assertFalse(empty["recorded"])
        self.assertEqual(empty["fact_count"], 0)
        self.assertEqual(empty["facts"], [])
        one = self._entry(plan, MAJOR + ".cultivar")
        self.assertTrue(one["recorded"])
        self.assertEqual(one["fact_count"], 1)
        two = self._entry(plan, MAJOR + ".business_type")
        self.assertEqual(two["fact_count"], 2)
        # 기간·범위가 다른 2건 — 충돌 표시 없이 각자 보존
        self.assertEqual(
            [(f["period"], f["scope"]) for f in two["facts"]],
            [("2026", "farm"), ("2027", "field")],
        )
        self.assertEqual(
            {f["answer_state"] for f in two["facts"]},
            {"provided", "unknown"},
        )

    def test_values_and_sentinels_never_leak(self):
        facts = [
            _fact("v1", self.FIELD, "provided",
                  value=SENTINEL_VALUE, unit=SENTINEL_UNIT),
            _fact("v2", MAJOR + ".cultivar", "provided",
                  value=0, scope="field"),
        ]
        plan = self._plan(facts)
        blob = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(SENTINEL_VALUE, blob)
        self.assertNotIn(SENTINEL_UNIT, blob)
        self.assertNotIn(SENTINEL_LOCATOR, blob)
        for q in plan["questions"]:
            self.assertNotIn("value", q)
            for f in q["facts"]:
                self.assertNotIn("value", f)
                self.assertNotIn("unit", f)
                self.assertNotIn("source_refs", f)
                self.assertNotIn("verification", f)


class WorkbookReferenceTests(_PlanCase):
    def _run(self, root, workbook_sha=WB_SHA):
        return self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file(
                synth_profile(workbook_sha=workbook_sha)
            ),
        )

    def test_eight_file_check_paths(self):
        outside = Path(tempfile.mkdtemp(prefix="knuaf-hort-out-"))
        self.addCleanup(lambda: None)
        (outside / "out.xlsx").write_bytes(WB_BYTES)

        sources = [
            _source("s_abs", path=str(outside / "out.xlsx"), hash=WB_SHA),
            _source("s_changed", path="changed.xlsx", hash=WB_SHA),
            _source("s_dir", path="dir.xlsx", hash=WB_SHA),
            _source("s_dotdot", path="sub/../match.xlsx", hash=WB_SHA),
            _source("s_link_in", path="link_in.xlsx", hash=WB_SHA),
            _source("s_link_out", path="link_out.xlsx", hash=WB_SHA),
            _source("s_missing", path="gone.xlsx", hash=WB_SHA),
            _source("s_nopath", path=None, hash=WB_SHA),
            _source("s_nonstr", path=7, hash=WB_SHA),
            _source("s_nul", path="source\x00.xlsx", hash=WB_SHA),
            _source("s_empty", path="", hash=WB_SHA),
            _source("s_match", path="match.xlsx", hash=WB_SHA),
            _source("s_not_wb", path="excluded.xlsx", hash=NON_WB_SHA),
        ]
        if os.geteuid() != 0:
            sources.append(
                _source("s_noperm", path="noperm.xlsx", hash=WB_SHA)
            )
        root = self._project(
            facts=[_binding_fact()], sources=sources,
        )
        (root / "match.xlsx").write_bytes(WB_BYTES)
        (root / "changed.xlsx").write_bytes(b"changed bytes")
        (root / "dir.xlsx").mkdir()
        (root / "real_in").mkdir()
        (root / "real_in" / "inside.xlsx").write_bytes(WB_BYTES)
        (root / "link_in.xlsx").symlink_to("real_in/inside.xlsx")
        (root / "link_out.xlsx").symlink_to(outside / "out.xlsx")
        (root / "excluded.xlsx").write_bytes(WB_BYTES)
        if os.geteuid() != 0:
            (root / "noperm.xlsx").write_bytes(WB_BYTES)
            (root / "noperm.xlsx").chmod(0)
        else:
            # root는 권한 비트와 무관하게 읽어서 match가 된다 — 재현 불가
            pass

        plan, code = self._run(root)
        self.assertEqual(code, 0)
        refs = plan["workbook_references"]
        self.assertEqual(refs["status"], "checked")
        items = {i["source_id"]: i for i in refs["items"]}
        self.assertNotIn("s_not_wb", items)

        expected = {
            "s_abs": "outside_project",
            "s_changed": "changed",
            "s_dir": "path_invalid",
            "s_dotdot": "outside_project",
            "s_link_in": "match",
            "s_link_out": "outside_project",
            "s_missing": "missing",
            "s_nopath": "path_invalid",
            "s_nonstr": "path_invalid",
            "s_nul": "path_invalid",
            "s_empty": "path_invalid",
            "s_match": "match",
        }
        if os.geteuid() != 0:
            expected["s_noperm"] = "read_error"
        self.assertEqual(
            {sid: i["file_check"] for sid, i in items.items()}, expected
        )
        wb = json.loads(self._profile_file(synth_profile())
                        .read_text(encoding="utf-8"))["workbook_reference"]
        for sid, item in items.items():
            self.assertEqual(item["role"], "professor_reference")
            self.assertEqual(item["authority"], "user_statement")
            if item["file_check"] == "match":
                self.assertEqual(item["cautions"], wb["cautions"])
                self.assertIsNone(item["held_reason"])
            else:
                self.assertEqual(item["cautions"], [])
                self.assertTrue(item["held_reason"])

    def test_no_matching_source_registered(self):
        root = self._project(
            facts=[_binding_fact()],
            sources=[_source("s_other", path="x.txt", hash="e" * 64)],
        )
        plan, code = self._run(root)
        self.assertEqual(code, 0)
        self.assertEqual(plan["workbook_references"], {
            "status": "no_hort_reference_registered",
            "items": [],
        })


class ProfileInvalidTests(_PlanCase):
    def _assert_invalid(self, profile=None, raw_text=None):
        if raw_text is None:
            path = self._profile_file(profile)
        else:
            path = Path(tempfile.mkdtemp(prefix="knuaf-hort-bad-")) / "profile.json"
            path.write_text(raw_text, encoding="utf-8")
        with self.assertRaises(self.hp.ProfileInvalidError) as caught:
            self.hp.load_profile(path, module=self.module)
        self.assertEqual(caught.exception.reason, "profile_invalid")
        self.assertIsInstance(
            caught.exception, self.mc.MajorContractError
        )

    def test_root_key_missing(self):
        p = synth_profile()
        del p["notice"]
        self._assert_invalid(p)

    def test_root_key_extra(self):
        p = synth_profile()
        p["unknown"] = True
        self._assert_invalid(p)

    def test_nested_key_extra(self):
        p = synth_profile()
        p["workbook_reference"]["extra"] = 1
        self._assert_invalid(p)

    def test_duplicate_key(self):
        text = json.dumps(synth_profile(), ensure_ascii=False)
        self.assertTrue(text.startswith('{"schema"'))
        dup = '"schema": "x", ' + text
        self._assert_invalid(raw_text=dup)

    def test_sheets_not_18_and_duplicated(self):
        p = synth_profile()
        p["workbook_reference"]["sheets"] = \
            p["workbook_reference"]["sheets"][:17]
        self._assert_invalid(p)
        p = synth_profile()
        p["workbook_reference"]["sheets"][17] = \
            p["workbook_reference"]["sheets"][0]
        self._assert_invalid(p)

    def test_sheet_strip_mismatch_and_unknown_sheet(self):
        # sheets에는 " 7. 합성시트07"만 있다 — strip한 "7. 합성시트07"은 무효.
        p = synth_profile()
        p["workbook_reference"]["cautions"][0]["locations"][1]["sheet"] = \
            "7. 합성시트07"
        self._assert_invalid(p)
        p = synth_profile()
        p["workbook_reference"]["cautions"][0]["locations"][0]["sheet"] = \
            "없는시트"
        self._assert_invalid(p)

    def test_locations_empty(self):
        p = synth_profile()
        p["workbook_reference"]["cautions"][0]["locations"] = []
        self._assert_invalid(p)
        p = synth_profile()
        p["workbook_reference"]["cautions"][0]["locations"][0]["cells"] = []
        self._assert_invalid(p)

    def test_bad_cellref(self):
        for cell in ("1A", "A", "A0 B2", "A1;B2", "abc"):
            p = synth_profile()
            p["workbook_reference"]["cautions"][0] \
                ["locations"][0]["cells"][0] = cell
            self._assert_invalid(p)

    def test_appendix_map_sheet_missing(self):
        p = synth_profile()
        p["workbook_reference"]["appendix_map"][0]["sheet"] = "없는시트"
        self._assert_invalid(p)
        p = synth_profile()
        p["workbook_reference"]["appendix_map"][0]["no"] = 17
        self._assert_invalid(p)

    def test_duplicate_appendix_numbers_rejected(self):
        # 1..18 all present plus one duplicate: set equality alone would
        # accept 19 rows, so the count must be checked too.
        p = synth_profile()
        amap = p["workbook_reference"]["appendix_map"]
        amap.append(dict(amap[7]))
        self._assert_invalid(p)
        p = synth_profile()
        apx = p["precedents"][0]["appendices"]
        apx.append(dict(apx[7]))
        self._assert_invalid(p)

    def test_caution_status_outside_two_values(self):
        p = synth_profile()
        p["workbook_reference"]["cautions"][0]["status"] = "confirmed"
        self._assert_invalid(p)

    def test_caution_id_duplicate(self):
        p = synth_profile()
        p["workbook_reference"]["cautions"][1]["id"] = "X-C1"
        self._assert_invalid(p)

    def test_precedent_ref_mismatch(self):
        p = synth_profile()
        p["precedents"][0]["ref"] = "HT3"
        self._assert_invalid(p)

    def test_precedent_title_author_required(self):
        for key in ("title", "author"):
            p = synth_profile()
            del p["precedents"][0][key]
            self._assert_invalid(p)
            p = synth_profile()
            p["precedents"][0][key] = ""
            self._assert_invalid(p)

    def test_use_scope_mismatch(self):
        p = synth_profile()
        p["evidence_sources"][0]["use_scope"] = "invented_scope"
        self._assert_invalid(p)

    def test_module_version_mismatch(self):
        p = synth_profile()
        p["module_version"] = "9.9.9"
        self._assert_invalid(p)

    def test_bad_json(self):
        self._assert_invalid(raw_text="{not json")


# ---------------------------------------------------------------------
# 개인정보 검사기 (설계 §13-6 + 형님 결정 정정 + Astra 검토 3 정정)
#
# 모든 문자열 "값"을 NFKC 정규화해 검사한다(키 이름은 제외).
# - precedents[*].author 값만 이름 검사(성명·교수·상호 표지)에서 제외.
#   그 값에도 숫자·연락처·주소·'학번' 표지 검사는 그대로 적용한다.
# - 8자리 이상 숫자열은 모든 필드에서 금지. 예외: sha256 키의 64자리
#   16진수 값, ID 형태 값( ^[A-Z]{1,3}-?[A-Z0-9]{1,6}$ ), 그리고
#   evidence_sources[*].edition 값이 ^LM[0-9]{10}_[0-9]{2}v[0-9]+$ 와
#   정확히 일치할 때만 — 필드 전체 면제 아님.
# ---------------------------------------------------------------------

_DIGITS8 = re.compile(r"[0-9]{8,}")
_ACCOUNT = re.compile(r"[0-9]+-[0-9]+-[0-9]+")
_ID_SHAPE = re.compile(r"^[A-Z]{1,3}-?[A-Z0-9]{1,6}$")
_NCS_EDITION = re.compile(r"^LM[0-9]{10}_[0-9]{2}v[0-9]+$")
_DATE_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_PHONE = re.compile(r"0[0-9]{1,2}-[0-9]{3,4}-[0-9]{4}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EXT = re.compile(r"(?i)\.(pdf|xlsx|pptx|docx|hwp)")
_ADDR = re.compile(r"(로|길)[ ]*[0-9]+|번지")
_FARM_NAME = re.compile(r"[가-힣]{2,}(농장|농원)")
_STUDENT_ID = re.compile(r"학번\s*[:：]?\s*[0-9]")
_GENERIC_FARM_PREFIX = {"모델", "시범", "표준"}


def _privacy_findings(doc):
    hits = []

    def visit(value, author_field=False, key=None):
        norm = unicodedata.normalize("NFKC", value)
        if _EMAIL.search(norm):
            hits.append(("email", value))
        if _PHONE.search(norm):
            hits.append(("phone", value))
        if _EXT.search(norm):
            hits.append(("file_extension", value))
        if "/Users/" in norm or "C:\\" in norm or "\\\\" in norm:
            hits.append(("private_path", value))
        if _ADDR.search(norm):
            hits.append(("address_marker", value))
        # '학번'은 형님 정정상 숫자 형태 금지 — 라벨+숫자 결합만 표지로 본다
        # ("학번…은 싣지 않는다" 같은 정책 문구는 표지가 아님).
        if _STUDENT_ID.search(norm):
            hits.append(("student_id_marker", value))
        if not author_field:
            if "성명" in norm:
                hits.append(("name_marker", value))
            if "교수" in norm:
                hits.append(("professor_marker", value))
            for m in _FARM_NAME.finditer(norm):
                if m.group(0)[:-2] not in _GENERIC_FARM_PREFIX:
                    hits.append(("farm_name_marker", value))
        for m in _ACCOUNT.finditer(norm):
            frag = m.group(0)
            if not (
                frag.startswith("0")
                or _DATE_SHAPE.fullmatch(frag)
            ):
                hits.append(("account_like", value))
        for m in _DIGITS8.finditer(norm):
            frag = m.group(0)
            if _DATE_SHAPE.fullmatch(frag):
                continue
            if (
                key == "sha256"
                and re.fullmatch(r"[0-9a-f]{64}", norm)
            ):
                continue
            if _ID_SHAPE.fullmatch(norm):
                continue
            if key == "edition" and _NCS_EDITION.fullmatch(norm):
                continue
            hits.append(("digits8+", value))
            break

    def walk(value, in_author=False, key=None):
        if isinstance(value, str):
            visit(value, author_field=in_author, key=key)
        elif isinstance(value, list):
            for v in value:
                walk(v, in_author=in_author, key=key)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v,
                     in_author=in_author or k == "author",
                     key=k)

    walk(doc)
    return hits


def _neg_profile(mutate):
    p = synth_profile()
    mutate(p)
    return p


class PrivacyScannerTests(_PlanCase):
    def test_synthetic_negatives_each_detected(self):
        cases = {
            "email": lambda p: p["evidence_sources"][0]["caveats"]
                .append("문의 a.b@example.org"),
            "phone": lambda p: p["evidence_sources"][0]["caveats"]
                .append("연락 010-1234-5678"),
            "digits8": lambda p: p["evidence_sources"][0]["caveats"]
                .append("번호 20261234"),
            "digits8_in_id_field": lambda p: p["evidence_sources"][0]
                .__setitem__("id", "20261234"),
            "digits8_in_edition_field": lambda p: p[
                "evidence_sources"][0].__setitem__("edition", "20261234"),
            "account": lambda p: p["evidence_sources"][0]["caveats"]
                .append("계좌 110-123-456789"),
            "address": lambda p: p["evidence_sources"][0]["caveats"]
                .append("소재지 합성로 12"),
            "users_path": lambda p: p["evidence_sources"][0]["caveats"]
                .append("/Users/name/file"),
            "unc_path": lambda p: p["evidence_sources"][0]["caveats"]
                .append("\\\\host\\share"),
            "extension": lambda p: p["evidence_sources"][0]["caveats"]
                .append("파일명 report.pdf"),
            "student_id_marker": lambda p: p["evidence_sources"][0]
                ["caveats"].append("학번: 20261234"),
            "name_marker": lambda p: p["evidence_sources"][0]["caveats"]
                .append("성명 홍길동"),
            "professor_marker": lambda p: p["evidence_sources"][0]
                ["caveats"].append("김철수 교수"),
            "farm_name": lambda p: p["evidence_sources"][0]["caveats"]
                .append("행복농장 사례"),
            "nfkc_fullwidth_digits": lambda p: p["evidence_sources"][0]
                ["caveats"].append("번호 \uFF12\uFF10\uFF12\uFF16"
                                   "\uFF11\uFF12\uFF13\uFF14"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                findings = _privacy_findings(_neg_profile(mutate))
                self.assertTrue(findings, "missed: " + name)

    def test_clean_profile_has_no_findings(self):
        self.assertEqual(_privacy_findings(synth_profile()), [])

    def test_author_name_checks_only(self):
        # author는 이름 검사(성명·교수·상호 표지)에서만 제외
        p = _neg_profile(lambda d: d["precedents"][0].__setitem__(
            "author", "성명 홍길동 교수"))
        self.assertEqual(_privacy_findings(p), [])
        # 같은 값이 다른 필드에 있으면 잡힌다
        p = _neg_profile(lambda d: d["evidence_sources"][0]
                         ["caveats"].append("성명 홍길동 교수"))
        self.assertTrue(_privacy_findings(p))
        # author에도 숫자·표지·주소 검사는 그대로 적용
        for bad in ("20261234", "학번 20261234", "010-1234-5678",
                    "합성로 12"):
            with self.subTest(author=bad):
                p = _neg_profile(lambda d: d["precedents"][0]
                                 .__setitem__("author", bad))
                self.assertTrue(_privacy_findings(p))

    def test_sha_and_id_exemptions_not_bypassed(self):
        # sha256 키의 64자리 16진수는 면제 — 숫자만으로 이뤄진 값으로 검증
        p = _neg_profile(lambda d: d["evidence_sources"][0]
                         .__setitem__("sha256", "1" * 64))
        self.assertEqual(_privacy_findings(p), [])
        # 다른 곳의 같은 문자열은 검출(해시 예외가 우회되지 않음)
        sha = "1" * 64
        p = _neg_profile(lambda d: d["evidence_sources"][0]
                         ["caveats"].append(sha))
        self.assertTrue(_privacy_findings(p))
        # ID 형태 값(OF-N1 등)은 면제 — 대조로 순수 8자리는 위 사례에서 검출
        p = _neg_profile(lambda d: d["evidence_sources"][0]
                         .__setitem__("id", "OF-N1"))
        self.assertEqual(_privacy_findings(p), [])

    def test_ncs_edition_exception_is_exact_only(self):
        # 정정(2): ^LM[0-9]{10}_[0-9]{2}v[0-9]+$ 정확 일치만 허용
        p = _neg_profile(lambda d: d["evidence_sources"][0]
                         .__setitem__("edition", "LM2401010803_17v1"))
        self.assertEqual(_privacy_findings(p), [])
        for bad in ("LM2401010803", "LM2401010803_17v1 extra",
                    "LM240101080_17v1", "AM2401010803_17v1",
                    "LM2401010803_7v1"):
            with self.subTest(edition=bad):
                p = _neg_profile(lambda d: d["evidence_sources"][0]
                                 .__setitem__("edition", bad))
                self.assertTrue(_privacy_findings(p),
                                "missed edition: " + bad)


class PlanContractTests(_PlanCase):
    def test_return_shape_and_no_computed_keys(self):
        root = self._project(facts=[_binding_fact()])
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=self._profile_file())
        self.assertEqual(code, 0)
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["binding"]["major_id"], MAJOR)
        self.assertEqual(plan["binding"]["basis"], "legacy_record")
        self.assertEqual(plan["binding"]["module_version"], "0.1.0")
        self.assertEqual(plan["project_revision"], 3)
        self.assertEqual(plan["module_version"], "0.1.0")
        self.assertRegex(plan["profile_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [q["field_id"] for q in plan["questions"]],
            [s.field_id for s in self.module.question_schema],
        )
        self.assertEqual(
            plan["document_plan"],
            [dict(vars(n)) for n in self.module.document_plan],
        )
        self.assertEqual(
            [p["ref"] for p in plan["precedents"]], ["HT1", "HT2"]
        )
        self.assertEqual(
            plan["evidence_rules"]["common_pack_policy"], "unverified"
        )
        self.assertEqual(plan["finance"]["status"], "unsupported")
        for fp in plan["finance"]["profiles"]:
            self.assertEqual(set(fp), {"profile", "status", "reason"})
            self.assertEqual(fp["status"], "unsupported")
        banned = {"calculated", "computed", "result"}
        for key in _all_keys(plan):
            self.assertNotIn(key, banned)

    def test_project_tree_and_profile_bytes_unchanged(self):
        root = self._project(facts=[_binding_fact()])
        profile_path = self._profile_file()
        tree_before = self._tree_bytes(root)
        profile_before = profile_path.read_bytes()
        plan, code = self.hp.build_plan(
            root, registry=self._registry(),
            profile_path=profile_path)
        self.assertEqual(code, 0)
        self.assertEqual(self._tree_bytes(root), tree_before)
        self.assertEqual(profile_path.read_bytes(), profile_before)


class RealProfileTests(_PlanCase):
    def test_real_profile_loads_with_default_module(self):
        path = self.hp.PROFILE_PATH
        if not path.is_file():
            self.fail("실제 profile.json 부재: %s" % path)
        before = path.read_bytes()
        # load_profile() 기본 인자 경로 — HORT_ENV_SYSTEMS_MODULE 사용
        profile = self.hp.load_profile()
        self.assertEqual(
            profile["profile_sha256"],
            hashlib.sha256(before).hexdigest(),
        )
        self.assertEqual(path.read_bytes(), before)

    def test_confirmed_list_exactly(self):
        path = self.hp.PROFILE_PATH
        if not path.is_file():
            self.fail("실제 profile.json 부재: %s" % path)
        profile = self.hp.load_profile(module=self.module)
        cautions = profile["workbook_reference"]["cautions"]
        self.assertEqual(
            {c["status"] for c in cautions},
            {"confirmed_inconsistency", "needs_confirmation"},
        )
        confirmed = {c["id"] for c in cautions
                     if c["status"] == "confirmed_inconsistency"}
        # Astra 검토 3 정정: X-N8은 needs_confirmation — confirmed는 6건
        self.assertEqual(
            confirmed,
            {"X-C1", "X-C2", "X-C3", "X-N1", "X-N2", "X-N5"},
        )

    def test_real_profile_privacy_clean(self):
        path = self.hp.PROFILE_PATH
        if not path.is_file():
            self.fail("실제 profile.json 부재: %s" % path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(_privacy_findings(doc), [])


class CliSubprocessTests(_PlanCase):
    def _cli(self, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "gg_hort_plan.py"), *args],
            capture_output=True, text=True, env=env)

    def test_exit_0_on_bound_project(self):
        root = self._project(facts=[_binding_fact()])
        proc = self._cli("--project", str(root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(
            len(plan["questions"]), len(self.module.question_schema)
        )

    def test_exit_2_when_unbound(self):
        root = self._project()
        proc = self._cli("--project", str(root))
        self.assertEqual(proc.returncode, 2)
        plan = json.loads(proc.stdout)
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["reason"], "major_required")

    def test_argparse_rejects_missing_and_extra_args(self):
        self.assertEqual(self._cli().returncode, 2)
        self.assertEqual(self._cli("--bogus").returncode, 2)
