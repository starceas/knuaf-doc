"""gg_progress 오프라인 시험 — 합성 정본만 쓴다(학생 원고 아님).

DESIGN-W-P §7의 1·2·3·4·5·6·7·11·13을 실제 ``gate()``로 검사한다
(mock 없음). 8·9·10·12는 곁파일 배선(2부) 몫이라 여기서 다루지 않는다.

정본은 전부 ``tests/_harness``가 만든 임시 폴더 안에만 생기고, 검토
보고서도 합성 Markdown/JSON이다.
"""
import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import openpyxl
from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO))

from tests import _harness as th  # noqa: E402
from tests._harness import (  # noqa: E402
    bind_major,
    claim_of,
    fact_op,
    runtime,
    section_op,
    source_op,
    write_text,
)
from tests.test_student_language import (  # noqa: E402
    FORBIDDEN_STUDENT_TERMS,
    KNOWN_MAJOR_NAMES,
)

SCRIPT = SCRIPTS / "gg_progress.py"
WRITTEN_AT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?"
    r"(?:Z|[+-]\d{2}:\d{2})$")
MAX_TEXT = 500
STEP_COUNT = 7
STATES = frozenset({"done", "now", "todo", "unknown"})
AFTER = {"text": "한글 마무리(글꼴·여백·쪽번호)·교수 승인", "owner": "student"}
REVIEW_KINDS = ("content", "logic", "calculation", "docx", "xlsx", "render")


def _write_bytes(root, rel, data):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _workbook_bytes(marker="BYTES"):
    book = openpyxl.Workbook()
    book.active["A1"] = marker
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _document_bytes(line="본문"):
    document = Document()
    document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _seed(root, major="specialty_crops"):
    """원답변 하나 + 전공 등록 — 1단계 근거만 있는 최소 정본."""
    core = runtime("gg_core")
    write_text(root, "answer.txt", "합성 원답변 한 줄\n")
    fact = fact_op("answer", "문체", "학부생 화법", None,
                   verification="claim_supported", claim_id="answer")
    core.apply(root, {"request_id": "seed", "ops": [
        source_op(claims=claim_of(fact["value"], "answer")), fact]}, 0)
    bind_major(root, major)
    return fact


def _add_finance_gate(root, base_fact, period="2030"):
    """surplus 게이트 사실 두 개 + 그 출처 — 2단계 ``done``의 근거."""
    core = runtime("gg_core")
    state = fact_op("gate-state", "finance.gate.state", "surplus", "",
                    scope="project", period=period,
                    verification="claim_supported", source_id="calculation",
                    claim_id="state")
    profit = fact_op("gate-profit", "finance.gate.target_profit", "100", "원",
                     value_type="decimal", period=period, scope="annual",
                     kind="observation", verification="claim_supported",
                     source_id="calculation", claim_id="profit")
    profit["value"].update(finance_role="context",
                           meaning_id="sales.net_profit",
                           measure={"kind": "monetary_total",
                                    "currency": "KRW", "unit": "원"})
    claims = claim_of(base_fact["value"], "answer")
    claims.update(claim_of(state["value"], "state"))
    claims.update(claim_of(profit["value"], "profit"))
    write_text(root, "calculation.json", json.dumps(
        {"description": "synthetic fixture", "target_year": period,
         "profit": 100}, ensure_ascii=False))
    source = source_op("calculation.json", claims=claims)
    source["value"]["id"] = "calculation"
    core.apply(root, {"request_id": "seed2", "ops": [source, state, profit]},
               core.load(root)["revision"])
    return state


def _set_formats(root, formats):
    core = runtime("gg_core")
    core.apply(root, {"request_id": "fmt", "ops": [{"collection": "rules", "value": {
        "id": "output_formats", "formats": list(formats),
        "source_refs": [{"id": "answer", "locator": "l1", "revision": 1}]}}]},
        core.load(root)["revision"])


def _add_section(root, sid="intro", title="머리말", order=1):
    core = runtime("gg_core")
    rel = "%s.md" % sid
    write_text(root, rel, "본문 한 줄.\n")
    core.apply(root, {"request_id": "sec", "ops": [
        section_op(sid, title, rel, order=order)]}, core.load(root)["revision"])


def _target_refs(root):
    core = runtime("gg_core")
    return core.sort_target_refs(core._export_refs(core.load(root)))


