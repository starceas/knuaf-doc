"""Provider-neutral project state. All mutations use locked, revisioned transactions."""

from pathlib import Path
from decimal import Decimal, InvalidOperation
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unicodedata
import uuid

VERSION = "1.0"
STATES = {
    "not_provided",
    "explicit_none",
    "unknown",
    "provided",
    "not_applicable",
    "withheld",
}
KINDS = {"reported_fact", "observation", "assumption", "target", "derived"}
VERIFY = {"unreviewed", "source_located", "claim_supported", "disputed", "superseded"}
START, END = "<!-- gg:draft:start -->", "<!-- gg:draft:end -->"
COLLECTIONS = (
    "sources",
    "facts",
    "sections",
    "questions",
    "tasks",
    "reviews",
    "rules",
    "approvals",
    "outputs",
)


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    )
    return hashlib.sha256(data).hexdigest()


def local(root, name):
    root = Path(root).resolve()
    p = (root / name).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ValueError("작업 폴더 밖 경로는 허용하지 않음: " + str(name))
    return p


def draft(text):
    if START in text or END in text:
        if (
            text.count(START) != 1
            or text.count(END) != 1
            or text.index(START) > text.index(END)
        ):
            raise ValueError("DRAFT 경계 손상")
        return text.split(START)[1].split(END)[0].strip()
    m = re.search(
        r"^##\s+DRAFT[^\n]*\n(.*?)(?=^##\s+(?:STATUS|INPUT|RESEARCH|FACTS|OPEN)\b|\Z)",
        text,
        re.M | re.S,
    )
    return m.group(1).strip() if m else text.strip()


META_BLOCK = re.compile(r"^##\s+(?:STATUS|INPUT|RESEARCH|FACTS|OPEN)\b")
TITLE_LINE = re.compile(r"^#\s+\S")
DRAFT_HEAD = re.compile(r"^##\s+DRAFT\b")


def draft_marker_extra(raw):
    """Text outside well-formed gg:draft markers that is not envelope.

    draft() exposes only the marked slice, so any other stored text never
    reaches merged()/checks.  The accepted envelope is the legacy six-block
    metadata (`## STATUS|INPUT|RESEARCH|FACTS|OPEN` sections), a `# ` title
    line, and the `## DRAFT` heading that labels the marked slice.  Anything
    else is returned so callers can flag the divergence; the raw file is
    never modified.  Returns the residue text or None.
    """
    if raw.count(START) != 1 or raw.count(END) != 1:
        return None
    if raw.index(START) > raw.index(END):
        return None
    before = raw[: raw.index(START)].splitlines()
    after = raw[raw.index(END) + len(END):].splitlines()
    # A trailing `## DRAFT` heading belongs to the marked slice itself, but
    # only when nothing but blank lines follows it.
    for i in range(len(before) - 1, -1, -1):
        if before[i].strip():
            if DRAFT_HEAD.match(before[i]):
                before = before[:i]
            break
    extra = []
    in_meta = False
    for line in before:
        if line.startswith("##"):
            in_meta = bool(META_BLOCK.match(line))
            if not in_meta:
                extra.append(line)
            continue
        if line.strip() and not in_meta and not TITLE_LINE.match(line):
            extra.append(line)
    in_meta = False
    for line in after:
        if line.startswith("##"):
            in_meta = bool(META_BLOCK.match(line))
            if not in_meta:
                extra.append(line)
            continue
        if line.strip() and not in_meta:
            extra.append(line)
    return "\n".join(extra).strip() or None


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".gg-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load(root):
    p = json.loads((Path(root) / "project.json").read_text(encoding="utf-8"))
    validate(p)
    return p


