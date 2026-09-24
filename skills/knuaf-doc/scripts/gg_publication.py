"""P2 lane C — immutable publication layer (gg_publication).

Contract source: design-v1 API.md §4/§6/§7/§8/§9 and SPEC.md §8.1/§8.2.
This module depends only on ``gg_lock.assert_held`` /
``verify_recovery_lineage`` / ``rebind_after_rename`` and on the ``gg_fs``
primitives (canonical JSON bytes, strict identity, no-replace rename,
no-clobber file publish).  It never imports gg_core, never parses the
canonical request ledger, and never decides whether canonical storage
succeeded — that judgment belongs to lane A.

Every publish_* runs ``assert_held`` after pure input validation and before
any product write (mkdir/temp included).  All workspace paths are handled
with no-follow fd-relative operations; ports that cannot provide them are
refused before the first write.  Staging prepared outside the guard is
always re-inventoried after the capability check.

``managed`` for ``publish_requested_file`` is ``{"path", "sha256"}`` plus an
optional ``"publication_path"`` (bundle dir holding the managed receipt) so
the requested path is cross-checked against the paper request.
"""
import hashlib
import json
import os
import stat
import unicodedata
import uuid
from pathlib import Path

import gg_fs
import gg_lock

RECEIPT_SCHEMA = "gg-publication/1"
REF_SCHEMA = "gg-publication-ref/1"
OBSERVATION_SCHEMA = "gg-review-observation/2"
RECEIPT_NAME = ".publication.json"
IMPORT_RECEIPT_NAME = ".gg-import-publication.json"
ARTIFACT_DIR = ".gg-artifacts"
OBSERVATION_DIR = ".gg-observations"
BUILD_DIR = "build"
CONTROL_DIR = ".gg-lock"
PROTOCOL_NAME = "protocol.json"

COLLECTIONS = frozenset({
    "sources", "facts", "sections", "questions", "tasks",
    "reviews", "rules", "approvals", "outputs",
})

RECEIPT_KEYS = frozenset({
    "schema", "publication_id", "workspace_id", "protocol_sha256", "kind",
    "request", "request_id", "request_sha256", "input_revision",
    "target_refs", "input_fingerprint", "source_files", "files",
    "primary_path", "requested_path", "output_id", "output_format",
})

OBSERVATION_KEYS = frozenset({
    "schema", "observed_by", "author_session", "reviewer_session",
    "author_id", "reviewer_id", "review_kinds", "target_refs",
    "input_fingerprint", "report_path", "report_hash", "input_revision",
    "workspace_id", "protocol_sha256",
})

# API §6: exact request preimage key sets per kind; companion_files is the
# only omittable list and defaults to [].
_REQUEST_KEYS = {
    "adopt_output": frozenset(
        {"kind", "expected_revision", "output_value", "companion_files"}),
    "export": frozenset({
        "kind", "export_kind", "input_revision", "project_sha256",
        "target_refs", "input_fingerprint"}),
    "paper": frozenset({
        "kind", "input_revision", "target_refs", "input_fingerprint",
        "spec_path", "spec_sha256", "requested_path", "output_format"}),
    "import": frozenset({"kind", "source_inventory", "destination"}),
}
_REQUEST_OPTIONAL = {"adopt_output": frozenset({"companion_files"})}
_HEX = frozenset("0123456789abcdef")


class PublicationError(ValueError):
    """Failure carrying the PublicationResult shape from API §4."""

    def __init__(self, result):
        self.result = result
        self.reason = result.get("reason")
        super().__init__(self.reason or "publication failed")


def _result(state, destination=None, *, publication_ref=None,
            target_sha256=None, reason=None, preserved=(), cleanup=()):
    return {
        "state": state,
        "publication_ref": publication_ref,
        "destination": None if destination is None else str(destination),
        "target_sha256": target_sha256,
        "preserved_paths": list(preserved),
        "cleanup_errors": list(cleanup),
        "reason": reason,
    }


def _fail(destination, reason, *, preserved=(), cleanup=(),
          state="not_published"):
    raise PublicationError(_result(
        state, destination, reason=reason,
        preserved=preserved, cleanup=cleanup))


def _port_supported():
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and {os.open, os.stat, os.mkdir} <= os.supports_dir_fd)


def _require_port(destination=None, *, preserved=()):
    if not _port_supported():
        _fail(destination, "unsupported_publication_port",
              preserved=preserved)


def _check_str(value, what):
    if not isinstance(value, str) or not value:
        raise ValueError("잘못된 " + what)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(what + "에 UTF-8 불가 문자") from e
    if "\x00" in value:
        raise ValueError(what + "에 NUL")
    return value


def _check_sha256(value, what="sha256"):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in _HEX for c in value)):
        raise ValueError("잘못된 " + what)
    return value


def _check_nonneg_int(value, what):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("잘못된 " + what)
    return value


def _check_relative_path(value, what="상대 경로"):
    """API §1 RelativePath: '/'-joined, root-internal, no escape/nul/drive."""
    _check_str(value, what)
    if value.startswith("/") or value.startswith("\\") or "\\" in value:
        raise ValueError(what + "는 절대/UNC/백슬래시 경로가 아니어야 함: "
                         + value)
    for part in value.split("/"):
        if part in ("", ".", ".."):
            raise ValueError(what + "에 빈/./.. 구성요소: " + value)
        if len(part) == 2 and part[1] == ":" and part[0].isalpha():
            raise ValueError(what + "에 드라이브 경로: " + value)
    return value.split("/")


def _check_abs_destination(value):
    _check_str(value, "import 대상 경로")
    parts = value.split("/")
    if not value.startswith("/") or value == "/":
        raise ValueError("import 대상은 절대 경로여야 함: " + value)
    for part in parts[1:]:
        if part in ("", ".", "..") or "\\" in part:
            raise ValueError("import 대상이 정규화되지 않음: " + value)
    return value


def _check_target_refs(refs):
    """API §9: [{collection,id}] fixed keys, allowed collections, canonical
    utf-8 (collection,id) order, no dup/NFC collision."""
    if not isinstance(refs, list):
        raise ValueError("target_refs는 객체 배열이어야 함")
    seen = set()
    nfc_seen = {}
    prev = None
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"collection", "id"}:
            raise ValueError("잘못된 대상 참조 객체: " + repr(ref))
        collection = ref["collection"]
        rid = ref["id"]
        if collection not in COLLECTIONS:
            raise ValueError("알 수 없는 collection: " + repr(collection))
        if not isinstance(rid, str):
            raise ValueError("대상 참조 ID는 문자열이어야 함")
        try:
            rid.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError("대상 참조 ID에 UTF-8 불가 문자") from e
        if not rid.strip() or rid != rid.strip():
            raise ValueError("대상 참조 ID에 양끝 공백")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rid):
            raise ValueError("대상 참조 ID에 제어 문자")
        if (collection, rid) in seen:
            raise ValueError("중복 대상 참조: " + repr(ref))
        seen.add((collection, rid))
        nfc = unicodedata.normalize("NFC", rid)
        prior = nfc_seen.setdefault(collection, {})
        if nfc in prior:
            raise ValueError("같은 collection의 NFC 충돌 ID: " + repr(rid))
        prior[nfc] = rid
        order = (collection.encode("utf-8"), rid.encode("utf-8"))
        if prev is not None and order <= prev:
            raise ValueError("target_refs가 canonical 정렬이 아님")
        prev = order