def _bind_semantic(root, kind, refs, rel, rid, output_hash=None):
    """의미 검토 보고서 + 관측 기록 + review 행 — 재무 검토범위를 만족한다."""
    core, sem = runtime("gg_core"), runtime("gg_fact_semantics")
    project = core.load(root)
    registry = core._semantic_context(root, project)["registry"]
    facts = [sem.classify_fact(project["facts"][ref["id"]], registry=registry,
                               bindings=[])
             for ref in refs
             if ref["collection"] == "facts" and ref["id"] in project["facts"]]
    consumers = sem.resolve_consumers(project, inputs=[], registry=registry)
    report = sem.make_semantics_report(
        registry=registry, target_refs=refs,
        input_revision=project["revision"],
        input_fingerprint=core.fingerprint(root, project, refs),
        facts=facts, consumers=consumers, body_result=None)
    report["review"] = {"author_id": "author", "reviewer_id": "reviewer",
                        "review_kinds": [kind], "findings": [],
                        "disposition": "resolved"}
    if output_hash is not None:
        report["semantics"]["output_file_hash"] = output_hash
    write_text(root, rel, json.dumps(report, ensure_ascii=False))
    report_hash = core.digest((Path(root) / rel).read_bytes())
    fingerprint = core.fingerprint(root, project, refs)
    observation = {
        "author_session": "s-a", "reviewer_session": "s-r",
        "author_id": "author", "reviewer_id": "reviewer",
        "review_kinds": [kind], "target_refs": refs,
        "input_fingerprint": fingerprint, "report_path": rel,
        "report_hash": report_hash, "input_revision": project["revision"]}
    receipt = core.ingest_review_observation(root, observation, "obs-1")
    core.apply(root, {"request_id": "b-" + rid, "ops": [{
        "collection": "reviews", "value": {
            "id": rid, "review_kind": kind, "author_id": "author",
            "reviewer_id": "reviewer", "target_refs": refs,
            "input_fingerprint": fingerprint, "findings": [],
            "disposition": "resolved", "status": "pass", "path": rel,
            "report_hash": report_hash, "coverage": {"intro": ["문장"]},
            "provenance_path": receipt["path"],
            "provenance_hash": receipt["hash"],
            "input_revision": project["revision"]}}]}, project["revision"])
    return report_hash


def _bind_plain(root, kind, refs, rid, request_id=None):
    """평문 Markdown 검토 — 의미 보고서 없이도 통과하는 갈래."""
    core = runtime("gg_core")
    rel = "review-%s.md" % rid
    write_text(root, rel, "# %s 검토\n" % kind)
    project = core.load(root)
    fingerprint = core.fingerprint(root, project, refs)
    observation = {
        "author_session": "s-a", "reviewer_session": "s-r",
        "author_id": "author", "reviewer_id": "reviewer",
        "review_kinds": [kind], "target_refs": refs,
        "input_fingerprint": fingerprint, "report_path": rel,
        "report_hash": core.digest((Path(root) / rel).read_bytes()),
        "input_revision": project["revision"]}
    receipt = core.ingest_review_observation(root, observation, "obs-1")
    core.apply(root, {"request_id": request_id or ("b-" + rid), "ops": [{
        "collection": "reviews", "value": {
            "id": rid, "review_kind": kind, "author_id": "author",
            "reviewer_id": "reviewer", "target_refs": refs,
            "input_fingerprint": fingerprint, "findings": [],
            "disposition": "resolved", "status": "pass", "path": rel,
            "coverage": {"intro": ["문장"]},
            "provenance_path": receipt["path"],
            "provenance_hash": receipt["hash"],
            "input_revision": project["revision"]}}]}, project["revision"])


def _adopt(root, path, fmt, data, checks, rid, evidence, evidence_hash,
           companion=None):
    core = runtime("gg_core")
    project = core.load(root)
    _write_bytes(root, path, data)
    refs = _target_refs(root)
    fingerprint = core.fingerprint(root, project, refs)
    file_hash = core.digest(data)
    output = {
        "id": rid, "path": path, "format": fmt, "file_hash": file_hash,
        "target_refs": refs, "input_fingerprint": fingerprint,
        "checks": [{"check_id": check, "status": "pass",
                    "file_hash": file_hash,
                    "input_fingerprint": fingerprint,
                    "evidence_path": evidence,
                    "evidence_hash": evidence_hash} for check in checks]}
    extra = {}
    if companion:
        output["native_manifest"] = companion
        extra["companion_files"] = [companion]
    return core.adopt_output(root, output, project["revision"],
                             "adopt-" + rid, major_id="specialty_crops",
                             **extra)


