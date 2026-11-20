#!/usr/bin/env python3
"""선언된 runtime 의존성: 정직한 doctor + 프로젝트 전용 venv 준비.

The package declares third-party requirements in requirements-runtime.txt /
runtime-deps.json next to this file. `doctor` reports per-dependency import
and declared version-range truth for BOTH the invoking interpreter (in-process
import, so -S/isolated envs are measured honestly) and the project venv
(<작업폴더>/.venv, checked through its own interpreter). It never treats
"installed somewhere else" as usable. `ensure` creates the project venv with
the stdlib venv module and installs the manifest with pip, then proves each
dependency by actually importing it in the venv interpreter. Failures are
reported, never faked. Nothing is copied from any HOME site-packages.

A venv whose interpreter link points at a Python that is no longer there
(the base interpreter moved or was uninstalled) is not rebuilt over: its
contents are renamed to ``.venv-broken-<UTC stamp>`` first, so nothing a
student had is deleted, and the result JSON names the preserved folder.
Renaming is the only recovery action tried; a rename that fails (the folder
is in use) is reported blocked with what to close, never worked around by
deleting.
"""

import argparse
import errno
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import venv
from pathlib import Path



def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _child_env():
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

HERE = Path(__file__).resolve().parent
DEPS_JSON = HERE / "runtime-deps.json"
REQUIREMENTS = HERE / "requirements-runtime.txt"

_DIST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SPEC = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._*-]*[ ]*(?:[<>!=~]+[ ]*[A-Za-z0-9._*]+[ ]*,?[ ]*)+"
)
_IDENT_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_OPS = (">=", "<=", "==", "!=", ">", "<")

# What the student sees when a broken .venv was moved aside. The sentence is
# FROZEN: no folder name, no path, no student text ever goes into it (lead
# condition 2026-11-20), so it reads the same byte for byte on every run. The
# folder name travels only as the machine value ``notices[].folder`` next to
# it, and ``preserved_venv.name`` keeps carrying it for the app as before.
VENV_PRESERVED_NOTICE = {
    "code": "venv_preserved",
    "message": (
        "예전 준비 폴더가 끊어져 있어서 새로 만들었어요. "
        "예전 폴더는 '.venv-broken-'으로 시작하는 이름으로 남겨 뒀어요. "
        "필요 없으면 지워도 돼요."
    ),
}

# What the student reads when the Windows venv launcher itself dies with 103
# while preparing the environment: the launcher could not look up the base
# ``home\python.exe`` recorded in pyvenv.cfg. 103 is a failed attribute
# lookup, not proof the file is gone (access denial lands there too), so the
# sentence names no cause and never suggests deleting or renaming ``.venv``.
# FROZEN like the notice above: it reads the same byte for byte every run, and
# it travels on ``reason`` — the one key the app lifts out of a failed
# response (APPHINT research 2026-11-21 §1).
VENV_LAUNCHER_UNPREPARED_REASON = (
    "Python 환경 준비를 마치지 못했습니다. "
    "Python 설치 상태와 접근 권한을 확인한 뒤 다시 시도하세요."
)

# The venv launcher's exit code when ``GetFileAttributesW`` on the base
# interpreter fails (CPython PC/launcher.c, PC/venvlauncher.c). Any other
# failure keeps the old response shape.
_LAUNCHER_HOME_LOOKUP_FAILED = 103

# Runs inside another interpreter: argv, not string interpolation, so the
# declared module/dist names can never inject code.
_PROBE = (
    "import sys, importlib, importlib.metadata as m\n"
    "importlib.import_module(sys.argv[1])\n"
    "try:\n    print(m.version(sys.argv[2]))\n"
    "except m.PackageNotFoundError:\n    print('')\n"
)


