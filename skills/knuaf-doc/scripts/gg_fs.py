"""P2 lane B — low-level filesystem primitives.

Import direction: gg_fs <- gg_lock <- gg_publication <- gg_core <- gg.py.
This module imports nothing from the product.  It provides:

- canonical JSON receipt bytes (new receipts only; existing
  project/history/request/fingerprint hashes keep the old core.digest
  serialization and are never re-serialized here);
- no-follow file/directory identity based on real stat data;
- no-replace rename for regular files and completed directories;
- publish-bytes via exclusive temp + fsync + no-replace rename;
- sanitized filesystem error details (reason/code/operation/path only).

Anything the platform cannot prove (stable identity, atomic no-replace
rename, directory fsync) fails closed instead of falling back to
path/size/mtime heuristics or exists()+rename.
"""

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import uuid
from pathlib import Path

__all__ = [
    "FsError",
    "canonical_json_bytes",
    "validate_relative_path",
    "identity",
    "fd_identity",
    "same_identity",
    "assert_identity",
    "sha256_bytes",
    "file_sha256",
    "read_bytes",
    "fsync_dir",
    "mkdir_directory",
    "unlink_file",
    "rename_noreplace",
    "publish_bytes_noreplace",
    "FsWriteResult",
]

_RENAME_EXCL = 0x00000004  # macOS renameatx_np
_RENAME_NOREPLACE = 0x00000001  # Linux renameat2
_AT_FDCWD = -2 if sys.platform == "darwin" else -100