def _economic_fixture(root):
    """경제 전공(specialty_crops) 7단계 전부 통과 — 산출물 검사까지 실제로 밟는다."""
    core = runtime("gg_core")
    base_fact = _seed(root, "specialty_crops")
    _add_finance_gate(root, base_fact)
    _set_formats(root, ["docx", "xlsx"])
    _add_section(root)

    workbook = _workbook_bytes()
    document = _document_bytes()
    native = {
        "status": "converted", "engine": "Microsoft Excel",
        "published_office_sha256": core.digest(workbook),
        "validation": {
            "workbook_profile": "school_17_sheet_v1",
            "finance_profile": "school_17_sheet_v1",
            "structure_issues": [], "school_issues": [],
            "excel": {"valid": True, "sha256": core.digest(workbook)}}}
    write_text(root, "out.native.json", json.dumps(native, ensure_ascii=False))
    native_path = "out.native.json"
    native_hash = core.digest((Path(root) / native_path).read_bytes())

    base_refs = _target_refs(root)
    workbook_hash = core.digest(workbook)
    content_report = _bind_semantic(root, "content", base_refs,
                                    "report-content.json", "rev-content",
                                    workbook_hash)
    _bind_semantic(root, "logic", base_refs, "report-logic.json", "rev-logic")
    _bind_semantic(root, "calculation", base_refs, "report-calculation.json",
                   "rev-calculation")

    _adopt(root, "out.xlsx", "xlsx", workbook,
           ("structure", "recalculation", "crosscheck", "render"),
           "out", "report-content.json", content_report,
           {"path": native_path, "sha256": native_hash})

    document_hash = core.digest(document)
    docx_refs = _target_refs(root)
    docx_report = _bind_semantic(root, "calculation", docx_refs,
                                 "report-docx.json", "rev-docx",
                                 document_hash)
    _adopt(root, "out.docx", "docx", document,
           ("structure", "font", "render"), "paper", "report-docx.json",
           docx_report)

    final_refs = _target_refs(root)
    for kind in ("docx", "xlsx", "render"):
        _bind_plain(root, kind, final_refs, "rev2-" + kind,
                    request_id="b2-" + kind)


def _text_fixture(root):
    """경제 사실 없이 본문·검토만 채운 7/7 — 2단계는 ``now``로 남는다."""
    core = runtime("gg_core")
    _seed(root, "specialty_crops")
    _set_formats(root, ["docx", "xlsx"])
    _add_section(root)

    write_text(root, "ev.md", "합성 검사 근거\n")
    evidence_hash = core.digest((Path(root) / "ev.md").read_bytes())
    workbook = _workbook_bytes("TEXT")
    document = _document_bytes("텍스트")
    _adopt(root, "out.xlsx", "xlsx", workbook,
           ("structure", "recalculation", "crosscheck", "render"),
           "out", "ev.md", evidence_hash)
    _adopt(root, "out.docx", "docx", document,
           ("structure", "font", "render"), "paper", "ev.md", evidence_hash)

    final_refs = _target_refs(root)
    for kind in REVIEW_KINDS:
        _bind_plain(root, kind, final_refs, "rev-" + kind)


def _progress(root):
    runtime("gg_progress").write_progress(root)
    return json.loads((Path(root) / ".knuaf-progress.json").read_text())


def _states(payload):
    return [step["state"] for step in payload["steps"]]


def _notes(payload):
    return {step["n"]: step.get("note") for step in payload["steps"]}


def _hits(body, terms):
    """학생화면 금지어 검사 — 대소문자 무시 부분일치."""
    lowered = body.lower()
    return [term for term in terms if term.lower() in lowered]


class EconomicCase(th.ContractCase):
    """경제 전공 7/7 픽스처를 매 시험마다 새 폴더에 세운다."""

    def setUp(self):
        self.root = self.make_project()
        _economic_fixture(self.root)
        self.payload = _progress(self.root)


class ProgressPayloadTests(EconomicCase):
    """§7-1 — 앱 계약(`progress-file.ts`)과 같은 모양."""

    def test_schema_and_timestamp(self):
        payload = self.payload
        self.assertEqual(payload["schema"], "knuaf.progress/1")
        written_at = payload["written_at"]
        self.assertIsInstance(written_at, str)
        self.assertRegex(written_at, WRITTEN_AT)
        self.assertTrue(written_at.endswith("+00:00"), written_at)

    def test_revision_version_and_pending(self):
        payload = self.payload
        self.assertIsInstance(payload["revision"], int)
        self.assertEqual(payload["revision"],
                         runtime("gg_core").load(self.root)["revision"])
        self.assertEqual(payload["skill_version"],
                         runtime("gg_update").format_version(
                             runtime("gg_update").read_version_file(
                                 SCRIPTS.parent / "version.json")))
        self.assertIsInstance(payload["review_pending"], bool)
        completion = runtime("gg_core").completion(
            self.root, runtime("gg_core").load(self.root))
        self.assertEqual(payload["review_pending"],
                         not completion["skill_ready"])

    def test_steps_shape(self):
        steps = self.payload["steps"]
        self.assertEqual(len(steps), STEP_COUNT)
        numbers = [step["n"] for step in steps]
        self.assertEqual(numbers, list(range(1, STEP_COUNT + 1)))
        self.assertEqual(sorted(set(numbers)), numbers)
        for step in steps:
            self.assertIn(step["state"], STATES)
        now_steps = [step for step in steps if step["state"] == "now"]
        self.assertLessEqual(len(now_steps), 1)
        if now_steps:
            earlier = [s for s in steps if s["n"] < now_steps[0]["n"]]
            self.assertTrue(all(s["state"] in {"done", "unknown"}
                                for s in earlier))
        else:
            self.assertTrue(all(step["state"] == "done" for step in steps))

    def test_text_budget(self):
        payload = self.payload
        texts = [step.get("note") for step in payload["steps"]
                 if step.get("note") is not None]
        texts += [flag["text"] for flag in payload["flags"]]
        texts.append(payload["after"]["text"])
        for text in texts:
            self.assertIsInstance(text, str)
            self.assertLessEqual(len(text), MAX_TEXT, text)

    def test_flags_and_after(self):
        payload = self.payload
        for flag in payload["flags"]:
            self.assertIsInstance(flag["text"], str)
            self.assertIsInstance(flag["needed_for"], int)
        self.assertEqual(payload["after"], AFTER)
        self.assertIsNone(payload["next"])
        self.assertIsNone(payload["ask"])