def load_manifest(path=DEPS_JSON):
    path = Path(path)
    if not path.is_file():
        raise ValueError("runtime-deps.json 선언 없음: " + str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("runtime-deps.json 최상위는 객체여야 함")
    return validate_dependencies(data.get("dependencies"))


def validate_dependencies(deps):
    if not isinstance(deps, list) or not deps:
        raise ValueError("runtime-deps.json dependencies 비어 있음")
    out = []
    for d in deps:
        if not isinstance(d, dict):
            raise ValueError("dependency 항목이 객체가 아님")
        dist, spec, imp = d.get("dist"), d.get("spec"), d.get("import_name")
        purpose = d.get("purpose")
        if (
            not isinstance(dist, str)
            or not _DIST.fullmatch(dist)
            or not isinstance(spec, str)
            or not _SPEC.fullmatch(spec)
            or not spec.startswith(dist)
            or not isinstance(imp, str)
            or not _IDENT_PATH.fullmatch(imp)
            or not isinstance(purpose, str)
            or not purpose.strip()
        ):
            raise ValueError("dependency 선언 형식 오류: " + repr(d))
        used_by = d.get("used_by", [])
        if not isinstance(used_by, list) or not all(
            isinstance(u, str) for u in used_by
        ):
            raise ValueError("dependency used_by 형식 오류: " + repr(d))
        platform = d.get("platform")
        if platform is not None and (
            not isinstance(platform, str) or not re.fullmatch(r"[a-z0-9_]+", platform)
        ):
            raise ValueError("dependency platform 형식 오류: " + repr(d))
        out.append(d)
    return out


def _applicable(d):
    """True unless the dependency declares a platform this host is not.

    A dependency with no "platform" key applies everywhere (existing
    behavior, unchanged). One declaring e.g. "platform": "win32" is skipped
    entirely on other hosts rather than ever being reported "missing" —
    that would be a false alarm, not an honest readiness signal.
    """
    plat = d.get("platform")
    return plat is None or plat == sys.platform


_VENV_INTERPRETERS = (
    Path("bin") / "python3",
    Path("bin") / "python",
    Path("Scripts") / "python.exe",
)


def venv_python(project):
    project = Path(project)
    for rel in _VENV_INTERPRETERS:
        cand = project / ".venv" / rel
        if cand.is_file():
            return cand
    return None


def _on_windows():
    """True only on Windows.

    A one-line seam so the contract tests can exercise the Windows branch on
    macOS and Linux. Patching ``os.name`` instead would be caught by
    ``pathlib``, which picks ``WindowsPath`` from that same global and then
    refuses to instantiate on POSIX.
    """
    return os.name == "nt"


def _is_windows_launcher_copy(vpy):
    """True when ``vpy`` is the Windows launcher copy a copy-mode venv holds.

    ``ensure`` builds Windows venvs with ``symlinks=False``, so
    ``.venv\\Scripts\\python.exe`` is a renamed ``venvlauncher.exe`` — a
    regular file, not a link to the base interpreter. Only that shape can die
    with the launcher's own exit codes, so only that shape gets the extra
    student sentence. POSIX, a symlink, or a missing file keep the existing
    response untouched.
    """
    if not _on_windows() or vpy is None:
        return False
    vpy = Path(vpy)
    return (
        vpy.name == "python.exe"
        and vpy.parent.name == "Scripts"
        and vpy.is_file()
        and not vpy.is_symlink()
    )


def _broken_venv_link(venv_dir):
    """The dangling interpreter link of an existing venv, if that is the fault.

    Deliberately narrow: a venv counts as broken only when its own
    interpreter entry is a symlink whose target no longer exists (the base
    Python was moved or uninstalled). Every other failure — a missing
    package, a corrupt pip, a deleted regular file — stays on the existing
    path, which reports "failed" without moving anyone's folder.
    """
    for rel in _VENV_INTERPRETERS:
        cand = venv_dir / rel
        if not cand.is_symlink() or cand.exists():
            continue
        try:
            target = os.readlink(cand)
        except OSError:
            target = None
        return {"path": str(cand), "link_target": target}
    return None


def _preserve_broken_venv(venv_dir, broken):
    """Rename a broken venv aside: never overwrite an earlier copy, never delete.

    Returns the record placed in the result JSON. A rename that fails (the
    folder is open in another program) and a name space that is exhausted
    both raise ValueError carrying what the student can do, so ``main``
    reports them as blocked instead of guessing.
    """
    parent, name = venv_dir.parent, venv_dir.name
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    for n in range(1, 101):
        dest = parent / (name + "-broken-" + stamp + ("" if n == 1 else "-" + str(n)))
        # lexists, not exists: a dangling symlink left at that name also
        # blocks the rename (and os.rename would silently replace an empty
        # directory on POSIX).
        if os.path.lexists(dest):
            continue
        try:
            os.rename(venv_dir, dest)
        except FileExistsError:
            continue
        except OSError as e:
            if e.errno in (errno.EEXIST, errno.ENOTEMPTY):
                continue
            raise ValueError(
                ".venv를 보존 이름으로 옮기지 못했음 — 이 폴더를 쓰는 앱·Codex·터미널을 모두 끄고 "
                "다시 시도해 주세요. 기존 .venv는 지우지 않았습니다: " + str(e)
            )
        return {
            "name": dest.name,
            "path": str(dest),
            "link": broken["path"],
            "link_target": broken["link_target"],
        }
    raise ValueError(
        ".venv를 보존 이름으로 옮기지 못했음 — 이 폴더에 보존본(.venv-broken-*)이 100개까지 "
        "있습니다. 필요 없는 보존본을 지운 뒤 다시 시도해 주세요."
    )


def _version_tuple(text):
    # Stable dotted-numeric versions only; prereleases/junk suffixes are not
    # comparable -> None (installed-version-unchecked), never a false pass.
    if not re.fullmatch(r"\d+(?:\.\d+)*", (text or "").strip()):
        return None
    return tuple(int(x) for x in text.strip().split("."))


def _cmp(a, b):
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def spec_satisfied(version, spec, dist):
    """True/False when the version is known and comparable, else None.

    Supports >=, <=, >, <, ==, != on dotted numeric versions and == with a
    trailing .*
    """
    if version is None:
        return None
    vt = _version_tuple(version)
    if vt is None or not spec.startswith(dist):
        return None
    for clause in spec[len(dist):].split(","):
        clause = clause.strip()
        if not clause:
            continue
        op = next((o for o in _OPS if clause.startswith(o)), None)
        if op is None:
            return None
        want = clause[len(op):].strip()
        wt = _version_tuple(want[:-2] if want.endswith(".*") else want)
        if wt is None:
            return None
        if want.endswith(".*"):
            if op != "==":
                return None
            if vt[: len(wt)] != wt:
                return False
            continue
        c = _cmp(vt, wt)
        ok = {
            ">=": c >= 0,
            ">": c > 0,
            "<=": c <= 0,
            "<": c < 0,
            "==": c == 0,
            "!=": c != 0,
        }[op]
        if not ok:
            return False
    return True


def _import_and_version_current(module, dist):
    """Actual in-process import + dist metadata of THIS interpreter."""
    if not _IDENT_PATH.fullmatch(module):
        raise ValueError("import 이름 형식 오류: " + repr(module))
    try:
        importlib.import_module(module)
    except Exception:
        return False, None
    try:
        return True, importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return True, None


def _import_and_version(python, module, dist):
    """Import + dist version checked inside the given interpreter."""
    if not _IDENT_PATH.fullmatch(module) or not _DIST.fullmatch(dist):
        raise ValueError("import/dist 이름 형식 오류")
    proc = subprocess.run(
        [str(python), "-c", _PROBE, module, dist],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        timeout=60,
    )
    if proc.returncode != 0:
        return False, None
    version = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    return True, (version or None)


def _probe_row(d, imported, version):
    """One interpreter's truth for one declared dependency."""
    satisfied = spec_satisfied(version, d["spec"], d["dist"]) if imported else None
    if not imported:
        status = "missing"
    elif satisfied is False:
        status = "unsupported-version"
    elif satisfied is None:
        status = "installed-version-unchecked"
    else:
        status = "installed"
    return {
        "imported": imported,
        "version": version,
        "version_in_range": satisfied,
        "status": status,
    }


def _check_venv_dir(venv_dir):
    if venv_dir.is_symlink():
        raise ValueError("프로젝트 .venv가 symlink면 준비를 거부함: " + str(venv_dir))
    if venv_dir.exists() and not venv_dir.is_dir():
        raise ValueError(".venv 경로가 디렉터리가 아님: " + str(venv_dir))


def doctor(project):
    deps = load_manifest()
    project = Path(project)
    vpy = venv_python(project)
    selected = str(vpy) if vpy else sys.executable
    rows = []
    for d in deps:
        if not _applicable(d):
            continue
        cur = _import_and_version_current(d["import_name"], d["dist"])
        ven = (
            _import_and_version(vpy, d["import_name"], d["dist"])
            if vpy
            else None
        )
        current = _probe_row(d, *cur)
        venv_row = _probe_row(d, *ven) if ven else None
        rows.append(
            {
                "dist": d["dist"],
                "spec": d["spec"],
                "import_name": d["import_name"],
                "purpose": d["purpose"],
                "current": current,
                "venv": venv_row,
                "status": (venv_row or current)["status"],
            }
        )
    ready = all(r["status"] == "installed" for r in rows)
    return {
        "manifest": str(DEPS_JSON),
        "requirements": str(REQUIREMENTS),
        "project": str(project),
        "current_python": sys.executable,
        "venv_python": str(vpy) if vpy else None,
        "selected_python": selected,
        "ready": ready,
        "dependencies": rows,
        "notice": "다른 환경의 site-packages에 있어도 이 프로젝트의 실행 인터프리터에서 import·버전 범위가 확인되지 않으면 준비로 보지 않는다.",
    }


def ensure(project, find_links=None, no_index=False):
    deps = load_manifest()
    project = Path(project)
    venv_dir = project / ".venv"
    _check_venv_dir(venv_dir)
    vpy = venv_python(project)
    preserved = None
    if not vpy:
        broken = _broken_venv_link(venv_dir)
        if broken:
            # A venv builder would refuse to write over the directory, and
            # deleting the student's environment is never the answer: keep
            # it under a new name and prepare a fresh one.
            preserved = _preserve_broken_venv(venv_dir, broken)
    created = False
    if not vpy:
        try:
            venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(venv_dir)
        except Exception as e:
            if not preserved:
                raise
            return {
                "status": "blocked",
                "reason": "새 환경을 만들지 못했음 — 기존 .venv는 보존본(" + preserved["name"] + ")에 있습니다. 다시 시도해 주세요: " + str(e),
                "preserved_venv": preserved,
            }, 2
        vpy = venv_python(project)
        created = True
    if not vpy:
        reason = "venv 생성 후 실행 파일을 찾지 못함: " + str(venv_dir)
        if preserved:
            reason += " — 기존 .venv는 보존본(" + preserved["name"] + ")에 있습니다."
        return {
            "status": "failed",
            "reason": reason,
            "preserved_venv": preserved,
        }, 1
    cmd = [
        str(vpy),
        "-m",
        "pip",
        "--disable-pip-version-check",
        "install",
        "--requirement",
        str(REQUIREMENTS),
    ]
    if no_index:
        cmd.append("--no-index")
    if find_links:
        cmd += ["--find-links", str(find_links)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=_child_env(),
        )
    except Exception as e:
        if not preserved:
            raise
        return {
            "status": "blocked",
            "reason": "pip 실행에 실패함 — 기존 .venv는 보존본(" + preserved["name"] + ")에 있습니다. 다시 시도해 주세요: " + str(e),
            "preserved_venv": preserved,
        }, 2
    verified = {}
    versions = {}
    try:
        if proc.returncode == 0:
            for d in deps:
                if not _applicable(d):
                    continue
                imported, version = _import_and_version(
                    vpy, d["import_name"], d["dist"]
                )
                verified[d["dist"]] = imported and spec_satisfied(
                    version, d["spec"], d["dist"]
                ) is True
                versions[d["dist"]] = version
    except Exception as e:
        if not preserved:
            raise
        return {
            "status": "blocked",
            "reason": "새 환경의 의존성 검증을 마치지 못했음 — 기존 .venv는 보존본(" + preserved["name"] + ")에 있습니다. 다시 시도해 주세요: " + str(e),
            "preserved_venv": preserved,
        }, 2
    ok = proc.returncode == 0 and all(verified.values())
    reason = None
    if not ok and preserved:
        reason = "pip 설치 또는 의존성 확인에 실패함 — 기존 .venv는 보존본(" + preserved["name"] + ")에 있습니다."
    elif (
        not ok
        and proc.returncode == _LAUNCHER_HOME_LOOKUP_FAILED
        and _is_windows_launcher_copy(vpy)
    ):
        # The Windows launcher could not reach the base interpreter it was
        # built against, so pip never ran and ``pip_stdout_tail`` holds no
        # student-readable sentence. Nothing was moved or deleted: this is
        # the one failure that gets a plain retry line, and only when no
        # preserved copy exists to name instead (``elif`` above keeps that
        # reason intact).
        reason = VENV_LAUNCHER_UNPREPARED_REASON
    result = {
        "status": "installed" if ok else "failed",
        "venv": str(venv_dir),
        "venv_python": str(vpy),
        "created": created,
        # Null unless this run moved a broken venv aside; then the record
        # names the folder so the app can tell the student where it went.
        "preserved_venv": preserved,
        "pip_argv": cmd,
        "pip_exit": proc.returncode,
        "pip_stdout_tail": proc.stdout[-2000:],
        "pip_stderr_tail": proc.stderr[-2000:],
        "installed_versions": versions,
        "verified_imports": verified,
        "requirements": str(REQUIREMENTS),
    }
    if ok and preserved:
        # Student-facing notice: only a successful re-preparation says the old
        # folder was kept. ``code`` is the machine key and ``folder`` the
        # machine value; ``message`` is the frozen sentence above, so an app
        # can show it verbatim instead of building Korean text of its own.
        # Every other response keeps its exact key set (no ``notices`` key).
        notice = dict(VENV_PRESERVED_NOTICE)
        notice["folder"] = preserved["name"]
        result["notices"] = [notice]
    if reason:
        result["reason"] = reason
    return result, (0 if ok else 1)


def main(argv=None):
    _utf8_stdio()
    if sys.version_info < (3, 10):
        print(json.dumps({"status": "blocked", "reason": "Python 3.10 이상이 필요함"},
                         ensure_ascii=False))
        return 2
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["doctor", "ensure", "python"])
    ap.add_argument("folder", help="프로젝트 작업폴더")
    ap.add_argument(
        "--find-links",
        help="로컬 wheel 디렉터리(선택). site-packages 복사는 절대 아님.",
    )
    ap.add_argument(
        "--no-index",
        action="store_true",
        help="인덱스 조회 없이 --find-links만 사용(오프라인 설치)",
    )
    a = ap.parse_args(argv)
    try:
        if a.command == "doctor":
            print(json.dumps(doctor(a.folder), ensure_ascii=False, indent=2))
            return 0
        if a.command == "python":
            vpy = venv_python(a.folder)
            print(str(vpy) if vpy else sys.executable)
            return 0
        result, code = ensure(
            a.folder, find_links=a.find_links, no_index=a.no_index
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({"status": "blocked", "reason": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    _utf8_stdio()
    sys.exit(main())