def init(root):
    """First product write: persistent protocol is installed before the
    canonical commit (SPEC §3).  Existing project.json is never overwritten
    — init on an initialized workspace is an error, not a no-op.  A ready
    v2 control without project.json is resumable under the guard."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "project.json").exists():
        raise _op("init", "canonical", "not_committed", "existing_canonical",
                  detail="기존 정본을 덮어쓰지 않음")
    if not os.path.lexists(str(root / ".gg-lock")):
        _lock_mod().install_new(root)

    def body(capability):
        return _init_locked(root, capability)

    return _guarded(root, "init", "canonical", body,
                    lambda: _recheck_canonical(root, 0))


def _init_locked(root, capability):
    """Canonical project.json creation under an already-held guard.

    Used by init and by the import path (which installs the protocol on a
    staged workspace and then publishes the whole workspace)."""
    _lock_mod().assert_held(capability, root)
    root = Path(root)
    if (root / "project.json").exists():
        raise _op("init", "canonical", "not_committed", "existing_canonical",
                  detail="기존 정본을 덮어쓰지 않음")
    p = dict(
        schema_version=2, revision=0, requests={}, views={}, issues=[], history=[]
    )
    p.update({k: {} for k in COLLECTIONS})
    _commit_canonical(
        root,
        json.dumps(p, ensure_ascii=False, indent=2).encode(),
        operation="init",
        expect_revision=0,
    )
    return p


def _lock_mod():
    """Guard protocol module (lane B). Imported lazily so read-only paths and
    pure helpers stay usable without it; it never imports gg_core."""
    import gg_lock

    return gg_lock


def _fs_mod():
    """Filesystem primitives module (lane B): canonical JSON bytes, identity,
    no-replace publish/rename. Lazily imported; never imports gg_core."""
    import gg_fs

    return gg_fs


def _publication_mod():
    """Immutable publication module (lane C). Lazily imported; depends on
    gg_lock.assert_held + gg_fs only, never on gg_core."""
    import gg_publication

    return gg_publication


def Lock(root):
    """Compatibility adapter: the real OS guard capability lives in gg_lock.

    ``with Lock(root) as capability`` yields the acquired capability token;
    it is a context manager handle, not proof of exclusivity — only
    ``gg_lock.assert_held`` decides that.
    """
    return _lock_mod().Lock(root)


COMMIT_EXIT = {
    "committed": 0,
    "not_committed": 2,
    "committed_cleanup_pending": 3,
    "indeterminate": 4,
}


class OperationError(ValueError):
    """Public-API abnormal result preserving commit evidence (API §1).

    ``result`` always carries operation, target, commit_state, reason and
    cleanup_errors; revision/request_id/target_sha256/receipt_path/
    publication_state/preserved_paths are added only when actually known.
    CLI maps commit_state to exit 0/2/3/4 and must handle this before the
    generic ValueError catch.
    """

    def __init__(self, result, detail=None):
        self.result = result
        message = str(result.get("reason"))
        if detail:
            message += ": " + str(detail)
        super().__init__(message)


def _result(operation, target, commit_state, reason, cleanup_errors=None,
            **fields):
    r = {
        "operation": operation,
        "target": target,
        "commit_state": commit_state,
        "reason": reason,
        "cleanup_errors": cleanup_errors or [],
    }
    for key in (
        "revision",
        "request_id",
        "target_sha256",
        "receipt_path",
        "publication_state",
        "preserved_paths",
    ):
        if fields.get(key) is not None:
            r[key] = fields[key]
    return r


def _op(operation, target, commit_state, reason, detail=None, **fields):
    return OperationError(
        _result(operation, target, commit_state, reason, **fields), detail
    )


def _recheck_canonical(root, expect_revision, request_id=None):
    """Decide commit_state after a canonical write attempt raised.

    Reads project.json back: confirmed new revision (+request entry) means
    the commit landed and only cleanup is pending; a confirmed older
    revision means not_committed; anything unreadable or inconsistent is
    indeterminate — never guessed from the exception type alone.
    """
    try:
        current = json.loads(
            (Path(root) / "project.json").read_bytes()
        )
    except (OSError, ValueError):
        return "indeterminate", {}
    revision = current.get("revision")
    if type(revision) is not int:
        return "indeterminate", {}
    if revision == expect_revision:
        if request_id is None or request_id in current.get("requests", {}):
            return "committed_cleanup_pending", {
                "revision": revision,
                "request_id": request_id,
            }
        return "indeterminate", {}
    if revision < expect_revision:
        return "not_committed", {}
    return "indeterminate", {}


def _commit_canonical(root, data, *, operation, expect_revision,
                      request_id=None, preserved_paths=None):
    """Single canonical commit point with post-attempt recheck."""
    try:
        atomic(Path(root) / "project.json", data)
    except OSError as error:
        state, extra = _recheck_canonical(root, expect_revision, request_id)
        raise _op(
            operation,
            "canonical",
            state,
            "canonical_commit_failed",
            detail=str(error),
            preserved_paths=preserved_paths,
            **extra,
        ) from error


def _guarded(root, operation, target, body, confirm):
    """Run ``body(capability)`` under the OS guard and fold release-time
    failures into the rechecked commit result.

    assert_held inside each *_locked function is the authority; this helper
    only owns the acquire/release boundary.  A LockCleanupError raised while
    leaving the guard is merged with the actual storage recheck instead of
    leaking as a generic error — that is what turns a verified commit plus
    failed owner cleanup into exit 3 and an unverifiable one into exit 4.
    """
    lock = _lock_mod()
    outcome = {}
    try:
        with lock.Lock(root) as capability:
            outcome["value"] = body(capability)
    except Exception as error:
        cleanup_type = getattr(lock, "LockCleanupError", None)
        if cleanup_type is None or not isinstance(error, cleanup_type):
            raise
        prior = getattr(error, "prior_error", None)
        if isinstance(prior, OperationError):
            prior.result["cleanup_errors"] = prior.result["cleanup_errors"] + [
                {"release": str(error)}
            ]
            raise prior from error
        if prior is not None:
            raise prior from error
        state, extra = confirm()
        if (
            state == "committed_cleanup_pending"
            and not getattr(error, "release_confirmed", False)
        ):
            # The storage commit is proven but the guard release itself is
            # unconfirmed — that is indeterminate, not merely pending.
            state = "indeterminate"
        raise OperationError(
            _result(
                operation,
                target,
                state,
                "release_cleanup_failed",
                cleanup_errors=[{"release": str(error)}],
                **extra,
            ),
            str(error),
        ) from error
    return outcome["value"]


def _check_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _check_ref_id(rid):
    """TargetRef ID rules (API §9): same shape as _check_record_id plus a
    surrogate rejection — an ID that cannot encode to UTF-8 cannot be a
    canonical dict key."""
    if not isinstance(rid, str):
        raise ValueError("대상 참조 ID는 문자열이어야 함")
    if not rid.strip():
        raise ValueError("대상 참조 ID는 비어 있을 수 없음")
    if rid != rid.strip() or any(ord(c) < 0x20 or c == "\x7f" for c in rid):
        raise ValueError("대상 참조 ID에 양끝 공백·제어 문자를 사용할 수 없음")
    try:
        rid.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError("대상 참조 ID는 UTF-8로 표현되어야 함") from e


def _check_target_ref(ref):
    if (
        not isinstance(ref, dict)
        or set(ref) != {"collection", "id"}
        or ref["collection"] not in COLLECTIONS
    ):
        raise ValueError("잘못된 대상 참조 객체: " + str(ref))
    _check_ref_id(ref["id"])


def sort_target_refs(refs):
    """Canonical TargetRefs array for new P2 requests (API §9).

    Exactly ``{collection,id}`` objects, sorted by UTF-8 bytes of
    (collection, id).  Exact duplicates, same-collection NFC collisions,
    string refs, unknown collections and extra keys are rejected here —
    never silently normalized.  The returned array is what callers feed to
    ``fingerprint`` and what request/intent/receipt objects carry.
    """
    if not isinstance(refs, list):
        raise ValueError("대상 참조는 객체 배열이어야 함")
    for ref in refs:
        _check_target_ref(ref)
    ordered = sorted(
        ({"collection": r["collection"], "id": r["id"]} for r in refs),
        key=lambda r: (
            r["collection"].encode("utf-8"),
            r["id"].encode("utf-8"),
        ),
    )
    seen, nfc_seen = set(), {}
    for ref in ordered:
        key = (ref["collection"], ref["id"])
        if key in seen:
            raise ValueError("중복 대상 참조: " + str(ref))
        seen.add(key)
        nfc = (ref["collection"], unicodedata.normalize("NFC", ref["id"]))
        if nfc in nfc_seen and nfc_seen[nfc] != ref["id"]:
            raise ValueError("정규화 충돌 대상 참조: " + str(ref))
        nfc_seen[nfc] = ref["id"]
    return ordered


def _canonical_request_bytes(value):
    """Canonical JSON bytes for new request preimages (gg_fs owns the rule)."""
    return _fs_mod().canonical_json_bytes(value)


def _request_sha256(request):
    return hashlib.sha256(_canonical_request_bytes(request)).hexdigest()


def _ledger_effective_kind(entry, current_revision):
    """Effective kind of a canonical request-ledger entry (API §10).

    Legacy ``{hash,revision}`` entries read as ``apply`` and stay
    byte-identical; adopt entries are exactly
    ``{hash,revision,kind:"adopt_output",publication_ref}``.  Anything else
    is malformed_request_ledger, decided before the first product write.
    """
    if not isinstance(entry, dict):
        raise ValueError("malformed_request_ledger")
    keys = set(entry)
    hash_ok = _check_sha256(entry.get("hash"))
    revision_ok = (
        type(entry.get("revision")) is int
        and 0 < entry["revision"] <= current_revision
    )
    if keys == {"hash", "revision"}:
        if hash_ok and revision_ok:
            return "apply"
        raise ValueError("malformed_request_ledger")
    if keys == {"hash", "revision", "kind", "publication_ref"}:
        ref = entry["publication_ref"]
        ref_ok = (
            isinstance(ref, dict)
            and ref.get("schema") == "gg-publication-ref/1"
            and _check_sha256(ref.get("id"))
            and isinstance(ref.get("path"), str)
            and ref["path"]
            and _check_sha256(ref.get("sha256"))
        )
        if (
            entry.get("kind") == "adopt_output"
            and hash_ok
            and revision_ok
            and ref_ok
        ):
            return "adopt_output"
        raise ValueError("malformed_request_ledger")
    raise ValueError("malformed_request_ledger")


def _check_request_ledger(p):
    requests = p.get("requests", {})
    if not isinstance(requests, dict):
        raise ValueError("malformed_request_ledger")
    for entry in requests.values():
        _ledger_effective_kind(entry, p["revision"])


def _find_request(root, request_id, *, operation="apply"):
    """Cross-check the publication receipt namespace for this request_id."""
    try:
        receipts = _publication_mod().find_request(root, request_id=request_id)
    except Exception as error:
        raise _op(
            operation,
            "canonical",
            "not_committed",
            "request_lookup_failed",
            detail=str(error),
            request_id=request_id,
        ) from error
    return list(receipts or [])


def _verify_publication_ref(root, publication_ref, *, operation):
    try:
        _publication_mod().verify_publication(
            root, publication_ref=publication_ref
        )
    except Exception as error:
        raise _op(
            operation,
            "canonical",
            "indeterminate",
            "committed_publication_unverified",
            detail=str(error),
        ) from error


class _AdoptionContext:
    """A-internal registry token proving an adoption was published and
    verified inside this same guard acquisition (API §5)."""

    __slots__ = (
        "capability",
        "root",
        "request_sha256",
        "output_value",
        "publication_ref",
        "managed_path",
    )

    def __init__(self, capability, root, request_sha256, output_value,
                 publication_ref, managed_path):
        self.capability = capability
        self.root = root
        self.request_sha256 = request_sha256
        self.output_value = output_value
        self.publication_ref = publication_ref
        self.managed_path = managed_path


_ADOPTION_CONTEXTS = set()


def _register_adoption(**kwargs):
    ctx = _AdoptionContext(**kwargs)
    _ADOPTION_CONTEXTS.add(ctx)
    return ctx


def _adoption_ok(adoption, capability, root):
    return (
        isinstance(adoption, _AdoptionContext)
        and adoption in _ADOPTION_CONTEXTS
        and adoption.capability is capability
        and Path(adoption.root) == Path(root)
    )


def _recovery_active(root):
    """Any recovery-active marker — valid, corrupt, partial or a dangling
    symlink — blocks product writes (mirrors gg_lock._read_active)."""
    try:
        os.lstat(str(Path(root) / ".gg-recovery-active.json"))
        return True
    except OSError:
        return False


def _control_files():
    return {
        ".gg-lock",
        ".gg-recovery",
        ".gg-recovery-active.json",
        ".gg-import-publication.json",
    }


def validate(p):
    if (
        p.get("schema_version") not in (1, 2)
        or type(p.get("revision")) is not int
    ):
        raise ValueError("지원하지 않는 정본 스키마")
    for c in COLLECTIONS:
        if not isinstance(p.get(c), dict):
            raise ValueError("잘못된 컬렉션: " + c)
        for key, v in p[c].items():
            if (
                not isinstance(v, dict)
                or v.get("id") != key
                or type(v.get("revision")) is not int
            ):
                raise ValueError("ID/개정번호 오류: " + key)
    for q in p["questions"].values():
        if (
            q.get("field_id") != q["id"]
            or type(q.get("attempts")) is not int
            or q["attempts"] < 0
            or type(q.get("decision_revision")) is not int
            or q["decision_revision"] < 1
        ):
            raise ValueError("질문 ID/횟수/결정 개정 오류")
    for s in p["sections"].values():
        if (
            s.get("status") not in {"empty", "drafting", "review_ready"}
            or type(s.get("order")) is not int
            or not s.get("title")
            or not s.get("path")
        ):
            raise ValueError("절 상태/순서/제목/경로 오류")
    for f in p["facts"].values():
        for key in (
            "field_id",
            "kind",
            "value",
            "unit",
            "value_type",
            "period",
            "scope",
            "answer_state",
            "verification",
            "source_refs",
        ):
            if key not in f:
                raise ValueError("사실 필드 누락: " + key)
        if (
            f["kind"] not in KINDS
            or f["answer_state"] not in STATES
            or f["verification"] not in VERIFY
        ):
            raise ValueError("잘못된 사실 상태")
        if f["answer_state"] == "provided" and f["value"] is None:
            raise ValueError("제공됨 상태에는 값 필요")
        if f["answer_state"] != "provided" and f["value"] is not None:
            raise ValueError("미제공/없음/거부를 수치로 변환할 수 없음")
        if f["answer_state"] in {
            "explicit_none",
            "withheld",
            "not_applicable",
        } and not f.get("reason"):
            raise ValueError("상태 사유 필요")
        if f["answer_state"] != "not_provided" and not f["source_refs"]:
            raise ValueError("원답변/원자료 참조 필요")
        if f["value_type"] == "decimal" and f["value"] is not None:
            try:
                if (
                    not isinstance(f["value"], str)
                    or not Decimal(f["value"]).is_finite()
                ):
                    raise ValueError("수치는 유한 십진 문자열이어야 함")
            except InvalidOperation as e:
                raise ValueError("유효한 십진 문자열 필요") from e
            if (
                not f["unit"]
                or not f["scope"]
                or (f["period"] is None and not f.get("period_reason"))
            ):
                raise ValueError("수치의 단위·기간·범위 필요")
        for ref in f["source_refs"]:
            if ref.get("id") not in p["sources"] or not ref.get("locator"):
                raise ValueError("원자료/위치 누락")
        if f["kind"] == "derived" and (not f.get("formula") or not f.get("input_refs")):
            raise ValueError("계산식/입력 필요")
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("순환 계산: " + key)
        if key in visited:
            return
        visiting.add(key)
        for r in p["facts"][key].get("input_refs", []):
            if r["id"] not in p["facts"]:
                raise ValueError("계산 입력 없음")
            visit(r["id"])
        visiting.remove(key)
        visited.add(key)

    for key in p["facts"]:
        visit(key)


def _check_record_id(key):
    """Reject record IDs that cannot round-trip as canonical dict keys.

    A non-string ID (e.g. int 7) is silently re-keyed as "7" by json.dumps
    while the stored record keeps id=7, so the next load fails with
    "ID/개정번호 오류" — the write itself must be refused instead.  Blank
    IDs, surrounding whitespace, and control characters are forbidden
    formats: they survive serialization but produce unusable or
    misleading keys.  Called on every collection mutation path (all ops
    funnel through apply()).
    """
    if not isinstance(key, str):
        raise ValueError("변경 ID는 문자열이어야 함")
    if not key.strip():
        raise ValueError("변경 ID는 비어 있을 수 없음")
    if key != key.strip() or any(ord(c) < 0x20 or c == "\x7f" for c in key):
        raise ValueError("변경 ID에 양끝 공백·제어 문자를 사용할 수 없음")


def _check_ref(ref):
    if (
        not isinstance(ref, dict)
        or ref.get("collection") not in COLLECTIONS
        or not isinstance(ref.get("id"), str)
    ):
        raise ValueError("잘못된 대상 참조: " + str(ref))


def node(p, ref):
    _check_ref(ref)
    try:
        return p[ref["collection"]][ref["id"]]
    except KeyError as e:
        raise ValueError("대상 기록 없음: " + str(ref)) from e


def fingerprint(root, p, refs):
    seen = set()

    def resolve(ref):
        _check_ref(ref)
        k = (ref["collection"], ref["id"])
        if k in seen:
            return list(k)
        seen.add(k)
        v = copy.deepcopy(node(p, ref))
        v.pop("stale", None)
        if "path" in v:
            path = local(root, v["path"])
            content = path.read_bytes()
            v["actual_hash"] = (
                digest(draft(content.decode()))
                if ref["collection"] == "sections"
                else digest(content)
            )
        v["resolved_dependencies"] = [resolve(x) for x in v.get("depends_on", [])]
        if ref["collection"] == "outputs":
            v["resolved_targets"] = [resolve(x) for x in v.get("target_refs", [])]
        if ref["collection"] == "facts":
            v["resolved_sources"] = [
                resolve({"collection": "sources", "id": x["id"]})
                for x in v["source_refs"]
            ]
            v["resolved_inputs"] = [
                resolve({"collection": "facts", "id": x["id"]})
                for x in v.get("input_refs", [])
            ]
        return v

    targets = [resolve(r) for r in refs]
    rules = {}
    for key, rule in p["rules"].items():
        scope = rule.get("applies_to")
        resolved_scope = (
            isinstance(scope, list)
            and bool(scope)
            and all(
                isinstance(ref, dict)
                and ref.get("collection") in COLLECTIONS
                and isinstance(ref.get("id"), str)
                and ref["id"] in p[ref["collection"]]
                for ref in scope
            )
        )
        if not resolved_scope or any(
            (ref["collection"], ref["id"]) in seen for ref in scope
        ):
            rules[key] = rule
    return digest({"targets": targets, "rules": rules})


def fingerprint_v1(root, p, refs):
    """Schema-1 fingerprint formula, kept only for the schema upgrade path."""
    seen = set()

    def resolve(ref):
        _check_ref(ref)
        k = (ref["collection"], ref["id"])
        if k in seen:
            return list(k)
        seen.add(k)
        v = copy.deepcopy(node(p, ref))
        v.pop("stale", None)
        if "path" in v:
            path = local(root, v["path"])
            content = path.read_bytes()
            v["actual_hash"] = (
                digest(draft(content.decode()))
                if ref["collection"] == "sections"
                else digest(content)
            )
        v["resolved_dependencies"] = [resolve(x) for x in v.get("depends_on", [])]
        return v

    return digest({"targets": [resolve(r) for r in refs], "rules": p["rules"]})


def _upgrade_schema_records(root, p):
    """Recompute fingerprints under the schema-2 formula.

    A stored fingerprint that still matches the schema-1 formula means the
    record's real dependencies are unchanged: its fingerprint is rewritten
    with the new formula and it must not become stale.  A stored fingerprint
    that no longer matches means the dependencies actually changed: the
    record goes stale and keeps its old fingerprint until it is re-registered.
    """
    for col in ("reviews", "approvals", "outputs"):
        for v in p[col].values():
            refs = v.get("target_refs")
            try:
                v1_fp = fingerprint_v1(root, p, refs)
                new_fp = fingerprint(root, p, refs)
            except (KeyError, TypeError, OSError, ValueError):
                v["stale"] = True
                continue
            if v.get("input_fingerprint") == v1_fp:
                v["input_fingerprint"] = new_fp
                v["stale"] = False
            else:
                v["stale"] = True
    return p


def upgrade_schema(root):
    """Upgrade a schema-1 project to schema 2 without invalidating reviews.

    Fingerprints are recomputed with the new formula; records whose actual
    dependencies are unchanged must not become stale.  This is separate from
    the legacy Markdown-folder ``migrate`` import path.
    """
    root = Path(root)

    def body(capability):
        _lock_mod().assert_held(capability, root)
        p = load(root)
        if p["schema_version"] == 2:
            return p
        _upgrade_schema_records(root, p)
        p["schema_version"] = 2
        _commit_canonical(
            root,
            json.dumps(p, ensure_ascii=False, indent=2).encode(),
            operation="upgrade_schema",
            expect_revision=p["revision"],
        )
        return p

    return _guarded(root, "upgrade_schema", "canonical", body,
                    lambda: _recheck_canonical(root, 0))


def _bind_adoption_output(adoption, key, value):
    """Bind an output op to the adoption published in the same guard.

    Caller metadata must match the verified adoption preimage exactly;
    server-derived path/publication_ref are then set by A."""
    meta = adoption.output_value
    if key != meta.get("id"):
        raise _op("adopt_output", "canonical", "not_committed",
                  "adoption_metadata_mismatch", detail="id")
    for k, v in meta.items():
        if value.get(k) != v:
            raise _op("adopt_output", "canonical", "not_committed",
                      "adoption_metadata_mismatch", detail=k)
    value["path"] = adoption.managed_path
    value["publication_ref"] = adoption.publication_ref


def _bound_observation(root, provenance_path, provenance_hash):
    """Authenticate a review's bound observation before any canonical
    write (API §8/§9).  Returns the parsed observation dict; raises
    ValueError on missing/forged provenance, hash or schema mismatch."""
    if not isinstance(provenance_path, str) or not _check_sha256(
        provenance_hash
    ):
        raise ValueError("관측 provenance는 경로·해시 모두 필요")
    relative = Path(provenance_path)
    if (
        relative.parts != (".gg-observations", relative.name)
        or relative.name != provenance_hash + ".json"
    ):
        raise ValueError("관측 파일이 관측 저장소 규약과 다름")
    try:
        data = local(root, provenance_path).read_bytes()
    except (OSError, ValueError) as error:
        raise ValueError("관측 기록을 읽을 수 없음") from error
    if digest(data) != provenance_hash:
        raise ValueError("관측 기록 해시 불일치")
    try:
        observation = json.loads(data)
    except ValueError as error:
        raise ValueError("관측 기록 JSON 손상") from error
    if not isinstance(observation, dict):
        raise ValueError("관측 기록이 객체 아님")
    if observation.get("schema") not in (
        "gg-review-observation/1",
        "gg-review-observation/2",
    ):
        raise ValueError("관측 스키마 불일치")
    return observation


def _apply_locked(root, change, expected_revision, *, capability, adoption=None):
    """Canonical commit for apply and adopt_output (API §2/§5, SPEC §4-§7).

    The guard capability must be real, held by this process and bound to
    this root — assert_held runs first.  When ``adoption`` is given it must
    be a live A-registered context from the same capability and root.
    """
    _lock_mod().assert_held(capability, root)
    operation = "adopt_output" if adoption is not None else "apply"
    root = Path(root)
    if adoption is not None and not _adoption_ok(adoption, capability, root):
        raise _op(operation, "canonical", "not_committed",
                  "adoption_context_invalid")
    p = load(root)
    p.setdefault("requests", {})
    try:
        # Whole-ledger structural check happens in memory before any
        # product write — malformed entries are never 'different requests'.
        _check_request_ledger(p)
    except ValueError as error:
        raise _op(operation, "canonical", "not_committed",
                  "malformed_request_ledger") from error
    if p["schema_version"] == 1:
        # Route schema-1 projects through the fingerprint upgrade before
        # any apply-time comparison; do not reject or invalidate them.
        # S01: prove record IDs before anything persists — a rejected
        # change must leave canonical bytes untouched.  The upgrade itself
        # stays in memory so the whole call is one canonical commit (L11).
        for op in change.get("ops", []):
            if (
                isinstance(op, dict)
                and isinstance(op.get("value"), dict)
                and "id" in op["value"]
            ):
                _check_record_id(op["value"]["id"])
        _upgrade_schema_records(root, p)
        p["schema_version"] = 2
    request = change.get("request_id")
    if not isinstance(request, str) or not request:
        raise _op(operation, "canonical", "not_committed",
                  "request_id_required", detail="요청 ID 필요")
    chash = (
        adoption.request_sha256 if adoption is not None else digest(change)
    )
    kind = "adopt_output" if adoption is not None else "apply"
    entry = p.get("requests", {}).get(request)
    if entry is not None:
        try:
            entry_kind = _ledger_effective_kind(entry, p["revision"])
        except ValueError as error:
            raise _op(operation, "canonical", "not_committed",
                      "malformed_request_ledger") from error
        if entry_kind != kind or entry["hash"] != chash:
            raise _op(operation, "canonical", "not_committed",
                      "request_id_conflict", request_id=request)
        if adoption is not None:
            _verify_publication_ref(
                root, entry["publication_ref"], operation=operation
            )
        return p
    if adoption is None:
        # New generic request: the receipt namespace must also be free.
        if _find_request(root, request):
            raise _op(operation, "canonical", "not_committed",
                      "request_id_conflict", request_id=request)
    if p["revision"] != expected_revision:
        raise _op(operation, "canonical", "not_committed",
                  "revision_conflict", revision=p["revision"],
                  request_id=request)
    if "result_of" in change:
        origin = change["result_of"]
        if not isinstance(origin, dict):
            raise ValueError("작업 결과 실행 식별 필요")
        if not isinstance(origin.get("task_id"), str) or not origin["task_id"]:
            raise ValueError("작업 결과 작업 ID 필요")
        execution = p["tasks"].get(origin.get("task_id"), {}).get("execution", {})
        for identity in (origin, execution):
            if (
                not isinstance(identity, dict)
                or not isinstance(identity.get("external_task_id"), str)
                or not identity["external_task_id"]
                or type(identity.get("epoch")) is not int
                or identity["epoch"] < 1
                or type(identity.get("input_revision")) is not int
                or not 0 <= identity["input_revision"] <= expected_revision
            ):
                raise ValueError("작업 실행 ID·차수·입력 개정 필요")
        if (
            execution.get("status") != "running"
            or any(
                key not in origin
                or origin[key] != execution.get(key)
                for key in ("epoch", "external_task_id", "input_revision")
            )
        ):
            raise ValueError("취소·미확인·이전 실행의 작업 결과는 반영하지 않음")
        if not isinstance(execution.get("target_refs"), list):
            raise ValueError("실행 입력 대상 기록 필요")
        try:
            current_input = fingerprint(root, p, execution["target_refs"])
        except (KeyError, TypeError, OSError) as error:
            raise ValueError("실행 입력 대상을 확인할 수 없음") from error
        if execution.get("input_fingerprint") != current_input:
            raise ValueError("실행 이후 입력 변경: 작업 결과 반영 거부")
        allowed_targets = {
            (ref.get("collection"), ref.get("id"))
            for ref in execution["target_refs"]
        }
        for op in change.get("ops", []):
            if (
                not isinstance(op, dict)
                or not isinstance(op.get("value"), dict)
                or (
                    op.get("collection") != "tasks"
                    and (op.get("collection"), op["value"].get("id"))
                    not in allowed_targets
                )
            ):
                raise ValueError(
                    "작업 실행 대상 범위 밖 변경: "
                    + str(op.get("collection") if isinstance(op, dict) else op)
                )
    old = copy.deepcopy(p)
    meta_linked = set()
    for op in change.get("ops", []):
        col, value = op["collection"], copy.deepcopy(op["value"])
        if col not in COLLECTIONS:
            raise ValueError("허용되지 않은 변경 컬렉션")
        key = value["id"]
        _check_record_id(key)
        prev = p[col].get(key)
        value["revision"] = prev["revision"] + 1 if prev else 1
        if (
            col == "questions"
            and prev
            and value.get("decision_revision") != prev.get("decision_revision")
        ):
            if value.get("resume_reason") not in {
                "new_evidence",
                "user_change",
                "explicit_resume",
            } or not value.get("source_ref"):
                raise ValueError("질문 재개에는 새 근거/사용자 결정 필요")
            if (
                value["decision_revision"] != prev["decision_revision"] + 1
                or value["source_ref"].get("id") not in p["sources"]
            ):
                raise ValueError("질문 재개 근거/결정 개정 불일치")
        elif (
            col == "questions"
            and prev
            and value.get("attempts", -1) < prev["attempts"]
        ):
            raise ValueError("동일 결정의 질문 횟수를 초기화할 수 없음")
        if col == "sections":
            value["draft_hash"] = digest(
                draft(local(root, value["path"]).read_text(encoding="utf-8"))
            )
            claims = value.get("claims")
            if isinstance(claims, list):
                # design-r2 §7 apply row: list-shaped claims are the P3
                # opt-in — identifier structure is rejected pre-commit.
                # Fail-closed (L16-001): only the two reason patterns the
                # evidentiary record proves safe to defer are exempted —
                # unsupported-key and missing-field checks are diagnosed
                # downstream in checks() (seeded claims legitimately carry
                # unit/period=None).  Every other issue, including any
                # future validate_claim_shape reason, is write-rejected.
                # Dict/absent claims stay legacy, read-preserved.
                reasons = []
                for entry in claims:
                    for issue in _finance_body_mod().validate_claim_shape(
                            entry,
                            location={
                                "path": value.get("path"),
                                "section_id": key, "line": None,
                                "column": None, "table": None,
                                "row": None, "cell": None}):
                        reason = issue["reason"]
                        if not reason.startswith((
                                "지원하지 않는 claim key:",
                                "claim 필수 필드 누락:")):
                            reasons.append(reason)
                if reasons:
                    raise ValueError(
                        "section claim 구조 오류: "
                        + "; ".join(sorted(set(reasons))))
        if col == "facts" and prev is None:
            # Lane 23 (architect finding C7-FINAL-3 / design-r2 §2): a
            # NEW fact record carrying explicit P3 metadata is admitted
            # under validate_metadata's strict mode="new" — unregistered
            # meaning_id (vs the installed registry), unknown measure
            # keys/kinds, non-KRW monetary currency, unsupported
            # unit_price denominator/basis and invalid finance_role all
            # reject pre-commit instead of surfacing only later in
            # checks().  Pure-legacy facts (meaning_id/measure/
            # finance_role all absent) return [] — zero new friction.
            # Updates are not re-admitted (prev is not None -> skip),
            # mirroring the reviews admission gate below: an existing
            # record's metadata was already checked at its own creation,
            # and re-validating on every revision could reject
            # legitimate edits to unrelated fields.
            meta_issues = _fact_semantics_mod().validate_metadata(
                value,
                registry=_semantic_context(root, p).get("registry"),
                mode="new")
            if any(i.get("status") == "fail" for i in meta_issues):
                raise ValueError(
                    "신규 사실 메타데이터 오류: "
                    + "; ".join(
                        sorted({i["reason"] for i in meta_issues})))
        if col == "sources":
            value["hash"] = digest(local(root, value["path"]).read_bytes())
        if col == "outputs":
            if adoption is not None:
                _bind_adoption_output(adoption, key, value)
            elif _meta_only_link(prev, value):
                _meta_only_link_verify(root, p, key, value, prev)
                meta_linked.add(key)
            else:
                raise _op(
                    operation, "canonical", "not_committed",
                    "adopt_output_required",
                    detail="출력 등록은 adopt_output 경유만 허용",
                    request_id=request,
                )
        if col == "approvals" and value.get("kind") == "professor":
            if value.get("input_revision") != expected_revision:
                raise ValueError("교수 승인 입력 개정 불일치")
            evidence_rel = value.get("evidence_path")
            if not isinstance(evidence_rel, str) or not evidence_rel.strip():
                raise ValueError("교수 승인 근거 파일 필요")
            evidence_bytes = local(root, evidence_rel).read_bytes()
            if value.get("evidence_hash") != digest(evidence_bytes):
                raise ValueError("교수 승인 근거 해시 불일치")
            value["registered_revision"] = expected_revision + 1
        if col == "reviews":
            for required in (
                "review_kind",
                "author_id",
                "reviewer_id",
                "target_refs",
                "input_fingerprint",
                "findings",
                "disposition",
                "status",
                "path",
            ):
                if required not in value:
                    raise ValueError("검토 필드 누락: " + required)
            if value["author_id"] == value["reviewer_id"]:
                raise ValueError("자가검토는 독립검토가 아님")
            if not value["target_refs"] or not value.get("coverage"):
                raise ValueError("검토 대상과 축별 위치 근거 필요")
            if value["input_fingerprint"] != fingerprint(
                root, p, value["target_refs"]
            ):
                raise ValueError("검토 입력 버전 불일치")
            value["report_hash"] = digest(
                local(root, value["path"]).read_bytes())
            if value.get("provenance_path") or value.get("provenance_hash"):
                observed = _bound_observation(
                    root,
                    value.get("provenance_path"),
                    value.get("provenance_hash"),
                )
                if observed["schema"] == "gg-review-observation/2":
                    # A schema2-bound review carries the reviewed input
                    # revision R from its immutable observation; the
                    # canonical commit produces R+1 separately (API §8/§9).
                    # The full binding is checked before any write and an
                    # omitted input_revision cannot bypass it.
                    #
                    # apply-time workspace/protocol binding (API §8/§9):
                    # a genuine receipt produced in one control workspace
                    # must not be borrowable by copying its immutable bytes
                    # into a different (or foreign-lineage) workspace and
                    # then applying there. This check runs BEFORE any
                    # canonical/history/ledger write; on rejection nothing
                    # is committed (S01/L11: rejected changes leave
                    # canonical bytes untouched).
                    if not _observation_workspace_trusted(root, observed):
                        raise ValueError(
                            "관측 워크스페이스·프로토콜 바인딩 불일치"
                        )
                    if type(value.get("input_revision")) is not int:
                        raise ValueError("관측 연계 검토의 입력 개정 필수")
                    if value["input_revision"] != observed.get(
                        "input_revision"
                    ):
                        raise ValueError("검토 입력 개정이 관측 개정과 다름")
                    if value["path"] != observed.get("report_path"):
                        raise ValueError("검토 보고서 경로가 관측과 다름")
                    if not isinstance(
                        observed.get("review_kinds"), list
                    ) or value["review_kind"] not in observed["review_kinds"]:
                        raise ValueError("관측 검토 종류 불일치")
                    for bound_key in (
                        "author_id",
                        "reviewer_id",
                        "target_refs",
                        "input_fingerprint",
                        "report_hash",
                    ):
                        if observed.get(bound_key) != value.get(bound_key):
                            raise ValueError(
                                "관측·검토 연계 필드 불일치: " + bound_key
                            )
                else:
                    # schema-1 bound observation: legacy registration
                    # revision convention is preserved.
                    if (
                        "input_revision" in value
                        and value["input_revision"] != expected_revision + 1
                    ):
                        raise ValueError("검토 입력 개정은 등록 개정이어야 함")
                    value["input_revision"] = expected_revision + 1
            elif (
                "input_revision" in value
                and value["input_revision"] != expected_revision + 1
            ):
                raise ValueError("검토 입력 개정은 등록 개정이어야 함")
            else:
                value["input_revision"] = expected_revision + 1
            # P3 wiring (design-r2 §7 'apply → _apply_locked'): a NEW
            # content/logic/calculation-kind review over an
            # economic-scoped fact must bind a readable, hash-matched
            # gg-finance-semantics-report/1 report before commit —
            # the same three-kind set _finance_review_state and
            # _economic_output_fresh treat uniformly at read time
            # (Lane 27, C7-L26-001); updates to existing records are
            # not re-admitted (prev is not None -> skip).
            if (
                prev is None
                and value.get("review_kind")
                    in ("content", "logic", "calculation")
                and _review_targets_economic_fact(
                    p, value.get("target_refs"), None)
            ):
                _validate_new_economic_review_binding(
                    root, value, p=p,
                    target_refs=value.get("target_refs"))
        p[col][key] = value
    validate(p)
    p["revision"] += 1
    for col in ("reviews", "approvals", "outputs"):
        for v in p[col].values():
            try:
                v["stale"] = v.get("input_fingerprint") != fingerprint(
                    root, p, v["target_refs"]
                )
            except (KeyError, OSError, ValueError):
                v["stale"] = True
    if meta_linked:
        _meta_only_links_final_check(root, p, meta_linked)
    p["history"].append(
        {"revision": old["revision"], "hash": digest(old), "request_id": request}
    )
    if adoption is not None:
        p["requests"][request] = {
            "hash": chash,
            "revision": p["revision"],
            "kind": "adopt_output",
            "publication_ref": adoption.publication_ref,
        }
    else:
        p["requests"][request] = {"hash": chash, "revision": p["revision"]}
    snapshot_rel = "migration/revision-%s.json" % old["revision"]
    try:
        atomic(
            local(root, snapshot_rel),
            json.dumps(old, ensure_ascii=False, indent=2).encode(),
        )
    except OSError as error:
        # History snapshot failed before the canonical commit — the
        # canonical is still untouched → not_committed.
        raise _op(
            operation, "canonical", "not_committed", "history_write_failed",
            detail=str(error), request_id=request,
        ) from error
    _commit_canonical(
        root,
        json.dumps(p, ensure_ascii=False, indent=2).encode(),
        operation=operation,
        expect_revision=p["revision"],
        request_id=request,
        preserved_paths=[snapshot_rel],
    )
    return p


def apply(root, change, expected_revision):
    """Generic canonical apply (API §2).  commit_state always comes from
    rechecking storage, never from the exception type alone."""
    root = Path(root)
    request_id = change.get("request_id") if isinstance(change, dict) else None

    def body(capability):
        try:
            return _apply_locked(
                root, change, expected_revision, capability=capability
            )
        except OperationError:
            raise
        except ValueError as error:
            raise _op(
                "apply", "canonical", "not_committed", "invalid_change",
                detail=str(error), request_id=request_id,
            ) from error

    return _guarded(
        root, "apply", "canonical", body,
        lambda: _recheck_canonical(
            root,
            expected_revision + 1 if type(expected_revision) is int else -1,
            request_id,
        ),
    )


def _check_adopt_output_value(output_value):
    """Caller metadata for adopt_output (API §5): reject server keys and
    noncanonical refs here, never silently rewrite them."""
    if not isinstance(output_value, dict):
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail="output_value not dict")
    server_keys = {"publication_ref", "revision", "stale"} & set(output_value)
    if server_keys:
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata",
                  detail="server keys: " + str(sorted(server_keys)))
    required = {"id", "path", "format", "file_hash",
                "target_refs", "input_fingerprint"}
    missing = required - set(output_value)
    if missing:
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail=sorted(missing))
    meta = dict(output_value)
    _check_record_id(meta["id"])
    if not isinstance(meta["path"], str) or not meta["path"].strip():
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail="path")
    if not isinstance(meta["format"], str) or not meta["format"].strip():
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail="format")
    if not _check_sha256(meta["file_hash"]):
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail="file_hash")
    if not _check_sha256(meta["input_fingerprint"]):
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail="input_fingerprint")
    try:
        meta["target_refs"] = sort_target_refs(meta["target_refs"])
    except ValueError as error:
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_output_metadata", detail=str(error)) from error
    return meta


def _check_companions(companion_files):
    companions = []
    for entry in companion_files or []:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry["path"], str)
            or not entry["path"].strip()
            or not _check_sha256(entry["sha256"])
        ):
            raise _op("adopt_output", "canonical", "not_committed",
                      "invalid_companion_files", detail=str(entry))
        companions.append({"path": entry["path"], "sha256": entry["sha256"]})
    companions.sort(key=lambda e: e["path"].encode("utf-8"))
    paths = [e["path"] for e in companions]
    if len(set(paths)) != len(paths):
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_companion_files", detail="duplicate path")
    return companions


def _publication_ref_for(root, receipt, receipt_rel):
    """Derive the publication_ref for an existing on-disk receipt."""
    publication_id = receipt.get("publication_id")
    if not isinstance(publication_id, str) or not publication_id:
        raise _op("adopt_output", "canonical", "indeterminate",
                  "committed_publication_unverified")
    try:
        raw = local(root, receipt_rel).read_bytes()
    except (OSError, ValueError) as error:
        raise _op("adopt_output", "canonical", "indeterminate",
                  "committed_publication_unverified", detail=str(error))
    return {
        "schema": "gg-publication-ref/1",
        "id": publication_id,
        "path": receipt_rel,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def adopt_output(root, output_value, expected_revision, request_id,
                 companion_files=None):
    """Register an existing output file through a managed publication
    (API §5).  Only this path may create new outputs records; the managed
    copy under .gg-artifacts/<id> is what the canonical path records."""
    root = Path(root)
    meta = _check_adopt_output_value(output_value)
    companions = _check_companions(companion_files)
    if type(expected_revision) is not int or expected_revision < 0:
        raise _op("adopt_output", "canonical", "not_committed",
                  "invalid_expected_revision")
    if not isinstance(request_id, str) or not request_id.strip():
        raise _op("adopt_output", "canonical", "not_committed",
                  "request_id_required", detail="요청 ID 필요")
    request = {
        "kind": "adopt_output",
        "expected_revision": expected_revision,
        "output_value": meta,
        "companion_files": companions,
    }
    request_sha = _request_sha256(request)

    def body(capability):
        p = load(root)
        p.setdefault("requests", {})
        try:
            _check_request_ledger(p)
        except ValueError as error:
            raise _op("adopt_output", "canonical", "not_committed",
                      "malformed_request_ledger") from error
        entry = p["requests"].get(request_id)
        if entry is not None:
            try:
                entry_kind = _ledger_effective_kind(entry, p["revision"])
            except ValueError as error:
                raise _op("adopt_output", "canonical", "not_committed",
                          "malformed_request_ledger") from error
            if entry_kind != "adopt_output" or entry["hash"] != request_sha:
                raise _op("adopt_output", "canonical", "not_committed",
                          "request_id_conflict", request_id=request_id)
            _verify_publication_ref(
                root, entry["publication_ref"], operation="adopt_output"
            )
            stored = p["outputs"].get(meta["id"], {})
            return {
                "path": str(local(root, stored.get("path", ""))),
                "publication_ref": entry["publication_ref"],
                "status": "existing",
            }
        if p["revision"] != expected_revision:
            raise _op("adopt_output", "canonical", "not_committed",
                      "revision_conflict", revision=p["revision"],
                      request_id=request_id)
        # Verify caller claims against actual files while holding the guard.
        if meta["input_fingerprint"] != fingerprint(root, p, meta["target_refs"]):
            raise _op("adopt_output", "canonical", "not_committed",
                      "input_fingerprint_mismatch", request_id=request_id)
        # Canonical source read FIRST (Lane 20 / L19-001): missing or
        # invalid source paths must surface their original errors —
        # invalid_output_metadata / output_source_missing /
        # output_hash_mismatch — before any economic-evidence evaluation,
        # identically for economic and non-economic outputs.
        try:
            source = local(root, meta["path"])
        except ValueError as error:
            raise _op("adopt_output", "canonical", "not_committed",
                      "invalid_output_metadata", detail=str(error)) from error
        if meta["path"].startswith(".gg-artifacts/"):
            raise _op("adopt_output", "canonical", "not_committed",
                      "invalid_output_metadata",
                      detail="managed path cannot be a source")
        try:
            source_bytes = source.read_bytes()
        except OSError as error:
            raise _op("adopt_output", "canonical", "not_committed",
                      "output_source_missing", detail=str(error)) from error
        if meta["file_hash"] != digest(source_bytes):
            raise _op("adopt_output", "canonical", "not_committed",
                      "output_hash_mismatch", request_id=request_id)
        # P3 wiring (design-r2 §7 'adopt_output': 새 경제 output은 바인딩
        # report를 checks[].evidence_path/hash로 제공하고 실제 file hash와
        # 대조): a NEW output over an economic-scoped fact must carry at
        # least one checks entry resolving to a byte-matched, schema-valid
        # gg-finance-semantics-report/1 report — fixed integrity AND the
        # Lane-25 write-time freshness stage: the helper re-derives
        # current registry/refs/fingerprint over the live project and
        # rejects a report declared against stale state (read-time
        # re-verification in _finance_review_state stays in addition,
        # not instead).  checks entries
        # speak evidence_path/evidence_hash while the Lane-7 helper
        # expects a review value's path/report_hash — same thin-adapter
        # pattern as Lane 8.  Non-dict/malformed entries simply do not
        # qualify; for non-economic outputs a checks key stays inert
        # caller metadata (unchanged P2 behavior).
        if _review_targets_economic_fact(
            p, meta["target_refs"],
            _semantic_context(root, p).get("registry")
        ):
            # §7 adopt row (Lane 19): the qualifying report must also be
            # bound to THIS output's bytes via semantics.output_file_hash
            # (schema's null|Sha256 — null or mismatched never qualifies).
            # The helper above already proved evidence_path's bytes are
            # hash-matched + schema-valid, so re-reading/re-parsing here
            # is safe; the helper stays untouched (its other callers only
            # need the raise-or-None contract).  The source bytes were
            # already read and file_hash-verified above.
            adopted_digest = digest(source_bytes)
            bound = False
            for entry in meta.get("checks") or []:
                if not isinstance(entry, dict):
                    continue
                try:
                    _validate_new_economic_review_binding(
                        root,
                        {"path": entry.get("evidence_path"),
                         "report_hash": entry.get("evidence_hash")},
                        p=p, target_refs=meta["target_refs"])
                except ValueError:
                    continue
                try:
                    report = json.loads(
                        local(
                            root, entry["evidence_path"]).read_bytes())
                except (OSError, ValueError):
                    continue
                if (not isinstance(report, dict)
                        or not isinstance(report.get("semantics"), dict)
                        or report["semantics"].get("output_file_hash")
                        != adopted_digest):
                    continue
                bound = True
                break
            if not bound:
                raise _op(
                    "adopt_output", "canonical", "not_committed",
                    "economic_evidence_missing",
                    detail="경제 출력은 유효한 의미 보고서 checks 항목 필요",
                    request_id=request_id)
        # The intent binds the *actual* project refs/fingerprint at
        # expected_revision — the caller's declared refs live only inside
        # the request's output_value (API §5/§6).
        actual_refs = sort_target_refs(_export_refs(p))
        actual_fp = fingerprint(root, p, actual_refs)
        for companion in companions:
            try:
                companion_path = local(root, companion["path"])
            except ValueError as error:
                raise _op("adopt_output", "canonical", "not_committed",
                          "companion_mismatch", detail=str(error)) from error
            try:
                companion_bytes = companion_path.read_bytes()
            except OSError as error:
                raise _op("adopt_output", "canonical", "not_committed",
                          "companion_mismatch", detail=str(error)) from error
            if companion["sha256"] != digest(companion_bytes):
                raise _op("adopt_output", "canonical", "not_committed",
                          "companion_mismatch", detail=companion["path"])
        # Resume check: same request_id already published but unregistered.
        publication_ref = None
        managed_path = None
        receipts = _find_request(root, request_id)
        if receipts:
            receipt = receipts[0]
            if receipt.get("request_sha256") != request_sha:
                raise _op("adopt_output", "canonical", "not_committed",
                          "request_id_conflict", request_id=request_id)
            publication_ref = _publication_ref_for(
                root,
                receipt,
                ".gg-artifacts/%s/.publication.json"
                % receipt["publication_id"],
            )
            managed_path = ".gg-artifacts/%s/%s" % (
                receipt["publication_id"], receipt.get("primary_path", "")
            )
        else:
            suffix = Path(meta["path"]).suffix
            primary_dest = "primary" + suffix
            intent = {
                "request": request,
                "request_id": request_id,
                "request_sha256": request_sha,
                "input_revision": expected_revision,
                "target_refs": actual_refs,
                "input_fingerprint": actual_fp,
            }
            sources = [
                {
                    "path": meta["path"],
                    "sha256": meta["file_hash"],
                    "size": len(source_bytes),
                    "destination": primary_dest,
                }
            ]
            for companion in companions:
                cpath = local(root, companion["path"])
                sources.append(
                    {
                        "path": companion["path"],
                        "sha256": companion["sha256"],
                        "size": cpath.stat().st_size,
                        "destination": "companions/" + Path(companion["path"]).name,
                    }
                )
            sources.sort(key=lambda s: s["path"].encode("utf-8"))
            publication = _publication_mod()
            try:
                result = publication.publish_output(
                    root, capability, intent=intent, sources=sources
                )
            except Exception as error:
                state = getattr(error, "result", {}).get("state")
                if state == "not_published":
                    raise _op("adopt_output", "canonical", "not_committed",
                              "managed_publish_failed", detail=str(error),
                              request_id=request_id) from error
                raise _op("adopt_output", "canonical", "indeterminate",
                          "managed_publish_indeterminate", detail=str(error),
                          publication_state=state, request_id=request_id,
                          ) from error
            if result.get("state") != "published":
                raise _op("adopt_output", "canonical", "indeterminate",
                          "managed_publish_unexpected",
                          publication_state=result.get("state"),
                          request_id=request_id)
            publication_ref = result["publication_ref"]
            managed_path = "%s/%s" % (
                result["destination"], primary_dest
            )
        # Register the output under the same guard acquisition.
        ctx = _register_adoption(
            capability=capability,
            root=root,
            request_sha256=request_sha,
            output_value=meta,
            publication_ref=publication_ref,
            managed_path=managed_path,
        )
        try:
            _apply_locked(
                root,
                {
                    "request_id": request_id,
                    "ops": [{"collection": "outputs",
                             "value": dict(meta)}],
                },
                expected_revision,
                capability=capability,
                adoption=ctx,
            )
        finally:
            _ADOPTION_CONTEXTS.discard(ctx)
        return {
            "path": str(local(root, managed_path)),
            "publication_ref": publication_ref,
            "status": "adopted",
        }

    return _guarded(
        root, "adopt_output", "canonical", body,
        lambda: _recheck_canonical(root, expected_revision + 1, request_id),
    )

def question(p, field_id):
    facts = [f for f in p["facts"].values() if f["field_id"] == field_id]
    if any(
        f["answer_state"] in {"provided", "explicit_none", "not_applicable", "withheld"}
        for f in facts
    ):
        return "reuse"
    q = p["questions"].get(field_id, {})
    return ("ask", "help", "deferred")[min(q.get("attempts", 0), 2)]


def user_finish_task_list(p):
    formats = p.get("rules", {}).get("output_formats", {}).get("formats", [])
    items = []
    if "hwp" in formats:
        items.append(
            (
                "hwp_convert",
                "DOCX를 한글에서 HWP로 저장한 뒤 재열기·렌더·표/인용/핵심 수치 대조",
            )
        )
    items.extend(
        (
            ("font_shinmyeongjo", "글꼴 신명조 확인·변경"),
            ("margins", "여백 위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm"),
            ("page_numbers", "페이지 번호"),
        )
    )
    return [
        {
            "id": cid,
            "status": "needs_user",
            "owner": "user",
            "missing": [],
            "reason": reason,
        }
        for cid, reason in items
    ]


def next_tasks(p):
    result = []
    for t in p["tasks"].values():
        missing = []
        for fid in t.get("required_fields", []):
            if not any(
                f["field_id"] == fid and f["answer_state"] == "provided"
                for f in p["facts"].values()
            ):
                missing.append(fid)
        result.append(
            {
                "id": t["id"],
                "status": (
                    "needs_user"
                    if missing and t.get("user_owned")
                    else "needs_evidence" if missing else "ready"
                ),
                "missing": missing,
            }
        )
    result.extend(user_finish_task_list(p))
    return result


def result(
    cid,
    target,
    status,
    reason,
    revision,
    severity="error",
    evidence=None,
    owner="skill",
    required_for=None,
):
    if owner not in {"skill", "user", "professor"}:
        raise ValueError("검사 소유자 오류")
    if required_for is None:
        required_for = ["submission_candidate"] if severity == "error" else []
    return dict(
        check_id=cid,
        target=target,
        status=status,
        severity=severity,
        reason=reason,
        evidence=evidence or [],
        owner=owner,
        required_for=list(required_for),
        input_revision=revision,
        checker_version=VERSION,
    )


VISUAL_FAIL = {
    "FONT-SUBSTITUTION": "font",
    "TOC-PAGE-NUMBERS": "render",
    "WIDE-TABLE-MONTH-WRAP": "render",
    "MISSING-VISIBLE-FIGURE": "render",
    "EXCEL-PRINT-CLIPPING": "render",
}


def visual_review_blocks(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return set()
    blocked = set()
    for finding in data["findings"]:
        if not isinstance(finding, dict):
            continue
        cid = VISUAL_FAIL.get(finding.get("id"))
        if cid and finding.get("status") == "fail":
            blocked.add(cid)
    if data.get("independent_review") == "not_run" or data.get("self_review"):
        blocked.add("independent_review")
    return blocked


def output_lineage(p):
    """Validate ``superseded_by`` chains and classify every output.

    Each verdict is ``current`` (no valid successor), ``history`` (superseded
    through a valid chain whose terminal output is not stale), or ``invalid``
    (broken relation: self-reference, cycle, missing target, format mismatch,
    or a stale terminal).  Stale alone never makes an output history, a
    broken relation never removes the output from inspection candidacy, and
    filenames/dates/student IDs are never consulted.
    """
    outputs = p.get("outputs", {})
    verdict = {}
    for oid in outputs:
        trail = [oid]
        cur = oid
        reason = None
        terminal = None
        while True:
            link = outputs[cur].get("superseded_by")
            if link is None:
                terminal = cur
                break
            if not isinstance(link, str) or not link:
                reason = "superseded_by가 출력 ID 문자열이 아님"
                break
            if link == cur:
                reason = "superseded_by 자기 참조"
                break
            if link not in outputs:
                reason = "superseded_by 대상 출력 없음: " + link
                break
            if link in trail:
                reason = "superseded_by 순환: " + " -> ".join(trail + [link])
                break
            if outputs[link].get("format") != outputs[cur].get("format"):
                reason = "superseded_by 대상과 format 불일치: " + link
                break
            trail.append(link)
            cur = link
        if reason is not None:
            verdict[oid] = {"state": "invalid", "reason": reason, "terminal": None}
        elif oid == terminal:
            verdict[oid] = {"state": "current", "reason": None, "terminal": oid}
        elif outputs[terminal].get("stale") is True:
            verdict[oid] = {
                "state": "invalid",
                "reason": "superseded_by 최신 출력이 stale: " + terminal,
                "terminal": terminal,
            }
        else:
            verdict[oid] = {"state": "history", "reason": None, "terminal": terminal}
    return verdict


def _meta_only_link(prev, value):
    """superseded_by 첫 연결만 하는 metadata-only 갱신 후보인지 판별한다.

    서버가 관리하는 revision과 superseded_by를 제외한 모든 필드·키 존재가
    저장 레코드와 같아야 한다. id/path/format/file_hash/target_refs/
    input_fingerprint/checks/depends_on/stale 등을 숨겨 바꾸는 면제 경로는
    없다 — 하나라도 다르면 False로 기존 엄격 검증 경로에 남긴다.
    """
    if prev is None:
        return False
    skip = ("superseded_by", "revision")
    base = {k: v for k, v in prev.items() if k not in skip}
    cand = {k: v for k, v in value.items() if k not in skip}
    return (
        cand == base
        and value.get("superseded_by") != prev.get("superseded_by")
    )


def _lineage_terminal_verify(root, p, terminal):
    """계보 끝단의 실제 현재성 — 저장된 stale 플래그를 신뢰하지 않는다."""
    try:
        current = fingerprint(root, p, terminal["target_refs"])
    except (KeyError, TypeError, OSError, ValueError) as e:
        raise ValueError(
            "계보 끝단의 실제 입력을 확인할 수 없음: " + str(e)
        ) from e
    if terminal.get("input_fingerprint") != current:
        raise ValueError("계보 끝단의 실제 입력이 등록 지문과 다름")
    # Every P2 output is registered through adopt_output — a lineage
    # terminal without a verifiable adopt publication is not a valid
    # terminal, and a receipt that fails verification fails the link.
    ref = terminal.get("publication_ref")
    if not isinstance(ref, dict):
        raise ValueError("계보 끝단의 발행 근거 없음")
    try:
        _publication_mod().verify_publication(
            root, publication_ref=ref, expected={"kind": "adopt_output"}
        )
    except Exception as e:
        raise ValueError("계보 끝단 발행 검증 실패: " + str(e)) from e
    try:
        actual = digest(local(root, terminal["path"]).read_bytes())
    except (KeyError, OSError, ValueError) as e:
        raise ValueError(
            "계보 끝단의 실제 파일을 확인할 수 없음: " + str(e)
        ) from e
    if terminal.get("file_hash") != actual:
        raise ValueError("계보 끝단 파일이 등록 해시와 다름")
    # Lane 24: an economic-scoped terminal additionally requires its
    # bound economic evidence to still be fresh — fingerprint/
    # publication/file-hash alone cannot prove the underlying report/
    # review has not drifted.  Non-economic terminals are unaffected
    # (the predicate returns True for them).
    if not _economic_output_fresh(root, p, terminal):
        raise ValueError("계보 끝단의 경제 근거가 최신이 아님")


def _meta_only_link_verify(root, p, key, value, prev):
    """첫 superseded_by 이력 연결의 전용 검증 — 실패는 폴백 없이 즉시 거부."""
    link = value.get("superseded_by")
    if prev.get("superseded_by") is not None:
        raise ValueError(
            "기존 superseded_by 재연결·해제는 이 경로로 할 수 없음: "
            + str(prev.get("superseded_by"))
        )
    if not isinstance(link, str) or not link:
        raise ValueError("superseded_by가 출력 ID 문자열이 아님")
    if link == key:
        raise ValueError("superseded_by 자기 참조")
    if link not in p["outputs"]:
        raise ValueError("superseded_by 대상 출력 없음: " + link)
    if p["outputs"][link].get("format") != prev.get("format"):
        raise ValueError("superseded_by 대상과 format 불일치: " + link)
    # 원 출력 파일이 존재하면 등록 해시와 실제 바이트가 같아야 한다.
    # 파일 부재만 기존 유실로 허용하고, 읽기 오류·루트 밖 경로·다른 바이트를
    # 유실로 바꾸지 않는다 — 원 path/hash와 유실 사실은 레코드에 보존된다.
    if prev.get("path"):
        try:
            content = local(root, prev["path"]).read_bytes()
        except FileNotFoundError:
            content = None
        except OSError as e:
            raise ValueError(
                "이력 원 출력 파일을 읽을 수 없음: " + str(e)
            ) from e
        if content is not None and digest(content) != prev.get("file_hash"):
            raise ValueError("이력 원 출력 파일이 등록 해시와 다름")
    scratch = copy.deepcopy(p)
    scratch["outputs"][key] = value
    after = output_lineage(scratch)
    if after[key]["state"] != "history":
        raise ValueError(
            "superseded_by 계보 무효: " + str(after[key]["reason"])
        )
    _lineage_terminal_verify(
        root, scratch, scratch["outputs"][after[key]["terminal"]]
    )
    before = output_lineage(p)
    for oid, verdict in after.items():
        if verdict["state"] == "invalid" and before[oid]["state"] != "invalid":
            raise ValueError(
                "superseded_by 연결이 다른 출력을 무효화: " + oid
            )


def _meta_only_links_final_check(root, p, linked):
    """트랜잭션 최종 상태에서 이번 경로로 추가한 링크를 다시 확인한다.

    같은 요청의 뒤 op나 기존 stale 재계산이 새 연결의 끝단을 무효화했을 수
    있으므로 기록·원자 쓰기 전에 유효 history와 끝단의 실제 입력·파일을 다시
    검증한다. 실패하면 요청 전체가 미반영이다. 이미 있던 무관한 invalid는
    유지하되, 이 연결을 지나는 출력이 새로 invalid가 되는 것은 거부한다.
    """
    lineage = output_lineage(p)
    for oid in linked:
        verdict = lineage[oid]
        if verdict["state"] != "history":
            raise ValueError(
                "최종 상태의 superseded_by 계보 무효: "
                + str(verdict["reason"])
            )
        _lineage_terminal_verify(root, p, p["outputs"][verdict["terminal"]])
    for oid, verdict in lineage.items():
        if verdict["state"] != "invalid" or oid in linked:
            continue
        cur, seen = oid, {oid}
        while True:
            nxt = p["outputs"][cur].get("superseded_by")
            if (
                not isinstance(nxt, str)
                or not nxt
                or nxt not in p["outputs"]
                or nxt in seen
            ):
                break
            seen.add(nxt)
            cur = nxt
        if seen & linked:
            raise ValueError(
                "superseded_by 연결이 다른 출력을 무효화: " + oid
            )


def _export_refs(p):
    """Direct input refs for export publish/resume fingerprints.

    All sources/facts/sections stay, and so do outputs judged ``current``
    or ``invalid`` by ``output_lineage`` — stale flags, filenames, and
    dates never decide.  Only valid ``history`` outputs leave the direct
    set; ``fingerprint`` still resolves them recursively whenever a
    kept node's ``depends_on``/``target_refs`` points at them.
    """
    lineage = output_lineage(p)
    return [
        {"collection": collection, "id": key}
        for collection in ("sources", "facts", "sections", "outputs")
        for key in sorted(p[collection])
        if collection != "outputs" or lineage[key]["state"] != "history"
    ]


def _finance_body_mod():
    """P3 C-lane integration point (lane B, gg_finance_body.py). Lazily
    imported; never imported by gg_finance_body itself."""
    import gg_finance_body
    return gg_finance_body


def _fact_semantics_mod():
    """P3 S-lane integration point (gg_fact_semantics.py). Lazily
    imported; never imported by gg_fact_semantics itself."""
    import gg_fact_semantics
    return gg_fact_semantics


def _statistical_ref_verdict(source, ref, fact=None):
    """P4 verification hook (stage-g005 D07/D09): a fact's source_ref that
    carries an ``audit_key`` claims audited statistical provenance — it
    must resolve through the provenance chain in gg_rda_research, and the
    submitted fact itself (value/unit/locator/source_pdf_sha256) is
    cross-checked against the trusted bound receipt.  The P4 layer being
    absent, a malformed key, an unverifiable pin, an unregistered key, or
    a mismatched claim all fail closed — never silently accepted."""
    try:
        import gg_rda_research
    except ImportError:
        return {"status": "invalid",
                "reason": "통계 출처 검증 계층(gg_rda_research) 없음"}
    try:
        return gg_rda_research.audit_source_ref(source, ref, claim=fact)
    except Exception as exc:  # verification must never crash checks()
        return {"status": "invalid", "reason": "통계 출처 검증 실패: %s" % exc}


def _semantic_context(root, p):
    """Minimal read-only Context snapshot for the P3 fact-semantics
    helpers (design-r2 §5/§7).  The installed registry file
    (skills/knuaf-doc/references/fact-semantics.json) is resolved
    relative to THIS module's own deployment location — scripts/ ->
    knuaf-doc/references/, the same path the test harness's
    _installed_bytes computes — never inside the user's project root
    (Lane 28: the project workspace is a separate
    directory; a co-deployed skill resource must come from the skill's
    own tree).  Missing, unreadable, unparseable or wrong-schema files
    fall back to registry=None so legacy/pre-S-lane trees are
    unaffected.  No InputSnapshot files are deployed, so
    inputs/target_refs stay empty and input_fingerprint stays None;
    input_revision pins the project's current revision for later
    freshness checks."""
    registry = None
    try:
        raw = (
            Path(__file__).resolve().parent.parent
            / "references" / "fact-semantics.json"
        ).read_bytes()
    except (OSError, ValueError):
        raw = None
    if raw is not None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = None
        if (isinstance(data, dict)
                and data.get("schema") == "gg-fact-semantics-registry/1"):
            registry = {
                "version": data.get("version"),
                "sha256": digest(raw),
                "meanings": data.get("meanings") or {},
                "consumer_bindings": data.get("consumer_bindings") or [],
            }
    return {
        "registry": registry,
        "inputs": [],
        "evidence": {"sources": {}, "reviews": {}, "approvals": {}},
        "target_refs": [],
        "input_fingerprint": None,
        "input_revision": p.get("revision"),
    }


_FINANCE_REPORT_SCHEMA = "gg-finance-semantics-report/1"


def _finance_issue(code, *, status="fail", reason="", remedy=""):
    """Issue dict per design-r2 §5 on the ``finance_review`` check_id —
    the channel the Issue schema reserves for review freshness."""
    return {
        "code": code,
        "check_id": "finance_review",
        "status": status,
        "severity": "error",
        "fact_id": None,
        "consumer_id": None,
        "location": {
            "path": None, "section_id": None, "line": None,
            "column": None, "table": None, "row": None, "cell": None,
        },
        "reason": reason,
        "required_for": ["submission_candidate"],
        "remedy": remedy,
    }


def _review_targets_economic_fact(p, target_refs, registry):
    """True iff any ``facts``-collection target_ref resolves to a fact
    whose classify_fact result carries an economic marker — finance_role,
    meaning_id or measure present-but-possibly-malformed (is-not-None
    semantics, Lane-5b).  Shared by _finance_review_state (read path) and
    _apply_locked's economic-review binding check (write path) so the two
    scopes can never drift apart."""
    sem = _fact_semantics_mod()
    for ref in target_refs or []:
        if not isinstance(ref, dict) or ref.get("collection") != "facts":
            continue
        fact = p["facts"].get(ref.get("id"))
        if not isinstance(fact, dict):
            continue
        classified = sem.classify_fact(fact, registry=registry, bindings=[])
        if (classified.get("finance_role") is not None
                or classified.get("meaning_id") is not None
                or classified.get("measure") is not None):
            return True
    return False


def _fresh_semantics_issues(root, p, semantics, target_refs, *, context):
    """design-r2 §7 coverage conjuncts plus the current refs/fingerprint
    recompute — the shared "current semantics" basis used at read time
    by ``_finance_review_state`` and at write time by
    ``_validate_new_economic_review_binding`` (Lane 25), so both derive
    freshness identically.

    Compares the report's declared ``checked_item_ids``/``bindings``/
    ``projections``/``result`` against a fresh re-interpretation of
    CURRENT state (``classify_fact``/``resolve_consumers`` over
    ``target_refs`` — the stored "pass" string is never trusted).
    Returns ``(issues, current_refs, current_fp)``; the refs/fp pair is
    ``None`` when the recompute itself fails (an issue records it) so
    callers can still run their own equality conjuncts."""
    sem = _fact_semantics_mod()
    registry = context.get("registry")
    issues = []
    scoped_ids = sorted(
        ref["id"] for ref in target_refs or []
        if isinstance(ref, dict) and ref.get("collection") == "facts"
        and ref.get("id") in p["facts"])
    fresh_facts = [
        sem.classify_fact(p["facts"][fid], registry=registry, bindings=[])
        for fid in scoped_ids]
    consumers = sem.resolve_consumers(
        p, inputs=context.get("inputs") or [], registry=registry)
    fresh_issues = [
        i for fr in fresh_facts for i in fr.get("issues", [])
    ] + list(consumers.get("issues", []))
    fresh_result = (
        "blocked" if any(
            i.get("status") in ("fail", "blocked") for i in fresh_issues)
        else "pass")
    if sorted(semantics.get("checked_item_ids") or []) != sorted(
            fr.get("fact_id") for fr in fresh_facts if fr.get("fact_id")):
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="검사 항목(checked_item_ids) 변경"))
    if semantics.get("bindings") != consumers.get("bindings"):
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="소비 바인딩(bindings) 변경"))
    if semantics.get("projections") != consumers.get("projections"):
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="소비 projections 변경"))
    if semantics.get("result") != fresh_result:
        issues.append(_finance_issue(
            "REGISTRY_STALE",
            reason="의미검사 재실행 결과 불일치: report=%s fresh=%s"
                   % (semantics.get("result"), fresh_result)))
    try:
        current_refs = sort_target_refs(target_refs or [])
        current_fp = fingerprint(root, p, target_refs or [])
    except (KeyError, OSError, ValueError, TypeError, AttributeError) as e:
        current_refs = None
        current_fp = None
        issues.append(_finance_issue(
            "REGISTRY_STALE",
            reason="현재 refs의 fingerprint 재계산 불가: " + str(e)))
    return issues, current_refs, current_fp


def _validate_new_economic_review_binding(root, value, *, p, target_refs):
    """Write-time admission for a NEW economic-scoped record binding a
    finance-semantics report (design-r2 §7 apply/ingest/adopt rows):
    신규 경제 review 등록 전 report/P2 바인딩 확인 · 경제 report의 현재
    registry/refs/coverage 검증 · 실제 file hash·현재 semantics와 대조.

    Two stages: (1) fixed integrity — the bound file is readable, its
    declared hash matches its bytes, and it parses as
    gg-finance-semantics-report/1 with a semantics dict; (2) Lane 25
    current-state comparison — the report's declared registry
    version/sha256, target_refs, input_fingerprint and coverage
    (checked_item_ids/bindings/projections/result) must equal a fresh
    re-derivation over CURRENT ``p`` via the shared
    ``_fresh_semantics_issues`` (the identical block
    ``_finance_review_state`` runs at read time).  The review-identity
    and observation-revision conjuncts of ``validate_report_binding``
    are not applied here: the record being admitted is not yet in ``p``
    and adopt_output has no review/observation at all, so that triple
    would fire vacuously."""
    report_rel = value.get("path")
    if not report_rel or not value.get("report_hash"):
        raise ValueError("신규 경제 검토는 의미 보고서 바인딩 필요")
    try:
        report_bytes = local(root, report_rel).read_bytes()
    except (OSError, ValueError):
        report_bytes = None
    if report_bytes is None or digest(report_bytes) != value["report_hash"]:
        raise ValueError("의미 보고서 파일 해시 불일치")
    try:
        report = json.loads(report_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        report = None
    if (not isinstance(report, dict)
            or report.get("schema") != _FINANCE_REPORT_SCHEMA
            or not isinstance(report.get("semantics"), dict)):
        raise ValueError("의미 보고서 스키마 불일치")
    context = _semantic_context(root, p)
    registry = context.get("registry")
    semantics = report["semantics"]
    issues, current_refs, current_fp = _fresh_semantics_issues(
        root, p, semantics, target_refs, context=context)
    declared_reg = semantics.get("registry") or {}
    current_reg = registry or {}
    if (declared_reg.get("version") != current_reg.get("version")
            or declared_reg.get("sha256") != current_reg.get("sha256")):
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="registry bytes/version 변경"))
    if semantics.get("target_refs") != current_refs:
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="target_refs 변경"))
    if semantics.get("input_fingerprint") != current_fp:
        issues.append(_finance_issue(
            "REGISTRY_STALE", reason="input_fingerprint 변경"))
    if issues:
        raise ValueError(
            "새 경제 승인은 현재 registry/refs/coverage와 불일치: "
            + "; ".join(i["reason"] for i in issues))


def _finance_review_state(root, p, review, *, context):
    """Economic freshness of one review per design-r2 §7's AND-list.

    Returns ``(state, issues)`` with state one of:

    - ``not_applicable`` — review_kind is none of "content"/"logic"/
      "calculation" (design-r2 §7's gated kinds), or none of
      the review's ``target_refs`` resolves to an economic-scoped fact
      (``classify_fact`` yields a finance_role, meaning_id or measure).
    - ``unresolved`` — no finance-semantics report is bound (the review
      record lacks ``path``/``report_hash``).
    - ``invalid`` — a bound report fails a fixed integrity check (file
      unreadable, byte hash mismatched, malformed JSON, wrong schema) or
      ``_p2_review_observation_state`` returns "invalid".  One-way call:
      _finance_review_state -> _p2_review_observation_state (the P2-only
      base, NOT the public composing wrapper), never back, so no call
      cycle exists.
    - ``stale`` — structurally valid but any freshness conjunct fails:
      registry version drift, coverage/bindings/projections/result that
      disagree with a fresh re-interpretation of the CURRENT project
      state (the stored "pass" string is never trusted), fingerprint
      drift, review status/disposition/findings not passing, or any of
      the report↔review↔observation exact-equality fields differing.
    - ``fresh`` — all conjuncts hold.
    """
    sem = _fact_semantics_mod()
    if (not isinstance(review, dict)
            or review.get("review_kind") not in (
                "content", "logic", "calculation")):
        return "not_applicable", []
    context = context or {}
    registry = context.get("registry")
    if not _review_targets_economic_fact(
            p, review.get("target_refs"), registry):
        return "not_applicable", []

    report_rel = review.get("path")
    declared_hash = review.get("report_hash")
    if not report_rel or not declared_hash:
        return "unresolved", [_finance_issue(
            "SEMANTIC_REPORT_REQUIRED", status="blocked",
            reason="경제 검토에 바인딩된 의미 보고서가 없음",
            remedy="gg-finance-semantics-report/1 보고서를 review.path에 바인딩")]

    # Fixed integrity checks: any failure is 'invalid' outright.
    issues = []
    report_bytes = None
    report = None
    try:
        report_bytes = local(root, report_rel).read_bytes()
    except (OSError, ValueError) as error:
        issues.append(_finance_issue(
            "SEMANTIC_REPORT_INVALID",
            reason="보고서 파일을 읽을 수 없음: " + str(error)))
    if report_bytes is not None and digest(report_bytes) != declared_hash:
        issues.append(_finance_issue(
            "SEMANTIC_REPORT_INVALID", reason="보고서 파일 해시 불일치"))
        report_bytes = None
    if report_bytes is not None:
        try:
            report = json.loads(report_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            issues.append(_finance_issue(
                "SEMANTIC_REPORT_INVALID",
                reason="보고서 JSON 손상: " + str(error)))
        else:
            if (not isinstance(report, dict)
                    or report.get("schema") != _FINANCE_REPORT_SCHEMA
                    or not isinstance(report.get("semantics"), dict)):
                issues.append(_finance_issue(
                    "SEMANTIC_REPORT_INVALID",
                    reason=_FINANCE_REPORT_SCHEMA + " 스키마 아님"))
                report = None
    obs_state, obs_reason = _p2_review_observation_state(root, review)
    if obs_state == "invalid":
        issues.append(_finance_issue(
            "OBSERVATION_BINDING_INVALID",
            reason="P2 관측 무효: " + obs_reason))
    if issues:
        return "invalid", issues

    # Freshness conjuncts: report coverage/consumption/result must equal a
    # fresh re-interpretation of current state (§7: stored "pass" is not
    # trusted — the semantic check actually re-runs).  Lane 25: the
    # re-derivation lives in _fresh_semantics_issues so the write-time
    # admission helper derives "current" identically.
    semantics = report["semantics"]
    coverage_issues, current_refs, current_fp = _fresh_semantics_issues(
        root, p, semantics, review.get("target_refs"), context=context)
    issues.extend(coverage_issues)

    # The review's own pass predicate — same conditions gate() inlines
    # (status/disposition/findings/coverage/stale flag), re-read here per
    # the packet's leave-gate-unchanged guidance.
    findings = review.get("findings")
    if not (
        review.get("status") == "pass"
        and not review.get("stale")
        and review.get("author_id") != review.get("reviewer_id")
        and review.get("disposition") == "resolved"
        and review.get("target_refs")
        and review.get("coverage")
        and isinstance(findings, list)
        and all(isinstance(x, dict) for x in findings)
        and not any(
            x.get("severity") == "error" and x.get("status") != "resolved"
            for x in findings)
    ):
        issues.append(_finance_issue(
            "REGISTRY_STALE",
            reason="review의 status/disposition/findings가 통과 조건 미충족"))

    # Bound observation's fields for the equality checks inside
    # validate_report_binding; provenance integrity was already decided
    # above, so anything unreadable here is treated as absent.
    observation = {}
    if review.get("provenance_path") and review.get("provenance_hash"):
        try:
            raw = local(root, review["provenance_path"]).read_bytes()
            if digest(raw) == review["provenance_hash"]:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    observation = parsed
        except (OSError, ValueError, TypeError):
            pass

    issues.extend(sem.validate_report_binding(
        report,
        registry=registry,
        current={
            "registry": registry or {},
            "target_refs": current_refs,
            "input_fingerprint": current_fp,
            "report_hash": declared_hash,
            "actual_report_hash": digest(report_bytes),
            "review_path": report_rel,
            "review_input_revision": review.get("input_revision"),
            "review_status": review.get("status"),
            "review_author_id": review.get("author_id"),
            "review_reviewer_id": review.get("reviewer_id"),
            "review_kinds": [review.get("review_kind")],
            "observation_report_path": observation.get("report_path"),
            "observation_input_revision": observation.get("input_revision"),
        }))
    return ("stale", issues) if issues else ("fresh", [])


def finance_review_freshness(root, p, review_id):
    """Additive read path (Lane 5): one review's economic freshness via
    ``_finance_review_state`` over a fresh semantic Context.  Not wired
    into checks()/gate() — those §7 call points are later lanes."""
    review = p["reviews"][review_id]
    return _finance_review_state(
        root, p, review, context=_semantic_context(root, p))


def _economic_output_fresh(root, p, output):
    """Lane 24 (architect finding C7-FINAL-4): the read-time counterpart
    of Lane 19's adopt-time ``output_file_hash`` binding.

    A NON-economic-scoped output returns True immediately — the
    predicate is not applicable to it, so the freshness requirement is
    per-output and never widened globally.  An economic-scoped output
    (its target_refs resolve to at least one fact with
    finance_role/meaning_id/measure set, per
    ``_review_targets_economic_fact``) is fresh only while at least one
    content/logic/calculation-kind review over EXACTLY the same
    target_refs currently evaluates "fresh" under
    ``_finance_review_state`` — i.e. a byte-matched, schema-valid
    report whose registry/target_refs/fingerprint/identity conjuncts
    still hold against CURRENT state.

    Output ``checks[]`` entries are static caller metadata (check_id/
    status/file_hash/evidence_path/evidence_hash) and cannot express a
    live re-derivation; recomputing binding semantics for them would
    duplicate ``validate_report_binding``'s review-oriented conjuncts.
    Composing the already-tested review freshness function keeps ONE
    freshness mechanism for reviews, gate() eligibility and lineage
    terminal verification.

    Lane 26 (C7-FINAL-4-REOPEN): the qualifying fresh review must be
    IDENTIFIABLY bound to this output's own adopted evidence — not
    merely any review sharing the same target_refs (an unrelated fresh
    review must never substitute for a stale bound report).  Identity:
    the review's (path, report_hash) equals some checks[] entry's
    (evidence_path, evidence_hash), and that report still declares
    ``semantics.output_file_hash == output["file_hash"]`` — Lane 19's
    adopt-time binding re-verified at read time."""
    ctx = _semantic_context(root, p)
    refs = output.get("target_refs") or []
    if not _review_targets_economic_fact(p, refs, ctx["registry"]):
        return True
    want = sort_target_refs(refs)
    bound_evidence = {
        (entry.get("evidence_path"), entry.get("evidence_hash"))
        for entry in output.get("checks") or []
        if isinstance(entry, dict)
    }
    for review in p["reviews"].values():
        try:
            if review.get("review_kind") not in (
                    "content", "logic", "calculation"):
                continue
            if sort_target_refs(review.get("target_refs") or []) != want:
                continue
            if (review.get("path"), review.get("report_hash")
                    ) not in bound_evidence:
                continue
            state, _issues = _finance_review_state(
                root, p, review, context=ctx)
            if state != "fresh":
                continue
            bound_report = json.loads(
                local(root, review["path"]).read_bytes())
            if ((bound_report.get("semantics") or {}).get(
                    "output_file_hash") != output.get("file_hash")):
                continue
            return True
        except (KeyError, OSError, ValueError, TypeError, AttributeError):
            continue
    return False


def _finance_check_items(p):
    """Select provided monetary facts for the body crosscheck.

    ``sections[].claims`` selects the checked facts only when every section
    carries an explicit claims list; a mixed or unregistered project falls
    back to checking all facts so nothing is dropped silently.  An
    all-empty claims set cannot switch the check off — it blocks instead.
    ``sources[].claims`` are source claims, not body claims, and are never
    consulted here.  Returns (items, blocked_reason).

    H9/R6-undercheck fix (gate=P3, fixed via gg_finance_body's
    ``_MONETARY_UNITS``): the eligible-unit set previously matched only
    '천원' and silently returned ([], None) for a provided '원' monetary
    fact — dropped from the check set and never reported. Both currency
    units gg_finance_body.normalize_monetary supports ('원','천원') are now
    eligible; a fact whose unit is neither is still out of scope (not a
    monetary fact at all, e.g. kg/㎡/ratio), which stays correct per
    R6-overblock's positive control (non-financial facts must never enter
    this check). Signature and legacy '천원'-only behavior for values
    already passing the old predicate are preserved unchanged.
    """
    def eligible(fid):
        f = p["facts"][fid]
        return (
            f.get("answer_state") == "provided"
            and f.get("unit") in _finance_body_mod()._MONETARY_UNITS
            and f.get("value") is not None
        )

    def item(fid):
        f = p["facts"][fid]
        return {
            "fact_id": fid,
            "field_id": f["field_id"],
            "value": f["value"],
            "unit": f.get("unit"),
            "period": f.get("period"),
            "scope": f.get("scope"),
        }

    sections = p.get("sections", {})
    complete = bool(sections) and all(
        isinstance(s.get("claims"), list) for s in sections.values()
    )
    eligible_ids = [fid for fid in p["facts"] if eligible(fid)]
    if not complete:
        return [item(fid) for fid in eligible_ids], None
    claimed = list(
        dict.fromkeys(
            c["fact_id"]
            for s in sections.values()
            for c in s["claims"]
            if isinstance(c, dict) and c.get("fact_id") in p["facts"]
        )
    )
    if not claimed:
        if eligible_ids:
            return [], "모든 절의 본문 주장(claims)이 빈 목록: 재무 본문 대조 불가"
        return [], None
    return [item(fid) for fid in claimed if eligible(fid)], None


def _body_check_items(p, *, registry):
    """Required BodyItem set for ``gg_finance_body.crosscheck_sections``.

    Lane 21 (architect finding C7-FINAL-1): the real §6 entry point needs
    BodyItem dicts (item_id/field_id/fact_id/measure.unit/value/period/
    scope/required/labels), not the FactResult shape ``build_check_set``
    returns.  The required set mirrors ``build_check_set``'s rule exactly:
    facts that classify ``resolved`` with ``finance_role == "plan"``.
    Facts resolving to non-plan/unresolved/invalid never become required
    items, so legacy unmetadated projects keep their previous coverage.
    """
    sem = _fact_semantics_mod()
    items = []
    for fid, f in p["facts"].items():
        if not isinstance(f, dict):
            continue
        fr = sem.classify_fact(f, registry=registry, bindings=[])
        if (fr.get("status") != "resolved"
                or fr.get("finance_role") != "plan"):
            continue
        measure = f.get("measure")
        items.append({
            "item_id": fid,
            "fact_id": fid,
            "field_id": f.get("field_id"),
            "value": f.get("value"),
            "measure": {"unit": (
                measure.get("unit") if isinstance(measure, dict)
                else None) or f.get("unit")},
            "period": f.get("period"),
            "scope": f.get("scope"),
            "required": True,
            "labels": [f["field_id"]] if f.get("field_id") else [],
        })
    return items


def checks(root, p):
    from gg_document import parse

    out = []

    def add(cid, target, reason, severity="error", status="fail"):
        out.append(result(cid, target, status, reason, p["revision"], severity))

    for sid, s in p["sources"].items():
        try:
            if digest(local(root, s["path"]).read_bytes()) != s["hash"]:
                add("source_hash", sid, "원자료 변경: 재검토 필요")
        except (OSError, ValueError):
            add("source_missing", sid, "원자료 읽기 실패", status="blocked")
    for fid, f in p["facts"].items():
        for ref in f["source_refs"]:
            s = p["sources"].get(ref["id"])
            if s is None:
                add(
                    "source_ref_missing",
                    fid,
                    "원자료 참조 기록 없음: " + str(ref.get("id")),
                    status="blocked",
                )
                continue
            if ref.get("revision") != s["revision"]:
                add("source_revision", fid, "근거 개정 불일치")
            if ref.get("audit_key") is not None:
                # P4 hook (D07/D09): audited statistical refs resolve only
                # through the provenance chain — fabricated or unanchored
                # receipts fail closed, and the fact's own claim is
                # cross-checked against the bound receipt.
                verdict = _statistical_ref_verdict(s, ref, f)
                if verdict["status"] == "invalid":
                    add("statistical_ref_invalid", fid,
                        verdict["reason"], status="blocked")
                elif verdict["status"] == "unresolved":
                    add("statistical_ref_unresolved", fid,
                        verdict["reason"], status="blocked")
                elif (
                    f["verification"] == "claim_supported"
                    and (
                        verdict.get("catalog_status")
                        != "verified_observation"
                        or verdict.get("catalog_accepted") is not True
                        or verdict.get("receipt_verified") is not True
                    )
                ):
                    add("statistical_ref_unverified", fid,
                        "미수용 카탈로그·영수증 없는 관측 또는 격리·탐색 "
                        "관측으로 주장 검증 불가 (D05)",
                        status="blocked")
            claim = s.get("claims", {}).get(ref.get("claim_id"))
            if f["kind"] != "derived" and f["answer_state"] == "provided":
                if claim is None:
                    add(
                        "claim_missing",
                        fid,
                        "원문 대조값 없음: 의미 검토 필요",
                        status="blocked",
                    )
                elif any(
                    f[k] != claim.get(k)
                    for k in (
                        "field_id",
                        "value",
                        "unit",
                        "period",
                        "scope",
                        "answer_state",
                        "kind",
                    )
                ):
                    add(
                        "claim_mismatch",
                        fid,
                        "항목·값·단위·기간·범위·사실 종류 원문 불일치",
                    )
            if f["verification"] == "claim_supported" and not s.get("claim_review"):
                add(
                    "claim_review",
                    fid,
                    "자료 실재만으로 주장 검증 불가",
                    status="blocked",
                )
        if f["kind"] == "derived":
            try:
                refs = f["input_refs"]
                vals = []
                for ref in refs:
                    v = p["facts"][ref["id"]]
                    if ref["revision"] != v["revision"]:
                        raise ValueError("계산 입력 개정 불일치")
                    vals.append(Decimal(v["value"]))
                op = f["formula"]["op"]
                if op == "convert":
                    factors = {
                        ("kg", "g"): "1000",
                        ("g", "kg"): ".001",
                        ("천원", "원"): "1000",
                        ("10a", "㎡"): "1000",
                        ("%", "ratio"): ".01",
                    }
                    src = p["facts"][refs[0]["id"]]
                    if src["period"] != f["period"] or src["scope"] != f["scope"]:
                        raise ValueError("환산으로 기간/범위를 바꿀 수 없음")
                    value = vals[0] * Decimal(factors[(src["unit"], f["unit"])])
                elif op in {"multiply", "divide", "add", "subtract"}:
                    if len(vals) != 2 or not f["formula"].get("unit_reason"):
                        raise ValueError("이항 산식/단위 근거 필요")
                    value = {
                        "multiply": lambda: vals[0] * vals[1],
                        "divide": lambda: vals[0] / vals[1],
                        "add": lambda: vals[0] + vals[1],
                        "subtract": lambda: vals[0] - vals[1],
                    }[op]()
                else:
                    raise ValueError("미지원 산식")
                if value != Decimal(f["value"]):
                    raise ValueError("계산 결과 불일치")
            except (
                KeyError,
                ValueError,
                InvalidOperation,
                ArithmeticError,
                TypeError,
            ) as e:
                add("calculation", fid, str(e))
    # Lane 22: one shared _semantic_context call (a file read) serves
    # every P3 consumer in this pass — the fact loop below, the
    # finance_review loop, and the body crosscheck's required items.
    _finance_ctx = _semantic_context(root, p)
    # P3 wiring (design-r2 §7): every fact is classified through the
    # S-lane semantics module; each returned issue surfaces under
    # check_id="fact_semantics".  The registry now flows from the shared
    # _semantic_context (the installed fact-semantics.json when present),
    # matching the finance_review loop.  bindings stays []: no
    # InputSnapshot files are deployed, so resolve_consumers-derived
    # per-fact bindings genuinely do not exist yet.
    for fid, f in p["facts"].items():
        for issue in _fact_semantics_mod().classify_fact(
            f, registry=_finance_ctx["registry"], bindings=[]
        )["issues"]:
            add(
                "fact_semantics",
                fid,
                issue["reason"],
                status=issue["status"],
            )
    # P3 wiring (design-r2 §7): every content/logic/calculation-kind
    # review's economic freshness surfaces under
    # check_id="finance_review" — the same three-kind set
    # _finance_review_state and the write-time admission guards use
    # (Lanes 18/27).  Reviews with
    # no economic-scoped target facts return not_applicable ([]) and add
    # nothing.  context is a single _semantic_context(root, p) call
    # shared across all reviews checked in this pass.
    for rid, r in p["reviews"].items():
        state, issues = _finance_review_state(
            root, p, r, context=_finance_ctx)
        for issue in issues:
            add(
                "finance_review",
                rid,
                issue["reason"],
                status=issue["status"],
            )
    for sid, s in p["sections"].items():
        try:
            raw = local(root, s["path"]).read_text(encoding="utf-8")
            text = draft(raw)
            extra = draft_marker_extra(raw)
            if extra:
                add(
                    "draft_marker_extra",
                    sid,
                    "DRAFT 마커 밖 원문이 병합·검사에 보이지 않음(원본 보존·대조 필요): "
                    + extra[:60],
                )
            if digest(text) != s["draft_hash"]:
                add("draft_changed", sid, "본문 수동 변경: 재등록·재검토 필요")
            if not any(n["kind"] in {"paragraph", "table"} for n in parse(text)):
                add(
                    "content_incomplete",
                    sid,
                    "본문 미작성 또는 제목·객체만 존재: 내용검토와 필수항목 대조 필요",
                )
            for c in s.get("claims", []):
                f = p["facts"][c["fact_id"]]
                if c.get("quote") not in text or not c.get("quote"):
                    add("claim_location", sid, "주장 본문 위치 없음")
                if f["answer_state"] != "provided" or any(
                    c.get(k) != f[k]
                    for k in ("value", "unit", "period", "scope", "kind", "field_id")
                ):
                    add("draft_claim", sid, "본문 주장의 사실·상태·기간 불일치")
            for dep in s.get("depends_on", []):
                if node(p, dep)["revision"] != dep["revision"]:
                    add("dependency_revision", sid, "절 의존 개정 불일치")
        except (OSError, KeyError, ValueError) as e:
            add("section", sid, str(e), status="blocked")
    for rid, rule in p["rules"].items():
        guideline_ids = set()
        profile_rule = p["rules"].get("guideline_profile")
        if isinstance(profile_rule, dict) and isinstance(profile_rule.get("requirement_ids"), list):
            guideline_ids = {x for x in profile_rule["requirement_ids"] if isinstance(x, str)}
        if rid == "guideline_profile" or rid in guideline_ids:
            continue
        refs = rule.get("source_refs", [])
        if not refs or any(
            r.get("id") not in p["sources"]
            or not r.get("locator")
            or r.get("revision") != p["sources"][r["id"]]["revision"]
            for r in refs
        ):
            add(
                "rule_source",
                rid,
                "학교 규칙 원문·위치·개정 근거 필요",
                status="blocked",
            )
        if rid == "output_formats":
            continue
        if rule.get("applicable") is False:
            condition = rule.get("applies_when", {})
            fact = p["facts"].get(condition.get("fact_id"), {})
            if (
                not rule.get("na_reason")
                or not rule.get("depends_on")
                or condition.get("operator") != "equals"
                or condition.get("revision") != fact.get("revision")
                or fact.get("answer_state") != "provided"
                or "value" not in condition
                or fact.get("value") == condition["value"]
            ):
                add("rule_na", rid, "해당 없음 적용 조건·사실 개정·평가 근거 필요")
        elif rule.get("applicable") is not True or not rule.get("locations"):
            add(
                "rule_coverage",
                rid,
                "학교 요구의 적용 여부/본문 위치 미확인",
                status="blocked",
            )
        else:
            for location in rule["locations"]:
                try:
                    body = draft(
                        local(
                            root, p["sections"][location["section_id"]]["path"]
                        ).read_text(encoding="utf-8")
                    )
                    if not location.get("quote") or location["quote"] not in body:
                        raise ValueError("적용 근거 본문 인용 없음")
                except (OSError, KeyError, ValueError, TypeError) as e:
                    add("rule_location", rid, str(e), status="blocked")
    # Guideline coverage is a separate source-bound runtime check.  Generic
    # projects remain usable without a school profile; selecting an official
    # school source without one is fail-closed.
    try:
        from gg_guidelines import check as guideline_check

        guideline_report = guideline_check(root, p)
        gstatus = guideline_report.get("status")
        if gstatus == "not_configured":
            out.append(
                result(
                    "guideline_profile",
                    "project",
                    "not_configured",
                    guideline_report.get("reason") or "학교 지침 프로필 미설정(일반 프로젝트)",
                    p["revision"],
                    severity="warning",
                    required_for=[],
                )
            )
        else:
            ready = guideline_report.get("guideline_ready") is True
            out.append(
                result(
                    "guideline_profile",
                    "project",
                    "pass" if ready else "blocked",
                    "지침 항목별 근거·검토 결합" if ready else (guideline_report.get("reason") or "지침 항목별 검증 근거 누락"),
                    p["revision"],
                    severity="warning" if ready else "error",
                    evidence=[{"status": gstatus, "blockers": guideline_report.get("blockers", []), "inventory": guideline_report.get("inventory", {})}],
                    required_for=[] if ready else ["submission_candidate"],
                )
            )
            for item in guideline_report.get("items", []):
                if item.get("status") == "blocked":
                    out.append(
                        result(
                            "guideline_item:" + str(item.get("id")),
                            "guidelines",
                            "blocked",
                            item.get("reason") or "지침 항목 검증 근거 누락",
                            p["revision"],
                            severity="error",
                        )
                    )
                elif item.get("status") == "pending" and item.get("kind") == "user_finish":
                    out.append(
                        result(
                            "guideline_item:" + str(item.get("id")),
                            "user_finish",
                            "pending",
                            item.get("reason") or "사용자 지침 마무리 확인 필요",
                            p["revision"],
                            severity="warning",
                            owner="user",
                            required_for=["user_finish"],
                        )
                    )
    except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
        out.append(result("guideline_runtime", "project", "blocked", str(e), p["revision"]))
    if not p["rules"]:
        add("school_profile", "project", "학교 규칙 미등록", status="blocked")
    try:
        import gg_document

        # CONTENT_REVIEW_HINTS is owned by Devin B (gg_document.py); core only
        # reads it and only these three lexical-hint ids may become warnings.
        hint_ids = getattr(gg_document, "CONTENT_REVIEW_HINTS", ())
        if isinstance(hint_ids, (set, frozenset, list, tuple)):
            hint_ids = set(hint_ids) & {
                "intro_topics",
                "closing_elements",
                "process_plan",
            }
        else:
            hint_ids = set()
        for cid, reason in gg_document.check(merged(root, p), root):
            add(cid, "body", reason,
                severity="warning" if cid in hint_ids else "error")
        lineage = output_lineage(p)
        for oid, verdict in lineage.items():
            if verdict["state"] == "invalid":
                add(
                    "output_superseded_by",
                    oid,
                    verdict["reason"],
                    status="blocked",
                )
        # Every canonical output must trace to a verifiable immutable
        # publication — records without a checked publication_ref fail
        # closed instead of passing on filename/bytes alone (SPEC §5).
        # The receipt must also be an adopt_output publication whose
        # declared refs/fingerprint/primary bytes match the canonical
        # record — a foreign receipt is not proof of this output.
        for oid, output in p["outputs"].items():
            ref = output.get("publication_ref")
            if not ref:
                add(
                    "output_publication",
                    oid,
                    "발행 참조 없음: adopt_output 경유 등록만 허용",
                    status="blocked",
                )
                continue
            try:
                receipt = _publication_mod().verify_publication(
                    root,
                    publication_ref=ref,
                    expected={"kind": "adopt_output"},
                )
            except Exception as e:
                add(
                    "output_publication",
                    oid,
                    "발행 검증 실패: " + str(e),
                    status="blocked",
                )
                continue
            mismatched = []
            if receipt.get("target_refs") != output.get("target_refs"):
                mismatched.append("target_refs")
            if receipt.get("input_fingerprint") != output.get(
                "input_fingerprint"
            ):
                mismatched.append("input_fingerprint")
            primary = receipt.get("primary_path")
            primary_sha = next(
                (
                    f["sha256"]
                    for f in receipt.get("files", [])
                    if isinstance(f, dict) and f.get("path") == primary
                ),
                None,
            )
            if (
                primary_sha is not None
                and output.get("file_hash") is not None
                and primary_sha != output["file_hash"]
            ):
                mismatched.append("file_hash")
            if mismatched:
                add(
                    "output_publication",
                    oid,
                    "receipt와 정본 불일치: " + ", ".join(mismatched),
                    status="blocked",
                )
        from gg_school_excel import inspect_outputs

        for cid, reason in inspect_outputs(root, p, lineage):
            add(cid, "xlsx", reason)
        body = merged(root, p)
        items, claims_block = _finance_check_items(p)
        if claims_block:
            add("body_finance_crosscheck", "body", claims_block, status="blocked")
        elif items:
            from gg_school_excel import crosscheck_body

            for item in crosscheck_body(body, items):
                add(
                    "body_finance_crosscheck",
                    item.get("fact_id") or item["location"],
                    item["reason"],
                )
        # Lane 21: the real §6 entry point, additive under its own
        # check_id and gated on crosscheck_sections's own complete_mode
        # litmus (every section carrying a claims list).  In that regime
        # the legacy call above keeps its claimed-subset text scan while
        # this path reports claim-shape and required-set coverage — the
        # two never emit the same issue.  Non-complete projects stay on
        # the legacy path only: crosscheck_sections's fallback branch
        # would re-run the same crosscheck_body text scan and double-
        # report under a second check_id.
        if p["sections"] and all(
                isinstance(s.get("claims"), list)
                for s in p["sections"].values()):
            sections_v2 = [
                {
                    "section_id": sid,
                    "path": s.get("path"),
                    "text": draft(
                        local(root, s["path"]).read_text(
                            encoding="utf-8")),
                    "claims": s.get("claims"),
                }
                for sid, s in p["sections"].items()
            ]
            body_result = _finance_body_mod().crosscheck_sections(
                sections_v2,
                _body_check_items(
                    p, registry=_finance_ctx["registry"]),
                registry=_finance_ctx["registry"])
            for issue in body_result["issues"]:
                add(
                    "body_finance_crosscheck_v2",
                    (issue.get("fact_id") or issue.get("consumer_id")
                     or issue.get("location")),
                    issue["reason"],
                    severity=issue.get("severity", "error"),
                    status=issue.get("status", "fail"),
                )
    except (OSError, ValueError, KeyError) as e:
        add("document_parse", "body", str(e), status="blocked")
    for name, v in p.get("views", {}).items():
        try:
            if digest(local(root, name).read_bytes()) != v["hash"]:
                add(
                    "view_edited",
                    name,
                    "생성 보기 수동 변경: 원본을 보존하고 대조해야 함",
                )
        except ValueError as e:
            add("view_invalid", name, str(e), status="blocked")
        except OSError:
            add("view_missing", name, "보기 생성 실패/누락", status="blocked")
    return out or [
        result(
            "structure",
            "project",
            "pass",
            "등록 데이터 일관성만 확인; 내용/출력 검토 별도",
            p["revision"],
        )
    ]


_OBSERVATION_CALLER_KEYS = {
    "author_session",
    "reviewer_session",
    "author_id",
    "reviewer_id",
    "review_kinds",
    "target_refs",
    "input_fingerprint",
    "report_path",
    "report_hash",
    "input_revision",
}

_OBSERVATION_SERVER_KEYS = {
    "schema",
    "observed_by",
    "workspace_id",
    "protocol_sha256",
}


def ingest_review_observation(root, observation, observer):
    """Record an adapter observation through the guard and publication
    boundary (API §8, SPEC §8).  Not an OS-user authentication boundary.

    Schema-2 records add report_path, input_revision, workspace_id and
    protocol_sha256 binding; unknown caller keys and server-provided fields
    are rejected, never silently normalized.
    """
    root = Path(root)
    if not isinstance(observer, str) or not observer.strip():
        raise _op("observe", "observation", "not_committed",
                  "observer_required", detail="관측 어댑터 ID 필요")
    if not isinstance(observation, dict):
        raise _op("observe", "observation", "not_committed",
                  "observation_not_object", detail="관측 기록은 객체여야 함")
    unknown = set(observation) - _OBSERVATION_CALLER_KEYS
    if unknown:
        raise _op("observe", "observation", "not_committed",
                  "observation_unknown_keys", detail=sorted(unknown))
    missing = [k for k in _OBSERVATION_CALLER_KEYS
               if observation.get(k) in (None, "", [], {})]
    if missing:
        raise _op("observe", "observation", "not_committed",
                  "observation_missing_fields", detail=sorted(missing))
    recorded = copy.deepcopy(observation)
    recorded["schema"] = "gg-review-observation/2"
    recorded["observed_by"] = observer.strip()
    for key in ("author_session", "reviewer_session", "author_id",
                "reviewer_id"):
        if not isinstance(recorded[key], str) or not recorded[key].strip():
            raise _op("observe", "observation", "not_committed",
                      "observation_identity_invalid", detail=key)
    if recorded["author_session"] == recorded["reviewer_session"]:
        raise _op("observe", "observation", "not_committed",
                  "self_session_observation",
                  detail="자기 세션 관측은 기록할 수 없음")
    if recorded["observed_by"] in {
        recorded["author_session"], recorded["reviewer_session"],
    }:
        raise _op("observe", "observation", "not_committed",
                  "observer_is_party",
                  detail="관측자가 당사자 세션이면 기록할 수 없음")
    if recorded["author_id"] == recorded["reviewer_id"]:
        raise _op("observe", "observation", "not_committed",
                  "self_review_observation",
                  detail="자가검토 관측은 기록할 수 없음")
    kinds = recorded["review_kinds"]
    if (
        not isinstance(kinds, list)
        or not kinds
        or any(not isinstance(k, str) or not k.strip() for k in kinds)
    ):
        raise _op("observe", "observation", "not_committed",
                  "invalid_review_kinds",
                  detail="관측 검토 종류는 비어있지 않은 문자열 목록이어야 함")
    # Canonical UTF-8 order; caller duplicates are never silently removed —
    # gg_publication rejects them loudly instead.
    recorded["review_kinds"] = sorted(
        kinds, key=lambda k: k.encode("utf-8")
    )
    try:
        recorded["target_refs"] = sort_target_refs(recorded["target_refs"])
    except ValueError as error:
        raise _op("observe", "observation", "not_committed",
                  "invalid_target_refs", detail=str(error)) from error
    if not _check_sha256(recorded["input_fingerprint"]):
        raise _op("observe", "observation", "not_committed",
                  "invalid_input_fingerprint")
    if not _check_sha256(recorded["report_hash"]):
        raise _op("observe", "observation", "not_committed",
                  "invalid_report_hash")
    if (
        type(recorded["input_revision"]) is not int
        or recorded["input_revision"] < 0
    ):
        raise _op("observe", "observation", "not_committed",
                  "invalid_input_revision")
    if not isinstance(recorded["report_path"], str) \
            or not recorded["report_path"].strip():
        raise _op("observe", "observation", "not_committed",
                  "invalid_report_path")

    def body(capability):
        _lock_mod().assert_held(capability, root)
        try:
            report = local(root, recorded["report_path"])
        except ValueError as error:
            raise _op("observe", "observation", "not_committed",
                      "invalid_report_path", detail=str(error)) from error
        if not report.is_file():
            raise _op("observe", "observation", "not_committed",
                      "report_missing")
        report_stat = report.stat()
        report_identity = (report_stat.st_dev, report_stat.st_ino)
        report_bytes = report.read_bytes()
        if digest(report_bytes) != recorded["report_hash"]:
            raise _op("observe", "observation", "not_committed",
                      "report_hash_mismatch")
        p = load(root)
        if recorded["input_revision"] != p["revision"]:
            raise _op("observe", "observation", "not_committed",
                      "observation_stale_input",
                      detail="input_revision이 현재 정본 개정과 다름")
        if recorded["input_fingerprint"] != fingerprint(
            root, p, recorded["target_refs"]
        ):
            raise _op("observe", "observation", "not_committed",
                      "input_fingerprint_mismatch")
        # P3 wiring (design-r2 §7 'ingest_review_observation': 기존
        # revision/hash 검사 후, 경제 report의 현재 registry/refs 검증,
        # 그 뒤 기존 publication 발행): an observation declaring any
        # content/logic/calculation-kind review over an economic-scoped
        # fact must bind
        # a gg-finance-semantics-report/1 report file whose declared
        # registry/refs/coverage still matches CURRENT state — the same
        # re-derivation _finance_review_state runs at read time (Lane 25,
        # mirroring Lane-7's apply boundary).  The observation record
        # speaks report_path while the helper expects a review value's
        # path — a thin adapter maps it rather than renaming either side.
        if (
            any(
                kind in ("content", "logic", "calculation")
                for kind in recorded["review_kinds"]
            )
            and _review_targets_economic_fact(
                p, recorded["target_refs"],
                _semantic_context(root, p).get("registry"))
        ):
            try:
                _validate_new_economic_review_binding(
                    root,
                    {"path": recorded["report_path"],
                     "report_hash": recorded["report_hash"]},
                    p=p, target_refs=recorded["target_refs"])
            except ValueError as error:
                raise _op(
                    "observe", "observation", "not_committed",
                    "economic_report_schema_invalid",
                    detail=str(error)) from error
        # The held capability is the authoritative workspace/protocol
        # binding — assert_held already re-verified both against disk.
        recorded["workspace_id"] = capability.workspace_id
        recorded["protocol_sha256"] = capability.protocol_sha256
        publication = _publication_mod()
        try:
            result = publication.publish_observation(
                root, capability, recorded=recorded
            )
        except Exception as error:
            raise _publication_failure(
                error, "observe", "observation"
            ) from error
        # The report file must be the same bytes at the same identity after
        # the write — a swapped report would falsify the binding.
        after = report.stat()
        if (after.st_dev, after.st_ino) != report_identity or digest(
            report.read_bytes()
        ) != recorded["report_hash"]:
            raise _op(
                "observe", "observation", "committed_cleanup_pending",
                "report_changed_during_write",
                receipt_path=result.get("path"),
            )
        return {"path": result["path"], "hash": result["hash"]}

    return _guarded(
        root, "observe", "observation", body,
        lambda: ("indeterminate", {}),
    )


def _observation_workspace_trusted(root, observation):
    """True if ``observation``'s workspace_id/protocol_sha256 match the
    control root's CURRENT protocol, or a B-verified trusted
    same_workspace_repair recovery lineage connects the observation's
    endpoint to the current one (API §8/§9). Never trusts caller-declared
    endpoints without checking them against the actual current control
    object; a copied/foreign workspace with a byte-identical receipt does
    not pass merely by carrying plausible-looking fields."""
    protocol_path = local(root, ".gg-lock/protocol.json")
    try:
        protocol_bytes = protocol_path.read_bytes()
        protocol = json.loads(protocol_bytes)
    except (OSError, ValueError):
        protocol_bytes, protocol = None, {}
    current_workspace_id = (
        protocol.get("workspace_id") if isinstance(protocol, dict) else None
    )
    current_protocol_sha256 = digest(protocol_bytes or b"")
    if (
        isinstance(protocol, dict)
        and current_workspace_id == observation.get("workspace_id")
        and current_protocol_sha256 == observation.get("protocol_sha256")
    ):
        return True
    try:
        lineage = _lock_mod().verify_recovery_lineage(
            root,
            previous_workspace_id=observation.get("workspace_id"),
            previous_protocol_sha256=observation.get("protocol_sha256"),
            current_workspace_id=current_workspace_id,
            current_protocol_sha256=current_protocol_sha256,
        )
    except Exception:
        return False
    return bool(lineage.get("trusted"))


def _review_observation_state_v2(root, review, observation, data):
    """Schema-2 provenance chain: observation bytes/name, workspace
    binding, report identity, and full review-field equality (SPEC §8)."""
    if digest(data) != review["provenance_hash"]:
        return "invalid", "관측 기록 해시 불일치"
    name = Path(review["provenance_path"]).name
    if name != review["provenance_hash"] + ".json":
        return "invalid", "관측 파일명이 기록 해시와 다름"
    expected = _OBSERVATION_CALLER_KEYS | _OBSERVATION_SERVER_KEYS
    if set(observation) != expected:
        return "invalid", "관측 기록 키 불일치"
    if not _observation_workspace_trusted(root, observation):
        return "invalid", "관측 워크스페이스·프로토콜 바인딩 불일치"
    report_path = observation.get("report_path")
    try:
        report = local(root, report_path)
        if (
            not report.is_file()
            or digest(report.read_bytes()) != observation.get("report_hash")
        ):
            return "invalid", "관측 보고서 파일 불일치"
    except (OSError, ValueError) as error:
        return "invalid", "관측 보고서 확인 실패: " + str(error)
    if (
        not isinstance(observation.get("review_kinds"), list)
        or review["review_kind"] not in observation["review_kinds"]
    ):
        return "invalid", "관측 검토 종류 불일치"
    for key in (
        "observed_by", "author_session", "reviewer_session",
        "author_id", "reviewer_id",
    ):
        if (
            not isinstance(observation.get(key), str)
            or not observation[key].strip()
        ):
            return "invalid", "관측 식별자 누락: " + key
    if observation["author_session"] == observation["reviewer_session"]:
        return "invalid", "자기 세션 관측"
    if observation["observed_by"] in {
        observation["author_session"], observation["reviewer_session"],
    }:
        return "invalid", "관측자가 당사자 세션"
    for key in (
        "author_id", "reviewer_id", "target_refs",
        "input_fingerprint", "report_hash", "input_revision",
    ):
        if observation.get(key) != review.get(key):
            return "invalid", "관측·검토 기록 불일치: " + key
    # API148: the observed report path must equal the review's report
    # path exactly — identical bytes at a different relative path are not
    # the observed report.
    if observation.get("report_path") != review.get("path"):
        return "invalid", "관측·검토 보고서 경로 불일치"
    return "valid", ""


def _p2_review_observation_state(root, review):
    """Return (state, reason) for a review's adapter observation.

    States: 'valid', 'legacy_unobserved' (no observation provenance was ever
    recorded; such reviews are not invalidated wholesale), 'invalid' (the
    provenance exists but is broken; the reason carries the cause).

    This is the P2-only base.  ``_finance_review_state`` calls it one-way
    as a freshness conjunct, and the public ``review_observation_state``
    composes economic state on top of it — splitting the two is what keeps
    that composition non-recursive (design-r2 §7).
    """
    if not review.get("provenance_path") and not review.get("provenance_hash"):
        return "legacy_unobserved", "관측 파일이 없는 레거시 독립검토"
    try:
        relative = Path(review["provenance_path"])
        if (
            relative.parts != (".gg-observations", relative.name)
            or not relative.name.endswith(".json")
        ):
            return (
                "invalid",
                "관측 파일이 관측 저장소 밖: " + str(review["provenance_path"]),
            )
        data = local(root, review["provenance_path"]).read_bytes()
        if digest(data) != review["provenance_hash"]:
            return "invalid", "관측 기록 해시 불일치"
        try:
            observation = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return "invalid", "관측 기록 JSON 손상: " + str(error)
        if not isinstance(observation, dict):
            return "invalid", "관측 기록이 객체 아님"
        if observation.get("schema") == "gg-review-observation/2":
            return _review_observation_state_v2(
                root, review, observation, data
            )
        if observation.get("schema") != "gg-review-observation/1":
            return "invalid", "관측 스키마 불일치"
        if (
            not isinstance(observation.get("review_kinds"), list)
            or review["review_kind"] not in observation["review_kinds"]
        ):
            return "invalid", "관측 검토 종류 불일치"
        for key in (
            "observed_by", "author_session", "reviewer_session",
            "author_id", "reviewer_id",
        ):
            if not isinstance(observation.get(key), str) or not observation[key].strip():
                return "invalid", "관측 식별자 누락: " + key
        if observation["author_session"] == observation["reviewer_session"]:
            return "invalid", "자기 세션 관측"
        if observation["observed_by"] in {
            observation["author_session"], observation["reviewer_session"],
        }:
            return "invalid", "관측자가 당사자 세션"
        for key in (
            "author_id", "reviewer_id", "target_refs",
            "input_fingerprint", "report_hash",
        ):
            if observation.get(key) != review.get(key):
                return "invalid", "관측·검토 기록 불일치: " + key
        return "valid", ""
    except (KeyError, OSError, ValueError, TypeError, AttributeError) as error:
        return (
            "invalid",
            "관측 기록 확인 실패: " + type(error).__name__ + ": " + str(error),
        )

def review_observation_state(root, review, *, p=None, context=None):
    """Return (state, reason) for a review's adapter observation,
    composing economic freshness on top when a project is supplied
    (design-r2 §7: P2 결과를 보존하고 경제 검토에는 _finance_review_state도
    합성).

    P2 diagnostics are preserved first: any non-"valid" P2 state returns
    unchanged, so an "invalid" or "legacy_unobserved" review is never
    masked or overridden by economic state.  With ``p`` supplied, a
    P2-valid review additionally runs ``_finance_review_state`` (which
    internally calls ``_p2_review_observation_state`` one-way — the
    split keeps this composition non-recursive).  ``stale`` and
    ``invalid`` economic states override the P2 result, returning the
    first issue's reason — "state stale; valid false" is the
    ACCEPTANCE.md test_all_public_paths contract.  ``unresolved`` does
    NOT override: whether a finance report has been bound yet is
    orthogonal to observation validity and already surfaces through
    checks()'s finance_review check_id; the acceptance contract names
    only ``stale`` as an overriding state, so no further public state is
    invented.  ``not_applicable``/``fresh`` return the original
    ("valid", "").

    Without ``p`` (legacy two-argument call sites), the result is
    byte-identical to the P2 base — existing callers keep their exact
    prior behavior.
    """
    p2_state, p2_reason = _p2_review_observation_state(root, review)
    if p2_state != "valid" or p is None:
        return p2_state, p2_reason
    fin_state, fin_issues = _finance_review_state(
        root, p, review,
        context=context or _semantic_context(root, p))
    if fin_state in ("stale", "invalid"):
        reason = (
            fin_issues[0]["reason"] if fin_issues
            else "경제 검토 상태 " + fin_state)
        return fin_state, reason
    return "valid", ""


def review_observation_valid(root, review, *, p=None, context=None):
    """Match adapter observations; this is not an OS-user authentication boundary."""
    return review_observation_state(
        root, review, p=p, context=context)[0] == "valid"


def gate(root, p):
    out = checks(root, p)
    content_covered = set()
    for f in p["facts"].values():
        if f["answer_state"] == "provided" and f["verification"] != "claim_supported":
            out.append(
                result(
                    "fact_unreviewed",
                    f["id"],
                    "blocked",
                    "주장 적합성 검토 미완료",
                    p["revision"],
                )
            )
    for kind in ("content", "logic", "calculation", "docx", "xlsx", "render"):
        valid = []
        causes = []
        for r in p["reviews"].values():
            try:
                state, obs_reason = review_observation_state(root, r, p=p)
                if state in ("invalid", "stale"):
                    causes.append(obs_reason)
                if (
                    r["review_kind"] == kind
                    and r["status"] == "pass"
                    and not r.get("stale")
                    and r["author_id"] != r["reviewer_id"]
                    and state not in ("invalid", "stale")
                    and r["disposition"] == "resolved"
                    and r["input_fingerprint"] == fingerprint(root, p, r["target_refs"])
                    and digest(local(root, r["path"]).read_bytes()) == r["report_hash"]
                    and r.get("target_refs")
                    and r.get("coverage")
                    and isinstance(r.get("findings"), list)
                    and all(isinstance(x, dict) for x in r["findings"])
                    and not any(
                        x.get("severity") == "error" and x.get("status") != "resolved"
                        for x in r["findings"]
                    )
                ):
                    if state == "legacy_unobserved":
                        r["observation"] = "legacy_unobserved"
                    valid.append(r)
                    if kind == "content":
                        content_covered.update(
                            x["id"]
                            for x in r["target_refs"]
                            if x["collection"] == "sections"
                        )
            except (KeyError, OSError, ValueError, TypeError, AttributeError):
                pass
        if not valid:
            reason = "현재 버전의 독립 검토와 위치별 근거 필요"
            if causes:
                reason += " (" + "; ".join(dict.fromkeys(causes)) + ")"
            out.append(
                result(
                    "review_" + kind,
                    "project",
                    "blocked",
                    reason,
                    p["revision"],
                )
            )
    legacy_reviews = sorted(
        r["id"]
        for r in p["reviews"].values()
        if r.get("observation") == "legacy_unobserved"
    )
    if legacy_reviews:
        out.append(
            result(
                "review_observation",
                "project",
                "blocked",
                "관측 없는 레거시 검토라 제출 후보 불가: " + ", ".join(legacy_reviews),
                p["revision"],
            )
        )
    # Missing sections or unreviewed prose must never pass based on a handful of review flags.
    if not p["sections"] or set(p["sections"]) - content_covered:
        out.append(
            result(
                "coverage",
                "project",
                "blocked",
                "모든 절의 내용검토 필요",
                p["revision"],
            )
        )
    profile = p["rules"].get("output_formats", {})
    formats = profile.get("formats", [])
    if (
        not formats
        or not profile.get("source_refs")
        or not set(formats) <= {"docx", "xlsx", "hwp"}
    ):
        out.append(
            result(
                "output_profile",
                "project",
                "blocked",
                "근거에 연결된 최종 제출 형식 미확정",
                p["revision"],
            )
        )
    lineage = output_lineage(p)
    for fmt in formats:
        required = {
            "docx": {"structure", "font", "render"},
            "xlsx": {"structure", "recalculation", "crosscheck", "render"},
            "hwp": {"reopen", "render", "crosscheck"},
        }.get(fmt)
        valid_output = False
        for oid, output in p["outputs"].items():
            try:
                if (
                    output.get("format") != fmt
                    or output.get("stale")
                    or not required
                    or lineage.get(oid, {}).get("state") != "current"
                ):
                    continue
                current_hash = digest(local(root, output["path"]).read_bytes())
                fp = fingerprint(root, p, output["target_refs"])
                covered_sections = {
                    r["id"]
                    for r in output["target_refs"]
                    if r["collection"] == "sections"
                }
                covered_facts = {
                    r["id"] for r in output["target_refs"] if r["collection"] == "facts"
                }
                if (
                    current_hash != output["file_hash"]
                    or fp != output["input_fingerprint"]
                    or set(p["sections"]) - covered_sections
                    or set(p["facts"]) - covered_facts
                ):
                    continue
                records = output.get("checks", [])
                if not isinstance(records, list) or any(
                    not isinstance(check, dict) for check in records
                ):
                    continue
                ids = [check.get("check_id") for check in records]
                # Conflicting/stale reruns cannot be hidden behind an earlier pass.
                if any(ids.count(cid) != 1 for cid in required):
                    continue
                passed = set()
                for check in records:
                    if (
                        check.get("status") == "pass"
                        and check.get("file_hash") == current_hash
                        and check.get("input_fingerprint") == fp
                        and check.get("evidence_path")
                        and check.get("check_id")
                        not in visual_review_blocks(local(root, check["evidence_path"]))
                        and check.get("evidence_hash")
                        == digest(local(root, check["evidence_path"]).read_bytes())
                    ):
                        passed.add(check.get("check_id"))
                if required <= passed:
                    # Lane 24: an economic-scoped output additionally
                    # needs a currently-fresh economic review over the
                    # same target_refs — Lane 19's evidence binding is
                    # write-time only and cannot prove freshness after
                    # registry/review drift.  Non-economic outputs are
                    # unaffected (the predicate returns True for them).
                    if not _economic_output_fresh(root, p, output):
                        continue
                    valid_output = True
                    out.append(
                        result(
                            "output_" + fmt,
                            output["id"],
                            "pass",
                            "현재 출력 파일의 필수 검사·증거 일치",
                            p["revision"],
                            owner="user" if fmt == "hwp" else "skill",
                            required_for=["user_finish"] if fmt == "hwp" else None,
                        )
                    )
                    break
            except (KeyError, OSError, ValueError, TypeError, AttributeError):
                continue
        if not valid_output:
            out.append(
                result(
                    "output_" + fmt,
                    "project",
                    "blocked",
                    "현재 파일 해시의 필수 출력검사·증거 누락/변경",
                    p["revision"],
                    owner="user" if fmt == "hwp" else "skill",
                    required_for=["user_finish"] if fmt == "hwp" else None,
                )
            )
    out.extend(user_finish_checks(p, out))
    out.append(professor_approval_check(p, root))
    return out


def blocks_skill_candidate(check):
    return (
        check["status"] != "pass"
        and check["severity"] == "error"
        and check.get("owner", "skill") == "skill"
    )


def user_finish_checks(p, report):
    hwp_pass = any(
        x["check_id"] == "output_hwp" and x["status"] == "pass" for x in report
    )
    return [
        result(
            item["id"],
            "user_finish",
            "blocked",
            item["reason"],
            p["revision"],
            owner="user",
            required_for=["user_finish"],
        )
        for item in user_finish_task_list(p)
        if item["id"] != "hwp_convert" or not hwp_pass
    ]


def professor_approval_check(p, root=None):
    recorded = False
    outputs = p.get("outputs") or {}
    try:
        lineage = output_lineage(p)
        current = {
            oid
            for oid, verdict in lineage.items()
            if verdict["state"] == "current"
        }
        degraded = any(
            verdict["state"] == "invalid"
            or (verdict["state"] == "current" and outputs[oid].get("stale"))
            for oid, verdict in lineage.items()
        )
    except (TypeError, AttributeError, KeyError):
        current, degraded = set(), True
    for approval in p.get("approvals", {}).values():
        try:
            if (
                root is not None
                and current
                and not degraded
                and approval.get("kind") == "professor"
                and approval.get("status") == "confirmed"
                and not approval.get("stale")
                and approval.get("evidence_path")
                and approval.get("scope") == "project_outputs"
                and {
                    (ref["collection"], ref["id"])
                    for ref in approval.get("target_refs", [])
                } == {("outputs", key) for key in current}
                and approval.get("human_confirmation", {}).get("explicit") is True
                and approval["human_confirmation"].get("confirmed_by")
                and approval.get("registered_revision") == p["revision"]
                and approval.get("input_revision") == p["revision"] - 1
                and approval.get("target_refs")
                and approval.get("input_fingerprint")
                == fingerprint(root, p, approval["target_refs"])
                and approval.get("evidence_hash")
                == digest(local(root, approval["evidence_path"]).read_bytes())
            ):
                recorded = True
                break
        except (TypeError, AttributeError, KeyError, OSError, ValueError):
            continue
    return result(
        "professor_approval",
        "project",
        "pass" if recorded else "blocked",
        "교수 승인 기록 확인" if recorded else "교수 승인 근거 미기록",
        p["revision"],
        owner="professor",
        required_for=["professor_approval"],
    )


def completion(root, p, report=None):
    report = list(report if report is not None else gate(root, p))
    user_items = [
        {
            "id": x["check_id"],
            "status": "needs_user",
            "owner": "user",
            "reason": x["reason"],
        }
        for x in report
        if x.get("owner") == "user"
        and x.get("target") == "user_finish"
        and x["status"] != "pass"
    ]
    professor = next(
        (x for x in report if x["check_id"] == "professor_approval"),
        None,
    )
    return {
        "skill_ready": not any(blocks_skill_candidate(x) for x in report),
        "guideline_ready": next(
            (
                None
                if x.get("status") == "not_configured"
                else x.get("status") == "pass"
                for x in report
                if x.get("check_id") == "guideline_profile"
            ),
            None,
        ),
        "user_finish_pending": user_items,
        "professor_approval_pending": not (professor and professor["status"] == "pass"),
        "notice": "스킬 Ⅰ~Ⅵ 작성·DOCX/XLSX 검사 완료와 한글 마무리·교수 승인은 별개다. 사용자 미완료를 자동 통과·N/A하지 않는다.",
    }


def merged(root, p):
    parts = []
    for s in sorted(p["sections"].values(), key=lambda x: x["order"]):
        body = draft(local(root, s["path"]).read_text(encoding="utf-8"))
        title = s["title"]
        parts.append(
            body
            if body.splitlines() and body.splitlines()[0].lstrip("# ") == title
            else title + "\n\n" + body
        )
    return "\n\n".join(parts)


def _cleanup_own_temp(root, rel, *, operation, target, fields=None):
    """Remove a workspace temp directory; failure after a verified commit
    must surface as committed_cleanup_pending, not silently pass."""
    try:
        shutil.rmtree(local(root, rel))
    except OSError as error:
        raise _op(
            operation,
            target,
            "committed_cleanup_pending",
            "temp_cleanup_failed",
            detail=str(error),
            cleanup_errors=[{"path": rel, "error": str(error)}],
            preserved_paths=[rel],
            **(fields or {}),
        ) from error


def _publication_failure(error, operation, target, request_id=None):
    """Map a gg_publication failure to the pinned OperationError result."""
    state = getattr(error, "result", {}).get("state")
    if state == "not_published":
        return _op(
            operation, target, "not_committed", "managed_publish_failed",
            detail=str(error), request_id=request_id,
        )
    return _op(
        operation, target, "indeterminate", "managed_publish_indeterminate",
        detail=str(error), publication_state=state, request_id=request_id,
    )


def _requested_publish_or_preserve(root, capability, *, operation,
                                   managed, requested_rel, request_id):
    """Publish a managed file to its requested workspace path.

    The managed bundle is the committed artifact; a blocked requested path
    never deletes it — the failure result preserves it and reports
    publication_state=managed_ready (API §4).  The bundle commit already
    landed, so a blocked requested publish is committed_cleanup_pending,
    not "nothing happened".
    """
    publication = _publication_mod()
    try:
        return publication.publish_requested_file(
            root, capability, managed=managed,
            requested_path=requested_rel,
        )
    except Exception as error:
        raise _op(
            operation,
            "requested_output",
            "committed_cleanup_pending",
            "requested_path_blocked",
            detail=str(error),
            publication_state="managed_ready",
            preserved_paths=[managed["path"]],
            request_id=request_id,
        ) from error


def export(root, kind):
    root = Path(root)
    box = {}

    def body(capability):
        value = export_locked(root, kind, capability=capability)
        box["request_id"] = value.get("request_id")
        return value

    def confirm():
        # A completed body proves intent, not commit — re-check the
        # receipt namespace for the deterministic request_id.
        rid = box.get("request_id")
        if not rid:
            return ("indeterminate", {})
        try:
            found = bool(_find_request(root, rid))
        except Exception:
            return ("indeterminate", {})
        if found:
            return ("committed_cleanup_pending", {"request_id": rid})
        return ("not_committed", {"request_id": rid})

    return _guarded(root, "export", "export", body, confirm)


def export_locked(root, kind, *, capability):
    """Export through an immutable managed publication (API §3, SPEC §5).

    Deterministic request_id from the canonical request preimage gives
    dedup/resume: an identical committed request returns ``existing``; the
    same request_id with a different preimage is a conflict.  A previous
    incomplete/blocked publish is never auto-repaired — the managed
    bundle stays preserved and the conflict is loud.
    """
    _lock_mod().assert_held(capability, root)
    p = load(root)
    report = gate(root, p)
    if kind == "submission_candidate":
        blocking = [x for x in report if blocks_skill_candidate(x)]
        if blocking:
            reasons = []
            for check in blocking:
                if check["reason"] not in reasons:
                    reasons.append(check["reason"])
            raise _op(
                "export", "export", "not_committed",
                "submission_candidate_blocked",
                detail="제출 후보 차단: " + "; ".join(reasons),
            )
    label = {
        "draft": "검토전_초안",
        "review": "검토용",
        "submission_candidate": "검사완료_교수확인전",
    }[kind]
    dest_rel = "build/%s/%s" % (p["revision"], kind)
    requested_rel = dest_rel + "/" + label + ".md"
    refs = sort_target_refs(_export_refs(p))
    fp = fingerprint(root, p, refs)
    request = {
        "kind": "export",
        "export_kind": kind,
        "input_revision": p["revision"],
        "project_sha256": digest(p),
        "target_refs": refs,
        "input_fingerprint": fp,
    }
    request_sha = _request_sha256(request)
    request_id = "gg:export:" + request_sha
    intent = {
        "request": request,
        "request_id": request_id,
        "request_sha256": request_sha,
        "input_revision": p["revision"],
        "target_refs": refs,
        "input_fingerprint": fp,
    }
    # Dedup/resume through the receipt namespace only — export/paper/import
    # never touch the canonical request ledger (API §10).  The ledger is
    # still consulted for the same-id cross-kind conflict: an existing
    # apply/adopt entry under this id is always a different kind.
    if request_id in p.get("requests", {}):
        raise _op(
            "export", "export", "not_committed", "request_id_conflict",
            request_id=request_id,
        )
    receipts = _find_request(root, request_id, operation="export")
    receipt = receipts[0] if receipts else None
    if receipt is not None and receipt.get("request_sha256") != request_sha:
        raise _op(
            "export", "export", "not_committed", "request_id_conflict",
            request_id=request_id,
        )
    dest_dir = local(root, dest_rel)
    requested = local(root, requested_rel)
    if receipt is not None:
        declared = {
            f["path"]: f["sha256"]
            for f in receipt.get("files", [])
            if isinstance(f, dict)
        }
        try:
            actual = (
                digest(requested.read_bytes()) if requested.is_file() else None
            )
        except OSError as error:
            raise _op(
                "export", "export", "indeterminate",
                "committed_requested_unverified", detail=str(error),
            ) from error
        if declared.get(label + ".md") is not None and actual == declared.get(
            label + ".md"
        ):
            return {
                "path": str(requested),
                "status": "existing",
                "request_id": request_id,
            }
        raise _op(
            "export", "export", "indeterminate",
            "committed_publication_unverified",
            detail="발행 receipt는 커밋됐으나 목적지 payload와 불일치",
            request_id=request_id,
        )
    if dest_dir.exists() or requested.exists():
        raise _op(
            "export", "export", "not_committed",
            "export_destination_conflict",
            detail="소유권 미확인 산출물 충돌: 기존 경로를 보존함",
        )
    body_text = merged(root, p)
    state = completion(root, p, report)
    manifest = {
        "kind": kind,
        "revision": p["revision"],
        "professor_approval": "not_confirmed",
        "skill_ready": state["skill_ready"],
        "user_finish_pending": state["user_finish_pending"],
        "professor_approval_pending": state["professor_approval_pending"],
        "checks": report,
        "request_id": request_id,
        "request_sha256": request_sha,
        "notice": state["notice"],
    }
    staging_rel = ".gg-export-staging-" + request_sha[:16]
    staging = local(root, staging_rel)
    payload = {}
    cleanup_errors = []
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        body_bytes = body_text.encode()
        payload[label + ".md"] = body_bytes
        manifest["files"] = {label + ".md": digest(body_bytes)}
        if kind == "submission_candidate":
            for check in report:
                if (
                    check["check_id"].startswith("output_")
                    and check["status"] == "pass"
                ):
                    output = p["outputs"][check["target"]]
                    data = local(root, output["path"]).read_bytes()
                    if digest(data) != output["file_hash"]:
                        raise _op(
                            "export", "export", "not_committed",
                            "output_changed_after_check",
                            detail="검사 후 출력 파일 변경: 제출 후보 무효",
                        )
                    name = "검사완료_교수확인전." + output["format"]
                    if name in payload:
                        raise _op(
                            "export", "export", "not_committed",
                            "duplicate_output_name",
                            detail="같은 형식의 출력 사본 이름 충돌: " + name,
                        )
                    payload[name] = data
                    manifest["files"][name] = digest(data)
            if (
                load(root)["revision"] != p["revision"]
                or body_text != merged(root, p)
                or any(blocks_skill_candidate(x) for x in gate(root, p))
            ):
                raise _op(
                    "export", "export", "not_committed",
                    "input_changed_during_export",
                    detail="출력 중 입력 변경: 제출 후보 무효",
                )
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode()
        payload["manifest.json"] = manifest_bytes
        for rel_name, data in payload.items():
            atomic(staging / rel_name, data)
        files = sorted(
            (
                {
                    "path": rel_name,
                    "sha256": digest(data),
                    "size": len(data),
                }
                for rel_name, data in payload.items()
            ),
            key=lambda e: e["path"].encode("utf-8"),
        )
        publication = _publication_mod()
        try:
            result = publication.publish_directory(
                root,
                capability,
                staging=str(staging),
                destination=dest_rel,
                intent=intent,
                files=files,
            )
        except Exception as error:
            raise _publication_failure(
                error, "export", "export", request_id
            ) from error
        if result.get("state") != "published":
            raise _op(
                "export", "export", "indeterminate",
                "managed_publish_unexpected",
                publication_state=result.get("state"),
                request_id=request_id,
            )
        cleanup_errors = list(result.get("cleanup_errors") or [])
    finally:
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
    if cleanup_errors:
        raise _op(
            "export", "export", "committed_cleanup_pending",
            "publish_cleanup_pending",
            cleanup_errors=cleanup_errors,
            request_id=request_id,
        )
    return {
        "path": str(requested),
        "status": "generated",
        "request_id": request_id,
    }


# Docstring-declared str|int economic fields, with the same key-alias
# order need() resolves in gg_school_paper's growth_target_table/
# _legacy_paper, paired with the fixed unit label the rendered table
# asserts.  These live here, not in gg_school_paper.py, because design-r2
# §9 freezes that module and prescribes paper's value/alias validation
# as an external adapter on core.paper.
_NATIVE_FIELD_ALIASES = (
    (("start_area_m2", "area_m2"), "㎡"),
    (("target_area_m2", "goal_area_m2"), "㎡"),
    (("start_sales_thousand", "sales_thousand"), "천원"),
    (("target_sales_thousand", "goal_sales_thousand"), "천원"),
    (("start_profit_thousand", "profit_thousand"), "천원"),
    (("target_profit_thousand", "goal_profit_thousand"), "천원"),
)


def _is_native_number(value):
    """Plain native number check for the declared str|int fields.

    Accepts an int (bool rejected) or a str that is, after stripping
    whitespace and an optional leading '-': a comma-free run of digits
    with an optional single '.' decimal fraction (kept because the ㎡
    area fields can legitimately be fractional and comma-free decimals
    were already accepted), OR a properly thousands-grouped form —
    1-3 digits, then one or more comma-separated groups of EXACTLY 3
    digits ("12,000", "1,234,567").  Malformed groupings like "1,,2",
    "12,34", "1,23,456" are rejected; commas are never simply deleted.
    """
    if type(value) is bool:
        return False
    if type(value) is int:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.startswith("-"):
        text = text[1:]
    int_part, dot, frac = text.partition(".")
    if dot and not (
            frac and all("0" <= c <= "9" for c in frac)):
        return False
    groups = int_part.split(",")
    if len(groups) == 1:
        digits = groups[0]
        return bool(digits) and all("0" <= c <= "9" for c in digits)
    if not groups[0] or len(groups[0]) > 3 or not all(
            "0" <= c <= "9" for c in groups[0]):
        return False
    return all(
        len(g) == 3 and all("0" <= c <= "9" for c in g)
        for g in groups[1:])


def validate_paper_native_fields(spec):
    """Pre-save check on the docstring-declared str|int economic fields.

    These six fields are rendered under fixed-unit labels (생산규모 ㎡,
    연간매출액/순이익 천원), so each resolved value must be a plain native
    number — an int, or a str of digits with optional leading '-',
    optional '.', and comma thousands separators.  Embedded unit tokens,
    currency symbols, or prose suffixes ("99999달러", "₩12000",
    "약 5000") would publish a self-contradictory cell and are reported
    as status="fail" issues.  Only the value need() would actually
    resolve is checked; absent optional fields produce no issues and the
    function never raises.  The Issue dict shape mirrors
    gg_finance.validate_economic_inputs literally — its issue() builder
    is a private closure and is not imported.
    """
    issues = []
    if not isinstance(spec, dict):
        return issues
    for keys, unit in _NATIVE_FIELD_ALIASES:
        key = None
        for candidate in keys:
            if candidate in spec and spec[candidate] is not None:
                if str(spec[candidate]).strip():
                    key = candidate
                    break
        if key is None:
            continue
        value = spec[key]
        if _is_native_number(value):
            continue
        issues.append({
            "code": "PAPER_NATIVE_FIELD_INVALID",
            "check_id": "finance_review",
            "status": "fail",
            "severity": "error",
            "fact_id": None,
            "consumer_id": None,
            "location": {
                "path": None, "section_id": None, "line": None,
                "column": None, "table": None, "row": None, "cell": None,
            },
            "reason": "spec 필드 %s(단위 %s) 값 %r: 단위 없는 십진 숫자여야 함"
                      % (key, unit, value),
            "required_for": ["submission_candidate"],
            "remedy": "단위·문자 없이 숫자만 전달 (예: 12000 또는 \"12,000\")",
        })
    return issues


def paper(root, spec_path, spec_bytes, requested_path):
    """Generate the markdown review body through a managed publication
    (API §6).  The spec file bytes are read once by the caller and never
    re-read — a swapped file cannot ride into the request."""
    root = Path(root)
    try:
        spec = json.loads(spec_bytes)
    except ValueError as error:
        raise _op("paper", "requested_output", "not_committed",
                  "invalid_spec", detail=str(error)) from error
    if not isinstance(spec, dict):
        raise _op("paper", "requested_output", "not_committed",
                  "invalid_spec", detail="spec은 JSON 객체여야 함")
    spec.setdefault(
        "school_profile",
        {"school": "경기대학교", "department": "일반대학원 한국어교육학과"},
    )
    # DESIGN.md §7: the native numeric-format adapter runs before save —
    # refuse malformed declared str|int economic fields before any lock,
    # receipt, or staging work, same refusal level as invalid_spec.
    # §9: the validator lives in this module as an external adapter on
    # core.paper — gg_school_paper.py is frozen.
    native_issues = validate_paper_native_fields(spec)
    if any(i["status"] == "fail" for i in native_issues):
        raise _op("paper", "requested_output", "not_committed",
                  "native_field_invalid",
                  detail="native 숫자 필드 형식 오류: " + "; ".join(
                      i["reason"] for i in native_issues
                      if i["status"] == "fail"),
                  issues=native_issues)
    spec_rel = str(spec_path)

    def body(capability):
        _lock_mod().assert_held(capability, root)
        p = load(root)
        refs = sort_target_refs(_export_refs(p))
        fp = fingerprint(root, p, refs)
        try:
            requested = local(root, requested_path)
        except ValueError as error:
            raise _op("paper", "requested_output", "not_committed",
                      "invalid_requested_path", detail=str(error)) from error
        requested_rel = str(requested.relative_to(Path(root).resolve()))
        request = {
            "kind": "paper",
            "input_revision": p["revision"],
            "target_refs": refs,
            "input_fingerprint": fp,
            "spec_path": spec_rel,
            "spec_sha256": digest(spec_bytes),
            "requested_path": requested_rel,
            "output_format": "md",
        }
        request_sha = _request_sha256(request)
        request_id = "gg:paper:" + request_sha
        intent = {
            "request": request,
            "request_id": request_id,
            "request_sha256": request_sha,
            "input_revision": p["revision"],
            "target_refs": refs,
            "input_fingerprint": fp,
        }
        if request_id in p.get("requests", {}):
            raise _op("paper", "requested_output", "not_committed",
                      "request_id_conflict", request_id=request_id)
        receipts = _find_request(root, request_id, operation="paper")
        receipt = receipts[0] if receipts else None
        bundle_rel = "build/%s/paper" % p["revision"]
        managed_rel = bundle_rel + "/paper.md"
        if receipt is not None:
            if receipt.get("request_sha256") != request_sha:
                raise _op("paper", "requested_output", "not_committed",
                          "request_id_conflict", request_id=request_id)
            managed_sha = next(
                (
                    f["sha256"]
                    for f in receipt.get("files", [])
                    if isinstance(f, dict) and f.get("path") == "paper.md"
                ),
                None,
            )
            if managed_sha is None or not local(
                root, managed_rel
            ).is_file():
                raise _op("paper", "requested_output", "indeterminate",
                          "committed_publication_missing",
                          request_id=request_id)
            if requested.is_file():
                try:
                    actual = digest(requested.read_bytes())
                except OSError as error:
                    raise _op("paper", "requested_output", "indeterminate",
                              "committed_requested_unverified",
                              detail=str(error)) from error
                if actual == managed_sha:
                    return {
                        "path": str(requested),
                        "status": "existing",
                        "request_id": request_id,
                    }
            # Same request committed to a managed bundle; requested path is
            # absent or stale → retry only the requested publish, never
            # republish a second bundle.
            _requested_publish_or_preserve(
                root, capability, operation="paper",
                managed={
                    "path": managed_rel,
                    "sha256": managed_sha,
                    "publication_path": bundle_rel,
                },
                requested_rel=requested_rel,
                request_id=request_id,
            )
            return {
                "path": str(requested),
                "status": "generated",
                "request_id": request_id,
            }
        if requested.exists():
            raise _op("paper", "requested_output", "not_committed",
                      "requested_output_conflict",
                      detail="기존 산출물을 덮어쓰지 않음")
        from gg_school_paper import paper as school_paper

        body_text = school_paper(spec)
        body_bytes = body_text.encode()
        body_sha = digest(body_bytes)
        staging_rel = ".gg-paper-staging-" + request_sha[:16]
        staging = local(root, staging_rel)
        try:
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            atomic(staging / "paper.md", body_bytes)
            files = [
                {
                    "path": "paper.md",
                    "sha256": body_sha,
                    "size": len(body_bytes),
                }
            ]
            publication = _publication_mod()
            try:
                result = publication.publish_directory(
                    root,
                    capability,
                    staging=str(staging),
                    destination=bundle_rel,
                    intent=intent,
                    files=files,
                )
            except Exception as error:
                raise _publication_failure(
                    error, "paper", "requested_output", request_id
                ) from error
            if result.get("state") != "published":
                raise _op("paper", "requested_output", "indeterminate",
                          "managed_publish_unexpected",
                          publication_state=result.get("state"),
                          request_id=request_id)
            cleanup_errors = list(result.get("cleanup_errors") or [])
        finally:
            if staging.exists():
                try:
                    shutil.rmtree(staging)
                except OSError:
                    pass
        _requested_publish_or_preserve(
            root, capability, operation="paper",
            managed={
                "path": managed_rel,
                "sha256": body_sha,
                "publication_path": bundle_rel,
            },
            requested_rel=requested_rel,
            request_id=request_id,
        )
        if cleanup_errors:
            raise _op(
                "paper", "requested_output", "committed_cleanup_pending",
                "publish_cleanup_pending",
                cleanup_errors=cleanup_errors,
                request_id=request_id,
            )
        return {
            "path": str(requested),
            "status": "generated",
            "request_id": request_id,
        }

    box = {}

    def tracked_body(capability):
        value = body(capability)
        box["request_id"] = value.get("request_id")
        return value

    def confirm():
        rid = box.get("request_id")
        if not rid:
            return ("indeterminate", {})
        try:
            found = bool(_find_request(root, rid, operation="paper"))
        except Exception:
            return ("indeterminate", {})
        if found:
            return ("committed_cleanup_pending", {"request_id": rid})
        return ("not_committed", {"request_id": rid})

    return _guarded(root, "paper", "requested_output", tracked_body, confirm)

def export_complete(folder, kind, revision):
    manifest = Path(folder) / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = data["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (
        data.get("kind") != kind
        or data.get("revision") != revision
        or not isinstance(files, dict)
        or not files
    ):
        return False
    try:
        return all(
            digest((Path(folder) / name).read_bytes()) == digest_value
            for name, digest_value in files.items()
        )
    except OSError:
        return False


def _import_inventory(root):
    """Source-content inventory for import: protocol/control files are not
    source content — the source guard owner record and the import receipt
    stub change across calls and must not falsify the freeze check."""
    inventory = migration_inventory(root)
    return {
        name: sha
        for name, sha in inventory.items()
        if name.split("/")[0] not in _control_files()
    }


def _import_request(src, dest, inventory):
    return {
        "kind": "import",
        "source_inventory": [
            {
                "path": name,
                "sha256": sha,
                "size": (Path(src) / name).stat().st_size,
            }
            for name, sha in sorted(inventory.items())
        ],
        "destination": str(dest),
    }


def _import_retry(src, dest):
    """Destination exists: it may be this import's own earlier commit.

    The request preimage is recomputed from the current source inventory.
    The destination-side import receipt (C-written, verified through
    verify_publication — protocol binding, payload inventory and lineage
    included) is the authoritative binding; the source-side stub is only a
    hint.  Identical completed requests return the recorded result;
    anything else is a loud conflict — never an overwrite or an empty
    destination repair."""
    request = _import_request(src, dest, _import_inventory(src))
    request_sha = _request_sha256(request)
    request_id = "gg:import:" + request_sha
    receipt_path = dest / ".gg-import-publication.json"
    if receipt_path.is_file():
        try:
            raw = receipt_path.read_bytes()
            header = json.loads(raw)
            publication_id = header["publication_id"]
        except (OSError, ValueError, KeyError) as error:
            raise _op(
                "import", "import_destination", "not_committed",
                "import_receipt_conflict",
                detail="목적지 receipt 판독 불가: " + str(error),
            ) from error
        ref = {
            "schema": "gg-publication-ref/1",
            "id": publication_id,
            "path": ".gg-import-publication.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        try:
            receipt = _publication_mod().verify_publication(
                dest, publication_ref=ref
            )
        except Exception as error:
            raise _op(
                "import", "import_destination", "indeterminate",
                "committed_publication_unverified", detail=str(error),
            ) from error
        if (
            receipt.get("request_sha256") == request_sha
            and receipt.get("request_id") == request_id
        ):
            try:
                return load(dest)
            except (OSError, ValueError) as error:
                raise _op(
                    "import", "import_destination", "indeterminate",
                    "imported_destination_unreadable", detail=str(error),
                ) from error
        raise _op(
            "import", "import_destination", "not_committed",
            "import_receipt_conflict",
            detail="기존 목적지는 다른 import 요청의 결과",
        )
    # No destination receipt: the source stub decides whether the
    # destination was once committed (receipt removed → indeterminate)
    # or is foreign content (refused, preserved).
    stub_path = src / ".gg-import-publication.json"
    if not stub_path.is_file():
        raise _op(
            "import", "import_destination", "not_committed",
            "import_destination_conflict",
            detail="목적지 존재 + import receipt 없음",
        )
    try:
        stub = json.loads(stub_path.read_bytes())
    except (OSError, ValueError) as error:
        raise _op(
            "import", "import_destination", "not_committed",
            "import_receipt_conflict", detail=str(error),
        ) from error
    if (
        stub.get("request_sha256") == request_sha
        and stub.get("request_id") == request_id
    ):
        raise _op(
            "import", "import_destination", "indeterminate",
            "imported_destination_unreadable",
            detail="import가 기록된 목적지에서 receipt가 사라짐",
        )
    raise _op(
        "import", "import_destination", "not_committed",
        "import_receipt_conflict",
        detail="기존 목적지는 다른 import 요청의 결과",
    )


def migrate(src, dest, *, offline_confirmed=False):
    """Import a legacy Markdown folder into a new guarded workspace
    (API §7, SPEC §7).

    A v2 source is frozen under its own held guard for the whole read
    window; a source without a ready protocol is only imported with the
    explicit --offline-confirmed flag and is verified by the dual
    inventory.  The staged workspace gets its own protocol before
    conversion, publishes under its own guard, and the destination guard
    is rebound after the rename — the destination never appears without
    a valid protocol."""
    src, dest = Path(src).resolve(), Path(dest).resolve()
    lm = _lock_mod()
    if src.is_relative_to(dest) or dest.is_relative_to(src):
        raise _op(
            "import", "import_destination", "not_committed",
            "nested_workspaces",
            detail="마이그레이션은 겹치지 않는 새 폴더에만 가능",
        )
    if (src / "project.json").exists():
        raise _op(
            "import", "import_destination", "not_committed",
            "already_new_format",
            detail="이미 새 형식: import 대신 status 사용",
        )
    if not src.is_dir() or (src / "migration").exists():
        raise _op(
            "import", "import_destination", "not_committed",
            "invalid_source", detail="원본 폴더·기존 migration 확인 필요",
        )
    if _recovery_active(src):
        raise _op(
            "import", "import_destination", "not_committed",
            "source_control_blocked",
            detail="원본에 진행 중 복구 상태가 있어 읽기 고정 불가",
        )
    status = lm.inspect_lock(src)
    if status["structure"] in ("damaged", "initializing"):
        raise _op(
            "import", "import_destination", "not_committed",
            "source_control_blocked",
            detail="원본 프로토콜 손상·초기화 중: 자동 수리 없음",
        )
    if dest.exists():
        return _import_retry(src, dest)
    source_cap = None
    if status["structure"] == "ready":
        source_cap = lm.Lock(src)
        source_cap.__enter__()
    elif not offline_confirmed:
        raise _op(
            "import", "import_destination", "not_committed",
            "offline_confirmation_required",
            detail="프로토콜 없는 원본은 --offline-confirmed 필요",
        )
    migrated = None
    try:
        before = _import_inventory(src)
        request = _import_request(src, dest, before)
        request_sha = _request_sha256(request)
        request_id = "gg:import:" + request_sha
        intent = {
            "request": request,
            "request_id": request_id,
            "request_sha256": request_sha,
            "input_revision": None,
            "target_refs": None,
            "input_fingerprint": None,
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(
            prefix=".gg-import-", dir=dest.parent
        )
        try:
            staged = Path(tmp.name) / "workspace"
            shutil.copytree(
                src,
                staged,
                ignore=shutil.ignore_patterns(
                    ".work", ".git", "__pycache__", ".gg-lock",
                    ".gg-recovery", ".gg-recovery-active.json",
                    ".gg-import-publication.json",
                ),
                symlinks=True,
            )
            if (
                _import_inventory(staged) != before
                or _import_inventory(src) != before
            ):
                raise _op(
                    "import", "import_destination", "not_committed",
                    "source_changed_during_copy",
                    detail="복사 중 원본 변경: 원본을 대조한 뒤 재시도",
                )
            # Fresh v2 protocol on the empty staging dir first — install_new
            # refuses once project.json exists — then the payload lands.
            lm.install_new(staged)
            cleanup_errors = []
            try:
                with lm.Lock(staged) as staged_cap:
                    p = migrate_staged(staged, before, capability=staged_cap)
                    if request_id in p.get("requests", {}):
                        raise _op(
                            "import", "import_destination", "not_committed",
                            "request_id_conflict",
                            detail="변환된 ledger가 import 요청 ID와 충돌",
                            request_id=request_id,
                        )
                    if _import_inventory(src) != before:
                        raise _op(
                            "import", "import_destination", "not_committed",
                            "source_changed_during_convert",
                            detail="변환 중 원본 변경: 원본을 대조한 뒤 재시도",
                        )
                    staged_inventory = _import_inventory(staged)
                    payload = [
                        {
                            "path": name,
                            "sha256": sha,
                            "size": (staged / name).stat().st_size,
                        }
                        for name, sha in sorted(staged_inventory.items())
                    ]
                    publication = _publication_mod()
                    try:
                        result = publication.publish_workspace(
                            staged,
                            staged_cap,
                            destination=str(dest),
                            intent=intent,
                            files=payload,
                        )
                    except Exception as error:
                        state = getattr(error, "result", {}).get("state")
                        if state == "not_published":
                            raise _op(
                                "import", "import_destination",
                                "not_committed",
                                "destination_publish_failed",
                                detail=str(error),
                            ) from error
                        raise _op(
                            "import", "import_destination", "indeterminate",
                            "destination_publish_indeterminate",
                            publication_state=state, detail=str(error),
                        ) from error
                    # Post-rename verification, protocol check and
                    # capability rebind are all inside publish_workspace;
                    # any leftover rebind/cleanup work is reported here.
                    cleanup_errors = list(result.get("cleanup_errors") or [])
                    migrated = p
            except Exception as error:
                cleanup_type = getattr(lm, "LockCleanupError", None)
                if cleanup_type is None or not isinstance(error, cleanup_type):
                    raise
                raise _op(
                    "import", "import_destination",
                    "committed_cleanup_pending" if dest.exists()
                    else "not_committed",
                    "staged_release_failed",
                    detail=str(error),
                    cleanup_errors=[{"release": str(error)}],
                ) from error
        finally:
            tmp.cleanup()
        try:
            load(dest)
            local(dest, ".gg-lock/protocol.json").read_bytes()
        except (OSError, ValueError) as error:
            raise _op(
                "import", "import_destination", "committed_cleanup_pending",
                "post_publish_verify_failed", detail=str(error),
            ) from error
        if _import_inventory(src) != before:
            raise _op(
                "import", "import_destination", "committed_cleanup_pending",
                "source_changed_after_publish",
            )
        stub = {
            "schema": "gg-import-receipt/1",
            "request_id": request_id,
            "request_sha256": request_sha,
            "destination": str(dest),
            "publication_ref": result.get("publication_ref"),
        }
        try:
            atomic(
                src / ".gg-import-publication.json",
                json.dumps(stub, ensure_ascii=False, indent=2).encode(),
            )
        except OSError as error:
            raise _op(
                "import", "import_destination", "committed_cleanup_pending",
                "stub_write_failed", detail=str(error),
                cleanup_errors=[
                    {"path": ".gg-import-publication.json",
                     "error": str(error)}
                ],
            ) from error
        if cleanup_errors:
            raise _op(
                "import", "import_destination", "committed_cleanup_pending",
                "publish_cleanup_pending",
                cleanup_errors=cleanup_errors,
            )
        return migrated
    finally:
        if source_cap is not None:
            try:
                source_cap.__exit__(*sys.exc_info())
            except Exception as error:
                if migrated is not None and dest.exists():
                    state = "committed_cleanup_pending"
                elif not dest.exists():
                    state = "not_committed"
                else:
                    state = "indeterminate"
                raise _op(
                    "import", "import_destination", state,
                    "source_release_failed", detail=str(error),
                    cleanup_errors=[{"release": str(error)}],
                ) from error


def migration_inventory(root):
    inventory = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {".work", ".git", "__pycache__"})
        for name in dirs + sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(
                    "마이그레이션은 심볼릭 링크를 따라가지 않음: "
                    + str(path.relative_to(root))
                )
            if path.is_file():
                inventory[str(path.relative_to(root))] = digest(path.read_bytes())
            elif not path.is_dir():
                raise ValueError(
                    "일반 파일/폴더가 아닌 원본: " + str(path.relative_to(root))
                )
    return inventory


def migrate_staged(dest, inventory, *, capability):
    _init_locked(dest, capability)
    ops, comparison, inputs, issues = [], [], [], []
    receipt_ids, field_values = {}, {}
    # Index verbatim originals, never treat old 'verified' strings as new approval.
    # Free-form interviews/assumptions stay source records until explicit field mapping.
    for name, original_hash in sorted(inventory.items()):
        path = local(dest, name)
        section = Path(name).parent == Path("sections") and path.suffix == ".md"
        source_path = "migration/original-sections/" + path.name if section else name
        sid = "legacy:" + name
        ops.append(
            {
                "collection": "sources",
                "value": dict(
                    id=sid,
                    path=source_path,
                    legacy=True,
                    verification="unreviewed",
                    original_path=name,
                ),
            }
        )
        entry = dict(
            path=name,
            preserved_path=source_path,
            original_hash=original_hash,
            status="unreviewed",
        )
        if path.suffix.lower() in {".md", ".txt", ".json", ".jsonl"}:
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                issues.append(
                    dict(
                        code="legacy_encoding",
                        path=name,
                        reason="원본 바이트 보존; 텍스트 대조 보류",
                    )
                )
            else:
                entry["numeric_tokens"] = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
                entry["unresolved_lines"] = [
                    dict(line=i, text=line)
                    for i, line in enumerate(text.splitlines(), 1)
                    if "미결" in line or "미확정" in line or "[ ]" in line
                ]
                entry["absolute_path_lines"] = [
                    i
                    for i, line in enumerate(text.splitlines(), 1)
                    if re.search(r"/Users/|/home/|[A-Za-z]:[\\/]", line)
                ]
                if entry["absolute_path_lines"]:
                    issues.append(
                        dict(
                            code="legacy_path",
                            path=name,
                            lines=entry["absolute_path_lines"],
                            reason="이전 절대경로는 따라가지 않음; 현재 원문 위치 대조 필요",
                        )
                    )
                if path.suffix.lower() == ".jsonl":
                    entry["records"] = []
                    for line_number, line in enumerate(text.splitlines(), 1):
                        if not line.strip():
                            continue
                        record = dict(
                            line=line_number, raw=line, verification="unreviewed"
                        )
                        entry["records"].append(record)
                        try:
                            value = json.loads(line)
                            if not isinstance(value, dict):
                                raise ValueError("객체 아님")
                        except ValueError:
                            issues.append(
                                dict(
                                    code="legacy_record_invalid",
                                    path=name,
                                    line=line_number,
                                )
                            )
                            continue
                        record["legacy_value"] = value
                        rid = value.get("id")
                        if isinstance(rid, str):
                            if rid in receipt_ids:
                                issues.append(
                                    dict(
                                        code="legacy_duplicate_id",
                                        path=name,
                                        line=line_number,
                                        id=rid,
                                        previous=receipt_ids[rid],
                                    )
                                )
                            receipt_ids[rid] = dict(path=name, line=line_number)
                        field = value.get("field_id")
                        if isinstance(field, str) and "value" in value:
                            if (
                                field in field_values
                                and field_values[field] != value["value"]
                            ):
                                issues.append(
                                    dict(
                                        code="legacy_value_conflict",
                                        path=name,
                                        line=line_number,
                                        field_id=field,
                                    )
                                )
                            field_values[field] = value["value"]
                        if (
                            value.get("kind") in {"price", "stat", "calc"}
                            or "value" in value
                        ):
                            missing = [
                                k
                                for k in ("unit", "period", "scope")
                                if not value.get(k)
                            ]
                            if missing:
                                issues.append(
                                    dict(
                                        code="legacy_numeric_context",
                                        path=name,
                                        line=line_number,
                                        missing=missing,
                                    )
                                )
        inputs.append(entry)
    for i, path in enumerate(
        sorted((dest / "sections").glob("*.md"), key=lambda p: section_order(p.name))
    ):
        path = local(dest, path.relative_to(dest))
        text = path.read_text(encoding="utf-8")
        blocks = re.findall(
            r"^##\s+(INPUT|RESEARCH|FACTS|DRAFT|OPEN|STATUS)\b", text, re.M
        )
        if any(blocks.count(b) > 1 for b in set(blocks)) or (
            blocks and blocks.count("DRAFT") != 1
        ):
            raise ValueError("구형 절의 DRAFT/6블록 경계가 모호함: " + path.name)
        body = draft(text)
        saved = local(dest, "migration/original-sections/" + path.name)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
        new = (
            text
            if START in text
            else re.sub(
                r"(^##\s+DRAFT[^\n]*\n)(.*?)(?=^##\s+(?:STATUS|INPUT|RESEARCH|FACTS|OPEN)\b|\Z)",
                lambda m: m[1] + START + "\n" + m[2].rstrip() + "\n" + END + "\n",
                text,
                flags=re.M | re.S,
            )
        )
        if START not in new:
            new = START + "\n" + text.rstrip() + "\n" + END + "\n"
        atomic(path, new.encode())
        preserved = body == draft(new)
        marker_extra = draft_marker_extra(new)
        if marker_extra:
            issues.append(
                dict(
                    code="legacy_marker_extra",
                    path=str(path.relative_to(dest)),
                    reason="DRAFT 마커 밖 원문은 병합·검사에 보이지 않음. 원본 보존; 대조 후 마커 안 이동 또는 제거 필요",
                    excerpt=marker_extra[:120],
                )
            )
        comparison.append(
            {
                "file": path.name,
                "body_preserved": preserved,
                "marker_extra": marker_extra is not None,
                "original_hash": inventory[str(path.relative_to(dest))],
                "preserved_path": str(saved.relative_to(dest)),
                "status": "legacy_unreviewed",
            }
        )
        if not preserved:
            raise ValueError("본문 보존 대조 실패")
        title = re.search(r"^#\s+(.+)$", text, re.M)
        ops.append(
            {
                "collection": "sections",
                "value": {
                    "id": path.stem,
                    "path": str(path.relative_to(dest)),
                    "title": title[1] if title else path.stem,
                    "order": i,
                    "status": "drafting",
                    "legacy": True,
                    "depends_on": [
                        {
                            "collection": "sources",
                            "id": "legacy:" + str(path.relative_to(dest)),
                            "revision": 1,
                        }
                    ],
                },
            }
        )
    atomic(
        local(dest, "migration/comparison.json"),
        json.dumps(comparison, ensure_ascii=False, indent=2).encode(),
    )
    for entry in inputs:
        if (
            digest(local(dest, entry["preserved_path"]).read_bytes())
            != entry["original_hash"]
        ):
            raise ValueError("원본 바이트 보존 대조 실패: " + entry["path"])
    report = dict(
        status="preserved_pending_semantic_mapping",
        inputs=inputs,
        issues=issues,
        section_count=len(comparison),
        source_count=len(inputs),
        promoted_facts=0,
        notice="원문·요약·수치 토큰·미결 항목을 보존함. 의미·단위·기간·충돌은 대조 후 정본 사실로 등록하며 기존 verified는 승격하지 않음.",
    )
    atomic(
        local(dest, "migration/inputs.json"),
        json.dumps(report, ensure_ascii=False, indent=2).encode(),
    )
    ops.append(
        {
            "collection": "sources",
            "value": dict(
                id="migration-report",
                path="migration/inputs.json",
                legacy=True,
                verification="unreviewed",
            ),
        }
    )
    ops.append(
        {
            "collection": "tasks",
            "value": dict(
                id="migration-review",
                required_fields=[],
                source_refs=[
                    dict(id="migration-report", revision=1, locator="inputs/issues")
                ],
                description="보존된 원답변·가정·증거를 먼저 읽고 사실 항목에 대조 등록. 재인터뷰나 기존 검증 승격 금지.",
            ),
        }
    )
    return _apply_locked(
        dest, {"request_id": "migration", "ops": ops}, 0,
        capability=capability,
    )


def section_order(name):
    match = re.match(r"([IVX]+)(?:-(\d+))?", name)
    return (
        (
            {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}.get(match[1], 99),
            int(match[2] or 0),
            name,
        )
        if match
        else (99, 0, name)
    )


# ---------------------------------------------------------------------------
# G006/P5 lane C — canonical source registration (SCHEMA §8/§9/§11, API §3).
#
# Two-authority outcome model (SCHEMA §11 == API §3.2): the admission phase
# (§8.3 step 1b) classifies input well-formedness only, BEFORE any storage
# read; admitted invocations are classified by the rechecked-state selector
# from persisted state — never from exception type.  ``capability`` is a
# required call parameter: the caller holds the guard and owns its release
# boundary.  ``runtime_root`` is the DEPLOYMENT's own installed base, bound
# by the caller's context bootstrap — never copied from
# ``registry.resolved_base`` and never derived from
# ``selection.byte_source.root``.


_REG_STAGING_SCHEMA = "p5-registration-staging/3"
_REG_SNAPSHOT_SCHEMA = "p5-source-snapshot/1"
_REG_CONTENT_SCHEMA = "p5-source-content/1"
_REG_RECORD_SCHEMA = "p5-source-registration/2"
_REG_VIEW_SCHEMA = "p5-source-view/2"
_REG_OPERATION = "register_source_snapshot"
_REG_VIEW_OPERATION = "publish_source_view"
_REG_SCAN_SCHEMA = "p5-scan/1"


def _reuse_mod():
    """G006/P5 lane-A reuse module: the constructed RegistryResolution, the
    SCHEMA §7.4 canonical JSON, the §4 key grammar and the §5 key-catalog
    validator.  Lazily imported; never imports gg_core."""
    import gg_reuse

    return gg_reuse


def _reg_canonical_json(value):
    """SCHEMA §7.4 canonical JSON: UTF-8, sorted keys, compact separators,
    ensure_ascii=False, no trailing whitespace."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _reg_digest(value):
    """sha256 over the §7.4 canonical serialization — request_digest,
    source_id, snapshot_sha256, view_id and key_catalog_digest all derive
    from this (distinct from the parent ``digest()`` used for
    change_digest, whose preimage is the change document itself)."""
    return hashlib.sha256(_reg_canonical_json(value).encode("utf-8")).hexdigest()