class StudentLanguageTests(EconomicCase):
    """§7-6 — 금지어·전공 표기."""

    def test_notes_have_no_forbidden_terms(self):
        payload = self.payload
        bodies = [step.get("note") or "" for step in payload["steps"]]
        bodies += [flag["text"] for flag in payload["flags"]]
        bodies.append(payload["after"]["text"])
        for body in bodies:
            self.assertEqual(_hits(body, FORBIDDEN_STUDENT_TERMS), [], body)

    def test_major_note_uses_known_label(self):
        note = _notes(self.payload)[1]
        self.assertEqual(note, "특용작물전공으로 등록됨")
        self.assertTrue(any(name in note for name in KNOWN_MAJOR_NAMES), note)
        self.assertTrue(note.endswith("으로 등록됨"), note)


class AtomicityTests(EconomicCase):
    """§7-7 — 두 번 써도 정본은 그대로, 임시 파일은 남지 않는다."""

    def test_rewrite_is_sidecar_only(self):
        core = runtime("gg_core")
        project_path = Path(self.root) / "project.json"
        before = project_path.read_bytes()
        first = json.loads(
            (Path(self.root) / ".knuaf-progress.json").read_text())
        cg = runtime("gg_progress")
        cg.write_progress(self.root)
        second = json.loads(
            (Path(self.root) / ".knuaf-progress.json").read_text())
        self.assertEqual(project_path.read_bytes(), before)
        self.assertEqual(list(Path(self.root).glob(".gg-tmp-*")), [])
        self.assertEqual(second["revision"], first["revision"])
        self.assertEqual(_states(second), _states(first))
        self.assertEqual(second["steps"], first["steps"])
        self.assertEqual(core.load(self.root)["revision"], first["revision"])


class SevenOfSevenTests(EconomicCase):
    """§7-2 — 전부 통과인 정본은 7 `done`, `now` 없음."""

    def test_all_steps_done(self):
        self.assertEqual(_states(self.payload), ["done"] * STEP_COUNT)
        self.assertEqual(_notes(self.payload), {
            1: "특용작물전공으로 등록됨",
            2: "재무 계획까지 정해짐",
            3: "본문 착수함",
            4: "본문 1개 절 초안 등록됨",
            5: "재무 엑셀 검사 통과",
            6: "검토 네 갈래 이상 통과",
            7: "워드 검토본 준비됨",
        })
        self.assertFalse([s for s in self.payload["steps"]
                          if s["state"] == "now"])

    def test_body_step_never_regresses(self):
        """3단계 `done`은 4~7 진행 중에도 `now`로 되돌아가지 않는다."""
        ladder = _ladder_states(self.make_project())
        for snapshot in ladder:
            self.assertEqual(len(snapshot), STEP_COUNT)
            if snapshot[2] == "done":
                self.assertNotEqual(snapshot[2], "now")


