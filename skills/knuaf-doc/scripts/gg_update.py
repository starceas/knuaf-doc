"""knuaf-doc 업데이트 확인·적용·채택·복구 (설계 U1 r3, 11.7.1 우선).

표준 라이브러리만 쓰고 이 파일 하나만으로 동작한다 — 교체 과정에서
state_dir/bin/ 아래에 복사돼 0.1.0 같은 구 설치본의 복구 명령으로도
실행되기 때문이다(11.3).  네트워크는 http.client.HTTPSConnection 한
번, 리다이렉트 미추종, check는 본문을 읽지 않는다.  출력은 stdout에
JSON 한 개.  argparse 사용 오류만 exit 2.
"""
import argparse
import errno
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
import tempfile
import threading
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

REPO = "starceas/knuaf-doc"
CHECK_HOST = "github.com"
CHECK_PATH = "/starceas/knuaf-doc/releases/latest"
DOWNLOAD_HOST = "codeload.github.com"
DOWNLOAD_PATH_FMT = "/starceas/knuaf-doc/zip/refs/tags/v%s"
RELEASE_URL_FMT = "https://github.com/starceas/knuaf-doc/releases/tag/v%s"
USER_AGENT = "knuaf-doc-update/1"

VERSION_SCHEMA = "knuaf-doc/version@1"
JOURNAL_SCHEMA = "knuaf-doc/update-journal@1"

VERSION_RE = re.compile(
    r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z",
    re.ASCII)
TAG_PATH_RE = re.compile(
    r"/starceas/knuaf-doc/releases/tag/v"
    r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z",
    re.ASCII)

CHECK_DEADLINE = 3.0
DOWNLOAD_DEADLINE = 60.0
DOWNLOAD_MAX = 32 * 1024 * 1024
ZIP_MAX_ENTRIES = 4000
ZIP_NAME_MAX = 240
ZIP_FILE_MAX = 32 * 1024 * 1024
ZIP_TOTAL_MAX = 128 * 1024 * 1024
LOCATION_MAX = 256

SKILL_NAME = "knuaf-doc"
STAGING_PREFIX = ".knuaf-doc-staging-"
STATE_DIR_NAME = "knuaf-doc-update"


# ---------------------------------------------------------------- results --

class Refused(Exception):
    """교체 전 안전 검사에서 거절. 대상 파일·외부 경로는 그대로다."""

    def __init__(self, reason, **extra):
        super().__init__(reason)
        self.reason = reason
        self.extra = extra


class Failed(Exception):
    """교체 도중 실패. state는 실제 디스크 상태를 말한다."""

    def __init__(self, stage, state, **extra):
        super().__init__("%s/%s" % (stage, state))
        self.stage = stage
        self.state = state
        self.extra = extra


class _Offline(Exception):
    pass


class VersionUnreadable(Exception):
    pass


def _refused(exc):
    out = {"status": "refused", "reason": exc.reason}
    out.update(exc.extra)
    return out


def _failed(exc):
    out = {"status": "failed", "stage": exc.stage, "state": exc.state}
    out.update(exc.extra)
    return out


# ----------------------------------------------------------------- common --

def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _nonce():
    return secrets.token_hex(8)


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _rename(src, dst):
    """모든 교체 rename이 지나는 한 지점 — 시험 절단점 훅."""
    os.rename(src, dst)


def _is_link_or_reparse(path):
    try:
        st = os.lstat(str(path))
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(reparse and attrs & reparse)


def _has_link_components(path):
    p = Path(path)
    if _is_link_or_reparse(p):
        return True
    return any(_is_link_or_reparse(parent) for parent in p.parents)


def _run_deadline(fn, deadline):
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — 결과를 옮긴다
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise _Offline()
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- version --