def _reg_hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _reg_rel_ok(rel_id):
    """A selection ``relative_id`` is a root-relative POSIX id as scanned —
    never absolute, never escaping, no empty/dot segments."""
    if not isinstance(rel_id, str) or not rel_id:
        return False
    if rel_id.startswith("/") or "\x00" in rel_id or "\\" in rel_id:
        return False
    parts = rel_id.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def _reg_norm_root(value):
    """The SCHEMA §2 comparison form: normpath + absolute, exactly as
    load_registry binds ``resolved_base``."""
    return Path(os.path.normpath(str(Path(value).absolute())))


def _reg_validate_audit_key(obj, field):
    """The §4 structured audit_key inside selection.p4_trust — P5-domain
    shape, validated through the lane-A key_ref validator under the
    statistical_observation kind."""
    if not isinstance(obj, dict):
        raise ValueError("%s must be an object" % field)
    _reuse_mod()._validate_key_ref(obj, "statistical_observation", field)


def _reg_validate_selection(selection, key_catalog):
    """SPEC §2.3/§4.2 selection grammar in full — admission check 5.
    Shape checks only; raises ValueError on any violation."""
    reuse = _reuse_mod()
    if not isinstance(selection, dict):
        raise ValueError("selection must be an object")
    allowed = {
        "kind", "source_hash", "scan_id", "byte_source", "files",
        "registry_digest", "requested_use", "excerpt",
        "external_identity", "p4_trust",
    }
    extra = set(selection) - allowed
    if extra:
        raise ValueError("selection carries unknown fields: %s"
                         % sorted(extra))
    kind = selection.get("kind")
    if kind not in ("external_document", "p4_snapshot"):
        raise ValueError(
            "selection.kind must be external_document|p4_snapshot")
    if not _reg_hex64(selection.get("source_hash")):
        raise ValueError("selection.source_hash must be lowercase 64-hex")
    if "scan_id" not in selection:
        raise ValueError("selection.scan_id required (null allowed)")
    scan_id = selection["scan_id"]
    if scan_id is not None and not _reg_hex64(scan_id):
        raise ValueError("selection.scan_id must be 64-hex or null")
    bs = selection.get("byte_source")
    if not isinstance(bs, dict):
        raise ValueError("selection.byte_source must be an object")
    if set(bs) - {"kind", "root", "scan_policy_digest"}:
        raise ValueError("selection.byte_source carries unknown fields")
    bsk = bs.get("kind")
    if bsk not in ("scanned_external", "installed_runtime"):
        raise ValueError(
            "selection.byte_source.kind must be "
            "scanned_external|installed_runtime")
    root_v = bs.get("root")
    spd = bs.get("scan_policy_digest")
    # §7.4 null-pairing: scan_id is null iff scan_policy_digest is null.
    if (scan_id is None) != (spd is None):
        raise ValueError(
            "selection scan_id/scan_policy_digest null-pairing violated")
    if spd is not None and not _reg_hex64(spd):
        raise ValueError(
            "selection.byte_source.scan_policy_digest must be 64-hex "
            "or null")
    if bsk == "scanned_external":
        if not isinstance(root_v, str) or not os.path.isabs(root_v):
            raise ValueError(
                "selection.byte_source.root must be a non-null absolute "
                "path for scanned_external")
        if scan_id is None:
            raise ValueError(
                "scanned_external requires a non-null scan_id")
    else:
        if root_v is not None:
            raise ValueError(
                "selection.byte_source.root must be null for "
                "installed_runtime")
        if scan_id is not None:
            raise ValueError(
                "installed_runtime requires scan_id null")
    files = selection.get("files")
    if not isinstance(files, list):
        raise ValueError("selection.files must be a list")
    seen_rel = set()
    for i, f in enumerate(files):
        ff = "selection.files[%d]" % i
        if not isinstance(f, dict):
            raise ValueError("%s must be an object" % ff)
        if set(f) - {"relative_id", "content_sha256", "bytes"}:
            raise ValueError("%s carries unknown fields" % ff)
        rid = f.get("relative_id")
        if not _reg_rel_ok(rid):
            raise ValueError("%s.relative_id is not a root-relative id"
                             % ff)
        if rid in seen_rel:
            raise ValueError("%s.relative_id duplicated" % ff)
        seen_rel.add(rid)
        if not _reg_hex64(f.get("content_sha256")):
            raise ValueError("%s.content_sha256 must be 64-hex" % ff)
        if (not isinstance(f.get("bytes"), int)
                or isinstance(f["bytes"], bool) or f["bytes"] < 0):
            raise ValueError("%s.bytes must be an int >= 0" % ff)
    if not _reg_hex64(selection.get("registry_digest")):
        raise ValueError(
            "selection.registry_digest must be lowercase 64-hex")
    # SPEC §2.3 requested_use grammar via the lane-A validator — it also
    # validates the key_catalog shape (§5); both failures map to
    # selection_invalid here.
    kind_keys = reuse.validate_requested_use(
        selection.get("requested_use"), key_catalog=key_catalog)
    exc = selection.get("excerpt")
    if exc is not None:
        if not isinstance(exc, dict) \
                or set(exc) - {"relative_id", "start_line", "end_line"}:
            raise ValueError("selection.excerpt shape invalid")
        if not _reg_rel_ok(exc.get("relative_id")):
            raise ValueError("selection.excerpt.relative_id invalid")
        sl, el = exc.get("start_line"), exc.get("end_line")
        if (not isinstance(sl, int) or isinstance(sl, bool) or sl < 1
                or not isinstance(el, int) or isinstance(el, bool)
                or el < sl):
            raise ValueError("selection.excerpt line bounds invalid")
    ei = selection.get("external_identity")
    if not isinstance(ei, dict) or set(ei) != {
            "original_basename", "original_sha256", "observed_utc",
            "role", "class"}:
        raise ValueError(
            "selection.external_identity must carry the five §4.2 fields")
    if not isinstance(ei["original_basename"], str) \
            or not ei["original_basename"]:
        raise ValueError("external_identity.original_basename required")
    if not _reg_hex64(ei["original_sha256"]):
        raise ValueError("external_identity.original_sha256 must be 64-hex")
    for f in ("observed_utc", "role", "class"):
        if not isinstance(ei[f], str) or not ei[f]:
            raise ValueError("external_identity.%s must be a non-empty "
                             "string" % f)
    trust = selection.get("p4_trust")
    if kind == "p4_snapshot":
        if not isinstance(trust, dict) or set(trust) != {
                "pack_id", "pack_revision", "audit_key",
                "catalog_authority", "catalog_digest",
                "observation_receipt_sha256"}:
            raise ValueError(
                "p4_snapshot requires the full §4.2 p4_trust block")
        if not isinstance(trust["pack_id"], str) \
                or not trust["pack_id"]:
            raise ValueError("p4_trust.pack_id must be a non-empty str")
        if not isinstance(trust["pack_revision"], str) \
                or not trust["pack_revision"]:
            raise ValueError("p4_trust.pack_revision must be a str")
        _reg_validate_audit_key(trust["audit_key"], "p4_trust.audit_key")
        if not isinstance(trust["catalog_authority"], str) \
                or not trust["catalog_authority"]:
            raise ValueError("p4_trust.catalog_authority required")
        if not _reg_hex64(trust["catalog_digest"]):
            raise ValueError("p4_trust.catalog_digest must be 64-hex")
        if not _reg_hex64(trust["observation_receipt_sha256"]):
            raise ValueError(
                "p4_trust.observation_receipt_sha256 must be 64-hex")
    elif trust is not None:
        raise ValueError("p4_trust is bound only to kind == p4_snapshot")
    return kind_keys


