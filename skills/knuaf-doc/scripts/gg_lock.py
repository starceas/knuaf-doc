"""P2 lane B — OS guard, capability, diagnosis, recovery.

Depends only on gg_fs (gg_fs <- gg_lock <- gg_publication <- gg_core).

Contract summary (design-v1 API §3, SPEC §3-5):
- Persistent control directory ``.gg-lock/`` with ``protocol.json``
  (schema gg-lock/2), a 1-byte ``guard`` regular file and diagnostic
  ``owner.json``. Exclusion comes from the OS guard only; owner is
  diagnostic and never read for authority.
- ``Capability`` objects live in an in-process registry. Constructed or
  copied lookalikes, other-PID use, released objects, forged dicts and
  recovery capabilities are all refused by ``assert_held``.
- Recovery uses ``.gg-recovery/<recovery_id>/`` (guard + immutable
  claim + stage receipts 00/10/20/30) plus the fixed
  ``.gg-recovery-active.json`` claim published no-clobber. Normal writes
  are blocked while an active file exists — valid or corrupt.
- Platform ports are lazy. Windows uses msvcrt LK_NBLCK on offset 0 and
  never reads guard bytes; where contention cannot be told from
  permission denial the state is unavailable/busy_or_denied. Platforms
  without a real guard primitive fail closed.
"""

import errno
import json
import os
import socket
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import gg_fs

LOCK_DIR = ".gg-lock"
PROTOCOL_NAME = "protocol.json"
GUARD_NAME = "guard"
OWNER_NAME = "owner.json"
RECOVERY_DIR = ".gg-recovery"
ACTIVE_NAME = ".gg-recovery-active.json"
PROTOCOL_SCHEMA = "gg-lock/2"
CLAIM_SCHEMA = "gg-lock-recovery-claim/1"
STAGE_SCHEMA = "gg-lock-recovery-stage/1"
STAGES = ("00-prepared", "10-quarantined", "20-installed", "30-completed")

# Private fault hook: tests may patch this with a callable(stage_name).
# Never driven by environment variables.
_FAULT = None


def _run_fault(stage):
    if _FAULT is not None:
        _FAULT(stage)


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


class LockError(ValueError):
    def __init__(self, status, reason):
        super().__init__("%s: %s" % (status, reason))
        self.status = status
        self.reason = reason


class LockCleanupError(OSError):
    def __init__(self, errors, *, release_confirmed, prior_error=None):
        super().__init__("lock release cleanup errors: %r" % (errors,))
        self.errors = list(errors)
        self.release_confirmed = release_confirmed
        self.prior_error = prior_error


class Capability:
    """Held-guard token. Only objects issued by Lock.__enter__ and still
    present in the internal registry are valid; constructing one or
    copying its fields produces an unregistered, useless object."""

    __slots__ = (
        "token", "pid", "root", "root_identity", "directory_identity",
        "guard_identity", "protocol_sha256", "workspace_id", "owner",
        "released", "_fd",
    )

    def __init__(self):
        self.token = None
        self.pid = None
        self.root = None
        self.root_identity = None
        self.directory_identity = None
        self.guard_identity = None
        self.protocol_sha256 = None
        self.workspace_id = None
        self.owner = None
        self.released = True
        self._fd = None


class _RecoveryCapability:
    """Recovery-only proof: matching active/claim/recovery_id plus a
    real held recovery guard fd. Never valid for product writes."""

    __slots__ = (
        "pid", "root", "recovery_id", "recovery_dir",
        "recovery_guard_identity", "claim_sha256", "active_identity",
        "new_guard_identity", "released", "_fd", "_new_fd",
    )

    def __init__(self):
        self.pid = None
        self.root = None
        self.recovery_id = None
        self.recovery_dir = None
        self.recovery_guard_identity = None
        self.claim_sha256 = None
        self.active_identity = None
        self.new_guard_identity = None
        self.released = True
        self._fd = None
        self._new_fd = None


_CAPS = {}
_RECOVERY_CAPS = {}


def _at_fork_child():
    # Child copies of held fds are closed without LOCK_UN so the
    # parent's lock stays held; registries are invalidated.
    for cap in list(_CAPS.values()):
        try:
            if cap._fd is not None:
                os.close(cap._fd)
        except OSError:
            pass
        cap._fd = None
        cap.released = True
    for cap in list(_RECOVERY_CAPS.values()):
        for fd in (cap._fd, cap._new_fd):
            try:
                if fd is not None:
                    os.close(fd)
            except OSError:
                pass
        cap._fd = None
        cap._new_fd = None
        cap.released = True
    _CAPS.clear()
    _RECOVERY_CAPS.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_at_fork_child)


# ---------------------------------------------------------------- ports


class _PosixPort:
    name = "posix"

    def open_guard(self, path):
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(
            os, "O_CLOEXEC", 0
        )
        return os.open(str(path), flags)

    def lock(self, fd):
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(self, fd):
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class _WindowsPort:
    """Real Windows port: msvcrt LK_NBLCK on offset 0 of the guard.
    Guard bytes are never read and owner I/O uses a separate file."""

    name = "win32"

    def __init__(self):
        import msvcrt

        self._msvcrt = msvcrt

    def open_guard(self, path):
        return os.open(str(path), os.O_RDWR | os.O_BINARY)

    def lock(self, fd):
        os.lseek(fd, 0, os.SEEK_SET)
        self._msvcrt.locking(fd, self._msvcrt.LK_NBLCK, 1)

    def unlock(self, fd):
        os.lseek(fd, 0, os.SEEK_SET)
        self._msvcrt.locking(fd, self._msvcrt.LK_UNLCK, 1)


class _UnsupportedPort:
    name = "unsupported"

    def open_guard(self, path):
        raise LockError("unavailable", "unsupported_platform")

    def lock(self, fd):
        raise LockError("unavailable", "unsupported_platform")

    def unlock(self, fd):
        raise LockError("unavailable", "unsupported_platform")


def _make_port():
    if sys.platform == "win32":
        try:
            return _WindowsPort()
        except ImportError:
            return _UnsupportedPort()
    try:
        import fcntl  # noqa: F401

        return _PosixPort()
    except ImportError:
        return _UnsupportedPort()


_PORT = _make_port()


# ------------------------------------------------------- small helpers


def _status(structure, *, lock_state="unprobed", reason=None, protocol=None,
            owner=None, recovery_id=None):
    return {
        "structure": structure,
        "lock_state": lock_state,
        "reason": reason,
        "protocol": protocol,
        "owner": owner,
        "observed_at": _utcnow(),
        "recovery_id": recovery_id,
    }


def _read_active(root):
    """Returns (recovery_id_or_None, reason_or_None). Any active file —
    valid, corrupt, partial or a symlink — is reported and blocks."""
    active = Path(root) / ACTIVE_NAME
    try:
        st = os.lstat(str(active))
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "recovery_active_corrupt"
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None, "recovery_active_corrupt"
    try:
        data, _ident = gg_fs.read_bytes(active)
        claim = json.loads(data.decode("utf-8"))
        rid = claim.get("recovery_id") if isinstance(claim, dict) else None
        if claim.get("schema") != CLAIM_SCHEMA or not isinstance(rid, str):
            return None, "recovery_active_corrupt"
        return rid, "recovery_active"
    except (OSError, ValueError):
        return None, "recovery_active_corrupt"


def _active_present(root):
    try:
        os.lstat(str(Path(root) / ACTIVE_NAME))
        return True
    except OSError:
        return False


def _read_protocol(lockdir):
    """Returns (protocol_dict, protocol_bytes, file_identity) or raises
    FsError/LockError."""
    path = Path(lockdir) / PROTOCOL_NAME
    try:
        data, ident = gg_fs.read_bytes(path)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise gg_fs.FsError(
            getattr(error, "reason", "protocol_read_failed"),
            code=getattr(error, "errno", None),
            operation="protocol_read",
            path=path,
        )
    try:
        proto = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LockError("damaged", "protocol_corrupt")
    if not isinstance(proto, dict) or proto.get("schema") != PROTOCOL_SCHEMA:
        raise LockError("damaged", "protocol_schema")
    return proto, data, ident


