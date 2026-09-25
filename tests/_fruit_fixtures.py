"""Synthetic fixtures for the fruit_trees first bundle (no real originals).

Every workbook, HWPX and handoff here is written by the test itself.  The
common reference set is replaced in-process by the synthetic workbooks'
own hashes (``reference_members``), so no professor or school file is
read or shipped.
"""
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

from tests._harness import runtime, write_text

FRUIT = "fruit_trees"
SHEETS = ["목록", "1. 기초재무상태조사", " 5. 판매계획", "10. 감가상각비계획 ",
          "15.추정소득분석"]
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"


def workbook_bytes(tag, sheets=SHEETS):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = sheets[0]
    for name in sheets[1:]:
        wb.create_sheet(name)
    wb.active["A1"] = tag
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def hwpx_bytes(paragraphs):
    """Minimal HWPX: Contents/section1.xml with direct ``hp:p`` children."""
    body = "".join(
        '<hp:p><hp:run><hp:t>%s</hp:t></hp:run></hp:p>'
        % text.replace("\t", "<hp:tab/>") for text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<hs:sec xmlns:hs="%s" xmlns:hp="%s">%s</hs:sec>' % (HS, HP, body))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section1.xml", xml.encode("utf-8"))
    return buf.getvalue()


PARAGRAPHS = ["Ⅰ. 서론", "Ⅱ-1 입지", "Ⅲ-3 재배작형", "Ⅲ-4 병해충 및 방제력",
              "Ⅳ. 맺음말", "3. 재배관리\t4. 병해충관리"]


def sha(data):
    return runtime("gg_core").digest(data)


class FruitProject:
    """A bound fruit_trees project with registered synthetic originals."""

    def __init__(self, case, root):
        self.case = case
        self.root = Path(root)
        self.core = runtime("gg_core")
        self.x01 = workbook_bytes("x01")
        self.x02 = workbook_bytes("x02")
        self.s01 = hwpx_bytes(PARAGRAPHS)
        self.members = {
            "X01": {"sha256": sha(self.x01), "sheets": list(SHEETS),
                    "label": "synthetic X01"},
            "X02": {"sha256": sha(self.x02), "sheets": list(SHEETS),
                    "label": "synthetic X02"},
        }
        patcher = mock.patch.object(runtime("gg_workbook_registry"),
                                    "load_reference_set",
                                    lambda path=None: self.members)
        patcher.start()
        case.addCleanup(patcher.stop)

    # -- canonical writes (existing apply transaction only) ------------
    def apply(self, ops, request_id):
        revision = self.core.load(self.root)["revision"]
        self.core.apply(self.root, {"request_id": request_id, "ops": ops},
                        revision)
        return self.core.load(self.root)["revision"]

    def register(self, source_id, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._n = getattr(self, "_n", 0) + 1
        return self.apply([{"collection": "sources", "value": {
            "id": source_id, "path": rel, "claims": {},
            "claim_review": "synthetic-review"}}],
            "src:%s:%d" % (source_id, self._n))

    def fact(self, fid, field_id, value, *, scope="farm",
             answer_state="provided", value_type="text", unit="",
             request_id=None):
        write_text(self.root, "answers/%s.txt" % fid,
                   "%s: %r\n" % (field_id, value))
        src = {"collection": "sources", "value": {
            "id": "ans-" + fid, "path": "answers/%s.txt" % fid,
            "claims": {}, "claim_review": "synthetic-review"}}
        fact = {"id": fid, "field_id": field_id, "kind": "reported_fact",
                "value": value if answer_state == "provided" else None,
                "unit": unit, "value_type": value_type, "period": None,
                "scope": scope, "answer_state": answer_state,
                "verification": "source_located",
                "source_refs": [{"id": "ans-" + fid, "locator": "line 1",
                                 "revision": 1}]}
        if value_type == "decimal":
            fact["period_reason"] = "계획 구조값(기간 무관)"
        if answer_state in ("explicit_none", "withheld", "not_applicable"):
            fact["reason"] = "synthetic reason"
        return self.apply([src, {"collection": "facts", "value": fact}],
                          request_id or "fact:" + fid)

    def record(self, source_id):
        return self.core.load(self.root)["sources"][source_id]

    # -- setup ---------------------------------------------------------
    def register_originals(self):
        self.register("s01", "sources/originals/S01.hwpx", self.s01)
        self.register("x01", "sources/originals/X01.xlsx", self.x01)
        self.register("x02", "sources/originals/X02.xlsx", self.x02)

    def template_refs(self):
        refs = []
        for ref_id, sid, role in (("S01", "s01", "school_template"),
                                  ("X01", "x01", "professor_reference"),
                                  ("X02", "x02", "professor_reference")):
            rec = self.record(sid)
            refs.append({"ref_id": ref_id, "source_id": sid,
                         "source_revision": rec["revision"],
                         "sha256": rec["hash"], "role": role,
                         "rights": "local research only"})
        return refs

    def attestation(self, scope_id, source_id="attest"):
        self.register(source_id, "sources/research/attest.txt",
                      ("과수 전공 폴더 조사 범위 %s: XLSX 없음\n"
                       % scope_id).encode("utf-8"))

    def register_handoff(self, h, source_id="handoff"):
        data = json.dumps(h, ensure_ascii=False, indent=1).encode("utf-8")
        self.register(source_id, "sources/research/handoff.json", data)
        return self.fact("handoff-ref", "fruit_trees.research_handoff",
                         source_id)

    def plan(self):
        return runtime("gg_fruit_plan").fruit_plan(self.root)


def base_handoff(fp, *, scope_id="/synthetic/major/fruit",
                 evidence=None, files=None, research_selection=None):
    """A complete, valid handoff for ``fp`` (hash added by with_hash)."""
    hm = runtime("gg_research_handoff")
    reqs = []
    for i in range(1, 18):
        rid = "FRT-S%02d" % i
        loc = [{"kind": "xml", "ref": "section1:p1", "heading": "Ⅰ. 서론"},
               {"kind": "parsed", "ref": "parsed:p1", "heading": "Ⅰ. 서론"}]
        conflicts = []
        if rid == "FRT-S13":
            loc = [{"kind": "xml", "ref": "section1:p4",
                    "heading": "Ⅲ-4 병해충 및 방제력"},
                   {"kind": "parsed", "ref": "parsed:p4",
                    "heading": "Ⅲ-4 병해충 및 방제력"}]
            conflicts = ["C06", "C08"]
        reqs.append({"id": rid, "title": "요구 %d" % i, "source_ref": "S01",
                     "locators": loc, "authority": "school_form",
                     "conflict_ids": conflicts})
    conflicts = [{"id": "C%02d" % i, "summary": "충돌 %d" % i,
                  "status": "unresolved",
                  "affected_sections": ["appendix_15"],
                  "blocked_outputs": [], "decision_owner": "교수",
                  "decision_ref": None, "xr_refs": []}
                 for i in range(1, 16)]
    conflicts[5]["affected_sections"] = ["ch3_pest"]
    conflicts[5]["xr_refs"] = ["XR-02"]
    h = {
        "schema": "knuaf-research-handoff/v1",
        "major_id": FRUIT,
        "template_source_refs": fp.template_refs(),
        "school_requirement_refs": reqs,
        "appendix_roles": [{"role_no": i, "name": "역할 %d" % i,
                            "school_tables": ["T%d" % i],
                            "workbook_sheets": {"X01": [SHEETS[1]],
                                                "X02": [SHEETS[1]]}}
                           for i in range(1, 16)],
        "row_mappings": [{"mapping_id": "M1", "school_table": "T75",
                          "school_row": "소득", "source_ref": "X02",
                          "source_locator": "15!E31", "indicator": "소득",
                          "unit": "천원", "period": "연", "aggregation":
                          "unresolved", "status": "unresolved",
                          "conflict_ids": ["C11"], "blocked_outputs": [],
                          "xr_refs": ["XR-04"]}],
        "known_conflicts": conflicts,
        "period_model": {"school_display_years": list(range(2027, 2037)),
                         "workbook_base_years": {"X01": list(range(2026, 2031)),
                                                 "X02": list(range(2026, 2031))},
                         "unresolved": ["5년→10년 변환"]},
        "workbook_reference_set": {
            "survey": {"scope_label": "fruit", "scope_id": scope_id,
                       "status": "complete",
                       "evidence": evidence or {
                           "kind": "user_attestation",
                           "attestation_source_id": "attest"}},
            "files": files if files is not None else [
                {"file_id": "x01", "label": "X01", "origin": "common_root",
                 "relative_id": None, "declared_role": "professor_reference",
                 "role_authority": {"kind": "none", "ref": None},
                 "link": {"kind": "template_ref", "ref_id": "X01"}},
                {"file_id": "x02", "label": "X02", "origin": "common_root",
                 "relative_id": None, "declared_role": "professor_reference",
                 "role_authority": {"kind": "none", "ref": None},
                 "link": {"kind": "template_ref", "ref_id": "X02"}}],
            "research_selection": research_selection},
        "unresolved_items": [{"id": "U1", "summary": "수선비율 규범 여부",
                              "blocks": ["appendix_09"]}],
    }
    return hm.with_hash(h)


def ready(case, *, bind=True, handoff=True, **handoff_kw):
    """Project bound to fruit_trees with originals, attestation, handoff."""
    from tests._harness import bind_major
    root = case.make_project()
    if bind:
        bind_major(root, FRUIT)
    fp = FruitProject(case, root)
    fp.register_originals()
    fp.attestation(handoff_kw.get("scope_id", "/synthetic/major/fruit"))
    if handoff:
        fp.register_handoff(base_handoff(fp, **handoff_kw))
    return fp