def _reg_derive(selection, request_id):
    """All registration identities are computable at first attempt —
    request_digest, source_id, snapshot_sha256, view_id and the §8.4
    change document are pure functions of the admitted selection plus
    request_id."""
    files_sorted = sorted(
        selection["files"], key=lambda f: f["relative_id"])
    request_digest = _reg_digest(selection)
    source_id = "p5src-" + _reg_digest({
        "kind": selection["kind"],
        "use_kind": selection["requested_use"]["kind"],
        "files": [{"relative_id": f["relative_id"],
                   "content_sha256": f["content_sha256"]}
                  for f in files_sorted],
    })[:24]
    snapshot_dir = "sources/snapshots/%s" % source_id
    snapshot_sha256 = _reg_digest({
        "schema": _REG_SNAPSHOT_SCHEMA,
        "files": [{"relative_id": f["relative_id"],
                   "content_sha256": f["content_sha256"],
                   "bytes": f["bytes"]} for f in files_sorted],
    })
    view_id = _reg_digest({
        "scan_id": selection["scan_id"],
        "request_digest": request_digest,
        "request_id": request_id,
        "registry_digest": selection["registry_digest"],
        "source_ids": [source_id],
    })
    view_path = "sources/generated/%s/source-index.json" % view_id
    change_doc = {
        "op": "register_source_snapshot",
        "request_id": request_id,
        "request_digest": request_digest,
        "source_ids": [source_id],
        "snapshot_dir": snapshot_dir,
        "snapshot_sha256": snapshot_sha256,
        "view_id": view_id,
        "view_path": view_path,
    }
    return {
        "request_digest": request_digest,
        "request_id_hash": hashlib.sha256(
            request_id.encode("utf-8")).hexdigest(),
        "source_id": source_id,
        "files_sorted": files_sorted,
        "snapshot_dir": snapshot_dir,
        "snapshot_sha256": snapshot_sha256,
        "view_id": view_id,
        "view_path": view_path,
        "change_doc": change_doc,
        "change_digest": digest(change_doc),  # parent's own digest (§8.4)
    }