def _inspect(root):
    """Structural read. Returns (LockStatus, detail) — detail carries
    protocol sha + identities for internal callers."""
    root = Path(root)
    detail = {}
    root_id = gg_fs.identity(root, kind="directory")
    detail["root_identity"] = root_id
    recovery_id, active_reason = _read_active(root)
    lockdir = root / LOCK_DIR
    try:
        st = os.lstat(str(lockdir))
    except FileNotFoundError:
        return _status(
            "absent", reason=active_reason, recovery_id=recovery_id
        ), detail
    except OSError as error:
        return _status(
            "damaged", reason="lock_dir_stat_failed:%d" % error.errno,
            recovery_id=recovery_id,
        ), detail
    if stat.S_ISLNK(st.st_mode):
        return _status(
            "damaged", reason="lock_dir_symlink", recovery_id=recovery_id
        ), detail
    if not stat.S_ISDIR(st.st_mode):
        return _status(
            "damaged", reason="lock_dir_not_directory",
            recovery_id=recovery_id,
        ), detail
    dir_id = gg_fs.identity(lockdir, kind="directory")
    detail["directory_identity"] = dir_id

    proto_path = lockdir / PROTOCOL_NAME
    guard_path = lockdir / GUARD_NAME
    owner_path = lockdir / OWNER_NAME
    have_protocol = os.path.lexists(str(proto_path))
    have_guard = os.path.lexists(str(guard_path))
    have_owner = os.path.lexists(str(owner_path))

    if not have_protocol:
        # No protocol: never resumable by a different request.
        if have_owner and not have_guard:
            return _status(
                "legacy", reason="pre_v2_lock_dir", recovery_id=recovery_id
            ), detail
        return _status(
            "initializing",
            reason=active_reason or "protocol_missing",
            recovery_id=recovery_id,
        ), detail

    try:
        proto, proto_bytes, _proto_ident = _read_protocol(lockdir)
    except FileNotFoundError:
        return _status(
            "initializing", reason="protocol_missing",
            recovery_id=recovery_id,
        ), detail
    except LockError as error:
        return _status(
            "damaged", reason=error.reason, recovery_id=recovery_id
        ), detail
    except gg_fs.FsError as error:
        return _status(
            "damaged", reason=error.reason, recovery_id=recovery_id
        ), detail
    detail["protocol_sha256"] = gg_fs.sha256_bytes(proto_bytes)

    if not gg_fs.same_identity(proto.get("directory_identity") or {},
                               dir_id):
        return _status(
            "damaged", reason="directory_identity_mismatch",
            protocol=proto, recovery_id=recovery_id,
        ), detail
    if not have_guard:
        return _status(
            "damaged", reason="guard_missing", protocol=proto,
            recovery_id=recovery_id,
        ), detail
    try:
        guard_id = gg_fs.identity(guard_path, kind="file")
    except gg_fs.FsError as error:
        return _status(
            "damaged", reason="guard_%s" % error.reason, protocol=proto,
            recovery_id=recovery_id,
        ), detail
    detail["guard_identity"] = guard_id
    if not gg_fs.same_identity(proto.get("guard_identity") or {},
                               guard_id):
        return _status(
            "damaged", reason="guard_replaced", protocol=proto,
            recovery_id=recovery_id,
        ), detail
    try:
        if os.lstat(str(guard_path)).st_size < 1:
            return _status(
                "damaged", reason="guard_truncated", protocol=proto,
                recovery_id=recovery_id,
            ), detail
    except OSError:
        pass

    owner = None
    reason = None
    if have_owner:
        try:
            odata, _oid = gg_fs.read_bytes(owner_path)
            parsed = json.loads(odata.decode("utf-8"))
            owner = parsed if isinstance(parsed, dict) else None
            if owner is None:
                reason = "owner_corrupt"
        except (OSError, ValueError):
            reason = "owner_corrupt"
    if not gg_fs.same_identity(proto.get("root_identity") or {}, root_id):
        # Intact control objects, but they describe another root: this
        # is a copied workspace and normal writes are blocked.
        reason = "root_identity_mismatch"
    reason = reason or active_reason
    return _status(
        "ready", reason=reason, protocol=proto, owner=owner,
        recovery_id=recovery_id,
    ), detail


def inspect_lock(root):
    """Pure structural/owner read: no owner/guard/receipt writes."""
    status, _detail = _inspect(root)
    return status


def _guard_path(root):
    return Path(root) / LOCK_DIR / GUARD_NAME


def _acquire_guard(root, detail):
    """Open + nonblocking lock the guard, verifying fd and path
    identity. Returns fd. Raises LockError."""
    guard_path = _guard_path(root)
    try:
        fd = _PORT.open_guard(guard_path)
    except LockError:
        raise
    except OSError as error:
        raise LockError(
            "unavailable", "guard_open_failed:%s" % error.errno
        )
    try:
        fd_id = gg_fs.fd_identity(fd, kind="file")
        if not gg_fs.same_identity(fd_id, detail["guard_identity"]):
            raise LockError("damaged", "guard_identity_mismatch")
        if os.fstat(fd).st_size < 1:
            raise LockError("damaged", "guard_truncated")
        try:
            _PORT.lock(fd)
        except OSError as error:
            code = error.errno
            if getattr(_PORT, "name", "") == "win32" and code in (
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
                errno.EDEADLK,
            ):
                raise LockError("unavailable", "busy_or_denied")
            if code in (
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
            ):
                raise LockError("busy", "guard_busy")
            raise LockError("unavailable", "guard_lock_failed:%s" % code)
        # The path must still resolve to the same open object.
        gg_fs.assert_identity(guard_path, fd_id)
        return fd
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _release_fd(fd):
    _PORT.unlock(fd)
    os.close(fd)


def probe_lock(root):
    """One nonblocking acquire/release on a valid ready v2 control. No
    owner write, no guard-byte read, no automatic retry."""
    status, detail = _inspect(root)
    if status["structure"] != "ready" or status["reason"] is not None:
        status["lock_state"] = "unavailable"
        status["reason"] = status["reason"] or status["structure"]
        return status
    try:
        fd = _acquire_guard(Path(root), detail)
    except LockError as error:
        status["lock_state"] = (
            "busy" if error.status == "busy" else "unavailable"
        )
        status["reason"] = error.reason
        return status
    try:
        _release_fd(fd)
    except OSError as error:
        status["lock_state"] = "unavailable"
        status["reason"] = "release_failed:%s" % error.errno
        return status
    status["lock_state"] = "free"
    status["reason"] = "observation_only_not_a_write_permit"
    return status


def _write_owner_file(lockdir, owner):
    """(Re)write owner.json while holding the guard. Stale owner files
    belong to a dead holder — we hold the OS lock, so replacement is
    safe; a non-regular owner entry is refused."""
    lockdir = Path(lockdir)
    owner_path = lockdir / OWNER_NAME
    try:
        st = os.lstat(str(owner_path))
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            raise gg_fs.FsError(
                "owner_not_regular", code="kind",
                operation="owner_write", path=owner_path,
            )
        os.unlink(str(owner_path))
    fd = gg_fs._open_nofollow(
        owner_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
    )
    try:
        data = gg_fs.canonical_json_bytes(owner)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        st = os.fstat(fd)
        return gg_fs._identity_from_stat(st, "file", owner_path)
    finally:
        os.close(fd)


def _install_control(root, *, recovery_id=None, workspace_id=None):
    """Create .gg-lock (mkdir winner only), exclusive 1-byte guard, then
    atomic protocol publication. Never continues another request's
    incomplete directory."""
    root = Path(root)
    lockdir = root / LOCK_DIR
    try:
        os.mkdir(str(lockdir))
    except FileExistsError:
        raise LockError("exists", "lock_dir_exists")
    except OSError as error:
        raise LockError("unavailable", "lock_dir_mkdir:%s" % error.errno)
    dir_id = gg_fs.identity(lockdir, kind="directory")
    root_id = gg_fs.identity(root, kind="directory")
    guard_path = lockdir / GUARD_NAME
    fd = gg_fs._open_nofollow(
        guard_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
    )
    try:
        os.write(fd, b"\x00")
        os.fsync(fd)
        guard_id = gg_fs._identity_from_stat(
            os.fstat(fd), "file", guard_path
        )
    finally:
        os.close(fd)
    protocol = {
        "schema": PROTOCOL_SCHEMA,
        "protocol": 2,
        "workspace_id": workspace_id or str(uuid.uuid4()),
        "root_identity": root_id,
        "directory_identity": dir_id,
        "guard_identity": guard_id,
        "recovery_id": recovery_id,
    }
    result = gg_fs.publish_bytes_noreplace(
        lockdir / PROTOCOL_NAME,
        gg_fs.canonical_json_bytes(protocol),
        parent_identity=dir_id,
    )
    if result["state"] != "installed":
        raise LockError(
            "unavailable",
            "protocol_publish_failed:%s" % result["reason"],
        )
    try:
        gg_fs.fsync_dir(lockdir)
    except gg_fs.FsError:
        pass
    return protocol