def parse_version(text):
    """엄격 버전 문법 → (major, minor, patch) 튜플, 아니면 None."""
    if not isinstance(text, str):
        return None
    m = VERSION_RE.match(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def format_version(triple):
    return "%d.%d.%d" % triple


def read_version_file(path):
    """version.json 엄격 판독. 정확히 3키·schema·repo·버전 문법."""
    st = os.lstat(str(path))
    if not stat.S_ISREG(st.st_mode) or _is_link_or_reparse(path):
        raise VersionUnreadable()
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise VersionUnreadable()  # FileNotFoundError·OSError는 통과

    def _pairs(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise VersionUnreadable()
            obj[k] = v
        return obj

    try:
        doc = json.loads(raw, object_pairs_hook=_pairs)
    except (json.JSONDecodeError, VersionUnreadable):
        raise VersionUnreadable()
    if not isinstance(doc, dict) or set(doc) != {"schema", "version", "repo"}:
        raise VersionUnreadable()
    if doc["schema"] != VERSION_SCHEMA or doc["repo"] != REPO:
        raise VersionUnreadable()
    version = parse_version(doc["version"])
    if version is None:
        raise VersionUnreadable()
    return version


def _try_version(root):
    """→ (triple|None, 'version'|'legacy'|'invalid')."""
    try:
        return read_version_file(Path(root) / "version.json"), "version"
    except FileNotFoundError:
        return None, "legacy"
    except (OSError, VersionUnreadable):
        return None, "invalid"


def skill_root():
    """이 스크립트의 스킬 루트. 심볼릭 링크 경유 실행이면 거절."""
    here_abs = os.path.abspath(__file__)
    here_real = os.path.realpath(__file__)
    if here_abs != here_real:
        raise Refused("symlinked_path")
    return Path(here_real).parents[1]


def classify_install(root):
    """install_kind 우선순위: plugin_cache → git → copy → unknown."""
    parts = [p.lower() for p in Path(root).parts]
    for i in range(len(parts) - 1):
        if parts[i] == "plugins" and parts[i + 1] == "cache":
            return "plugin_cache"
    p = Path(root)
    while True:
        if os.path.lexists(str(p / ".git")):
            return "git"
        if p.parent == p:
            break
        p = p.parent
    if Path(root).parent.name == "skills":
        return "copy"
    return "unknown"


def _frontmatter_name(path):
    """SKILL.md frontmatter의 name. 없거나 읽기 실패면 None."""
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return None
            for _ in range(200):
                line = fh.readline()
                if not line or line.strip() == "---":
                    return None
                if line.startswith("name:"):
                    value = line.split(":", 1)[1].strip()
                    return value.strip("'").strip('"')
    except (OSError, UnicodeDecodeError):
        return None
    return None


# ------------------------------------------------------------------ check --

def _default_connect(host, timeout):
    return http.client.HTTPSConnection(host, timeout=timeout)


def _check_location(loc):
    """검증된 releases path를 돌려준다. 아니면 None."""
    if not loc or len(loc) > LOCATION_MAX or not loc.isascii():
        return None
    if any(c in loc for c in (" ", "%", "\\", "?", "#")):
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in loc):
        return None
    if loc.startswith("https://"):
        rest = loc[len("https://"):]
        hostpart, sep, path = rest.partition("/")
        if not sep or not path:
            return None
        if hostpart.lower() != "github.com" or ":" in hostpart \
                or "@" in hostpart or hostpart.endswith("."):
            return None
        return "/" + path
    if loc.startswith("/") and not loc.startswith("//"):
        return loc
    return None


def check_status(*, connect=None, deadline=CHECK_DEADLINE, local_root=None):
    """releases/latest 한 번 보고 상태를 판정한다. 파일을 쓰지 않는다."""
    connect = connect or _default_connect
    try:
        root = skill_root() if local_root is None else Path(local_root)
        local, _local_state = _try_version(root)
        kind = classify_install(root)
    except Exception:
        local, kind = None, "unknown"

    out = {"status": "unknown",
           "local_version": format_version(local) if local else None,
           "latest_version": None, "release_url": None,
           "install_kind": kind, "reason": None}

    def fetch():
        conn = connect(CHECK_HOST, timeout=deadline)
        conn.request("GET", CHECK_PATH, headers={"User-Agent": USER_AGENT})
        resp = conn.getresponse()
        locs = [v for k, v in resp.getheaders() if k.lower() == "location"]
        return resp.status, locs

    try:
        status, locs = _run_deadline(fetch, deadline)
    except Exception:
        out["status"], out["reason"] = "offline", "connection_failed"
        return out

    if status != 302:
        out["reason"] = "bad_status"
        return out
    if len(locs) != 1:
        out["reason"] = "location_invalid"
        return out
    path = _check_location(locs[0])
    if path is None:
        out["reason"] = "location_invalid"
        return out
    if path == "/starceas/knuaf-doc/releases":
        out["status"] = "no_release"
        return out
    m = TAG_PATH_RE.match(path)
    if m:
        latest = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        out["latest_version"] = format_version(latest)
        out["release_url"] = RELEASE_URL_FMT % format_version(latest)
        if local is None:
            out["status"], out["reason"] = "unknown", "local_unreadable"
            return out
        if latest > local:
            out["status"] = "update_available"
        elif latest == local:
            out["status"] = "up_to_date"
        else:
            out["status"] = "local_newer"
        return out
    out["reason"] = "path_unmatched"
    return out


# ------------------------------------------------------------ paths/state --

def _require_canonical(path, what):
    """abspath == realpath 이고 각 구성요소가 링크·재분석점이 아닌지."""
    s = str(path)
    if os.path.abspath(s) != os.path.realpath(s):
        raise Refused("symlinked_path", target=what)
    if _has_link_components(s):
        raise Refused("symlinked_path", target=what)
    return Path(os.path.realpath(s))


def _cwd_guard(target):
    try:
        cwd = Path(os.getcwd()).resolve()
    except OSError:
        return
    if cwd == target or target in cwd.parents:
        raise Refused("cwd_inside_skill")


def _validate_target(target, *, allow_legacy):
    """11.4 target 독립 검증 → (skills_dir, home, old_version, is_legacy)."""
    t = _require_canonical(target, "target")
    if t.name != SKILL_NAME or t.parent.name != "skills":
        raise Refused("target_not_knuaf_doc")
    skills_dir = _require_canonical(t.parent, "skills_dir")
    home = _require_canonical(skills_dir.parent, "home")
    if not t.is_dir() or _is_link_or_reparse(t):
        raise Refused("target_not_knuaf_doc")
    kind = classify_install(t)
    if kind != "copy":
        hint = "git pull --ff-only" if kind == "git" \
            else "README의 수동 업데이트 절차를 따른다"
        raise Refused("install_kind", hint=hint)
    skill_md = t / "SKILL.md"
    try:
        st = os.lstat(str(skill_md))
    except OSError:
        raise Refused("target_not_knuaf_doc")
    if not stat.S_ISREG(st.st_mode) \
            or _frontmatter_name(skill_md) != SKILL_NAME:
        raise Refused("target_not_knuaf_doc")
    try:
        old = read_version_file(t / "version.json")
        legacy = False
    except FileNotFoundError:
        marker = t / "scripts" / "gg_report.py"
        try:
            marker_st = os.lstat(str(marker))
            valid_marker = stat.S_ISREG(marker_st.st_mode) \
                and not _is_link_or_reparse(marker)
        except OSError:
            valid_marker = False
        if not allow_legacy or not valid_marker:
            raise Refused("target_version_invalid")
        old, legacy = None, True
    except (OSError, VersionUnreadable):
        raise Refused("target_version_invalid")
    return skills_dir, home, old, legacy


def _prepare_state(skills_dir, home):
    """state_dir·backups·bin 생성 뒤 경로 안전을 확인한다 (11.4, 11.6)."""
    state_dir = home / STATE_DIR_NAME
    for sub in (state_dir, state_dir / "backups", state_dir / "bin"):
        try:
            sub.mkdir(exist_ok=True)
        except OSError:
            raise Refused("unsafe_state_path")
        try:
            valid = stat.S_ISDIR(os.lstat(str(sub)).st_mode) \
                and not _is_link_or_reparse(sub)
        except OSError:
            valid = False
        if not valid:
            raise Refused("unsafe_state_path")
    bin_dir = state_dir / "bin"
    if os.path.realpath(str(bin_dir)) != \
            os.path.join(os.path.realpath(str(state_dir)), "bin"):
        raise Refused("unsafe_state_path")
    backups_real = Path(os.path.realpath(str(state_dir / "backups")))
    if backups_real == skills_dir or skills_dir in backups_real.parents:
        raise Refused("unsafe_state_path")
    try:
        if os.stat(str(skills_dir)).st_dev != os.stat(str(state_dir)).st_dev:
            raise Refused("cross_device")
    except Refused:
        raise
    except OSError:
        raise Refused("unsafe_state_path")
    return state_dir


# ------------------------------------------------------------------- lock --

def _check_lock_identity(state_dir, fd):
    """열린 잠금 파일이 아직 같은 일반 파일인지 확인한다."""
    lock = state_dir / "lock"
    try:
        opened, named = os.fstat(fd), os.lstat(str(lock))
    except OSError:
        raise Refused("lock_replaced", lock=str(lock))
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
            or _is_link_or_reparse(lock)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise Refused("lock_replaced", lock=str(lock))


def _os_lock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _acquire_lock(state_dir, _nonce_unused=None):
    """호출마다 새 fd를 열어 비차단 OS 잠금을 잡는다. L은 영구 파일이다."""
    lock = state_dir / "lock"
    if _is_link_or_reparse(lock):
        raise Refused("unsafe_state_path")
    try:
        fd = os.open(str(lock), os.O_RDWR | os.O_CREAT |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        raise Refused("unsafe_state_path")
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("unsafe_state_path")
        try:
            _os_lock(fd)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise Refused("locked", lock=str(lock))
            raise Refused("lock_unsupported")
        acquired = True
        _check_lock_identity(state_dir, fd)
        return fd
    except BaseException:
        if acquired:
            try:
                _os_unlock(fd)
            except OSError:
                pass
        os.close(fd)
        raise


def _release_lock(fd):
    """획득한 호출만 해제한다. unlock 실패에도 fd는 닫는다."""
    try:
        _os_unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- journal --

def _write_journal(state_dir, doc):
    """O_EXCL 임시 파일 + fsync + os.replace + 디렉터리 fsync (11.2, 11.6)."""
    doc = dict(doc)
    doc["utc"] = _utcnow()
    fd, tmp = tempfile.mkstemp(dir=str(state_dir), prefix=".journal-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(state_dir / "journal.json"))
        _fsync_dir(state_dir)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _ident(path):
    """회복 상태표의 식별 함수 (11.2). version|legacy|None."""
    p = Path(path)
    try:
        st = os.lstat(str(p))
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    skill_md = p / "SKILL.md"
    try:
        st = os.lstat(str(skill_md))
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if _frontmatter_name(skill_md) != SKILL_NAME:
        return None
    try:
        return format_version(read_version_file(p / "version.json"))
    except FileNotFoundError:
        return "legacy"
    except (OSError, VersionUnreadable):
        return None


def _cleanup_staging(journal):
    """기록된 inode와 같은 자기 staging만 지운다 → 'done'|'skipped'|'absent'."""
    staging = journal.get("staging")
    skills_dir = journal.get("skills_dir")
    if not staging or not skills_dir:
        return "absent"
    s = Path(staging)
    try:
        st = os.lstat(str(s))
    except OSError:
        return "absent"
    ok = (s.parent == Path(skills_dir)
          and s.name.startswith(STAGING_PREFIX)
          and stat.S_ISDIR(st.st_mode)
          and not _is_link_or_reparse(s)
          and st.st_dev == journal.get("staging_dev")
          and st.st_ino == journal.get("staging_ino"))
    if not ok:
        return "skipped"
    try:
        shutil.rmtree(str(s))
    except OSError:
        return "skipped"
    return "done"


def _stage_helper(state_dir, txid):
    """11.3+11.6: 자기 gg_update.py를 bin에 O_EXCL 새 파일로 게시한다."""
    bin_dir = state_dir / "bin"
    if _is_link_or_reparse(bin_dir) or not bin_dir.is_dir():
        raise Refused("unsafe_state_path")
    if os.path.realpath(str(bin_dir)) != \
            os.path.join(os.path.realpath(str(state_dir)), "bin"):
        raise Refused("unsafe_state_path")
    helper = bin_dir / ("gg_update-%s.py" % txid)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(helper), flags, 0o644)
    except FileExistsError:
        raise Refused("helper_exists")
    except OSError:
        raise Refused("unsafe_state_path")
    try:
        with open(str(Path(__file__).resolve()), "rb") as src:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view):]
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            os.unlink(str(helper))
        except OSError:
            pass
        raise Refused("unsafe_state_path")
    else:
        os.close(fd)
    _fsync_dir(bin_dir)
    return helper