def _reg_read_json(path):
    """Read a durable record; returns (document, error).  A missing file
    is (None, None) — absence is a state, not an error; unreadable or
    unparseable content is (None, error) — canonical state unverifiable."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as error:
        return None, error
    try:
        return json.loads(raw.decode("utf-8")), None
    except (ValueError, UnicodeDecodeError) as error:
        return None, error


def _reg_write_staging(root, staging_rel, record):
    """§8.2.1 mutable operational state: atomic replace of the whole
    staging record (create when absent is the caller's job via the
    no-replace primitive)."""
    atomic(local(root, staging_rel),
           _reg_canonical_json(record).encode("utf-8"))


def _reg_view_payload(registration, content):
    """The deterministic SCHEMA §9.3 p5-source-view/2 payload, rebuilt
    from the durable per-registration record plus the content record —
    §9.4: no live registry, catalog or filesystem input."""
    eval_map = registration["coverage_evaluation"]
    statuses = set(eval_map.values())
    if statuses == {"verified"}:
        coverage_status = "verified"
    elif "verified" not in statuses:
        coverage_status = "unverified"
    else:
        coverage_status = "partial"
    return {
        "schema": _REG_VIEW_SCHEMA,
        "view_id": registration["view_id"],
        "request_id": registration["request_id"],
        "scan_id": registration["provenance"]["scan_id"],
        "request_digest": registration["request_digest"],
        "registry_digest": registration["registry_digest"],
        "scan_policy_digest": registration["scan_policy_digest"],
        "sources": [{
            "source_id": content["source_id"],
            "kind": content["kind"],
            "files": content["files"],
            "requested_use": registration["requested_use"],
            "coverage_status": coverage_status,
        }],
    }


def _reg_view_verify(root, registration):
    """§11 publication verification: the planned view path must exist and
    byte-verify against the deterministic §9.3 payload.  Returns
    "verified" | "missing" | "unverifiable"."""
    content, error = _reg_read_json(
        local(root, "sources/registered/%s.json"
              % registration["source_id"]))
    if error is not None or content is None:
        return "unverifiable"
    try:
        expected = _reg_canonical_json(
            _reg_view_payload(registration, content)).encode("utf-8")
    except (KeyError, TypeError, ValueError):
        return "unverifiable"
    try:
        observed = local(root, registration["view_path"]).read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unverifiable"
    return "verified" if observed == expected else "unverifiable"


def _reg_entry_for(p, request_id):
    requests = p.get("requests")
    if not isinstance(requests, dict):
        return None
    entry = requests.get(request_id)
    return entry if isinstance(entry, dict) else None


def _reg_source_id_claims(registry, use_kind):
    """The registry coverage claim per canonical key — the weakest claim
    across every entry claiming the key (the registry never self-declares
    verification; a key no entry claims is 'unverified')."""
    rank = _reuse_mod()._AUTHORITY_RANK
    claims = {}
    for entry in registry.document.get("entries") or []:
        cov = (entry.get("coverage") or {}).get(use_kind) or {}
        for claim in cov.get("keys") or []:
            ser = _reuse_mod().canonical_key(claim["key_ref"], use_kind)
            cur = claims.get(ser)
            if cur is None or rank[claim["authority"]] > rank[cur]:
                claims[ser] = claim["authority"]
    return claims


def _reg_live_trust_ok(selection, key_ref, catalog, indexes):
    """SCHEMA §6.4 live-trust quadruple for a statistical_observation key
    at commit: the bound catalog's live .accepted, its CURRENT digest vs
    the selection's p4_trust.catalog_digest, verified-index membership
    and the catalog entry's verified_observation receipt."""
    reuse = _reuse_mod()
    import gg_rda_research as research
    import gg_rda_provenance as prov
    if catalog is None or not isinstance(catalog, research.AcceptedCatalog):
        return False
    try:
        if catalog.accepted is not True:
            return False
        live_digest = catalog.digest
    except Exception:
        return False
    if live_digest != selection["p4_trust"]["catalog_digest"]:
        return False
    try:
        norm = prov.normalize_audit_key(
            {f: key_ref[f] for f in (
                "pack_id", "records_file_sha256",
                "physical_jsonl_line_1based", "raw_line_sha256")})
    except Exception:
        return False
    index = (indexes or {}).get(norm[0]) or {}
    if norm not in index:
        return False
    try:
        cat_entry = None
        for ser_key, entry in catalog.items():
            if prov.normalize_audit_key(ser_key) == norm:
                cat_entry = entry
                break
    except Exception:
        return False
    if not isinstance(cat_entry, dict) \
            or cat_entry.get("catalog_status") != "verified_observation":
        return False
    try:
        return bool(research._receipt_ok(cat_entry))
    except Exception:
        return False