def install_new(root):
    """Install a fresh v2 control on a path with no project, no lock and
    no active recovery. Returns the published protocol dict."""
    root = Path(root)
    gg_fs.identity(root, kind="directory")
    if os.path.lexists(str(root / "project.json")):
        raise LockError("legacy", "project_exists")
    if _active_present(root):
        raise LockError("busy", "recovery_active")
    if os.path.lexists(str(root / LOCK_DIR)):
        raise LockError("exists", "lock_dir_exists")
    return _install_control(root)


# -------------------------------------------------------------- Lock()


def _acquire(root):
    root = Path(root)
    root_id = gg_fs.identity(root, kind="directory")
    if _active_present(root):
        raise LockError("busy", "recovery_active")
    status, detail = _inspect(root)
    if status["structure"] != "ready":
        raise LockError(
            status["structure"], status["reason"] or "not_ready"
        )
    if status["reason"] is not None:
        raise LockError("ready", status["reason"])
    proto = status["protocol"]
    fd = _acquire_guard(root, detail)
    try:
        # Post-acquire re-verification before any product write.
        gg_fs.assert_identity(root, root_id)
        gg_fs.assert_identity(root / LOCK_DIR, proto["directory_identity"])
        _p, pb, _pi = _read_protocol(root / LOCK_DIR)
        if gg_fs.sha256_bytes(pb) != detail["protocol_sha256"]:
            raise LockError("damaged", "protocol_changed")
        if _active_present(root):
            raise LockError("busy", "recovery_active")
        owner = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "token": uuid.uuid4().hex,
            "protocol_sha256": detail["protocol_sha256"],
            "workspace_id": proto.get("workspace_id"),
            "acquired_at": _utcnow(),
        }
        try:
            _write_owner_file(root / LOCK_DIR, owner)
        except (OSError, gg_fs.FsError) as error:
            # Acquisition failure: no product write, fd certainly
            # closed, guard/protocol preserved.
            raise LockError(
                "unavailable",
                "owner_write_failed:%s"
                % getattr(error, "reason", getattr(error, "errno", error)),
            )
    except Exception:
        try:
            _release_fd(fd)
        except Exception:
            pass
        raise
    cap = Capability()
    cap.token = uuid.uuid4().hex
    cap.pid = os.getpid()
    cap.root = root
    cap.root_identity = root_id
    cap.directory_identity = proto["directory_identity"]
    cap.guard_identity = detail["guard_identity"]
    cap.protocol_sha256 = detail["protocol_sha256"]
    cap.workspace_id = proto.get("workspace_id")
    cap.owner = owner
    cap.released = False
    cap._fd = fd
    _CAPS[cap.token] = cap
    return cap