def _check_file_entries(entries, *, require_destination, what):
    """Sorted [{path,sha256,size}(,destination)] — dup/NFC/order/escape."""
    if not isinstance(entries, list):
        raise ValueError(what + "은 정렬된 목록이어야 함")
    required = {"path", "sha256", "size"}
    if require_destination:
        required = required | {"destination"}
    seen = set()
    nfc_seen = {}
    prev = None
    for entry in entries:
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise ValueError(what + " 항목 필수 키 누락: " + repr(entry))
        if not set(entry) <= required:
            raise ValueError(what + " 항목에 알 수 없는 키: " + repr(entry))
        path = entry["path"]
        _check_relative_path(path, what + " path")
        _check_sha256(entry["sha256"], what + " sha256")
        _check_nonneg_int(entry["size"], what + " size")
        if require_destination:
            _check_relative_path(entry["destination"], what + " destination")
        if path in seen:
            raise ValueError(what + " 중복 경로: " + path)
        seen.add(path)
        nfc = unicodedata.normalize("NFC", path)
        if nfc in nfc_seen and nfc_seen[nfc] != path:
            raise ValueError(what + " NFC 경로 충돌: " + path)
        nfc_seen[nfc] = path
        order = path.encode("utf-8")
        if prev is not None and order <= prev:
            raise ValueError(what + "이 canonical 정렬이 아님: " + path)
        prev = order


def _check_companions(companions):
    if not isinstance(companions, list):
        raise ValueError("companion_files는 목록이어야 함")
    seen = set()
    prev = None
    for entry in companions:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("잘못된 companion_files 항목: " + repr(entry))
        _check_relative_path(entry["path"], "companion path")
        _check_sha256(entry["sha256"], "companion sha256")
        if entry["path"] in seen:
            raise ValueError("companion 중복 경로: " + entry["path"])
        seen.add(entry["path"])
        order = entry["path"].encode("utf-8")
        if prev is not None and order <= prev:
            raise ValueError("companion_files가 canonical 정렬이 아님")
        prev = order


def _request_sha256(request):
    try:
        data = gg_fs.canonical_json_bytes(request)
    except (TypeError, ValueError) as e:
        raise ValueError("request를 canonical bytes로 만들 수 없음") from e
    return hashlib.sha256(data).hexdigest()


def _check_request(request):
    """API §6 exact preimage validation; returns the kind."""
    if not isinstance(request, dict):
        raise ValueError("request는 객체여야 함")
    if any(not isinstance(k, str) for k in request):
        raise ValueError("request 키는 문자열만 허용")
    kind = request.get("kind")
    if kind not in _REQUEST_KEYS:
        raise ValueError("알 수 없는 request kind: " + repr(kind))
    required = _REQUEST_KEYS[kind] - _REQUEST_OPTIONAL.get(kind, frozenset())
    missing = required - set(request)
    extra = set(request) - _REQUEST_KEYS[kind]
    if missing or extra:
        raise ValueError("request 키 불일치 missing=%s extra=%s"
                         % (sorted(missing), sorted(extra)))
    if kind == "adopt_output":
        _check_nonneg_int(request["expected_revision"], "expected_revision")
        if not isinstance(request["output_value"], dict):
            raise ValueError("output_value는 객체여야 함")
        _check_companions(request.get("companion_files", []))
    elif kind == "export":
        _check_str(request["export_kind"], "export_kind")
        _check_nonneg_int(request["input_revision"], "input_revision")
        _check_sha256(request["project_sha256"], "project_sha256")
        _check_target_refs(request["target_refs"])
        _check_sha256(request["input_fingerprint"], "input_fingerprint")
    elif kind == "paper":
        _check_nonneg_int(request["input_revision"], "input_revision")
        _check_target_refs(request["target_refs"])
        _check_sha256(request["input_fingerprint"], "input_fingerprint")
        _check_relative_path(request["spec_path"], "spec_path")
        _check_sha256(request["spec_sha256"], "spec_sha256")
        _check_relative_path(request["requested_path"], "requested_path")
        if request["output_format"] != "md":
            raise ValueError("paper output_format은 md만 허용")
    elif kind == "import":
        _check_file_entries(request["source_inventory"],
                            require_destination=False,
                            what="source_inventory")
        _check_abs_destination(request["destination"])
    return kind


def _check_intent(intent):
    """API §6: C re-validates the request object and recomputes its SHA; the
    caller-supplied request_sha256/request_id are never trusted."""
    if not isinstance(intent, dict) or set(intent) != {
            "request", "request_id", "request_sha256",
            "input_revision", "target_refs", "input_fingerprint"}:
        raise ValueError("intent 필수 키 불일치")
    request = intent["request"]
    kind = _check_request(request)
    actual_sha = _request_sha256(request)
    if intent["request_sha256"] != actual_sha:
        raise ValueError("request_sha256 불일치: 전달값을 신뢰하지 않음")
    if kind == "adopt_output":
        _check_str(intent["request_id"], "request_id")
        _check_nonneg_int(intent["input_revision"], "intent input_revision")
        if intent["input_revision"] != request["expected_revision"]:
            raise ValueError("intent input_revision != expected_revision")
        _check_target_refs(intent["target_refs"])
        _check_sha256(intent["input_fingerprint"], "intent input_fingerprint")
    elif kind == "import":
        if intent["request_id"] != "gg:import:" + actual_sha:
            raise ValueError("import request_id 규칙 불일치")
        for key in ("input_revision", "target_refs", "input_fingerprint"):
            if intent[key] is not None:
                raise ValueError(
                    "import intent의 " + key + "는 null이어야 함")
    else:
        if intent["request_id"] != "gg:" + kind + ":" + actual_sha:
            raise ValueError(kind + " request_id 규칙 불일치")
        for key in ("input_revision", "target_refs", "input_fingerprint"):
            if intent[key] != request[key]:
                raise ValueError("intent " + key + " != request " + key)
    return {
        "kind": kind,
        "request": request,
        "request_id": intent["request_id"],
        "request_sha256": actual_sha,
        "input_revision": intent["input_revision"],
        "target_refs": intent["target_refs"],
        "input_fingerprint": intent["input_fingerprint"],
    }


def _publication_id(workspace_id, kind, request_id, request_sha256):
    body = gg_fs.canonical_json_bytes({
        "workspace_id": workspace_id,
        "kind": kind,
        "request_id": request_id,
        "request_sha256": request_sha256,
    })
    return hashlib.sha256(body).hexdigest()


def _publication_ref(publication_id, path, sha256):
    return {
        "schema": REF_SCHEMA,
        "id": publication_id,
        "path": path,
        "sha256": sha256,
    }


def _build_receipt(workspace_id, protocol_sha256, checked, *,
                   source_files, files, primary_path, requested_path,
                   output_id, output_format):
    pid = _publication_id(
        workspace_id, checked["kind"],
        checked["request_id"], checked["request_sha256"])
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "publication_id": pid,
        "workspace_id": workspace_id,
        "protocol_sha256": protocol_sha256,
        "kind": checked["kind"],
        "request": checked["request"],
        "request_id": checked["request_id"],
        "request_sha256": checked["request_sha256"],
        "input_revision": checked["input_revision"],
        "target_refs": checked["target_refs"],
        "input_fingerprint": checked["input_fingerprint"],
        "source_files": source_files,
        "files": files,
        "primary_path": primary_path,
        "requested_path": requested_path,
        "output_id": output_id,
        "output_format": output_format,
    }
    return pid, receipt


# ---------- no-follow fd-relative filesystem helpers (POSIX) ----------