# ------------------------------------------------------------------- zip --

def _zip_kind(info):
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0:
        return "empty"
    if mode == stat.S_IFDIR:
        return "dir"
    if mode == stat.S_IFREG:
        return "file"
    return "other"


def _normalize_zip(info_list):
    """11.5: 전체 이름 정규화·검증 → (top, [(norm, kind)])."""
    if len(info_list) > ZIP_MAX_ENTRIES:
        raise Refused("zip_invalid", detail="too_many_entries")
    seen = {}
    entries = []
    tops = set()
    for info in info_list:
        raw = info.orig_filename  # 11.5: 정규화·NUL 절단 전의 원래 이름
        if info.compress_type not in (zipfile.ZIP_STORED,
                                    zipfile.ZIP_DEFLATED):
            raise Refused("zip_invalid", detail="compress_type")
        if info.flag_bits & 0x1:
            raise Refused("zip_invalid", detail="encrypted")
        if any(c in raw for c in ("\\", "\x00", ":")) \
                or any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
            raise Refused("zip_invalid", detail="bad_chars")
        if len(raw) > ZIP_NAME_MAX:
            raise Refused("zip_invalid", detail="name_too_long")
        if raw.startswith("/"):
            raise Refused("zip_invalid", detail="absolute")
        kind = _zip_kind(info)
        if kind == "other":
            raise Refused("zip_invalid", detail="entry_type")
        if raw.endswith("/"):
            if kind not in ("dir", "empty"):
                raise Refused("zip_invalid", detail="dir_declared_file")
            name, kind = raw[:-1], "dir"
        else:
            if kind == "dir":
                raise Refused("zip_invalid", detail="dir_without_slash")
            name = raw
        if not name:
            raise Refused("zip_invalid", detail="bad_component")
        parts = name.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise Refused("zip_invalid", detail="bad_component")
        cf = name.casefold()
        if cf in seen:
            raise Refused("zip_invalid", detail="duplicate")
        seen[cf] = kind
        entries.append((name, cf, kind, info))
        tops.add(parts[0].casefold())
    if len(tops) != 1:
        raise Refused("zip_invalid", detail="top_folder")
    prefixes = set()
    for _name, cf, _kind, _info in entries:
        parts = cf.split("/")
        for i in range(1, len(parts)):
            prefixes.add("/".join(parts[:i]))
    for _name, cf, kind, _info in entries:
        parts = cf.split("/")
        for i in range(1, len(parts)):
            if seen.get("/".join(parts[:i])) == "file":
                raise Refused("zip_invalid", detail="file_dir_conflict")
        if kind == "file" and cf in prefixes:
            raise Refused("zip_invalid", detail="file_dir_conflict")
    return tops.pop(), entries