def _reg_coverage_evaluation(registry, use_kind, keys, key_catalog,
                             selection, catalog, indexes):
    """§9.3.1 — per-key evaluation = min(registry claim, key_catalog
    authority); statistical keys are downgraded to 'unverified' when the
    §6.4 live-trust conditions did not hold at commit."""
    reuse = _reuse_mod()
    rank = reuse._AUTHORITY_RANK
    claims = _reg_source_id_claims(registry, use_kind)
    evaluation = {}
    for key_ref in keys:
        ser = reuse.canonical_key(key_ref, use_kind)
        reg_claim = claims.get(ser, "unverified")
        cat_claim = (key_catalog.get(use_kind) or {}).get(ser)
        cat_auth = cat_claim["authority"] if isinstance(
            cat_claim, dict) else "unverified"
        effective = max((reg_claim, cat_auth), key=lambda a: rank[a])
        if use_kind == "statistical_observation" \
                and effective == "verified" \
                and not _reg_live_trust_ok(
                    selection, key_ref, catalog, indexes):
            effective = "unverified"
        evaluation[ser] = effective
    return evaluation


def _reg_verify_live_bytes(root, selection, files_sorted, registry,
                           runtime_root):
    """§8.3 step 3 — re-verify every selection.files[] pair against the
    LIVE byte source under the guard: the external root for
    scanned_external, registry.resolved_base for installed_runtime.
    Returns the verified {relative_id: bytes} map for publication."""
    bsk = selection["byte_source"]["kind"]
    base = (Path(selection["byte_source"]["root"]) if bsk ==
            "scanned_external" else Path(registry.resolved_base))
    import stat
    blobs = {}
    for f in files_sorted:
        rel = f["relative_id"]
        path = base / rel
        try:
            st = os.lstat(str(path))
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                raise OSError("not a regular file")
            data = path.read_bytes()
        except OSError as error:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "selection_bytes_mismatch",
                      detail="live bytes unavailable: %s" % rel) from error
        if len(data) != f["bytes"] \
                or hashlib.sha256(data).hexdigest() != f["content_sha256"]:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "selection_bytes_mismatch",
                      detail="live bytes diverge: %s" % rel)
        blobs[rel] = data
    return blobs


