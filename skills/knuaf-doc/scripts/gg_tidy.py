"""Output hygiene: preview and apply cleanup of ``.gg-work`` run folders.

Only ``<root>/.gg-work/<artifact>/rNNN/`` folders are deletion
candidates.  A candidate is preserved when it is the latest run of its
artifact, contains ``.keep``, is referenced by any JSON file in the
workspace, or contains a symbolic link.  Nothing outside ``.gg-work`` is
ever removed; run-like siblings are reported for the user to judge.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

WORK_DIR = ".gg-work"
NOTES_DIR = "notes"
RUN_NAME = re.compile(r"r[0-9]+")
OUTSIDE_NAME = re.compile(r"(^build-|^evidence-|^run\d+$|_F\d)")

__all__ = ["tidy", "WORK_DIR", "NOTES_DIR"]


def _is_run_name(name):
    """Only a whole ``r`` + ASCII digits name is a numbered run.

    ``fullmatch`` keeps ``r001\\n`` and other rule-outside names out of the
    candidate set; ``re.match`` would accept a trailing newline.
    """
    return RUN_NAME.fullmatch(name) is not None


def _rel(root, path):
    return path.relative_to(root).as_posix()


def _mentions(text, base):
    """True when ``base`` appears as a path prefix, not as r0010."""
    start = 0
    while True:
        index = text.find(base, start)
        if index < 0:
            return False
        end = index + len(base)
        if end == len(text) or not (text[end].isalnum() or text[end] == "_"):
            return True
        start = index + 1


def _decode(data):
    """Decode a JSON blob; a BOM wins, otherwise UTF-8."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8")

def _raw_mentions(data, base):
    """Undecodable or damaged bytes: search the encodings we accept.

    The same trailing-boundary rule as ``_mentions`` applies, so a
    reference to ``r0010`` never protects ``r001``.
    """
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        needle = base.encode(encoding)
        width = 1 if encoding == "utf-8" else 2
        start = 0
        while True:
            index = data.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            tail = data[end:end + width]
            if len(tail) < width:
                return True
            char = tail.decode(encoding, errors="ignore")
            if not char or not (char.isalnum() or char == "_"):
                return True
            start = index + 1
    return False