class FsError(OSError):
    """Filesystem failure with sanitized detail.

    Only reason/code/operation/path are exposed — no environment
    variables, credentials, or document contents.
    """

    def __init__(self, reason, *, code=None, operation=None, path=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = {
            "reason": reason,
            "code": code,
            "operation": operation,
            "path": str(path) if path is not None else None,
        }


def FsWriteResult(
    state,
    reason,
    path,
    identity_value=None,
    sha256=None,
    preserved_paths=None,
    cleanup_errors=None,
):
    return {
        "state": state,
        "reason": reason,
        "path": str(path),
        "identity": identity_value,
        "sha256": sha256,
        "preserved_paths": list(preserved_paths or []),
        "cleanup_errors": list(cleanup_errors or []),
    }


def _check_json_keys(value):
    if isinstance(value, dict):
        for key, sub in value.items():
            if not isinstance(key, str):
                raise ValueError("receipt JSON object keys must be strings")
            _check_json_keys(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            _check_json_keys(sub)


def canonical_json_bytes(value):
    """New-receipt serialization: UTF-8, ensure_ascii=False, sort_keys,
    separators=(",", ":"), allow_nan=False, exactly one final LF."""
    _check_json_keys(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def validate_relative_path(value):
    """Root-relative '/'-separated path. Rejects absolute paths, empty
    components, '.', '..', NUL, Windows drive and UNC forms."""
    if not isinstance(value, str) or not value:
        raise ValueError("relative path must be a nonempty string")
    if "\x00" in value:
        raise ValueError("relative path contains NUL")
    if value.startswith("/") or value.startswith("//"):
        raise ValueError("relative path must not be absolute/UNC")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError("relative path has empty/dot component")
        if len(part) >= 2 and part[1] == ":" and part[0].isalpha():
            raise ValueError("relative path contains a Windows drive")
    return value


def _identity_from_stat(st, kind, path):
    if kind == "file":
        if not stat.S_ISREG(st.st_mode):
            raise FsError(
                "not_regular_file", code="kind", operation="identity", path=path
            )
    elif kind == "directory":
        if not stat.S_ISDIR(st.st_mode):
            raise FsError(
                "not_directory", code="kind", operation="identity", path=path
            )
    else:
        raise ValueError("kind must be 'file' or 'directory'")
    inode = getattr(st, "st_ino", 0)
    if sys.platform == "win32" and not inode:
        raise FsError(
            "identity_unavailable",
            code="unsupported_identity",
            operation="identity",
            path=path,
        )
    return {
        "platform": sys.platform,
        "kind": kind,
        "device": st.st_dev,
        "inode": inode,
    }


def identity(path, *, kind):
    """Real no-follow identity of an existing file or directory."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except OSError as error:
        raise FsError(
            "stat_failed", code=error.errno, operation="identity", path=path
        )
    if stat.S_ISLNK(st.st_mode):
        raise FsError(
            "symlink_reparse", code="symlink", operation="identity", path=path
        )
    return _identity_from_stat(st, kind, path)


def fd_identity(fd, *, kind):
    try:
        st = os.fstat(fd)
    except OSError as error:
        raise FsError(
            "stat_failed", code=error.errno, operation="fd_identity", path=None
        )
    return _identity_from_stat(st, kind, "fd:%d" % fd)


def same_identity(a, b):
    return (
        isinstance(a, dict)
        and isinstance(b, dict)
        and a.get("platform") == b.get("platform")
        and a.get("kind") == b.get("kind")
        and a.get("device") == b.get("device")
        and a.get("inode") == b.get("inode")
    )


def assert_identity(path, expected):
    got = identity(path, kind=expected["kind"])
    if not same_identity(got, expected):
        raise FsError(
            "identity_mismatch", code="identity", operation="assert_identity",
            path=path,
        )
    return None


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _open_nofollow(path, flags, mode=0o600):
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    return os.open(str(path), flags, mode)


def read_bytes(path):
    """Read a regular file without following symlinks.
    Returns (bytes, identity)."""
    path = Path(path)
    fd = _open_nofollow(path, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise FsError(
                "not_regular_file", code="kind", operation="read", path=path
            )
        ident = _identity_from_stat(st, "file", path)
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), ident
    finally:
        os.close(fd)


def file_sha256(path):
    data, _ident = read_bytes(path)
    return sha256_bytes(data)


def fsync_dir(path):
    """Best-effort directory fsync. Unsupported platforms raise FsError
    instead of silently claiming durability."""
    path = Path(path)
    if sys.platform == "win32":
        raise FsError(
            "dir_fsync_unsupported",
            code="unsupported",
            operation="fsync_dir",
            path=path,
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    except OSError as error:
        raise FsError(
            "dir_fsync_failed",
            code=error.errno,
            operation="fsync_dir",
            path=path,
        )
    finally:
        os.close(fd)


def mkdir_directory(path):
    path = Path(path)
    try:
        os.mkdir(str(path))
    except OSError as error:
        raise FsError(
            "mkdir_failed", code=error.errno, operation="mkdir", path=path
        )
    return identity(path, kind="directory")


def unlink_file(path, *, expected_identity=None):
    """Unlink a non-directory entry; never removes directories and never
    follows the final component. If expected_identity is given it must
    match before removal."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise FsError(
            "missing", code=errno.ENOENT, operation="unlink", path=path
        )
    if stat.S_ISDIR(st.st_mode):
        raise FsError(
            "is_directory", code="kind", operation="unlink", path=path
        )
    if expected_identity is not None:
        got = _identity_from_stat(st, expected_identity["kind"], path)
        if not same_identity(got, expected_identity):
            raise FsError(
                "identity_mismatch",
                code="identity",
                operation="unlink",
                path=path,
            )
    os.unlink(str(path))


_libc = None


def _get_libc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc


def _os_rename_noreplace(src, dst):
    """Atomic rename that fails when dst exists. No exists()+rename,
    os.replace, shutil.move or copy/delete fallback."""
    src_b = os.fsencode(str(src))
    dst_b = os.fsencode(str(dst))
    if sys.platform == "darwin":
        libc = _get_libc()
        fn = getattr(libc, "renameatx_np", None)
        if fn is None:
            raise FsError(
                "unsupported_no_replace",
                code="symbol_missing",
                operation="rename_noreplace",
                path=dst,
            )
        fn.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        fn.restype = ctypes.c_int
        rc = fn(_AT_FDCWD, src_b, _AT_FDCWD, dst_b, _RENAME_EXCL)
        if rc:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(dst))
        return
    if sys.platform.startswith("linux"):
        libc = _get_libc()
        fn = getattr(libc, "renameat2", None)
        if fn is None:
            raise FsError(
                "unsupported_no_replace",
                code="symbol_missing",
                operation="rename_noreplace",
                path=dst,
            )
        fn.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        fn.restype = ctypes.c_int
        rc = fn(_AT_FDCWD, src_b, _AT_FDCWD, dst_b, _RENAME_NOREPLACE)
        if rc:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(dst))
        return
    if sys.platform == "win32":
        # Documented contract: os.rename on Windows always fails when
        # dst exists (FileExistsError) — that is the no-replace path.
        os.rename(str(src), str(dst))
        return
    raise FsError(
        "unsupported_no_replace",
        code="platform",
        operation="rename_noreplace",
        path=dst,
    )