def _open_dir(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _open_dir_at(dir_fd, name):
    return os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _stat_at(dir_fd, name):
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def _open_checked(dir_fd, part, st):
    """Open a subdirectory and prove the opened object is the one stat'ed."""
    nfd = _open_dir_at(dir_fd, part)
    opened = os.fstat(nfd)
    if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
        os.close(nfd)
        raise ValueError("디렉터리가 경합으로 교체됨: " + part)
    return nfd


def _walk_parent(root, rel):
    """(dir_fd, basename) after a no-follow component walk from root."""
    parts = _check_relative_path(rel)
    fd = _open_dir(root)
    try:
        for part in parts[:-1]:
            st = _stat_at(fd, part)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise ValueError(
                    "경로 구성요소가 디렉터리가 아니거나 링크: " + part)
            nfd = _open_checked(fd, part, st)
            os.close(fd)
            fd = nfd
        return fd, parts[-1]
    except Exception:
        os.close(fd)
        raise


def _read_file(root, rel):
    """No-follow read of a root-relative regular file; returns bytes."""
    fd, name = _walk_parent(root, rel)
    try:
        st = _stat_at(fd, name)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ValueError("대상이 일반 파일이 아니거나 링크: " + rel)
        ffd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            opened = os.fstat(ffd)
            if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                raise ValueError("파일이 경합으로 교체됨: " + rel)
            chunks = []
            while True:
                chunk = os.read(ffd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(ffd)
    finally:
        os.close(fd)


def _lstat_rel(root, rel):
    """stat_result for a root-relative path via no-follow walk, or None."""
    fd = None
    try:
        fd, name = _walk_parent(root, rel)
        try:
            return _stat_at(fd, name)
        except FileNotFoundError:
            return None
    except FileNotFoundError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _ensure_dirs(root, rel):
    """Create missing components of a root-relative dir under no-follow
    exclusive mkdir; identity is re-verified after create/race.  Returns
    (final dir fd, [created rel paths]).  Product write — caller holds the
    capability."""
    parts = _check_relative_path(rel)
    created = []
    fd = _open_dir(root)
    prefix = []
    try:
        for part in parts:
            try:
                st = _stat_at(fd, part)
            except FileNotFoundError:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
                created.append("/".join(prefix + [part]))
                st = _stat_at(fd, part)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise ValueError(
                    "디렉터리 생성 경로가 링크/비디렉터리: " + part)
            nfd = _open_checked(fd, part, st)
            os.close(fd)
            fd = nfd
            prefix.append(part)
        return fd, created
    except Exception:
        os.close(fd)
        raise


def _scan_fd(fd, prefix, out):
    with os.scandir(fd) as it:
        entries = sorted(it, key=lambda e: e.name.encode("utf-8"))
    for entry in entries:
        st = entry.stat(follow_symlinks=False)
        rel = entry.name if not prefix else prefix + "/" + entry.name
        if stat.S_ISLNK(st.st_mode):
            raise ValueError("심볼릭 링크 항목은 발행할 수 없음: " + rel)
        if stat.S_ISDIR(st.st_mode):
            nfd = _open_checked(fd, entry.name, st)
            try:
                _scan_fd(nfd, rel, out)
            finally:
                os.close(nfd)
        elif stat.S_ISREG(st.st_mode):
            ffd = os.open(
                entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                fst = os.fstat(ffd)
                if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
                    raise ValueError("파일이 경합으로 교체됨: " + rel)
                h = hashlib.sha256()
                while True:
                    chunk = os.read(ffd, 1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
                out.append({
                    "path": rel,
                    "sha256": h.hexdigest(),
                    "size": fst.st_size,
                })
            finally:
                os.close(ffd)
        else:
            raise ValueError("일반 파일/디렉터리가 아닌 항목: " + rel)


def _scan_tree(base):
    """Recursive regular-file inventory of a directory (no-follow)."""
    out = []
    fd = _open_dir(base)
    try:
        _scan_fd(fd, "", out)
    finally:
        os.close(fd)
    return out


def _scan_root_filtered(base, drop_top_dirs, drop_root_files):
    """Workspace-root inventory excluding control objects; no-follow."""
    out = []
    fd = _open_dir(base)
    try:
        with os.scandir(fd) as it:
            entries = sorted(it, key=lambda e: e.name.encode("utf-8"))
        for entry in entries:
            st = entry.stat(follow_symlinks=False)
            name = entry.name
            if name in drop_root_files:
                if stat.S_ISLNK(st.st_mode):
                    raise ValueError("제외 대상 파일이 링크: " + name)
                continue
            if name in drop_top_dirs:
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    raise ValueError("제어 디렉터리가 링크/비디렉터리: " + name)
                continue
            if stat.S_ISLNK(st.st_mode):
                raise ValueError("심볼릭 링크 항목: " + name)
            if stat.S_ISDIR(st.st_mode):
                nfd = _open_checked(fd, name, st)
                try:
                    _scan_fd(nfd, name, out)
                finally:
                    os.close(nfd)
            elif stat.S_ISREG(st.st_mode):
                ffd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    fst = os.fstat(ffd)
                    if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
                        raise ValueError("파일이 경합으로 교체됨: " + name)
                    h = hashlib.sha256()
                    while True:
                        chunk = os.read(ffd, 1 << 20)
                        if not chunk:
                            break
                        h.update(chunk)
                    out.append({"path": name, "sha256": h.hexdigest(),
                                "size": fst.st_size})
                finally:
                    os.close(ffd)
            else:
                raise ValueError("일반 파일/디렉터리가 아닌 항목: " + name)
    finally:
        os.close(fd)
    return out


def _entries_equal(actual, declared):
    if len(actual) != len(declared):
        return False
    for a, d in zip(actual, declared):
        if (a["path"], a["sha256"], a["size"]) != (
                d["path"], d["sha256"], d["size"]):
            return False
    return True


def _identity_of(path, kind):
    return gg_fs.identity(path, kind=kind)


def _protocol_binding(root):
    """Read the workspace control protocol read-only; returns
    (workspace_id, sha256 of exact protocol bytes)."""
    rel = CONTROL_DIR + "/" + PROTOCOL_NAME
    st = _lstat_rel(root, rel)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise ValueError("제어 protocol이 없거나 일반 파일이 아님")
    data = _read_file(root, rel)
    try:
        proto = _loads_strict(data)
    except ValueError as e:
        raise ValueError("제어 protocol 파싱 불가") from e
    if not isinstance(proto, dict) or proto.get("protocol") != 2:
        raise ValueError("제어 protocol이 v2가 아님")
    ws = proto.get("workspace_id")
    _check_str(ws, "workspace_id")
    return ws, hashlib.sha256(data).hexdigest()


# ---------- read-only receipt verification ----------


def _loads_strict(data):
    def _no_dup(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("중복 JSON 키: " + str(k))
            obj[k] = v
        return obj

    def _no_const(value):
        raise ValueError("비유한수 JSON 값: " + value)

    return json.loads(
        data, object_pairs_hook=_no_dup, parse_constant=_no_const)


def _managed_receipt_kind(rel):
    """Classify a managed receipt path; returns 'import', 'bundle', or raises."""
    parts = rel.split("/")
    if rel == IMPORT_RECEIPT_NAME:
        return "import", ""
    if parts[0] == ARTIFACT_DIR and len(parts) == 3 \
            and parts[2] == RECEIPT_NAME:
        if len(parts[1]) == 64 and all(c in _HEX for c in parts[1]):
            return "bundle", parts[0] + "/" + parts[1]
        raise ValueError("잘못된 artifact publication 경로: " + rel)
    if parts[0] == BUILD_DIR and len(parts) == 4 \
            and parts[3] == RECEIPT_NAME and parts[1].isdigit():
        return "bundle", rel.rsplit("/", 1)[0]
    raise ValueError("receipt가 관리 위치가 아님: " + rel)


def verify_publication(root, *, publication_ref, expected=None):
    """Read-only verification of a managed publication receipt.  Returns the
    verified receipt dict or raises ValueError.  Never writes, never takes a
    guard, never parses the canonical ledger."""
    root = Path(root)
    if not isinstance(publication_ref, dict) or set(publication_ref) != {
            "schema", "id", "path", "sha256"}:
        raise ValueError("잘못된 publication_ref")
    if publication_ref["schema"] != REF_SCHEMA:
        raise ValueError("publication_ref schema 불일치")
    _check_sha256(publication_ref["id"], "publication id")
    _check_relative_path(publication_ref["path"], "receipt path")
    _check_sha256(publication_ref["sha256"], "receipt sha256")
    _managed_receipt_kind(publication_ref["path"])
    if not _port_supported():
        raise ValueError("unsupported_publication_port")

    ws_id, proto_sha = _protocol_binding(root)
    data = _read_file(root, publication_ref["path"])
    if hashlib.sha256(data).hexdigest() != publication_ref["sha256"]:
        raise ValueError("receipt bytes 해시 불일치")
    try:
        receipt = _loads_strict(data)
    except ValueError as e:
        raise ValueError("receipt JSON 파싱 불가: " + str(e)) from e
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("receipt schema 불일치")
    if set(receipt) != RECEIPT_KEYS:
        raise ValueError("receipt 필수 키 불일치")

    kind = receipt["kind"]
    if kind not in _REQUEST_KEYS:
        raise ValueError("receipt kind 불명: " + repr(kind))
    _check_request(receipt["request"])
    if _request_sha256(receipt["request"]) != receipt["request_sha256"]:
        raise ValueError("receipt request_sha256 재계산 불일치")
    if kind == "adopt_output":
        _check_str(receipt["request_id"], "receipt request_id")
    elif receipt["request_id"] != (
            "gg:" + kind + ":" + receipt["request_sha256"]):
        raise ValueError("receipt request_id 규칙 불일치")
    pid = _publication_id(
        receipt["workspace_id"], kind,
        receipt["request_id"], receipt["request_sha256"])
    if pid != receipt["publication_id"] or pid != publication_ref["id"]:
        raise ValueError("publication_id 재계산 불일치")

    if not (receipt["workspace_id"] == ws_id
            and receipt["protocol_sha256"] == proto_sha):
        lineage = gg_lock.verify_recovery_lineage(
            root,
            previous_workspace_id=receipt["workspace_id"],
            previous_protocol_sha256=receipt["protocol_sha256"],
            current_workspace_id=ws_id,
            current_protocol_sha256=proto_sha,
        )
        if not lineage.get("trusted"):
            raise ValueError(
                "과거 protocol 연결 미신뢰: " + str(lineage.get("reason")))

    files = receipt["files"]
    if not isinstance(files, list):
        raise ValueError("receipt files가 목록이 아님")
    _check_file_entries(files, require_destination=False,
                        what="receipt files")
    if receipt["source_files"] is not None:
        _check_file_entries(
            receipt["source_files"],
            require_destination=(kind == "adopt_output"),
            what="receipt source_files")
    kind_class, bundle = _managed_receipt_kind(publication_ref["path"])
    if kind == "import" and kind_class != "import":
        raise ValueError("import receipt가 root에 없음")
    if kind != "import" and kind_class != "bundle":
        raise ValueError("비 import receipt가 관리 bundle에 없음")
    if kind == "import":
        actual = _scan_root_filtered(
            root, drop_top_dirs={CONTROL_DIR},
            drop_root_files={IMPORT_RECEIPT_NAME})
    else:
        actual = [e for e in _scan_tree(Path(root) / bundle)
                  if e["path"] != RECEIPT_NAME]
    if not _entries_equal(actual, files):
        raise ValueError("발행본 파일 집합/hash가 receipt 선언과 불일치")
    if receipt["primary_path"] is not None:
        _check_relative_path(receipt["primary_path"], "primary_path")
        if receipt["primary_path"] not in {e["path"] for e in files}:
            raise ValueError("receipt primary_path가 files에 없음")
    for key in ("requested_path", "output_id", "output_format"):
        if receipt[key] is not None:
            _check_str(receipt[key], "receipt " + key)
    if receipt["requested_path"] is not None:
        _check_relative_path(receipt["requested_path"], "requested_path")

    if expected is not None:
        if not isinstance(expected, dict):
            raise ValueError("expected는 객체여야 함")
        for key in ("request_id", "request_sha256", "kind",
                    "publication_id", "workspace_id"):
            if key in expected and expected[key] != receipt[key]:
                raise ValueError("receipt " + key + "가 기대와 불일치")
    return receipt


def _iter_managed_receipts(root):
    """Rel paths of every managed receipt location (API §4)."""
    out = []
    art_st = _lstat_rel(root, ARTIFACT_DIR)
    if art_st is not None:
        if stat.S_ISLNK(art_st.st_mode) or not stat.S_ISDIR(art_st.st_mode):
            raise ValueError(".gg-artifacts가 링크/비디렉터리")
        fd = _open_dir(Path(root) / ARTIFACT_DIR)
        try:
            with os.scandir(fd) as it:
                names = sorted(e.name for e in it)
        finally:
            os.close(fd)
        for name in names:
            if len(name) != 64 or any(c not in _HEX for c in name):
                continue
            out.append(ARTIFACT_DIR + "/" + name + "/" + RECEIPT_NAME)
    build_st = _lstat_rel(root, BUILD_DIR)
    if build_st is not None:
        if stat.S_ISLNK(build_st.st_mode) or not stat.S_ISDIR(build_st.st_mode):
            raise ValueError("build가 링크/비디렉터리")
        fd = _open_dir(Path(root) / BUILD_DIR)
        try:
            with os.scandir(fd) as it:
                revs = sorted(e.name for e in it)
        finally:
            os.close(fd)
        for rev in revs:
            sub = _lstat_rel(root, BUILD_DIR + "/" + rev)
            if sub is None or not stat.S_ISDIR(sub.st_mode):
                continue
            fd = _open_dir(Path(root) / BUILD_DIR / rev)
            try:
                with os.scandir(fd) as it:
                    kinds = sorted(e.name for e in it)
            finally:
                os.close(fd)
            for kind in kinds:
                sub = _lstat_rel(root, BUILD_DIR + "/" + rev + "/" + kind)
                if sub is None or not stat.S_ISDIR(sub.st_mode):
                    continue
                out.append(
                    BUILD_DIR + "/" + rev + "/" + kind + "/" + RECEIPT_NAME)
    out.append(IMPORT_RECEIPT_NAME)
    return [rel for rel in out
            if (st := _lstat_rel(root, rel)) is not None
            and stat.S_ISREG(st.st_mode)]


def _find_request_full(root, request_id):
    """Like find_request but also returns each receipt's path+sha."""
    root = Path(root)
    _check_str(request_id, "request_id")
    if not _port_supported():
        raise ValueError("unsupported_publication_port")
    ws_id, _proto_sha = _protocol_binding(root)
    found = []
    for rel in _iter_managed_receipts(root):
        data = _read_file(root, rel)
        try:
            header = _loads_strict(data)
        except ValueError as e:
            raise ValueError("모호한 발행 기록 파싱 불가: " + rel) from e
        if not isinstance(header, dict) or not {
                "schema", "request_id", "workspace_id",
                "publication_id"} <= set(header):
            raise ValueError("모호한 발행 기록 헤더 누락: " + rel)
        if header["request_id"] != request_id:
            continue
        if header["workspace_id"] != ws_id:
            continue
        ref = {
            "schema": REF_SCHEMA,
            "id": header["publication_id"],
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        found.append((verify_publication(root, publication_ref=ref),
                      ref["path"], ref["sha256"]))
    return found


def find_request(root, *, request_id):
    """Read-only scan of managed receipt locations for a request_id.

    Returns verified receipts bound to the CURRENT workspace.  Receipts of a
    previous workspace_id are history, not ownership evidence, and are not
    returned.  Parse/link/header failures are an ambiguous conflict: the
    query fails rather than silently skipping.  No folders or request index
    are ever created."""
    return [receipt for receipt, _path, _sha in
            _find_request_full(root, request_id)]


def _replay_or_conflict(root, checked, destination):
    """Same request_id already published with the identical request -> its
    ref; same id with a different request -> request_id_conflict."""
    try:
        existing = _find_request_full(root, checked["request_id"])
    except ValueError as e:
        _fail(destination, "request 조회 실패(모호한 충돌): " + str(e))
    for receipt, path, sha in existing:
        if receipt["request_sha256"] == checked["request_sha256"]:
            ref = _publication_ref(receipt["publication_id"], path, sha)
            return ref, receipt
        _fail(
            destination,
            "request_id_conflict: 같은 ID의 다른 요청이 이미 발행됨")
    return None


# ---------- publication entry points ----------


def publish_directory(root, capability, *, staging, destination, intent,
                      files):
    """Publish a completed staging directory inside the workspace via one
    no-replace directory rename (export bundles, managed paper bundles)."""
    checked = _check_intent(intent)
    if checked["kind"] not in ("export", "paper"):
        raise ValueError("publish_directory는 export/paper 전용")
    _check_relative_path(destination, "destination")
    for bad in (CONTROL_DIR, ".gg-recovery", OBSERVATION_DIR):
        if destination == bad or destination.startswith(bad + "/"):
            raise ValueError("destination이 제어/예약 경로 내부: " + destination)
    _check_file_entries(files, require_destination=False, what="files")
    gg_lock.assert_held(capability, root)
    _require_port(destination)
    root = Path(root)

    ws_id, proto_sha = _protocol_binding(root)
    replay = _replay_or_conflict(root, checked, destination)
    if replay:
        ref, receipt = replay
        return _result("published", destination, publication_ref=ref)

    staging = Path(staging)
    try:
        st = os.stat(staging, follow_symlinks=False)
    except OSError:
        st = None
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _fail(destination, "staging이 실제 디렉터리가 아님")
    staging_identity = _identity_of(staging, "directory")
    actual = _scan_tree(staging)
    if any(e["path"] == RECEIPT_NAME for e in actual):
        _fail(destination, "staging에 이미 receipt 존재 — C 소유 파일",
              preserved=[str(staging)])
    if not _entries_equal(actual, files):
        _fail(destination, "staging inventory가 선언 files와 불일치",
              preserved=[str(staging)])

    pid, receipt = _build_receipt(
        ws_id, proto_sha, checked,
        source_files=None, files=files,
        primary_path=None,
        requested_path=(checked["request"].get("requested_path")
                        if checked["kind"] == "paper" else None),
        output_id=None,
        output_format=(checked["request"].get("output_format")
                       if checked["kind"] == "paper" else None))
    receipt_bytes = gg_fs.canonical_json_bytes(receipt)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    write = gg_fs.publish_bytes_noreplace(
        str(staging / RECEIPT_NAME), receipt_bytes,
        parent_identity=staging_identity)
    if write["state"] != "installed":
        _fail(destination,
              "receipt 발행 실패: " + str(write.get("reason")),
              preserved=[str(staging)])
    actual = _scan_tree(staging)
    if not _entries_equal(
            [e for e in actual if e["path"] != RECEIPT_NAME], files):
        _fail(destination, "receipt 쓰기 후 staging inventory 불일치",
              preserved=[str(staging)])

    if _lstat_rel(root, destination) is not None:
        return _existing_destination(
            root, destination, checked, staging, preserved=[str(staging)])
    parent_rel = str(Path(destination).parent).replace("\\", "/")
    created = []
    if parent_rel and parent_rel != ".":
        try:
            pfd, created = _ensure_dirs(root, parent_rel)
            os.close(pfd)
        except (ValueError, OSError) as e:
            _fail(destination, "parent 준비 실패: " + str(e),
                  preserved=[str(staging)] + created)
        parent_abs = Path(root) / parent_rel
    else:
        parent_abs = Path(root)
    dest_parent_identity = _identity_of(parent_abs, "directory")
    staging_parent_identity = _identity_of(staging.parent, "directory")
    preserved = [str(staging)] + created

    try:
        res = gg_fs.rename_noreplace(
            str(staging), str(Path(root) / destination),
            source_identity=staging_identity,
            source_parent_identity=staging_parent_identity,
            destination_parent_identity=dest_parent_identity)
    except OSError as e:
        return _after_dir_rename_exception(
            root, destination, checked, staging, staging_identity,
            pid, receipt_sha, files, preserved, e)
    if res["state"] == "installed":
        return _verify_renamed_directory(
            root, destination, staging_identity, pid, receipt_sha, files,
            preserved, res)
    if res["state"] == "not_installed":
        return _existing_destination(
            root, destination, checked, staging, preserved=[str(staging)])
    _fail(destination, "rename 결과 indeterminate: " + str(res.get("reason")),
          preserved=preserved, state="indeterminate")


def _existing_destination(root, destination, checked, staging, *,
                          preserved):
    """Existing destination: identical verified publication -> its ref;
    anything else is preserved and refused."""
    rel = destination.rstrip("/") + "/" + RECEIPT_NAME
    st = _lstat_rel(root, destination)
    if st is None or not stat.S_ISDIR(st.st_mode):
        _fail(destination, "목적지 충돌: 기존 비디렉터리 객체 보존",
              preserved=preserved)
    rst = _lstat_rel(root, rel)
    if rst is not None:
        receipt = None
        ref = None
        try:
            data = _read_file(root, rel)
            header = _loads_strict(data)
            ref = {
                "schema": REF_SCHEMA,
                "id": header["publication_id"],
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            receipt = verify_publication(root, publication_ref=ref)
        except (ValueError, OSError, KeyError):
            receipt = None
        if (receipt is not None
                and receipt["request_id"] == checked["request_id"]
                and receipt["request_sha256"] == checked["request_sha256"]):
            return _result("published", destination, publication_ref=ref)
    _fail(destination, "목적지 충돌: 다른 요청/사용자 객체 보존",
          preserved=preserved)


def _after_dir_rename_exception(root, destination, checked, staging,
                                staging_identity, pid, receipt_sha, files,
                                preserved, err):
    """A rename call that raised may still have landed: re-verify the actual
    destination instead of assuming failure."""
    st = _lstat_rel(root, destination)
    if st is not None and stat.S_ISDIR(st.st_mode):
        try:
            ident = _identity_of(Path(root) / destination, "directory")
        except (ValueError, OSError):
            ident = None
        if ident == staging_identity:
            return _verify_renamed_directory(
                root, destination, staging_identity, pid, receipt_sha,
                files, preserved, None,
                cleanup=[{"operation": "rename_noreplace",
                          "reason": type(err).__name__ + ": " + str(err)}])
        _fail(destination,
              "rename 오류 후 대상이 다른 객체 — 확인 불가: " + str(err),
              preserved=preserved, state="indeterminate")
    _fail(destination, "rename 호출 실패: " + str(err), preserved=preserved)


def _verify_renamed_directory(root, destination, staging_identity, pid,
                              receipt_sha, files, preserved, res,
                              cleanup=()):
    rel = destination.rstrip("/") + "/" + RECEIPT_NAME
    try:
        ident = _identity_of(Path(root) / destination, "directory")
        if ident != staging_identity:
            raise ValueError("대상 identity가 staging과 불일치")
        actual = _scan_tree(Path(root) / destination)
        data = _read_file(root, rel)
        if hashlib.sha256(data).hexdigest() != receipt_sha:
            raise ValueError("발행본 receipt 해시 불일치")
        if not _entries_equal(
                [e for e in actual if e["path"] != RECEIPT_NAME], files):
            raise ValueError("발행본 payload 불일치")
    except (ValueError, OSError) as e:
        _fail(destination, "rename 후 대상 재확인 불가: " + str(e),
              preserved=preserved, state="indeterminate")
    ref = _publication_ref(pid, rel, receipt_sha)
    out = _result("published", destination, publication_ref=ref)
    out["cleanup_errors"] = (
        list(cleanup) + list((res or {}).get("cleanup_errors") or []))
    return out


def publish_output(root, capability, *, intent, sources):
    """adopt_output: copy verified source files into
    .gg-artifacts/<publication_id>/ and issue the immutable receipt.
    Originals/sidecars are never modified."""
    checked = _check_intent(intent)
    if checked["kind"] != "adopt_output":
        raise ValueError("publish_output은 adopt_output 전용")
    _check_file_entries(sources, require_destination=True, what="sources")
    request = checked["request"]
    companions = request.get("companion_files", [])
    src_by_path = {s["path"]: s for s in sources}
    for comp in companions:
        got = src_by_path.get(comp["path"])
        if got is None or got["sha256"] != comp["sha256"]:
            raise ValueError(
                "companion 선언과 sources 불일치: " + comp["path"])
    primary_source = None
    out_path = request["output_value"].get("path")
    if out_path is not None:
        _check_relative_path(out_path, "output_value.path")
        primary_source = src_by_path.get(out_path)
        if primary_source is None:
            raise ValueError("primary 출력이 sources에 없음: " + str(out_path))
    dests = [s["destination"] for s in sources]
    if len(set(dests)) != len(dests):
        raise ValueError("sources destination 중복")
    gg_lock.assert_held(capability, root)
    _require_port(ARTIFACT_DIR)
    root = Path(root)

    ws_id, proto_sha = _protocol_binding(root)
    pid = _publication_id(
        ws_id, "adopt_output", checked["request_id"],
        checked["request_sha256"])
    bundle_rel = ARTIFACT_DIR + "/" + pid
    receipt_rel = bundle_rel + "/" + RECEIPT_NAME

    replay = _replay_or_conflict(root, checked, bundle_rel)
    if replay:
        ref, receipt = replay
        expected_files = [
            {"path": s["destination"], "sha256": s["sha256"],
             "size": s["size"]}
            for s in sorted(
                sources, key=lambda s: s["destination"].encode("utf-8"))]
        if receipt["files"] != expected_files:
            _fail(bundle_rel,
                  "같은 request_id의 기존 발행본과 선언 파일 불일치")
        return _result("published", bundle_rel, publication_ref=ref)

    if _lstat_rel(root, bundle_rel) is not None:
        return _existing_destination(
            root, bundle_rel, checked, bundle_rel, preserved=[])

    try:
        bfd, created = _ensure_dirs(root, bundle_rel)
        os.close(bfd)
    except (ValueError, OSError) as e:
        _fail(bundle_rel, "관리 디렉터리 준비 실패: " + str(e), preserved=[])

    payload_entries = []
    for source in sources:
        before = _read_file(root, source["path"])
        if (hashlib.sha256(before).hexdigest() != source["sha256"]
                or len(before) != source["size"]):
            _fail(bundle_rel,
                  "원본 hash/size가 선언과 불일치: " + source["path"],
                  preserved=created)
        dest_rel = bundle_rel + "/" + source["destination"]
        parent_rel = str(Path(dest_rel).parent).replace("\\", "/")
        try:
            pfd, made = _ensure_dirs(root, parent_rel)
            os.close(pfd)
            created += made
        except (ValueError, OSError) as e:
            _fail(bundle_rel, "bundle 하위 디렉터리 실패: " + str(e),
                  preserved=created)
        write = gg_fs.publish_bytes_noreplace(
            str(Path(root) / dest_rel), before,
            parent_identity=_identity_of(
                Path(root) / parent_rel, "directory"))
        if write["state"] != "installed":
            _fail(bundle_rel,
                  "파일 발행 실패: " + str(write.get("reason")),
                  preserved=created)
        after = _read_file(root, source["path"])
        if (hashlib.sha256(after).hexdigest() != source["sha256"]
                or len(after) != source["size"]):
            _fail(bundle_rel, "복사 중 원본 변경: " + source["path"],
                  preserved=created)
        payload_entries.append({
            "path": source["destination"],
            "sha256": source["sha256"],
            "size": source["size"],
        })
    payload_entries.sort(key=lambda e: e["path"].encode("utf-8"))

    source_files = [
        {"path": s["path"], "sha256": s["sha256"], "size": s["size"],
         "destination": s["destination"]}
        for s in sorted(sources, key=lambda s: s["path"].encode("utf-8"))]
    _pid, receipt = _build_receipt(
        ws_id, proto_sha, checked,
        source_files=source_files, files=payload_entries,
        primary_path=(
            primary_source["destination"] if primary_source else None),
        requested_path=None,
        output_id=request["output_value"].get("id"),
        output_format=request["output_value"].get("format"))
    receipt_bytes = gg_fs.canonical_json_bytes(receipt)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    write = gg_fs.publish_bytes_noreplace(
        str(Path(root) / receipt_rel), receipt_bytes,
        parent_identity=_identity_of(Path(root) / bundle_rel, "directory"))
    if write["state"] != "installed":
        _fail(bundle_rel, "receipt 발행 실패: " + str(write.get("reason")),
              preserved=created)
    actual = _scan_tree(Path(root) / bundle_rel)
    if not _entries_equal(
            [e for e in actual if e["path"] != RECEIPT_NAME],
            payload_entries):
        _fail(bundle_rel, "bundle 최종 inventory 불일치", preserved=created)
    ref = _publication_ref(pid, receipt_rel, receipt_sha)
    return _result("published", bundle_rel, publication_ref=ref)


def publish_workspace(staging, capability, *, destination, intent, files):
    """import 전용: staged workspace를 자기 guard를 보유한 채 외부 목적지로
    no-replace rename하고 동일 identity로 capability 경로를 갱신한다.
    대상 확인 성공 + rebind/owner 정리 실패는 cleanup_errors로 보존한다."""
    checked = _check_intent(intent)
    if checked["kind"] != "import":
        raise ValueError("publish_workspace는 import 전용")
    _check_abs_destination(destination)
    if destination != checked["request"]["destination"]:
        raise ValueError("destination 인자와 request.destination 불일치")
    _check_file_entries(files, require_destination=False, what="files")
    staging = Path(staging)
    gg_lock.assert_held(capability, staging)
    _require_port(destination)

    ws_id, proto_sha = _protocol_binding(staging)
    try:
        st = os.stat(staging, follow_symlinks=False)
    except OSError:
        st = None
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _fail(destination, "staging이 실제 디렉터리가 아님")
    staging_identity = _identity_of(staging, "directory")

    if _lstat_rel(staging, IMPORT_RECEIPT_NAME) is not None:
        _fail(destination, "원본에 import receipt 경로가 이미 존재 — 충돌")
    actual = _scan_root_filtered(
        staging, drop_top_dirs={CONTROL_DIR}, drop_root_files=set())
    if not _entries_equal(actual, files):
        _fail(destination, "staging payload inventory가 files와 불일치",
              preserved=[str(staging)])

    pid, receipt = _build_receipt(
        ws_id, proto_sha, checked,
        source_files=checked["request"]["source_inventory"],
        files=files, primary_path=None, requested_path=None,
        output_id=None, output_format=None)
    receipt_bytes = gg_fs.canonical_json_bytes(receipt)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    write = gg_fs.publish_bytes_noreplace(
        str(staging / IMPORT_RECEIPT_NAME), receipt_bytes,
        parent_identity=staging_identity)
    if write["state"] != "installed":
        _fail(destination,
              "import receipt 발행 실패: " + str(write.get("reason")),
              preserved=[str(staging)])

    dst = Path(destination)
    if dst.exists() or dst.is_symlink():
        return _existing_import_destination(
            destination, checked, staging, receipt_sha)
    try:
        pst = os.stat(dst.parent, follow_symlinks=False)
    except OSError:
        pst = None
    if pst is None or stat.S_ISLNK(pst.st_mode) or not stat.S_ISDIR(pst.st_mode):
        _fail(destination, "대상 부모가 실제 디렉터리가 아님",
              preserved=[str(staging)])
    dest_parent_identity = _identity_of(dst.parent, "directory")
    staging_parent_identity = _identity_of(staging.parent, "directory")

    try:
        res = gg_fs.rename_noreplace(
            str(staging), str(dst),
            source_identity=staging_identity,
            source_parent_identity=staging_parent_identity,
            destination_parent_identity=dest_parent_identity)
    except OSError as e:
        return _after_import_rename_exception(
            destination, staging, staging_identity, receipt_sha, e)
    if res["state"] == "not_installed":
        return _existing_import_destination(
            destination, checked, staging, receipt_sha)
    if res["state"] != "installed":
        _fail(destination, "import rename indeterminate: "
              + str(res.get("reason")), preserved=[str(staging)],
              state="indeterminate")

    cleanup = list(res.get("cleanup_errors") or [])
    try:
        ident = _identity_of(dst, "directory")
        if ident != staging_identity:
            raise ValueError("대상 root identity가 staging과 불일치")
        ws2, proto2 = _protocol_binding(dst)
        if (ws2, proto2) != (ws_id, proto_sha):
            raise ValueError("이동 후 protocol 불일치")
        data = _read_file(dst, IMPORT_RECEIPT_NAME)
        if hashlib.sha256(data).hexdigest() != receipt_sha:
            raise ValueError("import receipt 해시 불일치")
        actual = _scan_root_filtered(
            dst, drop_top_dirs={CONTROL_DIR},
            drop_root_files={IMPORT_RECEIPT_NAME})
        if not _entries_equal(actual, files):
            raise ValueError("이동 후 payload 불일치")
    except (ValueError, OSError) as e:
        _fail(destination, "import 대상 사후 확인 불가: " + str(e),
              preserved=[str(dst)], state="indeterminate")
    try:
        gg_lock.rebind_after_rename(
            capability, previous_root=str(staging), new_root=str(dst))
    except Exception as e:  # rebind 실패는 대상 확인과 별개의 cleanup
        cleanup.append({"operation": "rebind_after_rename",
                        "reason": type(e).__name__ + ": " + str(e)})
    ref = _publication_ref(pid, IMPORT_RECEIPT_NAME, receipt_sha)
    out = _result("published", destination, publication_ref=ref)
    out["cleanup_errors"] = cleanup
    return out


def _existing_import_destination(destination, checked, staging, receipt_sha):
    dst = Path(destination)
    try:
        data = _read_file(dst, IMPORT_RECEIPT_NAME)
        header = _loads_strict(data)
        ok = (
            isinstance(header, dict)
            and header.get("request_id") == checked["request_id"]
            and header.get("request_sha256") == checked["request_sha256"]
            and hashlib.sha256(data).hexdigest() == receipt_sha)
    except (OSError, ValueError):
        ok = False
    if ok:
        ref = _publication_ref(
            header["publication_id"], IMPORT_RECEIPT_NAME, receipt_sha)
        return _result("published", destination, publication_ref=ref)
    _fail(destination, "import 목적지 충돌: 다른 요청/사용자 객체 보존",
          preserved=[str(staging)])


def _after_import_rename_exception(destination, staging, staging_identity,
                                   receipt_sha, err):
    dst = Path(destination)
    try:
        if (dst.exists()
                and _identity_of(dst, "directory") == staging_identity):
            data = _read_file(dst, IMPORT_RECEIPT_NAME)
            if hashlib.sha256(data).hexdigest() == receipt_sha:
                ref = _publication_ref(
                    _loads_strict(data)["publication_id"],
                    IMPORT_RECEIPT_NAME, receipt_sha)
                out = _result("published", destination,
                              publication_ref=ref)
                out["cleanup_errors"] = [{
                    "operation": "rename_noreplace",
                    "reason": type(err).__name__ + ": " + str(err)}]
                return out
    except (OSError, ValueError):
        pass
    _fail(destination,
          "import rename 오류 후 대상 확인 불가: " + str(err),
          preserved=[str(staging)], state="indeterminate")


def publish_requested_file(root, capability, *, managed, requested_path):
    """paper --out: after capability verification C creates the internal
    parent no-follow, copies the verified managed file into an exclusive
    temp in the same parent, then no-replace renames onto the requested
    path.  Existing foreign destinations are preserved."""
    _check_relative_path(requested_path, "requested_path")
    if not isinstance(managed, dict) or not {"path", "sha256"} <= set(managed):
        raise ValueError("managed는 {path,sha256[,publication_path]} 필요")
    _check_relative_path(managed["path"], "managed path")
    _check_sha256(managed["sha256"], "managed sha256")
    pub_path = managed.get("publication_path")
    if pub_path is not None:
        _check_relative_path(pub_path, "managed publication_path")
    gg_lock.assert_held(capability, root)
    _require_port(requested_path)
    root = Path(root)

    managed_bytes = _read_file(root, managed["path"])
    if hashlib.sha256(managed_bytes).hexdigest() != managed["sha256"]:
        _fail(requested_path, "관리본 해시 불일치")

    if pub_path is not None:
        receipt_rel = pub_path.rstrip("/") + "/" + RECEIPT_NAME
        try:
            data = _read_file(root, receipt_rel)
            header = _loads_strict(data)
            req = header.get("request") or {}
            ok = (header.get("kind") == "paper"
                  and req.get("requested_path") == requested_path)
        except (ValueError, OSError, AttributeError):
            ok = False
        if not ok:
            _fail(requested_path, "관리본 receipt와 요청 경로 불일치")

    st = _lstat_rel(root, requested_path)
    if st is not None:
        if stat.S_ISREG(st.st_mode):
            existing = _read_file(root, requested_path)
            if hashlib.sha256(existing).hexdigest() == managed["sha256"]:
                return _result("published", requested_path,
                               target_sha256=managed["sha256"])
        _fail(requested_path, "요청 목적지 충돌: 기존 객체 보존")

    parent_rel = str(Path(requested_path).parent).replace("\\", "/")
    created = []
    try:
        if parent_rel and parent_rel != ".":
            pfd, created = _ensure_dirs(root, parent_rel)
            os.close(pfd)
            parent_abs = Path(root) / parent_rel
        else:
            parent_abs = Path(root)
    except (ValueError, OSError) as e:
        _fail(requested_path, "parent 준비 실패: " + str(e),
              preserved=created)
    parent_identity = _identity_of(parent_abs, "directory")

    tmp_name = ".gg-requested-" + uuid.uuid4().hex + ".tmp"
    tmp_rel = (parent_rel + "/" + tmp_name
               if parent_rel and parent_rel != "." else tmp_name)
    tmp_abs = Path(root) / tmp_rel
    preserved = created + [tmp_rel]
    try:
        pfd = _open_dir(parent_abs)
        try:
            tfd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644, dir_fd=pfd)
        finally:
            os.close(pfd)
        try:
            view = memoryview(managed_bytes)
            while view:
                view = view[os.write(tfd, view):]
            os.fsync(tfd)
        finally:
            os.close(tfd)
        written = _read_file(root, tmp_rel)
        if hashlib.sha256(written).hexdigest() != managed["sha256"]:
            _fail(requested_path, "임시 복사본 해시 불일치",
                  preserved=preserved)
    except PublicationError:
        raise
    except OSError as e:
        _fail(requested_path, "임시 파일 쓰기 실패: " + str(e),
              preserved=preserved)

    tmp_identity = _identity_of(tmp_abs, "file")
    try:
        res = gg_fs.rename_noreplace(
            str(tmp_abs), str(Path(root) / requested_path),
            source_identity=tmp_identity,
            source_parent_identity=parent_identity,
            destination_parent_identity=parent_identity)
    except OSError as e:
        st2 = _lstat_rel(root, requested_path)
        if st2 is not None and stat.S_ISREG(st2.st_mode):
            data = _read_file(root, requested_path)
            if hashlib.sha256(data).hexdigest() == managed["sha256"]:
                out = _result("published", requested_path,
                              target_sha256=managed["sha256"])
                out["cleanup_errors"] = [{
                    "operation": "rename_noreplace",
                    "reason": type(e).__name__ + ": " + str(e)}]
                return out
        _fail(requested_path,
              "rename 오류 후 요청 경로 확인 불가: " + str(e),
              preserved=preserved, state="indeterminate")
    if res["state"] == "not_installed":
        st2 = _lstat_rel(root, requested_path)
        if st2 is not None and stat.S_ISREG(st2.st_mode):
            data = _read_file(root, requested_path)
            if hashlib.sha256(data).hexdigest() == managed["sha256"]:
                _cleanup_temp(root, tmp_rel)
                return _result("published", requested_path,
                               target_sha256=managed["sha256"])
        _fail(requested_path,
              "요청 목적지 충돌: " + str(res.get("reason")),
              preserved=preserved)
    if res["state"] != "installed":
        _fail(requested_path,
              "rename indeterminate: " + str(res.get("reason")),
              preserved=preserved, state="indeterminate")
    try:
        data = _read_file(root, requested_path)
        if hashlib.sha256(data).hexdigest() != managed["sha256"]:
            raise ValueError("요청 경로 해시 불일치")
    except (ValueError, OSError) as e:
        _fail(requested_path, "발행 후 재확인 불가: " + str(e),
              preserved=preserved, state="indeterminate")
    out = _result("published", requested_path,
                  target_sha256=managed["sha256"])
    out["cleanup_errors"] = list(res.get("cleanup_errors") or [])
    return out


def _cleanup_temp(root, rel):
    try:
        fd, name = _walk_parent(root, rel)
        try:
            os.unlink(name, dir_fd=fd)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass


def _obs_fail(reason):
    raise PublicationError(
        _result("not_published", OBSERVATION_DIR, reason=reason))


def publish_observation(root, capability, *, recorded):
    """Observation v2 immutable publish to .gg-observations/<sha>.json.
    Structure and bytes are verified before the directory is even created;
    failures never leave .gg-observations behind."""
    if not isinstance(recorded, dict) or set(recorded) != OBSERVATION_KEYS:
        _obs_fail("observation 필수 키 불일치")
    if recorded["schema"] != OBSERVATION_SCHEMA:
        _obs_fail("observation schema 불일치")
    for key in ("observed_by", "author_session", "reviewer_session",
                "author_id", "reviewer_id"):
        if not isinstance(recorded[key], str) or not recorded[key].strip():
            _obs_fail("observation " + key + "는 비어 있을 수 없음")
    if recorded["author_session"] == recorded["reviewer_session"]:
        _obs_fail("자기 세션 관측 불가")
    if recorded["observed_by"] in (
            recorded["author_session"], recorded["reviewer_session"]):
        _obs_fail("관측자가 당사자 세션")
    kinds = recorded["review_kinds"]
    if (not isinstance(kinds, list) or not kinds
            or any(not isinstance(k, str) or not k for k in kinds)
            or len(set(kinds)) != len(kinds)
            or kinds != sorted(kinds, key=lambda k: k.encode("utf-8"))):
        _obs_fail("review_kinds는 중복 없는 UTF-8 정렬 문자열 목록")
    try:
        _check_target_refs(recorded["target_refs"])
        _check_sha256(recorded["input_fingerprint"], "input_fingerprint")
        _check_relative_path(recorded["report_path"], "report_path")
        _check_sha256(recorded["report_hash"], "report_hash")
        _check_nonneg_int(recorded["input_revision"], "input_revision")
        _check_str(recorded["workspace_id"], "workspace_id")
        _check_sha256(recorded["protocol_sha256"], "protocol_sha256")
    except ValueError as e:
        _obs_fail(str(e))
    gg_lock.assert_held(capability, root)
    _require_port(OBSERVATION_DIR)
    root = Path(root)

    ws_id, proto_sha = _protocol_binding(root)
    if (recorded["workspace_id"], recorded["protocol_sha256"]) != (
            ws_id, proto_sha):
        _obs_fail("observation의 workspace/protocol이 현재와 불일치")
    try:
        report = _read_file(root, recorded["report_path"])
    except (ValueError, OSError) as e:
        _obs_fail("report 파일 읽기 불가: " + str(e))
    if hashlib.sha256(report).hexdigest() != recorded["report_hash"]:
        _obs_fail("report bytes 해시 불일치")

    payload = gg_fs.canonical_json_bytes(recorded)
    sha = hashlib.sha256(payload).hexdigest()
    rel = OBSERVATION_DIR + "/" + sha + ".json"
    st = _lstat_rel(root, rel)
    if st is not None:
        existing = _read_file(root, rel)
        if existing == payload:
            return {"path": rel, "hash": sha}
        _obs_fail("관측 기록 충돌: 동일 경로 다른 bytes")
    try:
        dfd, created = _ensure_dirs(root, OBSERVATION_DIR)
        os.close(dfd)
    except (ValueError, OSError) as e:
        _obs_fail("관측 디렉터리 준비 실패: " + str(e))
    write = gg_fs.publish_bytes_noreplace(
        str(Path(root) / rel), payload,
        parent_identity=_identity_of(
            Path(root) / OBSERVATION_DIR, "directory"))
    if write["state"] != "installed":
        if created:
            try:
                os.rmdir(Path(root) / OBSERVATION_DIR)
            except OSError:
                pass
        _obs_fail("관측 발행 실패: " + str(write.get("reason")))
    try:
        check = _read_file(root, rel)
    except (ValueError, OSError) as e:
        _obs_fail("관측 발행 후 재확인 불가: " + str(e))
    if check != payload:
        _obs_fail("관측 발행 후 재확인 불일치")
    return {"path": rel, "hash": sha}
