#!/usr/bin/env python3
"""Bounded, offline Kordoc bootstrap and HWP-to-Markdown adapter.

The adapter deliberately keeps the npm package in an owned, versioned user
cache.  It never edits a system runtime, global npm configuration, or an input
document.  All process execution uses ``shell=False``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import re


PACKAGE = "kordoc"
VERSION = "4.17.1"
REGISTRY = "https://registry.npmjs.org"
OWNER = "ginseng-goat.gg_kordoc"
DEFAULT_TIMEOUT = 180
REQUIRED_PARSE_FLAGS = (
    "--format",
    "--keep-empty-cols",
    "--keep-empty-paragraphs",
    "--silent",
)
# Core `generate` (markdown -> HWPX) options the HWPX adapter relies on.
REQUIRED_GENERATE_FLAGS = (
    "--preset",
    "--paper",
    "--plain",
    "--image-dir",
    "--silent",
)
# Windows command shims: subprocess would route these through cmd.exe and
# re-parse Korean, spaces and ^&% in arguments, so they are never executed
# directly.
SHIM_SUFFIXES = (".cmd", ".bat", ".ps1")


class KordocBlocked(RuntimeError):
    """A user-actionable, expected failure represented by status=blocked."""

    def __init__(self, reason: str, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


def _result(status: str, **fields):
    return {"status": status, **fields}


def _cache_root(value: str | os.PathLike[str] | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return (Path(base).expanduser() / "ginseng-goat" / "kordoc").resolve()
    return (Path.home() / ".cache" / "ginseng-goat" / "kordoc").resolve()


def _runtime_path(value: str | None, names: tuple[str, ...]) -> str | None:
    if value:
        candidate = Path(value).expanduser()
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(value)
        return found
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _node_and_pnpm(node: str | None, pnpm: str | None, *, need_pnpm=True, check_node=True):
    node_path = _runtime_path(node, ("node",))
    pnpm_path = _runtime_path(pnpm, ("pnpm",)) if need_pnpm else None
    corepack = None
    if need_pnpm and not pnpm_path:
        corepack = _runtime_path(None, ("corepack",))
    missing = []
    if not node_path:
        missing.append("node")
    if need_pnpm and not pnpm_path and not corepack:
        missing.append("pnpm (or corepack)")
    if missing:
        raise KordocBlocked(
            "needs_runtime",
            missing=missing,
            action="Provide --node and --pnpm paths (the bundled host runtime may supply them), or install Node >=20 and pnpm.",
        )
    if check_node:
        version_probe = _run([node_path, "--version"], env=_env_for_node(node_path), timeout=15)
        version_text = (version_probe.get("stdout") or "").strip()
        match = re.match(r"v?(\d+)(?:\.(\d+))?", version_text)
        if not version_probe.get("ok") or not match or int(match.group(1)) < 20:
            raise KordocBlocked(
                "needs_runtime",
                missing=["node>=20"],
                node=node_path,
                probe=version_probe,
                action="Provide a Node >=20 executable with --node (the bundled host runtime may supply it).",
            )
    if not need_pnpm:
        return node_path, None
    if pnpm_path:
        pnpm_cmd = [pnpm_path]
    else:
        pnpm_cmd = [corepack, "pnpm"]
    return node_path, pnpm_cmd


# Variables an external process may inherit. Everything else — API keys,
# tokens, session credentials — is withheld from the third-party runtime.
_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "COLORTERM",
    "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "http_proxy", "https_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "PNPM_HOME", "COREPACK_HOME", "COREPACK_ENABLE_DOWNLOAD_PROMPT",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "PROGRAMDATA",
    # Windows still resolves the home directory from USERPROFILE (HOME is
    # usually unset there), and pnpm/Node read the drive and program-data
    # locations below.  All are non-secret path values.
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "SYSTEMDRIVE",
    "NO_COLOR", "CI", "KORDOC_OFFLINE",
})


def _strip_proxy_userinfo(value: str) -> str:
    """Remove userinfo (user:pass@) embedded in a proxy value.

    Handles both scheme://user:pass@host[:port][/path] and the schemeless
    user:pass@host[:port] form.  An '@' outside the authority part (e.g. in
    the path) is left untouched.
    """
    if "@" not in value:
        return value
    if "://" in value:
        scheme, rest = value.split("://", 1)
        prefix = scheme + "://"
    else:
        prefix, rest = "", value
    authority, sep, tail = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return prefix + authority + (sep + tail if sep else "")


def _base_env() -> dict[str, str]:
    """Allowlisted slice of os.environ for external processes.

    Proxy variables keep their host/port but never embedded credentials:
    a child that echoes its environment must not be able to leak them.
    """
    env = {}
    for key, value in os.environ.items():
        if key not in _ENV_ALLOWLIST:
            continue
        if key.lower().endswith("_proxy"):
            value = _strip_proxy_userinfo(value)
        env[key] = value
    return env


def _env_for_node(node_path: str, *, offline: bool = False) -> dict[str, str]:
    env = _base_env()
    node_dir = str(Path(node_path).resolve().parent)
    env["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
    if offline:
        # Kordoc itself does not download OCR models unless OCR is requested;
        # this marker makes the offline contract explicit to wrappers/builds.
        env["KORDOC_OFFLINE"] = "1"
    return env


def _env_for_external(*, offline: bool = False, node_path: str | None = None) -> dict[str, str]:
    """Environment for a PATH or explicitly supplied Kordoc executable."""
    env = _base_env()
    if node_path:
        node_candidate = Path(node_path).expanduser()
        if node_candidate.is_file():
            env["PATH"] = str(node_candidate.resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    if offline:
        env["KORDOC_OFFLINE"] = "1"
    return env


def _run(cmd: list[str], *, cwd: Path | None = None, env=None, timeout=DEFAULT_TIMEOUT):
    """Run one bounded process and return machine-readable process evidence."""
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False, encoding="utf-8",
        )
    except FileNotFoundError as exc:
        return {"ok": False, "kind": "missing_executable", "error": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "kind": "timeout", "error": f"timed out after {timeout}s: {exc.cmd!r}"}
    except OSError as exc:
        return {"ok": False, "kind": "os_error", "error": str(exc)}
    return {
        "ok": cp.returncode == 0,
        "returncode": cp.returncode,
        # Keep enough of --help for capability checks; still bound process
        # evidence so a noisy third-party CLI cannot grow the result forever.
        "stdout": (cp.stdout or "")[-16000:],
        "stderr": (cp.stderr or "")[-16000:],
    }


def _read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _package_paths(version_root: Path):
    package_root = version_root / "node_modules" / PACKAGE
    return package_root, package_root / "package.json", package_root / "dist" / "cli.js"


def _validated_install(version_root: Path, node_path: str, *, need_generate: bool = False):
    package_root, package_json, cli = _package_paths(version_root)
    marker = _read_json(version_root / ".gg-kordoc.json")
    spec = _read_json(package_json)
    if not marker or marker.get("owner") != OWNER or marker.get("version") != VERSION or marker.get("registry") != REGISTRY:
        return None
    if not spec or spec.get("name") != PACKAGE or str(spec.get("version")) != VERSION:
        return None
    if not cli.is_file():
        return None
    cli_command = [node_path, str(cli)]
    smoke = _run(cli_command + ["--help"], env=_env_for_node(node_path), timeout=30)
    if not smoke.get("ok"):
        return None
    capability = {"help": "ok", "smoke": "help"}
    if need_generate:
        generate = _probe_generate(cli_command, env=_env_for_node(node_path), timeout=30)
        if not generate["ok"]:
            return None
        capability["generate"] = "ok"
        capability["required_generate_flags"] = list(REQUIRED_GENERATE_FLAGS)
    return {"version_root": version_root, "package_root": package_root, "cli": cli,
            "smoke": smoke, "capability": capability}


def _version_from_probe(probe: dict) -> str | None:
    text = " ".join((probe.get("stdout") or "", probe.get("stderr") or ""))
    match = re.search(r"(?<![0-9])v?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)(?![0-9])", text)
    return match.group(1) if match else None


def _resolve_kordoc(value: str | None) -> tuple[str | None, str | None]:
    """Resolve an explicit path or the first PATH kordoc without touching cache."""
    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise KordocBlocked(
                "kordoc_missing",
                kordoc=str(candidate),
                explicit=True,
                action="The explicit --kordoc path is missing or not executable; provide a valid Kordoc CLI path.",
            )
        return str(candidate.resolve()), "explicit"
    found = shutil.which("kordoc")
    return (str(Path(found).resolve()), "path") if found else (None, None)


def _is_windows_shim(path: Path, platform: str) -> bool:
    """True for a Windows command shim (.cmd/.bat/.ps1) that subprocess would
    hand to cmd.exe.  ``platform`` is passed in rather than read from
    ``sys.platform`` here, so the decision can be exercised from macOS.
    """
    return platform.startswith("win") and path.suffix.lower() in SHIM_SUFFIXES


def _sibling_cli_js(shim: Path):
    """Locate the real ``dist/cli.js`` behind a Windows kordoc shim.

    Covers the two shapes that actually install a kordoc shim: npm global and
    local (``<dir>/node_modules/kordoc/dist/cli.js``) and pnpm global
    (``<dir>/global/<n>/node_modules/kordoc/dist/cli.js``).  Returns the first
    existing candidate, or None when the entry point cannot be found.
    """
    base = shim.parent
    candidates = [
        base / "node_modules" / PACKAGE / "dist" / "cli.js",
        base.parent / "node_modules" / PACKAGE / "dist" / "cli.js",
        base / "lib" / "node_modules" / PACKAGE / "dist" / "cli.js",
    ]
    candidates.extend(sorted(base.glob(f"global/*/node_modules/{PACKAGE}/dist/cli.js")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _exec_command_for_cli(path: str, *, node_path: str | None, platform: str | None = None):
    """Map a resolved Kordoc CLI path to a shell-free exec command.

    A Windows shim is never passed to subprocess directly (``shell=False``
    would route it through cmd.exe, which re-parses Korean text, spaces and
    ``^&%`` in arguments).  Its sibling ``dist/cli.js`` is executed with Node
    instead.  Returns ``(command, evidence)``; command is None when a shim
    cannot be resolved to a Node entry point.
    """
    if platform is None:
        platform = sys.platform
    shim = Path(path)
    if not _is_windows_shim(shim, platform):
        return [str(shim)], {"shim": False}
    cli_js = _sibling_cli_js(shim)
    if cli_js is None:
        return None, {"shim": True, "reason": "windows_shim_unresolved"}
    if not node_path:
        return None, {"shim": True, "reason": "windows_shim_needs_node", "cli_js": str(cli_js)}
    return [node_path, str(cli_js)], {"shim": True, "cli_js": str(cli_js)}


def _external_command(path: str, node_path: str | None):
    """Resolve an external path to its exec command, looking up Node only when
    a Windows shim actually requires it."""
    command, evidence = _exec_command_for_cli(path, node_path=node_path)
    if command is None and evidence.get("reason") == "windows_shim_needs_node":
        discovered = shutil.which("node")
        if discovered:
            command, evidence = _exec_command_for_cli(path, node_path=discovered)
            if command is not None:
                evidence["node_path"] = discovered
    return command, evidence


def _probe_generate(cli_command: list[str], *, env, timeout=DEFAULT_TIMEOUT):
    """Probe ``generate --help`` and require the core HWPX generation flags."""
    probe = _run(list(cli_command) + ["generate", "--help"], env=env, timeout=min(timeout, 30))
    text = " ".join((probe.get("stdout") or "", probe.get("stderr") or ""))
    missing = [flag for flag in REQUIRED_GENERATE_FLAGS if flag not in text]
    return {"ok": bool(probe.get("ok")) and not missing, "probe": probe, "missing_flags": missing}


def _probe_external(cli_command: list[str], *, path: str, source_kind: str,
                    node_path: str | None = None, timeout=DEFAULT_TIMEOUT,
                    need_generate: bool = False):
    """Verify identity, version, parse flags and (optionally) generate support."""
    probe_env = _env_for_external(node_path=node_path)
    version_probe = _run(list(cli_command) + ["--version"], env=probe_env, timeout=min(timeout, 30))
    help_probe = _run(list(cli_command) + ["--help"], env=probe_env, timeout=min(timeout, 30))
    version = _version_from_probe(version_probe)
    help_text = " ".join((help_probe.get("stdout") or "", help_probe.get("stderr") or ""))
    missing_flags = [flag for flag in REQUIRED_PARSE_FLAGS if flag not in help_text]
    identity = "kordoc" in ((help_text + " " + (version_probe.get("stdout") or "") + " " + (version_probe.get("stderr") or "")).lower())
    rejection = []
    if not version_probe.get("ok"):
        rejection.append("version_probe_failed")
    if not help_probe.get("ok"):
        rejection.append("help_probe_failed")
    if not version:
        rejection.append("version_unparseable")
    if not identity:
        rejection.append("identity_missing")
    if missing_flags:
        rejection.append("required_flags_missing")
    generate_evidence = None
    if need_generate:
        generate_evidence = _probe_generate(cli_command, env=probe_env, timeout=timeout)
        if not generate_evidence["ok"]:
            rejection.append("generate_unavailable" if not generate_evidence["probe"].get("ok") else "generate_flags_missing")
    evidence = {
        "path": path,
        "source_kind": source_kind,
        "version_probe": version_probe,
        "help_probe": help_probe,
        "missing_flags": missing_flags,
        "identity": identity,
    }
    if generate_evidence is not None:
        evidence["generate"] = generate_evidence
    if rejection:
        evidence["reasons"] = rejection
        return None, evidence
    capability = {
        "version": "ok",
        "help": "ok",
        "required_parse_flags": list(REQUIRED_PARSE_FLAGS),
        "smoke": "version_help",
    }
    if generate_evidence is not None:
        capability["generate"] = "ok"
        capability["required_generate_flags"] = list(REQUIRED_GENERATE_FLAGS)
    return {
        "cli_path": path,
        "cli_command": list(cli_command),
        "actual_version": version,
        "node_path": node_path,
        "source": "external",
        "source_kind": source_kind,
        "capability_verification": capability,
    }, evidence


def _claim_cache(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker_path = root / ".gg-kordoc-cache.json"
    current = _read_json(marker_path)
    if current is not None:
        # The cache root is owned across versions: each release lives in its
        # own versioned subdirectory and keeps its own .gg-kordoc.json marker.
        if current.get("owner") != OWNER or current.get("package") != PACKAGE or current.get("registry") != REGISTRY:
            raise KordocBlocked("cache_unowned", cache_dir=str(root), action="Choose a new --cache-dir; existing files are preserved.")
        return
    # Do not take over an arbitrary populated directory.
    if any(root.iterdir()):
        raise KordocBlocked("cache_unowned", cache_dir=str(root), action="Choose an empty --cache-dir; existing files are preserved.")
    payload = {
        "owner": OWNER,
        "package": PACKAGE,
        "version": VERSION,
        "registry": REGISTRY,
        "created_at": int(time.time()),
    }
    try:
        with marker_path.open("x", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except FileExistsError:
        current = _read_json(marker_path)
        if not current or current.get("owner") != OWNER or current.get("package") != PACKAGE or current.get("registry") != REGISTRY:
            raise KordocBlocked("cache_unowned", cache_dir=str(root), action="Choose a new --cache-dir; existing files are preserved.")


def _publish(stage_md: Path, target: Path) -> None:
    """Publish staged bytes to ``target`` without ever overwriting a file.

    A hard link publishes atomically on filesystems that support it.  Where
    hard links are unavailable (FAT/exFAT, common on Windows removable media)
    an exclusive create keeps the same no-overwrite guarantee.
    """
    try:
        os.link(stage_md, target)
        return
    except FileExistsError as exc:
        raise KordocBlocked(
            "output_exists",
            output=str(target),
            action="Output appeared during parse; existing files are preserved.",
        ) from exc
    except OSError:
        pass
    try:
        with open(target, "xb") as handle:
            handle.write(stage_md.read_bytes())
    except FileExistsError as exc:
        raise KordocBlocked(
            "output_exists",
            output=str(target),
            action="Output appeared during parse; existing files are preserved.",
        ) from exc


def ensure(*, cache_dir=None, node=None, pnpm=None, kordoc=None, timeout=DEFAULT_TIMEOUT,
           need_generate=False):
    """Ensure a validated Kordoc installation and return its CLI path.

    ``need_generate`` additionally requires the resolved CLI to advertise the
    ``generate`` (markdown -> HWPX) subcommand with its core options, so callers
    such as the HWPX adapter can rely on it.
    """
    rejected_candidates = []
    external_path, source_kind = _resolve_kordoc(kordoc)
    if external_path:
        # A supplied Node path is an environment hint for wrappers with a
        # node shebang; it does not make external reuse depend on Node checks.
        external_node = None
        if node:
            node_candidate = Path(node).expanduser()
            if node_candidate.is_file():
                external_node = str(node_candidate.resolve())
        cli_command, shim_evidence = _external_command(external_path, external_node)
        if cli_command is None:
            rejected_candidates.append({
                "path": external_path,
                "source_kind": shim_evidence.get("source_kind", source_kind),
                "reasons": [shim_evidence.get("reason", "windows_shim_unresolved")],
                **shim_evidence,
            })
        else:
            candidate, evidence = _probe_external(
                cli_command, path=external_path, source_kind=source_kind,
                node_path=shim_evidence.get("node_path", external_node),
                timeout=timeout, need_generate=need_generate,
            )
            if candidate:
                return _result(
                    "ready",
                    action="reused_existing",
                    package=PACKAGE,
                    version=candidate["actual_version"],
                    actual_version=candidate["actual_version"],
                    cli_path=candidate["cli_path"],
                    cli_command=candidate["cli_command"],
                    command=candidate["cli_command"],
                    source=candidate["source"],
                    source_kind=candidate["source_kind"],
                    node_path=candidate.get("node_path"),
                    shim=shim_evidence.get("shim", False),
                    shim_cli_js=shim_evidence.get("cli_js"),
                    capability_verification=candidate["capability_verification"],
                    rejected_candidates=rejected_candidates,
                )
            evidence["source_kind"] = source_kind
            rejected_candidates.append(evidence)

    try:
        node_path, _ = _node_and_pnpm(node, pnpm, need_pnpm=False)
    except KordocBlocked as exc:
        if rejected_candidates:
            exc.details["rejected_candidates"] = rejected_candidates
        raise
    root = _cache_root(cache_dir)
    _claim_cache(root)
    version_root = root / VERSION
    cached = _validated_install(version_root, node_path, need_generate=need_generate) if version_root.exists() else None
    if cached:
        return _result(
            "ready",
            action="reused",
            package=PACKAGE,
            version=VERSION,
            actual_version=VERSION,
            cache_dir=str(root),
            cli_path=str(cached["cli"]),
            cli_command=[node_path, str(cached["cli"])],
            command=[node_path, str(cached["cli"])],
            node_path=node_path,
            smoke="help_ok",
            capability_verification=cached["capability"],
            rejected_candidates=rejected_candidates,
        )
    if version_root.exists():
        if _owned_but_broken(version_root, node_path):
            # 우리가 설치했지만 안쪽 링크가 끊긴 캐시(옛 방식이 설치 뒤 폴더 이름을 바꿔
            # 남긴 잔해 등)는 지우지 않고 옆으로 치워 두고 새로 설치한다.
            outcome = _quarantine(version_root, root, "broken")
            if not outcome["moved"]:
                raise KordocBlocked(
                    "cache_invalid", cache_dir=str(root), version=VERSION,
                    rejected_candidates=rejected_candidates, quarantine=outcome,
                    action="The existing cache is broken and could not be set aside "
                           f"({outcome['error']}). Close programs using it, then delete or rename "
                           f"{outcome['remains_at']} and retry. No files were deleted.")
        else:
            raise KordocBlocked(
                "cache_invalid",
                cache_dir=str(root),
                version=VERSION,
                rejected_candidates=rejected_candidates,
                action="Existing owned cache is incomplete or failed the capability check; choose a new cache or repair it manually. No files were overwritten.",
            )

    try:
        _, pnpm_cmd = _node_and_pnpm(node_path, pnpm, need_pnpm=True, check_node=False)
    except KordocBlocked as exc:
        if rejected_candidates:
            exc.details["rejected_candidates"] = rejected_candidates
        raise
    # 최종 위치에서 바로 설치한다. pnpm은 Windows에서 절대 경로 정션으로 패키지를
    # 연결하므로, 다른 이름의 폴더에 설치한 뒤 이름을 바꾸면 링크가 끊어진다.
    # "반쯤 설치된 캐시를 쓰지 않는다"는 보장은 표식(.gg-kordoc.json)을 검증이
    # 끝난 뒤에 쓰는 것으로 지킨다 — 표식이 없으면 어떤 경로도 이 폴더를 캐시로 보지 않는다.
    try:
        version_root.mkdir(mode=0o700)
    except FileExistsError:
        raise KordocBlocked(
            "cache_busy", cache_dir=str(root), version=VERSION,
            action="Another run may be installing this cache right now; wait and retry.")
    except OSError as exc:
        raise KordocBlocked("cache_publish_failed", error=str(exc), action="No cache was published; retry with a writable cache directory.")
    try:
        (version_root / "package.json").write_text(
            json.dumps({"name": "ginseng-goat-kordoc-cache", "private": True}, indent=2) + "\n",
            encoding="utf-8",
        )
        install_cmd = pnpm_cmd + [
            "add",
            "--ignore-scripts",
            "--save-exact",
            "--store-dir",
            str(root / "store"),
            "--registry",
            REGISTRY,
            f"{PACKAGE}@{VERSION}",
        ]
        install_env = _env_for_node(node_path)
        install_env["COREPACK_HOME"] = str(root / "corepack")
        install_env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
        install = _run(install_cmd, cwd=version_root, env=install_env, timeout=timeout)
        if not install.get("ok"):
            raise KordocBlocked(
                "install_failed",
                command=install_cmd,
                process=install,
                action="Retry ensure after checking the network and pnpm.",
            )
        # 표식은 설치가 끝난 뒤에 쓰고, 같은 검증기로 패키지 정보와 --help를 확인한다.
        (version_root / ".gg-kordoc.json").write_text(
            json.dumps({"owner": OWNER, "package": PACKAGE, "version": VERSION, "registry": REGISTRY}, indent=2) + "\n",
            encoding="utf-8",
        )
        validated = _validated_install(version_root, node_path, need_generate=need_generate)
        if not validated:
            raise KordocBlocked("validation_failed", action="Install completed but package/version/CLI capability validation failed; no cache was published.")
        return _result(
            "ready",
            action="installed",
            package=PACKAGE,
            version=VERSION,
            actual_version=VERSION,
            cache_dir=str(root),
            cli_path=str(validated["cli"]),
            cli_command=[node_path, str(validated["cli"])],
            command=[node_path, str(validated["cli"])],
            node_path=node_path,
            smoke="help_ok",
            capability_verification=validated["capability"],
            rejected_candidates=rejected_candidates,
        )
    except KordocBlocked as exc:
        outcome = _quarantine(version_root, root, "failed")
        exc.details["quarantine"] = outcome
        exc.details["action"] = f"{exc.details.get('action', '')} {_set_aside_note(outcome)}".strip()
        raise
    except OSError as exc:
        outcome = _quarantine(version_root, root, "failed")
        raise KordocBlocked("cache_publish_failed", error=str(exc), quarantine=outcome,
                            action=f"No cache was published; retry with a writable cache directory. {_set_aside_note(outcome)}")


def _owned_but_broken(version_root: Path, node_path: str) -> bool:
    marker = _read_json(version_root / ".gg-kordoc.json")
    if not marker or marker.get("owner") != OWNER or marker.get("version") != VERSION or marker.get("registry") != REGISTRY:
        return False
    return _validated_install(version_root, node_path, need_generate=False) is None


def _quarantine(path: Path, root: Path, tag: str) -> dict:
    """실패·고장 난 설치 폴더를 지우지 않고 캐시 안 숨김 폴더로 옮긴다(다음 시도가 막히지 않게).

    옮기지 못하면(예: Windows에서 다른 프로그램이 파일을 열고 있을 때) 그 사실과
    오류, 폴더가 실제로 남은 자리를 그대로 돌려준다 — 옮긴 것처럼 안내하지 않는다.
    """
    target = root / f".{tag}-{VERSION}-{uuid.uuid4().hex}"
    try:
        os.replace(path, target)
    except OSError as exc:
        return {"moved": False, "remains_at": str(path), "error": str(exc)}
    return {"moved": True, "moved_to": str(target)}


def _set_aside_note(outcome: dict) -> str:
    if outcome["moved"]:
        return f"The partial install was set aside as {outcome['moved_to']} and can be removed manually."
    return (f"The partial install could NOT be moved ({outcome['error']}) and still sits at "
            f"{outcome['remains_at']}. Close programs using it, then delete or rename that folder before retrying.")


def parse(input_path, out, *, cache_dir=None, node=None, pnpm=None, kordoc=None, timeout=DEFAULT_TIMEOUT):
    """Parse one document to a new markdown file, preserving sidecar images."""
    source = Path(input_path).expanduser().resolve()
    target = Path(out).expanduser().resolve()
    if not source.is_file():
        raise KordocBlocked("input_missing", input=str(source), action="Provide an existing HWP/HWPX/PDF/DOCX/XLSX file.")
    if source == target:
        raise KordocBlocked("input_output_same", input=str(source), output=str(target), action="Choose a new output path; input is never modified.")
    if target.exists():
        raise KordocBlocked("output_exists", output=str(target), action="Choose a new output path; existing files are never overwritten.")
    input_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    ready = ensure(cache_dir=cache_dir, node=node, pnpm=pnpm, kordoc=kordoc, timeout=timeout)
    cli = Path(ready["cli_path"])
    node_path = ready.get("node_path")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=".gg-kordoc-", dir=str(target.parent)))
    stage_md = stage_parent / target.name
    cmd = list(ready.get("cli_command") or [node_path, str(cli)])
    cmd.extend([
        str(source),
        "-o",
        str(stage_md),
        "--format",
        "markdown",
        "--silent",
        "--keep-empty-cols",
        "--keep-empty-paragraphs",
    ])
    try:
        env = _env_for_node(node_path, offline=True) if node_path else _env_for_external(
            offline=True, node_path=ready.get("node_path")
        )
        process = _run(cmd, cwd=stage_parent, env=env, timeout=timeout)
        if not process.get("ok"):
            raise KordocBlocked("parse_failed", command=cmd, process=process, action="Input was preserved; no output was published.")
        # The primary output comes from an external process too: a symlink or
        # non-regular file (fifo/socket/dir) must never be followed, read, or
        # published as content.
        stage_root = stage_parent.resolve()
        if stage_md.is_symlink() or (stage_md.exists() and not stage_md.is_file()):
            raise KordocBlocked(
                "output_unsafe",
                output=str(stage_md),
                action="Kordoc output is not a regular file; the input was preserved and nothing was published.",
            )
        if stage_md.is_file() and not stage_md.resolve().is_relative_to(stage_root):
            raise KordocBlocked(
                "output_unsafe",
                output=str(stage_md),
                action="Kordoc output resolves outside staging; the input was preserved and nothing was published.",
            )
        if not stage_md.is_file() or stage_md.stat().st_size == 0:
            raise KordocBlocked("empty_output", action="Kordoc returned no markdown; no output was published.")
        if hashlib.sha256(source.read_bytes()).hexdigest() != input_hash:
            raise KordocBlocked("input_changed", action="Source changed while reading; retry the current source revision.")
        sidecars = stage_parent / "images"
        destination_sidecars = None
        if target.exists():
            raise KordocBlocked("output_exists", output=str(target), action="Output appeared during parse; existing files are preserved.")
        try:
            if sidecars.exists():
                # Sidecars come from an external process: publish only plain
                # files and directories that live inside the staging tree.
                # Links, fifos, sockets and anything resolving outside staging
                # are rejected recursively before anything is copied.  Nested
                # directories the adapter legitimately writes stay supported;
                # only the manifest (a flat basename list) governs link rewrite.
                if sidecars.is_symlink() or not sidecars.is_dir():
                    raise KordocBlocked(
                        "sidecar_unsafe",
                        sidecar=str(sidecars),
                        action="Kordoc produced an invalid sidecar path; the input was preserved and nothing was published.",
                    )
                sidecars_resolved = sidecars.resolve()
                for member in sorted(sidecars.rglob("*")):
                    if member.is_symlink() or (not member.is_file() and not member.is_dir()):
                        raise KordocBlocked(
                            "sidecar_unsafe",
                            sidecar=str(member.relative_to(sidecars)),
                            action="Kordoc sidecars must be regular files or directories inside staging; the input was preserved and nothing was published.",
                        )
                    if not member.resolve().is_relative_to(sidecars_resolved):
                        raise KordocBlocked(
                            "sidecar_unsafe",
                            sidecar=str(member.relative_to(sidecars)),
                            action="Kordoc sidecar escapes the staging directory; the input was preserved and nothing was published.",
                        )
                # Claim a unique per-document assets directory; no shared images
                # directory is replaced and another document can use this folder.
                destination_sidecars = Path(tempfile.mkdtemp(prefix="gg-assets-", dir=str(target.parent)))
                shutil.copytree(sidecars, destination_sidecars / "images")
                content = stage_md.read_text(encoding="utf-8")
                for prefix in ("(images/", "(./images/", 'src="images/', 'src="./images/'):
                    replacement = prefix.split("images/")[0].replace("./", "") + destination_sidecars.name + "/images/"
                    content = content.replace(prefix, replacement)
                # Actual Kordoc -o output may use bare names rather than images/.
                # Kordoc 4.17.1 nests sidecars under images/<output stem>/
                # (manifest.json included); 4.13.1 kept the flat images/
                # layout. Accept either, keeping validation at the manifest's
                # own directory.
                manifest_path = sidecars / "manifest.json"
                if not manifest_path.is_file():
                    preferred = sidecars / stage_md.stem / "manifest.json"
                    nested = [p for p in sorted(sidecars.glob("*/manifest.json")) if p.is_file()]
                    if preferred.is_file():
                        manifest_path = preferred
                    elif len(nested) == 1:
                        manifest_path = nested[0]
                    else:
                        raise OSError("Invalid image manifest")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, list):
                    raise OSError("Invalid image manifest")
                manifest_dir = manifest_path.parent
                rel = manifest_dir.relative_to(sidecars)
                asset_prefix = destination_sidecars.name + "/images/" + ("" if str(rel) == "." else rel.as_posix() + "/")
                for item in manifest:
                    name = item.get("name") if isinstance(item, dict) else None
                    if not isinstance(name, str) or Path(name).name != name or not (manifest_dir / name).is_file():
                        raise OSError("Invalid image manifest entry")
                    for old in (name, "./" + name):
                        content = content.replace("](" + old + ")", "](" + asset_prefix + name + ")")
                        content = content.replace('src="' + old + '"', 'src="' + asset_prefix + name + '"')
                stage_md.write_text(content, encoding="utf-8")
            # Publish only after all supporting assets are complete.
            _publish(stage_md, target)
        except KordocBlocked:
            if destination_sidecars is not None:
                shutil.rmtree(destination_sidecars, ignore_errors=True)
            raise
        except (OSError, ValueError, TypeError) as exc:
            if destination_sidecars is not None:
                shutil.rmtree(destination_sidecars, ignore_errors=True)
            raise KordocBlocked("output_publish_failed", error=str(exc), action="No complete output was published; input and existing files are preserved.")
        output_bytes = target.read_bytes()
        return _result(
            "ready",
            action="parsed",
            input=str(source),
            output=str(target),
            bytes=len(output_bytes),
            input_sha256=input_hash,
            output_sha256=hashlib.sha256(output_bytes).hexdigest(),
            sidecars=str(destination_sidecars) if destination_sidecars is not None else None,
            offline=True,
            runtime=ready,
        )
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded offline Kordoc bootstrap/parser")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ensure", "parse"):
        p = sub.add_parser(name)
        p.add_argument("--cache-dir")
        p.add_argument("--node", help="node executable path (bundled host runtime accepted)")
        p.add_argument("--pnpm", help="pnpm executable path (bundled host runtime accepted)")
        p.add_argument("--kordoc", help="existing Kordoc CLI executable path (reused when compatible)")
        p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
        if name == "parse":
            p.add_argument("input")
            p.add_argument("--out", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "ensure":
            value = ensure(cache_dir=args.cache_dir, node=args.node, pnpm=args.pnpm, kordoc=args.kordoc, timeout=args.timeout)
        else:
            value = parse(args.input, args.out, cache_dir=args.cache_dir, node=args.node, pnpm=args.pnpm, kordoc=args.kordoc, timeout=args.timeout)
    except KordocBlocked as exc:
        value = _result("blocked", reason=exc.reason, **exc.details)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps(_result("blocked", reason="unexpected_error", error=str(exc)), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main())
