"""Single selected manuscript intake; composes the existing source engines.

The caller supplies the preserved answer source (path + ordinary claims).
Claims must explicitly cover the selected file, self ownership and resume.
This checks recorded evidence, not the truth of a model's interpretation.
No prose classifier, approval flag, alternate store or registration engine.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

import gg_core as core
import gg_fs as fs
import gg_lock
import gg_reuse as reuse
import gg_source_intake as intake


FACT_KEYS = ("field_id", "kind", "value", "value_type", "unit", "period",
             "scope", "answer_state")


def _reject(reason):
    raise core.OperationError({
        "operation": "intake_manuscript", "target": "canonical",
        "commit_state": "not_committed", "reason": reason,
        "cleanup_errors": [],
    })


def _answer(root, input_path, filename):
    spec = json.loads(fs.read_bytes(core.local(root, input_path))[0])
    if not isinstance(spec, dict) or not isinstance(spec.get("claims"), dict):
        _reject("answer_confirmation_required")
    path = core.local(root, spec["path"])
    if path.suffix.lower() not in {".txt", ".md"}:
        _reject("answer_text_required")
    raw = fs.read_bytes(path)[0]
    lines = raw.decode("utf-8").splitlines()
    expected = {"intake.current_manuscript": filename,
                "intake.file_owner": "self", "intake.work_basis": "resume"}
    claims = {}
    for field, value in expected.items():
        found = [(cid, claim) for cid, claim in spec["claims"].items()
                 if isinstance(claim, dict) and claim.get("field_id") == field]
        if len(found) != 1:
            _reject("answer_confirmation_required")
        cid, claim = found[0]
        actual = claim.get("value")
        if isinstance(actual, str):
            actual = unicodedata.normalize("NFC", actual)
        if (actual != value or claim.get("answer_state") != "provided"
                or claim.get("kind") != "reported_fact"
                or claim.get("value_type") != "text"
                or claim.get("scope") != "project" or claim.get("unit") != ""
                or claim.get("period") is not None):
            _reject("answer_confirmation_required")
        locator = re.fullmatch(r"line ([1-9][0-9]*)", str(claim.get("locator", "")))
        quote = claim.get("quote")
        if (locator is None or not isinstance(quote, str) or not quote.strip()
                or int(locator[1]) > len(lines)
                or lines[int(locator[1]) - 1].strip() != quote.strip()):
            _reject("answer_quote_mismatch")
        claims[cid] = {key: claim.get(key) for key in
                       FACT_KEYS + ("locator", "quote")}
    return path, raw, claims


def _fact_id(project, field):
    found = [fid for fid, fact in project["facts"].items()
             if fact.get("field_id") == field
             and fact.get("verification") != "superseded"]
    if len(found) > 1:
        _reject("intake_fact_conflict")
    return found[0] if found else field


def _link_change(project, request_id, source_id, source_path, answer_path,
                 answer_hash, claims):
    answer_id = "intake-answer-" + core.digest([request_id, answer_hash])[:24]
    refs = [{"id": answer_id, "revision": 1, "claim_id": cid,
             "locator": claim["locator"]} for cid, claim in claims.items()]
    targets = [{"collection": "sources", "id": source_id}]
    ops = [
        {"collection": "sources", "value": {
            "id": answer_id, "path": answer_path, "claims": claims,
            "claim_review": "quoted lines matched preserved intake answer"}},
        {"collection": "sources", "value": {
            "id": source_id, "path": source_path, "file_owner": "self"}},
    ]
    for ref in refs:
        claim = claims[ref["claim_id"]]
        if claim["field_id"] == "intake.file_owner":
            continue
        fid = _fact_id(project, claim["field_id"])
        ops.append({"collection": "facts", "value": dict(
            {key: claim[key] for key in FACT_KEYS}, id=fid,
            verification="claim_supported", source_refs=[ref])})
        targets.append({"collection": "facts", "id": fid})
    ops.append({"collection": "tasks", "value": {
        "id": "intake-manuscript-" + core.digest(request_id)[:24],
        "action": "apply", "source_refs": refs,
        "target_refs": targets,
        "reason": "Selected manuscript snapshot linked to selection, ownership and resume answers; document claims not adopted.",
    }})
    return {"request_id": request_id, "ops": ops}, answer_id


def run(root, selected_file, input_path, expected_revision, request_id):
    """Return committed only after snapshot AND canonical answer linkage.

    Registration and apply retain their own guards and CAS. A partial result
    preserves their original commit_state and names the failed stage; it is
    never reported as a completed intake and never removes earlier evidence.
    """
    stage, steps, preserved = "input", [], []
    registration_revision = None
    resolution = None
    try:
        root = Path(root).resolve()
        if not isinstance(request_id, str) or not request_id.strip():
            _reject("request_id_required")
        if type(expected_revision) is not int or expected_revision < 0:
            _reject("expected_revision_required")
        if not selected_file or not input_path:
            _reject("selected_file_and_answer_required")
        original = Path(selected_file)
        if not original.is_absolute():
            original = root / original
        original = Path(os.path.abspath(original))
        filename = unicodedata.normalize("NFC", original.name)
        stage = "answer"
        answer_original, raw, claims = _answer(root, input_path, filename)
        answer_hash = core.digest(raw)
        stage = "source"
        data = fs.read_bytes(original)[0]
        source_hash, size = core.digest(data), len(data)
        del data

        stage = "init"
        if not (root / "project.json").exists():
            if expected_revision != 0:
                _reject("revision_conflict")
            core.init(root)
            steps.append("init")
        project = core.load(root)
        if project["revision"] != expected_revision:
            _reject("revision_conflict")
        # Resolve fact identity before any source registration. Reuse the
        # same canonical field records; never leave duplicate active choices.
        for field in ("intake.current_manuscript", "intake.work_basis"):
            _fact_id(project, field)
        runtime_root = Path(__file__).resolve().parent.parent
        registry = reuse.load_registry(runtime_root / "references/builtin-sources.json")
        key_catalog = {}  # preservation grants no reuse authority
        requested_use = {"kind": "narrative_reference", "keys": [
            {"kind": "delimited", "value": "student:current-manuscript"}]}
        policy = {"classification_version": registry.document["classifier_version"],
                  "include": [filename], "always_hash": [filename],
                  "exclude": [], "document_extensions": []}
        stage = "scan"
        with gg_lock.Lock(root) as capability:
            scan = intake.run_source_scan(
                original.parent, root=root,
                classification_version=registry.document["classifier_version"],
                registry=registry, key_catalog=key_catalog, scan_policy=policy,
                requested_use=requested_use)
            if "sealed" not in scan:
                _reject(scan.get("reason", "source_scan_failed"))
            receipt = intake.write_scan_receipt(root, scan, capability=capability)
        preserved.append(str(receipt.relative_to(root)))
        steps.append("scan")
        selected = next((f for f in scan["sealed"]["files"]
                         if f["relative_id"] == filename and f["type"] == "file"), None)
        if selected is None or selected["content_sha256"] != source_hash:
            _reject("selection_bytes_mismatch")

        stage = "resolve"
        resolution = reuse.resolve_reuse(source_hash, requested_use, context={
            "root": root, "runtime_root": runtime_root, "registry": registry,
            "key_catalog": key_catalog, "catalog": None, "catalog_digest": None,
            "indexes": {}, "packs_dir": registry.resolved_packs_dir})
        if not (resolution["status"] in {"reuse_ready", "needs_excerpt"}
                or (resolution["status"] == "unmapped"
                    and resolution["reasons"] == ["source_unmatched"])):
            _reject("source_resolution_blocked")
        steps.append("resolve")
        selection = {
            "kind": "external_document", "source_hash": source_hash,
            "scan_id": scan["scan_id"],
            "byte_source": {"kind": "scanned_external", "root": str(original.parent),
                            "scan_policy_digest": scan["sealed"]["scan_policy_digest"]},
            "files": [{"relative_id": filename, "content_sha256": source_hash, "bytes": size}],
            "registry_digest": registry.registry_digest, "requested_use": requested_use,
            "excerpt": None, "external_identity": {
                "original_basename": filename, "original_sha256": source_hash,
                "observed_utc": datetime.now(timezone.utc).isoformat(),
                "role": "student_current_manuscript", "class": "user_document"},
        }
        stage = "register"
        registration_id = request_id + ":snapshot"
        with gg_lock.Lock(root) as capability:
            project = core.register_source_snapshot(
                root, selection, expected_revision, capability=capability,
                request_id=registration_id, registry=registry,
                runtime_root=runtime_root, key_catalog=key_catalog)
            registration_revision = project["revision"]
        steps.append("register")
        record_path = root / "sources/registered/requests" / (
            hashlib.sha256(registration_id.encode("utf-8")).hexdigest() + ".json")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        source_id = record["source_id"]
        content = json.loads((root / "sources/registered" / (source_id + ".json"))
                             .read_text(encoding="utf-8"))
        source_path = content["snapshot_dir"] + "/" + filename
        preserved.extend([content["snapshot_dir"], record["view_path"]])

        stage = "answer_preservation"
        if fs.file_sha256(answer_original) != answer_hash:
            _reject("answer_bytes_mismatch")
        if fs.file_sha256(original) != source_hash:
            _reject("selection_bytes_mismatch")
        answer_path = "sources/answers/" + answer_hash + answer_original.suffix.lower()
        destination = core.local(root, answer_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        published = fs.publish_bytes_noreplace(
            destination, raw, parent_identity=fs.identity(destination.parent, kind="directory"),
            existing_sha256=answer_hash)
        if published["state"] != "installed":
            _reject("answer_snapshot_conflict")
        preserved.append(answer_path)
        change, answer_id = _link_change(
            project, request_id, source_id, source_path, answer_path, answer_hash, claims)
        stage = "apply"
        project = core.apply(root, change, registration_revision)
        steps.append("apply")
        stage = "verify"
        if (project["sources"][source_id]["hash"] != source_hash
                or project["sources"][answer_id]["hash"] != answer_hash):
            _reject("intake_evidence_mismatch")
        return {"operation": "intake_manuscript", "target": "canonical",
                "status": "registered", "commit_state": "committed",
                "revision": project["revision"], "request_id": request_id,
                "source_id": source_id, "source_path": source_path,
                "source_hash": source_hash, "answer_source_id": answer_id,
                "answer_path": answer_path, "resolution": resolution,
                "completed_steps": steps, "preserved_paths": preserved,
                "cleanup_errors": []}
    except core.OperationError as error:
        result = dict(error.result)
        result["failed_operation"] = result["operation"]
        if stage == "verify":
            result["commit_state"] = "indeterminate"
    except gg_lock.LockCleanupError as error:
        result = {"commit_state": "indeterminate", "reason": "lock_release_failed",
                  "cleanup_errors": error.errors}
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"commit_state": "indeterminate" if stage == "verify" else "not_committed",
                  "reason": error.reason if isinstance(error, gg_lock.LockError)
                  else "intake_input_or_evidence_unreadable", "cleanup_errors": []}
    result.update(operation="intake_manuscript", target="canonical", status="incomplete",
                  stage=stage, request_id=request_id, completed_steps=steps,
                  registration_revision=registration_revision, resolution=resolution,
                  preserved_paths=list(dict.fromkeys(preserved + result.get("preserved_paths", []))))
    return result