class UnsupportedMajorTests(th.ContractCase):
    """§7-3 — 재무 엑셀 미지원 전공은 5 `unknown`, 6은 검토만으로 `done`."""

    def _fixture(self, major):
        root = self.make_project()
        _seed(root, major)
        _add_section(root)
        return root

    def test_dairy_without_reviews(self):
        root = self._fixture("dairy")
        payload = _progress(root)
        self.assertEqual(_states(payload),
                         ["done", "unknown", "done", "done", "unknown",
                          "now", "todo"])
        notes = _notes(payload)
        self.assertEqual(notes[1], "낙농전공으로 등록됨")
        self.assertEqual(notes[2], "재무 계산을 할 수 없어요")
        self.assertEqual(notes[5], "이 전공은 재무 엑셀을 지원하지 않아요")
        self.assertEqual(notes[6], "남은 검토를 마저 해요")
        self.assertEqual(notes[7], "검토 뒤에 만들어요")
        finance = runtime("gg_core").finance_gate(
            runtime("gg_core").load(root))
        self.assertEqual(finance["state"], "not_computable")
        self.assertIs(finance["supported"], False)

    def test_dairy_with_six_reviews(self):
        root = self._fixture("dairy")
        refs = _target_refs(root)
        for kind in REVIEW_KINDS:
            _bind_plain(root, kind, refs, "rev-" + kind)
        payload = _progress(root)
        self.assertEqual(payload["steps"][5]["state"], "done")
        self.assertEqual(_notes(payload)[6], "검토 네 갈래 이상 통과")
        self.assertEqual(payload["steps"][6]["state"], "now")
        self.assertEqual(_notes(payload)[7], "워드 검토본을 만들어요")
        core = runtime("gg_core")
        rows = core.gate(root, core.load(root))
        blocked = [row for row in rows
                   if str(row.get("check_id", "")).startswith("review_")
                   and row.get("status") != "pass"]
        self.assertEqual(blocked, [])            # 막는 검토 행 0
        scope_rows = [row for row in rows
                      if str(row.get("check_id", "")).startswith("finance_")]
        self.assertTrue(scope_rows)              # 범위 행은 남아 있지만
        self.assertEqual({row.get("status") for row in scope_rows},
                         {"not_checked"})        # 막지는 않는다

    def test_industrial_insects(self):
        root = self._fixture("industrial_insects")
        payload = _progress(root)
        self.assertEqual(payload["steps"][4]["state"], "unknown")
        self.assertEqual(_notes(payload)[1], "산업곤충전공으로 등록됨")
        self.assertEqual(_notes(payload)[5],
                         "이 전공은 재무 엑셀을 지원하지 않아요")


def _native_manifest(core, workbook):
    return {
        "status": "converted", "engine": "Microsoft Excel",
        "published_office_sha256": core.digest(workbook),
        "validation": {
            "workbook_profile": "school_17_sheet_v1",
            "finance_profile": "school_17_sheet_v1",
            "structure_issues": [], "school_issues": [],
            "excel": {"valid": True, "sha256": core.digest(workbook)}}}


def _ladder_states(root):
    """경제 전공 사다리를 한 칸씩 올리며 각 칸의 7상태를 모은다(§7-4)."""
    core = runtime("gg_core")
    seen = []

    def snapshot():
        seen.append(_states(_progress(root)))

    snapshot()  # 초기화만 한 상태
    base_fact = _seed(root, "specialty_crops")
    snapshot()  # 전공 등록
    _add_finance_gate(root, base_fact)
    snapshot()  # 재무 게이트 사실
    _set_formats(root, ["docx", "xlsx"])
    _add_section(root)
    snapshot()  # 본문 1절

    refs = _target_refs(root)
    workbook = _workbook_bytes()
    workbook_hash = core.digest(workbook)
    content_report = None
    for kind in ("content", "logic", "calculation"):
        report = _bind_semantic(root, kind, refs, "report-%s.json" % kind,
                                "rev-" + kind,
                                workbook_hash if kind == "content" else None)
        if kind == "content":
            content_report = report
    snapshot()  # 의미 검토 3갈래 — 상태 변화 없음

    write_text(root, "out.native.json",
               json.dumps(_native_manifest(core, workbook),
                          ensure_ascii=False))
    native_path = "out.native.json"
    _adopt(root, "out.xlsx", "xlsx", workbook,
           ("structure", "recalculation", "crosscheck", "render"),
           "out", "report-content.json", content_report,
           {"path": native_path,
            "sha256": core.digest((Path(root) / native_path).read_bytes())})
    snapshot()  # 엑셀 채택 → 6 now

    final_refs = _target_refs(root)
    for kind in ("docx", "xlsx", "render"):
        _bind_plain(root, kind, final_refs, "rev2-" + kind,
                    request_id="b2-" + kind)
    snapshot()  # 검토 6갈래(의미 3 + 평문 3) → 7 now

    document = _document_bytes()
    document_hash = core.digest(document)
    docx_refs = _target_refs(root)
    docx_report = _bind_semantic(root, "calculation", docx_refs,
                                 "report-docx.json", "rev-docx",
                                 document_hash)
    _adopt(root, "out.docx", "docx", document,
           ("structure", "font", "render"), "paper", "report-docx.json",
           docx_report)
    snapshot()  # 워드 채택 → 7/7
    return seen