def _post_rename_state(src, dst, source_identity, error):
    """After a rename call raised, determine the real outcome instead of
    guessing from the exception name alone."""
    preserved = [str(src), str(dst)]
    try:
        got = identity(dst, kind=source_identity["kind"])
        if same_identity(got, source_identity):
            return FsWriteResult(
                "installed",
                "rename_error_but_target_verified",
                dst,
                identity_value=got,
                cleanup_errors=[
                    {
                        "reason": "ambiguous_rename_error",
                        "code": getattr(error, "errno", None),
                        "operation": "rename_noreplace",
                        "path": str(dst),
                    }
                ],
            )
        return FsWriteResult(
            "not_installed", "destination_exists", dst,
            preserved_paths=preserved,
        )
    except FsError:
        pass
    try:
        assert_identity(src, source_identity)
        return FsWriteResult(
            "not_installed",
            "rename_failed",
            dst,
            preserved_paths=preserved,
            cleanup_errors=[
                {
                    "reason": "rename_os_error",
                    "code": getattr(error, "errno", None),
                    "operation": "rename_noreplace",
                    "path": str(dst),
                }
            ],
        )
    except FsError:
        return FsWriteResult(
            "unknown", "post_rename_state_unverifiable", dst,
            preserved_paths=preserved,
            cleanup_errors=[
                {
                    "reason": "rename_os_error",
                    "code": getattr(error, "errno", None),
                    "operation": "rename_noreplace",
                    "path": str(dst),
                }
            ],
        )


def rename_noreplace(
    src,
    dst,
    *,
    source_identity,
    source_parent_identity,
    destination_parent_identity,
):
    """Rename a regular file or a completed directory without replacing
    an existing destination. Returns FsWriteResult."""
    src = Path(src)
    dst = Path(dst)
    preserved = [str(src), str(dst)]
    kind = source_identity.get("kind")
    if kind not in ("file", "directory"):
        raise ValueError("source_identity kind must be file or directory")
    for check, expected in (
        (src, source_identity),
        (src.parent, source_parent_identity),
        (dst.parent, destination_parent_identity),
    ):
        try:
            assert_identity(check, expected)
        except (FsError, OSError) as error:
            reason = getattr(error, "reason", "identity_check_failed")
            return FsWriteResult(
                "not_installed", reason, dst, preserved_paths=preserved
            )
    if os.lstat(str(src)).st_dev != os.lstat(str(dst.parent)).st_dev:
        return FsWriteResult(
            "not_installed", "cross_filesystem", dst, preserved_paths=preserved
        )
    try:
        _os_rename_noreplace(src, dst)
    except FsError as error:
        return FsWriteResult(
            "not_installed", error.reason, dst, preserved_paths=preserved
        )
    except OSError as error:
        code = error.errno
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            return FsWriteResult(
                "not_installed", "destination_exists", dst,
                preserved_paths=preserved,
            )
        if code == errno.EXDEV:
            return FsWriteResult(
                "not_installed", "cross_filesystem", dst,
                preserved_paths=preserved,
            )
        if code in (errno.EACCES, errno.EPERM):
            return FsWriteResult(
                "not_installed", "permission_denied", dst,
                preserved_paths=preserved,
            )
        if code in (
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        ):
            return FsWriteResult(
                "not_installed", "unsupported_no_replace", dst,
                preserved_paths=preserved,
            )
        return _post_rename_state(src, dst, source_identity, error)
    try:
        got = identity(dst, kind=kind)
    except FsError:
        return FsWriteResult(
            "unknown", "post_rename_unverifiable", dst,
            preserved_paths=preserved,
        )
    if not same_identity(got, source_identity):
        return FsWriteResult(
            "unknown", "identity_mismatch_after_rename", dst,
            identity_value=got, preserved_paths=preserved,
        )
    digest = None
    cleanup_errors = []
    if kind == "file":
        digest = file_sha256(dst)
    try:
        fsync_dir(dst.parent)
    except FsError as error:
        cleanup_errors.append(error.detail)
    return FsWriteResult(
        "installed",
        None,
        dst,
        identity_value=got,
        sha256=digest,
        cleanup_errors=cleanup_errors,
    )


