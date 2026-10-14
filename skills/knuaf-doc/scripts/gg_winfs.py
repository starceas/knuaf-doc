"""Native Windows local-filesystem primitives (Python 3.10+).

No POSIX emulation. Parent directory handles deny delete sharing while a
path operation runs, allowing child creation/rename. All reparse points are
refused, including junctions.
The caller still owns the cooperative workspace guard. No directory
power-loss durability, network/cloud filesystem or hostile-writer guarantee.
"""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys

_API = None
_REPARSE = 0x400
_DIRECTORY = 0x10
_READ = 0x80000000
_WRITE = 0x40000000
_DELETE = 0x10000
_ATTR = 0x80
_OPEN_REPARSE = 0x00200000
_BACKUP = 0x02000000


class _Info(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD),
                ("creation", wintypes.FILETIME),
                ("access", wintypes.FILETIME),
                ("write", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]


class _RenameInfo(ctypes.Structure):
    # Explicit Win32 DWORD/WCHAR widths also allow local layout checks.
    _fields_ = [("ReplaceIfExists", ctypes.c_uint32),
                ("RootDirectory", ctypes.c_void_p),
                ("FileNameLength", ctypes.c_uint32),
                ("FileName", ctypes.c_uint16 * 1)]


def _api():
    global _API
    if sys.platform != "win32":
        raise OSError("native Windows required")
    if _API is None:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE,
                                                   ctypes.POINTER(_Info)]
        api.GetFileInformationByHandle.restype = wintypes.BOOL
        api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE,
                                                   ctypes.c_int,
                                                   ctypes.c_void_p,
                                                   wintypes.DWORD]
        api.SetFileInformationByHandle.restype = wintypes.BOOL
        _API = api
    return _API


def supported():
    if sys.platform != "win32":
        return False
    try:
        _api()
        import msvcrt  # noqa: F401
        return True
    except (OSError, AttributeError, ImportError):
        return False


def validate_component(part):
    """Reject Win32 aliases, ADS, device names and normalization escapes."""
    if (not part or part in (".", "..") or part[-1:] in (" ", ".")
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in part)):
        raise ValueError("unsupported Windows path component: " + repr(part))
    stem = part.split(".")[0].rstrip(" ").upper()
    if (stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)):
        raise ValueError("Windows device name: " + part)


def _absolute(path):
    raw = os.fspath(path)
    # Device namespaces and network shares are outside this port's scope.
    if raw.startswith(("\\\\", "//")):
        raise ValueError("only local drive paths are supported")
    lexical = PureWindowsPath(raw)
    if lexical.drive and not lexical.is_absolute():
        raise ValueError("drive-relative Windows path is not supported")
    # Windows abspath/GetFullPathName can erase a trailing dot/space. Check
    # supplied components first, before normalization or any OS operation.
    for part in lexical.parts[1:] if lexical.anchor else lexical.parts:
        validate_component(part)
    p = Path(os.path.abspath(raw))
    if not p.is_absolute() or not re.fullmatch(r"[A-Za-z]:\\", p.anchor):
        raise ValueError("local absolute Windows path required")
    for part in p.parts[1:]:
        validate_component(part)
    return p


def _native(path):
    return "\\\\?\\" + str(path)


def _close(handle):
    if not _api().CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _info(handle, kind):
    if kind not in ("file", "directory"):
        raise ValueError("kind must be file or directory")
    info = _Info()
    if not _api().GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if info.attributes & _REPARSE:
        raise OSError("symlink_reparse")
    is_dir = bool(info.attributes & _DIRECTORY)
    if is_dir != (kind == "directory"):
        raise OSError("object kind mismatch")
    inode = (info.index_high << 32) | info.index_low
    if not inode:
        raise OSError("identity_unavailable")
    return {"platform": "win32", "kind": kind,
            "device": info.volume, "inode": inode}