def _extract_zip(data, dest):
    """검증 뒤 <top>/skills/knuaf-doc/ 하위만 dest에 쓴다."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Refused("zip_invalid", detail="not_a_zip")
    with zf:
        top, entries = _normalize_zip(zf.infolist())
        prefix = top + "/skills/knuaf-doc/"
        total = 0
        for name, cf, kind, info in entries:
            if not cf.startswith(prefix):
                continue
            # 원본 이름의 4번째 구성요소부터 — casefold 길이 변화에 안전.
            parts = name.split("/")[3:]
            if not parts:
                continue
            target = dest
            for comp in parts:
                target = target / comp
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                with zf.open(info) as src, \
                        open(str(target), "xb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        written += len(chunk)
                        total += len(chunk)
                        if written > ZIP_FILE_MAX or total > ZIP_TOTAL_MAX:
                            raise Refused("zip_invalid",
                                          detail="size_limit")
                        dst.write(chunk)
            except (zipfile.BadZipFile, zlib.error):
                raise Refused("zip_invalid", detail="crc_error")


def _validate_new_tree(new_root, expected):
    """S/knuaf-doc이 기대한 knuaf-doc 트리인지 확인한다."""
    skill_md = new_root / "SKILL.md"
    try:
        st = os.lstat(str(skill_md))
    except OSError:
        raise Refused("new_tree_invalid")
    if not stat.S_ISREG(st.st_mode) \
            or _frontmatter_name(skill_md) != SKILL_NAME:
        raise Refused("new_tree_invalid")
    try:
        version = read_version_file(new_root / "version.json")
    except (OSError, VersionUnreadable):
        raise Refused("new_tree_invalid")
    if version != expected:
        raise Refused("new_tree_invalid")


def _copy_source(source, dest):
    """adopt 원천 복사. 링크가 하나라도 들어오면 거절한다."""
    shutil.copytree(str(source), str(dest), symlinks=True)
    for p in Path(dest).rglob("*"):
        if _is_link_or_reparse(p):
            raise Refused("source_has_links")


# ---------------------------------------------------------------- replace --

def _run_replace(target, *, expected, prepare, allow_legacy,
                 connect=None, deadline=CHECK_DEADLINE, version_arg=None):
    """apply/adopt 공용 교체 엔진. journal 단계:
    prepared → old_moved → new_placed → done."""
    skills_dir, home, old_version, legacy = _validate_target(
        target, allow_legacy=allow_legacy)
    target = skills_dir / SKILL_NAME
    if old_version is not None and expected is not None \
            and old_version >= expected:
        raise Refused("not_newer")
    _cwd_guard(target)
    state_dir = _prepare_state(skills_dir, home)

    pre = os.stat(str(target))
    pre_ino = (pre.st_dev, pre.st_ino)

    fd = _acquire_lock(state_dir)
    try:
        prior, prior_state = _read_journal(state_dir / "journal.json", home)
        if prior_state == "manual_required":
            raise Refused("journal_requires_recovery")
        if prior_state == "ok" and prior.get("phase") != "done":
            raise Refused("journal_requires_recovery")
        post = os.stat(str(target))
        post_v, post_state = _try_version(target)
        if (post.st_dev, post.st_ino) != pre_ino:
            raise Refused("changed_during_update")
        if legacy:
            if post_state != "legacy":
                raise Refused("changed_during_update")
        elif post_state != "version" or post_v != old_version:
            raise Refused("changed_during_update")

        if version_arg is not None:
            status = check_status(connect=connect, deadline=deadline,
                                  local_root=target)
            if status["status"] != "update_available":
                raise Refused("not_update_available",
                              check_status=status["status"])
            if status["latest_version"] != format_version(version_arg):
                raise Refused("not_latest",
                              latest=status["latest_version"])

        # 백업 이름 + txid — 존재하면 새 nonce로 다시 만든다.
        backups = state_dir / "backups"
        for _ in range(100):
            txid = _nonce()
            backup = backups / ("%s-%s-%s" % (
                format_version(old_version) if old_version else "unknown",
                _utc_stamp(), txid[:8]))
            if not os.path.lexists(str(backup)):
                break
        else:
            raise Refused("backup_name_conflict")

        try:
            staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX,
                                            dir=str(skills_dir)))
        except OSError:
            raise Failed("prepare", "target_intact")
        st = os.lstat(str(staging))
        new_leaf = staging / SKILL_NAME

        journal = {
            "schema": JOURNAL_SCHEMA, "txid": txid,
            "home": str(home), "skills_dir": str(skills_dir),
            "target": str(target), "backup": str(backup),
            "staging": str(staging),
            "staging_dev": st.st_dev, "staging_ino": st.st_ino,
            "old_version": (format_version(old_version)
                            if old_version else None),
            "new_version": format_version(expected),
            "phase": "prepared", "utc": _utcnow(),
        }

        def cleanup_staging():
            return _cleanup_staging(journal)

        try:
            prepare(new_leaf)          # zip 추출 또는 source 복사
            _validate_new_tree(new_leaf, expected)
        except Refused:
            cleanup_staging()
            raise
        except OSError:
            cleanup_staging()
            raise Failed("prepare", "target_intact")

        try:
            helper = _stage_helper(state_dir, txid)  # 11.6: rename 전 게시
        except Refused:
            cleanup_staging()
            raise
        try:
            _check_lock_identity(state_dir, fd)
        except Refused:
            cleanup_staging()
            raise
        try:
            _write_journal(state_dir, journal)
        except OSError:
            cleanup_staging()
            raise Failed("prepare", "target_intact")

        try:
            _rename(str(target), str(backup))
        except OSError:
            cleanup_staging()
            raise Failed("move_old", "target_intact")

        journal["phase"] = "old_moved"
        try:
            _write_journal(state_dir, journal)
        except OSError:
            pass  # 기록하지 못해도 실제 상태표가 recover를 이끈다.

        try:
            _rename(str(new_leaf), str(target))
        except OSError:
            try:
                _rename(str(backup), str(target))
            except OSError:
                raise Failed(
                    "place_new", "backup_preserved_target_missing",
                    backup=str(backup),
                    recover_argv=["python3", str(helper), "recover",
                                  "--home", str(home), "--confirm"],
                    recover=shlex.join(
                        ["python3", str(helper), "recover",
                         "--home", str(home), "--confirm"]))
            journal["phase"] = "prepared"
            try:
                _write_journal(state_dir, journal)
            except OSError:
                pass
            raise Failed("place_new", "old_restored",
                         backup=str(backup))

        journal["phase"] = "new_placed"
        try:
            _write_journal(state_dir, journal)
        except OSError:
            pass

        cleanup = _cleanup_staging(journal)
        journal["phase"] = "done"
        try:
            _write_journal(state_dir, journal)
        except OSError:
            pass

        out = {"status": "applied",
               "old_version": journal["old_version"],
               "new_version": journal["new_version"],
               "backup": str(backup)}
        if cleanup != "done":
            out["cleanup"] = cleanup
            if cleanup == "skipped":
                out["staging"] = str(staging)
        return out
    finally:
        _release_lock(fd)


def _default_download(version, connect=None):
    connect = connect or _default_connect
    path = DOWNLOAD_PATH_FMT % format_version(version)

    def fetch():
        conn = connect(DOWNLOAD_HOST, timeout=DOWNLOAD_DEADLINE)
        conn.request("GET", path, headers={"User-Agent": USER_AGENT})
        resp = conn.getresponse()
        if resp.status != 200:
            raise Refused("download_failed")
        chunks, total = [], 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > DOWNLOAD_MAX:
                raise Refused("download_failed")
            chunks.append(chunk)
        return b"".join(chunks)

    try:
        return _run_deadline(fetch, DOWNLOAD_DEADLINE)
    except Refused:
        raise
    except Exception:
        raise Refused("download_failed")


def apply_cmd(version_text, *, confirm, connect=None, download=None):
    try:
        if not confirm:
            raise Refused("confirm_required")
        version = parse_version(version_text)
        if version is None:
            raise Refused("invalid_version")
        target = skill_root()
        dl = download or _default_download
        holder = {}

        def prepare(new_leaf):
            if "zip" not in holder:
                holder["zip"] = dl(version, connect)
            _extract_zip(holder["zip"], new_leaf)

        return _run_replace(target, expected=version, prepare=prepare,
                            allow_legacy=False, connect=connect,
                            version_arg=version)
    except Refused as exc:
        return _refused(exc)
    except Failed as exc:
        return _failed(exc)


def adopt_cmd(source, target, *, confirm):
    try:
        if not confirm:
            raise Refused("confirm_required")
        own = skill_root()
        src_real = os.path.realpath(str(source))
        if os.path.abspath(str(source)) != src_real \
                or Path(src_real) != own:
            raise Refused("source_mismatch")
        try:
            src_version = read_version_file(own / "version.json")
        except (OSError, VersionUnreadable):
            raise Refused("source_version_invalid")
        if os.path.abspath(str(target)) != os.path.realpath(str(target)):
            raise Refused("symlinked_path")
        tgt = Path(os.path.realpath(str(target)))
        src = Path(src_real)
        if tgt == src or tgt in src.parents or src in tgt.parents:
            raise Refused("source_target_alias")

        def prepare(new_leaf):
            _copy_source(src, new_leaf)

        return _run_replace(tgt, expected=src_version, prepare=prepare,
                            allow_legacy=True)
    except Refused as exc:
        return _refused(exc)
    except Failed as exc:
        return _failed(exc)


# ---------------------------------------------------------------- recover --

def _read_journal(journal_path, home_p):
    """→ (dict, 'ok') | (None, 'no_journal'|'manual_required')."""
    try:
        doc = json.loads(journal_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no_journal"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "manual_required"
    if not isinstance(doc, dict) \
            or doc.get("schema") != JOURNAL_SCHEMA \
            or doc.get("home") != str(home_p):
        return None, "manual_required"
    return doc, "ok"


def _journal_bounds_ok(journal, home_p, state_dir):
    """journal 경로들이 11.4 경계 안에 있는지 다시 검사한다."""
    skills_dir = home_p / "skills"
    target = journal.get("target")
    backup = journal.get("backup")
    staging = journal.get("staging")
    txid = journal.get("txid")
    old_raw = journal.get("old_version")
    new_raw = journal.get("new_version")
    dev = journal.get("staging_dev")
    ino = journal.get("staging_ino")
    ok = (isinstance(txid, str)
          and bool(re.fullmatch(r"[0-9a-f]{16}", txid))
          and (old_raw is None or parse_version(old_raw) is not None)
          and parse_version(new_raw) is not None
          and journal.get("skills_dir") == str(skills_dir)
          and isinstance(target, str)
          and target == str(skills_dir / SKILL_NAME)
          and isinstance(backup, str)
          and Path(backup).parent == state_dir / "backups"
          and Path(backup).name.endswith("-" + txid[:8])
          and isinstance(staging, str)
          and Path(staging).parent == skills_dir
          and Path(staging).name.startswith(STAGING_PREFIX)
          and isinstance(dev, int) and not isinstance(dev, bool)
          and isinstance(ino, int) and not isinstance(ino, bool)
          and not _has_link_components(state_dir)
          and not _has_link_components(state_dir / "backups")
          and not _has_link_components(skills_dir))
    if ok:
        for p in (target, backup, staging):
            if _is_link_or_reparse(Path(p)):
                ok = False
    return ok


def _staging_leaf_ok(journal):
    """S/knuaf-doc이 실제로 없거나(또는 우리가 만든 새 트리)면 True.

    다른 버전·손상·일반 파일·링크·판독 불가 leaf는 '없음'이 아니라
    예상 밖 존재다 — 회복이 지우거나 밟지 않고 보존한다."""
    leaf = Path(journal["staging"]) / SKILL_NAME
    try:
        st = os.lstat(str(leaf))
    except FileNotFoundError:
        return True, "absent"
    except OSError:
        return False, "foreign"
    if not stat.S_ISDIR(st.st_mode):
        return False, "foreign"
    if _ident(leaf) == journal["new_version"]:
        return True, "ours"
    return False, "foreign"


def _recover_locked(journal, home_p, state_dir):
    """lock을 쥔 채 확인된 journal의 실제 T/B/S 상태표 (11.2)."""
    target = journal["target"]
    backup = journal["backup"]
    staging = journal["staging"]
    old = journal.get("old_version") or "legacy"
    new = journal["new_version"]
    t_id = _ident(target)
    b_id = _ident(backup)
    t_exists = Path(target).exists()
    leaf_ok, _leaf = _staging_leaf_ok(journal)

    def result(status):
        out = {"status": status}
        cleanup = _cleanup_staging(journal)
        if cleanup != "done":
            out["cleanup"] = cleanup
            out["staging"] = staging
        return out

    if not leaf_ok:
        return {"status": "manual_required", "target": target,
                "backup": backup, "staging": staging}
    if t_id == old and not Path(backup).exists():
        return result("not_started")
    if not t_exists and b_id == old:
        try:
            _rename(backup, target)
        except OSError:
            return {"status": "manual_required", "target": target,
                    "backup": backup}
        return result("recovered_old")
    if t_id == new and b_id == old and _leaf == "absent":
        return result("completed")
    return {"status": "manual_required", "target": target,
            "backup": backup, "staging": staging}


def recover_cmd(home, *, confirm):
    """저널 판독부터 종결까지 apply/adopt와 같은 OS 잠금을 쥔다."""
    if not confirm:
        return _refused(Refused("confirm_required"))
    home_s = str(home)
    if os.path.abspath(home_s) != os.path.realpath(home_s) \
            or _has_link_components(home_s):
        return _refused(Refused("symlinked_path"))
    home_p = Path(os.path.realpath(home_s))
    state_dir = home_p / STATE_DIR_NAME
    journal_path = state_dir / "journal.json"

    try:
        st = os.lstat(str(state_dir))
    except FileNotFoundError:
        return {"status": "no_journal"}
    except OSError:
        return {"status": "manual_required", "journal": str(journal_path)}
    if not stat.S_ISDIR(st.st_mode) or _has_link_components(state_dir):
        return {"status": "manual_required", "journal": str(journal_path)}

    try:
        fd = _acquire_lock(state_dir)
    except Refused as exc:
        return _refused(exc)
    try:
        journal, jstate = _read_journal(journal_path, home_p)
        if jstate == "no_journal":
            return {"status": "no_journal"}
        if jstate != "ok" or not _journal_bounds_ok(journal, home_p, state_dir):
            return {"status": "manual_required", "journal": str(journal_path)}
        _check_lock_identity(state_dir, fd)
        out = _recover_locked(journal, home_p, state_dir)
        if out["status"] != "manual_required":
            closed = state_dir / ("journal-%s-closed.json" % journal["txid"])
            try:
                _check_lock_identity(state_dir, fd)
                os.rename(str(journal_path), str(closed))
            except Refused as exc:
                return _refused(exc)
            except OSError:
                return {"status": "manual_required", "journal": str(journal_path)}
        return out
    except Refused as exc:
        return _refused(exc)
    finally:
        _release_lock(fd)


# --------------------------------------------------------------------- cli --

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"status": "error", "reason": "usage_error"},
                         ensure_ascii=False))
        raise SystemExit(2)


def main(argv=None, *, connect=None, download=None):
    parser = _Parser(prog="gg_update.py",
                     description="knuaf-doc 업데이트 확인·적용·복구")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="새 버전이 있는지 확인")
    p_apply = sub.add_parser("apply", help="최신 릴리스로 교체")
    p_apply.add_argument("--version", required=True)
    p_apply.add_argument("--confirm", action="store_true")
    p_adopt = sub.add_parser("adopt", help="incoming 복사본으로 교체")
    p_adopt.add_argument("--source", required=True)
    p_adopt.add_argument("--target", required=True)
    p_adopt.add_argument("--confirm", action="store_true")
    p_rec = sub.add_parser("recover", help="중단된 교체 복구")
    p_rec.add_argument("--home", required=True)
    p_rec.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "check":
        result = check_status(connect=connect)
    elif args.command == "apply":
        result = apply_cmd(args.version, confirm=args.confirm,
                           connect=connect, download=download)
    elif args.command == "adopt":
        result = adopt_cmd(args.source, args.target,
                           confirm=args.confirm)
    elif args.command == "recover":
        result = recover_cmd(args.home, confirm=args.confirm)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
