#!/usr/bin/env python3
"""학생이 바꾼 계산 칸: 비교, 확인 주기, 알림의 단일 정본."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PHRASES = {
    "title_shown": "「{label}」에서 원래 자동으로 계산되던 칸 {count}곳이 바뀌어 있어요. 이번에는 만들지 않았어요.",
    "title_acknowledged": "「{label}」의 바뀐 계산 칸 {count}곳은 확인해 주셨어요.",
    "title_completed": "「{label}」의 바뀐 계산 칸 {count}곳을 그대로 두고 사본을 만들었어요.",
    "title_inherited": "「{label}」의 바뀐 계산 칸 {count}곳은 앞에서 확인해 주신 그대로예요.",
    "what_value": "원래는 자동으로 계산되는 칸인데, 숫자가 직접 들어 있어요.",
    "what_formula": "원래 계산식과 다른 식이 들어 있어요.",
    "what_empty": "원래 있던 계산식이 지워져 있어요.",
    "how": "실수였다면 이 칸에 원래 식을 다시 넣어 주세요: {formula}",
    "how_no_formula": "학교에서 받은 원본 양식의 같은 칸과 비교해 주세요.",
    "conflict": "「{label}」의 {cells} 칸은 도우미가 계산을 바로잡는 자리인데 내용이 달라져 있어서, 바로잡은 사본은 만들 수 없어요. 학교 양식의 원래 식으로 되돌린 뒤 다시 요청해 주세요. 빈 사본은 지금도 만들 수 있어요.",
    "audit_unavailable": "도우미 쪽 준비가 덜 되어 이 양식의 계산 칸을 확인할 수 없어요. 이번에는 만들지 않았어요. 파일 문제가 아니에요.",
    "notice_stale": "알림을 본 뒤에 파일이 바뀌었어요. 다시 요청해 주시면 지금 상태로 다시 알려 드릴게요.",
    "record_unwritable": "지금은 기록을 남길 수 없어 만들지 않았어요. 잠시 뒤 다시 요청해 주세요.",
    "stopped_app": "원래 자동으로 계산되던 칸이 바뀌어 있어서 이번에는 만들지 않았어요. 일부러 바꾼 거라면 같은 단추를 한 번 더 눌러 주세요.",
    "stopped_chat": "원래 자동으로 계산되던 칸이 바뀌어 있어서 이번에는 만들지 않았어요. 일부러 바꾼 거라면 알림의 확인 단추를 눌러 주세요.",
}
PREFIX = "finance.workbook.formula_cells."
CYCLE_STATES = {"shown", "acknowledged", "completed"}
PROVENANCE_PREFIX = "finance.workbook.tool_output."
PROVENANCE_SCHEMA = "knuaf-formula-tool-output/v1"


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _formula(text):
    return " ".join(str(text or "").strip().removeprefix("=").split())


def texts(row_state, *, label, count, inherited=False, cells=None, formulas=None):
    key = "title_inherited" if inherited and row_state == "acknowledged" else "title_" + row_state
    items = []
    formulas = formulas or {}
    for cell in cells or []:
        address = cell["sheet"] + "!" + cell["cell"]
        formula = formulas.get((cell["sheet"], cell["cell"]), formulas.get(address))
        items.append({"key": address, "kind": "other",
                      "what": PHRASES["what_" + cell["kind"]], "cells": [address],
                      "how": PHRASES["how"].format(formula=formula) if formula else PHRASES["how_no_formula"]})
    if len(items) > 50:
        items = items[:50] + [{"key": "more", "kind": "other",
                              "what": f"외 {len(items) - 50}곳", "cells": []}]
    return {"title": PHRASES[key].format(label=label, count=count), "items": items}


def detect_changes(read, audit, *, patched_formulas=None, input_sha256=None,
                   registered_receipts=None):
    from gg_excel_template import OBSERVATION_BLOCK, expand_ref
    sheet, refs = OBSERVATION_BLOCK
    excluded = {(sheet, cell) for cell in expand_ref(refs)}
    for receipt in registered_receipts or []:
        if input_sha256 and receipt.get("output", {}).get("sha256") == input_sha256:
            excluded.update((p["sheet"], p["cell"]) for p in receipt.get("patched", []))
    patched_formulas = patched_formulas or {}
    changes = []
    for row in audit["inventory"]:
        key = (row["sheet"], row["cell"])
        if key in excluded:
            continue
        rec = read.cells.get(key, {})
        if rec.get("kind") == "f":
            current = _formula(rec.get("resolved"))
            if current == _formula(row["formula"]):
                continue
            if key in patched_formulas and current == _formula(patched_formulas[key]):
                continue
            kind, fingerprint = "formula", _hash(current.encode("utf-8"))
        else:
            kind = "empty" if rec.get("value") in (None, "") else "value"
            fingerprint = ""
        changes.append({"sheet": key[0], "cell": key[1], "kind": kind, "content_fp": fingerprint})
    return sorted(changes, key=lambda item: (item["sheet"], item["cell"]))


def change_id(project_id, audit_ref, audit_sha256, changes):
    return _hash(_canonical({"project_id": project_id, "kind": "formula_cells_changed",
                             "profile": audit_ref, "audit_sha256": audit_sha256, "changes": changes}))


def lineage_key(*, input_sha256, fact=None):
    return f"reg:{fact['id']}@{fact.get('revision') or 0}" if fact else "file:" + input_sha256


def notice_identity(lineage_key, change_id, seq):
    return _hash(_canonical({"lineage_key": lineage_key, "change_id": change_id, "seq": seq}))


def lineage_record_id(lineage_key, seq):
    return PREFIX + _hash(lineage_key.encode("utf-8"))[:32] + f".s{seq}"


def _records(project):
    return [r for key, r in project.get("questions", {}).items() if key.startswith(PREFIX)]


def _active(project, lineage):
    return max((r for r in _records(project) if r.get("lineage_key") == lineage),
               key=lambda r: r["seq"], default=None)


def _lineage(project, root, input_sha256, input_path):
    facts = [f for f in project.get("facts", {}).values()
             if f.get("field_id") == "intake.current_finance"]
    fact = max(facts, key=lambda f: (f.get("revision") or 0, str(f.get("id"))), default=None)
    if fact:
        value = fact.get("value")
        path = fact.get("path")
        if not isinstance(path, str) or not path:
            path = value.get("path") if isinstance(value, dict) else value
        if isinstance(path, str) and path:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(root) / candidate
            if candidate.resolve() == Path(input_path).resolve():
                return lineage_key(input_sha256=input_sha256, fact=fact), None
    matches = [r for r in _records(project) if r.get("state") == "completed"
               and any(o.get("sha256") == input_sha256 for o in r.get("outputs", []))]
    inherited = (max(matches, key=lambda r: r["seq"])
                 if len({r["lineage_key"] for r in matches}) == 1 else None)
    return lineage_key(input_sha256=input_sha256), inherited


def resolve_lineage(root, *, input_sha256, input_path, project_id=None):
    import gg_core as core
    lineage, _ = _lineage(core.load(root), root, input_sha256, input_path)
    return lineage


def _verdict(lineage, changes, record=None, *, reason=None, written=False):
    record = record or {}
    return {"verdict": "stop" if reason else "proceed", "reason": reason,
            "lineage_key": lineage, "change_id": record.get("change_id"),
            "seq": record.get("seq"), "record_id": record.get("id"),
            "notice_identity": record.get("notice_identity"), "state": record.get("state"),
            "acknowledged_by": record.get("acknowledged_by"),
            "inherited_from": record.get("inherited_from"),
            "cells": changes, "written": written}


def _write(root, project, record, transition):
    import gg_core as core
    lk = _hash(record["lineage_key"].encode("utf-8"))[:32]
    change = {"request_id": f"fcn:{lk}:s{record['seq']}:{transition}",
              "ops": [{"collection": "questions", "value": record}]}
    try:
        result = core.apply(root, change, project["revision"])
    except core.OperationError as error:
        if error.result.get("commit_state") != "committed_cleanup_pending":
            raise
        current = core.load(root)
        saved = current.get("questions", {}).get(record["id"], {})
        request = current.get("requests", {}).get(change["request_id"], {})
        if (request.get("hash") != core.digest(change)
                or not all(saved.get(key) == value for key, value in record.items() if key != "revision")):
            raise
        return {"reason": error.result.get("reason"),
                "cleanup_errors": error.result.get("cleanup_errors", [])}
    if isinstance(result, dict) and result.get("commit_state") not in (None, "committed", "committed_cleanup_pending"):
        raise ValueError(result.get("reason") or "record_unwritable")


def _receipt_evidence(receipts):
    return [{"output": {"sha256": r.get("output", {}).get("sha256")},
             "patched": [{"sheet": p["sheet"], "cell": p["cell"]} for p in r.get("patched", [])]}
            for r in receipts or []]


def evaluate(root, *, input_bytes, input_path, audit_ref, audit_sha256, changes,
             kind="patch", conflict_cells=None, context=None,
             patched_formulas=None, registered_receipts=None, formulas=None):
    """판정표의 유일한 구현. 개정 충돌 때만 정본을 다시 읽어 한 번 재적용."""
    import gg_core as core
    changes = sorted(changes, key=lambda c: (c["sheet"], c["cell"]))
    digest = _hash(input_bytes)
    lineage = lineage_key(input_sha256=digest)
    if conflict_cells:
        result = _verdict(lineage, changes, reason="formula_cell_conflict")
        result["conflict_cells"] = conflict_cells
        return result
    if root is None:
        return _verdict(lineage, changes, reason="formula_cell_notice_record" if changes else None)
    for _ in range(2):
        try:
            project = core.load(root)
            lineage, inherited = _lineage(project, root, digest, input_path)
            active = _active(project, lineage)
            cid = change_id(core._advice_project_id(root), audit_ref, audit_sha256, changes) if changes else None
            if not changes and (not active or active["state"] == "reverted"):
                return _verdict(lineage, changes)
            if changes and active and active["state"] in CYCLE_STATES and active["change_id"] == cid:
                return _verdict(lineage, changes, active,
                                reason="formula_cells_changed" if active["state"] == "shown" else None)
            seq = active["seq"] + 1 if active else 1
            rid = lineage_record_id(lineage, seq)
            existing = project.get("questions", {}).get(rid)
            if existing:
                if existing.get("lineage_key") != lineage:
                    return _verdict(lineage, changes, reason="lineage_id_collision")
                continue
            record = {"id": rid, "field_id": rid, "attempts": 1, "decision_revision": 1,
                      "lineage_key": lineage, "seq": seq, "state": "reverted"}
            transition = "reverted"
            if changes:
                inherit = (not active or active["state"] == "reverted") and inherited and inherited.get("change_id") == cid
                transition = "inherited" if inherit else "shown"
                record.update({"state": "acknowledged" if inherit else "shown", "attempts": 2 if inherit else 1,
                               "change_id": cid, "notice_identity": notice_identity(lineage, cid, seq),
                               "profile": audit_ref, "audit_sha256": audit_sha256, "cells": changes,
                               "workbook_path": str(Path(input_path).resolve()), "workbook_label": Path(input_path).name,
                               "acknowledged_by": "inherited" if inherit else None, "relayed_quote": None,
                               "inherited_from": inherited["id"] if inherit else None,
                               "confirmed_sha256": digest if inherit else None, "outputs": [], "kind": kind,
                               "patched_formulas": [{"sheet": s, "cell": c, "new_formula": f}
                                                    for (s, c), f in sorted((patched_formulas or {}).items())],
                               "registered_receipts": _receipt_evidence(registered_receipts)})
                addresses = {c["sheet"] + "!" + c["cell"] for c in changes}
                record["formulas"] = {address: formula for address, formula in (formulas or {}).items()
                                      if address in addresses}
            _write(root, project, record, transition)
            return _verdict(lineage, changes, record, written=True,
                            reason="formula_cells_changed" if record["state"] == "shown" else None)
        except (OSError, ValueError, KeyError):
            continue
    return _verdict(lineage, changes, reason="formula_cell_notice_record")


def _matching_record(project, verdict):
    record = project.get("questions", {}).get(verdict.get("record_id"))
    if not record:
        raise ValueError("record_not_active")
    if record.get("lineage_key") != verdict.get("lineage_key"):
        raise ValueError("lineage_id_collision")
    if (record.get("seq"), record.get("change_id")) != (verdict.get("seq"), verdict.get("change_id")):
        raise ValueError("record_not_active")
    if _active(project, record["lineage_key"])["id"] != record["id"]:
        raise ValueError("record_not_active")
    return record


def finish(root, verdict, *, output_sha256, receipt_sha256):
    import gg_core as core
    if not verdict or verdict.get("state") not in {"acknowledged", "completed"}:
        return {"completed": False, "reason": None}
    error = "record_unwritable"
    for _ in range(2):
        try:
            project = core.load(root)
            record = _matching_record(project, verdict)
            if record["state"] not in {"acknowledged", "completed"}:
                raise ValueError("record_not_active")
            if any(o["sha256"] == output_sha256 for o in record["outputs"]):
                return {"completed": True, "reason": None}
            updated = {**record, "state": "completed", "attempts": 3,
                       "outputs": record["outputs"] + [{"sha256": output_sha256, "receipt_sha256": receipt_sha256}]}
            transition = "done" if record["state"] == "acknowledged" else "out:" + output_sha256[:32]
            _write(root, project, updated, transition)
            return {"completed": True, "reason": None}
        except (OSError, ValueError, KeyError) as exc:
            error = str(exc)
    return {"completed": False, "reason": error}


def confirm(root, identity, *, relayed_quote=None, by=None):
    import gg_core as core
    def reject(reason):
        return {"status": "rejected", "reason": reason, "record_id": None,
                "acknowledged_by": None, "inherited_from": None}
    if by is None and relayed_quote is not None:
        by = "relayed"
    if by is None:
        return reject("confirm_source_missing")
    if by not in {"app_click", "relayed"} or (by == "app_click" and relayed_quote is not None):
        return reject("confirm_source_invalid")
    if by == "relayed" and (not isinstance(relayed_quote, str) or not relayed_quote.strip()):
        return reject("relayed_quote_missing")
    for _ in range(2):
        try:
            project = core.load(root)
            record = next((r for r in _records(project) if r.get("notice_identity") == identity), None)
            if record is None:
                return reject("notice_identity_unknown")
            if record["state"] != "shown" or _active(project, record["lineage_key"])["id"] != record["id"]:
                return reject("record_not_active")
            if lineage_record_id(record["lineage_key"], record["seq"]) != record["id"]:
                return reject("lineage_id_collision")
            try:
                raw = Path(record["workbook_path"]).read_bytes()
                digest = _hash(raw)
                lineage, _ = _lineage(project, root, digest, record["workbook_path"])
                changes, _, audit_sha = inspect_snapshot(
                    raw, record["profile"], patched_formulas={
                        (p["sheet"], p["cell"]): p["new_formula"] for p in record.get("patched_formulas", [])},
                    registered_receipts=record.get("registered_receipts", []))
                cid = change_id(core._advice_project_id(root), record["profile"], audit_sha, changes) if changes else None
                if lineage != record["lineage_key"] or cid != record["change_id"] or audit_sha != record["audit_sha256"]:
                    return reject("notice_stale")
            except (OSError, ValueError, KeyError):
                return reject("notice_stale")
            updated = {**record, "state": "acknowledged", "attempts": 2,
                       "confirmed_sha256": digest, "acknowledged_by": by,
                       "relayed_quote": relayed_quote.strip() if by == "relayed" else None}
            warning = _write(root, project, updated, "ack")
            response = {"status": "acknowledged", "reason": None, "record_id": record["id"],
                        "acknowledged_by": by, "inherited_from": record.get("inherited_from"),
                        "notice_identity": identity}
            if warning is not None:
                response["cleanup_warning"] = warning
            return response
        except (OSError, ValueError, KeyError):
            continue
    return reject("record_unwritable")


def notice_rows(root, project=None):
    import gg_core as core
    project = core.load(root) if project is None else project
    rows = []
    for lineage in sorted({r["lineage_key"] for r in _records(project)}):
        record = _active(project, lineage)
        if record["state"] not in CYCLE_STATES:
            continue
        row = {"check_id": "finance_formula_cell_notice", "target": "xlsx", "status": "check_needed",
               "severity": "info", "required_for": [], "owner": "skill",
               "notice_identity": record["notice_identity"], "project_id": core._advice_project_id(root),
               "dismissible": True, "redisplay_rule": "identity_changed", "receipt_path": None,
               "record_refs": [{"collection": "questions", "id": record["id"]}]}
        row.update(texts(record["state"], label=record["workbook_label"], count=len(record["cells"]),
                         inherited=record.get("acknowledged_by") == "inherited",
                         cells=record["cells"], formulas=record.get("formulas")))
        if record["state"] == "shown":
            row["confirm"] = {"label": "확인했어요, 그대로 만들어 주세요"}
        rows.append(row)
    return rows


def snapshot(input_bytes):
    """경로 전용 XLSX API에 전달할 실행 한 번의 원본. 예외에도 회수한다."""
    from contextlib import contextmanager
    import tempfile
    @contextmanager
    def opened():
        with tempfile.TemporaryDirectory(prefix="knuaf-formula-notice-") as directory:
            path = Path(directory) / "source.xlsx"
            path.write_bytes(input_bytes)
            yield path
    return opened()


def inspect_snapshot(input_bytes, audit_ref, *, patched_formulas=None, registered_receipts=None):
    from gg_excel_template import _crop_label_table, registered_audit, read_template
    binding = _crop_label_table()["templates"][audit_ref]
    audit = registered_audit(binding)
    with snapshot(input_bytes) as path:
        read = read_template(path)
        changes = detect_changes(read, audit, patched_formulas=patched_formulas,
                                 input_sha256=_hash(input_bytes), registered_receipts=registered_receipts)
    return changes, audit, binding["audit_sha256"]


def registered_receipts(root):
    """해시 고정 영수증 및 도구가 직접 남긴 출력 근거만 읽는다."""
    import gg_core as core
    if root is None:
        return []
    receipts = []
    project = core.load(root)
    for record in project.get("questions", {}).values():
        if (record.get("id", "").startswith(PROVENANCE_PREFIX)
                and record.get("schema") == PROVENANCE_SCHEMA):
            receipts.append({"output": {"sha256": record["output_sha256"]},
                             "patched": record["patched"]})
    for output in project.get("outputs", {}).values():
        pointer = output.get("template_receipt")
        if not isinstance(pointer, dict):
            continue
        try:
            raw = core.local(root, pointer["path"]).read_bytes()
            if _hash(raw) != pointer["sha256"]:
                continue
            receipt = json.loads(raw)
            if receipt.get("schema") == "gg-xlsx-formula-patch-receipt/v1":
                receipts.append(receipt)
        except (OSError, ValueError, KeyError):
            continue
    return receipts


def is_school_snapshot(path):
    """기존 ZIP 시트 목록만 비교. 비대상에 새 패키지 검증을 요구하지 않는다."""
    import zipfile
    from gg_excel_template import workbook_sheets
    from gg_school_excel import SCHOOL_SHEETS
    try:
        with zipfile.ZipFile(path) as archive:
            return [name for name, _ in workbook_sheets(archive)] == list(SCHOOL_SHEETS)
    except zipfile.BadZipFile:
        # Scope probing must not replace the existing map/source error order.
        return False


def register_output(root, receipt, output_path, *, input_sha256):
    """출력 직후의 수정 근거. 완료/확인/제출 승인이 아닌 별도 불변 기록."""
    if root is None:
        return
    import gg_core as core
    import zipfile
    from gg_excel_template import workbook_sheets, read_template
    from gg_school_excel import SCHOOL_SHEETS
    try:
        with zipfile.ZipFile(output_path) as archive:
            if [name for name, _ in workbook_sheets(archive)] != list(SCHOOL_SHEETS):
                return
        output_sha = _hash(Path(output_path).read_bytes())
        if output_sha != receipt["output"]["sha256"]:
            raise ValueError("output_hash_mismatch")
        # Carry only previously proved tool edits, never the student's
        # acknowledged changes. A blank copy must not launder those changes.
        keys = {(p["sheet"], p["cell"]) for r in registered_receipts(root)
                if r.get("output", {}).get("sha256") == input_sha256
                for p in r.get("patched", [])}
        keys.update((p["sheet"], p["cell"]) for p in receipt.get("patched", []))
        keys.update((p["sheet"], p["cell"]) for p in receipt.get("cleared", []))
        patched = []
        if keys:
            with snapshot(Path(output_path).read_bytes()) as readable:
                read = read_template(readable)
            for sheet, cell in sorted(keys):
                current = read.cells.get((sheet, cell), {})
                patched.append({"sheet": sheet, "cell": cell,
                                "new_formula": current["resolved"] if current.get("kind") == "f" else None})
        evidence = {"schema": PROVENANCE_SCHEMA, "output_sha256": output_sha, "patched": patched}
        fingerprint = _hash(_canonical(evidence))
        rid = PROVENANCE_PREFIX + output_sha + "." + fingerprint
        record = {"id": rid, "field_id": rid, "attempts": 1, "decision_revision": 1, **evidence}
        for attempt in range(2):
            project = core.load(root)
            existing = project.get("questions", {}).get(rid)
            if existing:
                if all(existing.get(key) == value for key, value in record.items()):
                    return
                raise ValueError("tool_output_id_collision")
            try:
                core.apply(root, {"request_id": "fcn-output:" + fingerprint,
                                  "ops": [{"collection": "questions", "value": record}]}, project["revision"])
                return
            except (OSError, ValueError):
                if attempt:
                    raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        # Publication has succeeded. Preserve the copy and disclose that its
        # provenance could not be registered; do not grant an exemption.
        receipt["formula_cell_provenance_error"] = str(error)


def context_root(context):
    return context.get("project_root") if isinstance(context, dict) else getattr(context, "project_root", None)


def prepare(root, *, input_bytes, input_path, snapshot_path, profile, kind, patches=None):
    """기존 판·구조 승인 뒤 부른다. 다른 양식은 아무 부수 효과도 없다."""
    import gg_major_contract as mc
    from gg_excel_template import read_template, _crop_label_table, registered_audit
    if not is_school_snapshot(snapshot_path):
        return None
    read = read_template(snapshot_path)
    ref = "school_17_sheet_v2" if profile == "school_17_sheet_v2" else ("X02" if read.layout == "x02" else "X01")
    try:
        binding = _crop_label_table()["templates"][ref]
        audit = registered_audit(binding)
    except (OSError, ValueError, KeyError) as exc:
        raise mc.OutputHeldError("formula_audit_unavailable", guidance=PHRASES["audit_unavailable"]) from exc
    patched = {(p["sheet"], p["cell"].replace("$", "").upper()): p["new_formula"]
               for p in patches or [] if p.get("new_formula")}
    receipts = registered_receipts(root)
    changes = detect_changes(read, audit, patched_formulas=patched, input_sha256=_hash(input_bytes),
                             registered_receipts=receipts)
    targets = {(p["sheet"], p["cell"].replace("$", "").upper()) for p in patches or []}
    conflicts = [c["sheet"] + "!" + c["cell"] for c in changes if (c["sheet"], c["cell"]) in targets] if kind == "patch" else None
    verdict = evaluate(root, input_bytes=input_bytes, input_path=input_path, audit_ref=ref,
                       audit_sha256=binding["audit_sha256"], changes=changes, kind=kind,
                       conflict_cells=conflicts, patched_formulas=patched, registered_receipts=receipts,
                       formulas={r["sheet"] + "!" + r["cell"]: r["formula"] for r in audit["inventory"]})
    if verdict["verdict"] == "stop":
        guidance = (PHRASES["conflict"].format(label=Path(input_path).name, cells=", ".join(conflicts))
                    if conflicts else PHRASES["stopped_chat"] if verdict["reason"] == "formula_cells_changed"
                    else PHRASES["record_unwritable"])
        raise mc.OutputHeldError(verdict["reason"], cells=[c["sheet"] + "!" + c["cell"] for c in changes],
                                 lineage_key=verdict["lineage_key"], notice_identity=verdict["notice_identity"], guidance=guidance)
    return verdict


def attach(receipt, verdict):
    if verdict and verdict.get("state") in {"acknowledged", "completed"}:
        receipt["formula_cell_notice"] = {key: verdict.get(key) for key in (
            "lineage_key", "seq", "record_id", "change_id", "notice_identity",
            "acknowledged_by", "inherited_from", "cells")}
        receipt["formula_cell_notice"]["state_before"] = verdict["state"]
        receipt["formula_cell_notice_verdict"] = verdict
    return receipt


def complete_published(root, verdict, receipt, output_path, receipt_path):
    """파일 발행 직후만 완료. 뒤 검사 통과나 파일 현존을 보장하지 않는다."""
    if not verdict or receipt_path is None:
        return
    try:
        actual = _hash(Path(output_path).read_bytes())
        if actual != receipt["output"]["sha256"]:
            receipt["formula_cell_notice_error"] = "output_hash_mismatch"
        else:
            result = finish(root, verdict, output_sha256=actual,
                            receipt_sha256=_hash(Path(receipt_path).read_bytes()))
            if result["completed"]:
                return
            receipt["formula_cell_notice_error"] = result["reason"]
    except OSError as error:
        receipt["formula_cell_notice_error"] = str(error)
    # Only failed completion changes the already published receipt; no completed
    # record references its old hash. Replace through a sibling temporary file.
    persist_receipt_diagnostics(receipt, receipt_path, "formula_cell_notice_error")


def persist_receipt_diagnostics(receipt, receipt_path, error_key):
    """이미 발행한 사본은 유지하고 영수증 진단만 원자 갱신한다."""
    import os
    import tempfile
    path = Path(receipt_path)
    pending = None
    try:
        fd, pending = tempfile.mkstemp(prefix="." + path.name + ".notice-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
        os.replace(pending, path)
    except OSError as error:
        # Both artifacts were already published; diagnostic failure is not
        # grounds to roll back the successful copy.
        receipt[error_key] += "; receipt: " + str(error)
    finally:
        if pending is not None:
            try:
                Path(pending).unlink(missing_ok=True)
            except OSError:
                pass