def _open(path, access, creation, kind, share=1):
    api = _api()
    handle = api.CreateFileW(_native(path), access, share, None, creation,
                             _OPEN_REPARSE | _BACKUP, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _info(handle, kind)
    except BaseException:
        _close(handle)
        raise
    return handle


class Directory:
    """Pinned no-reparse component chain; close releases all handles."""
    def __init__(self, path, *, create=False):
        self.path = _absolute(path)
        self.handles = []
        self.created = []
        try:
            current = Path(self.path.anchor)
            self.handles.append(_open(current, _READ, 3, "directory", share=3))
            for part in self.path.parts[1:]:
                current = current / part
                try:
                    handle = _open(current, _READ, 3, "directory", share=3)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(_native(current))
                        self.created.append(current)
                    except FileExistsError:
                        pass
                    handle = _open(current, _READ, 3, "directory", share=3)
                self.handles.append(handle)
        except BaseException:
            self.close()
            raise

    def close(self):
        while self.handles:
            _close(self.handles.pop())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def identity(path, kind):
    p = _absolute(path)
    if kind == "directory":
        with Directory(p) as directory:
            return _info(directory.handles[-1], kind)
    with Directory(p.parent):
        # Metadata observation must coexist with an already open WRITE
        # guard. Share1 here would reject that live handle's access.
        # Payload/owner readers below still deny write/delete sharing.
        handle = _open(p, _ATTR, 3, kind, share=7)
        try:
            return _info(handle, kind)
        finally:
            _close(handle)


def fd_identity(fd, kind):
    import msvcrt
    return _info(msvcrt.get_osfhandle(fd), kind)


def rename_directory_noreplace(src, dst, *, source_identity):
    """Rename the verified directory handle, keeping descendant guards live.

    Called only after a real Windows path rename reports access/sharing
    failure. Caller pins both parents and performs its existing postcheck.
    No replacement flag, guard unlock, copy or delete fallback.
    """
    src, dst = _absolute(src), _absolute(dst)
    handle = _open(src, _READ | _DELETE, 3, "directory", share=7)
    try:
        if _info(handle, "directory") != source_identity:
            raise OSError("source identity changed before handle rename")
        name = str(dst).encode("utf-16-le")
        offset = _RenameInfo.FileName.offset
        size = max(ctypes.sizeof(_RenameInfo), offset + len(name) + 2)
        buffer = ctypes.create_string_buffer(size)
        info = _RenameInfo.from_buffer(buffer)
        info.ReplaceIfExists = 0
        info.RootDirectory = None
        info.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + offset, name, len(name))
        if not _api().SetFileInformationByHandle(handle, 3, buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _close(handle)


def open_file(path, flags, *, share_delete=False, delete_access=False):
    """Return a binary CRT fd owning a verified native handle."""
    import msvcrt
    p = _absolute(path)
    access_mode = flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
    access = (_READ | _WRITE if access_mode == os.O_RDWR else
              _WRITE if access_mode == os.O_WRONLY else _READ)
    if delete_access:
        access |= _DELETE
    if flags & os.O_CREAT:
        if not flags & os.O_EXCL:
            raise ValueError("Windows file creation requires O_EXCL")
        creation = 1  # CREATE_NEW
    else:
        creation = 3  # OPEN_EXISTING
    if flags & os.O_TRUNC:
        raise ValueError("Windows truncation is not supported")
    with Directory(p.parent):
        # Guard must share write for other lockers and delete for import move.
        share = 7 if share_delete else 1
        handle = _open(p, access, creation, "file", share)
    try:
        # CreateFileW controls access. Use the documented CRT wrapping
        # flags, then set binary translation explicitly (including Ctrl-Z).
        fd = msvcrt.open_osfhandle(handle, 0)
    except BaseException:
        _close(handle)
        raise
    try:
        msvcrt.setmode(fd, os.O_BINARY)
        return fd
    except BaseException:
        os.close(fd)  # ownership already transferred to the CRT fd
        raise


def lstat(path):
    p = _absolute(path)
    with Directory(p.parent):
        st = os.lstat(_native(p))
        if getattr(st, "st_file_attributes", 0) & _REPARSE:
            raise OSError("symlink_reparse")
        return st


def read_bytes(path):
    fd = open_file(path, os.O_RDONLY)
    try:
        ident = fd_identity(fd, "file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(), ident
    finally:
        os.close(fd)


def unlink_file(path, *, expected_identity=None, expected_record=None):
    """Delete the exact opened object, never a subsequently replaced name."""
    import json
    p = _absolute(path)
    with Directory(p.parent):
        fd = open_file(p, os.O_RDONLY, delete_access=True)
        try:
            if expected_identity is not None and fd_identity(fd, "file") != expected_identity:
                raise OSError("identity_mismatch")
            if expected_record is not None:
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    data = handle.read()
                    handle.seek(0)
                    if handle.read() != data:
                        raise OSError("owner record changed")
                if json.loads(data.decode("utf-8")) != expected_record:
                    raise OSError("owner record is not ours")
            delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO BOOLEAN
            import msvcrt
            if not _api().SetFileInformationByHandle(
                    msvcrt.get_osfhandle(fd), 4, ctypes.byref(delete), 1):
                raise ctypes.WinError(ctypes.get_last_error())
        except (UnicodeDecodeError, ValueError) as error:
            raise OSError("owner record corrupt") from error
        finally:
            os.close(fd)


def names(path):
    with Directory(path) as directory:
        return sorted(os.listdir(_native(directory.path)), key=lambda n: n.encode("utf-8"))


def scan_tree(base, *, drop_top_dirs=(), drop_root_files=()):
    out = []
    base = _absolute(base)

    def scan(directory, prefix):
        with Directory(directory):
            for name in names(directory):
                validate_component(name)
                child = directory / name
                st = lstat(child)
                rel = prefix + "/" + name if prefix else name
                if not prefix and name in drop_root_files:
                    if not stat.S_ISREG(st.st_mode):
                        raise OSError("excluded file is not regular")
                    continue
                if not prefix and name in drop_top_dirs:
                    identity(child, "directory")
                    continue
                if stat.S_ISDIR(st.st_mode):
                    scan(child, rel)
                elif stat.S_ISREG(st.st_mode):
                    data, _ident = read_bytes(child)
                    out.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(),
                                "size": len(data)})
                else:
                    raise OSError("not a regular file/directory: " + rel)
    scan(base, "")
    return out