def _reg_publish_snapshot(root, selection, plan, blobs):
    """SCHEMA §9.1 — the copied bytes at selection relative_id subpaths
    plus manifest.json, all through the no-replace primitive; identical
    content is a no-op, foreign bytes are a conflict."""
    fs = _fs_mod()
    snap_dir = local(root, plan["snapshot_dir"])
    try:
        os.makedirs(str(snap_dir), exist_ok=True)
    except OSError as error:
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "snapshot_conflict",
                  detail="snapshot dir unavailable: %s" % error) from error
    preserved = []
    for f in plan["files_sorted"]:
        rel = f["relative_id"]
        dest = snap_dir / rel
        try:
            os.makedirs(str(dest.parent), exist_ok=True)
            result = fs.publish_bytes_noreplace(
                dest, blobs[rel],
                parent_identity=fs.identity(dest.parent, kind="directory"),
                existing_sha256=f["content_sha256"])
        except (OSError, fs.FsError) as error:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "snapshot_conflict",
                      detail="snapshot publish failed: %s" % rel,
                      preserved_paths=preserved) from error
        preserved.append(str(dest))
        if result["state"] != "installed":
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "snapshot_conflict",
                      detail="snapshot publish failed (%s): %s"
                             % (result.get("reason"), rel),
                      preserved_paths=preserved)
    manifest = {
        "schema": _REG_SNAPSHOT_SCHEMA,
        "source_id": plan["source_id"],
        "files": [{"relative_id": f["relative_id"],
                   "content_sha256": f["content_sha256"],
                   "bytes": f["bytes"],
                   "snapshot_relpath": f["relative_id"]}
                  for f in plan["files_sorted"]],
        "snapshot_sha256": plan["snapshot_sha256"],
    }
    data = _reg_canonical_json(manifest).encode("utf-8")
    mpath = snap_dir / "manifest.json"
    try:
        result = fs.publish_bytes_noreplace(
            mpath, data,
            parent_identity=fs.identity(mpath.parent, kind="directory"),
            existing_sha256=hashlib.sha256(data).hexdigest())
    except (OSError, fs.FsError) as error:
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "snapshot_conflict",
                  detail="manifest publish failed: %s" % error,
                  preserved_paths=preserved) from error
    if result["state"] != "installed":
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "snapshot_conflict",
                  detail="manifest publish failed: %s"
                         % result.get("reason"),
                  preserved_paths=preserved)