class PromotionTests(th.ContractCase):
    """§7-4 — 1~7 전수로 '마지막 done 다음 한 칸'만 `now`."""

    def test_ladder(self):
        root = self.make_project()
        ladder = _ladder_states(root)
        self.assertEqual(ladder, [
            ["now", "unknown", "todo", "todo", "unknown",
             "todo", "todo"],
            ["done", "now", "todo", "todo", "todo", "todo", "todo"],
            ["done", "done", "now", "todo", "todo", "todo", "todo"],
            ["done", "done", "done", "done", "now", "todo", "todo"],
            ["done", "done", "done", "done", "now", "todo", "todo"],
            ["done", "done", "done", "done", "done", "now", "todo"],
            ["done", "done", "done", "done", "done", "done", "now"],
            ["done", "done", "done", "done", "done", "done", "done"],
        ])

    def test_major_only_is_second_now(self):
        root = self.make_project()
        _seed(root, "specialty_crops")
        states = _states(_progress(root))
        self.assertEqual(states[0], "done")
        self.assertEqual(states[1], "now")
        self.assertEqual(states[2:], ["todo"] * 5)


class DamagedCanonTests(th.ContractCase):
    """§7-5 — 손상 정본은 4·6·7 `unknown`(조기 반환 두 갈래 각각)."""

    def _fixture(self):
        root = self.make_project()
        _seed(root, "specialty_crops")
        _add_section(root)
        return root

    def _damaged(self, mutate):
        core = runtime("gg_core")
        root = self._fixture()
        clean = _states(_progress(root))
        self.assertEqual(clean[3], "done")
        damaged = copy.deepcopy(core.load(root))
        mutate(damaged)
        write_text(root, "project.json",
                   json.dumps(damaged, ensure_ascii=False))
        payload = _progress(root)
        states = _states(payload)
        self.assertEqual(states[3], "unknown", states)
        self.assertEqual(states[5], "unknown", states)
        self.assertEqual(states[6], "unknown", states)
        self.assertEqual(states[0], "done")
        notes = _notes(payload)
        self.assertEqual(notes[4], "본문 기록을 다시 봐야 해요")
        self.assertEqual(notes[6], "검토 기록을 다시 봐야 해요")
        self.assertIsNone(notes.get(7))

    def test_source_claims_invalid(self):
        self._damaged(lambda p: p["sources"]["answer"].__setitem__("claims", []))

    def test_view_invalid(self):
        self._damaged(lambda p: p.__setitem__("views", []))