def _cleanup_owner_record(cap):
    """Remove owner.json only when the live record is exactly ours:
    whole-dict match plus stable inode re-checks, never another
    worker's file."""
    lockdir = Path(cap.root) / LOCK_DIR
    dir_fd = os.open(
        str(lockdir),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        st = os.fstat(dir_fd)
        if not gg_fs.same_identity(
            gg_fs._identity_from_stat(st, "directory", lockdir),
            cap.directory_identity,
        ):
            raise OSError("lock directory identity changed")
        try:
            ofd = os.open(
                OWNER_NAME, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
        except FileNotFoundError:
            return
        with os.fdopen(ofd, "rb") as handle:
            first = os.fstat(handle.fileno())
            data = handle.read()
            handle.seek(0)
            if handle.read() != data:
                raise OSError("owner record replaced during read")
        try:
            record = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise OSError("owner record corrupt")
        if record != cap.owner:
            raise OSError("owner record is not ours")
        again = os.stat(OWNER_NAME, dir_fd=dir_fd, follow_symlinks=False)
        if (again.st_dev, again.st_ino) != (first.st_dev, first.st_ino):
            raise OSError("owner record replaced")
        os.unlink(OWNER_NAME, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def _release(cap, prior_error=None):
    if cap is None or cap.released:
        return
    if os.getpid() != cap.pid:
        # Child-at-fork: close only the copied fd, never LOCK_UN.
        try:
            if cap._fd is not None:
                os.close(cap._fd)
        except OSError:
            pass
        cap._fd = None
        cap.released = True
        _CAPS.pop(cap.token, None)
        return
    errors = []
    release_confirmed = False
    fd = cap._fd
    if fd is None:
        cap.released = True
        _CAPS.pop(cap.token, None)
        return
    try:
        # Guard ownership/identity must match before and after the
        # metadata cleanup attempt.
        try:
            fd_id = gg_fs.fd_identity(fd, kind="file")
            if not gg_fs.same_identity(fd_id, cap.guard_identity):
                errors.append(
                    {"reason": "guard_identity_changed",
                     "operation": "release_precheck"}
                )
            else:
                try:
                    _cleanup_owner_record(cap)
                except OSError as error:
                    errors.append(
                        {"reason": "owner_cleanup_failed:%s" % error,
                         "operation": "owner_cleanup"}
                    )
                try:
                    gg_fs.fd_identity(fd, kind="file")
                except gg_fs.FsError as error:
                    errors.append(
                        {"reason": "post_cleanup_fd_check:%s"
                         % error.reason,
                         "operation": "release_postcheck"}
                    )
        except gg_fs.FsError as error:
            errors.append(
                {"reason": "fd_check:%s" % error.reason,
                 "operation": "release_precheck"}
            )
    finally:
        try:
            _PORT.unlock(fd)
        except Exception as error:
            errors.append(
                {"reason": "unlock_failed:%s" % error,
                 "operation": "guard_unlock"}
            )
        try:
            os.close(fd)
            release_confirmed = True
        except OSError as error:
            errors.append(
                {"reason": "close_failed:%s" % error.errno,
                 "operation": "guard_close"}
            )
        cap._fd = None
        cap.released = True
        _CAPS.pop(cap.token, None)
    if errors:
        raise LockCleanupError(
            errors, release_confirmed=release_confirmed,
            prior_error=prior_error,
        )


class Lock:
    """Context manager: enter returns a held Capability. Never installs
    or recovers control objects; requires a valid ready v2 structure
    with no active recovery."""

    def __init__(self, root):
        self.root = Path(root)
        self._cap = None

    def __enter__(self):
        self._cap = _acquire(self.root)
        return self._cap

    def __exit__(self, exc_type, exc, tb):
        try:
            _release(self._cap, prior_error=exc)
        finally:
            self._cap = None
        return False


def assert_held(capability, root):
    """Verify a live product capability. Refuses recovery capabilities,
    forged/constructed objects, other-PID use, released objects, swapped
    guard/protocol/root and any active recovery."""
    if isinstance(capability, _RecoveryCapability):
        raise LockError(
            "invalid_capability", "recovery_capability_not_for_product"
        )
    if not isinstance(capability, Capability) or capability.token is None:
        raise LockError("invalid_capability", "not_a_capability")
    if _CAPS.get(capability.token) is not capability:
        raise LockError("invalid_capability", "not_registered")
    if capability.released:
        raise LockError("invalid_capability", "released")
    if os.getpid() != capability.pid:
        raise LockError("invalid_capability", "pid_mismatch")
    root = Path(root)
    root_id = gg_fs.identity(root, kind="directory")
    if not gg_fs.same_identity(root_id, capability.root_identity):
        raise LockError("invalid_capability", "root_identity_mismatch")
    fd_id = gg_fs.fd_identity(capability._fd, kind="file")
    if not gg_fs.same_identity(fd_id, capability.guard_identity):
        raise LockError("invalid_capability", "guard_identity_mismatch")
    gg_fs.assert_identity(root / LOCK_DIR, capability.directory_identity)
    _p, pb, _pi = _read_protocol(root / LOCK_DIR)
    if gg_fs.sha256_bytes(pb) != capability.protocol_sha256:
        raise LockError("invalid_capability", "protocol_changed")
    if _active_present(root):
        raise LockError("busy", "recovery_active")
    return None


def rebind_after_rename(capability, *, previous_root, new_root):
    """After a real held workspace was renamed, verify the same
    root/lock/guard object moved to new_root and previous_root is gone;
    then update only the recorded path. No new grant, no copy."""
    if isinstance(capability, _RecoveryCapability):
        raise LockError("invalid_capability", "recovery_capability")
    if not isinstance(capability, Capability) or capability.token is None:
        raise LockError("invalid_capability", "not_a_capability")
    if _CAPS.get(capability.token) is not capability:
        raise LockError("invalid_capability", "not_registered")
    if capability.released:
        raise LockError("invalid_capability", "released")
    if os.getpid() != capability.pid:
        raise LockError("invalid_capability", "pid_mismatch")
    fd_id = gg_fs.fd_identity(capability._fd, kind="file")
    if not gg_fs.same_identity(fd_id, capability.guard_identity):
        raise LockError("invalid_capability", "guard_identity_mismatch")
    previous_root = Path(previous_root)
    new_root = Path(new_root)
    try:
        prev_id = gg_fs.identity(previous_root, kind="directory")
        if gg_fs.same_identity(prev_id, capability.root_identity):
            raise LockError(
                "invalid_capability", "previous_root_still_present"
            )
    except gg_fs.FsError as error:
        if error.reason != "stat_failed":
            raise LockError("invalid_capability", error.reason)
    new_id = gg_fs.identity(new_root, kind="directory")
    if not gg_fs.same_identity(new_id, capability.root_identity):
        raise LockError("invalid_capability", "root_identity_mismatch")
    gg_fs.assert_identity(
        new_root / LOCK_DIR, capability.directory_identity
    )
    gg_fs.assert_identity(
        new_root / LOCK_DIR / GUARD_NAME, capability.guard_identity
    )
    _p, pb, _pi = _read_protocol(new_root / LOCK_DIR)
    if gg_fs.sha256_bytes(pb) != capability.protocol_sha256:
        raise LockError("invalid_capability", "protocol_changed")
    capability.root = new_root
    return None


def cleanup_owner(root):
    """The 'unlock' operation: acquire the ready v2 guard once, then
    remove only the stale owner record. Busy/unavailable/legacy/damaged
    refuse with no change; guard/protocol/directory are never deleted."""
    root = Path(root)
    status, detail = _inspect(root)
    if status["structure"] != "ready" or status["reason"] is not None:
        raise LockError(
            status["structure"], status["reason"] or "not_ready"
        )
    fd = _acquire_guard(root, detail)
    cleaned = False
    try:
        owner_path = root / LOCK_DIR / OWNER_NAME
        try:
            st = os.lstat(str(owner_path))
        except FileNotFoundError:
            st = None
        if st is not None:
            if not stat.S_ISREG(st.st_mode):
                raise LockError("damaged", "owner_not_regular")
            os.unlink(str(owner_path))
            cleaned = not os.path.lexists(str(owner_path))
    finally:
        _release_fd(fd)
    result = _status("ready", lock_state="free",
                     protocol=status["protocol"])
    result["owner_cleaned"] = cleaned
    return result


# ------------------------------------------------------------- recovery


def _recovery_result(recovery_id, status, commit_state, reason=None,
                     receipt_path=None, receipt_sha256=None,
                     preserved_paths=None, cleanup_errors=None):
    return {
        "recovery_id": recovery_id,
        "status": status,
        "commit_state": commit_state,
        "reason": reason,
        "receipt_path": receipt_path,
        "receipt_sha256": receipt_sha256,
        "preserved_paths": list(preserved_paths or []),
        "cleanup_errors": list(cleanup_errors or []),
    }


def _product_inventory(root):
    """Sorted {path,sha256,size} list of regular product files.
    Control/recovery entries excluded; symlinks or other non-regular
    entries fail closed."""
    root = Path(root)
    skip = {LOCK_DIR, RECOVERY_DIR, ACTIVE_NAME}
    items = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        rel = os.path.relpath(dirpath, str(root))
        top = rel.split(os.sep)[0] if rel != "." else None
        if top in skip:
            dirnames[:] = []
            continue
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                raise gg_fs.FsError(
                    "unsupported_entry", code="symlink",
                    operation="inventory",
                    path=os.path.relpath(
                        os.path.join(dirpath, d), str(root)),
                )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rp = os.path.relpath(full, str(root))
            if rp.split(os.sep)[0] in skip:
                continue
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                raise gg_fs.FsError(
                    "unsupported_entry", code="kind",
                    operation="inventory", path=rp,
                )
            items.append({
                "path": rp.replace(os.sep, "/"),
                "sha256": gg_fs.file_sha256(full),
                "size": st.st_size,
            })
    items.sort(key=lambda x: x["path"])
    return items


def _inventory_sha256(items):
    return gg_fs.sha256_bytes(gg_fs.canonical_json_bytes({"files": items}))


def _original_lock_record(root, status, detail):
    lockdir = Path(root) / LOCK_DIR
    record = {"structure": status["structure"], "identity": None,
              "files": {}}
    if status["structure"] == "absent":
        return record
    try:
        record["identity"] = gg_fs.identity(lockdir, kind="directory")
    except gg_fs.FsError:
        return record
    try:
        for entry in sorted(os.listdir(str(lockdir))):
            full = lockdir / entry
            try:
                st = os.lstat(str(full))
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            record["files"][entry] = {
                "identity": gg_fs.identity(full, kind="file"),
                "sha256": gg_fs.file_sha256(full),
            }
    except OSError:
        pass
    return record


def _stage_receipt(recovery_id, stage, previous_sha256, ctx,
                   completed_actions, next_action, extra=None):
    receipt = {
        "schema": STAGE_SCHEMA,
        "recovery_id": recovery_id,
        "stage": stage,
        "previous_sha256": previous_sha256,
        "claim_sha256": ctx["claim_sha256"],
        "canonical_sha256": ctx["canonical_sha256"],
        "product_inventory_sha256": ctx["inventory_sha256"],
        "original_lock_identity": ctx["original_lock"]["identity"],
        "installed_protocol_sha256": ctx.get("installed_protocol_sha256"),
        "installed_guard_identity": ctx.get("installed_guard_identity"),
        "previous_workspace_id": ctx["previous_workspace_id"],
        "previous_protocol_sha256": ctx["previous_protocol_sha256"],
        "current_workspace_id": ctx.get("current_workspace_id"),
        "current_protocol_sha256": ctx.get("current_protocol_sha256"),
        "disposition": ctx["disposition"],
        "completed_actions": completed_actions,
        "next_action": next_action,
    }
    if extra:
        receipt.update(extra)
    return receipt


def _issue_stage(rdir, rdir_id, receipt):
    data = gg_fs.canonical_json_bytes(receipt)
    result = gg_fs.publish_bytes_noreplace(
        Path(rdir) / (receipt["stage"] + ".json"), data,
        parent_identity=rdir_id,
    )
    if result["state"] != "installed":
        raise LockError(
            "unavailable",
            "stage_publish_failed:%s" % result["reason"],
        )
    return gg_fs.sha256_bytes(data)


def _read_stage(rdir, stage):
    data, _ident = gg_fs.read_bytes(Path(rdir) / (stage + ".json"))
    return json.loads(data.decode("utf-8")), gg_fs.sha256_bytes(data)


def _verify_canonical_unchanged(root, ctx):
    project = Path(root) / "project.json"
    if ctx["canonical_sha256"] is None:
        if os.path.lexists(str(project)):
            raise LockError("damaged", "canonical_appeared")
    else:
        try:
            if gg_fs.file_sha256(project) != ctx["canonical_sha256"]:
                raise LockError("damaged", "canonical_changed")
        except FileNotFoundError:
            raise LockError("damaged", "canonical_missing")
    items = _product_inventory(root)
    if _inventory_sha256(items) != ctx["inventory_sha256"]:
        raise LockError("damaged", "product_inventory_changed")


def _verify_installed_control(root, rec_cap):
    """Under a matching recovery capability, acquire the NEW guard and
    exercise the normal owner record/cleanup path. The fd stays held as
    internal proof until completion + active cleanup."""
    status, detail = _inspect(root)
    if status["structure"] != "ready":
        raise LockError(
            status["structure"],
            status["reason"] or "new_control_invalid",
        )
    # Our own matching active is expected during recovery; anything
    # else that blocks normal writes is still refused.
    if status["reason"] is not None:
        if status["reason"] != "recovery_active":
            raise LockError(status["structure"], status["reason"])
        rid, _ar = _read_active(root)
        if rid != rec_cap.recovery_id:
            raise LockError("damaged", "active_recovery_id_mismatch")
    proto = status["protocol"]
    if proto.get("recovery_id") != rec_cap.recovery_id:
        raise LockError("damaged", "new_protocol_recovery_id_mismatch")
    fd = _acquire_guard(root, detail)
    try:
        owner = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "token": uuid.uuid4().hex,
            "protocol_sha256": detail["protocol_sha256"],
            "workspace_id": proto.get("workspace_id"),
            "acquired_at": _utcnow(),
            "recovery_id": rec_cap.recovery_id,
        }
        _write_owner_file(Path(root) / LOCK_DIR, owner)
        # The owner path itself is verified; clean it so the new
        # control is left in normal free state.
        owner_path = Path(root) / LOCK_DIR / OWNER_NAME
        data, oid = gg_fs.read_bytes(owner_path)
        if json.loads(data.decode("utf-8")) != owner:
            raise LockError("damaged", "new_owner_verify_failed")
        os.unlink(str(owner_path))
    except Exception:
        try:
            _release_fd(fd)
        except Exception:
            pass
        raise
    rec_cap._new_fd = fd
    rec_cap.new_guard_identity = detail["guard_identity"]
    return detail


def _recovery_cleanup(root, rec_cap, ctx):
    """Post-commit cleanup: re-verify active, remove only our active
    file, release held guards. Returns cleanup_errors list."""
    errors = []
    active = Path(root) / ACTIVE_NAME
    try:
        data, ident = gg_fs.read_bytes(active)
        if not gg_fs.same_identity(ident, rec_cap.active_identity):
            raise gg_fs.FsError("active_identity_mismatch")
        if gg_fs.sha256_bytes(data) != rec_cap.claim_sha256:
            raise gg_fs.FsError("active_claim_mismatch")
        os.unlink(str(active))
        if os.path.lexists(str(active)):
            raise gg_fs.FsError("active_still_present")
    except (OSError, gg_fs.FsError) as error:
        errors.append({
            "reason": "active_cleanup:%s" % getattr(
                error, "reason", error),
            "operation": "active_unlink",
            "path": str(active),
        })
    for fd_attr in ("_new_fd", "_fd"):
        fd = getattr(rec_cap, fd_attr)
        if fd is not None:
            try:
                _release_fd(fd)
            except Exception as error:
                errors.append({
                    "reason": "guard_release:%s" % error,
                    "operation": "guard_release",
                    "path": None,
                })
            setattr(rec_cap, fd_attr, None)
    rec_cap.released = True
    _RECOVERY_CAPS.pop(id(rec_cap), None)
    try:
        gg_fs.fsync_dir(root)
    except gg_fs.FsError as error:
        errors.append(error.detail)
    return errors


def _preserved(root, *extra):
    out = []
    for cand in extra:
        if cand and os.path.lexists(str(cand)):
            out.append(str(cand))
    return out


def _recovery_prepare_ctx(root):
    """Shared context for new and resumed recovery."""
    status, detail = _inspect(root)
    proto = status["protocol"] or {}
    have_project = os.path.lexists(str(Path(root) / "project.json"))
    if status["structure"] == "ready" and status["reason"] is None:
        raise LockError("ready", "already_installed")
    if status["structure"] == "absent" and not have_project:
        raise LockError("absent", "nothing_to_recover")
    if proto and gg_fs.same_identity(
            proto.get("root_identity") or {}, detail["root_identity"]):
        disposition = "same_workspace_repair"
        prev_ws = proto.get("workspace_id")
        prev_sha = detail.get("protocol_sha256")
    elif proto:
        disposition = "copied_workspace"
        prev_ws = proto.get("workspace_id")
        prev_sha = detail.get("protocol_sha256")
    else:
        disposition = "legacy_or_unknown"
        prev_ws = None
        prev_sha = None
    items = _product_inventory(root)
    project = Path(root) / "project.json"
    canonical_sha = (
        gg_fs.file_sha256(project) if have_project else None
    )
    return {
        "status": status,
        "detail": detail,
        "disposition": disposition,
        "previous_workspace_id": prev_ws,
        "previous_protocol_sha256": prev_sha,
        "inventory": items,
        "inventory_sha256": _inventory_sha256(items),
        "canonical_sha256": canonical_sha,
        "original_lock": _original_lock_record(root, status, detail),
    }


def _do_stage_00(root, ctx):
    _verify_canonical_unchanged(root, ctx)
    actions = ["canonical_and_inventory_reverified"]
    # Best-effort active-writer check on the ORIGINAL guard if the old
    # control is a structurally valid ready v2; legacy locks cannot be
    # probed by OS means.
    if ctx["status"]["structure"] == "ready":
        try:
            fd = _acquire_guard(root, ctx["detail"])
        except LockError as error:
            if error.status == "busy":
                raise LockError("busy", "active_writer")
            raise
        else:
            _release_fd(fd)
            actions.append("original_guard_probe_free")
    else:
        actions.append("original_lock_not_probeable")
    return actions


def _do_stage_10(root, rdir, ctx):
    lockdir = Path(root) / LOCK_DIR
    target = Path(rdir) / "original-lock"
    original = ctx["original_lock"]["identity"]
    if original is None:
        if os.path.lexists(str(lockdir)):
            raise LockError(
                "blocked", "unexpected_lock_after_claim_none"
            )
        return ["original_lock_absent"]
    if not os.path.lexists(str(lockdir)):
        # The move may have completed physically before a crash left
        # no receipt: prove it by identity, then the receipt is still
        # published so the chain stays complete.
        try:
            got = gg_fs.identity(target, kind="directory")
        except gg_fs.FsError:
            raise LockError("blocked", "quarantine_state_ambiguous")
        if not gg_fs.same_identity(got, original):
            raise LockError("blocked", "quarantine_identity_mismatch")
        return ["original_lock_already_quarantined_verified"]
    src_id = gg_fs.identity(lockdir, kind="directory")
    if not gg_fs.same_identity(src_id, original):
        raise LockError("damaged", "original_lock_identity_changed")
    parent_id = gg_fs.identity(Path(rdir), kind="directory")
    result = gg_fs.rename_noreplace(
        lockdir, target,
        source_identity=src_id,
        source_parent_identity=ctx["detail"]["root_identity"],
        destination_parent_identity=parent_id,
    )
    if result["state"] != "installed":
        raise LockError(
            "unavailable", "quarantine_failed:%s" % result["reason"]
        )
    gg_fs.assert_identity(target, src_id)
    _run_fault("after_quarantine")
    return ["original_lock_quarantined"]


def _do_stage_20(root, rec_cap, ctx):
    keep_ws = (
        ctx["previous_workspace_id"]
        if ctx["disposition"] == "same_workspace_repair"
        else None
    )
    proto = _install_control(
        root, recovery_id=ctx["recovery_id"], workspace_id=keep_ws
    )
    proto_sha = gg_fs.sha256_bytes(gg_fs.canonical_json_bytes(proto))
    detail = _verify_installed_control(root, rec_cap)
    ctx["installed_protocol_sha256"] = proto_sha
    ctx["installed_guard_identity"] = detail["guard_identity"]
    ctx["current_workspace_id"] = proto["workspace_id"]
    ctx["current_protocol_sha256"] = proto_sha
    _run_fault("after_install")
    return ["new_control_installed", "owner_path_verified"]


def _do_stage_30(root, ctx):
    _verify_canonical_unchanged(root, ctx)
    return ["final_inventory_reverified"]


def _finish_cleanup_result(root, rid, rdir, rec_cap, ctx):
    errors = _recovery_cleanup(root, rec_cap, ctx)
    receipt_path = str(Path(rdir) / "30-completed.json")
    receipt_sha = ctx["stage_shas"].get("30-completed")
    preserved = _preserved(root, rdir, Path(root) / LOCK_DIR)
    if errors:
        return _recovery_result(
            rid, "blocked", "committed_cleanup_pending",
            "cleanup_failed", receipt_path, receipt_sha,
            preserved, errors,
        )
    return _recovery_result(
        rid, "completed", "committed", None,
        receipt_path, receipt_sha, preserved, [],
    )


def upgrade_offline(root, *, offline_confirmed, resume_id=None):
    """Explicit offline conversion/recovery (SPEC §5.1). Requires the
    caller's operational confirmation; never a software proof that all
    writers stopped."""
    root = Path(root)
    if not offline_confirmed:
        return _recovery_result(
            resume_id, "blocked", "not_committed",
            "offline_confirmation_required",
        )
    try:
        gg_fs.identity(root, kind="directory")
    except gg_fs.FsError as error:
        return _recovery_result(
            resume_id, "blocked", "not_committed", error.reason,
        )
    if resume_id is None:
        return _recovery_new(root)
    return _recovery_resume(root, resume_id)


def _recovery_new(root):
    rid = str(uuid.uuid4())
    rdir = Path(root) / RECOVERY_DIR / rid
    active = Path(root) / ACTIVE_NAME
    preserved = []
    try:
        if _active_present(root):
            raise LockError("busy", "recovery_active_exists")
        ctx = _recovery_prepare_ctx(root)
        ctx["recovery_id"] = rid
        ctx["stage_shas"] = {}
        ctx["prev_sha"] = None
        # Unique recovery directory: guard + immutable claim first.
        rbase = Path(root) / RECOVERY_DIR
        if not os.path.lexists(str(rbase)):
            gg_fs.mkdir_directory(rbase)
        else:
            gg_fs.identity(rbase, kind="directory")
        gg_fs.mkdir_directory(rdir)
        rdir_id = gg_fs.identity(rdir, kind="directory")
        guard_fd = gg_fs._open_nofollow(
            rdir / GUARD_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        )
        try:
            os.write(guard_fd, b"\x00")
            os.fsync(guard_fd)
            rguard_id = gg_fs._identity_from_stat(
                os.fstat(guard_fd), "file", rdir / GUARD_NAME
            )
        finally:
            os.close(guard_fd)
        claim = {
            "schema": CLAIM_SCHEMA,
            "recovery_id": rid,
            "root_identity": ctx["detail"]["root_identity"],
            "canonical_sha256": ctx["canonical_sha256"],
            "product_inventory_sha256": ctx["inventory_sha256"],
            "original_lock": ctx["original_lock"],
            "recovery_guard_identity": rguard_id,
            "previous_workspace_id": ctx["previous_workspace_id"],
            "previous_protocol_sha256": ctx["previous_protocol_sha256"],
            "disposition": ctx["disposition"],
            "created_at": _utcnow(),
        }
        claim_bytes = gg_fs.canonical_json_bytes(claim)
        ctx["claim"] = claim
        ctx["claim_sha256"] = gg_fs.sha256_bytes(claim_bytes)
        claim_res = gg_fs.publish_bytes_noreplace(
            rdir / "claim.json", claim_bytes, parent_identity=rdir_id
        )
        if claim_res["state"] != "installed":
            raise LockError(
                "unavailable",
                "claim_publish_failed:%s" % claim_res["reason"],
            )
        # Publish the fixed active path with exactly the claim bytes;
        # only a successful no-replace rename may proceed.
        active_res = gg_fs.publish_bytes_noreplace(
            active, claim_bytes,
            parent_identity=ctx["detail"]["root_identity"],
        )
        if active_res["state"] == "not_installed":
            reason = active_res["reason"]
            if reason == "unsupported_no_replace":
                reason = "unsupported_recovery_filesystem"
            raise LockError("unavailable", "active_publish:%s" % reason)
        if active_res["state"] != "installed":
            raise LockError("unavailable", "active_publish_unknown")
        _run_fault("after_active")
        # Hold the recovery guard for the rest of the operation.
        try:
            rfd = _PORT.open_guard(rdir / GUARD_NAME)
            _PORT.lock(rfd)
        except LockError:
            raise
        except OSError as error:
            raise LockError(
                "unavailable", "recovery_guard:%s" % error.errno
            )
        rec_cap = _RecoveryCapability()
        rec_cap.pid = os.getpid()
        rec_cap.root = root
        rec_cap.recovery_id = rid
        rec_cap.recovery_dir = rdir
        rec_cap.recovery_guard_identity = rguard_id
        rec_cap.claim_sha256 = ctx["claim_sha256"]
        rec_cap.active_identity = active_res["identity"]
        rec_cap.released = False
        rec_cap._fd = rfd
        _RECOVERY_CAPS[id(rec_cap)] = rec_cap
        _recovery_stages(root, rid, rdir, rec_cap, ctx, "00-prepared")
        return _finish_cleanup_result(root, rid, rdir, rec_cap, ctx)
    except LockError as error:
        preserved = _preserved(root, rdir, active)
        committed = _commit_point_verified(root, rdir)
        if committed is True:
            return _recovery_result(
                rid, "blocked", "committed_cleanup_pending",
                error.reason, preserved_paths=preserved,
            )
        if committed is None:
            return _recovery_result(
                rid, "blocked", "indeterminate",
                "commit_state_unverifiable:%s" % error.reason,
                preserved_paths=preserved,
            )
        return _recovery_result(
            rid, "blocked", "not_committed", error.reason,
            preserved_paths=preserved,
        )
    except (OSError, gg_fs.FsError) as error:
        preserved = _preserved(root, rdir, active)
        return _recovery_result(
            rid, "blocked", "not_committed",
            getattr(error, "reason", "os_error:%s" % error),
            preserved_paths=preserved,
        )


def _commit_point_verified(root, rdir):
    """True: valid 30-completed + matching new control. False: clearly
    pre-commit. None: cannot determine."""
    try:
        rdir = Path(rdir)
        receipt, _sha = _read_stage(rdir, "30-completed")
        status, _detail = _inspect(root)
        proto = status["protocol"] or {}
        ok = (
            status["structure"] == "ready"
            and proto.get("recovery_id") == receipt.get("recovery_id")
            and proto.get("workspace_id")
            == receipt.get("current_workspace_id")
        )
        return True if ok else None
    except (OSError, ValueError, gg_fs.FsError, LockError):
        return False


def _validate_chain(root, rdir, claim, claim_sha256):
    """Verify the present stage receipts form an untampered chain
    anchored to the claim. Returns (receipts_by_stage, last_stage)."""
    receipts = {}
    prev = None
    for stage in STAGES:
        path = Path(rdir) / (stage + ".json")
        if not os.path.lexists(str(path)):
            break
        try:
            receipt, sha = _read_stage(rdir, stage)
        except (OSError, ValueError):
            raise LockError("damaged", "stage_receipt_unreadable")
        if receipt.get("schema") != STAGE_SCHEMA:
            raise LockError("damaged", "stage_schema")
        if receipt.get("recovery_id") != claim["recovery_id"]:
            raise LockError("damaged", "stage_recovery_id_mismatch")
        if receipt.get("stage") != stage:
            raise LockError("damaged", "stage_name_mismatch")
        if receipt.get("previous_sha256") != prev:
            raise LockError("damaged", "stage_chain_broken")
        for key in (
            "claim_sha256", "canonical_sha256",
            "product_inventory_sha256", "original_lock_identity",
            "previous_workspace_id", "previous_protocol_sha256",
            "disposition",
        ):
            expected = (
                claim_sha256 if key == "claim_sha256"
                else claim["original_lock"]["identity"]
                if key == "original_lock_identity"
                else claim.get(key)
            )
            if receipt.get(key) != expected:
                raise LockError("damaged", "stage_field_mismatch:%s" % key)
        if stage in ("20-installed", "30-completed"):
            if (
                receipt.get("installed_protocol_sha256")
                != receipt.get("current_protocol_sha256")
                or receipt.get("current_workspace_id") is None
            ):
                raise LockError("damaged", "stage_install_fields")
        receipts[stage] = (receipt, sha)
        prev = sha
    last = next(
        (s for s in reversed(STAGES) if s in receipts), None
    )
    return receipts, last


def _recovery_resume(root, resume_id):
    root = Path(root)
    rdir = Path(root) / RECOVERY_DIR / resume_id
    active = Path(root) / ACTIVE_NAME
    try:
        if not _active_present(root):
            if _completed_chain(root, rdir, resume_id):
                receipt, sha = _read_stage(rdir, "30-completed")
                return _recovery_result(
                    resume_id, "already_completed", "committed", None,
                    str(rdir / "30-completed.json"), sha,
                    _preserved(root, rdir, Path(root) / LOCK_DIR), [],
                )
            raise LockError("blocked", "no_active_recovery")
        data, active_id = gg_fs.read_bytes(active)
        active_sha = gg_fs.sha256_bytes(data)
        try:
            claim = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LockError("blocked", "active_corrupt")
        if not isinstance(claim, dict) or claim.get("schema") != CLAIM_SCHEMA:
            raise LockError("blocked", "active_corrupt")
        if claim.get("recovery_id") != resume_id:
            raise LockError("blocked", "recovery_id_mismatch")
        for key in (
            "root_identity", "canonical_sha256",
            "product_inventory_sha256", "original_lock",
            "recovery_guard_identity", "previous_workspace_id",
            "previous_protocol_sha256", "disposition",
        ):
            if key not in claim:
                raise LockError("blocked", "active_corrupt")
        try:
            cdata, _cid = gg_fs.read_bytes(rdir / "claim.json")
        except OSError:
            raise LockError("blocked", "claim_missing")
        if gg_fs.sha256_bytes(cdata) != active_sha:
            raise LockError("blocked", "claim_mismatch")
        # Exclusive resume: same recovery guard, nonblocking.
        try:
            rguard_id = gg_fs.identity(rdir / GUARD_NAME, kind="file")
        except gg_fs.FsError:
            raise LockError("blocked", "recovery_guard_missing")
        if not gg_fs.same_identity(
                rguard_id, claim["recovery_guard_identity"]):
            raise LockError("blocked", "recovery_guard_mismatch")
        try:
            rfd = _PORT.open_guard(rdir / GUARD_NAME)
            _PORT.lock(rfd)
        except LockError as error:
            raise LockError("blocked", "recovery_guard_%s" % error.reason)
        except OSError as error:
            raise LockError(
                "blocked", "recovery_guard_busy:%s" % error.errno
            )
        ctx = _recovery_prepare_ctx_for_resume(root, claim)
        ctx["recovery_id"] = resume_id
        ctx["claim"] = claim
        ctx["claim_sha256"] = active_sha
        ctx["stage_shas"] = {}
        receipts, last = _validate_chain(root, rdir, claim, active_sha)
        ctx["prev_sha"] = receipts[last][1] if last else None
        for stage, (_r, sha) in receipts.items():
            ctx["stage_shas"][stage] = sha
        if last in ("20-installed", "30-completed"):
            inst = receipts[last][0]
            ctx["installed_protocol_sha256"] = inst[
                "installed_protocol_sha256"]
            ctx["installed_guard_identity"] = inst[
                "installed_guard_identity"]
            ctx["current_workspace_id"] = inst["current_workspace_id"]
            ctx["current_protocol_sha256"] = inst[
                "current_protocol_sha256"]
        rec_cap = _RecoveryCapability()
        rec_cap.pid = os.getpid()
        rec_cap.root = root
        rec_cap.recovery_id = resume_id
        rec_cap.recovery_dir = rdir
        rec_cap.recovery_guard_identity = rguard_id
        rec_cap.claim_sha256 = active_sha
        rec_cap.active_identity = active_id
        rec_cap.released = False
        rec_cap._fd = rfd
        _RECOVERY_CAPS[id(rec_cap)] = rec_cap
        # Determine real progress — receipts alone never prove a step.
        start = _resume_start(root, rdir, receipts, last, ctx)
        if last == "30-completed":
            # Completion already committed: verify the new control
            # still matches the receipt, then only clean our active.
            _verify_new_control_for_resume(root, rec_cap, ctx)
            return _finish_cleanup_result(
                root, resume_id, rdir, rec_cap, ctx
            )
        _recovery_stages(root, resume_id, rdir, rec_cap, ctx, start)
        return _finish_cleanup_result(root, resume_id, rdir, rec_cap, ctx)
    except LockError as error:
        preserved = _preserved(root, rdir, active)
        committed = _commit_point_verified(root, rdir)
        if committed is True:
            return _recovery_result(
                resume_id, "blocked", "committed_cleanup_pending",
                error.reason, preserved_paths=preserved,
            )
        if committed is None:
            return _recovery_result(
                resume_id, "blocked", "indeterminate",
                "commit_state_unverifiable:%s" % error.reason,
                preserved_paths=preserved,
            )
        return _recovery_result(
            resume_id, "blocked", "not_committed", error.reason,
            preserved_paths=preserved,
        )
    except (OSError, gg_fs.FsError) as error:
        preserved = _preserved(root, rdir, active)
        return _recovery_result(
            resume_id, "blocked", "not_committed",
            getattr(error, "reason", "os_error:%s" % error),
            preserved_paths=preserved,
        )


def _completed_chain(root, rdir, resume_id):
    try:
        receipts, last = _validate_chain_from_disk(rdir, resume_id)
        return last == "30-completed"
    except Exception:
        return False


def _validate_chain_from_disk(rdir, resume_id):
    try:
        cdata, _cid = gg_fs.read_bytes(Path(rdir) / "claim.json")
        claim = json.loads(cdata.decode("utf-8"))
        if claim.get("recovery_id") != resume_id:
            return {}, None
        return _validate_chain(
            Path(rdir).parent.parent, rdir, claim,
            gg_fs.sha256_bytes(cdata),
        )
    except Exception:
        return {}, None


def _recovery_prepare_ctx_for_resume(root, claim):
    items = _product_inventory(root)
    return {
        "status": {"structure": "resumed"},
        "detail": {"root_identity": claim["root_identity"]},
        "disposition": claim["disposition"],
        "previous_workspace_id": claim["previous_workspace_id"],
        "previous_protocol_sha256": claim["previous_protocol_sha256"],
        "inventory": items,
        "inventory_sha256": _inventory_sha256(items),
        "canonical_sha256": claim["canonical_sha256"],
        "original_lock": claim["original_lock"],
    }


def _resume_start(root, rdir, receipts, last, ctx):
    """Map verified receipt progress + real on-disk state to the next
    stage. Mere existence never proves completion; ambiguous state is
    preserved and reported blocked."""
    root = Path(root)
    rdir = Path(rdir)
    lockdir = root / LOCK_DIR
    original = ctx["original_lock"]["identity"]
    if last is None:
        return "00-prepared"
    if last == "00-prepared":
        quarantined = rdir / "original-lock"
        lock_present = os.path.lexists(str(lockdir))
        quar_present = os.path.lexists(str(quarantined))
        if original is None:
            if lock_present or quar_present:
                raise LockError(
                    "blocked", "unexpected_lock_after_claim_none"
                )
            return "10-quarantined"
        if lock_present and not quar_present:
            try:
                got = gg_fs.identity(lockdir, kind="directory")
            except gg_fs.FsError:
                raise LockError("blocked", "original_lock_unreadable")
            if gg_fs.same_identity(got, original):
                return "10-quarantined"
            raise LockError("blocked", "original_lock_identity_changed")
        if quar_present and not lock_present:
            try:
                got = gg_fs.identity(quarantined, kind="directory")
            except gg_fs.FsError:
                raise LockError("blocked", "quarantine_unreadable")
            if gg_fs.same_identity(got, original):
                # Physical move done, receipt missing: stage 10
                # re-verifies identity and still publishes its
                # receipt so the chain stays complete.
                return "10-quarantined"
            raise LockError("blocked", "quarantine_identity_mismatch")
        raise LockError("blocked", "quarantine_state_ambiguous")
    if last == "10-quarantined":
        lock_present = os.path.lexists(str(lockdir))
        quar = rdir / "original-lock"
        if original is not None:
            try:
                got = gg_fs.identity(quar, kind="directory")
                if not gg_fs.same_identity(got, original):
                    raise LockError(
                        "blocked", "quarantine_identity_mismatch"
                    )
            except gg_fs.FsError:
                raise LockError("blocked", "quarantine_unreadable")
        if not lock_present:
            return "20-installed"
        # .gg-lock exists post-quarantine: either a crashed install of
        # this recovery or a foreign object. A complete valid protocol
        # naming this recovery is sufficient ownership proof to adopt;
        # an EMPTY directory is safe to remove; anything else is
        # preserved and reported blocked.
        proto = None
        try:
            proto, _pb, _pi = _read_protocol(lockdir)
        except (LockError, gg_fs.FsError, OSError):
            proto = None
        if proto is not None:
            if proto.get("recovery_id") == ctx["recovery_id"]:
                proto_sha = gg_fs.sha256_bytes(
                    gg_fs.canonical_json_bytes(proto)
                )
                try:
                    guard_id = gg_fs.identity(
                        lockdir / GUARD_NAME, kind="file"
                    )
                except gg_fs.FsError:
                    raise LockError("blocked", "install_guard_unproven")
                ctx["installed_protocol_sha256"] = proto_sha
                ctx["installed_guard_identity"] = guard_id
                ctx["current_workspace_id"] = proto["workspace_id"]
                ctx["current_protocol_sha256"] = proto_sha
                return "20-installed_verify"
            raise LockError("blocked", "foreign_lock_present")
        try:
            if os.listdir(str(lockdir)):
                raise LockError("blocked", "install_ownership_unproven")
            os.rmdir(str(lockdir))
            return "20-installed"
        except LockError:
            raise
        except OSError:
            raise LockError("blocked", "install_ownership_unproven")
    if last == "20-installed":
        return "30-completed_verify"
    if last == "30-completed":
        return "30-completed"
    raise LockError("blocked", "resume_state_unknown")


def _recovery_stages(root, rid, rdir, rec_cap, ctx, start_stage):
    rdir_id = gg_fs.identity(rdir, kind="directory")
    if start_stage == "20-installed_verify":
        _verify_new_control_for_resume(root, rec_cap, ctx)
        actions = ["new_control_verified"]
        receipt = _stage_receipt(
            rid, "20-installed", ctx["prev_sha"], ctx, actions,
            "30-completed",
        )
        ctx["prev_sha"] = _issue_stage(rdir, rdir_id, receipt)
        ctx["stage_shas"]["20-installed"] = ctx["prev_sha"]
        start_stage = "30-completed"
    if start_stage == "30-completed_verify":
        _verify_new_control_for_resume(root, rec_cap, ctx)
        actions = _do_stage_30(root, ctx)
        receipt = _stage_receipt(
            rid, "30-completed", ctx["prev_sha"], ctx, actions, None
        )
        ctx["prev_sha"] = _issue_stage(rdir, rdir_id, receipt)
        ctx["stage_shas"]["30-completed"] = ctx["prev_sha"]
        return
    order = list(STAGES)
    for stage in order[order.index(start_stage):]:
        if stage == "00-prepared":
            actions = _do_stage_00(root, ctx)
            nxt = "10-quarantined"
        elif stage == "10-quarantined":
            actions = _do_stage_10(root, rdir, ctx)
            nxt = "20-installed"
        elif stage == "20-installed":
            actions = _do_stage_20(root, rec_cap, ctx)
            nxt = "30-completed"
        else:
            actions = _do_stage_30(root, ctx)
            nxt = None
        receipt = _stage_receipt(
            rid, stage, ctx["prev_sha"], ctx, actions, nxt
        )
        ctx["prev_sha"] = _issue_stage(rdir, rdir_id, receipt)
        ctx["stage_shas"][stage] = ctx["prev_sha"]
        _run_fault("after_" + stage)


def _verify_new_control_for_resume(root, rec_cap, ctx):
    status, detail = _inspect(root)
    proto = status["protocol"] or {}
    if status["structure"] != "ready":
        raise LockError("blocked", "new_control_invalid")
    if status["reason"] is not None:
        if status["reason"] != "recovery_active":
            raise LockError("blocked", status["reason"])
        rid, _ar = _read_active(root)
        if rid != rec_cap.recovery_id:
            raise LockError("blocked", "active_recovery_id_mismatch")
    if proto.get("recovery_id") != ctx["recovery_id"]:
        raise LockError("blocked", "new_protocol_recovery_id_mismatch")
    proto_sha = gg_fs.sha256_bytes(
        gg_fs.canonical_json_bytes(proto)
    )
    if proto_sha != ctx["installed_protocol_sha256"]:
        raise LockError("blocked", "installed_protocol_mismatch")
    fd = _acquire_guard(root, detail)
    rec_cap._new_fd = fd
    rec_cap.new_guard_identity = detail["guard_identity"]
    return detail


# --------------------------------------------------- recovery lineage


def verify_recovery_lineage(
    root,
    *,
    previous_workspace_id,
    previous_protocol_sha256,
    current_workspace_id,
    current_protocol_sha256,
):
    """Read-only verification that the completed recovery chain is the
    unique path connecting previous -> current protocol endpoints with
    same_workspace_repair disposition. No guard acquire, no writes."""
    root = Path(root)
    result = {
        "trusted": False,
        "reason": None,
        "previous_workspace_id": previous_workspace_id,
        "previous_protocol_sha256": previous_protocol_sha256,
        "current_workspace_id": current_workspace_id,
        "current_protocol_sha256": current_protocol_sha256,
        "recovery_ids": [],
        "receipt_sha256s": [],
    }
    try:
        status, detail = _inspect(root)
    except gg_fs.FsError as error:
        result["reason"] = error.reason
        return result
    proto = status["protocol"] or {}
    if status["structure"] != "ready" or status["reason"] is not None:
        result["reason"] = "current_control_invalid"
        return result
    if proto.get("workspace_id") != current_workspace_id:
        result["reason"] = "current_workspace_id_mismatch"
        return result
    if detail.get("protocol_sha256") != current_protocol_sha256:
        result["reason"] = "current_protocol_sha256_mismatch"
        return result
    if _active_present(root):
        result["reason"] = "recovery_active"
        return result
    if (previous_workspace_id, previous_protocol_sha256) == (
            current_workspace_id, current_protocol_sha256):
        result["trusted"] = True
        return result

    edges = []
    rbase = root / RECOVERY_DIR
    if os.path.isdir(str(rbase)) and not os.path.islink(str(rbase)):
        for sub in sorted(os.listdir(str(rbase))):
            rdir = rbase / sub
            if not os.path.isdir(str(rdir)) or os.path.islink(str(rdir)):
                continue
            edge = _chain_edge(rdir)
            if edge is not None:
                edges.append(edge)

    start = (previous_workspace_id, previous_protocol_sha256)
    goal = (current_workspace_id, current_protocol_sha256)
    frontier = [(start, [], [])]
    visited = set()
    found = None
    while frontier:
        node, rids, shas = frontier.pop()
        if node in visited:
            continue
        visited.add(node)
        outgoing = [
            e for e in edges
            if (e["previous_workspace_id"],
                e["previous_protocol_sha256"]) == node
        ]
        if len({(e["current_workspace_id"],
                 e["current_protocol_sha256"]) for e in outgoing}) > 1:
            result["reason"] = "chain_forked"
            return result
        for edge in outgoing:
            if edge["disposition"] != "same_workspace_repair":
                result["reason"] = "disposition_untrusted:%s" % edge[
                    "disposition"]
                return result
            nxt = (edge["current_workspace_id"],
                   edge["current_protocol_sha256"])
            nrids = rids + [edge["recovery_id"]]
            nshas = shas + edge["receipt_sha256s"]
            if nxt == goal:
                found = (nrids, nshas)
                frontier = []
                break
            if nxt in visited or nxt == node:
                result["reason"] = "chain_cycle"
                return result
            frontier.append((nxt, nrids, nshas))
    if found is None:
        result["reason"] = "chain_missing"
        return result
    result["trusted"] = True
    result["recovery_ids"] = found[0]
    result["receipt_sha256s"] = found[1]
    return result


def _chain_edge(rdir):
    """Validate a full claim+4-stage chain on disk; return its endpoint
    edge or None when the chain is incomplete/unreadable (incomplete
    recovery dirs are not links)."""
    try:
        cdata, _cid = gg_fs.read_bytes(Path(rdir) / "claim.json")
        claim = json.loads(cdata.decode("utf-8"))
        if not isinstance(claim, dict) or claim.get(
                "schema") != CLAIM_SCHEMA:
            return None
        claim_sha = gg_fs.sha256_bytes(cdata)
        receipts, last = _validate_chain(
            Path(rdir).parent.parent, rdir, claim, claim_sha
        )
        if last != "30-completed":
            return None
        final = receipts["30-completed"][0]
        return {
            "recovery_id": claim["recovery_id"],
            "previous_workspace_id": claim["previous_workspace_id"],
            "previous_protocol_sha256": claim[
                "previous_protocol_sha256"],
            "current_workspace_id": final["current_workspace_id"],
            "current_protocol_sha256": final[
                "current_protocol_sha256"],
            "disposition": claim["disposition"],
            "receipt_sha256s": [receipts[s][1] for s in STAGES],
        }
    except (OSError, ValueError, LockError, gg_fs.FsError):
        return None