def _reg_snapshot_present(root, plan):
    """Byte-verify the planned snapshot dir equals the staged
    snapshot_sha256 content (§8.2.2 'jump to published' probe)."""
    manifest_path = local(
        root, plan["snapshot_dir"] + "/manifest.json")
    manifest, error = _reg_read_json(manifest_path)
    if error is not None or not isinstance(manifest, dict):
        return False
    if manifest.get("schema") != _REG_SNAPSHOT_SCHEMA \
            or manifest.get("source_id") != plan["source_id"] \
            or manifest.get("snapshot_sha256") \
            != plan["snapshot_sha256"]:
        return False
    listed = manifest.get("files")
    if not isinstance(listed, list):
        return False
    want = [{"relative_id": f["relative_id"],
             "content_sha256": f["content_sha256"],
             "bytes": f["bytes"],
             "snapshot_relpath": f["relative_id"]}
            for f in plan["files_sorted"]]
    if listed != want:
        return False
    snap_dir = local(root, plan["snapshot_dir"])
    for f in plan["files_sorted"]:
        dest = snap_dir / f["relative_id"]
        try:
            if hashlib.sha256(dest.read_bytes()).hexdigest() \
                    != f["content_sha256"]:
                return False
        except OSError:
            return False
    return True


def _reg_content_record(selection, plan):
    """The §9.2 immutable content record (create-once, no-replace)."""
    return {
        "schema": _REG_CONTENT_SCHEMA,
        "source_id": plan["source_id"],
        "kind": selection["kind"],
        "use_kind": selection["requested_use"]["kind"],
        "files": [{"relative_id": f["relative_id"],
                   "content_sha256": f["content_sha256"],
                   "bytes": f["bytes"],
                   "snapshot_relpath": f["relative_id"]}
                  for f in plan["files_sorted"]],
        "snapshot_dir": plan["snapshot_dir"],
        "snapshot_sha256": plan["snapshot_sha256"],
    }


def _reg_change_doc_of_record(registration, content):
    """Rebuild the §8.4 change document from durable records — the
    ledger hash binding for a committed registration."""
    return {
        "op": "register_source_snapshot",
        "request_id": registration["request_id"],
        "request_digest": registration["request_digest"],
        "source_ids": [registration["source_id"]],
        "snapshot_dir": content["snapshot_dir"],
        "snapshot_sha256": content["snapshot_sha256"],
        "view_id": registration["view_id"],
        "view_path": registration["view_path"],
    }


def publish_source_view(root, registration, *, capability):
    """Publishes sources/generated/<view_id>/source-index.json (SCHEMA §9.3
    payload, p5-source-view/2) AFTER a successful canonical registration.

    ``registration`` is the §9.2 per-registration record; the view bytes
    are rebuilt from it plus the content record alone (§9.4 durable
    reconstruction).  The ledger entry for ``request_id`` is rechecked
    against the rebuilt §8.4 change digest — a committed registration is
    required before any publication.  An existing path with different
    bytes is a conflict classified per §3.2 (pre-commit
    ``not_committed / view_conflict``; committed
    ``indeterminate / committed_publication_*``).  The same request's
    retry reuses its one path after byte-equality verification; a
    generated file never contains its own hash."""
    root = Path(root)
    _lock_mod().assert_held(capability, root)
    if not isinstance(registration, dict) \
            or registration.get("schema") != _REG_RECORD_SCHEMA:
        raise ValueError(
            "registration must be a p5-source-registration/2 record")
    for f in ("request_id", "request_id_hash", "request_digest",
              "registry_digest", "scan_policy_digest", "source_id",
              "requested_use", "coverage_evaluation", "provenance",
              "view_id", "view_path"):
        if f not in registration:
            raise ValueError("registration record missing %r" % f)
    if hashlib.sha256(registration["request_id"].encode("utf-8")
                      ).hexdigest() != registration["request_id_hash"]:
        raise ValueError("registration.request_id_hash inconsistent")

    content_rel = "sources/registered/%s.json" % registration["source_id"]
    content, error = _reg_read_json(local(root, content_rel))
    if error is not None or not isinstance(content, dict) \
            or content.get("schema") != _REG_CONTENT_SCHEMA:
        # The durable content record the view derives from is absent or
        # unverifiable — canonical state cannot be verified.
        raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="content record %s unreadable or absent"
                         % content_rel)
    record_rel = ("sources/registered/requests/%s.json"
                  % registration["request_id_hash"])
    persisted, error = _reg_read_json(local(root, record_rel))
    if error is not None or persisted != registration:
        raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="registration record %s does not match durable "
                         "bytes" % record_rel)
    expected_view = _reg_digest({
        "scan_id": registration["provenance"]["scan_id"],
        "request_digest": registration["request_digest"],
        "request_id": registration["request_id"],
        "registry_digest": registration["registry_digest"],
        "source_ids": [registration["source_id"]],
    })
    if expected_view != registration["view_id"]:
        raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="registration.view_id does not recompute")
    payload = _reg_view_payload(registration, content)
    data = _reg_canonical_json(payload).encode("utf-8")
    want_sha = hashlib.sha256(data).hexdigest()

    # Rechecked ledger state — never exception type: the committed entry
    # for this request_id must verify against the rebuilt §8.4 change
    # digest before any view publication.
    try:
        p = load(root)
    except (OSError, ValueError, KeyError) as error:
        raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="canonical state unreadable") from error
    entry = _reg_entry_for(p, registration["request_id"])
    committed = entry is not None and entry.get("hash") == digest(
        _reg_change_doc_of_record(registration, content))
    if not committed:
        # §11 rows 2e/2f — classified from the rechecked artifact path,
        # never from exception type: with NO committed entry, foreign
        # bytes already holding the view path are `view_conflict`;
        # otherwise the registration's snapshot may be preserved but the
        # canonical entry is verified absent — `published_unregistered`.
        try:
            observed = local(
                root, registration["view_path"]).read_bytes()
        except OSError:
            observed = None
        if observed is not None and observed != data:
            raise _op(_REG_VIEW_OPERATION, "canonical", "not_committed",
                      "view_conflict",
                      detail="view path %s held different bytes before "
                             "publish" % registration["view_path"],
                      request_id=registration["request_id"],
                      preserved_paths=[registration["view_path"]])
        raise _op(_REG_VIEW_OPERATION, "canonical", "not_committed",
                  "published_unregistered",
                  detail="no committed ledger entry for request_id %r"
                         % registration["request_id"],
                  request_id=registration["request_id"],
                  preserved_paths=[record_rel])

    fs = _fs_mod()
    view_path = local(root, registration["view_path"])
    try:
        os.makedirs(str(view_path.parent), exist_ok=True)
        result = fs.publish_bytes_noreplace(
            view_path, data,
            parent_identity=fs.identity(view_path.parent, kind="directory"),
            existing_sha256=want_sha)
    except (OSError, fs.FsError) as error:
        result = {"state": "not_installed", "reason": str(error)}
    if result["state"] == "installed":
        return {
            "operation": _REG_VIEW_OPERATION,
            "target": registration["view_path"],
            "commit_state": "committed",
            "view_id": registration["view_id"],
            "view_path": registration["view_path"],
            "state": result.get("reason") or "published",
            "sha256": want_sha,
            "cleanup_errors": result.get("cleanup_errors") or [],
        }
    # Recheck the artifact path after the failed publish — the observed
    # bytes decide the row, never the publish exception itself.
    try:
        observed = view_path.read_bytes()
    except OSError:
        observed = None
    if observed == data:
        raise _op(_REG_VIEW_OPERATION, "canonical",
                  "committed_cleanup_pending", "post_publish_verify_failed",
                  detail="publish reported failure but the view path "
                         "byte-verifies",
                  request_id=registration["request_id"],
                  preserved_paths=result.get("preserved_paths"))
    if observed is None:
        raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
                  "committed_publication_missing",
                  detail="view absent at %s after publish attempt"
                         % registration["view_path"],
                  request_id=registration["request_id"],
                  preserved_paths=result.get("preserved_paths"))
    raise _op(_REG_VIEW_OPERATION, "canonical", "indeterminate",
              "committed_publication_unverified",
              detail="view at %s holds divergent bytes"
                     % registration["view_path"],
              request_id=registration["request_id"],
              preserved_paths=result.get("preserved_paths"))


def _reg_persisted_digest(staging, regrec):
    """§11 row 1 operand: the staging record's request_digest, else the
    per-registration record's when staging is absent."""
    if staging is not None:
        return staging.get("request_digest")
    if regrec is not None:
        return regrec.get("request_digest")
    return None


def register_source_snapshot(root, selection, expected_revision, *,
                             capability, request_id, registry,
                             runtime_root, key_catalog,
                             catalog=None, indexes=None):
    """Canonical source registration (API §3.1, SCHEMA §8–§11).

    ``registry`` is REQUIRED — the constructed ``RegistryResolution``
    produced ONLY by ``load_registry`` (a bare dict/digest fails
    admission check 2).  ``runtime_root`` is REQUIRED — the DEPLOYMENT's
    own installed artifact base, bound by the caller's context bootstrap
    independently of the registry load; check 9 compares it to
    ``registry.resolved_base`` for BOTH ``byte_source.kind`` values and
    ``selection.byte_source.root`` is never the operand.  ``key_catalog``
    is a REQUIRED keyword parameter with NO default — omission is a
    binding ``TypeError``; ``None``/malformed is rejected at admission
    BEFORE any storage read or committed-duplicate return.
    ``catalog``/``indexes`` are the constructed objects from
    ``load_accepted_catalog`` — required iff ``selection.kind ==
    "p4_snapshot"``; a plain dict is never a trust source.

    Call order (SCHEMA §8.3): assert held guard → admission checks 1–9 in
    listed order (first failure wins, §11 row 0) → incoming-identity check
    (row 1) → §8.2.2 reconciliation → for a committed matching request,
    publication verification then the CURRENTLY LOADED projection; for a
    new/uncommitted request, CAS → live-byte verification → snapshot →
    commit via _apply_locked → §9.2 records → view.  Raises
    ``OperationError`` on any non-committed outcome."""
    root = Path(root)
    _lock_mod().assert_held(capability, root)
    reuse = _reuse_mod()

    def reject(reason, detail=None):
        raise _op(_REG_OPERATION, "canonical", "not_committed", reason,
                  detail=detail, request_id=request_id
                  if isinstance(request_id, str) else None)

    # ---- Phase A — admission (SCHEMA §8.3 step 1b).  Input well-formedness
    # only, BEFORE ANY storage read; the §8.3 table's listed order binds
    # and the FIRST failed check supplies the reason (V11-2).
    # 1 — request_id present.
    if not isinstance(request_id, str) or not request_id:
        reject("request_id_required")
    # 2 — registry is the constructed RegistryResolution.
    if not isinstance(registry, reuse.RegistryResolution):
        reject("adoption_context_invalid")
    # 3 — runtime_root present.
    if not isinstance(runtime_root, (str, os.PathLike)):
        reject("adoption_context_invalid")
    # 4 — key_catalog present and non-None.
    if key_catalog is None:
        reject("selection_invalid")
    # 5 — selection schema-valid (SPEC §2.3/§4.2 grammar in full,
    #     including non-empty requested_use.keys, the §7.4
    #     scan_id/scan_policy_digest null-pairing and the byte_source
    #     block discipline).
    try:
        use_kind, norm_keys = _reg_validate_selection(
            selection, key_catalog)
    except ValueError as error:
        reject("selection_invalid", detail=str(error))
    # 6 — key_catalog SCHEMA §5-valid.
    try:
        reuse._validate_key_catalog(key_catalog)
    except ValueError as error:
        reject("selection_invalid", detail=str(error))
    # 7 — constructed catalog/indexes present iff kind == p4_snapshot;
    #     for other kinds a supplied catalog/indexes must still be the
    #     constructed objects, never a plain dict.
    import gg_rda_research as research
    if selection["kind"] == "p4_snapshot":
        if not isinstance(catalog, research.AcceptedCatalog) \
                or not isinstance(indexes, dict):
            reject("adoption_context_invalid")
    else:
        if catalog is not None and not isinstance(
                catalog, research.AcceptedCatalog):
            reject("adoption_context_invalid")
        if indexes is not None and not isinstance(indexes, dict):
            reject("adoption_context_invalid")
    # 8 — selection.registry_digest == registry.registry_digest.
    if selection["registry_digest"] != registry.registry_digest:
        reject("selection_invalid",
               detail="selection.registry_digest != "
                      "registry.registry_digest")
    # 9 — runtime_root == registry.resolved_base (the registry FILE's own
    #     bound base; selection.byte_source.root is never the operand).
    if _reg_norm_root(runtime_root) != Path(registry.resolved_base):
        reject("selection_invalid",
               detail="runtime_root != registry.resolved_base")

    # ---- Phase B — storage-consulting classification (admitted only).
    plan = _reg_derive(selection, request_id)
    incoming_digest = plan["request_digest"]
    request_id_hash = plan["request_id_hash"]
    staging_rel = ("sources/intake/request-%s.staging.json"
                   % request_id_hash)
    regrec_rel = ("sources/registered/requests/%s.json"
                  % request_id_hash)
    content_rel = ("sources/registered/%s.json" % plan["source_id"])

    staging, staging_err = _reg_read_json(local(root, staging_rel))
    regrec, regrec_err = _reg_read_json(local(root, regrec_rel))
    content, content_err = _reg_read_json(local(root, content_rel))
    try:
        p = load(root)
    except (OSError, ValueError, KeyError) as error:
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate", detail="canonical state unreadable",
                  request_id=request_id) from error
    if staging_err is not None or regrec_err is not None \
            or content_err is not None:
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="durable registration state unreadable",
                  request_id=request_id)
    if staging is not None and not isinstance(staging, dict):
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate", detail="staging record malformed",
                  request_id=request_id)
    if regrec is not None and not isinstance(regrec, dict):
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate", detail="registration record malformed",
                  request_id=request_id)

    # §11 row 1 — FIRST among storage-consulting classifications: a
    # persisted request_digest for this request_id that differs from the
    # incoming selection digest rejects the invocation over ANY stored
    # state (a verified historical commit is preserved evidence).
    persisted_digest = _reg_persisted_digest(staging, regrec)
    if persisted_digest is not None \
            and persisted_digest != incoming_digest:
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "request_id_conflict", request_id=request_id,
                  detail="request_id is bound to a different selection")
    if staging is not None and regrec is not None \
            and regrec.get("request_digest") \
            != staging.get("request_digest"):
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="staging/registration digests diverge",
                  request_id=request_id)

    entry = _reg_entry_for(p, request_id)
    committed = entry is not None \
        and entry.get("hash") == plan["change_digest"]
    if entry is not None and not committed:
        if staging is None and regrec is None:
            # A committed ledger entry with no durable registration
            # record is a storage contradiction (§8.2.2 'none|yes').
            raise _op(_REG_OPERATION, "canonical", "indeterminate",
                      "indeterminate",
                      detail="committed ledger entry lacks a durable "
                             "registration record",
                      request_id=request_id)
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "request_id_conflict", request_id=request_id,
                  detail="ledger entry binds a different change")
    if committed and regrec is None:
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="committed ledger entry lacks a durable "
                         "registration record",
                  request_id=request_id)
    if not committed and staging is not None \
            and staging.get("state") == "committed":
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="staging claims committed but the ledger does "
                         "not", request_id=request_id)
    if not committed and regrec is not None:
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="registration record present without a "
                         "committed ledger entry",
                  request_id=request_id)

    def staging_marked_committed(resulting_revision):
        record = dict(staging) if staging else {
            "schema": _REG_STAGING_SCHEMA,
            "request_id": request_id,
            "request_id_hash": request_id_hash,
            "request_digest": incoming_digest,
            "change_digest": plan["change_digest"],
            "first_attempt_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "planned_snapshot_dir": plan["snapshot_dir"],
            "planned_view_id": plan["view_id"],
            "planned_view_path": plan["view_path"],
            "source_ids": [plan["source_id"]],
            "snapshot_sha256": plan["snapshot_sha256"],
        }
        record["state"] = "committed"
        record["resulting_revision"] = resulting_revision
        record["committed_utc"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return record

    if committed:
        # §8.3 step 2 — the committed duplicate: (a) incoming identity
        # already verified at row 1; (b) publication verification BEFORE
        # returning; (c) the CURRENTLY LOADED projection only.
        state = _reg_view_verify(root, regrec)
        if state == "missing":
            raise _op(_REG_OPERATION, "canonical", "indeterminate",
                      "committed_publication_missing",
                      request_id=request_id,
                      detail="committed registration's view is absent")
        if state != "verified":
            raise _op(_REG_OPERATION, "canonical", "indeterminate",
                      "committed_publication_unverified",
                      request_id=request_id,
                      detail="committed registration's view diverges")
        if staging is None or staging.get("state") != "committed" \
                or staging.get("resulting_revision") != entry["revision"]:
            # §8.2.2 'none|yes' / 'staged|yes' reconciliation — durable
            # retry identity filled from durable records.
            try:
                _reg_write_staging(
                    root, staging_rel,
                    staging_marked_committed(entry["revision"]))
            except OSError as error:
                raise _op(_REG_OPERATION, "canonical",
                          "committed_cleanup_pending",
                          "staged_release_failed",
                          detail=str(error), request_id=request_id,
                          revision=entry["revision"]) from error
        return p

    # ---- §8.2.2 reconciliation precedes any new-request work: create
    # the staging record for a fresh attempt, verify its identity, and
    # jump to `published` when the planned snapshot already byte-verifies
    # (a preserved snapshot is never re-copied).
    state = staging.get("state") if staging else None
    if staging is None:
        staging = {
            "schema": _REG_STAGING_SCHEMA,
            "request_id": request_id,
            "request_id_hash": request_id_hash,
            "request_digest": incoming_digest,
            "change_digest": plan["change_digest"],
            "first_attempt_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "state": "staged",
            "planned_snapshot_dir": plan["snapshot_dir"],
            "planned_view_id": plan["view_id"],
            "planned_view_path": plan["view_path"],
            "source_ids": [plan["source_id"]],
            "resulting_revision": None,
            "snapshot_sha256": plan["snapshot_sha256"],
            "committed_utc": None,
        }
        try:
            _reg_write_staging(root, staging_rel, staging)
        except OSError as error:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "snapshot_conflict",
                      detail="staging record could not be created: %s"
                             % error,
                      request_id=request_id) from error
        state = "staged"
    elif (staging.get("schema") != _REG_STAGING_SCHEMA
            or staging.get("request_id_hash") != request_id_hash
            or staging.get("request_digest") != incoming_digest
            or staging.get("change_digest") != plan["change_digest"]
            or staging.get("planned_view_id") != plan["view_id"]
            or staging.get("planned_view_path") != plan["view_path"]
            or staging.get("planned_snapshot_dir")
            != plan["snapshot_dir"]
            or staging.get("snapshot_sha256")
            != plan["snapshot_sha256"]):
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="staging identity diverges from the admitted "
                         "selection", request_id=request_id)
    elif state not in ("staged", "failed", "published", "committed"):
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="staging carries unknown state %r" % state,
                  request_id=request_id)

    snapshot_ok = _reg_snapshot_present(root, plan)
    if state == "published" and not snapshot_ok:
        # §8.2.2 'published | no' — the record claims published but the
        # bytes do not verify: storage contradiction.
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "indeterminate",
                  detail="staging claims published but the snapshot "
                         "does not verify", request_id=request_id)
    if state in ("staged", "failed") and snapshot_ok:
        # §8.2.2 'staged/failed | no' — the planned snapshot dir
        # byte-verified: jump to published without re-copying.
        staging["state"] = "published"
        state = "published"
        try:
            _reg_write_staging(root, staging_rel, staging)
        except OSError as error:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "snapshot_conflict",
                      detail="staging update failed: %s" % error,
                      request_id=request_id,
                      preserved_paths=[plan["snapshot_dir"]]) from error

    # ---- §8.3 step 3 — new or uncommitted request.  All consistency
    # checks already passed at admission; CAS precedes byte verification
    # and publication.
    if p["revision"] != expected_revision:
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "revision_conflict", revision=p["revision"],
                  request_id=request_id,
                  preserved_paths=(
                      [plan["snapshot_dir"]] if snapshot_ok else None))
    if not snapshot_ok:
        blobs = _reg_verify_live_bytes(
            root, selection, plan["files_sorted"], registry, runtime_root)
        try:
            _reg_publish_snapshot(root, selection, plan, blobs)
        except OperationError as error:
            landed = (error.result or {}).get("preserved_paths") or []
            if not landed:
                # §8.2.2 'staged/published | publish fails before any
                # bytes land -> failed' — mutable operational state.
                staging["state"] = "failed"
                try:
                    _reg_write_staging(root, staging_rel, staging)
                except OSError:
                    pass
            raise
        staging["state"] = "published"
        state = "published"
        try:
            _reg_write_staging(root, staging_rel, staging)
        except OSError as error:
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "snapshot_conflict",
                      detail="staging update failed: %s" % error,
                      request_id=request_id,
                      preserved_paths=[plan["snapshot_dir"]]) from error

    # §9.2 record conflict probes run BEFORE the commit so a divergent
    # durable record stays a known no-commit (row 2g), never a
    # post-commit contradiction.
    if content is not None:
        if content != _reg_content_record(selection, plan):
            raise _op(_REG_OPERATION, "canonical", "not_committed",
                      "source_id_conflict", request_id=request_id,
                      detail="content record %s holds divergent bytes"
                             % content_rel,
                      preserved_paths=[content_rel,
                                       plan["snapshot_dir"]])
    else:
        content = _reg_content_record(selection, plan)

    # §11 row 2e — `view_conflict` is PRE-COMMIT only: with no committed
    # entry (verified absent), a view path already holding different
    # bytes is a known no-commit, not a post-commit contradiction.  The
    # expected bytes are deterministic: the §9.3 payload derives from
    # identities fixed at staging time plus the §9.3.1 coverage
    # evaluation, all computable before the commit.
    evaluation = _reg_coverage_evaluation(
        registry, use_kind, norm_keys,
        key_catalog, selection, catalog, indexes)
    regrec = {
        "schema": _REG_RECORD_SCHEMA,
        "request_id": request_id,
        "request_id_hash": request_id_hash,
        "request_digest": incoming_digest,
        "registry_digest": selection["registry_digest"],
        "scan_policy_digest": selection["byte_source"]
        ["scan_policy_digest"],
        "key_catalog_digest": _reg_digest(key_catalog),
        "source_id": plan["source_id"],
        "requested_use": {
            "kind": use_kind,
            "keys": list(norm_keys),
        },
        "provenance": {
            "scan_id": selection["scan_id"],
            "external_identity": selection["external_identity"],
        },
        "p4_trust": selection.get("p4_trust"),
        "coverage_evaluation": evaluation,
        "view_id": plan["view_id"],
        "view_path": plan["view_path"],
    }
    expected_view = _reg_canonical_json(
        _reg_view_payload(regrec, content)).encode("utf-8")
    vpath = local(root, plan["view_path"])
    try:
        observed_view = vpath.read_bytes()
    except OSError:
        observed_view = None
    if observed_view is not None and observed_view != expected_view:
        raise _op(_REG_OPERATION, "canonical", "not_committed",
                  "view_conflict", request_id=request_id,
                  detail="view path %s held different bytes before "
                         "publish" % plan["view_path"],
                  preserved_paths=[plan["view_path"],
                                   plan["snapshot_dir"]])

    try:
        p = _apply_locked(root, plan["change_doc"], expected_revision,
                          capability=capability)
    except OperationError as error:
        # The ledger rejection already carries the parent's own
        # rechecked state; only the entry-point name is rewritten.
        result = dict(error.result)
        result["operation"] = _REG_OPERATION
        if not result.get("preserved_paths") and snapshot_ok:
            result["preserved_paths"] = [plan["snapshot_dir"]]
        raise OperationError(result) from error

    # §9.2 — create-once no-replace canonical records after the commit.
    fs = _fs_mod()
    preserved = [plan["snapshot_dir"]]
    if _reg_read_json(local(root, content_rel))[0] is None:
        data = _reg_canonical_json(content).encode("utf-8")
        cpath = local(root, content_rel)
        try:
            os.makedirs(str(cpath.parent), exist_ok=True)
            result = fs.publish_bytes_noreplace(
                cpath, data,
                parent_identity=fs.identity(
                    cpath.parent, kind="directory"),
                existing_sha256=hashlib.sha256(data).hexdigest())
        except (OSError, fs.FsError) as error:
            result = {"state": "not_installed", "reason": str(error)}
        if result["state"] != "installed":
            raise _op(_REG_OPERATION, "canonical", "indeterminate",
                      "committed_publication_missing",
                      detail="content record could not be persisted",
                      request_id=request_id,
                      revision=p["revision"],
                      preserved_paths=preserved
                      + (result.get("preserved_paths") or []))
    # resulting_revision is an OBSERVATION of committed state (§8.4) —
    # recorded from the returned projection, never an input to identity.
    regrec["first_registered_revision"] = p["revision"]
    data = _reg_canonical_json(regrec).encode("utf-8")
    rpath = local(root, regrec_rel)
    try:
        os.makedirs(str(rpath.parent), exist_ok=True)
        result = fs.publish_bytes_noreplace(
            rpath, data,
            parent_identity=fs.identity(rpath.parent, kind="directory"),
            existing_sha256=hashlib.sha256(data).hexdigest())
    except (OSError, fs.FsError) as error:
        result = {"state": "not_installed", "reason": str(error)}
    if result["state"] != "installed":
        raise _op(_REG_OPERATION, "canonical", "indeterminate",
                  "committed_publication_missing",
                  detail="registration record could not be persisted",
                  request_id=request_id, revision=p["revision"],
                  preserved_paths=preserved
                  + (result.get("preserved_paths") or []))

    # §9.3 — the registration-addressed view, after the commit and the
    # durable records.  Its classification is this operation's own — the
    # result object is re-stamped to the caller's entry point.
    try:
        publish_source_view(root, regrec, capability=capability)
    except OperationError as error:
        result = dict(error.result)
        result["operation"] = _REG_OPERATION
        raise OperationError(result) from error

    # Durable retry identity: the staging record marks committed.
    try:
        _reg_write_staging(
            root, staging_rel, staging_marked_committed(p["revision"]))
    except OSError as error:
        raise _op(_REG_OPERATION, "canonical",
                  "committed_cleanup_pending", "staged_release_failed",
                  detail=str(error), request_id=request_id,
                  revision=p["revision"],
                  preserved_paths=preserved) from error
    return p