class ProgressLinkTests(th.ContractCase):
    """C-01 — 진행 파일 자리가 링크면 쓰기 거절, 지우기는 링크만.

    폴더 안 링크는 `core.local`이 끝까지 풀어 통과시키므로(`gg_core.py:54-59`)
    정본이 진행 문서로 덮이거나 지워질 수 있다. 두 함수 모두 링크를 풀기
    전 경로로 판정한다.
    """

    def _linked(self, target="project.json", name=".knuaf-progress.json"):
        root = self.make_project()
        _seed(root, "specialty_crops")
        _add_section(root)
        write_text(root, "memo.txt", "건드리면 안 되는 내부 파일\n")
        link = Path(root) / name
        link.symlink_to(target)
        before = {rel: (Path(root) / rel).read_bytes()
                  for rel in ("project.json", "memo.txt")}
        return root, link, before

    def test_write_refuses_internal_link(self):
        root, link, before = self._linked()
        cg = runtime("gg_progress")
        with self.assertRaises(cg.ProgressLinkError):
            cg.write_progress(root)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (Path(root) / "project.json").resolve())
        for rel, data in before.items():
            self.assertTrue((Path(root) / rel).is_file(), rel)
            self.assertEqual((Path(root) / rel).read_bytes(), data, rel)
        self.assertEqual(list(Path(root).glob(".gg-tmp-*")), [])

    def test_write_refuses_second_internal_link(self):
        root, link, before = self._linked(target="memo.txt")
        cg = runtime("gg_progress")
        with self.assertRaises(cg.ProgressLinkError):
            cg.write_progress(root)
        self.assertEqual(link.readlink(), Path("memo.txt"))
        for rel, data in before.items():
            self.assertTrue((Path(root) / rel).is_file(), rel)
            self.assertEqual((Path(root) / rel).read_bytes(), data, rel)

    def test_write_refuses_outside_link(self):
        root = self.make_project()
        outside = Path(root).parent / "outside-progress.json"
        outside.write_text("밖\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (Path(root) / ".knuaf-progress.json").symlink_to(outside)
        with self.assertRaises(runtime("gg_progress").ProgressLinkError):
            runtime("gg_progress").write_progress(root)
        self.assertEqual(outside.read_text(encoding="utf-8"), "밖\n")

    def test_remove_drops_only_the_link(self):
        root, link, before = self._linked()
        self.assertIs(runtime("gg_progress").remove_progress(root), True)
        self.assertFalse(link.exists())
        self.assertFalse(link.is_symlink())
        for rel, data in before.items():
            self.assertTrue((Path(root) / rel).is_file(), rel)
            self.assertEqual((Path(root) / rel).read_bytes(), data, rel)

    def test_remove_second_internal_link(self):
        root, link, before = self._linked(target="memo.txt")
        self.assertIs(runtime("gg_progress").remove_progress(root), True)
        self.assertFalse(link.is_symlink())
        for rel, data in before.items():
            self.assertTrue((Path(root) / rel).is_file(), rel)
            self.assertEqual((Path(root) / rel).read_bytes(), data, rel)

    def test_remove_leaves_canon_loadable(self):
        root, _, _ = self._linked()
        runtime("gg_progress").remove_progress(root)
        project = runtime("gg_core").load(root)
        self.assertIn("revision", project)
        self.assertEqual(runtime("gg_progress").remove_progress(root), True)


class MajorNoteTests(th.ContractCase):
    """I-01 — 1단계 문구는 등록 결과 세 갈래를 구분한다."""

    def test_unregistered_prompts_for_major(self):
        root = self.make_project()
        payload = _progress(root)
        self.assertEqual(payload["steps"][0]["state"], "now")
        self.assertEqual(_notes(payload)[1], "전공을 골라 주세요")

    def test_invalid_binding_prompts_for_major(self):
        core = runtime("gg_core")
        mc = runtime("gg_major_contract")
        root = self.make_project()
        write_text(root, "major-answer.txt", "전공 선택: specialty_crops\n")
        fact = fact_op("selected_major", "common.major_id", "specialty_crops", "",
                       scope="project", verification="source_located",
                       source_id="major-answer", claim_id="major")
        fact["value"]["module_version"] = (
            mc.default_registry().resolve("specialty_crops").module_version)
        source = source_op("major-answer.txt",
                           claims=claim_of(fact["value"], "major"))
        source["value"]["id"] = "major-answer"
        core.apply(root, {"request_id": "bad-major", "ops": [source, fact]}, 0)
        with self.assertRaises(mc.BindingInvalidError):
            mc.binding_from_project(mc.default_registry(), core.load(root))
        payload = _progress(root)
        self.assertEqual(payload["steps"][0]["state"], "now")
        self.assertEqual(_notes(payload)[1], "전공을 골라 주세요")

    def _unreadable_alias(self):
        cg = runtime("gg_progress")
        original = cg.major_label

        def _broken(_major_id):
            return None

        cg.major_label = _broken
        self.addCleanup(setattr, cg, "major_label", original)

    def test_alias_failure_keeps_done_state_note(self):
        root = self.make_project()
        _seed(root, "specialty_crops")
        self._unreadable_alias()
        payload = _progress(root)
        self.assertEqual(payload["steps"][0]["state"], "done")
        self.assertEqual(_notes(payload)[1], "전공 등록됨")

    def test_alias_failure_on_later_step_still_registered(self):
        root = self.make_project()
        _seed(root, "specialty_crops")
        _add_section(root)
        self._unreadable_alias()
        notes = _notes(_progress(root))
        self.assertEqual(notes[1], "전공 등록됨")
        self.assertEqual(notes[3], "본문 착수함")


class ShowCommandTests(th.ContractCase):
    """§7-11 — `show`는 읽기만 한다(없음/못 읽음/형식 오류/정상)."""

    def _run(self, root, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "show", *args],
            capture_output=True, text=True, cwd=str(root))

    def test_with_file(self):
        root = self.make_project()
        _text_fixture(root)
        _progress(root)
        target = Path(root) / ".knuaf-progress.json"
        before_bytes = target.read_bytes()
        before_mtime = target.stat().st_mtime_ns
        proc = self._run(root, str(root))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(set(summary),
                         {"path", "revision", "written_at", "steps", "now"})
        self.assertEqual(summary["steps"], STEP_COUNT)
        self.assertEqual(summary["revision"],
                         runtime("gg_core").load(root)["revision"])
        self.assertEqual(target.read_bytes(), before_bytes)
        self.assertEqual(target.stat().st_mtime_ns, before_mtime)

    def test_without_file(self):
        root = self.make_project()
        proc = self._run(root, str(root))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("진행 파일 없음: "), proc.stderr)
        self.assertFalse((Path(root) / ".knuaf-progress.json").exists())

    def test_corrupt_json(self):
        root = self.make_project()
        write_text(root, ".knuaf-progress.json", "{oops")
        proc = self._run(root, str(root))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(
            proc.stderr.startswith("진행 파일을 읽을 수 없음: "), proc.stderr)
        self.assertEqual(
            (Path(root) / ".knuaf-progress.json").read_text(), "{oops")

    def test_non_object_json(self):
        root = self.make_project()
        write_text(root, ".knuaf-progress.json", "[1,2]")
        proc = self._run(root, str(root))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(
            proc.stderr.startswith("진행 파일 형식 오류: "), proc.stderr)

    def _broken(self, mutate):
        """정상 파일 하나를 만들어 변형한 뒤 `show` 결과를 돌려준다."""
        root = self.make_project()
        _seed(root, "specialty_crops")
        payload = _progress(root)
        mutate(payload)
        write_text(root, ".knuaf-progress.json",
                   json.dumps(payload, ensure_ascii=False) + "\n")
        target = Path(root) / ".knuaf-progress.json"
        before = target.read_bytes()
        proc = self._run(root, str(root))
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(
            proc.stderr.startswith("진행 파일 형식 오류: "), proc.stderr)
        self.assertEqual(target.read_bytes(), before)   # 고치지 않는다
        return proc

    def test_empty_object_rejected(self):
        self._broken(lambda payload: payload.clear())

    def test_wrong_schema_rejected(self):
        self._broken(lambda payload: payload.update(schema="knuaf.progress/2"))

    def test_short_steps_rejected(self):
        self._broken(lambda payload: payload["steps"].pop())

    def test_duplicate_step_number_rejected(self):
        def mutate(payload):
            payload["steps"][1]["n"] = payload["steps"][0]["n"]

        self._broken(mutate)

    def test_bad_state_rejected(self):
        self._broken(lambda payload: payload["steps"][0].update(state="blocked"))

    def test_bad_step_number_rejected(self):
        self._broken(lambda payload: payload["steps"][0].update(n=8))