def _strings(value):
    """Every string in a decoded JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)

def _matches(text, base):
    """Compare with a backslash normalized to a slash on both sides."""
    return _mentions(text.replace("\\", "/"), base)

def _json_blobs(root):
    """Read every ``*.json`` under root; also report enumeration failures.

    A directory we cannot list may hold a reference we cannot see, so the
    caller must treat ``errors`` as "reference state unknown".
    """
    blobs = []
    errors = []

    def onerror(error):
        name = getattr(error, "filename", None) or str(error)
        errors.append(
            {
                "path": str(name),
                "reason": "scan_failed:%s" % getattr(error, "errno", error),
            }
        )

    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=onerror
    ):
        dirnames[:] = [
            name for name in dirnames
            if name not in {".git", "__pycache__"}
        ]
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = Path(dirpath) / name
            try:
                blobs.append((path, path.read_bytes()))
            except OSError:
                blobs.append((path, None))
    return blobs, errors


def _referencing(blobs, root, run):
    """Return (relative path, reason) when a JSON file mentions this run."""
    base = "%s/%s/%s" % (WORK_DIR, run.parent.name, run.name)
    for path, data in blobs:
        if path.is_relative_to(run):
            continue
        if data is None:
            return _rel(root, path), "unreadable_reference"
        if _raw_mentions(data, base):
            return _rel(root, path), "referenced"
        try:
            text = _decode(data)
        except UnicodeDecodeError:
            continue
        if _matches(text, base):
            return _rel(root, path), "referenced"
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if any(_matches(item, base) for item in _strings(value)):
            return _rel(root, path), "referenced"
    return None


def _measure(path):
    total = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [
            name for name in dirnames
            if not os.path.islink(os.path.join(dirpath, name))
        ]
        for name in filenames:
            entry = os.path.join(dirpath, name)
            try:
                info = os.lstat(entry)
            except OSError:
                continue
            files += 1
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total, files


def _contains_symlink(path):
    if os.path.islink(path):
        return True
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames + filenames:
            if os.path.islink(os.path.join(dirpath, name)):
                return True
    return False


def _run_key(path):
    return (int(path.name[1:]), path.name)


def _run_entries(parent):
    entries = []
    try:
        children = list(parent.iterdir())
    except OSError:
        return entries
    for child in children:
        if not _is_run_name(child.name):
            continue
        if child.is_symlink() or child.is_dir():
            entries.append(child)
    entries.sort(key=_run_key)
    return entries


def _entry(root, path):
    size, files = _measure(path)
    return {
        "path": _rel(root, path),
        "bytes": size,
        "files": files,
    }


def _kept(root, path, reason, **extra):
    value = _entry(root, path)
    value["reason"] = reason
    value.update(extra)
    return value


def _outside(root):
    found = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return found
    for child in children:
        if child.name == WORK_DIR or not OUTSIDE_NAME.search(child.name):
            continue
        kind = "file"
        try:
            if stat.S_ISDIR(os.lstat(child).st_mode):
                kind = "dir"
        except OSError:
            pass
        found.append({"path": child.name, "kind": kind})
    return found


def _plan(root):
    work = root / WORK_DIR
    plan = {
        "candidates": [], "kept": [], "outside": _outside(root),
        "scan_errors": [],
    }
    if not os.path.lexists(work):
        return plan
    if work.is_symlink():
        plan["kept"].append(_kept(root, work, "symlink_work_dir"))
        return plan
    if not work.is_dir():
        plan["kept"].append(_kept(root, work, "work_dir_not_directory"))
        return plan
    blobs, scan_errors = _json_blobs(root)
    plan["scan_errors"] = scan_errors
    try:
        artifacts = sorted(work.iterdir(), key=lambda item: item.name)
    except OSError:
        plan["kept"].append(_kept(root, work, "unreadable_work_dir"))
        return plan
    for artifact in artifacts:
        if artifact.name == NOTES_DIR:
            plan["kept"].append(_kept(root, artifact, "notes"))
            continue
        if artifact.is_symlink():
            plan["kept"].append(_kept(root, artifact, "symlink_artifact"))
            continue
        if not artifact.is_dir():
            plan["kept"].append(_kept(root, artifact, "non_run"))
            continue
        try:
            children = sorted(artifact.iterdir(), key=lambda item: item.name)
        except OSError:
            plan["kept"].append(_kept(root, artifact, "unreadable_artifact"))
            continue
        runs = []
        for child in children:
            if not _is_run_name(child.name):
                plan["kept"].append(_kept(root, child, "non_run"))
            elif child.is_symlink() or child.is_dir():
                runs.append(child)
            else:
                plan["kept"].append(_kept(root, child, "non_run"))
        runs.sort(key=_run_key)
        latest = runs[-1] if runs else None
        for run in runs:
            if run is latest:
                plan["kept"].append(_kept(root, run, "latest"))
            elif run.is_symlink():
                plan["kept"].append(_kept(root, run, "symlink"))
            elif _contains_symlink(run):
                plan["kept"].append(_kept(root, run, "symlink"))
            elif os.path.lexists(run / ".keep"):
                plan["kept"].append(_kept(root, run, "keep_file"))
            else:
                reference = _referencing(blobs, root, run)
                if reference is not None:
                    plan["kept"].append(
                        _kept(
                            root, run, reference[1],
                            reference=reference[0],
                        )
                    )
                else:
                    plan["candidates"].append(_entry(root, run))
    return plan


def _value(plan, applied):
    total = sum(item["bytes"] for item in plan["candidates"])
    return {
        "status": "applied" if applied else "preview",
        "applied": applied,
        "work_dir": WORK_DIR,
        "candidates": plan["candidates"],
        "kept": plan["kept"],
        "outside_candidates": plan["outside"],
        "scan_errors": plan.get("scan_errors", []),
        "totals": {
            "candidate_count": len(plan["candidates"]),
            "candidate_bytes": total,
            "kept_count": len(plan["kept"]),
        },
        "notice": (
            "삭제는 .gg-work/<산출물>/<회차>/ 폴더로만 한정. "
            "--apply는 사용자 확인 뒤에만."
        ),
    }


def _recheck(root, target):
    work = root / WORK_DIR
    if target.parent.parent != work:
        raise ValueError("outside_work")
    if target.parent.name == NOTES_DIR:
        raise ValueError("notes")
    if not _is_run_name(target.name):
        raise ValueError("not_run")
    if os.path.islink(target):
        raise ValueError("symlink")
    if not target.is_dir():
        raise ValueError("not_directory")
    artifact = os.lstat(target.parent)
    if stat.S_ISLNK(artifact.st_mode) or not stat.S_ISDIR(artifact.st_mode):
        raise ValueError("artifact_not_directory")
    if not Path(os.path.realpath(target)).is_relative_to(
        Path(os.path.realpath(work))
    ):
        raise ValueError("outside_work")
    if os.path.lexists(target / ".keep"):
        raise ValueError("keep_file")
    if _contains_symlink(target):
        raise ValueError("symlink")
    runs = _run_entries(target.parent)
    if runs and target.name == runs[-1].name:
        raise ValueError("became_latest")


def _remove_tree(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise OSError("symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("not_directory")
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_symlink():
                raise OSError("symlink")
            if entry.is_dir(follow_symlinks=False):
                _remove_tree(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                os.unlink(entry.path)
            else:
                raise OSError("unsupported_entry")
    os.rmdir(path)


def _reason(error):
    if isinstance(error, ValueError):
        return str(error)
    return "remove_failed:%s" % getattr(error, "errno", error)


def tidy(root, *, apply=False):
    """Preview (default) or apply run-folder cleanup under ``.gg-work``."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("작성 폴더가 아님")
    work = root / WORK_DIR
    if not os.path.lexists(work):
        value = _value(_plan(root), bool(apply))
        value["deleted"] = []
        value["errors"] = []
        if apply:
            value["lock"] = "not_needed"
        return value
    plan = _plan(root)
    if not apply:
        return _value(plan, False)
    if work.is_symlink():
        value = _value(plan, False)
        value.update(
            status="blocked",
            applied=False,
            reason="work_dir_symlink",
            lock_state="unavailable",
        )
        value["deleted"] = []
        value["errors"] = []
        return value
    if plan["scan_errors"]:
        value = _value(plan, False)
        value.update(
            status="blocked",
            applied=False,
            reason="reference_scan_incomplete",
            lock_state="not_attempted",
        )
        value["deleted"] = []
        value["errors"] = []
        return value

    import gg_lock

    try:
        with gg_lock.Lock(root):
            plan = _plan(root)
            if plan["scan_errors"]:
                value = _value(plan, False)
                value.update(
                    status="blocked",
                    applied=False,
                    reason="reference_scan_incomplete",
                    lock_state="held",
                )
                value["deleted"] = []
                value["errors"] = []
                return value
            deleted = []
            errors = []
            for candidate in plan["candidates"]:
                target = root / candidate["path"]
                try:
                    _recheck(root, target)
                    _remove_tree(target)
                except (OSError, ValueError) as error:
                    errors.append(
                        {"path": candidate["path"], "reason": _reason(error)}
                    )
                else:
                    deleted.append(candidate)
            value = _value(plan, True)
            value["deleted"] = deleted
            value["errors"] = errors
            value["lock"] = "held"
            if errors:
                value["status"] = "failed"
            return value
    except gg_lock.LockError as error:
        value = _value(plan, False)
        value.update(
            status="blocked",
            applied=False,
            reason=error.reason,
            lock_state=error.status,
        )
        value["deleted"] = []
        value["errors"] = []
        return value
