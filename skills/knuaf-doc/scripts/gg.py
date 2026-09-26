#!/usr/bin/env python3
"""Local command interface; never calls model providers or web services."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main(argv=None):
    if sys.version_info < (3, 10):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "Python 3.10 이상 필요: 현재 " + sys.version.split()[0],
                },
                ensure_ascii=False,
            )
        )
        return 2
    # All runtime text I/O is UTF-8 (C2); stdout is part of that contract —
    # blocked/error JSON must not crash on a non-UTF-8 console locale.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass
    import gg_core as core

    ap = argparse.ArgumentParser(description="논문 정본·검사·검토본 관리")
    ap.add_argument(
        "command",
        choices=[
            "init",
            "doctor",
            "import",
            "status",
            "next",
            "apply",
            "check",
            "export",
            "question",
            "bundle",
            "paper",
            "observe",
            "adopt-output",
            "unlock",
            "lock-upgrade",
            "rda-lookup",
            "rda-propose",
            "rda-apply",
            "major-plan",
            "insect-plan",
            "insect-evidence",
            "rda-candidates",
            "source-scan",
            "source-resolve",
            "source-register",
            "source-view",
        ],
    )
    ap.add_argument("folder", nargs="?")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--change")
    ap.add_argument("--expected-revision", type=int)
    ap.add_argument("--scope", default="all")
    ap.add_argument("--kind", default="review")
    ap.add_argument("--field")
    ap.add_argument("--input")
    ap.add_argument("--pdf")
    ap.add_argument("--xlsx")
    ap.add_argument("--observer")
    ap.add_argument("--name")
    ap.add_argument("--companion", action="append")
    ap.add_argument("--request-id")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--offline-confirmed", action="store_true")
    ap.add_argument("--resume")
    # P4 research/lookup options (rda-* commands only; never touch canonical)
    ap.add_argument("--crop")
    ap.add_argument("--region")
    ap.add_argument("--major")
    ap.add_argument("--rda-kind")
    ap.add_argument("--year", type=int)
    ap.add_argument("--form")
    ap.add_argument("--unit")
    ap.add_argument("--use-scope")
    ap.add_argument("--allow-web", action="store_true")
    ap.add_argument("--audit-key")
    ap.add_argument("--packs-dir")
    ap.add_argument("--catalog")
    ap.add_argument("--target-current")
    # P5 source commands (stage-g006): every trusted input is an explicit
    # caller-supplied path — no implicit discovery, no dev-path default,
    # no installed-default substitution (API section 3.4).
    ap.add_argument("--root")
    ap.add_argument("--registry")
    ap.add_argument("--key-catalog")
    ap.add_argument("--policy")
    ap.add_argument("--requested-use")
    ap.add_argument("--external-root")
    ap.add_argument("--selection")
    ap.add_argument("--source-hash")
    a = ap.parse_args(argv)
    p4_commands = {"rda-lookup", "rda-propose", "rda-apply"}
    p5_commands = {
        "source-scan", "source-resolve", "source-register", "source-view"
    }
    if a.command not in p4_commands and a.folder is None:
        raise SystemExit("folder 필요")
    root_resolved = Path(a.folder).resolve() if a.folder else None

    def rel(path):
        return str(Path(path).resolve().relative_to(root_resolved))

    def insect_selection():
        # The common CLI's generic error path prints exception text.  New
        # private-project inputs must never disclose an absolute path there.
        import gg_insect_document

        try:
            text = core.local(a.folder, a.input).read_text(encoding="utf-8")
        except (OSError, ValueError, TypeError, UnicodeError):
            raise ValueError("insect_input_invalid_or_unreadable") from None
        return insect_call(gg_insect_document.parse_selection, text)

    def insect_call(fn, *args):
        # Insect modules raise ValueError with fixed reason codes only; any
        # other exception text could carry input data, so it is reduced to
        # one stable code before the common error path prints it.
        try:
            return fn(*args)
        except ValueError:
            raise
        except Exception:
            raise ValueError("insect_internal_error") from None

    try:
        code = 0
        if a.command == "init":
            value = core.init(a.folder)
        elif a.command == "import":
            if not a.out:
                raise ValueError("--out 새 폴더 필요")
            value = core.migrate(
                a.folder, a.out, offline_confirmed=a.offline_confirmed
            )
        elif a.command == "doctor":
            try:
                import gg_lock

                lock_state = dict(
                    gg_lock.probe_lock(a.folder)
                    if a.probe
                    else gg_lock.inspect_lock(a.folder)
                )
            except Exception as e:
                lock_state = {
                    "structure": "unavailable",
                    "lock_state": "unavailable",
                    "reason": str(e),
                }
            value = {
                "python": sys.version,
                "dependencies": {
                    m: bool(importlib.util.find_spec(m)) for m in ("docx", "openpyxl")
                },
                "recalculation": "not_run",
                "render": "not_run",
                "hwp": "unsupported",
                "web": "not_run",
                "model_calls": "disabled",
                "lock_present": (Path(a.folder) / ".gg-lock").exists(),
                "lock": lock_state,
                "orphan_files": [
                    str(p)
                    for p in list(Path(a.folder).rglob(".gg-tmp-*"))
                    + list(Path(a.folder).rglob(".gg-export-*"))
                ],
                "notice": "설치 탐지는 실행 검증이 아님. 채팅만 가능하면 초안과 원답변 인계, 제출 후보 불가.",
            }
        elif a.command == "unlock":
            import gg_lock

            # cleanup_owner removes only a stale owner record under a
            # freshly-acquired ready v2 guard — never the guard/protocol.
            value = dict(gg_lock.cleanup_owner(a.folder))
            code = (
                0
                if value.get("owner_cleaned")
                or value.get("lock_state") == "free"
                else 2
            )
        elif a.command == "lock-upgrade":
            import gg_lock

            value = dict(
                gg_lock.upgrade_offline(
                    a.folder,
                    offline_confirmed=a.offline_confirmed,
                    resume_id=a.resume,
                )
            )
            code = core.COMMIT_EXIT.get(value.get("commit_state"), 4)
        elif a.command == "observe":
            if not a.input or not a.observer:
                raise ValueError("--input 관측 JSON과 --observer 필요")
            payload = json.loads(
                core.local(a.folder, a.input).read_text(encoding="utf-8")
            )
            if not isinstance(payload, dict):
                raise ValueError("관측 기록은 객체여야 함")
            value = core.ingest_review_observation(a.folder, payload, a.observer)
        elif a.command in {"major-plan", "insect-plan", "insect-evidence", "rda-candidates"}:
            # New writing contract: the major comes from a confirmed fact in
            # the canonical project.  The raw rda-lookup contract below stays
            # independent and retains its optional major filter.
            import gg_major_contract as major_contract

            registry = major_contract.default_registry()
            try:
                project = core.load(a.folder)
            except (OSError, ValueError) as error:
                if a.command in {"insect-plan", "insect-evidence"}:
                    raise ValueError("insect_project_unreadable") from None
                raise
            try:
                binding = major_contract.binding_from_project(registry, project)
            except major_contract.MajorContractError as error:
                value = major_contract.common_only_plan(error.reason)
                code = 2
            else:
                if a.major is not None and a.major != binding.major_id:
                    raise ValueError("--major와 정본의 전공 바인딩이 다름")
                if a.command == "major-plan":
                    value = {
                        "binding": vars(binding),
                        "project_revision": project["revision"],
                        "proposals": {
                            kind: proposal.to_dict()
                            for kind, proposal in major_contract.capability_proposals(
                                registry, binding.major_id).items()
                        },
                    }
                elif a.command == "insect-plan":
                    import gg_insect_document

                    if a.input:
                        selection = insect_selection()
                        value = insect_call(
                            gg_insect_document.build_plan,
                            registry, project, selection,
                        )
                    else:
                        # No selection yet: the common interview reads the
                        # line/year instances from this view.
                        value = insect_call(
                            gg_insect_document.build_line_view,
                            registry, project,
                        )
                elif a.command == "insect-evidence":
                    if not a.pdf or not a.xlsx:
                        raise ValueError("--pdf R0와 --xlsx R1 원본 경로 필요")
                    import gg_insect_evidence

                    value = insect_call(
                        gg_insect_evidence.review_sources,
                        registry, project, a.pdf, a.xlsx,
                    )
                else:
                    if not a.crop or not a.region:
                        raise ValueError("--crop과 --region 필요")
                    import gg_rda_candidates

                    value = gg_rda_candidates.lookup_approved_candidates(
                        a.crop, a.region, binding.major_id,
                        kind=a.rda_kind, year=a.year, form=a.form,
                        unit=a.unit, use_scope=a.use_scope,
                        audit_key=(json.loads(a.audit_key)
                                   if a.audit_key else None),
                    )
        elif a.command in p4_commands:
            # P4 research/lookup routes (stage-g005): proposal/lookup only —
            # canonical state is written exclusively through `apply` above.
            import gg_rda_research

            def p4_ctx():
                kw = {}
                if a.packs_dir:
                    kw["packs_dir"] = a.packs_dir
                if a.catalog:
                    kw["catalog"] = json.loads(
                        Path(a.catalog).read_text(encoding="utf-8"))
                return gg_rda_research.resolver_context(**kw) if kw else None

            if a.command == "rda-lookup":
                if not a.crop or not a.region:
                    raise ValueError("--crop과 --region 필요")
                import gg_rda_lookup

                value = gg_rda_lookup.lookup_rda_data(
                    a.crop,
                    a.region,
                    major=a.major,
                    kind=a.rda_kind,
                    year=a.year,
                    form=a.form,
                    allow_web=a.allow_web,
                    audit_key=(
                        json.loads(a.audit_key) if a.audit_key else None),
                )
            elif a.command == "rda-propose":
                if not a.input:
                    raise ValueError("--input 제안 입력 JSON 필요")
                spec = json.loads(
                    Path(a.input).read_text(encoding="utf-8"))
                value = gg_rda_research.propose(
                    spec.get("pack_revision"),
                    spec.get("target"),
                    spec.get("selection"),
                    context=p4_ctx(),
                )
            else:  # rda-apply
                if not a.input or a.expected_revision is None:
                    raise ValueError(
                        "--input 제안 JSON과 --expected-revision 필요")
                proposal = json.loads(
                    Path(a.input).read_text(encoding="utf-8"))
                value = gg_rda_research.apply(
                    proposal,
                    expected_revision=a.expected_revision,
                    target_current=(
                        json.loads(a.target_current)
                        if a.target_current else None),
                    packs_dir=a.packs_dir,
                )
                if value.get("status") in {"stale", "rejected"}:
                    code = 2
        elif a.command in p5_commands:
            # G006/P5 source commands (API section 3.3): trusted inputs
            # load only from explicit caller-supplied paths — never
            # implicit discovery or installed-default substitution.
            import hashlib
            import os

            import gg_lock
            import gg_reuse

            # The DEPLOYMENT's own installed base — the running tool's
            # install location, bound independently of --registry (V5-4):
            # never copied from resolved_base and never a CLI flag.
            runtime_root = Path(
                os.path.normpath(
                    str(Path(__file__).absolute().parent.parent)))

            def _json_path(flag, label):
                if not flag:
                    raise ValueError("%s 경로 필요" % label)
                return json.loads(Path(flag).read_text(encoding="utf-8"))

            if a.command == "source-scan":
                # V15-2 — the scanner carries NO packs_dir operand: this
                # subcommand has no --packs-dir flag to consume.
                if a.packs_dir:
                    raise ValueError(
                        "--packs-dir is not a source-scan operand")
                if not a.root:
                    raise ValueError("--root 외부 루트 필요")
                if not a.registry:
                    raise ValueError("--registry 경로 필요")
                if not a.key_catalog:
                    raise ValueError("--key-catalog 경로 필요")
                if not a.policy:
                    raise ValueError("--policy 경로 필요")
                registry = gg_reuse.load_registry(a.registry)
                import gg_source_intake

                with gg_lock.Lock(a.folder) as capability:
                    scan = gg_source_intake.run_source_scan(
                        a.root,
                        root=a.folder,
                        classification_version=registry.document[
                            "classifier_version"],
                        registry=registry,
                        key_catalog=_json_path(
                            a.key_catalog, "--key-catalog"),
                        scan_policy=_json_path(a.policy, "--policy"),
                        requested_use=(
                            json.loads(a.requested_use)
                            if a.requested_use
                            else None
                        ),
                    )
                    if "sealed" not in scan:
                        value = scan
                        code = scan.get("exit", 2)
                    else:
                        receipt = gg_source_intake.write_scan_receipt(
                            a.folder, scan, capability=capability)
                        value = {
                            "status": "ok",
                            "scan_id": scan["scan_id"],
                            "scan_state": scan["sealed"]["scan_state"],
                            "receipt": str(receipt),
                        }
            elif a.command == "source-resolve":
                if not a.source_hash:
                    raise ValueError("--source-hash 필요")
                if not a.requested_use:
                    raise ValueError("--requested-use JSON 필요")
                if not a.registry:
                    raise ValueError("--registry 경로 필요")
                if not a.key_catalog:
                    raise ValueError("--key-catalog 경로 필요")
                registry = gg_reuse.load_registry(a.registry)
                key_catalog = _json_path(a.key_catalog, "--key-catalog")
                catalog = indexes = None
                load_error = None
                if a.catalog:
                    try:
                        catalog, indexes = gg_reuse.load_accepted_catalog(
                            a.catalog,
                            registry=registry,
                            packs_dir=a.packs_dir,
                        )
                    except ValueError as exc:
                        load_error = exc
                if load_error is not None:
                    # SCHEMA section 6.1 site 3: a catalog that cannot be
                    # constructed at this input-loading boundary maps to
                    # the resolver's own vocabulary.
                    value = {
                        "status": "blocked",
                        "reasons": ["catalog_not_accepted"],
                        "detail": str(load_error),
                        "exit": 2,
                    }
                    code = 2
                else:
                    context = {
                        "root": a.folder,
                        "runtime_root": runtime_root,
                        "registry": registry,
                        "key_catalog": key_catalog,
                        "catalog": catalog,
                        "catalog_digest": (
                            catalog.digest if catalog is not None else None
                        ),
                        "indexes": indexes if indexes is not None else {},
                        "packs_dir": (
                            Path(a.packs_dir)
                            if a.packs_dir
                            else registry.resolved_packs_dir
                        ),
                    }
                    value = gg_reuse.resolve_reuse(
                        a.source_hash,
                        json.loads(a.requested_use),
                        context=context,
                    )
                    code = value.get("exit", 2)
            elif a.command == "source-register":
                if not a.selection:
                    raise ValueError("--selection JSON 필요")
                if a.expected_revision is None:
                    raise ValueError("--expected-revision 필요")
                if not a.request_id:
                    raise ValueError("--request-id 필요")
                if not a.registry:
                    raise ValueError("--registry 경로 필요")
                if not a.key_catalog:
                    raise ValueError("--key-catalog 경로 필요")
                selection = json.loads(a.selection)
                byte_kind = (
                    (selection.get("byte_source") or {}).get("kind")
                    if isinstance(selection, dict)
                    else None
                )
                if byte_kind == "scanned_external":
                    if not a.external_root:
                        raise ValueError(
                            "--external-root 필요 "
                            "(byte_source.kind=scanned_external)"
                        )
                    if Path(a.external_root) != Path(
                        selection["byte_source"].get("root") or ""
                    ):
                        # The explicit path must name the same external
                        # root the selection binds — a divergent claim is
                        # the request's own invalidity, not a new state.
                        value = {
                            "operation": "register_source_snapshot",
                            "target": "canonical",
                            "commit_state": "not_committed",
                            "reason": "selection_invalid",
                            "request_id": a.request_id,
                            "detail": "--external-root does not equal "
                                     "selection.byte_source.root",
                        }
                        print(
                            json.dumps(value, ensure_ascii=False, indent=2)
                        )
                        return core.COMMIT_EXIT.get("not_committed", 4)
                registry = gg_reuse.load_registry(a.registry)
                key_catalog = _json_path(a.key_catalog, "--key-catalog")
                catalog = indexes = None
                if a.catalog:
                    try:
                        catalog, indexes = gg_reuse.load_accepted_catalog(
                            a.catalog,
                            registry=registry,
                            packs_dir=a.packs_dir,
                        )
                    except ValueError as exc:
                        # section 6.1 site 3 / section 3.4: a catalog that
                        # cannot be constructed is an admission failure on
                        # the register path — 'blocked' is the resolver's
                        # vocabulary and never a commit_state.
                        value = {
                            "operation": "register_source_snapshot",
                            "target": "canonical",
                            "commit_state": "not_committed",
                            "reason": "adoption_context_invalid",
                            "request_id": a.request_id,
                            "detail": str(exc),
                        }
                        print(
                            json.dumps(value, ensure_ascii=False, indent=2)
                        )
                        return core.COMMIT_EXIT.get("not_committed", 4)
                if isinstance(selection, dict) \
                        and selection.get("kind") == "p4_snapshot" \
                        and catalog is None:
                    value = {
                        "operation": "register_source_snapshot",
                        "target": "canonical",
                        "commit_state": "not_committed",
                        "reason": "adoption_context_invalid",
                        "request_id": a.request_id,
                        "detail": "--catalog and --packs-dir are required "
                                  "for kind=p4_snapshot",
                    }
                    print(json.dumps(value, ensure_ascii=False, indent=2))
                    return core.COMMIT_EXIT.get("not_committed", 4)
                with gg_lock.Lock(a.folder) as capability:
                    value = core.register_source_snapshot(
                        a.folder,
                        selection,
                        a.expected_revision,
                        capability=capability,
                        request_id=a.request_id,
                        registry=registry,
                        runtime_root=runtime_root,
                        key_catalog=key_catalog,
                        catalog=catalog,
                        indexes=indexes,
                    )
            else:  # source-view
                if not a.request_id:
                    raise ValueError("--request-id 필요")
                rec_rel = (
                    "sources/registered/requests/%s.json"
                    % hashlib.sha256(
                        a.request_id.encode("utf-8")).hexdigest()
                )
                rec_path = core.local(a.folder, rec_rel)
                if not rec_path.exists():
                    value = {
                        "operation": "publish_source_view",
                        "target": rec_rel,
                        "commit_state": "not_committed",
                        "reason": "published_unregistered",
                        "request_id": a.request_id,
                    }
                    print(json.dumps(value, ensure_ascii=False, indent=2))
                    return core.COMMIT_EXIT.get("not_committed", 4)
                registration = json.loads(
                    rec_path.read_text(encoding="utf-8"))
                with gg_lock.Lock(a.folder) as capability:
                    value = core.publish_source_view(
                        a.folder, registration, capability=capability)
        elif a.command == "paper":
            if not a.input:
                raise ValueError("--input 논문 입력 JSON 필요")
            src = core.local(a.folder, a.input)
            spec_bytes = src.read_bytes()
            requested_rel = a.out or "build/검토전_본문.md"
            core.local(a.folder, requested_rel)
            value = core.paper(
                a.folder, rel(src), spec_bytes, requested_rel,
                major_id=a.major)
        elif a.command == "adopt-output":
            # SPEC: caller supplies {output, companion_files?} metadata —
            # core validates every claim against actual bytes and the
            # canonical state; the CLI never invents hashes or refs.
            if not a.input or a.expected_revision is None:
                raise ValueError("--input 채택 JSON과 --expected-revision 필요")
            spec = json.loads(
                core.local(a.folder, a.input).read_text(encoding="utf-8")
            )
            if (
                not isinstance(spec, dict)
                or "output" not in spec
                or set(spec) - {"output", "companion_files"}
            ):
                raise ValueError(
                    "채택 JSON은 {output, companion_files?} 형식이어야 함"
                )
            companions = spec.get("companion_files")
            if companions is None:
                companions = []
            if not isinstance(companions, list):
                raise ValueError("companion_files는 목록이어야 함")
            request_id = a.request_id or (
                "gg:adopt:" + core.digest(spec["output"])
            )
            value = core.adopt_output(
                a.folder,
                spec["output"],
                a.expected_revision,
                request_id,
                companion_files=companions,
                major_id=a.major,
            )
        else:
            p = core.load(a.folder)
            if a.command == "apply":
                if not a.change or a.expected_revision is None:
                    raise ValueError("--change와 --expected-revision 필요")
                value = core.apply(
                    a.folder,
                    json.loads(Path(a.change).read_text(encoding="utf-8")),
                    a.expected_revision,
                )
            elif a.command == "check":
                value = (
                    core.gate(a.folder, p)
                    if a.scope == "submission"
                    else core.checks(a.folder, p)
                )
            elif a.command == "status":
                report = core.gate(a.folder, p)
                state = core.completion(a.folder, p, report)
                value = {
                    "revision": p["revision"],
                    "checks": core.checks(a.folder, p),
                    "tasks": core.next_tasks(p),
                    "completion": state,
                    "skill_ready": state["skill_ready"],
                    "guideline_ready": state["guideline_ready"],
                    "user_finish_pending": state["user_finish_pending"],
                    "professor_approval_pending": state["professor_approval_pending"],
                    "professor_approval": "별도 기록 확인 필요",
                }
            elif a.command == "next":
                value = core.next_tasks(p)
            elif a.command == "question":
                if not a.field:
                    raise ValueError("--field 필요")
                action = core.question(p, a.field)
                if action in {"ask", "help"}:
                    q = p["questions"].get(
                        a.field,
                        {
                            "id": a.field,
                            "field_id": a.field,
                            "decision_revision": 1,
                            "attempts": 0,
                        },
                    )
                    q = dict(q, attempts=q["attempts"] + 1)
                    core.apply(
                        a.folder,
                        {
                            "request_id": "question:%s:%s:%s"
                            % (a.field, q["decision_revision"], q["attempts"]),
                            "ops": [{"collection": "questions", "value": q}],
                        },
                        p["revision"],
                    )
                value = {
                    "action": action,
                    "field": a.field,
                    "notice": "질문 1회·도움 1회 후 해당 작업만 보류",
                }
            elif a.command == "bundle":
                if a.scope not in p["sections"]:
                    raise ValueError("--scope 절 ID 필요")
                s = p["sections"][a.scope]
                refs = [{"collection": "sections", "id": a.scope}]
                value = {
                    "section": s,
                    "draft": core.draft(
                        core.local(a.folder, s["path"]).read_text(
                            encoding="utf-8"
                        )
                    ),
                    "original_sources": p["sources"],
                    "facts": p["facts"],
                    "rules": p["rules"],
                    "input_fingerprint": core.fingerprint(a.folder, p, refs),
                    "review_axes": [
                        "사실성",
                        "적용 타당성",
                        "논증",
                        "실행성",
                        "일관성",
                        "균형성",
                        "표현",
                    ],
                    "required_findings": [
                        "본문 위치",
                        "원문 위치",
                        "문제",
                        "영향",
                        "수정 조건",
                    ],
                    "notice": "요약 대신 실제 원답변·원문 파일을 읽어 대조. 같은 작성자의 자가검토는 독립검토가 아님.",
                }
            else:
                if a.kind not in {"draft", "review", "submission_candidate"}:
                    raise ValueError(
                        "--kind는 draft·review·submission_candidate 중 하나"
                    )
                value = core.export(a.folder, a.kind, major_id=a.major)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        if a.command == "check" and any(
            core.blocks_skill_candidate(r)
            if a.scope == "submission"
            else r["severity"] == "error" and r["status"] != "pass"
            for r in value
        ):
            return 1
        return code
    except core.OperationError as e:
        # Public-API abnormal result: the result object carries the
        # rechecked commit evidence — never flatten it into a generic
        # blocked message, and never let exit 2 impersonate exit 3/4.
        print(json.dumps(e.result, ensure_ascii=False, indent=2))
        return core.COMMIT_EXIT.get(e.result.get("commit_state"), 4)
    except (ValueError, KeyError, OSError, TypeError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