def publish_bytes_noreplace(path, data, *, parent_identity, existing_sha256=None):
    """Publish bytes to a path that must not already hold foreign content:
    exclusive temp in the same parent -> fsync -> no-replace rename ->
    post-verify. Returns FsWriteResult."""
    path = Path(path)
    parent = path.parent
    try:
        assert_identity(parent, parent_identity)
    except (FsError, OSError) as error:
        return FsWriteResult(
            "not_installed",
            getattr(error, "reason", "parent_identity_check_failed"),
            path,
            preserved_paths=[str(path)],
        )
    try:
        st = os.lstat(str(path))
    except FileNotFoundError:
        st = None
    except OSError as error:
        return FsWriteResult(
            "not_installed", "stat_failed", path,
            preserved_paths=[str(path)],
        )
    want_sha = sha256_bytes(data)
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            return FsWriteResult(
                "not_installed", "destination_not_regular", path,
                preserved_paths=[str(path)],
            )
        if existing_sha256 is None:
            return FsWriteResult(
                "not_installed", "destination_exists", path,
                preserved_paths=[str(path)],
            )
        have = file_sha256(path)
        if have == existing_sha256 and want_sha == existing_sha256:
            ident = identity(path, kind="file")
            return FsWriteResult(
                "installed", "already_present", path,
                identity_value=ident, sha256=have,
            )
        return FsWriteResult(
            "not_installed", "destination_hash_mismatch", path,
            preserved_paths=[str(path)],
        )
    temp = parent / (".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    fd = None
    try:
        fd = _open_nofollow(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except OSError as error:
        return FsWriteResult(
            "not_installed", "temp_create_failed", path,
            preserved_paths=[str(path)],
            cleanup_errors=[
                {
                    "reason": "temp_create_os_error",
                    "code": error.errno,
                    "operation": "temp_create",
                    "path": str(temp),
                }
            ],
        )
    temp_identity = None
    try:
        st = os.fstat(fd)
        temp_identity = _identity_from_stat(st, "file", temp)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except (OSError, FsError) as error:
        if fd is not None:
            os.close(fd)
            fd = None
        return FsWriteResult(
            "not_installed", "temp_write_failed", path,
            preserved_paths=[str(temp), str(path)],
            cleanup_errors=[
                {
                    "reason": getattr(error, "reason", "temp_write_os_error"),
                    "code": getattr(error, "errno", None),
                    "operation": "temp_write",
                    "path": str(temp),
                }
            ],
        )
    finally:
        if fd is not None:
            os.close(fd)
    result = rename_noreplace(
        temp,
        path,
        source_identity=temp_identity,
        source_parent_identity=parent_identity,
        destination_parent_identity=parent_identity,
    )
    if result["state"] == "installed" and result["sha256"] != want_sha:
        result["state"] = "unknown"
        result["reason"] = "post_publish_hash_mismatch"
    if result["state"] != "installed":
        try:
            os.lstat(str(temp))
            result["preserved_paths"].append(str(temp))
        except FileNotFoundError:
            pass
    return result