class NoWriteCommandTests(th.ContractCase):
    """§7-13 — `write` 서브커맨드는 잘린 채로 유지된다(C4)."""

    def test_write_is_argument_error(self):
        root = self.make_project()
        absent = Path(root) / "새폴더"
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "write", str(absent)],
            capture_output=True, text=True, cwd=str(root))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid choice: 'write'", proc.stderr)
        self.assertIn("choose from", proc.stderr)
        self.assertIn("show", proc.stderr)
        self.assertNotIn("write", proc.stderr.split("choose from", 1)[1])
        self.assertFalse(absent.exists())
        self.assertFalse((Path(root) / ".knuaf-progress.json").exists())

    def test_show_requires_target(self):
        root = self.make_project()
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "show"],
            capture_output=True, text=True, cwd=str(root))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("required", proc.stderr)


class RemoveProgressTests(th.ContractCase):
    """`remove_progress` — 실패는 감지 가능하고 예외를 안 낸다(I3)."""

    def test_missing_file_is_success(self):
        root = self.make_project()
        self.assertIs(runtime("gg_progress").remove_progress(root), True)

    def test_directory_in_place_falls_back_to_empty_object(self):
        root = self.make_project()
        blocked = Path(root) / ".knuaf-progress.json"
        blocked.mkdir()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = runtime("gg_progress").remove_progress(root)
        self.assertIs(result, False)
        self.assertTrue(stderr.getvalue().startswith("진행 파일을 지우지 못함: "),
                        stderr.getvalue())
        self.assertTrue(blocked.is_dir())


class HwpFlagTests(th.ContractCase):
    """한글 서식이 걸린 정본만 `flags`를 채운다."""

    def _flagged(self, formats):
        root = self.make_project()
        _seed(root, "specialty_crops")
        _set_formats(root, formats)
        _add_section(root)
        return _progress(root)

    def test_hwp_format_sets_flag(self):
        payload = self._flagged(["hwp"])
        self.assertEqual(payload["flags"],
                         [{"text": "한글 파일로 마지막 저장", "needed_for": 7}])

    def test_docx_format_has_no_flag(self):
        payload = self._flagged(["docx", "xlsx"])
        self.assertEqual(payload["flags"], [])


class MajorLabelTests(th.ContractCase):
    """`major_label`은 배포 별칭 파일을 읽는다(사전 복제 금지)."""

    def test_known_majors(self):
        label = runtime("gg_progress").major_label
        self.assertEqual(label("specialty_crops"), "특용작물전공")
        self.assertEqual(label("dairy"), "낙농전공")
        self.assertEqual(label("industrial_insects"), "산업곤충전공")

    def test_unknown_majors(self):
        label = runtime("gg_progress").major_label
        self.assertIsNone(label("없는전공"))
        self.assertIsNone(label(None))
        self.assertIsNone(label(""))


class TextOnlySevenTests(th.ContractCase):
    """경제 사실 없이도 7/7 모양·2단계 `now`가 성립한다(보조 픽스처)."""

    def test_states(self):
        root = self.make_project()
        _text_fixture(root)
        payload = _progress(root)
        self.assertEqual(_states(payload),
                         ["done", "now", "done", "done", "done", "done", "done"])
        self.assertEqual(_notes(payload)[2], "재무 계획을 마저 정해요")
        self.assertEqual(payload["flags"], [])
        rows = runtime("gg_core").gate(root, runtime("gg_core").load(root))
        finance_rows = [row for row in rows
                        if row.get("check_id") == "finance_gate"]
        self.assertEqual([row.get("status") for row in finance_rows], ["fail"])


if __name__ == "__main__":
    unittest.main()
